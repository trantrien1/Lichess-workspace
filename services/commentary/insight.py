"""Why a move may have been played: the commentator's reading of the board, said with a hedge.

A plain description ("Trắng đưa mã lên f3") tells the listener what they can already see. This module
adds the second half of a commentator's sentence: what the move attacks, pins, defends or prepares,
where the forces are heading, which opening the game has reached.

Everything is read from the legal board with python-chess, never from the engine. A player hears it
during the game, so it never names a move for the side about to play, never says who stands better,
and never announces a mate. It only says what the move just played does on the board, which both
players can see, in the hedged voice of a commentator ("có vẻ", "có lẽ").
"""

from dataclasses import dataclass

import chess

from core import PIECES, square_name
from phrases import INSIGHT, OPENINGS, POSITION, fill

VALUE = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}
COUNT = {2: "hai", 3: "ba", 4: "bốn", 5: "năm", 6: "sáu"}
FILE_NAMES = "abcdefgh"


@dataclass
class Insight:
    key: str
    subject: str
    priority: int
    texts: list


def side_name(color):
    return "Trắng" if color else "Đen"


def relative_rank(color, square):
    rank = chess.square_rank(square)
    return rank if color else 7 - rank


def captures(board, color):
    """Legal captures `color` could make if it were its turn (kings are never targets)."""
    probe = board.copy(stack=False)
    probe.turn = color
    probe.ep_square = None
    return [m for m in probe.legal_moves
            if probe.is_capture(m) and probe.piece_type_at(m.to_square) not in (None, chess.KING)]


def en_prise(board, square):
    """The piece on `square` can be taken by a cheaper piece, or taken while nothing guards it."""
    piece = board.piece_at(square)
    if piece is None or piece.piece_type == chess.KING:
        return False
    attackers = [board.piece_type_at(m.from_square) for m in captures(board, not piece.color) if m.to_square == square]
    if not attackers:
        return False
    guarded = bool(board.attackers(piece.color, square))
    return not guarded or min(VALUE[a] for a in attackers) < VALUE[piece.piece_type]


def worth(board, attacker, victim_square):
    """An attack worth a commentator's mention: on a bigger piece, or on one nothing guards."""
    victim = board.piece_at(victim_square)
    if victim is None or victim.piece_type in (chess.KING, chess.PAWN):
        return False
    return VALUE[victim.piece_type] > VALUE[attacker] or not board.attackers(victim.color, victim_square)


def pieces(board, color, *kinds):
    squares = chess.SquareSet()
    for kind in kinds:
        squares |= board.pieces(kind, color)
    return squares


def king_zone(board, color):
    king = board.king(color)
    return chess.SquareSet(chess.BB_KING_ATTACKS[king] | chess.BB_SQUARES[king]) if king is not None else chess.SquareSet()


def zone_attackers(board, color):
    """Pieces of `color` (no pawns, no king) bearing on the squares around the other king."""
    zone = king_zone(board, not color)
    return [sq for sq in pieces(board, color, chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)
            if board.attacks(sq) & zone]


def passed(board, color, square):
    file, rank = chess.square_file(square), chess.square_rank(square)
    for enemy in board.pieces(chess.PAWN, not color):
        if abs(chess.square_file(enemy) - file) <= 1 and (
                chess.square_rank(enemy) > rank if color else chess.square_rank(enemy) < rank):
            return False
    return True


def wing(square):
    file = chess.square_file(square)
    return "king" if file >= 5 else "queen" if file <= 2 else "centre"


def castled_wing(board, color):
    """Where a king sits after castling, judged from its square on the back rank."""
    king = board.king(color)
    if king is None or relative_rank(color, king) != 0:
        return None
    side = wing(king)
    return None if side == "centre" else side


def opening(sans):
    """An opening reached exactly with the last move, as (name, idea)."""
    played = tuple(sans)
    for line, name, idea in OPENINGS:
        if line == played:
            return name, idea
    return None


def analyse(facts, sans):
    """Every reading that holds for the last move, best first."""
    before, board = facts.before, facts.board
    move = board.peek()
    me = before.turn
    you = not me
    moved = board.piece_at(move.to_square)
    mover_type = before.piece_type_at(move.from_square)
    captured = before.piece_at(move.to_square)
    if before.is_en_passant(move):
        captured = chess.Piece(chess.PAWN, you)
    ctx = {"side": side_name(me), "opponent": side_name(you), "piece": PIECES[moved.piece_type],
           "Piece": PIECES[moved.piece_type].capitalize(), "target": square_name(move.to_square)}
    found = []

    def add(key, priority, subject="", **values):
        found.append(Insight(key, subject, priority, fill(INSIGHT[key], ctx | values)))

    check = board.is_check()
    ply = board.ply()

    # The opening, once, at the move that defines it.
    if ply <= 20 and (named := opening(sans)):
        add("opening", 64, named[0], name=named[0], idea=named[1])

    # What the move now attacks: two targets at once, or one new one (also by discovery). A piece that
    # can simply be taken back is not forking or threatening anything yet.
    exposed = en_prise(board, move.to_square)
    if not check:
        before_targets = {m.to_square for m in captures(before, me)}
        after = captures(board, me)
        own = sorted({m.to_square for m in after if m.from_square == move.to_square
                      and worth(board, moved.piece_type, m.to_square)},
                     key=lambda sq: -VALUE[board.piece_type_at(sq)])
        if len(own) >= 2 and not exposed:
            a, b = own[:2]
            add("fork", 86, f"{a}{b}", victim=PIECES[board.piece_type_at(a)], vsq=square_name(a),
                victim2=PIECES[board.piece_type_at(b)], vsq2=square_name(b))
        else:
            fresh = sorted({m.to_square for m in after
                            if ((m.from_square == move.to_square and not exposed) or
                                (m.from_square != move.to_square and m.to_square not in before_targets))
                            and worth(board, board.piece_type_at(m.from_square), m.to_square)},
                           key=lambda sq: -VALUE[board.piece_type_at(sq)])
            if fresh:
                sq = fresh[0]
                victim = PIECES[board.piece_type_at(sq)]
                discovered = not any(m.from_square == move.to_square and m.to_square == sq for m in after)
                add("discovered" if discovered else "threat", 70, str(sq), victim=victim,
                    Victim=victim.capitalize(), vsq=square_name(sq))

    # Pins: to the king, or a piece in front of the queen.
    for sq in pieces(board, you, chess.KNIGHT, chess.BISHOP, chess.ROOK):
        if board.is_pinned(you, sq) and not (before.piece_at(sq) == board.piece_at(sq) and before.is_pinned(you, sq)):
            pinned = PIECES[board.piece_type_at(sq)]
            add("pin_king", 72, str(sq), pinned=pinned, Pinned=pinned.capitalize(), psq=square_name(sq))
            break
    if moved.piece_type in (chess.BISHOP, chess.ROOK, chess.QUEEN) and not check:
        for queen in board.pieces(chess.QUEEN, you):
            straight = chess.square_file(queen) == chess.square_file(move.to_square) or \
                chess.square_rank(queen) == chess.square_rank(move.to_square)
            diagonal = abs(chess.square_file(queen) - chess.square_file(move.to_square)) == \
                abs(chess.square_rank(queen) - chess.square_rank(move.to_square))
            fits = (straight and moved.piece_type != chess.BISHOP) or (diagonal and moved.piece_type != chess.ROOK)
            between = chess.SquareSet(chess.between(move.to_square, queen)) & board.occupied
            if fits and moved.piece_type != chess.QUEEN and len(between) == 1:
                sq = next(iter(between))
                piece = board.piece_at(sq)
                if piece.color == you and piece.piece_type in (chess.KNIGHT, chess.BISHOP, chess.ROOK):
                    pinned = PIECES[piece.piece_type]
                    add("pin_queen", 66, str(sq), pinned=pinned, Pinned=pinned.capitalize(), psq=square_name(sq))

    # The moved piece itself: out of danger, or into it.
    if not captured and VALUE[moved.piece_type] >= 3 and en_prise(before, move.from_square) and not exposed:
        add("retreat", 50, str(move.from_square))
    gained = VALUE[captured.piece_type] if captured else 0
    if exposed and VALUE[moved.piece_type] >= 3 and gained < VALUE[moved.piece_type]:
        add("hanging", 67, str(move.to_square))

    # A piece of ours that was hanging and is now guarded by the moved piece (not by taking the attacker).
    for sq in (chess.SquareSet() if captured else chess.SquareSet(board.attacks(move.to_square)) & board.occupied_co[me]):
        piece = board.piece_at(sq)
        if piece.piece_type != chess.KING and VALUE[piece.piece_type] >= 3 and en_prise(before, sq) and not en_prise(board, sq):
            add("defend", 48, str(sq), guarded=PIECES[piece.piece_type], gsq=square_name(sq))
            break

    # Out of check: the king is not necessarily safe.
    if before.is_check() and len(zone_attackers(board, you)) >= 2:
        add("still_pressed", 60, "")

    # Pressure towards the other king.
    if ply >= 14:
        now, then = len(zone_attackers(board, me)), len(zone_attackers(before, me))
        if now >= 3 and now > then:
            add("king_attack", 58, "", count=COUNT.get(now, "nhiều"))
    enemy_king = board.king(you)
    own_king = board.king(me)
    if mover_type == chess.PAWN and not captured and ply >= 16 and enemy_king is not None and own_king is not None:
        near = abs(chess.square_file(move.to_square) - chess.square_file(enemy_king)) <= 1
        apart = abs(chess.square_file(own_king) - chess.square_file(move.to_square)) >= 3
        if near and apart and relative_rank(me, move.to_square) >= 3 and castled_wing(board, you):
            add("pawn_storm", 54, "")

    # Early pressure on f7 / f2 while that king has not castled.
    weak = chess.F7 if me else chess.F2
    if (ply <= 20 and enemy_king == (chess.E8 if me else chess.E1) and moved.piece_type != chess.PAWN
            and weak in board.attacks(move.to_square) and board.piece_at(weak) == chess.Piece(chess.PAWN, you)
            and weak not in before.attacks(move.from_square)):
        add("weak_f7", 50, "", weak=square_name(weak))

    # Pawn moves: prophylaxis, an air hole, a weakened shelter, a pawn break, space, a new passed pawn.
    if mover_type == chess.PAWN and not captured and not move.promotion:
        file = chess.square_file(move.to_square)
        rank_rel = relative_rank(me, move.to_square)
        stop = None
        if rank_rel == 2 and file in (0, 7) and chess.square_file(move.from_square) == file:
            # h3 keeps a bishop or knight off g4; a3 keeps one off b4 (and the mirror for Black).
            guard = chess.square(1 if file == 0 else 6, 3 if me else 4)
            probe = before.copy(stack=False)
            probe.turn = you
            probe.ep_square = None
            stop = next((m for m in probe.legal_moves if m.to_square == guard
                         and probe.piece_type_at(m.from_square) in (chess.BISHOP, chess.KNIGHT)), None)
            if stop:
                add("prophylaxis", 44, "", stop_piece=PIECES[probe.piece_type_at(stop.from_square)],
                    stop_sq=square_name(guard))
            elif castled_wing(board, me) == ("king" if file == 7 else "queen"):
                add("luft", 34, "")
        if not stop and own_king is not None and castled_wing(board, me) and relative_rank(me, move.from_square) == 1 \
                and abs(file - chess.square_file(own_king)) <= 1 and rank_rel >= 3:
            add("shelter", 36, "")
        if ply > 6 and chess.SquareSet(board.attacks(move.to_square)) & board.pieces(chess.PAWN, you):
            add("pawn_break", 30, "")
        if 2 <= file <= 5 and rank_rel == 4:
            add("space", 24, "")
    if mover_type == chess.PAWN and not move.promotion and relative_rank(me, move.to_square) >= 3 \
            and passed(board, me, move.to_square) and not passed(before, me, move.from_square):
        add("new_passer", 46, str(move.to_square))

    # Lines opened for the long-range pieces by a pawn move in the opening.
    if mover_type == chess.PAWN and ply <= 20:
        gains = []
        for kind in (chess.BISHOP, chess.QUEEN):
            then = sum(len(before.attacks(sq)) for sq in before.pieces(kind, me))
            now = sum(len(board.attacks(sq)) for sq in board.pieces(kind, me))
            if now - then >= 2:
                gains.append(PIECES[kind])
        if gains:
            add("open_lines", 30, "", pieces=" và ".join(gains))

    # Castling: the path cleared, opposite wings, a king still in the centre.
    if not before.is_castling(move):
        for wing_name, rights, path in (("vua", board.has_kingside_castling_rights(me), (5, 6)),
                                        ("hậu", board.has_queenside_castling_rights(me), (1, 2, 3))):
            back = 0 if me else 7
            squares = [chess.square(f, back) for f in path]
            if rights and all(board.piece_at(sq) is None for sq in squares) and \
                    any(before.piece_at(sq) is not None for sq in squares) and not board.is_check():
                add("castle_ready", 40, wing_name, wing=wing_name)
                break
    else:
        mine, theirs = castled_wing(board, me), castled_wing(board, you)
        if theirs and mine and mine != theirs:
            add("opposite_castling", 52, "")
        elif enemy_king in (chess.E1, chess.E8) and ply >= 14:
            add("king_centre", 42, "")

    # Knight outposts, rooks on the seventh, doubled rooks.
    if moved.piece_type == chess.KNIGHT and 3 <= relative_rank(me, move.to_square) <= 5 and 2 <= chess.square_file(move.to_square) <= 5:
        guarded = board.attackers(me, move.to_square) & board.pieces(chess.PAWN, me)
        if guarded and not any(abs(chess.square_file(p) - chess.square_file(move.to_square)) == 1
                               and relative_rank(me, p) > relative_rank(me, move.to_square)
                               for p in board.pieces(chess.PAWN, you)):
            add("outpost", 46, str(move.to_square))
    if moved.piece_type == chess.ROOK and relative_rank(me, move.to_square) == 6 and relative_rank(me, move.from_square) != 6:
        seventh = [sq for sq in board.pieces(chess.PAWN, you) if relative_rank(me, sq) == 6]
        if seventh or (enemy_king is not None and relative_rank(me, enemy_king) == 7):
            add("seventh", 52, "")
    if moved.piece_type == chess.ROOK:
        for other in board.pieces(chess.ROOK, me):
            if other != move.to_square and chess.square_file(other) == chess.square_file(move.to_square) and \
                    not chess.SquareSet(chess.between(other, move.to_square)) & board.occupied:
                add("doubled_rooks", 44, "", file=FILE_NAMES[chess.square_file(other)])
                break

    # Trades.
    material = sum(VALUE[p.piece_type] * (1 if p.color == me else -1) for p in before.piece_map().values())
    previous_capture = None
    if before.move_stack:
        probe = before.copy(stack=True)
        last = probe.pop()
        if probe.is_capture(last) and last.to_square == move.to_square:
            previous_capture = probe.piece_type_at(last.to_square)
    # A trade: the opponent can take back, or this move took back.
    traded = captured and (board.attackers(you, move.to_square) or previous_capture)
    if traded and VALUE[captured.piece_type] >= 3 and material >= 3:
        add("trade_ahead", 45, "")
    if captured and captured.piece_type == chess.QUEEN and previous_capture == chess.QUEEN and not board.queens:
        add("queens_off", 40, "")
    if captured and captured.piece_type == chess.BISHOP and len(before.pieces(chess.BISHOP, you)) == 2 \
            and len(board.pieces(chess.BISHOP, me)) == 2 and not board.attackers(you, move.to_square):
        # Not while the opponent can simply take back and restore the balance.
        add("bishop_pair", 32, "")

    # Development race in the opening.
    if 8 <= ply <= 24 and mover_type in (chess.KNIGHT, chess.BISHOP) and not captured:
        mine, theirs = developed(board, me), developed(board, you)
        if mine - theirs >= 2:
            add("development_lead", 32, "", mine=COUNT.get(mine, str(mine)))

    found.sort(key=lambda insight: -insight.priority)
    return found


HOME = {chess.WHITE: (chess.B1, chess.G1, chess.C1, chess.F1), chess.BLACK: (chess.B8, chess.G8, chess.C8, chess.F8)}


def developed(board, color):
    """Knights and bishops no longer on their starting squares (captured ones do not count)."""
    minors = board.pieces(chess.KNIGHT, color) | board.pieces(chess.BISHOP, color)
    return sum(1 for sq in minors if sq not in HOME[color])


# Out of check, the only reading worth adding is that the king is still under fire.
ALL_BUT_PRESSURE = frozenset(INSIGHT) - {"still_pressed"}
# A theme already says this; the insight would repeat it.
COVERED = {
    "check_fork": {"fork", "threat", "discovered"}, "capture_check_fork": {"fork", "threat", "discovered"},
    "passed_pawn": {"new_passer"}, "castling": {"castle_ready"}, "escape": ALL_BUT_PRESSURE,
}
# Engine-led questions about a surprising move stand alone.
ALONE = {"danger", "intention", "capture_question"}
# Readings about the same thing that should not come back move after move.
SLOW = {"king_attack", "pawn_storm", "space", "open_lines", "development_lead", "still_pressed", "castle_ready",
        "pawn_break", "luft", "shelter"}


# In a fast game a line must be over before the next move: only a threat, a pin on the king, a fork.
FAST_PRIORITY = 70


def choose(found, motif, recent=(), fast=False):
    """The best reading not said lately, or None. In a fast game only the striking ones."""
    if motif in ALONE:
        return None
    recent = list(recent)
    for insight in found:
        if insight.key in COVERED.get(motif, ()):
            continue
        if fast and insight.priority < FAST_PRIORITY:
            continue
        ident = f"{insight.key}:{insight.subject}"
        if ident in recent[-8:] or (insight.key in SLOW and any(r.startswith(insight.key + ":") for r in recent[-4:])):
            continue
        if insight.key == "opening" and any(r.startswith("opening:") and r == ident for r in recent):
            continue
        return insight
    return None


def position_summary(board):
    """Strategic remarks about the whole board for a long think: no move, no verdict, no engine."""
    lines = []
    ply = board.ply()
    for color in (chess.WHITE, chess.BLACK):
        mine, theirs = developed(board, color), developed(board, not color)
        home = sum(1 for sq in HOME[not color] if board.piece_at(sq) and board.piece_at(sq).color != color
                   and board.piece_type_at(sq) in (chess.KNIGHT, chess.BISHOP))
        if 8 <= ply <= 30 and mine >= 3 and home >= 2:
            lines += fill(POSITION["development"], {"ahead": side_name(color), "behind": side_name(not color),
                                                     "count": COUNT.get(home, str(home))})
    wings = castled_wing(board, chess.WHITE), castled_wing(board, chess.BLACK)
    if all(wings) and wings[0] != wings[1]:
        lines += POSITION["opposite_castling"]
    if board.pieces(chess.ROOK, chess.WHITE) | board.pieces(chess.ROOK, chess.BLACK):
        open_files = [FILE_NAMES[f] for f in range(8)
                      if not any(chess.square_file(p) == f for p in chess.SquareSet(board.pawns))]
        if 1 <= len(open_files) <= 3 and ply >= 12:
            files = " và cột ".join(open_files[:2])
            lines += fill(POSITION["open_files" if len(open_files) > 1 else "open_file"], {"file": files})
    for color in (chess.WHITE, chess.BLACK):
        passers = [sq for sq in board.pieces(chess.PAWN, color)
                   if relative_rank(color, sq) >= 3 and passed(board, color, sq)]
        if passers:
            best = max(passers, key=lambda sq: relative_rank(color, sq))
            lines += fill(POSITION["passer"], {"owner": side_name(color), "square": square_name(best)})
        pressure = len(zone_attackers(board, color))
        if pressure >= 3 and ply >= 14:
            lines += fill(POSITION["king_pressure"], {"attacker": side_name(color), "defender": side_name(not color),
                                                       "count": COUNT.get(pressure, "nhiều")})
        if len(board.pieces(chess.BISHOP, color)) == 2 and len(board.pieces(chess.BISHOP, not color)) < 2:
            lines += fill(POSITION["bishop_pair"], {"owner": side_name(color)})
        files = [chess.square_file(p) for p in board.pieces(chess.PAWN, color)]
        doubled = sorted({f for f in files if files.count(f) > 1})
        if doubled:
            lines += fill(POSITION["doubled"], {"owner": side_name(color), "file": FILE_NAMES[doubled[0]]})
    return lines
