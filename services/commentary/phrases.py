"""Every sentence the Vietnamese commentator can say, written in advance. There is no LLM.

`narrative.py` works out what is true about the move or the position and picks a motif; the
templates for that motif are filled in with verified facts only: the side, the piece, the square.
`dress` may add a short lead-in or closing line, written for that motif, so it never claims
anything the motif has not already established.

Nothing here is copied. No public corpus of live or spoken chess commentary exists, in
Vietnamese or in English: GameKnot annotations (Jhamtani et al., ACL 2018) are written and
unlicensed, and Lichess broadcast PGNs (CC BY-SA) carry no speech. The categories follow that
study (what was played, why, plans, game context) and broadcast practice: lead with the event, one
short sentence, sometimes a question to the side about to move, no mind reading, little hype.
"""

import hashlib
import re
import unicodedata

INTRO = [
    "Ván đấu chính thức bắt đầu! Quân Trắng sẽ là người đi trước.",
    "Bàn cờ đã sẵn sàng, hãy cùng theo dõi cuộc đấu trí giữa hai kỳ thủ.",
    "Chào mừng bạn đến với ván đấu. Cùng chờ xem hai bên sẽ lựa chọn lối chơi nào.",
    "Một ván cờ mới lại mở ra, hứa hẹn nhiều pha xử lý thú vị từ cả hai phía.",
    "Hai kỳ thủ đã vào vị trí. Liệu hôm nay sẽ là đôi công rực lửa hay đấu trí chắc chắn?",
    "Xin chào quý vị! Ván cờ sắp bắt đầu, Trắng sẽ có nước đi đầu tiên.",
    "Mọi quân cờ đã về đúng vị trí, cùng chờ nước mở màn của Trắng.",
    "Chào mừng quý vị đến với bàn cờ hôm nay. Ván đấu xin được phép bắt đầu!",
    "Ba mươi hai quân cờ đã vào vị trí. Ván đấu bắt đầu thôi!",
    "Xin chào, chúc hai kỳ thủ có một ván cờ thật hay!",
    "Ván cờ bắt đầu! Trắng đi trước, Đen sẽ đáp lời.",
    "Mời quý vị cùng theo dõi ván đấu. Hy vọng đây sẽ là một trận cờ hấp dẫn.",
    "Bàn cờ đã sẵn sàng, ai sẽ làm chủ thế trận hôm nay?",
    "Bắt đầu nào! Cùng theo dõi những nước cờ đầu tiên.",
]

# Said when several moves went by unspoken, before the newest one.
TRANSITIONS = [
    "Các nước đi đang đến rất nhanh, cùng cập nhật thế cờ mới nhất.",
    "Hai bên đang đi rất nhanh, chúng ta cùng bắt kịp diễn biến.",
    "Ván cờ trôi đi rất nhanh, cùng theo dõi thế cờ mới nhất.",
    "Nhiều nước đi liên tiếp, cùng nhìn vào thế cờ hiện tại.",
    "Tốc độ rất cao, chúng ta tập trung vào nước mới nhất.",
]

# White's first move. Spoken from audio made at startup, so these take no placeholders.
OPENING_CENTRAL = [  # e4, d4
    "Trắng mở màn bằng nước đẩy tốt quen thuộc, chủ động chiếm lĩnh trung tâm.",
    "Không chần chừ, Trắng mở đường cho các quân chủ lực phát triển.",
    "Trắng đẩy tốt vào trung tâm, một cách nhập cuộc đầy chủ động.",
    "Một nước mở màn kinh điển! Trắng đặt ngay một tốt vào giữa bàn cờ.",
    "Trắng tiến tốt hai ô vào trung tâm, lối mở màn quen thuộc bậc nhất.",
    "Tốt trung tâm đã tiến lên, Trắng muốn tranh giành trung tâm ngay từ đầu.",
]
OPENING_FLEXIBLE = [  # Nf3, Nc3, c4, g3, b3
    "Trắng nhập cuộc điềm tĩnh, ưu tiên sự linh hoạt.",
    "Một nước mở màn mang tính thăm dò của Trắng.",
    "Trắng lựa chọn cách mở màn linh hoạt, chưa vội tấn công.",
    "Trắng chưa vội bộc lộ ý đồ, giữ nhiều lựa chọn cho các nước tiếp theo.",
    "Một cách mở màn mềm mại, để ngỏ nhiều hướng phát triển.",
    "Trắng chọn lối mở màn kín đáo, không đi theo con đường quen thuộc nhất.",
]
OPENING_GENERAL = [  # any other first move
    "Nước cờ đầu tiên đã được đưa ra, cuộc đối đầu chính thức khởi động.",
    "Trắng đã đưa ra lời chào đầu tiên, cùng xem Đen đáp trả thế nào.",
    "Ván cờ đã chính thức lăn bánh với nước đi đầu tiên của Trắng.",
    "Trắng đi nước đầu tiên, giờ đến lượt Đen lên tiếng.",
]
OPENING_RARE = [  # not among e4, d4, Nf3, Nc3, c4, g3, b3
    "Một lựa chọn mở màn ít gặp của Trắng, thú vị đây!",
    "Trắng không đi theo lối mở màn phổ biến, ván cờ có màu sắc riêng ngay từ đầu.",
]

# Moves. Placeholders: {side} moved, {opponent} (also {to_move}), {piece} / {Piece}, {captured} /
# {Captured}, {target} and {origin} squares, {wing} (vua / hậu), {castle} (ngắn / dài),
# {promo} (promoted piece), {victim} (queen or rook now threatened), {action} (who took what).
MOVE = {
    "capture": [
        "{side} vừa dùng {piece} bắt {captured} {opponent}.",
        "{Piece} {side} bắt {captured} {opponent} tại {target}.",
        "{side} dùng {piece} ăn {captured} {opponent} ở {target}.",
        "{side} ra tay! {Piece} ăn {captured} tại {target}.",
        "Tại {target}, {piece} {side} vừa hạ gục {captured} {opponent}.",
        "{Captured} {opponent} ở {target} đã bị {piece} {side} bắt gọn.",
        "Một quân {captured} {opponent} vừa bị {piece} {side} loại khỏi bàn cờ.",
        "{Piece} {side} lao vào {target}, {captured} {opponent} rời bàn cờ.",
        "Không chần chừ, {side} hốt gọn {captured} của {opponent}!",
        "Lực lượng của {opponent} vơi đi sau pha bắt {captured} ở {target}.",
    ],
    "en_passant": [
        "{side} bắt tốt qua đường, tốt tiến lên {target}!",
        "Bắt tốt qua đường! Tốt {side} dừng lại ở {target}.",
        "{side} vừa bắt tốt {opponent} qua đường.",
        "Một nước bắt tốt qua đường của {side}, không phải ván nào cũng được thấy!",
    ],
    # {action} already says what was taken (and "qua đường", or the promotion), so these are
    # safe for every kind of capture.
    "capture_check": [
        "{action}, đồng thời chiếu vua!",
        "{action}, chiếu tướng!",
        "{action} và chiếu luôn vua {opponent}!",
        "Ăn quân kèm theo nước chiếu! {opponent} phải ứng phó ngay.",
        "{opponent} vừa mất {captured} lại còn bị chiếu!",
    ],
    "capture_check_fork": [
        "{action}, chiếu vua và nhắm {victim}!",
        "{action}, vừa chiếu vừa đe dọa {victim}!",
        "Nguy hiểm! {action}, chiếu vua và còn nhắm tới {victim} {opponent}!",
        "{action}! Vua {opponent} bị chiếu, {victim} cũng đang bị đe dọa.",
    ],
    "promotion_capture": [
        "{action}!",
        "Tốt {side} bắt {captured} {opponent} rồi phong {promo}!",
        "Vừa ăn {captured} vừa phong cấp, {side} có thêm một {promo} tại {target}!",
        "Tốt {side} ăn chéo lên {target} và hóa thành {promo}!",
    ],
    # The engine (checked twice) thinks the move loses a lot. A player hears this mid-game, so it is
    # a question, never a verdict.
    "capture_question": [
        "{action}, liệu có ý đồ gì?",
        "{action}, liệu còn tính toán phía sau?",
        "{action}. Không biết {side} đang nhắm tới điều gì?",
        "{action}, có lẽ {side} đã tính trước điều gì đó?",
    ],
    "exchange": [
        "{side} dùng {piece} bắt lại {captured} {opponent}.",
        "{Piece} {side} vừa bắt lại {captured} {opponent} ở {target}.",
        "{side} ăn lại {captured} ngay tại {target}, hai bên trao đổi quân.",
        "{side} không chần chừ ăn lại {captured} tại {target}.",
        "Màn trao đổi quân tại {target}, {piece} {side} vừa ăn lại {captured}.",
        "Đổi quân! {side} bắt lại bằng {piece} ở {target}.",
        "Ăn qua ăn lại, {side} vừa bắt lại {captured} tại {target}.",
        "{side} ăn lại {captured} ngay, thế cờ được đơn giản hóa.",
    ],
    # The threat may come from a piece other than the one that moved (a discovered check), so
    # these never say which piece gives check or makes the threat.
    "check_fork": [
        "Nguy hiểm, {side} vừa chiếu vua vừa đe dọa bắt {victim} {opponent}!",
        "{side} chiếu tướng, đồng thời nhắm vào {victim} {opponent}!",
        "Một đòn chiếu kèm đe dọa {victim} {opponent} của {side}!",
        "Đòn hiểm! {side} chiếu vua và đe dọa cả {victim} {opponent}.",
        "Chiếu! Và không chỉ vậy, {victim} {opponent} cũng đang bị nhắm tới!",
    ],
    "intention": [
        "{Piece} {side} sang {target}. Liệu {side} có ý đồ gì?",
        "{side} đưa {piece} tới {target}, liệu còn tính toán phía sau?",
        "{Piece} {side} tới {target}. {side} đang hướng tới kế hoạch nào?",
        "{side} chọn {piece} {target}, một lựa chọn cần thời gian để hiểu.",
    ],
    # Only when the engine (checked twice) finds a forced mate for the side that moved.
    "danger": [
        "{side} đang tạo sức ép chiếu hết!",
        "Rất nguy hiểm, {side} đang duy trì sức ép chiếu hết!",
        "Vua {opponent} đang gặp nguy hiểm thực sự!",
        "{side} đang nhắm thẳng vào vua {opponent}!",
    ],
    "check": [
        "{side} chiếu vua, {opponent} phải ứng phó!",
        "Chiếu vua, {side} đang gây sức ép!",
        "Vua {opponent} đang bị chiếu!",
        "Chiếu! {opponent} buộc phải xử lý ngay.",
        "{side} tung đòn chiếu, {opponent} phải hóa giải.",
        "Một nước chiếu từ {side}, vua {opponent} không được yên.",
        "{opponent} bị chiếu, mọi kế hoạch khác phải tạm gác lại.",
        "{Piece} {side} tới {target}, chiếu!",
        "Đòn chiếu thẳng mặt! {opponent} phải tìm cách đối phó ngay.",
        "Chiếu! {opponent} phải chống chiếu.",
    ],
    # The side that moved also gave check with its previous move.
    "check_again": [
        "Lại một nước chiếu nữa của {side}!",
        "{side} tiếp tục chiếu, vua {opponent} chưa được yên.",
        "Thêm một nước chiếu! {side} không cho {opponent} kịp thở.",
        "Vẫn là chiếu! {side} liên tục gây sức ép lên vua {opponent}.",
    ],
    "promotion": [
        "{side} phong cấp thành {promo} tại {target}!",
        "Tốt {side} đã hóa {promo} ở {target}!",
        "Phong cấp! {side} có thêm một {promo} mới.",
        "Chú tốt {side} đã đi hết chặng đường và trở thành {promo}!",
        "Khoảnh khắc đáng nhớ, tốt {side} phong {promo} tại {target}!",
        "Một {promo} mới của {side} vừa xuất hiện trên bàn cờ!",
    ],
    "queen_promotion": [
        "Hóa hậu! Tốt {side} đã trở thành hậu tại {target}.",
    ],
    "underpromotion": [
        "Phong {promo} chứ không phải hậu, một lựa chọn đặc biệt của {side}!",
    ],
    "castling": [
        "{side} vừa nhập thành cánh {wing}.",
        "{side} đưa vua về cánh {wing}.",
        "Vua {side} đã nhập thành cánh {wing}.",
        "{side} nhập thành {castle}, xe cũng được đưa vào cuộc.",
        "Nhập thành {castle}! {side} sắp xếp lại vị trí cho vua.",
        "{side} chọn nhập thành {castle}.",
        "Vua và xe {side} cùng dịch chuyển, nhập thành {castle} đã hoàn tất.",
    ],
    # Out of check without a capture: the king stepped away, or another piece blocked.
    "escape_king": [
        "{side} đã hóa giải thế chiếu.",
        "{side} vừa thoát chiếu!",
        "Vua {side} tránh sang {target}, thoát khỏi nước chiếu.",
        "Vua {side} bước sang {target}, thế chiếu đã được hóa giải.",
        "{side} đưa vua ra khỏi tầm chiếu.",
        "Vua {side} né sang {target}.",
        "{side} đã chống chiếu thành công.",
    ],
    "escape_block": [
        "{side} đã hóa giải thế chiếu.",
        "{side} vừa thoát chiếu!",
        "{Piece} {side} vào chắn ở {target}, hóa giải thế chiếu.",
        "{side} dùng {piece} che chắn cho vua.",
        "Thế chiếu đã được {side} hóa giải bằng {piece}.",
        "{side} kịp thời đưa {piece} ra che chắn, hóa giải nước chiếu.",
        "{side} đã chống chiếu thành công.",
    ],
    # A passed pawn on its sixth or seventh rank.
    "passed_pawn": [
        "Tốt thông {side} đã tiến sâu tới {target}!",
        "{side} đẩy tốt thông lên {target}!",
        "Tốt thông {side} ở {target} đang rất đáng chú ý!",
        "Chú tốt thông của {side} đã tới {target}!",
        "Tốt thông {side} lên {target}, không còn xa hàng phong cấp.",
        "Tốt thông {side} đã lên tới {target}, rất gần hàng phong cấp.",
    ],
    # A rook on a file with no pawns at all.
    "open_file": [
        "Xe {side} tới {target}, đứng trên cột mở.",
        "{side} đưa xe vào cột mở ở {target}.",
        "Xe {side} ở {target} giờ hoạt động trên cột mở.",
        "Xe {side} chiếm lấy cột mở tại {target}.",
        "Cột này đã thông thoáng, xe {side} tận dụng ngay bằng nước tới {target}.",
    ],
    # A knight or bishop leaving its home rank for the first time, in the first 20 plies.
    "development": [
        "{side} phát triển {piece} lên {target}.",
        "{side} đưa {piece} vào cuộc ở {target}.",
        "Thêm một {piece} {side} được triển khai, lần này ở {target}.",
        "{Piece} {side} nhập cuộc ở {target}!",
        "{Piece} {side} rời hàng cuối, tiến lên {target}.",
        "{side} tiếp tục phát triển lực lượng, {piece} ra {target}.",
        "Một nước phát triển tự nhiên, {piece} {side} ra {target}.",
        "{side} triển khai {piece} tại {target}.",
    ],
    # Both sides have a pawn on d4, e4, d5 or e5, in the first 12 plies.
    "centre_duel": [
        "{Piece} {side} tới {target}, hai bên giằng co ở trung tâm.",
        "{side} đưa {piece} lên {target}, trung tâm đang là điểm nóng.",
        "{Piece} {side} ở {target}, tốt hai bên đã đối đầu ở trung tâm.",
        "{side} chọn {piece} {target}, cuộc tranh giành trung tâm bắt đầu.",
    ],
    # The mover now attacks d4, e4, d5 and e5 more often than before the move.
    "centre": [
        "{Piece} {side} tới {target}, tăng sức kiểm soát trung tâm.",
        "{side} đưa {piece} sang {target}, thêm ảnh hưởng ở trung tâm.",
        "Với {piece} ở {target}, {side} nắm trung tâm chặt hơn.",
        "{Piece} {side} sang {target}, nhắm vào các ô trung tâm.",
    ],
    "early_queen": [
        "{side} đưa hậu ra {target} khá sớm.",
        "Hậu {side} đã sớm nhập cuộc ở {target}.",
        "Hậu {side} xuất trận tới {target} từ rất sớm!",
        "Mới vào ván mà hậu {side} đã ra {target}.",
        "{side} tung hậu tới {target} ngay từ khai cuộc.",
    ],
    "endgame": [
        "{side} điều {piece} sang {target} trong tàn cuộc.",
        "{Piece} {side} vừa chuyển sang {target}.",
        "Tàn cuộc tiếp diễn, {side} đưa {piece} tới {target}.",
        "Trong tàn cuộc, {side} kiên nhẫn đưa {piece} sang {target}.",
    ],
    "endgame_king": [
        "Vua {side} tiến ra {target}, trong tàn cuộc vua cũng là một quân chiến đấu.",
        "Vua {side} chủ động bước tới {target}.",
        "Vua {side} xung phong tới {target}, đúng tinh thần của cờ tàn!",
    ],
    "endgame_pawn": [
        "Tốt {side} tiến lên {target}, trong tàn cuộc mỗi bước tốt đều đáng giá.",
        "{side} đẩy tốt lên {target}.",
    ],
    # The engine finds the move sound (small loss, no worse than slightly behind).
    "steady": [
        "{side} điều {piece} sang {target}.",
        "{Piece} {side} chuyển từ {origin} sang {target}.",
        "{Piece} {side} sang {target}, một lựa chọn chắc chắn.",
        "Nước đi điềm tĩnh của {side}, {piece} tới {target}.",
    ],
    # Plain descriptions of the move, when nothing else stands out.
    "event_ambiguous": [
        "{side} đưa {piece} từ {origin} đến {target}.",
        "{Piece} {side} từ {origin} chuyển sang {target}.",
        "{Piece} ở {origin} của {side} vừa tới {target}.",
        "{side} chọn {piece} ở {origin} để đi tới {target}.",
    ],
    "event_pawn": [
        "{side} đẩy tốt lên {target}.",
        "Tốt {side} tiến lên {target}.",
        "{side} tiến tốt tới {target}.",
        "Tốt {side} được đẩy lên {target}.",
        "{side} chọn đẩy tốt {target}.",
    ],
    "event_king": [
        "Vua {side} bước sang {target}.",
        "{side} di chuyển vua tới {target}.",
        "Vua {side} đổi vị trí, giờ đứng ở {target}.",
        "{side} đưa vua sang {target}.",
    ],
    "event": [
        "{side} điều {piece} sang {target}.",
        "{side} chọn nước {piece} đến {target}.",
        "{Piece} {side} vừa chuyển sang {target}.",
        "{side} đặt {piece} tại {target}.",
        "{Piece} {side} di chuyển tới {target}.",
        "{side} đưa {piece} tới {target}.",
        "{Piece} {side} đổi vị trí, giờ đứng ở {target}.",
    ],
}

# While a player thinks. {side} is the side to move, {other} the side that just moved.
IDLE = {
    "first": [
        "Trắng vẫn đang cân nhắc nước đi đầu tiên.",
        "Mọi thứ đã sẵn sàng, chỉ còn chờ nước đi đầu tiên của Trắng.",
        "Bàn cờ vẫn nguyên vẹn, Trắng chưa đi nước nào.",
        "Chúng ta vẫn đang chờ nước mở màn của Trắng.",
    ],
    "check": [
        "{side} đang bị chiếu và phải tìm cách hóa giải.",
        "Vua {side} đang bị chiếu, {side} cần tìm nước ứng phó.",
        "{side} cần xử lý thế chiếu trước tiên.",
        "Thế chiếu vẫn đang chờ {side} hóa giải.",
    ],
    "thinking": [
        "{side} đang suy nghĩ khá lâu ở nước này.",
        "{side} vẫn đang cân nhắc, chưa vội đưa ra quyết định.",
        "Đến lượt {side}, một thời điểm cần nhiều tính toán.",
        "Vẫn chưa có nước đi nào từ {side}.",
        "Chúng ta vẫn đang chờ nước đi của {side}.",
        "{side} đang dành thời gian để cân nhắc các phương án.",
        "Một khoảng lặng trên bàn cờ, {side} vẫn đang suy nghĩ.",
        "{side} chưa đi, có vẻ đang tính toán rất kỹ.",
        "Một quãng lặng dài, {side} đang tìm lời giải cho thế cờ.",
        "{side} cân nhắc rất kỹ, thế cờ này không thể vội.",
    ],
    "thinking_long": [
        "{side} đã suy nghĩ hơn {seconds} giây cho nước này.",
        "Đã hơn {seconds} giây trôi qua, {side} vẫn chưa đi.",
    ],
    "clock_low": [
        "{side} chỉ còn {time} trên đồng hồ!",
        "Thời gian của {side} đang cạn dần, chỉ còn {time}.",
        "Đồng hồ của {side} chỉ còn {time}, áp lực thời gian rất lớn!",
        "Báo động thời gian! {side} chỉ còn {time}!",
        "{side} đang rơi vào thế cháy giờ, chỉ còn {time}!",
    ],
    "clock": [
        "Đồng hồ của {side} còn {time}.",
        "{side} còn {time}, còn {other} có {other_time}.",
        "Trên đồng hồ, {side} còn {time} để suy nghĩ.",
    ],
    "clock_behind": ["{side} đang ít thời gian hơn {other} khá nhiều."],
    "clock_ahead": ["{side} vẫn còn nhiều thời gian hơn {other}."],
    "material_equal": [
        "Lực lượng hai bên vẫn đang cân bằng.",
        "Trên bàn cờ, hai bên vẫn ngang nhau về lực lượng.",
        "Tính theo giá trị quân, hai bên vẫn ngang nhau.",
        "Chưa bên nào nhỉnh hơn về lực lượng.",
    ],
    "material_one": [
        "{leader} đang nhỉnh hơn một chút về lực lượng.",
        "Về lực lượng, {leader} đang nhỉnh hơn.",
        "{leader} đang hơn đối thủ tương đương một tốt.",
    ],
    "material_two": [
        "{leader} đang nhỉnh hơn về lực lượng.",
        "{leader} đang hơn đối thủ tương đương hai tốt.",
        "Về lực lượng, {trailer} đang kém một chút.",
    ],
    "material_big": [
        "{leader} đang hơn đáng kể về lực lượng.",
        "{leader} đang có lợi thế lực lượng rõ rệt.",
        "{trailer} đang thiếu hụt lực lượng khá nhiều.",
        "Cán cân lực lượng đang nghiêng hẳn về {leader}.",
    ],
    "kings_both": [
        "Cả hai vua đều đã nhập thành.",
        "Cả Trắng lẫn Đen đều đã hoàn tất nhập thành.",
    ],
    "kings_one": [
        "Vua {yes} đã nhập thành, còn vua {no} thì chưa.",
        "{no} vẫn chưa nhập thành, trong khi {yes} đã làm điều đó.",
    ],
    "kings_none": [
        "Cả hai bên đều chưa nhập thành.",
        "Đến giờ, chưa bên nào nhập thành.",
    ],
    "phase_opening": [
        "Ván cờ vẫn đang ở giai đoạn khai cuộc.",
        "Chúng ta vẫn đang trong phần khai cuộc của ván đấu.",
    ],
    "phase_middlegame": [
        "Ván cờ đang ở giai đoạn trung cuộc.",
        "Ván cờ đã bước sang nước thứ {move}.",
        "Trên bàn cờ hiện còn {pieces} quân.",
    ],
    "phase_endgame": [
        "Ván cờ đã bước vào tàn cuộc.",
        "Bàn cờ đã thưa quân, đây là giai đoạn tàn cuộc.",
        "Chỉ còn {pieces} quân trên bàn cờ, tàn cuộc đã thực sự bắt đầu.",
    ],
    "queens_off": ["Cả hai hậu đều đã rời bàn cờ."],
    # {mover} made the last move; {victim} lost a piece in it.
    "recap_castling": [
        "Nước vừa rồi, {mover} đã nhập thành cánh {wing}.",
        "Nhắc lại nước vừa rồi, {mover} nhập thành {castle}.",
    ],
    "recap_promotion": [
        "Nước vừa rồi, {mover} phong cấp thành {promo} tại {target}.",
        "Ở nước trước, tốt {mover} đã hóa {promo}.",
    ],
    "recap_en_passant": [
        "Nước vừa rồi, {mover} bắt tốt qua đường tại {target}.",
        "Ở nước trước, {mover} đã bắt tốt qua đường.",
    ],
    "recap_capture": [
        "Nước vừa rồi, {mover} dùng {piece} bắt {captured} tại {target}.",
        "Ở nước trước, {captured} {victim} đã bị bắt tại {target}.",
    ],
    "recap_move": [
        "Nước vừa rồi, {mover} đưa {piece} đến {target}.",
        "Nhắc lại nước vừa rồi, {piece} {mover} đã tới {target}.",
    ],
}

# Endings. One is chosen per game and position, so the last move and the "game over" notice,
# which may arrive separately, say the same thing.
FINISH = {
    "mate": [
        "Chiếu hết! {winner} giành chiến thắng.",
        "Vua {loser} không còn đường thoát. Chiếu hết, {winner} thắng!",
        "Một đòn chiếu hết dứt điểm, {winner} là người chiến thắng!",
        "Chiếu hết! Ván cờ khép lại với chiến thắng thuộc về {winner}.",
        "{winner} chiếu hết vua {loser}. Ván cờ kết thúc.",
        "Vua hết đường chạy! Chiếu hết, {winner} thắng.",
    ],
    "stalemate": [
        "Ván cờ hòa do hết nước đi hợp lệ, vua không bị chiếu.",
        "Hòa pat! {side} không còn nước đi hợp lệ nào, dù vua không bị chiếu.",
        "Thế pat! Ván cờ kết thúc với kết quả hòa.",
    ],
    "resign": [
        "{loser} bỏ cuộc. {winner} thắng.",
        "{loser} xin thua, chiến thắng thuộc về {winner}.",
        "{loser} chấp nhận dừng cuộc chơi, {winner} giành chiến thắng.",
        "Ván cờ kết thúc, {loser} đã đầu hàng. Chúc mừng {winner}!",
        "{loser} đã buông cờ, xin chúc mừng {winner}!",
    ],
    "outoftime": [
        "{loser} hết giờ. {winner} thắng.",
        "Đồng hồ của {loser} đã về không, {winner} thắng do đối thủ hết giờ.",
        "{loser} thua vì hết thời gian, {winner} giành chiến thắng.",
        "Đồng hồ về số không, {loser} thua vì hết giờ. {winner} thắng!",
    ],
    "timeout": [
        "{loser} không tiếp tục ván cờ. {winner} thắng.",
        "{loser} đã rời ván đấu, {winner} được xử thắng.",
    ],
    "aborted": [
        "Ván cờ đã bị hủy.",
        "Ván đấu đã bị hủy, hẹn gặp lại ở ván sau.",
    ],
    "win": [
        "Ván cờ kết thúc. {winner} thắng.",
        "Ván cờ khép lại, chiến thắng thuộc về {winner}.",
    ],
    "draw": [
        "Ván cờ kết thúc với kết quả hòa.",
        "Hai bên chia điểm, ván cờ hòa.",
        "Ván cờ khép lại với kết quả hòa.",
    ],
    "insufficient": [
        "Ván cờ hòa vì không bên nào còn đủ lực lượng để chiếu hết.",
        "Hòa! Lực lượng còn lại không đủ để chiếu hết.",
    ],
    "repetition": [
        "Thế cờ lặp lại ba lần, ván cờ hòa.",
        "Hòa do lặp lại thế cờ.",
        "Bất phân thắng bại, thế cờ lặp lại ba lần và ván đấu kết thúc hòa.",
    ],
    "fifty": [
        "Ván cờ hòa theo luật năm mươi nước.",
        "Đã năm mươi nước không có bắt quân hay đi tốt, ván cờ hòa.",
    ],
}
FINISH_CLOSERS = [
    "Cảm ơn quý vị đã theo dõi!",
    "Hẹn gặp lại quý vị ở ván đấu tiếp theo!",
    "Xin cảm ơn hai kỳ thủ!",
]

# Lead-ins and closing lines by kind of moment. They name no piece, square, number or judgement
# the motif does not already make; a side is named only as {to_move}, in a question.
COLOUR = {
    "capture": (
        ["Ồ!", "Không chần chừ!", "Ra tay rồi!", "Quyết đoán!", "Và đây rồi!"],
        ["Cuộc đấu trí đang nóng dần lên.", "Bàn cờ vừa vơi đi một quân.",
         "Liệu {to_move} sẽ đáp trả ra sao?", "Một pha va chạm đáng chú ý.",
         "Đến lượt {to_move}, liệu có nước đáp trả nào?"],
    ),
    "exchange": (
        [],
        ["Các quân đang dần được trao đổi.", "Bàn cờ đang thưa dần.",
         "Liệu {to_move} có tiếp tục trao đổi?", "Thế trận đang dần đơn giản hơn."],
    ),
    "check": (
        ["Xem kìa!", "Ối chà!", "Căng rồi đây!", "Một pha chủ động!"],
        ["Không khí trên bàn cờ căng lên từng chút.",
         "Liệu {to_move} sẽ xoay xở thế nào?", "Liệu {to_move} sẽ hóa giải ra sao?"],
    ),
    "danger": (
        ["Nguy hiểm quá!", "Ối chà!", "Căng thẳng tột độ!"],
        ["Liệu {to_move} có tìm ra cách chống đỡ?",
         "Đây có thể là thời khắc quyết định của ván cờ."],
    ),
    "escape": (
        ["Phù!"],
        ["Giờ thì {to_move} sẽ làm gì tiếp theo?", "Liệu {to_move} có tiếp tục gây sức ép?"],
    ),
    "castling": (
        [],
        ["Một quyết định quen thuộc ở giai đoạn này.", "Ván cờ bắt đầu có những gam màu riêng.",
         "Liệu {to_move} sẽ chọn hướng đi nào?", "Một bước chuẩn bị quan trọng cho ván cờ."],
    ),
    "development": (
        [],
        ["Từng bước một, thế trận dần thành hình.", "Các quân đang lần lượt vào vị trí."],
    ),
    "centre": (
        [],
        ["Chưa ai chịu nhường ai một bước.", "Trung tâm luôn là chìa khóa của ván cờ.",
         "Liệu {to_move} có chấp nhận tranh chấp?"],
    ),
    "question": (
        [],
        [],
    ),
    "promotion": (
        ["Thật bất ngờ!", "Khoảnh khắc lớn!", "Và đây rồi!"],
        ["Cục diện ván cờ có thể thay đổi hoàn toàn.",
         "Liệu {to_move} sẽ phản ứng ra sao?"],
    ),
    "passed_pawn": (
        ["Xem kìa!", "Thú vị đây!"],
        ["Chú tốt này đang được mọi người dõi theo.",
         "Liệu {to_move} có cản được chú tốt này?"],
    ),
    "endgame": (
        [],
        ["Mỗi quyết định lúc này đều rất nặng ký.", "Trong tàn cuộc, từng nước đi đều quý như vàng.",
         "Liệu {to_move} sẽ đi tiếp ra sao?"],
    ),
    "quiet": (
        [],
        ["Liệu {to_move} sẽ đáp lại ra sao?"],
    ),
    # A plain description of the move, which may still matter: no remark on its character.
    "plain": (
        [],
        ["Liệu {to_move} sẽ đáp lại ra sao?"],
    ),
    # Waiting: the player thinking, the clock, a check to answer.
    "idle_wait": (
        ["Hừm.", "Chờ thêm chút nữa."],
        ["Liệu {to_move} sẽ chọn hướng đi nào?",
         "Một quyết định không hề dễ dàng.", "Cùng chờ xem nước đi tiếp theo.",
         "Liệu {to_move} đã tìm ra phương án ưng ý?"],
    ),
    # Looking back over the board: material, kings, the phase, the last move.
    "idle_board": (
        ["Nhìn lại bàn cờ một chút.", "Trong lúc chờ đợi, cùng điểm lại thế cờ."],
        ["Liệu {to_move} sẽ chọn hướng đi nào?", "Cùng chờ xem nước đi tiếp theo.",
         "Một quyết định không hề dễ dàng."],
    ),
}

# The commentator's reading of the move (insight.py), added after the sentence about what happened.
# Board facts only, in a hedged voice: what the move attacks, pins, guards, prepares, where the game is
# heading. Never a move for the side about to play, never who stands better, never a mate.
# Placeholders: {side} moved, {opponent}, {piece} / {Piece} moved to {target}; {victim} / {Victim} on
# {vsq} (and {victim2} on {vsq2}); {pinned} / {Pinned} on {psq}; {guarded} on {gsq}; {count} in words.
INSIGHT = {
    "fork": [
        "{Piece} ở {target} đang nhắm cùng lúc {victim} {vsq} và {victim2} {vsq2}!",
        "Một đòn chĩa đôi! {Piece} {side} đe dọa cả {victim} lẫn {victim2} của {opponent}.",
        "{opponent} gặp rắc rối: {victim} ở {vsq} và {victim2} ở {vsq2} đều bị {piece} tấn công.",
        "Hai mục tiêu một lúc, {victim} {vsq} và {victim2} {vsq2} đều nằm trong tầm {piece} {side}.",
    ],
    "threat": [
        "{Piece} {side} giờ đang nhắm vào {victim} ở {vsq}.",
        "Có vẻ {side} muốn gây sức ép lên {victim} {opponent} ở {vsq}.",
        "{Victim} {opponent} ở {vsq} bắt đầu bị đe dọa.",
        "Nước này đặt {victim} ở {vsq} vào tầm ngắm, {opponent} cần để ý.",
    ],
    "discovered": [
        "Nước đi mở ra một đường tấn công, {victim} {opponent} ở {vsq} giờ bị nhắm tới.",
        "Một đòn mở đường! {Victim} ở {vsq} đột nhiên nằm trong tầm ngắm.",
        "Đằng sau nước đi ấy là một đường tấn công mới vào {victim} {vsq}.",
    ],
    "pin_king": [
        "{Pinned} {opponent} ở {psq} đang bị ghim vào vua.",
        "Có vẻ {side} muốn ghim {pinned} ở {psq}, quân này giờ không thể rời chỗ.",
        "{Pinned} ở {psq} bị ghim chặt, {opponent} sẽ phải tính toán kỹ.",
    ],
    "pin_queen": [
        "{Pinned} {opponent} ở {psq} đang đứng chắn ngay trước hậu.",
        "{Piece} {side} ghim {pinned} ở {psq} vào hậu phía sau.",
        "Một thế ghim khó chịu, sau {pinned} {psq} là hậu của {opponent}.",
    ],
    "retreat": [
        "{Piece} {side} rút khỏi vùng nguy hiểm.",
        "{side} kịp đưa {piece} tránh khỏi đòn tấn công.",
        "Có vẻ {side} đã thấy nguy cơ, {piece} được rút về {target}.",
    ],
    "hanging": [
        "{Piece} ở {target} đang nằm trong tầm bắt của {opponent}. Táo bạo, hay là một cái bẫy?",
        "{side} đặt {piece} vào {target}, ngay trong tầm bắt. Có tính toán gì phía sau chăng?",
        "Khoan đã, {piece} {side} ở {target} có vẻ chưa được bảo vệ đủ. Một mồi nhử chăng?",
        "{Piece} {side} ở {target} đang trong tầm bắt, cố ý chăng?",
    ],
    "defend": [
        "{side} kịp thời bảo vệ {guarded} ở {gsq}.",
        "Nước này cũng che chở cho {guarded} ở {gsq} đang bị nhắm tới.",
        "Có vẻ mục đích là giữ {guarded} ở {gsq}.",
    ],
    "still_pressed": [
        "Tuy vậy, vua {side} vẫn chưa thật sự an toàn.",
        "Nhưng quân {opponent} vẫn đang vây quanh vua {side}.",
        "Thoát chiếu rồi, nhưng sức ép lên vua {side} vẫn còn đó.",
    ],
    "king_attack": [
        "{side} đã có {count} quân hướng về phía vua {opponent}.",
        "Có vẻ {side} đang dồn lực về phía vua đối phương.",
        "Lực lượng {side} đang tụ lại quanh vua {opponent}, một cuộc tấn công có thể đang hình thành.",
    ],
    "pawn_storm": [
        "Có vẻ {side} muốn dùng tốt mở đường tấn công vua {opponent}.",
        "Tốt {side} bắt đầu tràn lên phía vua đối phương.",
        "Một dấu hiệu tấn công, tốt {side} đang tiến về phía vua {opponent}.",
    ],
    "weak_f7": [
        "{Piece} {side} nhắm vào {weak}, điểm nhạy cảm gần vua {opponent}.",
        "Có vẻ {side} để mắt tới ô {weak}, nơi chỉ có vua che chở.",
        "Ô {weak} lại bị nhắm tới, một điểm yếu quen thuộc khi vua chưa nhập thành.",
    ],
    "prophylaxis": [
        "Một nước phòng ngừa, không cho {stop_piece} {opponent} nhảy vào {stop_sq}.",
        "Có vẻ {side} muốn ngăn {stop_piece} đối phương vào ô {stop_sq}.",
        "Nước tốt nhỏ nhưng có ý, ô {stop_sq} giờ đã được canh giữ.",
    ],
    "luft": [
        "{side} tạo khoảng thở cho vua.",
        "Một ô thoát cho vua, đề phòng các đòn ở hàng cuối.",
        "Có vẻ {side} muốn vua an toàn hơn trước các nước chiếu ở hàng cuối.",
    ],
    "shelter": [
        "Lớp tốt che vua {side} có phần mỏng đi.",
        "{side} chấp nhận đẩy tốt trước vua, hẳn là có lý do.",
        "Nước này làm thưa hàng rào tốt quanh vua {side}, một quyết định khá mạo hiểm.",
    ],
    "pawn_break": [
        "Tốt hai bên chạm nhau, thế cờ có thể sắp mở ra.",
        "{side} thách thức chuỗi tốt của đối phương.",
        "Tốt {side} tiến lên đối mặt, cấu trúc tốt bắt đầu căng thẳng.",
    ],
    "space": [
        "{side} giành thêm không gian.",
        "Tốt {side} tiến sâu, lấn dần không gian của đối phương.",
        "Có vẻ {side} muốn chiếm thêm đất cho các quân phía sau.",
    ],
    "new_passer": [
        "Tốt {side} ở {target} giờ đã là tốt thông!",
        "{side} vừa có một tốt thông ở {target}.",
        "Không còn tốt nào cản đường, tốt {target} của {side} đã thành tốt thông.",
    ],
    "open_lines": [
        "Nước tốt này mở đường cho {pieces} {side}.",
        "Đường đi cho {pieces} đã được khai thông.",
        "{side} mở lối ra cho {pieces}.",
    ],
    "castle_ready": [
        "Đường nhập thành cánh {wing} đã thông.",
        "Có vẻ {side} đang chuẩn bị nhập thành cánh {wing}.",
        "{side} dọn đường, vua có thể sớm nhập thành cánh {wing}.",
    ],
    "opposite_castling": [
        "Hai vua nhập thành khác cánh, ván cờ có thể thành cuộc đua tấn công!",
        "Vua hai bên ở hai cánh khác nhau, tốt có thể sớm được đẩy lên tấn công.",
        "Nhập thành khác cánh, thường là dấu hiệu của một ván cờ sắc bén.",
    ],
    "king_centre": [
        "Trong khi đó vua {opponent} vẫn đứng ở trung tâm.",
        "Vua {side} đã an toàn, còn vua {opponent} vẫn chưa nhập thành.",
        "Vua {opponent} vẫn ở giữa bàn cờ, điều mà {side} có thể khai thác.",
    ],
    "outpost": [
        "{Piece} {side} đứng ở {target}, một tiền đồn vững chắc.",
        "{Piece} ở {target} được tốt bảo vệ, khó bị tốt đối phương đuổi đi.",
        "Một vị trí lý tưởng, {target} giờ là tiền đồn của {side}.",
    ],
    "seventh": [
        "Xe {side} đã xâm nhập hàng thứ bảy!",
        "Xe {side} đột nhập vào hàng thứ bảy, tốt {opponent} gặp nguy.",
        "Một chiếc xe trên hàng thứ bảy luôn rất đáng gờm.",
    ],
    "doubled_rooks": [
        "Hai xe {side} đã nối nhau trên cột {file}.",
        "{side} xếp hai xe cùng cột {file}, sức ép tăng gấp đôi.",
        "Hai xe {side} cùng đứng trên cột {file}.",
    ],
    "trade_ahead": [
        "Đang hơn lực lượng, {side} sẵn lòng đổi bớt quân.",
        "Hơn quân thì đổi quân, một cách chơi thực dụng.",
        "Có vẻ {side} muốn đơn giản hóa thế cờ khi đang hơn quân.",
    ],
    "queens_off": [
        "Cặp hậu rời bàn cờ, thế trận bước sang một giai đoạn khác.",
        "Không còn hậu trên bàn, giờ là cuộc đấu của các quân còn lại.",
        "Đổi hậu xong, vua hai bên bớt phải lo các đòn tấn công bất ngờ.",
    ],
    "bishop_pair": [
        "{side} giờ có cặp tượng, còn {opponent} thì không.",
        "Cặp tượng của {side} sẽ càng mạnh khi thế cờ mở ra.",
        "{opponent} vừa mất cặp tượng.",
    ],
    "development_lead": [
        "{side} đã ra {mine} quân nhẹ, nhanh hơn hẳn {opponent}.",
        "Về phát triển quân, {side} đang đi trước.",
        "{side} đang dẫn trước về tốc độ triển khai lực lượng.",
    ],
    "opening": [
        "Đây là {name}. {idea}",
        "Ván cờ đã đi vào {name}. {idea}",
        "Chúng ta đang ở {name}. {idea}",
    ],
}

# Openings, recognised at the move that completes the line. The idea is general opening knowledge
# about the side that chose it, never a move to play next.
OPENINGS = [
    (("e4", "e5", "Nf3", "Nc6", "Bb5"), "khai cuộc Tây Ban Nha",
     "Tượng Trắng gây sức ép lên mã c6, gián tiếp nhắm vào tốt e5."),
    (("e4", "e5", "Nf3", "Nc6", "Bc4"), "khai cuộc Ý", "Tượng Trắng hướng thẳng về điểm f7."),
    (("e4", "e5", "Nf3", "Nc6", "d4"), "khai cuộc Scotland", "Trắng muốn mở trung tâm ngay từ sớm."),
    (("e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "b4"), "gambit Evans",
     "Trắng hy sinh một tốt để đổi lấy nhịp phát triển."),
    (("e4", "e5", "Nf3", "Nc6", "Bc4", "Nf6"), "phòng thủ hai mã",
     "Đen chọn phản công thay vì phòng thủ thụ động."),
    (("e4", "e5", "Nf3", "Nc6", "Nc3", "Nf6"), "khai cuộc bốn mã", "Một lối chơi cổ điển, phát triển quân trước hết."),
    (("e4", "e5", "Nf3", "Nf6"), "phòng thủ Petrov", "Đen đáp trả đối xứng, một lối chơi nổi tiếng vững chắc."),
    (("e4", "e5", "Nf3", "d6"), "phòng thủ Philidor", "Đen giữ tốt e5 bằng một thế trận chắc chắn nhưng hơi chật."),
    (("e4", "e5", "f4"), "gambit Vua", "Trắng hy sinh tốt để mở cột f, một lối chơi rất mạo hiểm."),
    (("e4", "e5", "Nc3"), "khai cuộc Viên", "Trắng phát triển mã trước, giữ khả năng đẩy tốt f sau này."),
    (("e4", "e5", "Bc4"), "khai cuộc Tượng", "Tượng Trắng ra sớm, nhắm về phía f7."),
    (("e4", "c5"), "phòng thủ Sicilia", "Đen chọn thế trận bất đối xứng, sẵn sàng cho một ván cờ phức tạp."),
    (("e4", "c5", "Nf3", "d6", "d4", "cxd4", "Nxd4", "Nf6", "Nc3", "a6"), "biến Najdorf của phòng thủ Sicilia",
     "Một trong những hệ thống sắc bén và được nghiên cứu kỹ nhất."),
    (("e4", "c5", "c3"), "biến Alapin của phòng thủ Sicilia", "Trắng muốn dựng một trung tâm tốt vững chắc."),
    (("e4", "c5", "Nc3"), "phòng thủ Sicilia đóng", "Trắng chọn lối chơi chậm, chuẩn bị dần cho tấn công."),
    (("e4", "e6"), "phòng thủ Pháp", "Đen chuẩn bị phản kích trung tâm, chấp nhận thế trận hơi chật."),
    (("e4", "e6", "d4", "d5", "e5"), "biến tiến của phòng thủ Pháp", "Trắng khóa trung tâm và giành không gian."),
    (("e4", "c6"), "phòng thủ Caro-Kann", "Đen muốn tranh chấp trung tâm mà không để lộ sơ hở."),
    (("e4", "c6", "d4", "d5", "e5"), "biến tiến của phòng thủ Caro-Kann", "Trắng chiếm không gian ở trung tâm."),
    (("e4", "d5"), "phòng thủ Scandinavia", "Đen thách thức tốt e4 ngay lập tức."),
    (("e4", "Nf6"), "phòng thủ Alekhine", "Đen mời tốt Trắng tiến lên rồi tìm cách tấn công chúng."),
    (("e4", "d6"), "phòng thủ Pirc", "Đen để Trắng chiếm trung tâm trước rồi mới phản công."),
    (("e4", "g6"), "phòng thủ Hiện đại", "Đen nhường trung tâm, chờ thời cơ phản công từ xa."),
    (("d4", "d5", "c4"), "gambit Hậu", "Trắng đề nghị một tốt cánh hậu để tranh trung tâm."),
    (("d4", "d5", "c4", "e6"), "gambit Hậu từ chối", "Đen giữ vững tốt d5, một thế trận cổ điển."),
    (("d4", "d5", "c4", "dxc4"), "gambit Hậu chấp nhận", "Đen nhận tốt, còn Trắng có cơ hội chiếm trung tâm."),
    (("d4", "d5", "c4", "c6"), "phòng thủ Slav", "Đen giữ d5 mà vẫn để ngỏ đường ra cho tượng."),
    (("d4", "Nf6", "c4", "g6"), "phòng thủ Ấn Độ của Vua", "Đen để Trắng dựng trung tâm rồi tìm cách phá nó."),
    (("d4", "Nf6", "c4", "e6", "Nc3", "Bb4"), "phòng thủ Nimzo-Ấn", "Tượng Đen ghim mã c3, tranh giành ô e4."),
    (("d4", "Nf6", "c4", "e6", "Nf3", "b6"), "phòng thủ Ấn Độ của Hậu", "Đen muốn kiểm soát ô e4 bằng tượng."),
    (("d4", "Nf6", "c4", "g6", "Nc3", "d5"), "phòng thủ Grünfeld", "Đen nhường trung tâm để tấn công nó từ xa."),
    (("d4", "Nf6", "c4", "c5", "d5"), "phòng thủ Benoni", "Thế trận bất đối xứng, mỗi bên mạnh ở một cánh."),
    (("d4", "Nf6", "c4", "e6", "g3"), "khai cuộc Catalan", "Tượng Trắng sẽ nhìn dọc đường chéo dài."),
    (("d4", "f5"), "phòng thủ Hà Lan", "Đen tranh ô e4 bằng tốt f, một lối chơi sắc bén."),
    (("d4", "d5", "Bf4"), "hệ thống London", "Trắng dựng một thế trận vững chắc, đều đặn."),
    (("d4", "Nf6", "Bf4"), "hệ thống London", "Trắng dựng một thế trận vững chắc, đều đặn."),
    (("d4", "d5", "Nf3", "Nf6", "Bf4"), "hệ thống London", "Trắng dựng một thế trận vững chắc, đều đặn."),
    (("c4", "e5"), "khai cuộc Anh", "Một thế trận giống phòng thủ Sicilia nhưng đổi màu quân."),
    (("Nf3", "d5", "c4"), "khai cuộc Réti", "Trắng tấn công trung tâm từ cánh, không vội đẩy tốt giữa."),
]

# How Piper should say the foreign names, spelled the Vietnamese way.
PRONOUNCE = {
    "Sicilia": "Xi xi li a", "Najdorf": "Nai đoóc", "Alapin": "A la pin", "Caro-Kann": "Ca rô Can",
    "Scandinavia": "Xcăng đi na vi a", "Alekhine": "A lê khin", "Pirc": "Pớc", "Petrov": "Pê tơ rốp",
    "Philidor": "Phi li đo", "Evans": "E vần", "Grünfeld": "Grun phen", "Benoni": "Bê nô ni",
    "Nimzo-Ấn": "Nim dô Ấn", "London": "Lân đơn", "Slav": "Xla vơ", "Réti": "Rê ti", "Catalan": "Ca ta lan",
    "Scotland": "Xcốt len", "gambit": "găm bít",
}

# Remarks on the whole board for a long think: no move, no verdict.
POSITION = {
    "development": [
        "{ahead} đã triển khai gần hết quân nhẹ, trong khi {behind} vẫn còn {count} quân ở hàng cuối.",
        "{behind} còn {count} quân nhẹ chưa ra trận, chậm hơn {ahead} về phát triển.",
    ],
    "opposite_castling": [
        "Hai vua nhập thành ở hai cánh khác nhau, tốt cả hai bên đều có thể dâng lên tấn công.",
        "Vua hai bên ở hai cánh khác nhau, cuộc đua tấn công có thể nổ ra bất cứ lúc nào.",
    ],
    "open_file": [
        "Cột {file} đã mở, có lẽ xe hai bên sẽ sớm tranh giành nó.",
        "Trên bàn cờ đang có cột {file} hoàn toàn thông thoáng, chỗ lý tưởng cho xe.",
    ],
    "open_files": [
        "Cột {file} đang mở, chỗ lý tưởng cho các xe.",
        "Bàn cờ đã thoáng hơn, cột {file} không còn tốt nào.",
    ],
    "passer": [
        "{owner} có tốt thông ở {square}, một quân cờ đáng để ý.",
        "Tốt thông {owner} ở {square} có thể trở thành yếu tố quan trọng.",
    ],
    "king_pressure": [
        "{attacker} đang có {count} quân hướng về khu vực vua {defender}.",
        "Vua {defender} đang chịu sức ép từ {count} quân {attacker}.",
    ],
    "bishop_pair": [
        "{owner} đang giữ cặp tượng.",
        "Cặp tượng của {owner} có thể phát huy khi thế cờ mở ra.",
    ],
    "doubled": [
        "{owner} có tốt chồng ở cột {file}.",
        "Cấu trúc tốt của {owner} có một điểm yếu, tốt chồng ở cột {file}.",
    ],
}

GROUPS = {
    "capture": "capture", "en_passant": "capture", "exchange": "exchange",
    "capture_question": "question", "intention": "question",
    "promotion_capture": "promotion", "promotion": "promotion",
    "capture_check": "check", "capture_check_fork": "check", "check": "check", "check_again": "check",
    "check_fork": "check",
    "danger": "danger", "escape": "escape", "castling": "castling",
    "development": "development", "early_queen": "development",
    "centre": "centre", "centre_duel": "centre",
    "passed_pawn": "passed_pawn", "endgame": "endgame", "event": "plain",
}


def group(theme):
    if theme and theme.startswith("idle_"):
        return "idle_wait" if theme in ("idle_thinking", "idle_clock", "idle_check") else "idle_board"
    return GROUPS.get(theme or "", "quiet")


def fill(templates, values):
    return [template.format(**values) for template in templates]


def seed_of(*parts):
    return int(hashlib.sha256("|".join(str(part) for part in parts).encode()).hexdigest()[:12], 16)


def pick(templates, seed, **values):
    return templates[seed % len(templates)].format(**values)


def norm(text):
    return unicodedata.normalize("NFC", text).strip()


VARIABLE = re.compile(r"\b(?:Trắng|Đen|vua|hậu|xe|tượng|mã|tốt|[a-h][1-8])\b|\d+", re.IGNORECASE)


def shape(text):
    """`text` with sides, pieces, squares and numbers masked, so two fillings of one template match."""
    return VARIABLE.sub("_", norm(text))


def pairs(text):
    words = re.findall(r"\w+", norm(text).lower())
    return set(zip(words, words[1:]))


def dress(theme, base, to_move, seed, recent=()):
    """`base` with, sometimes, a prebuilt lead-in and closing line for its kind of moment.

    About one line in five gets a lead-in (only for moments with an event) and two in ten a closing
    line; none that was heard in the last sixteen lines or that repeats two words of the sentence ("trao đổi"), no lead-in when the
    sentence opens with its own exclamation or with the same word, no question after a question,
    and no closing line on a sentence that is already long.
    """
    leads, tails = COLOUR[group(theme)]
    heard = [norm(line) for line in list(recent)[-16:]]
    said = pairs(base)

    def fresh(options):
        return [option for option in options
                if not any(option in line for line in heard) and not pairs(option) & said]
    lead = tail = ""
    exclaims = any(word.endswith("!") for word in base.split()[:3])
    first = base.split()[0].lower()
    if seed % 10 < 2 and not exclaims:
        options = [option for option in fresh(leads) if option.split()[0].lower() != first]
        if options:
            lead = options[(seed // 10) % len(options)]
    if (seed // 100) % 10 < 2 and len(base) + len(lead) < 75:
        options = [tail.format(to_move=to_move) for tail in tails]
        if base.endswith("?"):
            options = [option for option in options if not option.endswith("?")]
        options = fresh(options)
        if options:
            tail = options[(seed // 1000) % len(options)]
    return " ".join(part for part in (lead, base, tail) if part)
