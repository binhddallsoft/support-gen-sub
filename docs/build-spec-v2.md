# Đặc tả bổ sung đợt 2 — dịch tiếng Việt, trình sửa trực tiếp, nâng cấp công nghệ

> Đọc SAU `docs/build-spec.md`. Chỗ nào tài liệu này nói khác, **tài liệu này thắng**.
> `docs/format-contract.md` (README) vẫn là cao nhất cho định dạng dòng Hán/pinyin.

Mục tiêu cuối của người dùng, nguyên văn: *"chỉ cần dán link ytb và sinh ra file sub đầy đủ có sang
tiếng trung"*, trước mắt là **tiếng nói → file sub tiếng Trung + file sub tiếng Việt**, và
*"có thể kiểm tra và chỉnh sửa trực tiếp hoặc import vào Aegisub để check và sửa lại"*.

Nghĩa là tool phải giao **hai file** cho mỗi video, và phải có **trình sửa ngay trong app**.

---

## 1. Đánh số lại pipeline

Chèn thêm chặng dịch. Đánh số lại cho liền mạch:

```
S0 fetch → S1 audio → S2 ASR → S3 cleanup → S4 cue-split → S5 tokenize+pinyin
   → S6 normalize → S7 AI assist → S8 TRANSLATE (mới) → S9 emit
```

- **Đổi tên file** `srtgen/stages/s8_emit.py` → `srtgen/stages/s9_emit.py`.
- **Tạo mới** `srtgen/stages/s8_translate.py`.
- Cập nhật `srtgen/pipeline.py`, `srtgen/cli.py` và mọi chỗ import theo.
- Tên bước hiển thị đổi thành `Bước n/10`.

Tên tiếng Việt của các bước (dùng nguyên văn trong UI):

| # | Tên hiển thị |
| --- | --- |
| 1/10 | Tải video và tách âm thanh |
| 2/10 | Xử lý âm thanh |
| 3/10 | Nghe và gỡ băng *(lâu nhất)* |
| 4/10 | Dọn kết quả gỡ băng |
| 5/10 | Chia câu theo nhịp thoại |
| 6/10 | Tách cụm và sinh pinyin |
| 7/10 | Chuẩn hoá định dạng |
| 8/10 | Nhờ AI soát tên riêng và chữ khó |
| 9/10 | Dịch sang tiếng Việt |
| 10/10 | Xuất file |

---

## 2. Hợp đồng định dạng file phụ đề tiếng Việt

Đo trực tiếp từ các cặp file người dùng đang dùng thật (`BonnieBears_tap1`, `news_myiran`,
`tintuc_ai`, `TheUntamed_tap1`): **mọi cặp đều cùng số block và cùng timestamp tuyệt đối.**

File `<title>_vi.srt`:

```
1
00:01:22,100 --> 00:01:28,980
Những chú gấu Boonie

2
00:01:28,980 --> 00:01:31,980
Tập 1: Hàng xóm mới
```

Ràng buộc **cứng**:

1. Số block **bằng đúng** số block của file `.srt` tiếng Trung.
2. Timestamp **giống từng ký tự** với file tiếng Trung, cue thứ i khớp cue thứ i.
3. Đúng **3 dòng** mỗi block: số thứ tự / timestamp / một dòng tiếng Việt.
4. UTF-8 **có BOM**, newline LF, giữa các block đúng 1 dòng trống.
5. **Không** khoảng trắng đầu/cuối dòng. (File hiện tại của người dùng có space thừa cuối dòng
   — đó là lỗi, tool phải cắt sạch.)
6. Dấu câu dòng tiếng Việt dùng dấu **ASCII bình thường** (`,` `.` `?` `!` `…`) — đây là tiếng Việt,
   KHÔNG áp luật dấu câu tiếng Trung của README lên dòng này.
7. Marker đổi người nói `- ` nếu cue tiếng Trung có thì dòng tiếng Việt phải giữ đúng vị trí.
8. Cue không có lời (nhạc nền) → dòng tiếng Việt để trống thì **bỏ hẳn cue đó ở cả hai file**
   là SAI. Phải giữ cue và để nguyên văn bản gốc hoặc dấu `♪`, để hai file luôn khớp chỉ số.

Đặt tên file theo đúng thói quen hiện có của người dùng: `<title>.srt` và `<title>_vi.srt`.

Thêm mã lỗi vào `srtgen/core/rules.py`:

| code | severity | luật |
| --- | --- | --- |
| `VI_BLOCK_COUNT` | error | số block file vi khác file zh |
| `VI_TIMESTAMP_DRIFT` | error | timestamp cue thứ i không khớp file zh |
| `VI_EMPTY_LINE` | warn | cue có dòng tiếng Việt rỗng |
| `VI_TRAILING_SPACE` | error | thừa khoảng trắng đầu/cuối dòng |
| `VI_MARKER_SYNC` | warn | vị trí marker `-` lệch so với file zh |

Và hàm `validate_pair(zh_doc, vi_doc) -> list[Finding]`.

---

## 3. `srtgen/stages/s8_translate.py` — chặng dịch

```python
def run(ctx, on_progress) -> dict
```

### Bảo đảm cấu trúc

Cùng triết lý với token list: **số cue không đổi là bất khả thi về cấu trúc, không phải thứ đi kiểm.**
Dịch theo lô, mỗi lô gửi kèm chỉ số cue và **ép JSON schema trả về ĐÚNG N phần tử có đúng các chỉ số đã gửi**.
Thiếu/thừa phần tử → thử lại lô đó; thử lại vẫn hỏng → giữ nguyên văn bản gốc cho cue đó và ghi finding,
**tuyệt đối không dồn/xê dịch cue**.

### Chất lượng dịch — đây là điểm ăn thua

Nhìn file người dùng đang dùng (`"Giới nhân viên văn phòng đang âm thầm bị buộc phải xếp hàng rời khỏi
thị trường lao động."`) thì rõ là dịch có ngữ cảnh, không phải Google Translate từng dòng. Vì vậy:

- **Gửi theo lô 20–30 cue**, kèm **5 cue trước và 5 cue sau** làm ngữ cảnh (chỉ để đọc, không dịch lại).
- **Bảng thuật ngữ (glossary)** lấy từ `names.json`: tên nhân vật/địa danh có bản tiếng Việt cố định,
  ép model dùng đúng. Đây là thứ giữ tên nhân vật nhất quán suốt 40 phút phim — giá trị lớn nhất.
- Chỉ dẫn văn phong đưa vào prompt: phụ đề phim, **khẩu ngữ tự nhiên**, câu ngắn, xưng hô nhất quán
  theo quan hệ nhân vật, giữ sắc thái (đùa/gắt/trang trọng), **không thêm chú thích**,
  **không dịch chữ trong `《》`** thành nghĩa mà giữ tên tác phẩm.
- `temperature = 0`, `responseSchema` ép JSON.
- **Cache** theo hash `(zh_text, target_lang, model, glossary_hash)` ở `user_cache_dir()`,
  dùng chung mọi video.

### Nhà cung cấp dịch — `srtgen/providers/translate.py`

```python
class Translator(Protocol):
    name: str
    needs_key: bool
    def translate_batch(self, items: list[dict], context: dict, *, target: str) -> dict[int, str]: ...
```

| lớp | nguồn | khi nào dùng |
| --- | --- | --- |
| `GeminiTranslator` | Gemini, dùng CHUNG ô API key đã có | **mặc định khi có key** — chất lượng cao nhất |
| `GoogleFreeTranslator` | `deep-translator` endpoint miễn phí của Google | **mặc định khi KHÔNG có key** — không cần đăng ký |
| `NullTranslator` | trả nguyên văn | test tất định, chạy offline |

`GoogleFreeTranslator`: dịch từng cue (endpoint không nhận ngữ cảnh), có **retry backoff cấp số nhân
khi bị chặn tốc độ** (học từ voice-pro), và **phải cảnh báo rõ trong UI** rằng bản dịch miễn phí
kém hơn đáng kể, nên nhập API key nếu cần chất lượng.

Chọn nhà cung cấp tự động: có key → Gemini; không key → Google free; `--no-translate` → bỏ chặng.

Lưu `S8_translate.json`: bản dịch từng cue, nhà cung cấp, số request, cache hit/miss, cue nào phải
giữ nguyên văn vì lỗi.

---

## 4. Trình sửa trực tiếp trong app — tab "Sửa phụ đề"

Đây là yêu cầu mới, ngang hàng quan trọng với việc tạo file. Thêm **tab thứ 5** vào web UI.

### Bố cục

Bảng một dòng một cue, cột:

| # | Thời gian | ▶ | Tiếng Trung | Pinyin | Tiếng Việt | ⚠ |
| --- | --- | --- | --- | --- | --- | --- |

- Cột `▶`: **bấm để nghe đúng đoạn âm thanh của cue đó**. Đây là tính năng quan trọng nhất của
  trình sửa — soát phụ đề mà không nghe lại được thì vô nghĩa.
  Backend phục vụ file wav đã xử lý ở S1 qua `GET /api/jobs/{id}/audio` có hỗ trợ HTTP Range,
  frontend dùng một thẻ `<audio>` duy nhất, đặt `currentTime = cue.start` và tự dừng ở `cue.end`.
- Cột `⚠`: hiện số finding của cue đó, bấm vào xem chi tiết tiếng Việt.
- Dòng đang chọn tô sáng; dòng có lỗi viền đỏ nhạt; dòng có cờ AI chưa duyệt viền vàng.

### Sửa

- Bấm vào ô để sửa tại chỗ (contenteditable hoặc input), Esc huỷ, Enter xác nhận.
- **Sửa ô Tiếng Trung** → hiện nút nhỏ *"Sinh lại pinyin cho câu này"* gọi
  `POST /api/retokenize` (chạy S5+S6 cho riêng cue đó) và cập nhật ô Pinyin.
  Không tự động chạy để người dùng không bị mất phần đã sửa tay.
- **Sửa ô Pinyin** → kiểm ngay số cụm so với ô Tiếng Trung, lệch thì viền đỏ và báo
  *"Dòng Hán có 5 cụm, dòng pinyin có 4 cụm"*.
- **Sửa ô Tiếng Việt** → tự do, chỉ kiểm khoảng trắng thừa.
- Sửa timestamp: cho phép, kiểm chồng lấn ngay.
- **Ctrl+Z / Ctrl+Shift+Z**: hoàn tác nhiều bước, giữ trong bộ nhớ trình duyệt lẫn gửi lên server.
- Phím tắt: `Space` nghe cue đang chọn, `↑/↓` chuyển cue, `Tab` sang ô kế.

### Lưu

- Nút **Lưu** ghi lại cả `.srt`, `_vi.srt`, `.bundle.json` và chạy lại validator.
- **Tự lưu nháp** vào `work/<video_id>/edit_draft.json` mỗi 5 giây để không mất công khi đóng nhầm.
- Nút **Hoàn nguyên về bản máy tạo** có hỏi xác nhận.

### Vào trình sửa

- Từ màn hình kết quả: nút **"Kiểm tra và sửa"**.
- Từ tab "Kiểm tra file": mở file `.srt` có sẵn (kèm `_vi.srt` nếu cùng thư mục) để sửa,
  **không cần chạy pipeline** — dùng được ngay cho file bên dịch gửi sang.

### API bổ sung

| method | path | việc |
| --- | --- | --- |
| GET | `/api/jobs/{id}/doc` | trả toàn bộ cue dạng JSON cho trình sửa |
| PUT | `/api/jobs/{id}/doc` | lưu bản đã sửa, trả findings mới |
| POST | `/api/retokenize` | `{zh}` → trả `{tokens, zh_line, py_line}` cho một câu |
| GET | `/api/jobs/{id}/audio` | phát wav, **bắt buộc hỗ trợ HTTP Range** |
| POST | `/api/open-local` | mở cặp `.srt`/`_vi.srt` có sẵn vào trình sửa |

---

## 5. Nâng cấp công nghệ (học từ voice-pro)

### 5.1 Model mặc định đổi sang `large-v3-turbo` — thay đổi có tác động lớn nhất

Số đo: trên CPU int8, 5 phút audio mất **19.6s** với `large-v3-turbo` so với **52.6s** với `large-v3`
(nhanh ~2.7×, có nguồn đo tới 6×). RAM đỉnh **1545MB** so với **2953MB**. Model nặng ~1.6GB thay vì ~3GB.

Trên iMac 2017 điều này biến "video 40 phút chờ 35 phút" thành "chờ khoảng 10–12 phút".
Đây là điểm đau lớn nhất mà `plan.md` mục 7 đã nêu.

Đổi `srtgen/config/default.yaml`:

```yaml
asr:
  model: large-v3-turbo        # trước là large-v3
```

Bảng model cho người dùng chọn trong UI, kèm **ước lượng thật cho video 40 phút trên iMac 2017**:

| Lựa chọn trong UI | model | thời gian ước tính | dung lượng tải |
| --- | --- | --- | --- |
| **Cân bằng (khuyên dùng)** | `large-v3-turbo` | ~10–15 phút | ~1.6 GB |
| Chính xác nhất | `large-v3` | ~30–40 phút | ~3 GB |
| Nhanh, độ chính xác vừa | `medium` | ~8–12 phút | ~1.5 GB |
| Thử nhanh | `small` | ~4–6 phút | ~0.5 GB |

`large-v3-turbo` là model **chỉ để gỡ băng**, không làm nhiệm vụ dịch của Whisper — không sao,
vì ta dịch riêng ở S8 bằng model ngôn ngữ, chất lượng cao hơn hẳn Whisper dịch thẳng.

### 5.2 Tải model có thể nối lại

Học từ voice-pro ("self-healing Whisper model downloads after interruptions"):
tải dở bị đứt mạng thì lần sau **nối tiếp**, không tải lại từ đầu. Kiểm tra toàn vẹn file
trước khi dùng; hỏng thì tải lại và báo rõ bằng tiếng Việt.

### 5.3 Tách giọng khỏi nhạc nền

`plan.md` đã nêu `demucs` là tuỳ chọn. Giữ nguyên, nhưng:
- Trong UI gọi là **"Tách giọng khỏi nhạc nền (dành cho phim nhiều nhạc)"**, ghi rõ *làm chậm khoảng 2×*.
- S3 đếm tỉ lệ ảo giác; vượt ngưỡng thì báo cáo **tự gợi ý** bật tuỳ chọn này và chạy lại từ S1.

### 5.4 Chống chặn tốc độ

Mọi lần gọi mạng (Gemini, Google free, tải model) dùng **retry backoff cấp số nhân**, không nhảy key ngay.

---

## 6. Bộ cài macOS làm lại — bỏ hẳn Homebrew

Thay toàn bộ mục 12 của `build-spec.md`.

**Vì sao đổi:** cài Homebrew trên máy trắng kéo theo Xcode Command Line Tools (~1GB), hỏi mật khẩu
quản trị, và mất 10–20 phút. Với người dùng không phải dân IT đó là chỗ hỏng đầu tiên.

Đường mới, **không cần sudo, không cần Homebrew, không cần Xcode**:

| việc | cách làm |
| --- | --- |
| Trình quản lý | tải `uv` bằng `curl -LsSf https://astral.sh/uv/install.sh \| sh` — một file nhị phân, cài vào `~/.local/bin`, không cần quyền quản trị |
| Python | `uv python install 3.12` — bản standalone, không đụng Python hệ thống, không cần sudo |
| Thư viện | `uv venv` + `uv pip install -r requirements-macos.txt` |
| ffmpeg / ffprobe | tải bản tĩnh x86_64 từ `https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip` và `.../ffprobe/zip`, giải nén vào thư mục app, `chmod +x`. Không cần cài hệ thống. |
| yt-dlp | cài bằng pip trong venv → nút **"Cập nhật yt-dlp"** trong app chỉ là `uv pip install -U yt-dlp` |
| model Whisper | tải vào `~/Library/Application Support/SrtGen/models`, có thanh tiến trình, nối lại được |

Toàn bộ nằm gọn trong `~/Library/Application Support/SrtGen/`. Gỡ cài đặt = xoá một thư mục.

Yêu cầu thêm cho `CaiDat.command`:
- Dò kiến trúc: `x86_64` là máy đích; `arm64` thì ffmpeg evermeet chạy qua Rosetta, ghi chú rõ.
- Nếu máy **đã có** ffmpeg/yt-dlp hệ thống thì dùng luôn, bỏ qua bước tải.
- Mỗi bước in tiếng Việt kèm ✓/✗ và thời gian ước tính.
- Chạy lại lần hai phải an toàn (idempotent).
- Hỏng giữa chừng thì lần sau **chạy tiếp từ bước hỏng**, không làm lại từ đầu.
- Cuối cùng chạy `srtgen doctor` và in kết quả.

`srtgen doctor` phải kiểm thêm: `uv` có chưa, ffmpeg tìm thấy ở đâu (hệ thống hay thư mục app),
model nào đã tải, dung lượng đĩa trống có đủ cho model không.

---

## 7. Xuất thêm định dạng cho việc soát

Ngoài `<title>.srt`, `<title>_vi.srt`, `<title>.bundle.json`, `<title>.report.html`:

- `<title>_song-ngu.ass` *(tuỳ chọn, mặc định BẬT)* — file Aegisub **song ngữ** để soát cho nhanh:
  ba dòng một sự kiện, style riêng cho từng dòng (Hán cỡ lớn, pinyin cỡ vừa màu nhạt,
  tiếng Việt màu khác). Font macOS: `PingFang SC` cho Hán, fallback `Arial Unicode MS`;
  dòng pinyin và tiếng Việt ngăn bằng `\N`.
  Đây là thứ người dùng mở trong Aegisub để soát cả hai ngôn ngữ cùng lúc.
- Vẫn giữ `<title>.ass` một ngôn ngữ như cũ.

Trong tài liệu `HUONG-DAN.md` phải nói rõ: **mở file `_song-ngu.ass` trong Aegisub để soát**,
còn `.srt` và `_vi.srt` là file giao đi.

---

## 8. Bổ sung cho tab "Cài đặt"

- Ô **API key Gemini** (đã có) — thêm câu giải thích: *dùng cho cả soát tên riêng lẫn dịch tiếng Việt;
  để trống thì vẫn chạy được, dịch bằng Google miễn phí nhưng chất lượng thấp hơn.*
- Nút **Kiểm tra key** gọi thử một request nhỏ, báo ✓/✗ bằng tiếng Việt.
- Chọn **model gỡ băng** theo bảng ở mục 5.1, hiện rõ thời gian ước tính và dung lượng tải,
  và model nào **đã tải rồi**.
- Chọn **nhà cung cấp dịch**.
- Bật/tắt **tách giọng khỏi nhạc nền**.
- Nút **Cập nhật yt-dlp** và **Kiểm tra máy** (doctor).

---

## 9. Kiểm thử bổ sung

Thêm vào `tests/`:

- `test_translate_offline.py`: dùng `NullTranslator`, xác nhận **số cue và timestamp của
  `_vi.srt` khớp tuyệt đối** với `.srt`; lô trả thiếu phần tử thì giữ nguyên văn gốc chứ không xê dịch cue.
- `test_vi_format.py`: chạy `validate_pair` trên các cặp file thật của người dùng trong
  `corpus/` (chép sẵn `BonnieBears_tap1.srt` + `_vi.srt` vào `corpus/pairs/`), xác nhận
  validator bắt đúng lỗi khoảng trắng thừa cuối dòng mà file gốc đang có.
- `test_editor_api.py`: `PUT /api/jobs/{id}/doc` rồi `GET` lại phải ra đúng nội dung đã lưu;
  `POST /api/retokenize` với `"我来中国只有一个目的"` phải trả 6 cụm khớp cách tách của người kiểm duyệt.

Vẫn giữ ràng buộc: **không cần mạng, không cần model, không cần API key.**
