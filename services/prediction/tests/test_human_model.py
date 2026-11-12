import json
import math

import chess
import pytest
from test_human_features import context

from app.human import FEATURE_NAMES, HumanModel, load_model


def artifact(**overrides):
    return {
        "schema_version": 1,
        "model_id": "test-model",
        "feature_names": list(FEATURE_NAMES),
        "classes": ["white", "draw", "black"],
        "mean": [0] * len(FEATURE_NAMES),
        "scale": [1] * len(FEATURE_NAMES),
        "coefficients": [[0] * len(FEATURE_NAMES) for _ in range(3)],
        "intercept": [math.log(4), 0, 0],
        "temperature": 2,
        "approved_for_pilot": True,
        "support": {"min_rating": 600, "max_rating": 3000, "min_ply": 4, "max_ply": 140},
        **overrides,
    }


def board():
    b = chess.Board()
    for move in ["e4", "e5", "Nf3", "Nc6"]:
        b.push_san(move)
    return b


def test_portable_inference_applies_temperature_and_class_order():
    prediction = HumanModel(artifact()).predict(board(), context(), 0)
    assert prediction.status == "ready"
    assert prediction.model_id == "test-model"
    assert prediction.probabilities.white == pytest.approx(0.5)
    assert prediction.probabilities.draw == pytest.approx(0.25)
    assert prediction.probabilities.black == pytest.approx(0.25)


def test_coefficients_receive_current_rating_context_in_declared_feature_order():
    model_data = artifact(intercept=[0, 0, 0], temperature=1)
    index = list(FEATURE_NAMES).index("rating_diff")
    model_data["coefficients"][0][index] = 1
    model_data["coefficients"][2][index] = -1
    p = HumanModel(model_data).predict(board(), context(white_rating=1800, black_rating=1400), 0)
    assert p.probabilities.white == pytest.approx(0.6652409558)
    assert p.probabilities.black == pytest.approx(0.0900305732)


def test_large_logits_do_not_overflow():
    p = HumanModel(artifact(intercept=[1000, 0, -1000])).predict(board(), context(), 0)
    assert all(math.isfinite(v) for v in p.probabilities.model_dump().values())
    assert sum(p.probabilities.model_dump().values()) == pytest.approx(1)


@pytest.mark.parametrize(
    "bad_context", [None, context(white_rating=None), context(black_clock=None)]
)
def test_missing_context_yields_no_invented_probabilities(bad_context):
    p = HumanModel(artifact()).predict(board(), bad_context, 0)
    assert p.status == "unavailable"
    assert p.reason == "missing_context"
    assert p.probabilities is None


def test_out_of_scope_speed_rating_and_ply_are_not_silently_extrapolated():
    model = HumanModel(artifact())
    for b, c in [
        (board(), context(speed="bullet")),
        (board(), context(white_rating=3200)),
        (chess.Board(), context()),
    ]:
        p = model.predict(b, c, 0)
        assert p.status == "unavailable" and p.reason == "outside_scope"


@pytest.mark.parametrize(
    "changes",
    [
        {"classes": ["black", "draw", "white"]},
        {"scale": [0] * len(FEATURE_NAMES)},
        {"intercept": [float("nan"), 0, 0]},
        {"coefficients": [[1], [2], [3]]},
        {"temperature": 0},
    ],
)
def test_invalid_artifact_is_rejected(changes):
    with pytest.raises(ValueError):
        HumanModel(artifact(**changes))


def test_runtime_loader_refuses_unapproved_artifact(tmp_path):
    path = tmp_path / "model.json"
    path.write_text(json.dumps(artifact(approved_for_pilot=False)), encoding="utf-8")
    with pytest.raises(ValueError, match="approved"):
        load_model(path)
    path.write_text(json.dumps(artifact()), encoding="utf-8")
    assert load_model(path).predict(board(), context(), 0).status == "ready"
