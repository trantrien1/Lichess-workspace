# WebMCP cho lila — tài liệu triển khai

Tài liệu này giải thích **đã thêm/sửa gì**, **vì sao**, **cách chạy thử**, và **những chỗ khác với
`PROMPT.md`** (phát hiện khi đọc code lila thật).

Nhánh làm việc: `feature/webmcp` (monorepo `~/ws`).

---

## 1. Tóm tắt thay đổi

| File | Loại | Nội dung |
|---|---|---|
| `lila/ui/lib/src/webmcp.ts` | **mới** | Toàn bộ logic WebMCP: 2 tool + đăng ký + `window.__ltw` |
| `lila/ui/site/src/site.ts` | **sửa** | Thêm đúng 1 import side-effect ở cuối file: `import 'lib/webmcp';` |
| `lila/ui/lib/tests/webmcp.test.ts` | **mới (bonus)** | 29 test: form search + phụ thuộc field + reset state cũ (player + non-player) + bucket số + bits rebuild + submit, parser PGN, eval, thứ tự/topN/mover |

Không sửa:

- `ui/round/**` (đặc biệt `winbar.ts`) — không đụng, không import.
- `package.json` — không thêm dependency, không đổi export (wildcard `"./*"` đã có sẵn).
- Không thêm MCP server riêng; tất cả nằm trong bundle `site` chạy trên mọi trang.

Sau khi build, chỉ bundle `site` đổi hash; các file cũ **không bị xoá** (đã kiểm tra).

---

## 2. Kiến trúc

```
site.ts  ──import 'lib/webmcp'──▶  webmcp.ts
                                     │
                                     ├─ tools[0] = lichess_search_games
                                     ├─ tools[1] = lichess_analyse_game
                                     ├─ window.__ltw = { searchGames, analyseGame, tools }
                                     └─ nếu browser có WebMCP:
                                          registerTool / provideTools / provideContext
```

Nguyên tắc:

- Tool **trả dữ liệu JSON**, không viết câu văn. LLM tự diễn giải.
- WebMCP mới là early preview → **feature-detect** nhiều biến thể API và bọc `try/catch`.
- Không có API WebMCP thì trang vẫn chạy bình thường, console không lỗi.
- Có cờ module-level `registrationAttempted` để không đăng ký 2 lần khi bundle chạy lại.
- Luôn expose `window.__ltw` để test bằng console ngay cả trên Chrome thường.

---

## 3. Tool 1 — `lichess_search_games`

### 3.1 Input

Mọi field đều optional. Hai cách truyền player:

```js
// cách 1: đúng schema của PROMPT.md
__ltw.searchGames({ players: { white: 'thibault' }, perf: 'Blitz' })

// cách 2: shortcut phẳng (dùng trong nghiệm thu 7.2)
__ltw.searchGames({ white: 'thibault', perf: 'Blitz', dateMin: '2024-01-01' })
```

Các field: `players.{a,b,white,black,winner,loser}`, `winnerColor`, `perf`, `source`, `mode`,
`status`, `hasAi`, `aiLevelMin/Max`, `turnsMin/Max`, `ratingMin/Max`, `durationMin/Max`,
`clockInit`, `clockInc`, `dateMin`, `dateMax`, `analysed`, `sortField`, `sortOrder`, `submit`.

#### Field phụ thuộc field khác (quan trọng)

Đây là các ràng buộc **phía server/UI**, không phải tool tự bịa ra:

| Field | Phụ thuộc | Tool xử lý |
|---|---|---|
| `players.white`, `players.black`, `players.winner`, `players.loser` | Chỉ có tác dụng khi username **trùng `players.a` hoặc `players.b`**. `SearchPlayer.oneOf` lọc bỏ mọi tên không nằm trong 2 ghế này; nếu không có `players.a/b` thì coi như query rỗng và trang không trả kết quả nào. | Tự điền ghế trống từ các field này (`autoFilled` + `warnings`). Nếu cả 2 ghế đã là tên khác thì bỏ field, ghi `skipped` + `warnings`. |
| `aiLevelMin`, `aiLevelMax` | Chỉ hiển thị và có tác dụng khi **Opponent = Computer** (`hasAi=1`). UI ẩn row `.aiLevel` khi `hasAi != computer`. | Nếu truyền aiLevel mà không truyền `hasAi` → tự đặt `hasAi=computer`. Nếu `hasAi=human` → bỏ aiLevel, ghi `skipped` + `warnings`. |
| `players.a`, `players.b` | Là 2 ghế gốc của mọi filter người chơi. | Được điền tự động khi cần. Mỗi search bắt đầu từ player state sạch (xóa player không được request chỉ định) để không rò rỉ query trước; **ngoại lệ** ghế hidden/fixed (ví dụ `players.a` trên `/@user/games`) được giữ nếu request không ghi đè. |

Ví dụ: `{ white: 'astra' }` → tool tự thêm `players.a=astra`; query thực tế là
`players.a=astra&players.white=astra`. Nếu chỉ gửi `players.white=astra` (không có a/b),
lila sẽ không lọc gì cả.

### 3.2 Hành vi

1. Không thấy `form.search__form`:
   - nếu đang không ở `/games/search` → lưu input vào `sessionStorage` (`__ltw_pending_search`),
     `location.assign('/games/search')`, và trả
     `{ status: 'navigating', pending: true, url: '/games/search', message: ... }`. Khi
     `/games/search` load và có form, tool tự chạy search đã lưu (một lần), rồi xóa key — nên
     cùng một yêu cầu của người dùng vẫn được thực hiện sau điều hướng (agent không cần tự gọi lại,
     trừ khi sessionStorage không khả dụng).
   - nếu đã ở `/games/search` mà vẫn không có form → trả `{ status: 'error', error: 'NO_FORM' }`
     (tránh vòng lặp điều hướng).
2. Có form:
   - **Bước 0 — dọn sạch form (search mới)**: mỗi lần search, tool xóa `data-req-*` và clear
     toàn bộ `input` (trừ `hidden` như CSRF / `players.a` fixed trên `/@user/games`), `select`,
     `textarea` để **không rò rỉ filter từ tìm kiếm/trang kết quả trước** — kể cả player lẫn
     non-player (date, rating, hasAi, mode...). **Không dùng `form.reset()`** vì trang kết quả
     lila pre-fill giá trị làm HTML default; `reset()` sẽ khôi phục các giá trị cũ đó. Ví dụ sau
     "astra thắng ngày 07/11, Elo < 2000", query "astra thua" không được giữ date/rating cũ nếu
     request mới không truyền. Ghế hidden/fixed được giữ trừ khi request ghi đè.
   - **Bước 1 — ghế người chơi**: tự điền `players.a/b` từ `white/black/winner/loser` nếu cần,
     rồi set `players.a` / `players.b` trước.
   - **Bước 2 — select phụ thuộc**: **players.white/black/winner/loser** thực tế là `<select>`
     (xem mục 6.1); nếu username chưa có trong option thì tool tự thêm `<option>` rồi chọn.
     Phải chạy sau bước 1 vì `bits.gameSearch.ts` rebuild các select này từ `players.a/b`.
     Tool cũng set `data-req-white/black/winner/loser` để `bits.gameSearch` giữ đúng lựa chọn
     nếu nó rebuild select muộn (ví dụ sau `site.load` khi auto-run pending search).
   - **Bước 3 — hasAi trước aiLevel**: set `hasAi` (và bắn `change`) trước khi set `aiLevel*`,
     để UI mở row `.aiLevel` đúng như người dùng chọn Computer.
   - Các `<select>` khác: chọn option theo **nhãn** trước, theo **value** sau (xem mục 6.2).
     Với select số dạng bucket/range (`rating*`, `turns*`, `duration*`, `clock*`, `aiLevel*`),
     nếu giá trị không khớp option có sẵn, tool **snap** sang option hợp lệ gần nhất (vd.
     `ratingMax=1999` → `1900` với bucket 100 điểm) và ghi `warnings`.
   - Text/date: set `.value`, có kiểm tra định dạng `yyyy-MM-dd` và `min`/`max` của input.
   - Checkbox `analysed`: set `.checked`.
   - Bắn cả `input` và `change` event để lichess cập nhật UI/JS.
3. **Tự chạy query**: mặc định `submit = true`. Tool gọi `form.requestSubmit()` sau ~50 ms
   (để kết quả JSON kịp trả về trước khi trang điều hướng). Truyền `submit: false` nếu chỉ muốn
   điền form và lấy URL.
4. Trả về:

```json
{
  "status": "filled",
  "filled": ["players.a", "players.white", "perf", "dateMin"],
  "autoFilled": ["players.a"],
  "warnings": ["players.a was set to \"thibault\" because players.white only works when that username is players.a or players.b."],
  "skipped": [{ "field": "source", "reason": "option not found" }],
  "query": "players.a=thibault&players.white=thibault&perf=2&dateMin=2024-01-01",
  "url": "/games/search?players.a=thibault&players.white=thibault&perf=2&dateMin=2024-01-01#results",
  "submitted": true
}
```

- `filled`: các field **tool vừa set trong lần gọi này**.
- `autoFilled`: các field tool tự thêm vì ràng buộc phụ thuộc (ví dụ thêm `players.a` từ `winner`).
- `warnings`: giải thích vì sao tự thêm/bỏ field; cũng báo khi ràng buộc đã thỏa bởi ghế `players.a/b`
  có sẵn trong form (khi đó không cần `autoFilled`).
- `query` / `url`: dựng từ **toàn bộ `FormData` hiện tại của form**, nên có thể chứa giá trị/`sort`
  mặc định đã có sẵn trên trang (không do lần gọi này set). Nếu form còn state cũ từ tìm kiếm trước,
  query sẽ phản ánh state đó; test sạch bằng `form.reset()` hoặc mở tab `/games/search` mới.
- `submit: true` (mặc định) → gọi `form.requestSubmit()` sau ~50 ms (không dùng `.submit()` để
  không mất handler); trễ để JSON kịp trả về trước khi trang điều hướng.
- `submit: false` → chỉ điền form, không điều hướng.
- Field không tìm thấy option / không thỏa phụ thuộc → ghi vào `skipped`, **không throw**.

---

## 4. Tool 2 — `lichess_analyse_game`

### 4.1 Input

```jsonc
{
  "gameId": "abcd1234",          // hoặc "url": "https://lichess.org/game/abcd1234"
  "topN": 10,                    // số nước bước ngoặt muốn trả về, mặc định 10, tối đa 100
  "order": "chronological",      // "chronological" (mặc định) | "importance"
  "mover": "both"                 // "both" (mặc định) | "white" | "black"
}
```

- `gameId` thắng `url`.
- Nếu không truyền cả hai, tool **tự lấy ván đang xem**:
  1. `location.pathname` khớp `^/(game|analyse|analysis)/[A-Za-z0-9]{8}`;
  2. nếu URL không có id (ví dụ `/analysis`), đọc `<script id="page-init-data">` của analysis board.
- Nếu vẫn không có ván nào đang mở → trả `{ status: "skipped", hasEval: false }`, **không coi là lỗi**.
- `gameId` truyền tay mà sai định dạng (không phải 8 chữ/số) → `INVALID_GAME_ID`.
- `topN` mặc định 10, kẹp trong `[1, 100]`. Dùng số lớn (ví dụ 100) để quét cả ván.
- `order` mặc định `chronological` (theo `ply`) để dễ theo dõi diễn biến; `importance` xếp theo `|delta|` giảm dần.
- `mover` lọc nước của một bên; áp dụng cho cả `turningPoints` lẫn `bestMove`.

Một số câu hỏi điển hình và cách gọi:

| Câu hỏi | Input |
|---|---|
| 10 nước bước ngoặt theo thứ tự thời gian | `{ topN: 10 }` (mặc định) |
| 3 nước sụp đổ lớn nhất | `{ topN: 3, order: "importance" }` |
| Nước hay nhất của Trắng | `{ mover: "white" }` → xem `bestMove` |
| Toàn bộ nước của Đen | `{ mover: "black", topN: 100 }` |

### 4.2 Pipeline

1. `fetch('/game/export/{id}?evals=1')`.
2. Bỏ header PGN, bỏ biến thể trong `( ... )`, chỉ đọc dòng chính.
3. Tách SAN, bỏ số nước, bỏ `!?`, bỏ token kết quả `1-0 / 0-1 / 1/2-1/2 / *`.
4. Đọc `{ [%eval X] }` gắn sau mỗi nước:
   - `X` số thực (đơn vị **tốt**) → `cp = X * 100`;
   - `X = #N` / `#-N` → mate, quy đổi `cp = (21 - min(10, |N|)) * 100 * dấu`.
   - `X = #0` (nước vừa chiếu hết) → lấy dấu theo SAN `#` và bên đi nước đó; nếu không phải
     nước chiếu hết thì coi như không có eval mới và giữ mốc trước.
5. `expectedScore` giống hệt `ui/lib/src/ceval/winningChances.ts`
   (cp bị kẹp `±1000`; mate **không** kẹp).
6. `delta = es[nước này] - es[nước có eval hợp lệ gần nhất trước đó]`.
   Nếu thiếu eval ở giữa, tool nối với mốc hợp lệ gần nhất (đúng như PROMPT.md).
7. Tính `bestMove` = nước mà bên đi nước đó đạt expected score cao nhất (góc nhìn bên đi).
8. Lọc theo `mover`, sắp theo `|delta|` giảm dần, lấy `topN`, gắn `importanceRank` (1 = lớn nhất)
   và đánh dấu đúng một nước `isBiggest: true`.
9. Nếu `order = "chronological"` thì sắp lại theo `ply`; `importanceRank` / `isBiggest` vẫn giữ nguyên.

### 4.3 Output

```json
{
  "gameId": "abcd1234",
  "hasEval": true,
  "plies": 63,
  "evaluatedPlies": 63,
  "order": "chronological",
  "mover": "both",
  "topN": 10,
  "bestMove": {
    "ply": 19,
    "moveNumber": 10,
    "mover": "white",
    "san": "Bxc6",
    "evalAfter": 12.83,
    "mateAfter": null,
    "expectedScoreForMover": 0.951
  },
  "turningPoints": [
    {
      "ply": 11,
      "moveNumber": 6,
      "mover": "white",
      "san": "exd5",
      "evalBefore": 0.4,
      "evalAfter": -1.3,
      "mateBefore": null,
      "mateAfter": null,
      "esBefore": 0.23,
      "esAfter": -0.32,
      "delta": -0.55,
      "kind": "blunder",
      "importanceRank": 3,
      "isBiggest": false
    },
    {
      "ply": 41,
      "moveNumber": 21,
      "mover": "black",
      "san": "Qxb2",
      "evalBefore": 0.35,
      "evalAfter": -2.1,
      "mateBefore": null,
      "mateAfter": null,
      "esBefore": 0.22,
      "esAfter": -0.36,
      "delta": -0.58,
      "kind": "blunder",
      "importanceRank": 1,
      "isBiggest": true
    }
  ],
  "game": {
    "white": "Alice",
    "black": "Bob",
    "result": "1-0",
    "variant": "Standard",
    "opening": "Sicilian Defense",
    "date": "2024.01.01",
    "event": "Rated Blitz game"
  }
}
```

- `turningPoints` mặc định **theo thời gian** (`ply` tăng dần). `importanceRank` cho biết độ lớn
  (`1` = swing lớn nhất); đúng một phần tử có `isBiggest: true`. Nếu muốn xếp theo độ lớn,
  truyền `order: "importance"`.
- `bestMove`: nước mà bên đi nước đó đạt expected score cao nhất, tức "nước hay nhất bàn cờ"
  theo nghĩa thế cờ sau nước đó. Khác với `isBiggest` (swing lớn nhất).
  `expectedScoreForMover` dùng thang `-1 … 1` **góc nhìn bên đi nước đó** (dương = có lợi).
- `evalBefore` / `evalAfter`: đơn vị **tốt**, góc nhìn Trắng. Mate được biểu diễn bằng
  `±(21 - min(10, |N|))` (khoảng ±11 … ±21) và nói rõ ở `mateBefore` / `mateAfter`.
- `esBefore` / `esAfter`: expected score `-1 … 1`, góc nhìn Trắng.
- `kind` theo `|delta|`:

| Ngưỡng | kind |
|---|---|
| `>= 0.30` | `blunder` |
| `>= 0.15` | `mistake` |
| `>= 0.07` | `inaccuracy` |
| còn lại | `fine` |

Ngưỡng nằm trong `KIND_THRESHOLDS`, không rải số trong code.

### 4.4 Lỗi (đều là kết quả hợp lệ, không throw)

| `error` | Khi nào |
|---|---|
| `INVALID_GAME_ID` | Có truyền `gameId`/`url` nhưng id không phải 8 chữ/số |
| `FETCH_FAILED` | Mạng lỗi hoặc HTTP không 2xx |
| `EMPTY_PGN` | PGN không có nước đi |
| `NO_EVAL` | Không có `[%eval]` nào (ván chưa phân tích) |

Không có ván nào đang mở **không phải lỗi**:

```json
{
  "gameId": null,
  "status": "skipped",
  "hasEval": false,
  "message": "No game is currently open. Pass gameId or url to analyse a specific game."
}
```

Ví dụ `NO_EVAL`:

```json
{
  "gameId": "abcd1234",
  "hasEval": false,
  "plies": 63,
  "error": "NO_EVAL",
  "message": "Ván chưa có eval do server lưu. Phân tích bằng engine trong browser (analysis board) không được ghi vào DB.",
  "hint": "Cần server analysis (fishnet) hoặc một ván đã được phân tích phía server; kiểm tra PGN export có [%eval] chưa.",
  "game": { "white": "Alice", "black": "Bob" }
}
```

Tool **không** tự chạy engine, **không** đọc DOM analysis board, **không** sửa PGN.
Cách bật server analysis trên local: xem §6.4.

---

## 5. Cách build & test

```bash
cd ~/ws/lila
export PATH=$HOME/.nvm/versions/node/v24.22.1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# build UI (tsc + esbuild + sass)
cd ui && ./build

# test riêng module webmcp
cd .. && node ui/test webmcp.test.ts

# lint 3 file đã đụng
node_modules/.bin/oxlint --type-aware \
  ui/lib/src/webmcp.ts ui/lib/tests/webmcp.test.ts ui/site/src/site.ts
```

Kết quả hiện tại:

- `./build` xanh, `public/compiled/manifest.json` đổi hash (kiểm tra `public/compiled/manifest.json`
  cho hash hiện tại; ví dụ bundle `site` `SUVJ37RM`, manifest `4cf880cc` sau bản clear form (không dùng `form.reset()`).
- Chỉ bundle `site` đổi hash; **không file cũ nào bị xoá**.
- 29/29 test pass.
- `oxlint --type-aware` sạch.

### Test bằng console trình duyệt

Mở `/games/search` (sau khi restart lila + Ctrl+Shift+R):

```js
// điền + tự chạy query (mặc định submit=true)
await __ltw.searchGames({ white: 'thibault', perf: 'Blitz', dateMin: '2024-01-01' })

// chỉ điền form, không điều hướng
await __ltw.searchGames({ white: 'thibault', submit: false })
```

Mở một ván **đã phân tích**:

```js
await __ltw.analyseGame({ topN: 10 })                    // 10 bước ngoặt, theo thời gian
await __ltw.analyseGame({ topN: 3, order: 'importance' }) // 3 swing lớn nhất
await __ltw.analyseGame({ mover: 'white' })               // chỉ nước Trắng + bestMove của Trắng
```

Ván chưa phân tích:

```js
await __ltw.analyseGame({ gameId: 'abcd1234' })  // → hasEval:false, error:'NO_EVAL'
```

Kiểm tra WebMCP:

```js
__ltw.tools.length          // 2
navigator.modelContext      // có hoặc undefined — cả hai đều không làm hỏng trang
```

---

## 6. Những chỗ khác `PROMPT.md` (đã xử lý)

### 6.1 `players.white/black/winner/loser` không phải text input

`PROMPT.md` ghi các field này là text. Thực tế trong `modules/gameSearch/src/main/GameSearchUi.scala`:

- `players.a`, `players.b` → `form3.input` (text);
- `players.white`, `players.black`, `players.winner`, `players.loser` → `emptySelect(...)`
  (`<select>`), và `ui/bits/src/bits.gameSearch.ts` đổ option vào từ giá trị `players.a` / `players.b`
  mỗi khi 2 ô text này đổi.

Vì vậy code:

- set `players.a` / `players.b` **trước**, bắn `input` để bits rebuild select;
- sau đó mới set 4 select player;
- nếu username không có trong option thì **tự thêm option** (không phụ thuộc thứ tự chạy của bits).

Cách này hoạt động cả khi `site.load` chưa chạy xong.

Quan trọng hơn: server (`SearchPlayer.oneOf` trong `GameSearchForm.scala`) **chỉ giữ**
`white/black/winner/loser` nếu username đó trùng `players.a` hoặc `players.b`; nếu không, filter bị
bỏ âm thầm (và nếu không có a/b thì query rỗng, trang không trả kết quả). Vì vậy tool tự điền ghế
trống từ các field này và báo lại trong `autoFilled` / `warnings`.

### 6.1b `aiLevelMin/Max` phụ thuộc `hasAi=computer`

`bits.gameSearch.ts` ẩn row `.aiLevel` khi Opponent khác Computer (`hasAi !== '1'`). Tool set
`hasAi` trước (kèm `change` event) rồi mới set `aiLevelMin/Max`; nếu người dùng chỉ truyền aiLevel,
tool tự đặt `hasAi=computer`. Nếu `hasAi=human` thì aiLevel bị bỏ và ghi vào `skipped`/`warnings`.

### 6.2 Không hardcode id số của `<select>` (và snap bucket số)

Tool chọn option theo thứ tự:

1. **value** khớp (dùng cho `status`, `source`, `sort.field/order`, các field số);
2. **nhãn** khớp chính xác (đã chuẩn hoá dấu tiếng Việt + hoa/thường);
3. **nhãn chứa** (vd. `turns` → "Number of turns");
4. không thấy → `skipped`, không lỗi.

Nhãn được lấy từ `window.i18n` (catalog `site` + `variant` nếu có) và fallback tiếng Anh, nên hoạt
động cả khi UI đang ở tiếng Việt. Không có bảng id số nào bị hardcode.

Riêng `sort.field` có value là `date | turns | averageRating` (không phải `rating`), nên
`sortField: 'rating'` được map qua nhãn "Rating" thay vì value.

### 6.3 Trang tìm kiếm của user

Route thật là `/@:username/:filterName`, nên trang search là `/@user/search` (không phải
`/@user/games`). Tool không hardcode đường dẫn này: nó tìm `form.search__form` trên trang hiện tại.

### 6.4 `?evals=1` và chuyện eval không được lưu ở local

`GET /game/export/{id}?evals=1` lấy analysis từ `analysisRepo` phía server
(`modules/api/src/main/GameApiV2.scala` → `enrich` → `analysisRepo.byGame`). Nếu engine chạy
client-side trên analysis board, **không có gì được ghi vào Mongo** → PGN không có `[%eval]` →
tool trả `NO_EVAL`. Đây là hành vi đúng, không phải bug của tool.

Trên local, analysis phía server do **fishnet client** tính. Lưu ý hai endpoint khác nhau:

| Endpoint | Dùng cho | Ai phục vụ |
|---|---|---|
| `http://127.0.0.1:9663/fishnet` | **analysis** (eval cho tool) | lila (9663) |
| `http://127.0.0.1:9665/fishnet` | **move** cho bot | lila-fishnet (9665) |

Muốn tool có eval, trỏ fishnet client vào **lila 9663**:

```bash
FISHNET_ENDPOINT=http://127.0.0.1:9663/fishnet bash setup/chay-fishnet.sh
```

Sau đó vào ván → bấm **Computer analysis** (hoặc `POST /game/<id>/request-analysis`), đợi fishnet
chạy xong. Kiểm tra:

```bash
# game đã đánh dấu phân tích chưa (an: true)
mongosh --quiet --eval 'db=db.getSiblingDB("lichess"); printjson(db.game5.findOne({_id:"<id>"},{an:1}))'

# analysis thật được lưu ở collection analysis2 (không phải analysis)
mongosh --quiet --eval 'db=db.getSiblingDB("lichess"); print("analysis2:", db.analysis2.countDocuments())'

# PGN export phải có [%eval]
curl -s "http://localhost:9663/game/export/<id>?evals=1" | grep -o "%eval" | wc -l
```

Nếu trước đó fishnet **không chạy** mà đã bấm request-analysis, work sẽ kẹt trong
`fishnet_analysis` (message Redis đã mất) và lần request sau báo
`You already have an ongoing requested analysis`. Xoá work kẹt rồi request lại:

```bash
mongosh lichess --quiet --eval 'db.fishnet_analysis.deleteMany({})'
```

Đã kiểm chứng trên máy này (2026-11-07): xoá work kẹt → request lại → fishnet client ở 9663
chạy xong → `an: true`, `analysis2: 1`, export có `[%eval 0.26] ... [%eval #0]`, tool trả
`hasEval: true`.

Hai hướng khác nếu không muốn chạy fishnet (chưa làm):

1. Export PGN từ analysis board rồi upload lại (vẫn không tự lưu analysis).
2. Cho tool tự chạy `CevalCtrl` (depth ~14) trên client — chậm 5–15 s/ván.

### 6.5 Chỉ standard/chess960 mới có eval đáng tin

Các biến thể khác vẫn tìm kiếm được (tool 1), nhưng eval/analysis có thể không có hoặc không đáng
tin (tool 2 trả `NO_EVAL`).

---

## 7. Quyết định thiết kế đáng chú ý

| Quyết định | Lý do |
|---|---|
| `window.__ltw` luôn được set, kể cả không có WebMCP | Test được ngay trên Chrome thường như PROMPT.md yêu cầu |
| Feature-detect `registerTool` → `provideTools` → `provideContext` | API preview đang đổi tên; thử lần lượt |
| Cờ `triedHosts` + `try/catch` toàn bộ phần đăng ký | Không đăng ký trùng; API lỗi/đổi shape không làm vỡ trang |
| Không `throw` ở tool execute | Agent nhận JSON lỗi có cấu trúc dễ xử lý hơn exception |
| Query URL dựng từ `FormData` | Khớp đúng những gì form thật sẽ submit (kể cả sort mặc định) |
| `submit` mặc định `true`, trễ 50 ms trước khi `requestSubmit` | Đây là tool tìm kiếm nên phải chạy query; trễ để JSON kịp trả về trước khi trang điều hướng. Khác `PROMPT.md` (yêu cầu không tự bấm), theo yêu cầu mới của người dùng. |
| `evalBefore/After` là số, mate tách riêng | Giữ đúng schema số của PROMPT.md; vẫn nói rõ mate |
| Test dùng `window.__ltw` (không export internal) | Test đúng contract công khai, không mở rộng API |

---

## 8. Test đã viết

File `lila/ui/lib/tests/webmcp.test.ts`, dùng `node:test` + jsdom có sẵn của repo:

1. `__ltw` expose đúng 2 tool.
2. `__ltw.debug()` / `__ltw.register()` / `__ltw.attempts` sẵn sàng để debug.
3. Search: điền `players.a`, `players.white` (select), `perf`, `mode`, `dateMin`, `analysed`,
   `sort.field`, `sort.order`; kiểm tra URL/query.
4. Search: shortcut `black: 'Bob'` tự điền `players.a` để query có hiệu lực.
5. Search: `white` + `black` tự điền cả 2 ghế `players.a/b`.
6. Search: đã có `players.a`, thêm `winner` → tự điền `players.b`.
7. Search: cả 2 ghế đã là tên khác → bỏ `winner`, ghi `skipped` + `warnings`.
8. Search: chỉ truyền `aiLevelMin/Max` → tự set `hasAi=computer`.
9. Search: `hasAi=human` + aiLevel → bỏ aiLevel, ghi `skipped` + `warnings`.
10. Search: có player filter mới → xóa state người chơi cũ trên form (giữ hidden/fixed seat), tránh rò rỉ query trước.
11. Search: **không** chỉ định player → vẫn xóa player state cũ (giữ hidden/fixed seat), để query kiểu "Elo > 400, đấu với máy" không dính người chơi cũ.
12. Search: search mới **không** chỉ định filter non-player (date/rating/...) → `form.reset()` xóa chúng, tránh rò rỉ (vd. sau search có date+rating, query "astra thua" không giữ date/rating cũ).
13. Search: mặc định tự submit (`requestSubmit` được gọi sau ~50 ms).
14. Search: `submit:false` → không submit.
15. Search: giá trị số bucket không khớp option (vd. `ratingMax=1999`) → snap sang option hợp lệ gần nhất + `warnings`.
16. Search: giữ `winner/loser` khi `bits.gameSearch` rebuild select phụ thuộc từ `players.a/b` (qua `data-req-*`).
17. Search: option/ngày sai → `skipped`, không throw.
18. Search: không có form → `navigating` + lưu pending; auto-run trên `/games/search` khi form sẵn sàng.
19. Analyse: mặc định theo thời gian, đánh dấu đúng một `isBiggest`, có `importanceRank`, `bestMove`.
20. Analyse: `order='importance'` xếp theo `|delta|`.
21. Analyse: `topN` + `mover` lọc đúng.
22. Analyse: bỏ biến thể, chỉ đọc dòng chính.
23. Analyse: `#0` ở nước chiếu hết lấy đúng dấu theo SAN.
24. Analyse: ván không eval → `NO_EVAL`, không throw.
25. Analyse: lấy `gameId` từ URL analysis.
26. Analyse: tự lấy ván từ URL trang hiện tại.
27. Analyse: tự lấy ván từ `page-init-data` khi URL không có id.
28. Analyse: không có ván nào mở → `status: 'skipped'`.
29. Analyse: `gameId` sai định dạng → `INVALID_GAME_ID`.

Chạy:

```bash
cd ~/ws/lila && node ui/test webmcp.test.ts
```

---

## 9. Không thấy tool trong extension WebMCP — debug

### 9.1 Kiểm tra nhanh trong Console

```js
// 1. Bundle mới đã load chưa? (description/schema mới?)
__ltw?.tools.find(t => t.name === 'lichess_search_games')?.description

// 2. Trình duyệt có WebMCP API không, và API nào?
__ltw.debug()

// 3. Lần đăng ký vừa rồi dùng host/API nào, có lỗi gì?
__ltw.attempts

// 4. Hỏi thẳng trình duyệt xem nó đang giữ tool nào
await __ltw.browserTools()
```

Nếu `__ltw` đã mới nhưng extension vẫn gọi sai/thiếu tham số: extension có thể đang **cache tool
schema/description của phiên trước**. Sau khi hard reload trang (`Ctrl+Shift+R`), hãy reload
extension hoặc mở lại panel/chat WebMCP để nó đọc lại tool.

Với `lichess_search_games`, `query` lấy từ toàn bộ `FormData` hiện tại nên có thể chứa giá trị sót
từ tìm kiếm trước. Để test sạch: `document.querySelector('form.search__form')?.reset()` rồi gọi lại
với `submit: false`.

Ví dụ `__ltw.debug()` khi **không có API**:

```js
{
  'navigator.modelContext': 'undefined',
  'document.modelContext': 'undefined',
  'window.modelContext': 'undefined'
}
```

→ Nguyên nhân: trình duyệt chưa bật WebMCP. Extension chỉ là công cụ xem tool; nó **không
tự thêm API** vào Chrome stable.

Ví dụ khi có API:

```js
{
  'navigator.modelContext': 'undefined',
  'document.modelContext': 'object with registerTool, getTools, executeTool',
  'window.modelContext': 'undefined'
}
```

→ `__ltw.attempts` phải có:

```js
[{ host: 'document.modelContext', api: 'registerTool', ok: true, error: null }]
```

`ok: false` + `error` → xem lỗi (thường là `NotAllowedError` do Permissions-Policy, hoặc API
preview đổi shape).

### 9.2 Yêu cầu của WebMCP native

- **Extension không phải là API.** Extension (Model Context Tool Inspector / Rook / MCP-B) chỉ
  đọc và gọi tool từ `document.modelContext`. Nếu trang không có `document.modelContext` thì
  extension không thể thấy gì.
- Cần một trong hai:
  1. **Chrome native WebMCP**: bật `chrome://flags/#enable-webmcp-testing` rồi relaunch Chrome
     (bản dev/early preview; tài liệu Chrome hiện ghi origin trial từ Chrome 149), hoặc
  2. **Polyfill**: trang phải load `@mcp-b/webmcp-polyfill` / `@mcp-b/global` để tạo
     `document.modelContext` trên Chrome thường.
- Trang phải là **secure context**: `http://localhost:9663` hoặc `https://...`.
- Phải mở **đúng bản local** (`localhost:9663`), không phải `lichess.org`.
- Restart lila + **Ctrl+Shift+R** sau khi build, nếu không sẽ chạy bundle `site` cũ.

Kiểm tra nhanh trình duyệt có API chưa:

```js
typeof document.modelContext
// "undefined"  → chưa có native API và chưa load polyfill
// "object"     → có, chạy tiếp __ltw.register()
```

Nếu dùng polyfill để test local (chỉ dev, không đưa vào production):

```js
// Console trên localhost:9663
const script = document.createElement('script');
script.src = 'https://unpkg.com/@mcp-b/webmcp-polyfill@6/dist/index.iife.js';
script.onload = () => {
  __ltw.register();
  console.log(__ltw.attempts);
};
document.head.append(script);
```

Lưu ý: polyfill phải được cài **trước** khi tool đăng ký. Vì bundle của lila chạy trước khi bạn
dán script, `__ltw.register()` ở trên là bắt buộc — đó là lý do hàm này tồn tại.

### 9.3 Những gì đã sửa để dễ debug hơn

Trước đây code chỉ lấy **một** host theo thứ tự `navigator.modelContext ?? document.modelContext`.
Nếu extension/polyfill tạo `navigator.modelContext` rỗng (không có `registerTool`) thì code bỏ qua
luôn `document.modelContext` thật → không tool nào được đăng ký.

Bây giờ code:

1. Thử **độc lập** `navigator.modelContext`, `document.modelContext`, `window.modelContext`.
2. Gọi API theo thứ tự `registerTool` → `provideTools` → `provideContext({ tools })`.
3. `registerTool` là async trong spec hiện tại → có `.then/.catch`, lỗi được ghi vào
   `__ltw.attempts` thay vì bị nuốt im lặng.
4. Thêm `__ltw.debug()`, `__ltw.attempts`, `__ltw.register()`, `__ltw.browserTools()`.

Nếu extension/polyfill inject API **sau** khi bundle chạy, gọi tay:

```js
__ltw.register()
__ltw.attempts
```

### 9.4 Nếu vẫn không thấy tool

Gửi lại cho tôi output của 4 lệnh ở mục 9.1, kèm:

- `chrome://version` (bản Chrome),
- `chrome://flags/#enable-webmcp-testing` đang Enabled hay Default,
- URL đang mở (`localhost:9663/...` hay `lichess.org/...`),
- extension đang dùng tên gì (Model Context Tool Inspector / MCP-B / khác).

---

## 10. Việc chưa làm (để dành, theo PROMPT.md mục 9)

- `lichess_game_summary` (ACPL / accuracy / opening).
- `lichess_compare_games`.
- Tool phát hiện hy sinh quân (material drop mà eval không giảm).
- Share link kết quả tìm kiếm vào clipboard.
- Tool voice tiếng Việt.
- Chạy engine client trong tool 2 để khỏi phụ thuộc eval có sẵn.

---

## 11. Bật advanced search trên local (lila-search + Elasticsearch)

Advanced search của lila **không** đọc trực tiếp Mongo. Nó gọi `lila-search` (cổng 9673), service này
lại query Elasticsearch (cổng 9200). Local mặc định trong `conf/base.conf`:

```conf
search {
  enabled = false
  endpoint = "http://localhost:9673"
}
```

Vì vậy khi chưa bật backend, cả form advanced search lẫn `__ltw.searchGames` đều trả **0 games**,
dù Mongo có ván. Đây không phải lỗi WebMCP.

### 11.1 Elasticsearch

Dùng Elasticsearch **9.5.6** (khớp `elastic4s 9.5.0` mà `lila-search v3.6.1` dùng). Tải archive
thay vì git clone, giải nén thành **thư mục thường** trong monorepo:

```bash
cd ~/ws
curl -LO https://artifacts.elastic.co/downloads/elasticsearch/elasticsearch-9.5.6-linux-x86_64.tar.gz
tar -xzf elasticsearch-9.5.6-linux-x86_64.tar.gz
cd elasticsearch-9.5.6

cat > config/elasticsearch.yml <<'YAML'
cluster.name: lila-search-local
node.name: lila-search-local-1
discovery.type: single-node
network.host: 127.0.0.1
http.port: 9200
xpack.security.enabled: false
xpack.security.http.ssl.enabled: false
xpack.ml.enabled: false
action.destructive_requires_name: false
YAML

mkdir -p config/jvm.options.d
printf -- '-Xms512m\n-Xmx1g\n' > config/jvm.options.d/heap.options

./bin/elasticsearch -d -p es.pid
```

Kiểm tra:

```bash
curl http://127.0.0.1:9200
```

Nếu gặp `max virtual memory areas vm.max_map_count`, chạy:

```bash
sudo sysctl -w vm.max_map_count=262144
```

### 11.2 lila-search v3.6.1

Dùng đúng tag khớp client `3.6.1` trong `lila/project/Dependencies.scala`. Cũng tải archive để
thành thư mục thường (không có `.git`), đặt cạnh các repo khác trong monorepo:

```bash
cd /tmp
curl -L -o lila-search-v3.6.1.tar.gz \
  https://github.com/lichess-org/lila-search/archive/refs/tags/v3.6.1.tar.gz
tar -xzf lila-search-v3.6.1.tar.gz
mv lila-search-3.6.1 ~/ws/lila-search
cd ~/ws/lila-search
```

**Patch local bắt buộc:** `BotUserCache` của upstream ném lỗi nếu DB không có user nào
`title = "BOT"`, trong khi DB dev thường không có bot. Bỏ guard đó trong
`modules/lila-mongo/src/main/scala/BotUserCache.scala` (xóa block `IO.raiseError(... loaded 0 bot IDs ...)`
và import `cats.syntax.all.*` không còn dùng). Không cần sửa dữ liệu Mongo.

Index game rồi chạy service (sbt 2 cần quote cả command):

```bash
export MONGO_URI="mongodb://127.0.0.1:27017"
export MONGO_DATABASE="lichess"
export MONGO_STUDY_URI="mongodb://127.0.0.1:27017"
export ELASTIC_URI="http://127.0.0.1:9200"

sbt -Dsbt.color=false "ingestorCli/run index --index game --since 0 --refresh"
sbt -Dsbt.color=false "app/stage"
./modules/app/target/universal/stage/bin/lila-search-app
```

`lila-search-app` giữ terminal ở cổng 9673. Kiểm tra ở terminal khác:

```bash
curl -s http://127.0.0.1:9673/api/health
curl -s "http://127.0.0.1:9200/_cat/indices?v"
curl -s "http://127.0.0.1:9200/game/_count"
```

Lưu ý: `GameRepo.gameFilter` của upstream chỉ index game **đã kết thúc** (`s >= 30`), không phải
game import (`so != 7`), và có ít nhất một user (`us` khác rỗng). Vì vậy game anonymous đấu với
AI sẽ không được index dù có trong Mongo — đây là hành vi đúng.

### 11.3 Bật trong lila

Sửa `lila/conf/application.conf` (file này đã gitignore):

```conf
search.enabled = true
```

Restart lila (`./lila.sh` → `run`). Không cần build lại UI.

Sau đó mở `http://localhost:9663/games/search`, tìm theo username hoặc rating range; kết quả phải
xuất hiện. `__ltw.searchGames(...)` cũng trả URL có kết quả thật.

Đã kiểm chứng trên máy này (2026-11-07):

```text
GET  http://127.0.0.1:9200/game/_count        -> {"count":2}
POST http://127.0.0.1:9673/api/count          -> {"count":2}
POST http://127.0.0.1:9673/api/search/0/10    -> {"hitIds":["0fmkLncq","emMqTNl8"]}
GET  /games/search?players.a=astra            -> 2 link /game/..., "Search in 3 chess games"
```

`/games/search` yêu cầu đăng nhập (route dùng `AuthBody`), nên test bằng `curl` phải kèm cookie
`lila2` của một session hợp lệ; còn trong browser chỉ cần đang đăng nhập.

### 11.4 Lưu ý

- Mongo local đang là **standalone**, nên `ingestorApp/run` (watch change stream) sẽ không chạy.
  Dùng batch `ingestorCli/run ...` mỗi khi có ván mới, hoặc chuyển Mongo sang replica set nếu muốn
  watch tự động.
- ES 9.5.6 tải ~839 MB, giải nén thêm ~1.5 GB; đặt heap 512 MB–1 GB cho WSL2.
- Chỉ dùng cho local dev; đừng commit `conf/application.conf`.
