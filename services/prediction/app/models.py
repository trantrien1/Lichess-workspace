import hashlib
import json
from typing import Literal

import chess
from pydantic import BaseModel, ConfigDict, Field


class HumanContext(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    white_rating: int | None = Field(default=None, ge=100, le=4000)
    black_rating: int | None = Field(default=None, ge=100, le=4000)
    white_clock: float | None = Field(default=None, ge=0, le=86400)
    black_clock: float | None = Field(default=None, ge=0, le=86400)
    initial_seconds: int = Field(ge=0, le=86400)
    increment_seconds: int = Field(ge=0, le=1800)
    speed: str = Field(max_length=20)


class PositionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    game_id: str = Field(pattern=r"^[A-Za-z0-9]{8}$")
    initial_fen: str = Field(default=chess.STARTING_FEN, max_length=100)
    moves: list[str] = Field(default_factory=list, max_length=1200)
    result: Literal["white", "black", "draw", "aborted"] | None = None
    context: HumanContext | None = None

    def board(self) -> chess.Board:
        board = chess.Board(self.initial_fen)
        if not board.is_valid():
            raise ValueError("Invalid initial position")
        for san in self.moves:
            if len(san) > 16:
                raise ValueError("Move too long")
            move = board.parse_san(san)
            if not move:
                raise ValueError("Null moves are not legal game moves")
            board.push(move)
        return board

    def key(self) -> str:
        # Include the history and official result, not merely final FEN or ply.
        canonical = json.dumps(
            [self.initial_fen, self.moves, self.result], separators=(",", ":")
        ).encode()
        return hashlib.sha256(canonical).hexdigest()[:32]


class Probabilities(BaseModel):
    white: float = Field(ge=0, le=1)
    draw: float = Field(ge=0, le=1)
    black: float = Field(ge=0, le=1)


class HumanPrediction(BaseModel):
    status: Literal["ready", "unavailable"]
    probabilities: Probabilities | None = None
    model_id: str | None = None
    reason: (
        Literal["missing_context", "outside_scope", "model_unavailable", "score_unavailable"] | None
    ) = None


class Prediction(BaseModel):
    game_id: str
    position_key: str
    fen: str
    ply: int
    status: Literal["pending", "ready", "finished", "aborted", "unavailable", "busy"]
    probabilities: Probabilities | None = None
    engine: str | None = None
    depth: int | None = None
    elapsed_ms: int | None = None
    evaluation_cp: int | None = None
    mate: int | None = None
    human: HumanPrediction | None = None
    source: Literal["engine", "official"] = "engine"


def response_for(request: PositionRequest, board: chess.Board, **values) -> Prediction:
    return Prediction(
        game_id=request.game_id,
        position_key=request.key(),
        fen=board.fen(),
        ply=board.ply(),
        **values,
    )


def official_result(request: PositionRequest, board: chess.Board) -> Prediction | None:
    if request.result is None:
        return None
    if request.result == "aborted":
        return response_for(request, board, status="aborted", source="official")
    return response_for(
        request,
        board,
        status="finished",
        source="official",
        probabilities=Probabilities(
            white=float(request.result == "white"),
            draw=float(request.result == "draw"),
            black=float(request.result == "black"),
        ),
    )
