import time

import chess
import chess.engine
import pytest
from fastapi.testclient import TestClient
from test_human_features import context
from test_human_model import artifact

from app.engine import EngineEvaluation, white_score
from app.human import FEATURE_NAMES, HumanModel
from app.main import make_app
from app.models import Probabilities
from app.scheduler import Scheduler


class ScoreEngine:
    async def evaluate(self, board, game_id):
        return EngineEvaluation(
            Probabilities(white=0.05, draw=0.9, black=0.05), 17, "test engine", -75
        )

    async def close(self):
        pass


def ready(client, payload):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        response = client.post("/v1/predictions", json=payload)
        assert response.status_code == 200
        prediction = response.json()
        if prediction["status"] != "pending":
            return prediction
        time.sleep(0.002)
    pytest.fail("Engine prediction never became ready")


def test_engine_cp_and_mate_are_both_normalized_to_white():
    assert white_score(chess.engine.PovScore(chess.engine.Cp(75), chess.BLACK)) == (-75, None)
    assert white_score(chess.engine.PovScore(chess.engine.Mate(3), chess.BLACK)) == (-9997, -3)


def test_shared_engine_result_uses_each_current_context_without_caching_human_output():
    data = artifact(intercept=[0, 0, 0], temperature=1)
    data["coefficients"][0][list(FEATURE_NAMES).index("rating_diff")] = 1
    scheduler = Scheduler(engine_factory=ScoreEngine)
    payload = {"game_id": "live1234", "moves": ["e4", "e5", "Nf3", "Nc6"]}
    with TestClient(make_app(scheduler, HumanModel(data))) as client:
        first = ready(
            client,
            {**payload, "context": context(white_rating=1800, black_rating=1400).model_dump()},
        )
        second = ready(
            client,
            {**payload, "context": context(white_rating=1400, black_rating=1800).model_dump()},
        )
        anonymous = ready(client, payload)
        assert first["status"] == second["status"] == "ready"
        assert first["evaluation_cp"] == -75
        assert first["probabilities"] == second["probabilities"]
        assert first["human"]["probabilities"]["white"] > second["human"]["probabilities"]["white"]
        assert anonymous["human"]["reason"] == "missing_context"
        assert client.get("/health").json()["completed"] == 1
        terminal = client.post("/v1/predictions", json={**payload, "result": "black"}).json()
        assert terminal["source"] == "official" and terminal["probabilities"]["black"] == 1
        assert "human" not in terminal


def test_bad_model_file_does_not_break_engine_predictions(tmp_path, monkeypatch):
    path = tmp_path / "broken.json"
    path.write_text('{"coefficients": [1]}', encoding="utf-8")
    monkeypatch.setenv("HUMAN_MODEL_PATH", str(path))
    with TestClient(make_app(Scheduler(engine_factory=ScoreEngine))) as client:
        prediction = ready(client, {"game_id": "live1234", "moves": ["e4"]})
        assert prediction["status"] == "ready"
        assert prediction["human"]["reason"] == "model_unavailable"
        assert client.get("/health").json()["human_model"]["loaded"] is False
