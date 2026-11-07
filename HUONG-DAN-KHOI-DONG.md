# Hướng dẫn khởi động (lila local + WebMCP)

Môi trường: WSL2 Debian, repo tại `~/ws`. Server chính: `http://localhost:9663`.

> Script khởi động chi tiết nằm ở `/mnt/d/Deepseek Workspace/Trường/LTW/setup/`  
> (xem `HUONG-DAN.md` trong thư mục đó). File này là bản tóm tắt nhanh.

## 1. Thứ tự khởi động khuyến nghị

Mở từng terminal/cửa sổ riêng (theo nhu cầu):

| # | Thành phần | Cổng | Script (trong `/mnt/d/Deepseek Workspace/Trường/LTW/setup/`) | Ghi chú |
|---|---|---:|---|---|
| 1 | Elasticsearch | 9200 | `bash chay-elasticsearch.sh` | Cần cho advanced search/lila-search. |
| 2 | lila-search | 9673 | `bash chay-lila-search.sh` | Cần `search.enabled=true`; phụ thuộc ES. |
| 3 | lila (server chính) | 9663 | `bash chay-lila.sh` | Play/SBT dev server. |
| 4 | lila-ws | 9664 | `bash chay-lila-ws.sh` | WebSocket realtime. |
| 5 | Fishnet (server analysis/bot tùy cấu hình) | 9665* | `bash chay-lila-fishnet.sh` / `bash chay-fishnet.sh` | *Cấu hình tùy mục đích; xem §4.* |

Chỉ cần UI/WebMCP cơ bản + chơi/test form: thường chạy **lila** (9663) là đủ; muốn tìm kiếm nâng cao chạy thêm **ES + lila-search**; muốn realtime/WS chạy thêm **lila-ws**.

## 2. Khởi động lila thủ công (nếu không dùng script)

```bash
export PATH=$HOME/.nvm/versions/node/v24.22.1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd ~/ws/lila
./lila.sh
# trong sbt console:
# compile
# run
```

Kiểm tra: mở `http://localhost:9663`.

## 3. Build UI/WebMCP sau khi sửa code

WebMCP nằm ở `~/ws/lila/ui/lib/src/webmcp.ts`, được bundle qua `ui/site`.

```bash
export PATH=$HOME/.nvm/versions/node/v24.22.1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd ~/ws/lila/ui
./build
```

Sau build:
- Hard reload trình duyệt: **Ctrl+Shift+R** tại `http://localhost:9663`.
- Nếu dùng WebMCP extension: reload panel/chat extension để nó đọc schema/tool mới.
- Kiểm tra bundle mới:
  ```js
  __ltw?.tools?.map(t => t.name)
  __ltw?.tools?.find(t => t.name === 'lichess_search_games')?.description
  ```

## 4. Phân tích ván (fishnet) — tóm tắt

- `lichess_analyse_game` đọc eval **đã lưu server-side** (PGN `?evals=1`). Engine chạy trên analysis board (client) **không** được lưu DB.
- Nếu gặp `NO_EVAL`: cần server analysis (fishnet) hoặc dùng ván đã có eval; kiểm tra export PGN có `[%eval]`.
- Fishnet cho **analysis** thường trỏ `http://127.0.0.1:9663/fishnet` (key dev: `fishnetplaydev`).
- Nếu kẹt `You already have an ongoing requested analysis` (Mongo local):
  ```bash
  mongosh lichess --quiet --eval 'db.fishnet_analysis.deleteMany({})'
  ```
- Chi tiết endpoint/cấu hình: xem `WEBMCP.md` §6.4 và scripts `chay-fishnet*.sh`.

## 5. Kiểm tra nhanh WebMCP

Trên `/games/search`:
```js
await __ltw.searchGames({ winner: 'astra', submit: false })
await __ltw.searchGames({ hasAi: 'computer', ratingMax: 1999, submit: false })
```

Trên ván đã có eval server-side:
```js
await __ltw.analyseGame({ topN: 10 })
await __ltw.analyseGame({ gameId: '0fmkLncq', topN: 5 })
```

## 6. Test tự động

```bash
export PATH=$HOME/.nvm/versions/node/v24.22.1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
cd ~/ws/lila
node ui/test webmcp.test.ts
pnpm exec oxlint --type-aware ui/lib/src/webmcp.ts ui/lib/tests/webmcp.test.ts
```

## 7. Tài liệu liên quan

- `~/ws/WEBMCP.md` — thiết kế, schema, hành vi, troubleshooting WebMCP.
- `~/ws/PROMPT.md` — yêu cầu gốc WebMCP.
- `/mnt/d/Deepseek Workspace/Trường/LTW/setup/HUONG-DAN.md` — hướng dẫn chi tiết setup/scripts.
- `~/ws/lila/AGENTS.md` — quy ước phát triển lila.
