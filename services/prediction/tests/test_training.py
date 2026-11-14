import copy
import io

import numpy as np
import pytest
from test_dataset import PGN

from app.human import HumanModel
from app.models import HumanContext, PositionRequest
from training.dataset import samples_from_pgn
from training.train import fit_model, game_weights, metrics, validate_splits


def row(game_id, date, label=0, cp=75):
    ply = 5 if int(game_id[-1]) % 2 else 4
    value = next(samples_from_pgn(io.StringIO(PGN), selected_plies=(ply,)))
    return {**value, "game_id": game_id, "played_at": date, "label": label, "evaluation_cp": cp}


def test_long_games_do_not_outweigh_short_games():
    rows = [row("game0001", "2026-08-01")] * 4 + [row("game0002", "2026-08-02")]
    assert list(game_weights(rows)) == [0.25, 0.25, 0.25, 0.25, 1]


def test_reported_scores_use_game_weights_and_multiclass_brier_sum():
    report = metrics([0, 0, 2], [[0.8, 0.1, 0.1], [0.8, 0.1, 0.1], [0.1, 0.2, 0.7]], [0.5, 0.5, 1])
    assert report["log_loss"] == pytest.approx(0.2899092476)
    assert report["brier"] == pytest.approx(0.1)


def test_split_validation_rejects_overlapping_games_or_future_training_data():
    train = [row("game0001", "2026-08-01")]
    calibration = [row("game0002", "2026-09-01")]
    test = [row("game0003", "2026-10-01")]
    validate_splits(train, calibration, test)
    with pytest.raises(ValueError, match="overlap"):
        validate_splits(train, calibration, [{**test[0], "game_id": "game0001"}])
    with pytest.raises(ValueError, match="chronological"):
        validate_splits([{**train[0], "played_at": "2026-11-01"}], calibration, test)


def test_model_learns_from_training_outcomes_and_exports_real_inference():
    train = [row(f"train{i:03d}", "2026-08-01", i % 3, [600, 0, -600][i % 3]) for i in range(90)]
    calibration = [
        row(f"calib{i:03d}", "2026-09-01", i % 3, [550, 0, -550][i % 3]) for i in range(30)
    ]
    before = copy.deepcopy(calibration)
    artifact = fit_model(train, calibration)
    model = HumanModel(artifact)
    game = PositionRequest(game_id="check123", moves=["e4", "e5", "Nf3", "Nc6"])
    for cp, winner in [(550, "white"), (0, "draw"), (-550, "black")]:
        p = model.predict(game.board(), HumanContext(**train[0]["context"]), cp)
        assert p.probabilities.model_dump()[winner] > 0.7
    assert calibration == before
    assert np.isfinite(artifact["coefficients"]).all()


def test_fit_refuses_a_training_set_covering_only_one_side_to_move():
    train = [row(f"train{i:03d}0", "2026-08-01", i % 3, [600, 0, -600][i % 3]) for i in range(30)]
    calibration = [
        row(f"calib{i:03d}", "2026-09-01", i % 3, [550, 0, -550][i % 3]) for i in range(30)
    ]
    with pytest.raises(ValueError, match="both sides"):
        fit_model(train, calibration)
