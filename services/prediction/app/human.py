"""Shared, prefix-only features and portable human-outcome inference."""

import json
import math
from pathlib import Path
from typing import Any, Literal

import chess
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import HumanContext, HumanPrediction, Probabilities

FEATURE_NAMES = (
    "cp",
    "abs_cp",
    "cp_rating",
    "cp_clock_pressure",
    "abs_cp_ply",
    "rating_diff",
    "mean_rating",
    "white_turn",
    "ply",
    "material",
    "material_balance",
    "halfmove_clock",
    "repetition",
    "white_clock_log",
    "black_clock_log",
    "clock_log_diff",
    "time_pressure_diff",
    "time_pressure_sum",
    "initial_log",
    "increment_log",
    "rapid",
    "mate_sign",
    "mate_distance",
)


def extract_features(
    board: chess.Board, context: HumanContext, evaluation_cp: int, mate: int | None = None
):
    wr, br, wc, bc = (
        context.white_rating,
        context.black_rating,
        context.white_clock,
        context.black_clock,
    )
    if None in (wr, br, wc, bc) or context.initial_seconds <= 0:
        raise ValueError("Incomplete human context")
    cp = math.asinh(max(-1000, min(1000, evaluation_cp)) / 100)
    mean_rating = ((wr + br) / 2 - 1600) / 500
    ply = min(board.ply(), 160) / 80
    white_pressure, black_pressure = 10 / (wc + 10), 10 / (bc + 10)
    material = {}
    for color in chess.COLORS:
        material[color] = sum(
            len(board.pieces(piece, color)) * value
            for piece, value in (
                (chess.PAWN, 1),
                (chess.KNIGHT, 3),
                (chess.BISHOP, 3),
                (chess.ROOK, 5),
                (chess.QUEEN, 9),
            )
        )
    return {
        "cp": cp,
        "abs_cp": abs(cp),
        "cp_rating": cp * mean_rating,
        "cp_clock_pressure": cp * (white_pressure + black_pressure),
        "abs_cp_ply": abs(cp) * ply,
        "rating_diff": (wr - br) / 400,
        "mean_rating": mean_rating,
        "white_turn": 1 if board.turn else -1,
        "ply": ply,
        "material": (material[chess.WHITE] + material[chess.BLACK]) / 78,
        "material_balance": (material[chess.WHITE] - material[chess.BLACK]) / 39,
        "halfmove_clock": min(board.halfmove_clock, 100) / 100,
        "repetition": int(board.is_repetition(2)),
        "white_clock_log": math.log1p(wc) / math.log1p(context.initial_seconds),
        "black_clock_log": math.log1p(bc) / math.log1p(context.initial_seconds),
        "clock_log_diff": (math.log1p(wc) - math.log1p(bc)) / 3,
        "time_pressure_diff": black_pressure - white_pressure,
        "time_pressure_sum": white_pressure + black_pressure,
        "initial_log": math.log1p(context.initial_seconds) / math.log(601),
        "increment_log": math.log1p(context.increment_seconds) / math.log(61),
        "rapid": int(context.speed == "rapid"),
        "mate_sign": (int(mate > 0) - int(mate < 0)) if mate is not None else 0,
        "mate_distance": min(abs(mate), 100) / 100 if mate is not None else 0,
    }


class ModelArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    schema_version: Literal[1]
    model_id: str = Field(pattern=r"^[A-Za-z0-9._-]{1,80}$")
    feature_names: list[str]
    classes: list[str]
    mean: list[float]
    scale: list[float]
    coefficients: list[list[float]]
    intercept: list[float]
    temperature: float = Field(gt=0, le=20)
    approved_for_pilot: bool
    support: dict[str, int]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_schema(self):
        size = len(FEATURE_NAMES)
        if self.feature_names != list(FEATURE_NAMES) or self.classes != ["white", "draw", "black"]:
            raise ValueError("Incompatible feature or class order")
        if len(self.mean) != size or len(self.scale) != size or any(x < 1e-12 for x in self.scale):
            raise ValueError("Invalid normalization")
        if (
            len(self.coefficients) != 3
            or any(len(row) != size for row in self.coefficients)
            or len(self.intercept) != 3
        ):
            raise ValueError("Invalid coefficient dimensions")
        if set(self.support) != {"min_rating", "max_rating", "min_ply", "max_ply"}:
            raise ValueError("Invalid support domain")
        if not 100 <= self.support["min_rating"] <= self.support["max_rating"] <= 4000:
            raise ValueError("Invalid rating domain")
        if not 0 <= self.support["min_ply"] <= self.support["max_ply"] <= 1200:
            raise ValueError("Invalid ply domain")
        return self


class HumanModel:
    def __init__(self, artifact):
        self.artifact = ModelArtifact.model_validate(artifact)

    def predict(self, board, context, evaluation_cp, mate=None):
        model = self.artifact
        reason = None
        if context is None or None in (
            context.white_rating,
            context.black_rating,
            context.white_clock,
            context.black_clock,
        ):
            reason = "missing_context"
        elif (
            context.speed not in ("blitz", "rapid")
            or not 30 <= context.initial_seconds <= 1800
            or context.increment_seconds > 60
            or not model.support["min_ply"] <= board.ply() <= model.support["max_ply"]
            or not all(
                model.support["min_rating"] <= r <= model.support["max_rating"]
                for r in (context.white_rating, context.black_rating)
            )
        ):
            reason = "outside_scope"
        elif evaluation_cp is None:
            reason = "score_unavailable"
        if reason:
            return HumanPrediction(status="unavailable", reason=reason, model_id=model.model_id)
        features = extract_features(board, context, evaluation_cp, mate)
        vector = [
            (features[name] - mean) / scale
            for name, mean, scale in zip(FEATURE_NAMES, model.mean, model.scale, strict=True)
        ]
        logits = [
            (sum(weight * value for weight, value in zip(weights, vector, strict=True)) + intercept)
            / model.temperature
            for weights, intercept in zip(model.coefficients, model.intercept, strict=True)
        ]
        if not all(math.isfinite(value) for value in logits):
            raise ValueError("Non-finite human prediction")
        maximum = max(logits)
        exponents = [math.exp(value - maximum) for value in logits]
        total = sum(exponents)
        return HumanPrediction(
            status="ready",
            model_id=model.model_id,
            probabilities=Probabilities(
                **dict(zip(model.classes, [value / total for value in exponents], strict=True))
            ),
        )


def load_model(path):
    path = Path(path)
    if path.stat().st_size > 1_000_000:
        raise ValueError("Model artifact is too large")
    model = HumanModel(json.loads(path.read_text(encoding="utf-8")))
    if not model.artifact.approved_for_pilot:
        raise ValueError("Model is not approved for pilot inference")
    return model
