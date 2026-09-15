# Đặc tả build nội bộ — srtgen

> Đây là **hợp đồng giữa các module**. Mọi chữ ký hàm dưới đây là bắt buộc, không đổi tên.
> Nguồn sự thật về định dạng đầu ra là `docs/format-contract.md` (README).
> Nguồn sự thật về kiến trúc là `docs/plan.md`.
> Các con số hiệu chỉnh dưới đây đo trực tiếp từ `corpus/completed.srt` (1351 cue đã audit).

---

## 0. Nguyên tắc bất di bất dịch

1. **Một token list, hai dòng render.** Dòng Hán và dòng pinyin KHÔNG BAO GIỜ được sinh
   độc lập. Lệch số cụm phải là bất khả thi về cấu trúc, không phải thứ đi kiểm rồi sửa.
2. **Chỉ `io_utils` được gọi `open()`.** Mọi module khác import từ đó.
3. **Mọi thao tác dấu câu/khoảng trắng làm trên token, rồi render.** Cấm regex trên chuỗi đã ghép.
4. **Không `print()` trong thư viện.** Tiến trình báo qua callback `on_progress`.
5. Tiếng Việt cho mọi chuỗi hiển thị cho người dùng; tiếng Anh cho tên hàm/biến.

---

## 1. `srtgen/core/token.py` — mô hình dữ liệu + renderer

```python
KIND_WORD   = "word"
KIND_PUNCT  = "punct"
KIND_MARKER = "marker"

# cờ gắn vào token để S7/report biết chỗ cần người duyệt
FLAG_HETERONYM   = "HETERONYM"      # đa âm tự, chưa chắc âm
FLAG_CASE_AMBIG  = "CASE_AMBIG"     # viết hoa nhập nhằng (sau ……)
FLAG_AI_APPLIED  = "AI_APPLIED"     # AI đã sửa, cần người duyệt
FLAG_NAME        = "NAME"           # là tên riêng

@dataclass
class Token:
    kind: str = KIND_WORD
    zh: str = ""
    pinyin: str | None = None       # None với punct/marker
    confidence: float = 1.0
    source: str = "jieba"           # jieba | dict | ai | manual | punct | asr
    flags: list[str] = field(default_factory=list)

    def is_word(self) -> bool
    def to_dict(self) -> dict          # cho bundle.json
    @classmethod
    def from_dict(cls, d: dict) -> "Token"

@dataclass
class Cue:
    index: int
    start: float                     # giây
    end: float
    tokens: list[Token] = field(default_factory=list)

    def words(self) -> list[Token]   # chỉ kind == word
    def zh_text(self) -> str         # render_zh(self.tokens)
    def py_text(self) -> str         # render_py(self.tokens)
    def han_count(self) -> int       # số Hán tự trong dòng zh
    def duration(self) -> float

@dataclass
class Document:
    cues: list[Cue] = field(default_factory=list)
    meta: dict = field(default_factory=dict)   # video_id, title, source_url, model, ...
```

### Renderer — luật nối chuỗi (đã kiểm định round-trip 1351/1351)

```python
def render_tokens(tokens: list[Token], field: str) -> str
def render_zh(tokens: list[Token]) -> str      # = render_tokens(tokens, "zh")
def render_py(tokens: list[Token]) -> str      # = render_tokens(tokens, "pinyin")
```

Thuật toán, duyệt token theo thứ tự, giữ biến `prev_kind`:

| token | quy tắc phát ra |
| --- | --- |
| `word` | phát 1 space trước **nếu và chỉ nếu** `prev_kind == KIND_WORD`, rồi phát nội dung |
| `punct` | phát thẳng nội dung, **không space trước, không space sau** |
| `marker` | phát 1 space trước (bỏ qua nếu đang ở đầu dòng), phát `-`, phát 1 space sau |

Với `field == "pinyin"`: token `punct` dùng `zh` (dấu câu giống nhau ở hai dòng),
token `marker` dùng `-`, token `word` dùng `pinyin` (rỗng thì fallback `zh`).

Kết quả phải luôn thoả: `line == line.strip()`, không có `"  "` (2 space liền).
Renderer tự đảm bảo, không cần hậu xử lý.

**Bất biến bắt buộc (assert trong `validate_document`):**
`len([t for t in tokens if t.kind == KIND_WORD])` phải bằng số cụm đếm được ở
cả hai dòng sau khi bỏ punct/marker. Fail = bug renderer, không phải lỗi dữ liệu.

---

## 2. `srtgen/core/srt.py` — đọc/ghi SRT

```python
TIMESTAMP_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})$")

def parse_timestamp_line(line: str) -> tuple[float, float]
def format_timestamp(seconds: float) -> str        # "00:01:19,580", làm tròn ms
def parse_srt(text: str) -> list[RawBlock]         # RawBlock = (index:int, start, end, lines:list[str])
def parse_srt_document(text: str) -> Document      # dùng tokenize_line cho dòng 3 & 4
def emit_srt(doc: Document) -> str                 # 4 dòng/block, 1 dòng trống giữa các block
def tokenize_line(line: str) -> list[Token]        # tách 1 dòng đã render ngược về token
def merge_zh_py(zh_tokens, py_tokens) -> list[Token]   # ghép 2 dòng của file có sẵn thành 1 token list
```

`tokenize_line` — dùng cho lệnh `fix` khi đọc file người khác gửi:

- Dấu đa ký tự nhận trước: `……`, `——` (và biến thể `...`, `. . .`, `--` khi `normalize=True`).
- `-` là **marker** khi đứng đầu dòng, hoặc có space ít nhất một bên, hoặc đứng ngay sau
  dấu kết câu. `-` dính giữa hai chữ (không space) là dấu ngắt lời → đổi thành `——`.
- Ký tự trong `。，、？！：；《》（）“”‘’…—` và tương đương ASCII → `punct`.
- Còn lại tích luỹ thành `word`, space là ranh giới cụm.

`merge_zh_py` là chỗ dễ sai nhất. Yêu cầu:
- Tách riêng danh sách word của mỗi dòng.
- Nếu số word bằng nhau → ghép theo vị trí, punct lấy từ dòng zh.
- Nếu lệch → **không được đoán**. Trả về token list từ dòng zh với `pinyin=None`
  cho các word không ghép được và ghi finding `CUM_MISMATCH` để S5 sinh lại pinyin.

`emit_srt` đánh số lại từ 1 liên tục; không giữ số cũ.

---

## 3. `srtgen/core/rules.py` — validator theo README

```python
@dataclass
class Finding:
    code: str          # mã ổn định, xem bảng dưới
    severity: str      # "error" | "warn" | "info"
    cue_index: int | None
    message: str       # tiếng Việt, mô tả cụ thể, có trích đoạn
    line: str = ""     # dòng vi phạm

def validate_document(doc: Document) -> list[Finding]
def validate_text(srt_text: str) -> list[Finding]     # parse rồi validate
```

Mã lỗi, ánh xạ 1-1 với checklist 12 mục của README:

| code | severity | luật |
| --- | --- | --- |
| `BLOCK_SHAPE` | error | block phải đúng 4 dòng |
| `INDEX_SEQ` | error | số thứ tự liên tục từ 1 |
| `TIMESTAMP_FORMAT` | error | đúng `HH:MM:SS,mmm --> HH:MM:SS,mmm` |
| `TIMESTAMP_ORDER` | error | start < end, và start >= end của cue trước |
| `CUM_MISMATCH` | error | số cụm zh ≠ số cụm pinyin (sau khi bỏ punct/marker) |
| `PAIR_MISMATCH` | warn | cặp zh–pinyin cùng vị trí không khớp phát âm (kiểm bằng pypinyin) |
| `LEADING_SPACE` | error | space đầu dòng |
| `TRAILING_SPACE` | error | space cuối dòng |
| `DOUBLE_SPACE` | error | 2 space liền |
| `ASCII_PUNCT` | error | còn `, . ? ! : ;` trong nội dung |
| `SPACE_AROUND_PUNCT` | error | có space trước/sau dấu câu tiếng Trung |
| `ELLIPSIS_FORM` | error | dấu lửng không phải đúng `……` |
| `DASH_FORM` | error | dấu ngắt lời không phải đúng `——` |
| `MARKER_SPACING` | error | marker `-` không có đúng 1 space mỗi bên |
| `MARKER_SYNC` | error | vị trí marker lệch giữa hai dòng |
| `PUNCT_SYNC` | error | loại/vị trí dấu câu lệch giữa hai dòng |
| `NAME_INCONSISTENT` | warn | cùng tên riêng lúc gộp lúc tách trong 1 file |
| `ERHUA_SPLIT` | error | `儿` bị tách thành cụm riêng |
| `EMPTY_CUE` | warn | cue không có word nào |

**Ngoại lệ bắt buộc:** dấu `,` trong dòng timestamp không tính là `ASCII_PUNCT`.

`PAIR_MISMATCH` kiểm bằng cách so `lazy_pinyin(token.zh, style=NORMAL)` với
`token.pinyin` sau khi bỏ dấu thanh và lowercase. Chỉ cảnh báo, không chặn,
vì tên riêng và từ mượn sẽ lệch hợp lệ.

---

## 4. `srtgen/core/sandhi.py` — biến điệu

```python
def apply_sandhi(tokens: list[Token], cfg: dict) -> None   # sửa tại chỗ
```

Cấu hình `cfg`: `{"bu": True, "yi": False, "third_tone": False}`.

Vì pinyin sinh **theo từng token** bằng `pypinyin`, phần lớn biến điệu đã đúng sẵn
(`一个` → `yígè`, `不是` → `búshì`). Module này chỉ xử lý biến điệu **qua ranh giới token**:

- `bu`: token `不` đơn lẻ, token kế tiếp là word có âm tiết đầu thanh 4 → `bú`.
- `yi`: token `一` đơn lẻ → thanh đổi theo âm tiết sau (mặc định **tắt**, corpus giữ `yī`).
- `third_tone`: mặc định tắt.

Đo trên corpus: `bù`×100, `bú`×41, `yī`×66, `yì`×44, `yí`×32, `yǐ`×20.

---

## 5. `srtgen/core/erhua.py` — 儿化音

```python
ERHUA_HEAD = {"哪", "这", "那", "点", "一会", "一块", "今", "明", "味", "花", ...}
def merge_erhua(tokens: list[Token]) -> None
def erhua_pinyin(base_pinyin: str) -> str     # "nǎ" + 儿 -> "nǎr"
```

Luật, theo README mục 1:

- Token `儿` đơn lẻ, token trước là word danh từ/đại từ → **gộp vào token trước**,
  `zh` thành `哪儿`, `pinyin` thành dạng **rút gọn** `nǎr`.
- `一会儿` → `yíhuìr`, `一块儿` → `yíkuàir`, `这儿` → `zhèr`, `那儿` → `nàr`, `点儿` → `diǎnr`.
- Cấm sinh `nǎér` / `zhèér`.

> ⚠️ `corpus/completed.srt` ghi `nǎér`, `zhèér`, `yīhuìer` — **đó là lỗi của corpus**,
> README mới là hợp đồng. Regression test phải coi các ca này là "corpus sai", không
> phải "tool sai". Liệt kê chúng trong `tests/known_corpus_errors.py`.

`erhua_pinyin`: bỏ âm cuối `n`/`ng` nếu có rồi thêm `r`; giữ nguyên dấu thanh của
âm tiết gốc. `nǎ`→`nǎr`, `diǎn`→`diǎnr`, `wán`→`wánr`, `kòng`→`kòngr`.

---

## 6. `srtgen/core/casing.py` — viết hoa

```python
def apply_casing(doc: Document, names: dict, cfg: dict) -> None
CASE_ENDERS   = set("。？！”》）")     # cue trước kết bằng đây -> cue sau VIẾT HOA
CASE_CONT     = set("，、：；’")        # -> giữ THƯỜNG
CASE_AMBIG    = ("……",)               # -> gắn FLAG_CASE_AMBIG, mặc định theo cfg
```

Luật đo trên corpus, độ chính xác **99.16%** (1302/1313, đã trừ 37 ca `……`):

| cue trước kết bằng | cue sau | n | đúng |
| --- | --- | --- | --- |
| `。` | HOA | 711 | 99.9% |
| `？` | HOA | 220 | 100% |
| `！` | HOA | 192 | 99.5% |
| `”` `》` `）` | HOA | 12 | 91.7% |
| `，` | thường | 66 | 98.5% |
| `、` `：` `’` | thường | 18 | 94.4% |
| không có dấu (kết bằng chữ cái) | thường | 107 | 94.4% |
| `……` | **nhập nhằng** | 37 | 54/46 → gắn cờ cho S7 |

Ngoài ra:
- Cue đầu file luôn viết hoa.
- Ngay sau `marker` luôn viết hoa (bắt đầu lượt thoại mới) — corpus xác nhận.
- Tên riêng trong `names` luôn viết hoa bất kể vị trí, gắn `FLAG_NAME`.
- Viết hoa chỉ đụng **ký tự chữ cái đầu tiên** của token pinyin, giữ nguyên dấu thanh
  (`ā`→`Ā`, `ǎ`→`Ǎ`, `é`→`É`, `ǹ`→`Ǹ`). Dùng `str.upper()` trên 1 ký tự là đủ với Python.

---

## 7. Các chặng `srtgen/stages/`

Mọi chặng có cùng chữ ký:

```python
def run(ctx: Context, on_progress: Callable[[str, float], None]) -> Any
```

`Context` (`srtgen/core/context.py`):

```python
@dataclass
class Context:
    video_id: str
    work_dir: Path            # work/<video_id>/
    out_dir: Path
    cfg: dict                 # config đã merge
    doc: Document | None = None
    meta: dict = field(default_factory=dict)
    cancelled: Callable[[], bool] = lambda: False   # để UI bấm Dừng

    def stage_path(self, n: int, name: str) -> Path      # work/<id>/S<n>_<name>.json
    def save_stage(self, n: int, name: str, data: Any) -> None
    def load_stage(self, n: int, name: str) -> Any | None
    def has_stage(self, n: int, name: str) -> bool
```

| file | chặng | vào | ra |
| --- | --- | --- | --- |
| `s0_fetch.py` | tải nguồn | url hoặc file local | `audio.wav` 16kHz mono + `S0_info.json` |
| `s1_audio.py` | tiền xử lý | `audio.wav` | `audio_norm.wav` + `S1_audio.json` |
| `s2_asr.py` | ASR | wav | `S2_asr.json` (segment + word timestamp) |
| `s3_cleanup.py` | dọn ASR | S2 | `S3_clean.json` |
| `s4_cue.py` | chia cue | S3 | `S4_cues.json` |
| `s5_tokenize.py` | tokenize + pinyin | S4 | `S5_tokens.json` |
| `s6_normalize.py` | chuẩn hoá | S5 | `S6_norm.json` |
| `s7_ai.py` | tầng AI | S6 | `S7_ai.json` |
| `s8_emit.py` | kiểm + xuất | S7 | `.srt` + `.bundle.json` + `.report.html` |

**Resume:** mỗi chặng kiểm `ctx.has_stage()` trước, có rồi thì load và trả về ngay,
trừ khi `cfg["force_from"] <= n`.

### S0 — chi tiết

- Gọi `yt-dlp` qua `subprocess.run(list, ...)`, **luôn truyền list**, không nối chuỗi shell
  (đường dẫn có dấu tiếng Việt và khoảng trắng).
- `-f bestaudio --extract-audio --audio-format wav --postprocessor-args "-ar 16000 -ac 1"`
- Bắt lỗi và dịch sang tiếng Việt: video riêng tư, chặn theo vùng, `yt-dlp` cũ
  (gợi ý `yt-dlp -U`), không có mạng.
- Nhận file local: bỏ qua tải, chỉ convert bằng `ffmpeg` sang 16k mono.
- Cảnh báo pháp lý hiển thị 1 lần trong UI, không chặn.

### S2 — chi tiết

Máy đích là **iMac 2017 Core i7, 32GB RAM, không có GPU NVIDIA** → luôn CPU.

```python
WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=<n_physical>)
transcribe(audio, language="zh", word_timestamps=True, vad_filter=True,
           condition_on_previous_text=False, beam_size=5, initial_prompt=cfg["initial_prompt"])
```

- `cpu_threads`: mặc định `max(1, os.cpu_count() // 2)` (i7 2017 = 4 nhân 8 luồng → 4).
- Ước lượng thời gian: hệ số thực nghiệm ~**0.6–1.0× thời lượng** với `large-v3` int8 trên
  máy này. Hiển thị "ước tính còn X phút", cập nhật theo tiến độ thật.
- `initial_prompt` mặc định: một đoạn tiếng Trung giản thể có đủ `。，？！`, cộng tên riêng
  từ `names.json` nếu có.
- Báo tiến trình theo `segment.end / duration`.

### S4 — tham số đã hiệu chỉnh theo corpus

Đo trên 1351 cue của bản chuẩn:

| tham số | giá trị | căn cứ |
| --- | --- | --- |
| `max_chars` | **18** | max thực đo 17, p95 = 12 |
| `max_duration` | **6.0** | 8/1351 cue vượt 6s |
| `min_duration` | **0.25** | min thực đo 0.24s; 175 cue < 0.8s → **không được gộp** |
| `min_gap` | **0.0** | 261 cue có gap đúng 0 |
| `silence_split` | **0.30** | ngưỡng khoảng lặng để cắt |

Phân bố mục tiêu để so khi hiệu chỉnh: duration med 1.32s / p95 2.92s;
Hán tự med 5 / p95 12; cụm/cue med 3 / p95 8.

Ưu tiên điểm cắt: sau `。？！` → sau `，、` → khoảng lặng > `silence_split` → ép cắt.

Marker đổi người nói: **bỏ qua ở v1**, ghi rõ trong tài liệu.

### S5 — chi tiết

1. Tách dấu câu thành token `punct` **trước tiên**.
2. `jieba.lcut` phần chữ Hán; nạp `jieba.load_userdict` từ `names.json`.
3. `merge_erhua(tokens)`.
4. Sinh pinyin **theo từng token**: `lazy_pinyin(tok.zh, style=Style.TONE)` rồi `"".join(...)`.
5. `apply_sandhi`.
6. Gắn `FLAG_HETERONYM` cho token chứa `得 着 了 长 行 还 差 数 重 分 少 相 假 干 空 乐`.

### S6 — thứ tự bắt buộc

1. Chuẩn hoá dấu ASCII → dấu tiếng Trung (theo dõi trạng thái cho cặp ngoặc kép).
2. `...`/`…` → `……`; `-`/`--` ngắt lời → `——`.
3. `apply_casing`.
4. Áp `names.json` (viết hoa tên riêng).
5. Render và assert bất biến.

Vì mọi thứ làm trên token, không có bước "sửa khoảng trắng" — renderer lo.

### S7 — tầng AI

```python
# srtgen/providers/base.py
class Provider(Protocol):
    name: str
    def complete_json(self, prompt: str, schema: dict, *, model: str) -> dict: ...

# null.py -> NullProvider: trả về {} , không gọi mạng. MẶC ĐỊNH.
# gemini.py -> GeminiProvider(api_key): httpx, temperature=0, responseSchema
```

4 nhiệm vụ T1–T4 theo `docs/plan.md`. Nguyên tắc:
- **T1 chạy trước**, kết quả ghi `names.json`, nạp vào `jieba` rồi **chạy lại S5**.
- Bắt model **chọn** giữa phương án có sẵn (`pypinyin(heteronym=True)`), không tự sinh.
- Gộp 20–30 mục/request kèm cue lân cận làm ngữ cảnh.
- Cache theo hash `(zh_line, task_type, model)` trong `platformdirs.user_cache_dir`.
- AI **không ghi file .srt**; chỉ đề xuất, rule engine kiểm lại, người duyệt trên UI.
- Mọi token AI sửa gắn `FLAG_AI_APPLIED` để report liệt kê.

### S8 — xuất

- `<title>.srt` — UTF-8 **có BOM**, newline LF (cấu hình được), 4 dòng/block.
- `<title>.bundle.json` — token list, đúng shape ở README mục "backend".
- `<title>.report.html` — tiếng Việt: thống kê, danh sách finding, mục AI chưa duyệt.
- Chạy `validate_document` trước khi ghi. Có `error` → vẫn ghi file nhưng UI báo đỏ
  và report liệt kê từng lỗi kèm số cue.

---

## 8. `srtgen/config/default.yaml`

Mọi giá trị ở mục 4/5/6 phải nằm trong file này, không hardcode.
Thêm `profiles:` với `drama` (mặc định) và `news`.
Model AI để trong config (`gemini-2.5-pro` cho T1, `gemini-2.5-flash` cho T2–T4).

---

## 9. `srtgen/cli.py`

```
srtgen run <url|file> [--out DIR] [--model large-v3] [--no-ai] [--profile drama]
srtgen resume <video_id> --from s5
srtgen check <file.srt>
srtgen fix <file.srt> [--out FILE]
srtgen names <video_id>
srtgen doctor
srtgen ui                       # mở web UI
```

`doctor` kiểm: python, ffmpeg, yt-dlp (kèm tuổi bản), model đã tải chưa, RAM, số nhân,
API key, quyền ghi thư mục. In tiếng Việt, mỗi mục ✓/✗ kèm câu lệnh sửa cụ thể.

---

## 10. Web UI — `srtgen/web/`

FastAPI + HTML/CSS/JS thuần, **không build step**, không CDN (chạy offline được).

```
web/server.py      # FastAPI app, chạy 127.0.0.1:<port tự tìm>
web/jobs.py        # hàng đợi job chạy nền, 1 job/lần, có huỷ
web/static/index.html  app.js  style.css
```

API:

| method | path | việc |
| --- | --- | --- |
| GET | `/` | trang chính |
| POST | `/api/jobs` | tạo job: `{source, mode, options}` |
| GET | `/api/jobs/{id}/events` | SSE: tiến trình từng chặng, log, ETA |
| POST | `/api/jobs/{id}/cancel` | huỷ |
| GET | `/api/jobs/{id}/result` | đường dẫn 3 file + findings |
| POST | `/api/fix` | upload .srt → trả .srt đã chuẩn hoá |
| POST | `/api/check` | upload .srt → trả findings |
| GET/POST | `/api/settings` | API key, model, profile, thư mục ra |
| GET | `/api/doctor` | kết quả doctor |
| GET | `/api/names/{video_id}` GET/PUT | sửa bảng tên riêng |
| GET | `/api/download/{job}/{kind}` | tải srt/bundle/report |
| POST | `/api/reveal` | mở thư mục kết quả trong Finder |

**Yêu cầu UX (người dùng KHÔNG phải dân IT):**

- Toàn bộ tiếng Việt, không thuật ngữ kỹ thuật trần trụi. "S2 ASR" → "Bước 3/8: Nghe và gỡ băng".
- Màn hình chính chỉ có **một ô lớn**: dán link YouTube hoặc kéo thả file. Một nút **Bắt đầu**.
- Cài đặt nâng cao gập lại, mặc định đóng.
- Tiến trình: 8 bước có tên tiếng Việt, bước đang chạy có thanh chạy + **ước tính thời gian còn lại**.
  Bước ASR phải nói rõ "Bước này lâu nhất, khoảng X phút, có thể để máy chạy".
- Lỗi hiển thị bằng câu người thường hiểu + **nút hành động** (vd "Cài ffmpeg giúp tôi").
- Xong: hiện 3 nút to — **Mở thư mục**, **Tải file .srt**, **Xem báo cáo**;
  kèm câu hướng dẫn mở bằng Aegisub.
- Có trang **Kiểm tra file** riêng để chạy `check`/`fix` trên file bên dịch gửi sang —
  đây là giá trị dùng được ngay, không dính whisper.
- Không đóng được job đang chạy do lỡ tắt tab: job chạy phía server, mở lại tab thấy lại tiến trình.

---

## 11. Tương thích Aegisub (bắt buộc)

Người dùng sẽ import kết quả vào Aegisub để soát lại.

- Aegisub đọc SRT UTF-8 có BOM tốt; giữ BOM.
- Aegisub coi **mỗi dòng trong block là một dòng hiển thị**; 4 dòng → dòng 3 và 4 nằm
  trong cùng một sự kiện, ngăn bằng `\N` khi Aegisub chuyển sang ASS. Đây là hành vi đúng,
  không cần làm gì thêm, nhưng phải **ghi rõ trong hướng dẫn** để người dùng không hoảng.
- Timestamp phải tăng dần và không chồng lấn, nếu không Aegisub cảnh báo → validator
  `TIMESTAMP_ORDER` chặn trước.
- Cấm ký tự điều khiển và cấm dòng rỗng bên trong block.
- Thêm nút **"Xuất bản .ass cho Aegisub"** (tuỳ chọn): cùng nội dung, style sẵn font
  hiển thị được cả Hán và dấu thanh pinyin.

---

## 12. Bộ cài macOS — `installer/`

Máy đích: **iMac 2017, Core i7, 32GB RAM, macOS Intel**, coi như máy trắng.

```
installer/CaiDat.command        # bấm đúp để cài
installer/KhoiDong.command      # bấm đúp để chạy
installer/uninstall.command
```

`CaiDat.command` phải:
1. Kiểm và cài Homebrew nếu thiếu (hỏi trước, hiện rõ việc sẽ làm).
2. `brew install python@3.12 ffmpeg yt-dlp`.
3. Tạo venv riêng ở `~/Library/Application Support/SrtGen/venv` — không đụng python hệ thống.
4. `pip install` từ `requirements-macos.txt` (ghim phiên bản).
5. Tải model `large-v3` (~3GB) có thanh tiến trình, cho phép chọn `medium` nếu muốn nhanh.
6. Tạo shortcut `SrtGen.app` trên Desktop (dùng `osascript` tạo app bọc lệnh).
7. In tiếng Việt từng bước, gặp lỗi thì in cách xử lý.

`KhoiDong.command`: kích hoạt venv, chạy `srtgen ui`, tự mở trình duyệt vào đúng cổng,
giữ cửa sổ Terminal ẩn nếu được.

Lưu ý Intel Mac: `faster-whisper`/CTranslate2 có wheel x86_64, chạy CPU int8 tốt.
Không dùng `device="auto"`, ép `cpu`.

---

## 13. Kiểm thử — `tests/`

**Tầng 1 — bất biến renderer** (`test_renderer.py`): với mọi token list sinh ngẫu nhiên,
số word ở hai dòng luôn bằng nhau; không có space thừa; round-trip
`tokenize_line(render(t)) == t` về mặt kind/zh.

**Tầng 2 — hồi quy corpus** (`test_regression.py`): chạy `fix` trên `corpus/filter.srt`,
so với `corpus/completed.srt`.

- `validate_text(fix(filter))` phải trả về **0 finding severity=error**. Đây là cổng cứng.
- Độ chính xác viết hoa ≥ **97%** (mục tiêu đo được 99.16%).
- False positive của validator trên `completed.srt` phải bằng 0 **sau khi trừ**
  danh sách lỗi corpus đã biết trong `tests/known_corpus_errors.py`.

`known_corpus_errors.py` — 8 ca lệch số cụm và 11 ca marker thiếu space, đã xác định:

```python
CUM_MISMATCH_CUES = [2, 89, 340, 369, 729, 1038, 1280, 1324]
MARKER_SPACING_CUES = [84, 103, 550, 580, 654, 1071, 1079, 1080, 1138, 1178, 1296]
ERHUA_CONTRACTION_CUES = [143, 158, 348, 349, 350, 510, 729]   # corpus ghi ér thay vì r
```

**Tầng 3 — end-to-end**: chỉ chạy khi có sẵn audio, đo CER, Cue IoU, Format pass.
Đánh dấu `@pytest.mark.slow`, không chạy trong CI mặc định.

Test phải chạy được **không cần mạng, không cần model** (dùng `NullProvider` và
corpus có sẵn).
