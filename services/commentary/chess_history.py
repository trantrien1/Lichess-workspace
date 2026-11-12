"""Replay the authoritative SAN history used by commentary."""

from dataclasses import dataclass

import chess


def canonical_fen(board: chess.Board) -> str:
    return " ".join(board.fen(en_passant="legal").split()[:4])


@dataclass
class History:
    board: chess.Board
    before: chess.Board
    last: chess.Move | None
    san: str | None


def replay(initial_fen: str, san_history: list[str]) -> History:
    board = chess.Board(initial_fen)
    if not board.is_valid():
        raise ValueError("invalid initial position")
    before = board.copy(stack=True)
    last = None
    san = None
    for value in san_history:
        before = board.copy(stack=True)
        last = board.parse_san(value)
        if last not in board.legal_moves:
            raise ValueError("illegal history move")
        san = board.san(last)
        board.push(last)
    return History(board, before, last, san)
