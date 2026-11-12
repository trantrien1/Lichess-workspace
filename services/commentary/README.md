# Bình luận viên tiếng Việt

Dịch vụ tạo lời bình cho ván cờ tiêu chuẩn giữa người dùng đã đăng nhập và máy. Lila lấy các nước đi, gửi thế cờ tới dịch vụ và phát âm thanh qua giao diện ván đấu. Stockfish phân tích thế cờ; Piper tổng hợp giọng nói. Có thể cấu hình thêm endpoint LLM tương thích OpenAI; nếu không có, dịch vụ dùng lời bình dựng sẵn.

## Chạy dịch vụ

Cần Python 3, Stockfish và mô hình Piper tiếng Việt (cả tệp `.onnx` và `.onnx.json` cùng tên). Nguồn và checksum của các mô hình đã dùng trong bản nghiên cứu nằm trong `assets.json`. Tạo môi trường và cài thư viện:

```sh
cd services/commentary
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.lock
```

Cấu hình các biến môi trường theo `.env.example`, đặc biệt `BLV_TOKEN` là một chuỗi bí mật ngẫu nhiên và `BLV_PIPER_MODEL` là đường dẫn đến mô hình. Sau đó chạy:

```sh
uvicorn app:app --host 127.0.0.1 --port 9666 --workers 1
```

Dịch vụ yêu cầu bearer token; giữ endpoint trong mạng tin cậy. Khi Lila chạy trong container, endpoint phải có thể truy cập từ container đó.

## Cấu hình Lila

Thêm vào cấu hình Lila đang sử dụng (ví dụ `lila/conf/application.conf`), thay token bằng cùng giá trị `BLV_TOKEN`:

```hocon
commentary.enabled = true
commentary.endpoint = "http://127.0.0.1:9666"
commentary.token = "<BLV_TOKEN>"
```

Khởi động lại Lila và build lại UI theo hướng dẫn của Lila. Tính năng mặc định tắt khi chưa cấu hình. Trên màn hình ván đấu đủ điều kiện, người chơi có thể bật hoặc tắt bình luận.
