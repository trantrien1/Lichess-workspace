import chess
import pytest
from pydantic import ValidationError

from app.human import extract_features
from app.models import HumanContext, PositionRequest


def context(**overrides):
    return HumanContext(
        **{
            "white_rating": 1500,
            "black_rating": 1700,
            "white_clock": 297,
            "black_clock": 295,
            "initial_seconds": 300,
            "increment_seconds": 2,
            "speed": "blitz",
            **overrides,
        }
    )


def test_position_features_keep_white_perspective_and_game_phase():
    board = chess.Board()
    for move in ["e4", "e5", "Nf3", "Nc6"]:
        board.push_san(move)
    features = extract_features(board, context(), -75)
    assert features["cp"] == pytest.approx(-0.69314718056)
    assert features["rating_diff"] == -0.5
    assert features["mean_rating"] == 0
    assert features["white_turn"] == 1
    assert features["ply"] == 0.05
    assert features["material"] == 1
    assert features["material_balance"] == 0
    assert features["repetition"] == 0


def test_repetition_feature_uses_full_history():
    board = chess.Board()
    for move in ["Nf3", "Nf6", "Ng1", "Ng8"] * 2:
        board.push_san(move)
    assert extract_features(board, context(), 0)["repetition"] == 1
    assert extract_features(chess.Board(board.fen()), context(), 0)["repetition"] == 0


def test_clock_disadvantage_and_rating_disadvantage_keep_the_same_color():
    features = extract_features(chess.Board(), context(white_clock=1, black_clock=300), 0)
    assert features["clock_log_diff"] < 0
    assert features["time_pressure_diff"] < 0
    assert features["rating_diff"] < 0


def test_missing_context_cannot_be_replaced_with_invented_rating_or_clock():
    with pytest.raises(ValueError, match="context"):
        extract_features(chess.Board(), context(white_rating=None), 0)
    with pytest.raises(ValueError, match="context"):
        extract_features(chess.Board(), context(black_clock=None), 0)


def test_context_cannot_contain_result_or_post_game_rating_change():
    with pytest.raises(ValidationError):
        context(result="white")
    with pytest.raises(ValidationError):
        context(white_rating_diff=9)


def test_clock_context_changes_do_not_trigger_duplicate_engine_searches():
    a = PositionRequest(game_id="abcd1234", moves=["e4"], context=context())
    b = PositionRequest(game_id="abcd1234", moves=["e4"], context=context(white_clock=100))
    assert a.key() == b.key()
