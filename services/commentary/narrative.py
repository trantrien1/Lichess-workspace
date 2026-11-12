"""Vietnamese commentary composed from legal-board observations and bounded engine evidence.

All variants are complete assertions checked here, rather than unvalidated free
text. Every live ply is eligible, independently of player color or human/bot role.
"""

import hashlib

import chess

from core import PIECES, square_name

CENTRE = (chess.D4, chess.E4, chess.D5, chess.E5)
VALUE = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}

INTRO_PHRASES = [
    "Ván đấu chính thức bắt đầu! Quân Trắng sẽ là người đi trước.",
    "Bàn cờ đã sẵn sàng, hãy cùng theo dõi cuộc đấu trí giữa hai kỳ thủ.",
    "Chào mừng bạn đến với ván đấu. Cùng chờ xem hai bên sẽ lựa chọn lối chơi nào.",
    "Một ván cờ mới lại mở ra, hứa hẹn nhiều pha xử lý thú vị từ cả hai phía.",
    "Hai kỳ thủ đã vào vị trí. Liệu hôm nay sẽ là đôi công rực lửa hay đấu trí chắc chắn?",
]
TRANSITION_TEXT = "Ván cờ đang diễn biến rất căng thẳng, cùng theo dõi thế mới nhất."
OPENING_CENTRAL = [
    "Trắng mở màn bằng nước đẩy tốt quen thuộc, chủ động chiếm lĩnh trung tâm.",
    "Không chần chừ, Trắng mở đường cho các quân chủ lực phát triển.",
    "Trắng đẩy tốt vào trung tâm, một cách nhập cuộc đầy chủ động.",
]
OPENING_FLEXIBLE = [
    "Trắng nhập cuộc điềm tĩnh, ưu tiên sự linh hoạt.",
    "Một nước mở màn mang tính thăm dò của Trắng.",
    "Trắng lựa chọn cách mở màn linh hoạt, chưa vội tấn công.",
]
OPENING_GENERAL = [
    "Nước cờ đầu tiên đã được đưa ra, cuộc đối đầu chính thức khởi động.",
    "Trắng đã đưa ra lời chào đầu tiên, cùng xem Đen đáp trả thế nào.",
]


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
    }


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


def event_phrases(move, position):
    side, piece = move["mover"], move["piece"]
    target = square_name(chess.parse_square(move["to"]))
    origin = square_name(chess.parse_square(move["from"]))
    if move["castling"]:
        return [f"{side} vừa nhập thành cánh {move['wing']}", f"{side} đưa vua về cánh {move['wing']} bằng nước nhập thành",
                f"{side} quyết định nhập thành cánh {move['wing']}", f"Vua {side.lower()} đã nhập thành về cánh {move['wing']}"]
    if move["promotion"]:
        capture = f"bắt {move['capture']} và " if move["capture"] else ""
        return [f"{side} {capture}phong cấp thành {move['promotion']} tại {target}",
                f"Tốt {side.lower()} đã {capture}phong cấp thành {move['promotion']} ở {target}"]
    if move["enPassant"]:
        return [f"{side} bắt tốt qua đường tại {target}", f"Một nước bắt tốt qua đường của {side.lower()} ở {target}"]
    if move["capture"]:
        return [f"{side} dùng {piece} bắt {move['capture']} tại {target}",
                f"{piece.capitalize()} {side.lower()} vừa ăn {move['capture']} ở {target}",
                f"{side} chọn bắt {move['capture']} bằng {piece} tại {target}",
                f"Quân {move['capture']} ở {target} đã bị {piece} {side.lower()} bắt"]
    if position.get("ambiguous"):
        return [f"{side} đưa {piece} từ {origin} đến {target}", f"{piece.capitalize()} {side.lower()} từ {origin} chuyển sang {target}"]
    if position.get("development"):
        return [f"{side} phát triển {piece} lên {target}", f"{side} đưa {piece} vào cuộc ở {target}",
                f"{piece.capitalize()} {side.lower()} rời hàng cuối, tiến lên {target}", f"{side} triển khai {piece} tại {target}"]
    return [f"{side} điều {piece} sang {target}", f"{side} chọn nước {piece} đến {target}",
            f"{piece.capitalize()} {side.lower()} vừa chuyển sang {target}", f"{side} đặt {piece} tại {target}"]


def theme_phrases(facts, quality):
    p, m = facts.values["position"], facts.values["move"]
    side = m["mover"]
    opponent = "Đen" if side == "Trắng" else "Trắng"
    piece = m["piece"]
    target = square_name(chess.parse_square(m["to"]))
    first_move = (facts.board.ply() == 1 and len(facts.board.move_stack) == 1
                  and facts.before.fen() == chess.STARTING_FEN)
    if first_move:
        if m["uci"] in ("e2e4", "d2d4"):
            return "opening_central", OPENING_CENTRAL + OPENING_GENERAL
        if m["uci"] in ("g1f3", "b1c3", "c2c4", "g2g3", "b2b3"):
            return "opening_flexible", OPENING_FLEXIBLE + OPENING_GENERAL
        return "opening_general", OPENING_GENERAL
    # Describe what just HAPPENED before discussing a possible next capture.
    # Actor and victim colors are explicit, including recaptures of equal pieces.
    if m["capture"]:
        action = f"{side} vừa dùng {piece} bắt {m['capture']} {opponent}"
        if m["enPassant"]:
            action += " qua đường"
        if m["promotion"]:
            action += f", phong {m['promotion']}"
        if m["check"]:
            if p["checkForkTargets"]:
                victim = p["checkForkTargets"][0]["piece"]
                return "capture_check_fork", [action + f", chiếu vua và nhắm {victim}!",
                                              action + f", vừa chiếu vừa đe dọa {victim}!"]
            return "capture_check", [action + ", đồng thời chiếu vua!", action + ", chiếu tướng!"]
        if m["promotion"]:
            return "promotion_capture", [action + "!",
                                         f"Tốt {side} bắt {m['capture']} {opponent} rồi phong {m['promotion']}!"]
        if quality["mistake"] or quality["lostAdvantage"]:
            return "capture_question", [action + f", liệu có ý đồ gì?",
                                        action + f", liệu còn tính toán phía sau?"]
        if p["recapture"]:
            return "exchange", [action + ".", f"{side} dùng {piece} bắt lại {m['capture']} {opponent}.",
                                f"{piece.capitalize()} {side} vừa bắt lại {m['capture']} {opponent} ở {target}."]
        return "capture", [action + ".", f"{piece.capitalize()} {side} bắt {m['capture']} {opponent} tại {target}.",
                           f"{side} dùng {piece} ăn {m['capture']} {opponent} ở {target}."]
    # This is a legal-board observation, independent of a noisy cp label. It
    # says the queen/rook is threatened, never that its capture is forced.
    if m["check"] and p["checkForkTargets"]:
        victim = p["checkForkTargets"][0]["piece"]
        return "check_fork", [f"Nguy hiểm, {side} vừa chiếu vua vừa đe dọa bắt {victim} {opponent}!",
                              f"{side} chiếu tướng, đồng thời nhắm vào {victim} {opponent}!",
                              f"Một đòn chiếu kèm đe dọa {victim} {opponent} của {side}!"]
    if quality["mistake"] or quality["lostAdvantage"]:
        return "intention", [f"Liệu {side} có ý đồ gì trong nước đi này?",
                             f"Một lựa chọn đáng chú ý của {side}, liệu còn tính toán phía sau?",
                             f"{side} đang hướng tới kế hoạch nào với nước vừa rồi?",
                             f"Liệu {side} đang chuẩn bị một ý tưởng khác?"]
    if quality["dangerous"]:
        return "danger", [f"{side} đang tạo sức ép chiếu hết!",
                          f"Rất nguy hiểm, {side} đang duy trì sức ép chiếu hết!"]
    if m["check"]:
        return "check", [f"{side} chiếu vua, {opponent} phải ứng phó!",
                         f"Chiếu vua, {side} đang gây sức ép!",
                         f"Vua {opponent} đang bị chiếu!"]
    if m["promotion"]:
        return "promotion", [f"{side} phong cấp thành {m['promotion']} tại {target}!",
                             f"Tốt {side} đã hóa {m['promotion']} ở {target}!"]
    if m["castling"]:
        return "castling", [f"{side} vừa nhập thành cánh {m['wing']}.",
                            f"{side} đưa vua về cánh {m['wing']}.",
                            f"Vua {side} đã nhập thành cánh {m['wing']}."]
    if p["escapingCheck"]:
        return "escape", [f"{side} đã hóa giải thế chiếu.", f"{side} vừa thoát chiếu!"]
    if p["looseMovedPiece"] and piece in ("mã", "tượng", "xe", "hậu"):
        return "contact", [f"{piece.capitalize()} {side} vừa đi đang bị tấn công.",
                           f"{piece.capitalize()} {side} vừa tới vị trí bị tấn công.",
                           f"{piece.capitalize()} {side} đang trong tầm bắt của đối phương."]
    if p["advancedPawn"]:
        return "passed_pawn", [f"Tốt thông {side} đã tiến rất sâu!",
                               f"{side} vừa đẩy tốt thông lên cao!",
                               f"Tốt thông {side} đang rất đáng chú ý!"]
    if p["openFile"]:
        return "open_file", [f"Xe {side} đã đứng trên cột mở.",
                             f"{side} vừa đưa xe vào cột mở.",
                             f"Xe {side} đang hoạt động trên cột mở."]
    if p["development"]:
        return "development", [f"{side} phát triển {piece} lên {target}.",
                               f"{side} đưa {piece} vào cuộc ở {target}.",
                               f"Thêm một {piece} {side} được triển khai.",
                               f"{piece.capitalize()} {side} đã nhập cuộc!"]
    if p["bothCentrePawns"] and facts.board.ply() <= 12:
        return "centre_duel", ["Hai bên đang giằng co ở trung tâm.",
                               f"{side} tiếp tục tranh chấp trung tâm.",
                               "Tốt hai bên đang đối đầu ở trung tâm."]
    if p["centreGain"]:
        return "centre", [f"{side} tăng sức kiểm soát trung tâm.",
                          f"{side} vừa tạo thêm ảnh hưởng ở trung tâm.",
                          f"Sức ép của {side} lên trung tâm vừa tăng."]
    if p["queenEarly"]:
        return "early_queen", [f"{side} đưa hậu ra khá sớm.", f"Hậu {side} đã sớm nhập cuộc."]
    if p["sparse"]:
        return "endgame", [f"{side} điều {piece} sang {target} trong tàn cuộc.",
                           f"{piece.capitalize()} {side} vừa chuyển sang {target}."]
    if quality["sound"]:
        return "quiet", [f"Một nước điều {piece} chắc tay của {side}.",
                         f"{side} vừa có một nước đi vững vàng.",
                         f"{side} giữ được chất lượng thế cờ."]
    return "quiet", [event + "." for event in event_phrases(m, p)]


def compose(facts, job):
    if "finish" in facts.values or "intro" in facts.values or "move" not in facts.values:
        return
    quality = assessment(facts)
    facts.values["assessment"] = quality
    motif, candidates = theme_phrases(facts, quality)
    # One short observation of the CURRENT position. Repeating the previous
    # move and appending generic praise delayed the voice and sounded rushed.
    seed = int(hashlib.sha256((job.gameId + "|" + " ".join(job.sanHistory)).encode()).hexdigest()[:12], 16)
    facts.alternatives = candidates[seed % len(candidates):] + candidates[:seed % len(candidates)]
    ids = ["move", "position"]
    if motif == "exchange":
        ids.append("previousMove")
    if quality["reliable"]:
        ids.append("engine")
    facts.values["narration"] = {"source": "verified-composer:6-flow", "theme": motif, "factIds": ids}
    facts.priority = 90 if "check_fork" in motif else 80 if motif in ("danger", "intention", "capture_question") else facts.priority
