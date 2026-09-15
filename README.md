# SrtGen

Dán link YouTube, máy tự nghe và viết ra **phụ đề tiếng Trung kèm pinyin** cùng **bản dịch tiếng Việt**.
Bạn xem lại, sửa ngay trong app, rồi lưu thành hai file phụ đề để giao đi.

Chạy trên **máy Mac** (dựng cho iMac 2017 trở lên). Không cần biết gì về máy tính.

---

## Cài và dùng

Cần: máy Mac, mạng Internet, ổ đĩa trống ít nhất **8 GB**, khoảng **30 phút** cho lần cài đầu.
Không cần mật khẩu máy.

**1. Tải về.** Trên trang này, bấm nút xanh **Code** rồi chọn **Download ZIP**.
Bấm đúp file vừa tải để giải nén.

**2. Mở file cài.** Mở thư mục vừa giải nén. **Bấm chuột phải** vào file **`SrtGen.command`**,
chọn **Mở** (Open). Hộp thoại hiện ra, bấm **Mở** thêm một lần nữa.

> Lần đầu phải bấm chuột phải, vì máy Mac chặn file tải từ Internet. Bấm đúp thì hộp thoại
> không có nút Mở. Nếu bấm chuột phải cũng không được: vào **Cài đặt hệ thống › Quyền riêng
> tư & Bảo mật**, kéo xuống, bấm **Vẫn mở** cạnh dòng nói về `SrtGen.command`.

**3. Chờ cài.** Cửa sổ chữ đen hiện ra. Khi được hỏi, cứ nhấn **Enter**. Chờ tới dòng
**CÀI ĐẶT XONG**.

**4. Dùng.** Biểu tượng **SrtGen** xuất hiện trên màn hình nền. Từ giờ chỉ cần bấm đúp vào đó.
Thư mục vừa tải về có thể xoá.

Trong app có tab **Hướng dẫn** giải thích từng việc bằng lời dễ hiểu.
Bản đầy đủ để in nằm ở [`HUONG-DAN.md`](HUONG-DAN.md).

### Cập nhật lên bản mới

Tải bản mới về, giải nén, rồi làm lại bước 2: chuột phải vào `SrtGen.command`, chọn **Mở**.
Máy tự nhận ra bản mới và cập nhật. Phụ đề, cài đặt và mã API đang có giữ nguyên.

Cũng dùng đúng cách này khi app bị hỏng: `SrtGen.command` tự kiểm tra và cài lại phần bị thiếu.

### Gỡ khỏi máy

Bấm chuột phải vào `installer/GoCaiDat.command`, chọn **Mở**. Nó hỏi có giữ lại các file
phụ đề không, rồi dọn sạch phần còn lại.

### Gửi cho người không có tài khoản GitHub

Kho mã này để riêng tư, nên người muốn tải phải được mời vào kho. Cách nhanh hơn: tải ZIP
một lần rồi gửi file đó qua Zalo, Google Drive hoặc USB. Người nhận làm y như bước 2 trở đi.

Người phát triển có thể đóng một gói gọn hơn (bỏ phần kiểm thử và tài liệu kỹ thuật):

```bash
python tools/make_release.py        # ra dist/SrtGen-<phiên bản>-macOS.zip
```

---

# Tài liệu cho người phát triển

Công cụ biến một link YouTube (hoặc một file âm thanh/video) thành **hai file phụ đề**:

- `<title>.srt` — tiếng Trung 4 dòng: số thứ tự / timestamp / dòng Hán / dòng pinyin
- `<title>_vi.srt` — tiếng Việt 3 dòng, **cùng số block và cùng timestamp tuyệt đối** với file trên

Người dùng cuối không phải dân IT. Xem `HUONG-DAN.md` để biết họ nhìn thấy gì.

## Ba tài liệu, theo thứ tự ưu tiên

| Tài liệu | Vai trò |
| --- | --- |
| `docs/format-contract.md` | **Cao nhất.** Hợp đồng định dạng dòng Hán/pinyin. |
| `docs/build-spec-v2.md` | Đặc tả đợt 2: dịch tiếng Việt, trình sửa, uv, `large-v3-turbo`. |
| `docs/build-spec.md` | Hợp đồng giữa các module, đợt 1. Chỗ nào v2 nói khác thì v2 thắng. |

`docs/plan.md` là bối cảnh, không phải hợp đồng.

---

## Chạy trên máy phát triển

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[all]"
python -m pytest tests -q
```

Bộ test **không cần mạng, không cần model, không cần API key**. Đó là ràng buộc
cứng: bất cứ test nào cần một trong ba thứ đó là test viết sai.

Chạy một file test:

```bash
python -m pytest tests/test_renderer.py -q
python -m pytest tests -q -k erhua
```

### Cổng cứng phải luôn xanh

| Kiểm | Kết quả bắt buộc |
| --- | --- |
| `python -m pytest tests -q` | pass toàn bộ, exit 0 |
| `run_fix(corpus/filter.srt)` rồi `validate_text` | 0 lỗi `severity=error` |
| `validate_text(corpus/completed.srt)` | đúng danh sách ở `tests/known_corpus_errors.py` |

Danh sách trong `tests/known_corpus_errors.py` là **lỗi thật của corpus**, đã soi tay
từng cái. Đừng nới danh sách đó ra để test xanh; nếu code mới làm nó lệch thì code mới sai.

---

## Kiến trúc: 10 chặng

```
S0 fetch → S1 audio → S2 asr → S3 cleanup → S4 cue → S5 tokenize
   → S6 normalize → S7 ai → S8 translate → S9 emit
```

| # | Module | Việc | Ghi ra `work/<video_id>/` |
| --- | --- | --- | --- |
| S0 | `stages/s0_fetch.py` | yt-dlp tải nguồn, ffmpeg ép wav 16kHz mono | `S0_fetch.json`, `audio.wav` |
| S1 | `stages/s1_audio.py` | loudnorm, tuỳ chọn demucs tách giọng | `S1_audio.json` |
| S2 | `stages/s2_asr.py` | faster-whisper, CPU int8, `word_timestamps=True` | `S2_asr.json` |
| S3 | `stages/s3_cleanup.py` | phồn→giản, bỏ segment rỗng, cắt lặp ảo giác | `S3_cleanup.json` |
| S4 | `stages/s4_cue.py` | chia cue theo nhịp thoại | `S4_cue.json` |
| S5 | `stages/s5_tokenize.py` | jieba + pypinyin → **token list** | `S5_tokenize.json` |
| S6 | `stages/s6_normalize.py` | dấu câu, viết hoa, 儿化音, biến điệu | `S6_normalize.json` |
| S7 | `stages/s7_ai.py` | T1 tên riêng, T2 đa âm, T3 viết hoa, T4 dấu kết câu | `S7_ai.json` |
| S8 | `stages/s8_translate.py` | dịch tiếng Việt theo lô, có ngữ cảnh và glossary | `S8_translate.json` |
| S9 | `stages/s9_emit.py` | ghi `.srt`, `_vi.srt`, `_song-ngu.ass`, bundle, report | `S9_emit.json` |

Mỗi chặng có chữ ký giống nhau:

```python
def run(ctx, on_progress) -> dict
```

`ctx` mang config + đường dẫn + `video_id`. Chặng nào cũng đọc artifact JSON của chặng
trước và ghi artifact của mình, nên `srtgen resume <video_id> --from s5` chạy tiếp được
từ giữa mà không phải nghe lại băng.

Tên bước hiển thị cho người dùng là tiếng Việt, `Bước n/10`, liệt kê ở
`docs/build-spec-v2.md` mục 1. Đừng đặt tên khác trong UI.

### Nguyên tắc bất di bất dịch

1. **Một token list, hai dòng render.** Dòng Hán và dòng pinyin không bao giờ sinh độc lập.
   Lệch số cụm phải là *bất khả thi về cấu trúc*, không phải thứ đi kiểm rồi vá.
2. **Chỉ `srtgen/io_utils.py` được gọi `open()`.** Module khác import từ đó.
3. **Mọi thao tác dấu câu/khoảng trắng làm trên token, rồi mới render.**
   Cấm regex trên chuỗi đã ghép.
4. **Không `print()` trong thư viện.** Tiến trình đi qua callback `on_progress`.
5. **Import nặng nằm trong hàm**, không ở đầu file. `srtgen doctor` phải chạy được
   trên máy còn thiếu nửa số thư viện — đó chính là lệnh dùng để phát hiện thiếu.
6. Tiếng Việt cho mọi chuỗi hiển thị; tiếng Anh cho tên hàm và biến.
7. Docstring giải thích **lý do**, không chép lại code.
8. Python ≥ 3.10, type hints, dataclasses.

Máy đích: **iMac 2017, Core i7 4 nhân, 32GB RAM, macOS Intel, không GPU.**
Mọi lựa chọn mặc định về tốc độ đều tính cho CPU.

---

## Cấu trúc thư mục

```
srtgen/
  cli.py                 typer; các lệnh run/resume/check/fix/names/doctor/ui
  pipeline.py            điều phối S0..S9, xử lý PipelineError / PipelineCancelled
  io_utils.py            NƠI DUY NHẤT gọi open(); BOM, newline, ghi nguyên tử
  config/default.yaml    NGUỒN SỰ THẬT của mọi tham số + profiles theo thể loại
  core/
    token.py             Token / Cue / Document + renderer (render_zh, render_py)
    srt.py               parse/emit SRT, tokenize_line, merge_zh_py
    rules.py             validator: RULES, VI_RULES, validate_document, validate_pair
    context.py           load_config, user_data_dir, user_cache_dir, định danh nguồn
    casing.py            viết hoa đầu câu, ca nhập nhằng sau "……"
    erhua.py             儿化音: 哪儿 -> nǎr, gộp ở tầng token
    sandhi.py            biến điệu 不/一/thanh 3
  stages/                s0_fetch … s9_emit, xem bảng trên
  providers/
    base.py              Protocol chung cho tầng AI
    gemini.py            gọi Gemini cho S7
    null.py              NullProvider: offline, tất định, dùng cho test
    translate.py         Translator Protocol + Gemini/GoogleFree/Null cho S8
  web/
    server.py            FastAPI, chỉ nghe 127.0.0.1
    jobs.py              hàng đợi việc, trạng thái, huỷ
    static/              giao diện 6 tab (tab cuối là Hướng dẫn)

tests/                   pytest; conftest.py có sẵn factory dựng Document
corpus/                  file thật đã audit: completed.srt (1351 cue), filter.srt, raw.srt
corpus/pairs/            cặp .srt + _vi.srt thật, dùng cho validate_pair
docs/                    ba tài liệu hợp đồng + plan
installer/               bộ cài macOS (uv), xem mục cuối
```

Thư mục lúc chạy, do `platformdirs` quyết định (macOS):

```
~/Library/Application Support/SrtGen/
  work/<video_id>/       artifact S0..S9, audio.wav, edit_draft.json
  output/                .srt, _vi.srt, _song-ngu.ass, .bundle.json, .report.html
  names/                 bảng tên riêng theo phim
~/Library/Caches/SrtGen/
  models/                model faster-whisper
```

---

## Hiệu chỉnh S4 (chia cue)

S4 là chặng duy nhất mà "đúng" được định nghĩa bằng số đo, không bằng cảm tính.
Các ngưỡng mặc định đo trực tiếp trên `corpus/completed.srt` — 1351 cue đã có người soát:

```yaml
cue:
  max_chars: 18         # max thực đo 17 Hán tự, p95 = 12
  max_duration: 6.0     # chỉ 8/1351 cue vượt 6 giây
  min_duration: 0.25    # min thực đo 0.24s; 175 cue ngắn hơn 0.8s -> KHÔNG gộp
  min_gap: 0.0          # 261 cue có gap đúng bằng 0, ép giãn ra là sai bản chuẩn
  silence_split: 0.30   # khoảng lặng dài hơn mức này thì được phép cắt
  break_after_end: "。？！"
  break_after_pause: "，、"
```

Thứ tự ưu tiên điểm cắt: **sau dấu kết câu → sau dấu ngắt → khoảng lặng → ép cắt.**

Phân bố mục tiêu khi chỉnh lại: thời lượng med 1.32s / p95 2.92s; Hán tự med 5 / p95 12;
số cụm mỗi cue med 3 / p95 8.

Quy trình chỉnh:

1. Sửa số trong `srtgen/config/default.yaml`, hoặc thêm `profiles.<tên>` nếu chỉ áp cho
   một thể loại. **Không hardcode ngưỡng trong code** — mọi giá trị đã có mặt ở YAML.
2. Chạy lại pipeline từ S4 trên một video đã có artifact:
   ```bash
   srtgen resume <video_id> --from s4
   ```
3. Đo lại phân bố và so với bảng trên. Lệch nhiều mà không giải thích được là hồi quy.
4. `python -m pytest tests -q` phải vẫn xanh.

Thể loại khác nhau cần bộ số khác nhau: `drama` (mặc định) cue ngắn và vụn,
`news` nới `max_chars` lên 24 và `min_duration` lên 0.60 cho đỡ vụn.

---

## Thêm một nhà cung cấp dịch

Tất cả nằm trong `srtgen/providers/translate.py`.

### 1. Hiện thực Protocol

```python
class Translator(Protocol):
    name: str
    needs_key: bool

    def translate_batch(
        self, items: list[dict], context: dict, *, target: str
    ) -> dict[int, str]: ...
```

- `items`: các cue phải dịch, **mỗi phần tử mang chỉ số cue**.
- `context`: 5 cue trước và 5 cue sau (chỉ để đọc), bảng thuật ngữ từ `names.json`,
  và chỉ dẫn văn phong.
- Trả về: `dict` ánh xạ **đúng những chỉ số đã nhận**. Không được thêm, không được bớt.

### 2. Bảo đảm cấu trúc — chỗ dễ sai nhất

Số cue không đổi là **bất khả thi về cấu trúc**, không phải thứ đi kiểm sau.
Lô nào trả thiếu/thừa phần tử thì gửi lại lô đó (`translate.retries`); vẫn hỏng thì
**giữ nguyên văn bản gốc cho đúng cue đó** và ghi một finding.
**Tuyệt đối không dồn hay xê dịch cue** — làm vậy là hỏng `VI_TIMESTAMP_DRIFT`
trên toàn bộ phần còn lại của file.

### 3. Đăng ký

Thêm tên vào `translate.provider` trong `config/default.yaml`
(`auto | gemini | google_free | null`) và vào hàm chọn provider.
`auto` = có API key thì Gemini, không có thì Google miễn phí.

### 4. Chống chặn tốc độ

Mọi lần gọi mạng dùng **retry backoff cấp số nhân**. Không nhảy sang key khác ngay
khi gặp 429 — đó là cách nhanh nhất để mất cả hai key.

### 5. Cache

Khoá cache là hash của `(zh_text, target_lang, model, glossary_hash)`, lưu ở
`user_cache_dir()`, **dùng chung mọi video**. Sửa prompt mà không đổi khoá là
sẽ đọc phải kết quả cũ.

### 6. Test

Thêm ca vào `tests/test_translate_offline.py`. Dùng `NullTranslator` để test tất định:
xác nhận `_vi.srt` khớp tuyệt đối số cue và timestamp với `.srt`, và lô trả thiếu
phần tử thì giữ nguyên văn gốc chứ không xê dịch cue.
**Không gọi mạng trong test.**

---

## Validator

`srtgen/core/rules.py` hiện thực checklist 12 mục của `docs/format-contract.md`.

```python
validate_document(doc) -> list[Finding]        # file tiếng Trung
validate_text(text)    -> list[Finding]        # đọc từ chuỗi, dùng cho lệnh check
validate_pair(zh, vi)  -> list[Finding]        # ràng buộc giữa hai file
```

Mã lỗi cặp file (thêm ở đợt 2):

| code | severity | luật |
| --- | --- | --- |
| `VI_BLOCK_COUNT` | error | số block file vi khác file zh |
| `VI_TIMESTAMP_DRIFT` | error | timestamp cue thứ i không khớp file zh |
| `VI_EMPTY_LINE` | warn | cue có dòng tiếng Việt rỗng |
| `VI_TRAILING_SPACE` | error | thừa khoảng trắng đầu/cuối dòng |
| `VI_MARKER_SYNC` | warn | vị trí marker `-` lệch so với file zh |

Dòng tiếng Việt dùng **dấu câu ASCII bình thường**. Không áp luật dấu câu tiếng Trung
của format-contract lên dòng đó.

---

## CLI

```bash
srtgen run <LINK|FILE> [--out DIR] [--model M] [--profile drama|news] [--no-ai] [--ass]
srtgen resume <video_id> [--from s5]
srtgen check <file.srt>            # chỉ soi, không sửa
srtgen fix <file.srt>              # chuẩn hoá file bên ngoài gửi sang
srtgen names <video_id>            # nhiệm vụ T1: lập bảng tên riêng bằng AI
srtgen doctor                      # kiểm máy: uv, ffmpeg, yt-dlp, thư viện, model, đĩa
srtgen ui                          # mở giao diện web tại 127.0.0.1
```

`srtgen doctor` là lệnh đầu tiên nên chạy khi có báo lỗi từ người dùng.
Mỗi mục chưa đạt đều **kèm sẵn dòng lệnh để xử lý** — quy ước này không được phá.

## Web API

Ngoài các endpoint chạy pipeline, trình sửa cần:

| method | path | việc |
| --- | --- | --- |
| GET | `/api/jobs/{id}/doc` | trả toàn bộ cue dạng JSON cho trình sửa |
| PUT | `/api/jobs/{id}/doc` | lưu bản đã sửa, trả findings mới |
| POST | `/api/retokenize` | `{zh}` → `{tokens, zh_line, py_line}` cho một câu |
| GET | `/api/jobs/{id}/audio` | phát wav, **bắt buộc hỗ trợ HTTP Range** |
| POST | `/api/open-local` | mở cặp `.srt`/`_vi.srt` có sẵn vào trình sửa |

Máy chủ chỉ nghe `127.0.0.1`. Không mở ra ngoài, không thêm CORS cho origin lạ.

---

## Bộ cài macOS

`installer/` — **không dùng Homebrew, không cần sudo, không cần Xcode**.

| việc | cách làm |
| --- | --- |
| trình quản lý | `uv`, tải bằng `curl -LsSf https://astral.sh/uv/install.sh \| sh`, đặt vào thư mục app |
| Python | `uv python install 3.12`, bản standalone, không đụng Python hệ thống |
| thư viện | `uv venv` + `uv pip install -r installer/requirements-macos.txt` |
| ffmpeg/ffprobe | bản tĩnh x86_64 từ evermeet.cx, giải nén vào thư mục app, `chmod +x` |
| yt-dlp | pip trong venv → nút "Cập nhật yt-dlp" chỉ là `uv pip install -U yt-dlp` |
| model | tải vào thư mục app, nối lại được khi đứt mạng |

Tất cả nằm trong `~/Library/Application Support/SrtGen/`. Gỡ = xoá một thư mục.

### BẮT BUỘC trước khi giao bản cho người dùng — bit quyền chạy

Repo này đang để `core.fileMode=false`, nên git **không giữ** bit `x` của các file
`.command`: `git add installer/CaiDat.command` cho ra mode `100644`. Người dùng
clone hoặc tải zip về sẽ bấm đúp mà không có gì xảy ra — hỏng ngay ở bước đầu tiên,
với đúng nhóm người không biết mở Terminal để tự chữa.

Vì vậy, **mỗi lần commit có đụng tới `installer/` hoặc `SrtGen.command`**, người phát hành phải chạy:

```bash
git update-index --chmod=+x SrtGen.command installer/*.command
git ls-files -s SrtGen.command installer/   # phải thấy mode 100755 ở cả năm file
```

Năm file phải là `100755`: `SrtGen.command`, `CaiDat.command`, `KhoiDong.command`,
`GoCaiDat.command`, `CAP-QUYEN.command`.

### `SrtGen.command` — cửa vào duy nhất của người dùng

Người dùng chỉ bấm đúng file này, cho cả lần cài, lần mở lại, lần cập nhật và lần sửa.
Nó chép phần chương trình vào `~/Library/Application Support/SrtGen/app` rồi mới gọi
`installer/CaiDat.command` **từ chỗ đó**. Lý do: bộ cài đăng ký SrtGen kiểu `--editable`,
nên chạy thẳng trong thư mục tải về thì người dùng dọn thư mục Tải về là app hỏng.
Bản tải về khác bản đang cài (so bằng dấu vân tay nội dung file, không so số phiên bản)
thì cập nhật; giống hệt và còn đủ thư viện thì mở app ngay.

Nếu chuyện này vẫn xảy ra (đóng gói bằng công cụ khác, gửi qua email, chép qua ổ
USB định dạng Windows), người dùng còn một phao cứu sinh: `installer/CAP-QUYEN.command`
gắn lại quyền chạy cho tất cả file `.command` cùng thư mục và gỡ nhãn quarantine.
Nó chạy được **cả khi chính nó cũng mất quyền chạy**, bằng cách gõ `bash ` trong
Terminal rồi kéo file thả vào — cách này được viết thành từng bước trong
`HUONG-DAN.md` mục 2. Đó là lý do phao này tồn tại; nó không thay cho lệnh
`git update-index` ở trên.

Vài chi tiết dễ vấp khi sửa bộ cài:

- **ffmpeg được nối vào `venv/bin/`.** `which_tool()` trong `s0_fetch.py` dò
  `sys.prefix/bin` trước tiên, nên đặt liên kết ở đó là cách duy nhất chắc chắn app
  tìm thấy ffmpeg khi được mở từ biểu tượng Desktop (lúc đó macOS chỉ đưa PATH tối thiểu).
- **Thư mục model** nằm trong thư mục app, còn `user_cache_dir()/models` là một
  symlink trỏ về đó. Máy cài từ bản cũ đã có model thật ở `~/Library/Caches` thì
  bộ cài giữ nguyên chỗ cũ và làm symlink ngược lại, không bắt tải lại 1.6GB.
- **`requirements-macos.txt` ghim cứng từng phiên bản** vì Intel Mac đang ở cuối vòng
  đời: vài gói đã ngừng phát hành bản biên dịch sẵn cho x86_64, để pip tự chọn
  "bản mới nhất" là nó quay ra biên dịch từ nguồn, cần Xcode, và hỏng.
  Ngoại lệ duy nhất là `yt-dlp` — ghim nó lại chính là hẹn giờ cho tool hỏng.
- File chia hai phần bởi mốc `@@CORE_END@@`. Phần nghe cài hỏng thì `CaiDat.command`
  cài lại riêng phần lõi, để người dùng vẫn kiểm tra và sửa được file `.srt`.
- `CaiDat.command` **idempotent** và **chạy tiếp từ bước hỏng**: mỗi bước ghi một dấu
  mốc ở `.tien-do/`, nhưng dấu mốc chỉ có giá trị khi kiểm tra lại vẫn thấy kết quả
  còn nguyên trên đĩa.

Sửa xong bất cứ file `.command` nào thì kiểm cú pháp trước khi giao:

```bash
bash -n installer/CaiDat.command
```

---

## Model gỡ băng

Mặc định `large-v3-turbo`. Số đo trên CPU int8, 5 phút audio: **19.6s** với
`large-v3-turbo` so với **52.6s** với `large-v3`; RAM đỉnh 1545MB so với 2953MB.

| Lựa chọn trong UI | model | video 40 phút trên iMac 2017 | tải về |
| --- | --- | --- | --- |
| Cân bằng (khuyên dùng) | `large-v3-turbo` | ~10-15 phút | ~1.6 GB |
| Chính xác nhất | `large-v3` | ~30-40 phút | ~3 GB |
| Nhanh, độ chính xác vừa | `medium` | ~8-12 phút | ~1.5 GB |
| Thử nhanh | `small` | ~4-6 phút | ~0.5 GB |

`large-v3-turbo` chỉ làm việc gỡ băng, không dịch. Không sao: việc dịch nằm ở S8 và
dùng model ngôn ngữ riêng, chất lượng cao hơn hẳn Whisper dịch thẳng.
