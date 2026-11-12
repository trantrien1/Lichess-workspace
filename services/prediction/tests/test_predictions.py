import asyncio
import math

import chess
import chess.engine
import pytest
from fastapi.testclient import TestClient

from app.engine import EngineEvaluation, white_wdl
from app.main import make_app
from app.models import PositionRequest, Probabilities, official_result
from app.scheduler import Scheduler


def position(moves=(), game_id="test1234", **kwargs):
    return PositionRequest(game_id=game_id, moves=list(moves), **kwargs)


@pytest.mark.parametrize("turn", [chess.WHITE, chess.BLACK])
def test_wdl_has_white_point_of_view(turn):
    result = white_wdl(chess.engine.PovWdl(chess.engine.Wdl(700, 200, 100), turn))
    assert result.white == (0.7 if turn else 0.1)
    assert result.black == (0.1 if turn else 0.7)
    assert result.draw == 0.2
    assert math.isclose(sum(result.model_dump().values()), 1)


def test_empty_wdl_is_rejected():
    with pytest.raises(ValueError, match="empty"):
        white_wdl(chess.engine.PovWdl(chess.engine.Wdl(0, 0, 0), chess.WHITE))


@pytest.mark.parametrize("moves", [["e5"], ["e4", "e4"], ["--"], ["a" * 17]])
def test_illegal_history_is_rejected(moves):
    with pytest.raises(ValueError):
        position(moves).board()


def test_repetition_history_survives():
    repeated = position(["Nf3", "Nf6", "Ng1", "Ng8"] * 2)
    assert len(repeated.board().move_stack) == 8
    assert repeated.board().is_repetition(3)
    # Same final FEN without history must never reuse the repetition-aware result.
    assert repeated.key() != position(initial_fen=repeated.board().fen()).key()


@pytest.mark.parametrize("result", ["white", "black", "draw", "aborted"])
def test_official_result_wins_over_engine(result):
    request = position(["e4"], result=result)
    prediction = official_result(request, request.board())
    assert prediction.source == "official"
    assert prediction.position_key != position(["e4"]).key()
    if result == "aborted":
        assert prediction.status == "aborted" and prediction.probabilities is None
    else:
        assert prediction.probabilities.model_dump()[result] == 1
        assert sum(prediction.probabilities.model_dump().values()) == 1


class ControlledEngine:
    def __init__(self):
        self.started = asyncio.Queue()
        self.release = asyncio.Queue()
        self.cancelled = 0
        self.closed = 0

    async def evaluate(self, board, game_id):
        self.started.put_nowait((game_id, tuple(board.move_stack)))
        try:
            value = await self.release.get()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        if isinstance(value, Exception):
            raise value
        return EngineEvaluation(Probabilities(white=0.6, draw=0.3, black=0.1), 12, "test engine")

    async def close(self):
        self.closed += 1


async def eventually(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


@pytest.fixture
async def running():
    engine = ControlledEngine()
    scheduler = Scheduler(engine_factory=lambda: engine)
    scheduler.start()
    yield scheduler, engine
    await scheduler.close()


async def test_many_spectators_share_one_evaluation(running):
    scheduler, engine = running
    request = position(["e4"])
    assert scheduler.submit(request).status == "pending"
    await engine.started.get()
    for _ in range(100):
        assert scheduler.submit(request).status == "pending"
    engine.release.put_nowait(True)
    await eventually(lambda: scheduler.completed == 1)
    for _ in range(100):
        assert scheduler.submit(request).status == "ready"
    assert engine.started.empty()
    assert scheduler.cache_hits == 100


async def test_rapid_moves_cancel_old_search_and_only_keep_latest(running):
    scheduler, engine = running
    scheduler.submit(position(["e4"]))
    await engine.started.get()
    scheduler.submit(position(["e4", "e5"]))
    latest = position(["e4", "e5", "Nf3"])
    scheduler.submit(latest)
    _, moves = await engine.started.get()
    assert len(moves) == 3 and engine.cancelled == 1
    engine.release.put_nowait(True)
    await eventually(lambda: scheduler.completed == 1)
    assert scheduler.submit(latest).position_key == latest.key()
    assert len(scheduler.cache) == 1


async def test_takeback_to_cancelled_active_position_is_not_lost(running):
    scheduler, engine = running
    original = position(["e4"])
    scheduler.submit(original)
    await engine.started.get()
    scheduler.submit(position(["e4", "e5"]))
    scheduler.submit(original)
    _, moves = await engine.started.get()
    assert len(moves) == 1
    engine.release.put_nowait(True)
    await eventually(lambda: scheduler.completed == 1)
    assert scheduler.submit(original).status == "ready"


async def test_terminal_result_cancels_search_without_waiting(running):
    scheduler, engine = running
    scheduler.submit(position(["e4"]))
    await engine.started.get()
    result = scheduler.submit(position(["e4"], result="black"))
    assert result.status == "finished" and result.probabilities.black == 1
    await eventually(lambda: not scheduler.active)
    assert engine.cancelled == 1 and scheduler.completed == 0


async def test_worker_recovers_after_engine_failure(running):
    scheduler, engine = running
    request = position(["e4"])
    scheduler.submit(request)
    await engine.started.get()
    engine.release.put_nowait(RuntimeError("engine died"))
    await eventually(lambda: scheduler.errors == 1)
    assert scheduler.submit(request).status == "unavailable"
    next_request = position(["e4", "e5"])
    scheduler.submit(next_request)
    await engine.started.get()
    engine.release.put_nowait(True)
    await eventually(lambda: scheduler.completed == 1)
    assert scheduler.submit(next_request).status == "ready"
    assert engine.closed >= 1


def test_pending_queue_is_bounded_and_coalesces():
    scheduler = Scheduler(max_pending=2)
    scheduler.submit(position(game_id="game0001"))
    scheduler.submit(position(game_id="game0002"))
    assert scheduler.submit(position(game_id="game0003")).status == "busy"
    assert scheduler.submit(position(["d4"], game_id="game0001")).status == "pending"
    assert len(scheduler.pending) == 2


def test_http_validation_and_official_results():
    with TestClient(make_app(Scheduler())) as client:
        assert client.get("/health").status_code == 200
        assert client.post("/v1/predictions", json={"game_id": "bad"}).status_code == 422
        assert client.post("/v1/predictions", json=position(["e5"]).model_dump()).status_code == 422
        response = client.post("/v1/predictions", json=position(["e4"], result="draw").model_dump())
        assert response.status_code == 200
        assert response.json()["probabilities"] == {"white": 0, "draw": 1, "black": 0}
