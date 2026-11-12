"""Bounded extraction of human-game snapshots from official PGN exports."""

import io
import re
from datetime import UTC, datetime

import chess
import chess.pgn

LABELS = {"1-0": 0, "1/2-1/2": 1, "0-1": 2}
SAMPLE_PLIES = (4, 13, 24, 41, 60, 81, 100, 139)


def pgn_blocks(handle):
    lines = []
    length = 0
    for line in handle:
        if line.startswith('[Event "'):
            if lines:
                yield "".join(lines)
            lines, length = [], 0
        length += len(line)
        if length <= 1_000_000:
            lines.append(line)
        else:
            lines.clear()
    if lines:
        yield "".join(lines)


def samples_from_pgn(handle, selected_plies=SAMPLE_PLIES):
    selected = set(selected_plies)
    seen = set()
    for block in pgn_blocks(handle):
        # A partial download may end mid-game despite a Result header already being present.
        if "[%eval " not in block or block.rstrip().split()[-1] not in LABELS:
            continue
        headers = chess.pgn.read_headers(io.StringIO(block))
        if not headers:
            continue
        event = re.match(r"Rated (Blitz|Rapid)\b", headers.get("Event", ""), re.I)
        game_id = re.fullmatch(
            r"https://lichess.org/(?:game/)?([A-Za-z0-9]{8})/?", headers.get("Site", "")
        )
        clock = re.fullmatch(r"(\d+)\+(\d+)", headers.get("TimeControl", ""))
        if not (event and game_id and clock):
            continue
        if game_id[1] in seen:
            continue
        if headers.get("Variant", "Standard") != "Standard" or "FEN" in headers:
            continue
        if any(headers.get(side + "Title") == "BOT" for side in ("White", "Black")):
            continue
        try:
            ratings = [int(headers[side + "Elo"]) for side in ("White", "Black")]
            played_at = (
                datetime.strptime(
                    headers["UTCDate"] + " " + headers["UTCTime"], "%Y.%m.%d %H:%M:%S"
                )
                .replace(tzinfo=UTC)
                .isoformat()
            )
        except (KeyError, ValueError):
            continue
        if not all(600 <= rating <= 3000 for rating in ratings):
            continue
        initial, increment = map(int, clock.groups())
        if not 30 <= initial <= 1800 or not 0 <= increment <= 60:
            continue
        result = headers.get("Result")
        if result not in LABELS or block.rstrip().split()[-1] != result:
            continue
        game = chess.pgn.read_game(io.StringIO(block))
        if not game or game.errors:
            continue
        board = game.board()
        clocks = {chess.WHITE: None, chess.BLACK: None}
        rows, moves = [], []
        for ply, node in enumerate(game.mainline(), start=1):
            if not node.move or node.move not in board.legal_moves:
                rows.clear()
                break
            color = board.turn
            moves.append(board.san(node.move))
            board.push(node.move)
            # Missing annotation invalidates that player's snapshot, never forward-fill it.
            clocks[color] = node.clock()
            if ply not in selected or board.is_game_over(claim_draw=False):
                continue
            evaluation = node.eval()
            if evaluation is None or any(value is None for value in clocks.values()):
                continue
            if not all(0 <= value <= 86400 for value in clocks.values()):
                continue
            score = evaluation.white()
            rows.append(
                {
                    "game_id": game_id[1],
                    "played_at": played_at,
                    "label": LABELS[result],
                    "fen": board.fen(),
                    "moves": list(moves),
                    "evaluation_cp": score.score(mate_score=10000),
                    "mate": score.mate(),
                    "context": {
                        "white_rating": ratings[0],
                        "black_rating": ratings[1],
                        "white_clock": clocks[chess.WHITE],
                        "black_clock": clocks[chess.BLACK],
                        "initial_seconds": initial,
                        "increment_seconds": increment,
                        "speed": event[1].lower(),
                    },
                }
            )
        if rows:
            seen.add(game_id[1])
            yield from rows
