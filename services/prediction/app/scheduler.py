import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass

import chess

from .engine import Stockfish
from .models import PositionRequest, Prediction, official_result, response_for

logger = logging.getLogger(__name__)


@dataclass
class Job:
    request: PositionRequest
    board: chess.Board
    key: str


class Scheduler:
    """Single event-loop owner; bounded FIFO of games, coalescing pending positions per game."""

    def __init__(
        self, workers=1, max_pending=64, cache_size=512, cache_ttl=300, engine_factory=Stockfish
    ):
        self.worker_count = workers
        self.max_pending = max_pending
        self.cache_size = cache_size
        self.cache_ttl = cache_ttl
        self.engine_factory = engine_factory
        self.pending: OrderedDict[str, Job] = OrderedDict()
        self.active: dict[str, tuple[str, asyncio.Task]] = {}
        self.cache: OrderedDict[tuple[str, str], tuple[float, Prediction]] = OrderedDict()
        self.wake = asyncio.Event()
        self.tasks: list[asyncio.Task] = []
        self.closed = False
        self.completed = self.cache_hits = self.superseded = self.errors = 0

    def start(self):
        self.tasks = [asyncio.create_task(self._worker()) for _ in range(self.worker_count)]

    async def close(self):
        self.closed = True
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.pending.clear()
        self.active.clear()

    def _cached(self, game_id: str, key: str) -> Prediction | None:
        item = self.cache.get((game_id, key))
        if item:
            expires, prediction = item
            if expires > time.monotonic():
                self.cache.move_to_end((game_id, key))
                self.cache_hits += 1
                return prediction
            del self.cache[(game_id, key)]
        return None

    def _save(self, prediction: Prediction, ttl: float | None = None):
        key = (prediction.game_id, prediction.position_key)
        self.cache[key] = (time.monotonic() + (ttl or self.cache_ttl), prediction)
        self.cache.move_to_end(key)
        while len(self.cache) > self.cache_size:
            self.cache.popitem(last=False)

    def submit(self, request: PositionRequest) -> Prediction:
        board = request.board()
        key = request.key()
        current = self.active.get(request.game_id)
        terminal = official_result(request, board)
        if terminal:
            self.pending.pop(request.game_id, None)
            if current and not current[1].cancelling():
                current[1].cancel()
            return terminal
        cached = self._cached(request.game_id, key)
        if cached:
            # A valid takeback can return a prior cached position, but stop obsolete work.
            self.pending.pop(request.game_id, None)
            if current and current[0] != key and not current[1].cancelling():
                current[1].cancel()
            return cached
        if current and current[0] == key and not current[1].cancelling():
            return response_for(request, board, status="pending")
        existing = self.pending.get(request.game_id)
        if existing and existing.key == key:
            return response_for(request, board, status="pending")
        if request.game_id not in self.pending and len(self.pending) >= self.max_pending:
            return response_for(request, board, status="busy")
        if current and not current[1].cancelling():
            current[1].cancel()
            self.superseded += 1
        if existing:
            self.superseded += 1
        self.pending[request.game_id] = Job(request, board, key)
        self.wake.set()
        return response_for(request, board, status="pending")

    def _take(self) -> Job | None:
        # Do not dispatch the same game on two engines during cancellation handover.
        for game_id in self.pending:
            if game_id not in self.active:
                return self.pending.pop(game_id)
        return None

    async def _worker(self):
        engine = self.engine_factory()
        try:
            while not self.closed:
                job = self._take()
                if job is None:
                    self.wake.clear()
                    await self.wake.wait()
                    continue
                start = time.monotonic()
                evaluation = asyncio.create_task(engine.evaluate(job.board, job.request.game_id))
                self.active[job.request.game_id] = (job.key, evaluation)
                try:
                    result = await evaluation
                    prediction = response_for(
                        job.request,
                        job.board,
                        status="ready",
                        probabilities=result.probabilities,
                        depth=result.depth,
                        engine=result.name,
                        evaluation_cp=result.evaluation_cp,
                        mate=result.mate,
                        elapsed_ms=round((time.monotonic() - start) * 1000),
                    )
                    self._save(prediction)
                    self.completed += 1
                except asyncio.CancelledError:
                    if self.closed:
                        raise
                except Exception:
                    logger.exception("Evaluation failed for game %s", job.request.game_id)
                    self.errors += 1
                    self._save(response_for(job.request, job.board, status="unavailable"), ttl=2)
                    await engine.close()
                finally:
                    self.active.pop(job.request.game_id, None)
                    self.wake.set()
        finally:
            await engine.close()

    def stats(self):
        return {
            "pending": len(self.pending),
            "active": len(self.active),
            "cached": len(self.cache),
            "completed": self.completed,
            "cache_hits": self.cache_hits,
            "superseded": self.superseded,
            "errors": self.errors,
        }
