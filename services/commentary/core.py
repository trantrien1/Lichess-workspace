"""Authoritative chess facts; every sentence is prebuilt (phrases.py), none is generated."""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

import chess
import chess.engine
from pydantic import BaseModel, ConfigDict, Field

from chess_history import canonical_fen, replay
from phrases import FINISH, FINISH_CLOSERS, INTRO, PRONOUNCE, pick, seed_of
from voices import DEFAULT, VOICES

PHRASEBOOK = "vi-prebuilt-1"
PIECES = {1: "tốt", 2: "mã", 3: "tượng", 4: "xe", 5: "hậu", 6: "vua"}
FILES = ("a", "bê", "xê", "đê", "e", "ép", "gờ", "hát")


class Clock(BaseModel):
    """Remaining seconds as Lila shows them to both players."""
    model_config = ConfigDict(extra="forbid")
    white: int = Field(ge=0, le=360_000)
    black: int = Field(ge=0, le=360_000)
    running: bool = True


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
    voice: str = DEFAULT
    # "idle": nobody has moved for a while; Lila asks for a remark about the game so far.
    event: Literal["move", "intro", "idle"] = "move"
    idleCount: int = Field(0, ge=0, le=20)
    thinkingMs: int = Field(0, ge=0, le=86_400_000)
    clock: Clock | None = None


def square_name(square: chess.Square) -> str:
    """Written form for subtitles ("f3"); `speech_text` spells it out for the voice."""
    return chess.square_name(square)


SQUARE = re.compile(r"(?<!\w)([a-h])([1-8])(?!\w)")
FILE = re.compile(r"(?<!\w)(cột) ([a-h])(?!\w)")
NUMBER = re.compile(r"(?<!\w)\d+(?!\w)")
DIGITS = ("không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín")
NAMES = re.compile("|".join(sorted(map(re.escape, PRONOUNCE), key=len, reverse=True)))


def vi_number(n: int) -> str:
    """0 to 9999 in Vietnamese words: 21 "hai mươi mốt", 105 "một trăm lẻ năm", 15 "mười lăm"."""
    def after_hundreds(rest):
        return f"lẻ {DIGITS[rest]}" if rest < 10 else vi_number(rest)
    if n >= 1000:
        thousands, rest = divmod(n, 1000)
        words = f"{DIGITS[thousands]} nghìn"
        if rest == 0:
            return words
        # 1005 "một nghìn không trăm lẻ năm", 1020 "một nghìn không trăm hai mươi".
        return f"{words} {vi_number(rest) if rest >= 100 else 'không trăm ' + after_hundreds(rest)}"
    if n >= 100:
        hundreds, rest = divmod(n, 100)
        words = f"{DIGITS[hundreds]} trăm"
        return f"{words} {after_hundreds(rest)}" if rest else words
    if n >= 20:
        tens, unit = divmod(n, 10)
        tail = {0: "", 1: " mốt", 4: " tư", 5: " lăm"}.get(unit, f" {DIGITS[unit]}")
        return f"{DIGITS[tens]} mươi{tail}"
    if n >= 10:
        unit = n - 10
        return "mười" + {0: "", 5: " lăm"}.get(unit, f" {DIGITS[unit]}")
    return DIGITS[n]


def speech_text(text: str) -> str:
    """The sentence as Piper should read it: "f3" becomes "ép ba", "cột c" "cột xê", 21 "hai mươi mốt"."""
    text = NAMES.sub(lambda m: PRONOUNCE[m.group(0)], text)
    text = SQUARE.sub(lambda m: f"{FILES[ord(m.group(1)) - ord('a')]} {DIGITS[int(m.group(2))]}", text)
    text = FILE.sub(lambda m: f"{m.group(1)} {FILES[ord(m.group(2)) - ord('a')]}", text)
    return NUMBER.sub(lambda m: vi_number(int(m.group(0))) if len(m.group(0)) <= 4 else m.group(0), text)


@dataclass
class Facts:
    board: chess.Board
    before: chess.Board
    values: dict
    alternatives: list[str]
    priority: int
    selected: bool
    # narrative.compose: a reading of why the move was played (insight.Insight), said after the sentence.
    insight: object = None


def extract(job: Job) -> Facts:
    if job.voice not in VOICES:
        raise ValueError("unsupported voice")
    history = replay(job.initialFen, job.sanHistory)
    board, before, last, san = history.board, history.before, history.last, history.san
    if board.ply() != job.ply or canonical_fen(board) != canonical_fen(chess.Board(job.fenAfter)):
        raise ValueError("snapshot/history mismatch")
    values = {"rules": {"source": "python-chess:1.11.2", "fen": board.fen(),
                         "uciHistory": [m.uci() for m in board.move_stack]}}
    # The final move and FinishGame may arrive separately. The ending depends on the board, the
    # status and the game only, so both notifications have exactly the same comment identity.
    ending = finish_text(job, board)
    if ending:
        values["finish"] = {"source": "rules/lila-status", "status": job.status, "text": ending}
        return Facts(board, before, values, [ending], 100, True)
    if job.event == "intro":
        if last is not None or board.fen() != chess.STARTING_FEN:
            raise ValueError("intro requires the untouched initial board")
        seed = int(hashlib.sha256(job.gameId.encode()).hexdigest()[:8], 16) % len(INTRO)
        phrases = INTRO[seed:] + INTRO[:seed]
        values["intro"] = {"source": "rules/lila-start", "firstToMove": "white"}
        return Facts(board, before, values, phrases, 30, True)
    if job.event == "idle":
        # Board, history and clock only: no engine, so nothing hints at a move to play.
        from narrative import idle_facts
        return idle_facts(job, Facts(board, before, values, [], 5, True))
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


def finish_text(job: Job, board: chess.Board) -> str | None:
    seed = seed_of(job.gameId, " ".join(job.sanHistory))
    to_move = "Trắng" if board.turn else "Đen"
    other = "Đen" if board.turn else "Trắng"
    if board.is_checkmate():
        key, winner, loser = "mate", other, to_move
    elif board.is_stalemate():
        key, winner, loser = "stalemate", None, None
    elif job.status in ("started", "created"):
        return None
    else:
        winner = {"white": "Trắng", "black": "Đen"}.get(job.winner)
        loser = None if winner is None else "Đen" if winner == "Trắng" else "Trắng"
        if job.status == "aborted":
            key = "aborted"
        elif winner:
            key = job.status if job.status in ("resign", "outoftime", "timeout") else "win"
        elif board.is_insufficient_material():
            key = "insufficient"
        elif board.is_repetition(3):
            key = "repetition"
        elif board.halfmove_clock >= 100:
            key = "fifty"
        else:
            key = "draw"
    text = pick(FINISH[key], seed, winner=winner, loser=loser, side=to_move)
    if key != "aborted" and (seed >> 8) % 2:
        text += " " + pick(FINISH_CLOSERS, seed >> 16)
    return text


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


def comment_id(job: Job, facts: Facts) -> str:
    payload = [job.gameId, job.sanHistory, facts.alternatives, facts.priority]
    if "idle" in facts.values:
        payload.append(["idle", job.requestId])
    elif "intro" in facts.values:
        payload.append("intro")
    elif "finish" not in facts.values:
        payload.append(job.requestId)
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()[:24]
