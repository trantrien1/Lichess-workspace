"""Vietnamese commentary composed from legal-board observations and bounded engine evidence.

This module decides what is true and which motif fits; the sentences come from `phrases.py`.
Every live ply is eligible, independently of player color or human/bot role.
"""

import chess

from core import PIECES, square_name
from insight import analyse, choose, position_summary
from phrases import IDLE, MOVE, OPENING_CENTRAL, OPENING_FLEXIBLE, OPENING_GENERAL, OPENING_RARE, fill, seed_of

CENTRE = (chess.D4, chess.E4, chess.D5, chess.E5)
VALUE = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}


def describe(before, move):
    piece = before.piece_at(move.from_square)
    capture = before.piece_at(move.to_square)
    if before.is_en_passant(move):
        capture = chess.Piece(chess.PAWN, not before.turn)
    return {"mover": "Trắng" if before.turn else "Đen", "piece": PIECES[piece.piece_type],
            "from": chess.square_name(move.from_square), "to": chess.square_name(move.to_square),
            "capture": PIECES[capture.piece_type] if capture else None,
            "castling": before.is_castling(move), "wing": "vua" if before.is_kingside_castling(move) else "hậu",
            "promotion": PIECES.get(move.promotion), "enPassant": before.is_en_passant(move)}


def position_facts(facts):
    before, board = facts.before, facts.board
    last = board.peek()
    color = before.turn
    piece = before.piece_at(last.from_square)
    enemy = not color
    current = facts.values["move"]
    current["wing"] = "vua" if before.is_kingside_castling(last) else "hậu"
    previous = None
    if before.move_stack:
        previous_before = before.copy(stack=True)
        previous_move = previous_before.pop()
        previous = describe(previous_before, previous_move)
        facts.values["previousMove"] = {"source": "rules/history", **previous}
    development = (piece.piece_type in (chess.KNIGHT, chess.BISHOP)
                   and chess.square_rank(last.from_square) == (0 if color else 7) and board.ply() <= 20)
    # Do not call repeated shuffling back to the home rank fresh development.
    development = development and not any(m.from_square == last.from_square for m in before.move_stack)
    pressure_before = sum(len(before.attackers(color, sq)) for sq in CENTRE)
    pressure_after = sum(len(board.attackers(color, sq)) for sq in CENTRE)
    open_file = piece.piece_type == chess.ROOK and not any(
        board.piece_type_at(chess.square(chess.square_file(last.to_square), rank)) == chess.PAWN for rank in range(8))
    passed = piece.piece_type == chess.PAWN and not any(
        abs(chess.square_file(sq) - chess.square_file(last.to_square)) <= 1
        and ((chess.square_rank(sq) > chess.square_rank(last.to_square)) if color
             else (chess.square_rank(sq) < chess.square_rank(last.to_square)))
        for sq in board.pieces(chess.PAWN, enemy))
    # Static, legally capturable targets. A pinned pseudo-attack is not enough.
    probe = board.copy(stack=False)
    probe.turn = color
    old_targets = {(m.from_square, m.to_square) for m in before.legal_moves
                   if before.is_capture(m) and before.piece_type_at(m.to_square) in (chess.QUEEN, chess.ROOK)}
    targets = []
    for capture in probe.legal_moves:
        target = probe.piece_at(capture.to_square)
        if (capture.from_square == last.to_square or (capture.from_square, capture.to_square) not in old_targets) and target and target.color == enemy and target.piece_type in (chess.QUEEN, chess.ROOK):
            targets.append({"piece": PIECES[target.piece_type], "square": chess.square_name(capture.to_square)})
    loose = any(m.to_square == last.to_square and board.is_capture(m) for m in board.legal_moves)
    ambiguous = sum(m.to_square == last.to_square and before.piece_type_at(m.from_square) == piece.piece_type
                    for m in before.legal_moves) > 1
    material = sum(VALUE[p.piece_type] * (1 if p.color == color else -1) for p in board.piece_map().values())
    both_centre = (any(board.piece_at(sq) == chess.Piece(chess.PAWN, chess.WHITE) for sq in CENTRE)
                   and any(board.piece_at(sq) == chess.Piece(chess.PAWN, chess.BLACK) for sq in CENTRE))
    facts.values["position"] = {
        "source": "rules/board", "development": development,
        "centreGain": pressure_after > pressure_before, "bothCentrePawns": both_centre,
        "openFile": open_file, "passedPawn": passed,
        "advancedPawn": passed and chess.square_rank(last.to_square) in ((5, 6) if color else (1, 2)),
        "queenEarly": piece.piece_type == chess.QUEEN and board.ply() <= 10 and not current["capture"],
        "escapingCheck": before.is_check(), "checkForkTargets": sorted(targets, key=lambda t: t["piece"] == "hậu", reverse=True) if board.is_check() else [],
        "looseMovedPiece": loose, "ambiguous": ambiguous, "materialBalance": material,
        "recapture": bool(previous and previous["capture"] and current["capture"] and previous["to"] == current["to"]),
        "sparse": len(board.piece_map()) <= 10 and not board.queens,
        "checkAgain": board.is_check() and len(board.move_stack) >= 3 and checked_after(board, 2),
    }


def checked_after(board, back):
    """Whether the move `back` plies before the last one also gave check."""
    probe = board.copy(stack=True)
    for _ in range(back):
        probe.pop()
    return probe.is_check()


def assessment(facts):
    engine = facts.values.get("engine", {})
    pre, post = engine.get("before", {}), engine.get("after", {})
    reliable = (not engine.get("unavailable") and pre.get("pov") == post.get("pov")
                and not pre.get("bound", True) and not post.get("bound", True)
                and min(pre.get("depth") or 0, post.get("depth") or 0) >= 10
                and min(pre.get("nodes") or 0, post.get("nodes") or 0) >= 1000)
    loss = engine.get("lossCpHeuristic")
    sound = bool(reliable and loss is not None and loss <= 45 and post.get("cp") is not None
                 and post["cp"] >= -70)
    confirmed = bool(engine.get("confirmed"))
    fork = facts.values.get("position", {}).get("checkForkTargets", [])
    dangerous = bool(confirmed and ((sound and fork) or
                     (reliable and post.get("mate") is not None and post["mate"] > 0)))
    lost_mate = bool(reliable and confirmed and pre.get("mate") is not None and pre["mate"] > 0
                     and ((post.get("cp") is not None and post["cp"] <= 20)
                          or (post.get("mate") is not None and post["mate"] < 0)))
    return {"source": "engine/heuristic", "reliable": reliable, "sound": sound,
            "dangerous": dangerous, "lossCp": loss,
            "mistake": lost_mate or bool(reliable and confirmed and loss is not None and loss >= 150),
            "advantage": bool(sound and post["cp"] >= 100),
            "lostAdvantage": lost_mate or bool(reliable and confirmed and loss is not None and loss >= 150
                                  and pre.get("cp") is not None and post.get("cp") is not None
                                  and pre["cp"] >= 100 and post["cp"] <= 20)}


def move_context(facts):
    """The verified facts a move sentence may mention."""
    m, p = facts.values["move"], facts.values.get("position", {})
    side = m["mover"]
    opponent = "Đen" if side == "Trắng" else "Trắng"
    piece, captured = m["piece"], m["capture"] or ""
    action = f"{side} vừa dùng {piece} bắt {captured} {opponent}" if captured else ""
    if m["enPassant"]:
        action += " qua đường"
    if m["promotion"]:
        action += f", phong {m['promotion']}"
    wing = m.get("wing") or ("vua" if chess.square_file(chess.parse_square(m["to"])) >= 4 else "hậu")
    targets = p.get("checkForkTargets") or []
    return {"side": side, "opponent": opponent, "to_move": opponent, "piece": piece, "Piece": piece.capitalize(),
            "captured": captured, "Captured": captured.capitalize(),
            "target": square_name(chess.parse_square(m["to"])), "origin": square_name(chess.parse_square(m["from"])),
            "wing": wing, "castle": "ngắn" if wing == "vua" else "dài", "promo": m["promotion"] or "",
            "victim": targets[0]["piece"] if targets else "", "action": action}


def brief(facts):
    """The move in a few words, for players moving faster than a sentence can be read."""
    m, ctx = facts.values["move"], move_context(facts)
    if m["castling"]:
        text = f"{ctx['side']} nhập thành {ctx['castle']}"
    elif m["promotion"]:
        text = f"Tốt {ctx['side']} phong {ctx['promo']} ở {ctx['target']}"
    elif m["capture"]:
        text = f"{ctx['Piece']} {ctx['side']} ăn {ctx['captured']} {ctx['target']}"
    else:
        text = f"{ctx['Piece']} {ctx['side']} tới {ctx['target']}"
    return text + (", chiếu!" if m["check"] else ".")


def event_phrases(move, position, ctx):
    """Plain descriptions of the move."""
    if move["castling"]:
        return fill(MOVE["castling"], ctx)
    if move["promotion"]:
        return fill(MOVE["promotion"], ctx)
    if move["enPassant"]:
        return fill(MOVE["en_passant"], ctx)
    if move["capture"]:
        return fill(MOVE["capture"], ctx)
    if position.get("ambiguous"):
        return fill(MOVE["event_ambiguous"], ctx)
    if position.get("development"):
        return fill(MOVE["development"], ctx)
    if move["piece"] == "tốt":
        return fill(MOVE["event_pawn"], ctx)
    if move["piece"] == "vua":
        return fill(MOVE["event_king"], ctx)
    return fill(MOVE["event"], ctx)


def theme_phrases(facts, quality):
    p, m = facts.values["position"], facts.values["move"]
    ctx = move_context(facts)
    first_move = (facts.board.ply() == 1 and len(facts.board.move_stack) == 1
                  and facts.before.fen() == chess.STARTING_FEN)
    if first_move:
        if m["uci"] in ("e2e4", "d2d4"):
            return "opening_central", OPENING_CENTRAL + OPENING_GENERAL
        if m["uci"] in ("g1f3", "b1c3", "c2c4", "g2g3", "b2b3"):
            return "opening_flexible", OPENING_FLEXIBLE + OPENING_GENERAL
        return "opening_general", OPENING_RARE + OPENING_GENERAL

    def say(motif, *keys):
        return motif, fill([t for key in (keys or (motif,)) for t in MOVE[key]], ctx)
    # Describe what just HAPPENED before discussing a possible next capture.
    # Actor and victim colors are explicit, including recaptures of equal pieces.
    if m["capture"]:
        if m["check"]:
            return say("capture_check_fork") if p["checkForkTargets"] else say("capture_check")
        if m["promotion"]:
            return say("promotion_capture")
        # Taking back is the natural reply; "is there an idea behind it?" sounds absurd there.
        if p["recapture"]:
            return say("exchange")
        if quality["mistake"] or quality["lostAdvantage"]:
            return say("capture_question")
        if m["enPassant"]:
            return say("en_passant")
        return say("capture")
    # This is a legal-board observation, independent of a noisy cp label. It
    # says the queen/rook is threatened, never that its capture is forced.
    if m["check"] and p["checkForkTargets"]:
        return say("check_fork")
    if quality["mistake"] or quality["lostAdvantage"]:
        return say("intention")
    if quality["dangerous"]:
        return say("danger")
    if m["check"]:
        return say("check_again", "check_again", "check") if p["checkAgain"] else say("check")
    if m["promotion"]:
        extra = "queen_promotion" if m["promotion"] == "hậu" else "underpromotion"
        return say("promotion", extra, "promotion")
    if m["castling"]:
        return say("castling")
    if p["escapingCheck"]:
        return "escape", fill(MOVE["escape_king" if m["piece"] == "vua" else "escape_block"], ctx)
    if p["advancedPawn"]:
        return say("passed_pawn")
    if p["openFile"]:
        return say("open_file")
    if p["development"]:
        return say("development")
    if p["bothCentrePawns"] and facts.board.ply() <= 12:
        return say("centre_duel")
    if p["centreGain"] and facts.board.ply() <= 30:
        return say("centre")
    if p["queenEarly"]:
        return say("early_queen")
    if p["sparse"]:
        extra = {"vua": "endgame_king", "tốt": "endgame_pawn"}.get(m["piece"])
        return say("endgame", *((extra, "endgame") if extra else ()))
    if quality["sound"]:
        return say("steady")
    return "quiet", event_phrases(m, p, ctx)


# Observations that do not name the move. Said twice in a row they sound like a
# stuck record, so a recent repeat falls back to describing the move itself.
GENERIC_MOTIFS = frozenset({"centre", "centre_duel", "early_queen", "open_file", "passed_pawn", "steady"})


def compose(facts, job, recent_motifs=(), recent_insights=(), fast=False):
    """The sentence about the move, and (in `facts.insight`) at most one reading of why it was played.

    `fast`: the players are moving quickly, so only the striking readings are added."""
    if "finish" in facts.values or "intro" in facts.values or "move" not in facts.values:
        return
    quality = assessment(facts)
    facts.values["assessment"] = quality
    motif, candidates = theme_phrases(facts, quality)
    if motif in GENERIC_MOTIFS and motif in list(recent_motifs)[-3:]:
        motif = "event"
        candidates = event_phrases(facts.values["move"], facts.values["position"], move_context(facts))
    # One short observation of the CURRENT position. Repeating the previous
    # move and appending generic praise delayed the voice and sounded rushed.
    seed = seed_of(job.gameId, " ".join(job.sanHistory))
    candidates = list(dict.fromkeys(candidates))
    facts.alternatives = candidates[seed % len(candidates):] + candidates[:seed % len(candidates)]
    ids = ["move", "position"]
    if motif == "exchange":
        ids.append("previousMove")
    if quality["reliable"]:
        ids.append("engine")
    facts.values["narration"] = {"source": "verified-composer:6-flow", "theme": motif, "factIds": ids}
    facts.priority = 90 if "check_fork" in motif else 80 if motif in ("danger", "intention", "capture_question") else facts.priority
    if not motif.startswith("opening_"):
        facts.insight = choose(analyse(facts, job.sanHistory), motif, recent_insights, fast)
        if facts.insight:
            facts.values["insight"] = {"source": "rules/board", "key": facts.insight.key}
            ids.append("insight")
            if facts.insight.priority >= 64:
                facts.priority = max(facts.priority, 60)


def clock_text(seconds):
    minutes, rest = divmod(int(seconds), 60)
    if minutes and rest:
        return f"{minutes} phút {rest} giây"
    return f"{minutes} phút" if minutes else f"{rest} giây"


def castled(board):
    """Colors that castled during the game, from the replayed history."""
    probe, done = board.root(), set()
    for move in board.move_stack:
        if probe.is_castling(move):
            done.add(probe.turn)
        probe.push(move)
    return done


IDLE_ORDER = ("check", "position", "kings", "material", "recap", "phase", "clock", "thinking")


def idle_facts(job, facts):
    """What a commentator says while a player thinks: the clock, material, kings, the phase, the last move.

    Board, history and clock only. No engine: nothing here may hint at what to play.
    """
    board, before = facts.board, facts.before
    side, other = ("Trắng", "Đen") if board.turn else ("Đen", "Trắng")
    ctx = {"side": side, "other": other}
    thinking = job.thinkingMs // 1000
    pieces = len(board.piece_map())
    phase = ("opening" if board.ply() <= 16 and pieces >= 28
             else "endgame" if pieces <= 14 or (not board.queens and pieces <= 20) else "middlegame")
    material = sum(VALUE[p.piece_type] * (1 if p.color else -1) for p in board.piece_map().values())
    done = castled(board)
    topics = {}
    if board.is_check():
        topics["check"] = fill(IDLE["check"], ctx)
    if not board.move_stack:
        topics["thinking"] = list(IDLE["first"])
    else:
        topics["thinking"] = fill(IDLE["thinking"], ctx)
        if thinking >= 40:
            topics["thinking"] = fill(IDLE["thinking_long"], ctx | {"seconds": thinking // 10 * 10}) + topics["thinking"]
    low = False
    if job.clock:
        mine, theirs = (job.clock.white, job.clock.black) if board.turn else (job.clock.black, job.clock.white)
        times = ctx | {"time": clock_text(mine), "other_time": clock_text(theirs)}
        low = mine <= 20
        if low:
            topics["clock"] = fill(IDLE["clock_low"], times)
        else:
            topics["clock"] = fill(IDLE["clock"], times)
            if mine * 2 <= theirs and mine < 120:
                topics["clock"] += fill(IDLE["clock_behind"], times)
            elif theirs * 2 <= mine and theirs < 120:
                topics["clock"] += fill(IDLE["clock_ahead"], times)
    if board.move_stack:
        leader, trailer = ("Trắng", "Đen") if material > 0 else ("Đen", "Trắng")
        size = abs(material)
        key = ("material_equal" if size == 0 else "material_one" if size == 1
               else "material_two" if size == 2 else "material_big")
        topics["material"] = fill(IDLE[key], {"leader": leader, "trailer": trailer})
    if board.ply() >= 8:
        if len(done) == 2:
            # Old news by the middlegame: worth saying only shortly after the second castling.
            if board.ply() <= 20:
                topics["kings"] = list(IDLE["kings_both"])
        elif done:
            yes, no = ("Trắng", "Đen") if chess.WHITE in done else ("Đen", "Trắng")
            topics["kings"] = fill(IDLE["kings_one"], {"yes": yes, "no": no})
        else:
            topics["kings"] = list(IDLE["kings_none"])
    if board.ply() >= 4:
        topics["phase"] = fill(IDLE[f"phase_{phase}"], {"move": board.fullmove_number, "pieces": pieces})
        if not board.queens:
            topics["phase"] += IDLE["queens_off"]
    if board.move_stack:
        last = describe(before, board.peek())
        recap = {"mover": last["mover"], "victim": side, "piece": last["piece"], "captured": last["capture"] or "",
                 "target": last["to"], "wing": last["wing"], "castle": "ngắn" if last["wing"] == "vua" else "dài",
                 "promo": last["promotion"] or ""}
        key = ("recap_castling" if last["castling"] else "recap_promotion" if last["promotion"]
               else "recap_en_passant" if last["enPassant"] else "recap_capture" if last["capture"]
               else "recap_move")
        topics["recap"] = fill(IDLE[key], recap)
    if board.move_stack and (summary := position_summary(board)):
        topics["position"] = summary
    # The first remark in a think reads the board; the second looks at the clock or the thinking;
    # later ones take the remaining topics in turn. A check or a nearly empty clock always comes first.
    present = [name for name in IDLE_ORDER if name in topics]
    second = next((name for name in ("clock", "thinking") if name in topics and name != present[0]), None)
    sequence = present[:1] + ([second] if second else []) + [name for name in present[1:] if name != second]
    primary = ("check" if "check" in topics else "clock" if low
               else sequence[(max(job.idleCount, 1) - 1) % len(sequence)])
    start = sequence.index(primary)
    others = [topics[name][0] for name in sequence[start + 1:] + sequence[:start]][:2]
    facts.alternatives = list(dict.fromkeys(topics[primary] + others))
    facts.values["idle"] = {"source": "rules/lila-clock", "side": side, "count": job.idleCount,
                            "thinkingS": thinking, "phase": phase, "materialBalance": material,
                            "castled": sorted("white" if c else "black" for c in done),
                            "clock": job.clock.model_dump() if job.clock else None}
    facts.values["narration"] = {"source": "idle-composer", "theme": f"idle_{primary}", "factIds": ["idle"]}
    return facts
