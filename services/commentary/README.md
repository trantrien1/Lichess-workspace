# Bình luận viên tiếng Việt

Dịch vụ tạo lời bình cho các ván cờ tiêu chuẩn: chơi với máy, thách đấu bạn bè và ghép cặp ở sảnh, kể cả ván của khách chưa đăng nhập. Lila lấy các nước đi, gửi thế cờ tới dịch vụ và phát âm thanh qua giao diện ván đấu. python-chess và Stockfish xác định chuyện gì vừa xảy ra; câu bình luận lấy từ kho câu viết sẵn trong `phrases.py`; Piper tổng hợp giọng nói. Không dùng LLM.

## Chạy dịch vụ

Cần Python 3, Stockfish và mô hình Piper tiếng Việt (cả tệp `.onnx` và `.onnx.json` cùng tên). Giọng mặc định dùng `vi_VN-vais1000-medium`; các giọng thử nghiệm cần thêm `vi_VN-vivos-x_low` (xem mục Giọng đọc). Nguồn và checksum của các mô hình nằm trong `assets.json`. Tạo môi trường và cài thư viện:

```sh
cd services/commentary
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.lock
```

Cấu hình các biến môi trường theo `.env.example`, đặc biệt `BLV_TOKEN` là một chuỗi bí mật ngẫu nhiên và `BLV_PIPER_MODEL` là đường dẫn đến mô hình của giọng mặc định (mô hình khác đặt cùng thư mục, hoặc trong `BLV_PIPER_DIR`). Sau đó chạy:

```sh
uvicorn app:app --host 127.0.0.1 --port 9666 --workers 1
```

Dịch vụ yêu cầu bearer token; giữ endpoint trong mạng tin cậy. Khi Lila chạy trong container, endpoint phải có thể truy cập từ container đó.

Chạy test (không cần Stockfish hay Piper; test đọc thật mọi giọng chỉ chạy khi đủ mô hình trong `BLV_PIPER_DIR` hoặc `~/.cache/piper`):

```sh
python -m unittest discover -s tests
```

### Kho câu dựng sẵn

`phrases.py` chứa mọi câu dịch vụ có thể nói: khoảng 310 mẫu câu và hơn 110 lời dẫn và câu kết.

- Câu chào và câu nối khi trình duyệt bỏ qua vài nước.
- Câu cho nước đầu tiên, chia theo kiểu khai cuộc: e4/d4, Nf3/c4/g3…, các nước hiếm.
- `MOVE`: 33 tình huống cho một nước đi. Ví dụ: bắt quân, đổi quân, bắt kèm chiếu, chiếu đôi tấn công, chiếu liên tiếp, thoát chiếu bằng vua hoặc bằng quân che, nhập thành, phong hậu và phong cấp dưới, tốt thông, xe vào cột mở, phát triển quân, trung tâm, hậu ra sớm, tàn cuộc, nước yên.
- `IDLE`: 24 tình huống lúc chờ. Ví dụ: suy nghĩ lâu, đồng hồ, cách biệt thời gian, lực lượng, nhập thành, giai đoạn ván, hậu đã đổi, nhắc lại nước vừa đi.
- `FINISH`: chiếu hết, hòa pat, xin thua, hết giờ, hủy ván, lặp lại 3 lần, luật 50 nước, không đủ quân chiếu hết, cùng các câu cảm ơn kết ván.

Mẫu câu chỉ có các chỗ trống như `{side}`, `{piece}`, `{target}`, `{captured}`, `{castle}`. Chỗ trống được điền từ dữ kiện python-chess tính ra, nên câu không bao giờ nói sai thế cờ. Không có chỗ trống nào cho nhận định: câu không khen chê nước đi và không gợi ý nước tiếp theo.

`narrative.compose` gom các câu hợp với nước vừa đi. `Recent.order` đưa câu chưa nói gần đây lên trước. Việc so sánh dựa trên khung câu (`phrases.shape` che tên bên, quân, ô cờ và chữ số), nên "Liệu Đen…" ngay sau "Liệu Trắng…" vẫn bị coi là lặp. Lựa chọn tất định theo `gameId` và chuỗi nước đi: cùng một ván luôn ra cùng một câu, ván khác sẽ khác.

`phrases.dress` thỉnh thoảng thêm lời dẫn và câu kết của nhóm tình huống (bắt quân, chiếu, nhập thành, căng thẳng, lúc chờ…):

- khoảng 2/5 số câu có lời dẫn và 1/2 có câu kết;
- không thêm lời dẫn khi câu đã mở bằng câu cảm thán hoặc bắt đầu bằng cùng một chữ;
- không thêm câu hỏi sau một câu hỏi; chỉ thêm câu kết khi câu chính cùng lời dẫn dưới 75 ký tự;
- không dùng lời dẫn hay câu kết đã nghe trong 4 câu gần nhất, hoặc có cụm 2 chữ trùng với câu chính;
- câu kết chỉ nêu tên bên sắp đi khi là câu hỏi ("Liệu Đen sẽ đáp lại ra sao?");
- câu chào, nước đầu tiên và câu kết ván giữ nguyên.

Câu kết ván cũng tất định theo nước đi, nên nước chiếu hết và thông báo kết thúc của Lila nhận cùng một `commentId` và chỉ đọc một lần.

### Nguồn văn phong

Không có bộ dữ liệu bình luận cờ vua trực tiếp (dạng lời nói) nào công khai, và không có bằng tiếng Việt. Bộ GameKnot (Jhamtani và cộng sự, ACL 2018, 298 nghìn cặp nước đi–bình luận) là bình luận viết trên diễn đàn, không ghi giấy phép. Các bài tường thuật và video giải đấu (Lichess, Chess.com, các kênh YouTube tiếng Việt) cũng không có giấy phép dùng lại. Vì vậy kho câu tự viết và không chép câu nào. Chỉ dùng:

- thuật ngữ cờ tiếng Việt thông dụng: chiếu hết, hòa pat, phong hậu, bắt tốt qua đường, tốt thông, cột mở, xin thua, hết giờ;
- nhịp của bình luận truyền hình: một câu sự kiện ngắn, thêm chút không khí, thường kết bằng một câu hỏi về nước tiếp theo.

Câu viết ngắn để Piper đọc tự nhiên. Ô cờ viết `f3` trong phụ đề, Piper đọc thành "ép 3".

### Độ trễ

Lời bình sẵn sàng sau phần phân tích Stockfish (`BLV_ENGINE_MS`, mặc định 100 ms) cộng thời gian Piper đọc câu (khoảng 130–250 ms). Câu chào, nước đầu tiên và câu nối được tổng hợp sẵn khi dịch vụ khởi động, nên phát ngay. Trình duyệt bỏ lời bình đến muộn hơn 4,5 s sau nước đi.

### Giọng đọc

Người chơi chọn giọng ở khung bình luận khi đã bật; lựa chọn lưu trong trình duyệt và có câu mẫu để nghe thử. Danh sách giọng nằm trong `voices.py`. Whitelist của Lila (`app/controllers/Commentary.scala`) và giao diện (`ui/round/src/commentary.ts`) phải có đúng các id đó; test kiểm tra việc này.

| id | Tên trên giao diện | Mô hình | Lỗi ký tự* |
|---|---|---|---|
| `piper-vais1000` | Nữ (mặc định) | vais1000 medium | 0,08–0,11 |
| `piper-vais1000-low` | Nữ, giọng trầm (cao độ ×0,88) | vais1000 medium | 0,09 |
| `piper-vais1000-high` | Nữ, giọng cao (cao độ ×1,08) | vais1000 medium | 0,08 |
| `piper-vivos-28` | Nam 1 (thử nghiệm) | vivos x_low, người nói 28 | 0,23 |
| `piper-vivos-45` | Nam 2 (thử nghiệm) | vivos x_low, người nói 45 | 0,18 |
| `piper-vivos-40` | Nữ 2 (thử nghiệm) | vivos x_low, người nói 40 | 0,24 |

\* Tỉ lệ lỗi ký tự khi Whisper small nghe lại 6 câu bình luận, tính cả dấu. Càng thấp càng rõ; hai lần đo chênh nhau khoảng ±0,04.

- Giọng trầm và giọng cao dùng chung mô hình vais1000. Piper đọc nhanh hơn (hoặc chậm hơn) rồi phát lại ở tần số mẫu khác, nên cao độ và âm sắc đổi còn độ dài câu giữ nguyên.
- Mô hình vivos (Piper 0.2) không có âm vị thanh điệu nên đọc lơ lớ dấu; vì vậy ghi "thử nghiệm". Tốc độ gốc chậm hơn vais1000 khoảng 1/3, nên dịch vụ cho đọc nhanh hơn (`length=0.75`) để lời bình không trễ so với ván.
- vivos dùng giấy phép CC BY-NC-SA 4.0, không được dùng cho mục đích thương mại. Mô hình 25hours không được dùng vì không rõ giấy phép.
- Mỗi tệp mô hình chạy một tiến trình Piper riêng (2 tiến trình cho 6 giọng). Thiếu mô hình thì giọng đó đọc bằng giọng mặc định. `/health` liệt kê giọng đã cài (`voices`) và giọng đã tổng hợp sẵn câu (`prepared`).
- Giọng mặc định được tổng hợp sẵn trước khi dịch vụ nhận request. Các giọng khác được tổng hợp ở nền ngay sau đó, mỗi giọng 36 câu (câu mẫu, câu nối, câu chào, nước đầu tiên).
- `GET /sample/{voice}` trả câu mẫu dạng WAV; trình duyệt mở qua `GET /commentary/voice/{voice}` của Lila. Nếu đổi giọng giữa lúc đang đọc, câu hiện tại đọc nốt bằng giọng cũ, câu sau dùng giọng mới.

### Lời bình lúc chờ

Khi không ai đi, Lila gửi `event: "idle"` sau 10 s, rồi mỗi 15 s, tối đa 4 lần cho một thế cờ, kèm `idleCount`, `thinkingMs` và `clock` (giây còn lại của hai bên). Dịch vụ không gọi Stockfish cho các câu này để không gợi ý nước đi. Mỗi lần nói một chủ đề khác: thời gian suy nghĩ, đồng hồ, lực lượng, nhập thành, giai đoạn ván, nước vừa đi. Bị chiếu hoặc còn ≤ 20 s thì nói về điều đó trước. Một nước đi mới hủy ngay câu chờ đang tạo. Piper vẫn đọc nốt câu đó ở nền thay vì bị tắt, để câu bình nước mới không phải chờ nạp lại giọng.

### Dữ liệu trả về

- `text` là phụ đề (ô cờ viết `f3`), `speechText` là câu Piper đọc (`ép 3`).
- `kind` là `intro`, `move`, `idle` hoặc `finish`.
- `phrasebook` là phiên bản kho câu (`vi-prebuilt-1`); `/health` cũng trả giá trị này.
- `timings` gồm `engineMs`, `composedMs`, `audioReadyMs`.
- Không trả điểm engine hay PV cho trình duyệt; Lila cũng gỡ khóa `facts` trước khi gửi. `BLV_DEBUG_FACTS=1` chỉ để gỡ lỗi local.
- Dịch vụ nhớ 8 câu gần nhất của mỗi ván (tối đa 64 ván, trong RAM). Nhờ vậy câu không lặp lại, và các nhận xét chung chung như "tranh chấp trung tâm" không xuất hiện liên tiếp.

## Cấu hình Lila

Thêm vào cấu hình Lila đang sử dụng (ví dụ `lila/conf/application.conf`), thay token bằng cùng giá trị `BLV_TOKEN`:

```hocon
commentary.enabled = true
commentary.endpoint = "http://127.0.0.1:9666"
commentary.token = "<BLV_TOKEN>"
```

Khởi động lại Lila và build lại UI theo hướng dẫn của Lila. Tính năng mặc định tắt khi chưa cấu hình. Trên màn hình ván đấu, mỗi người chơi có thể tự bật hoặc tắt bình luận. Người dùng được xác thực bằng tài khoản; khách dùng cookie người chơi sẵn có của Lila. Người xem không có quyền mở luồng bình luận.
