"""Authoritative chess facts and deliberately constrained Vietnamese realization."""

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

import chess
import chess.engine
from pydantic import BaseModel, ConfigDict, Field

from chess_history import canonical_fen, replay

PROMPT_VERSION = "vi-position-6-flow"
VOICES = ("piper-vais1000",)
PIECES = {1: "tốt", 2: "mã", 3: "tượng", 4: "xe", 5: "hậu", 6: "vua"}
FILES = ("a", "bê", "xê", "đê", "e", "ép", "gờ", "hát")


class Job(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schemaVersion: int = Field(ge=1, le=1)
    requestId: str = Field(min_length=1, max_length=120)
    gameId: str = Field(pattern=r"^[a-zA-Z0-9]{8}$")
    gameRevision: int = Field(ge=1)
    ply: int = Field(ge=0, le=2000)
    initialFen: str = Field(max_length=120)
    # SAN comes exclusively from Lila, never from the browser or a PGN comment.
    sanHistory: list[str] = Field(max_length=2000)
    fenAfter: str = Field(max_length=120)
    status: str = Field(max_length=30)
    winner: str | None = None
    expiresAt: int
    acceptedAt: int
    voice: str = VOICES[0]
    event: Literal["move", "intro"] = "move"


def square_name(square: chess.Square) -> str:
    return f"{FILES[chess.square_file(square)]} {chess.square_rank(square) + 1}"


@dataclass
class Facts:
    board: chess.Board
    before: chess.Board
    values: dict
    alternatives: list[str]
    priority: int
    selected: bool


def extract(job: Job) -> Facts:
    if job.voice not in VOICES:
        raise ValueError("unsupported voice")
    history = replay(job.initialFen, job.sanHistory)
    board, before, last, san = history.board, history.before, history.last, history.san
    if board.ply() != job.ply or canonical_fen(board) != canonical_fen(chess.Board(job.fenAfter)):
        raise ValueError("snapshot/history mismatch")
    values = {"rules": {"source": "python-chess:1.11.2", "fen": board.fen(),
                         "uciHistory": [m.uci() for m in board.move_stack]}}
    # The final move and FinishGame may arrive separately. Prefer the terminal
    # board fact so both notifications have exactly the same comment identity.
    ending = None
    if board.is_checkmate():
        ending = f"{'Trắng' if not board.turn else 'Đen'} chiếu hết trong sự ngỡ ngàng của {'Trắng' if board.turn else 'Đen'}. Ván cờ kết thúc."
    elif board.is_stalemate():
        ending = "Ván cờ hòa do hết nước đi hợp lệ, vua không bị chiếu."
    elif job.status not in ("started", "created"):
        side = {"white": "Trắng", "black": "Đen"}.get(job.winner)
        reason = {"resign": "bỏ cuộc", "outoftime": "hết giờ", "timeout": "không tiếp tục ván cờ"}
        if job.status == "aborted":
            ending = "Ván cờ đã bị hủy."
        elif job.status in reason and side:
            loser = "Đen" if side == "Trắng" else "Trắng"
            ending = f"{loser} {reason[job.status]}. {side} thắng."
        elif side:
            ending = f"Ván cờ kết thúc. {side} thắng."
        else:
            ending = "Ván cờ kết thúc với kết quả hòa."
    if ending:
        values["finish"] = {"source": "rules/lila-status", "status": job.status, "text": ending}
        return Facts(board, before, values, [ending], 100, True)
    if job.event == "intro":
        if last is not None or board.fen() != chess.STARTING_FEN:
            raise ValueError("intro requires the untouched initial board")
        from narrative import INTRO_PHRASES
        seed = int(hashlib.sha256(job.gameId.encode()).hexdigest()[:8], 16) % len(INTRO_PHRASES)
        phrases = INTRO_PHRASES[seed:] + INTRO_PHRASES[:seed]
        values["intro"] = {"source": "rules/lila-start", "firstToMove": "white"}
        return Facts(board, before, values, phrases, 30, True)
    if last is None:
        return Facts(board, before, values, [], 0, False)
    mover = "Trắng" if before.turn else "Đen"
    piece = before.piece_at(last.from_square)
    assert piece
    captured = before.piece_at(last.to_square)
    en_passant = before.is_en_passant(last)
    if en_passant:
        captured = chess.Piece(chess.PAWN, not before.turn)
    castle = before.is_castling(last)
    data = {"source": "rules", "uci": last.uci(), "san": san, "mover": mover,
            "piece": PIECES[piece.piece_type], "from": chess.square_name(last.from_square),
            "to": chess.square_name(last.to_square), "capture": PIECES[captured.piece_type] if captured else None,
            "enPassant": en_passant, "castling": castle,
            "promotion": PIECES.get(last.promotion), "check": board.is_check()}
    values["move"] = data
    if castle:
        wing = "vua" if before.is_kingside_castling(last) else "hậu"
        text = f"{mover} vừa nhập thành cánh {wing}."
        alt = f"{mover} nhập thành về cánh {wing}."
    elif last.promotion:
        text = f"{mover} {'bắt ' + PIECES[captured.piece_type] + ' và ' if captured else ''}phong cấp thành {PIECES[last.promotion]} tại {square_name(last.to_square)}."
        alt = text
    elif en_passant:
        text = f"{mover} bắt tốt qua đường tại {square_name(last.to_square)}."
        alt = text
    elif captured:
        text = f"{mover} dùng {PIECES[piece.piece_type]} bắt {PIECES[captured.piece_type]} tại {square_name(last.to_square)}."
        alt = f"{PIECES[piece.piece_type].capitalize()} {mover.lower()} vừa bắt {PIECES[captured.piece_type]} tại {square_name(last.to_square)}."
    else:
        text = f"{mover} đưa {PIECES[piece.piece_type]} từ {square_name(last.from_square)} đến {square_name(last.to_square)}."
        alt = f"{PIECES[piece.piece_type].capitalize()} {mover.lower()} vừa đi từ {square_name(last.from_square)} đến {square_name(last.to_square)}."
    if board.is_check():
        text += " Chiếu vua."
        alt += " Chiếu vua."
    important = bool(castle or captured or last.promotion or board.is_check())
    facts = Facts(board, before, values, list(dict.fromkeys([text, alt])), 50 if important else 10, True)
    from narrative import position_facts
    position_facts(facts)
    return facts


def engine_score(info: dict, mover: chess.Color) -> dict:
    """Both searches use the mover's POV; cp and mate are never mixed."""
    score = info["score"].pov(mover)
    return {"cp": score.score(), "mate": score.mate(), "pov": "white" if mover else "black",
            "depth": info.get("depth"), "nodes": info.get("nodes"),
            "bound": bool(info.get("lowerbound") or info.get("upperbound")),
            "pv": [m.uci() for m in info.get("pv", [])[:6]]}


def cp_loss(before: dict, after: dict) -> int | None:
    if before["pov"] != after["pov"] or before["bound"] or after["bound"]:
        return None
    if before["cp"] is None or after["cp"] is None:
        return None
    return max(0, before["cp"] - after["cp"])


def validate_realization(raw: str, facts: Facts) -> str:
    value = json.loads(raw)
    expected = facts.values.get("narration", {}).get("factIds", ["finish"] if "finish" in facts.values else ["intro"] if "intro" in facts.values else ["move"])
    # A fact ID cannot validate arbitrary free text. This MVP accepts only
    # complete, independently verified realizations of the selected fact.
    if (set(value) != {"text", "factIds"} or value["factIds"] != expected
            or value["text"] not in facts.alternatives):
        raise ValueError("unverified model assertion")
    return value["text"]


def comment_id(job: Job, facts: Facts) -> str:
    payload = [job.gameId, job.sanHistory, facts.alternatives, facts.priority]
    if "intro" in facts.values:
        payload.append("intro")
    elif "finish" not in facts.values:
        payload.append(job.requestId)
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()[:24]
