# Kế hoạch chi tiết — Tool YouTube → Whisper → SRT chuẩn

Mục tiêu: từ một link YouTube, chạy local trên macOS và Windows, cho ra file `.srt`
4 dòng đạt 100% hợp đồng định dạng trong README, không cần chỉnh tay ở tầng format.

---

## 0. Xác định lại mục tiêu chất lượng

Không đặt mục tiêu "giống hệt file completed". Whisper nghe từ audio nên tầng chữ
chắc chắn khác. Tách thành hai tiêu chí đo được riêng:

| Tầng | Tiêu chí | Ngưỡng |
| --- | --- | --- |
| Định dạng | Qua toàn bộ checklist README, 0 finding | **100%, cứng** |
| Cấu trúc cụm | Số cụm zh/py khớp tuyệt đối | **100%, do thiết kế** |
| Viết hoa | Đúng theo quy tắc ngữ cảnh | ≥ 97% |
| Nội dung chữ | CER so với transcript chuẩn | Đo, không chặn |
| Chia block | Độ trùng timestamp với bản chuẩn | Đo, không chặn |

Hai dòng cuối là chất lượng ASR, cải thiện bằng cách đổi model hoặc thêm prompt,
không phải bằng cách sửa tool chuẩn hoá. Đừng trộn hai nhóm chỉ số này.

**Lưu ý pháp lý:** tải nội dung từ YouTube có thể vi phạm điều khoản dịch vụ tuỳ nội dung
và mục đích sử dụng. Đây là việc bạn cân nhắc, tool chỉ nên hỗ trợ và không nên khuyến khích.

---

## 1. Quyết định kiến trúc cốt lõi

**Tách cụm một lần, sinh cả hai dòng từ cùng một token list.**

Quy trình hiện tại của bạn tách cụm Hán và sinh pinyin ở hai bước rời nhau, nên mới
có lỗi lệch số cụm. Thiết kế mới:

```
văn bản Hán  →  tokenize (1 lần)  →  [Token, Token, Token, ...]
                                          ↓                ↓
                                     render dòng Hán   render dòng pinyin
```

`Token` là cấu trúc chung:

```
Token {
    kind: word | punctuation | marker
    zh: str            # "秒钟"
    pinyin: str|None   # "miǎozhōng", None nếu punctuation/marker
    confidence: float
    source: str        # jieba | dict | ai | manual
}
```

Hệ quả: lệch số cụm trở thành **bất khả thi về mặt cấu trúc**, không phải thứ cần đi kiểm.
Toàn bộ 10 lỗi trong file completed biến mất. Đây là lý do quan trọng nhất để làm lại
từ đầu thay vì vá quy trình cũ.

Token list cũng chính là bundle mà backend cần — xuất thẳng ra JSON, không phải parse lại.

---

## 2. Pipeline 8 chặng

```
S0 fetch → S1 audio → S2 ASR → S3 cleanup → S4 cue-split
   → S5 tokenize+pinyin → S6 normalize → S7 AI assist → S8 validate+emit
```

Mỗi chặng ghi output ra `work/<video_id>/S<n>_*.json` để chạy lại từ giữa mà không
phải làm lại từ đầu. Chặng S2 tốn nhiều thời gian nhất nên khả năng resume là bắt buộc.

### S0 — Lấy nguồn

Công cụ: `yt-dlp` (gọi qua subprocess, không dùng thư viện Python vì bản CLI cập nhật nhanh hơn).

```
yt-dlp -f bestaudio --extract-audio --audio-format wav
       --postprocessor-args "-ar 16000 -ac 1" -o work/<id>/audio.wav <url>
```

16kHz mono là định dạng whisper cần, ép ngay từ đầu để khỏi convert lần hai.

Lưu `info.json` (tiêu đề, thời lượng, id) để đặt tên file đầu ra và làm khoá cache.

Xử lý lỗi cần có: video riêng tư, bị chặn theo vùng, `yt-dlp` quá cũ (lỗi phổ biến nhất,
gợi ý người dùng chạy `yt-dlp -U`). Hỗ trợ cả file local (`--input audio.mp3`)
để dùng lại pipeline khi đã có sẵn file.

### S1 — Tiền xử lý audio

- Tách giọng khỏi nhạc nền nếu cần: `demucs` (tuỳ chọn, nặng, chỉ bật khi phim nhiều nhạc).
- VAD cắt đoạn im lặng: dùng VAD tích hợp của `faster-whisper` (`vad_filter=True`).
  Đây là biện pháp chính chống ảo giác của whisper ở đoạn không có tiếng nói.
- Chuẩn hoá âm lượng bằng `ffmpeg loudnorm`.

### S2 — ASR

**Chọn engine:** `faster-whisper` (CTranslate2). Lý do: nhanh hơn whisper gốc 3-4 lần,
chạy CPU tốt với `compute_type=int8`, hỗ trợ word-level timestamp, cài bằng pip
trên cả hai hệ. `whisper.cpp` là phương án dự phòng cho máy yếu.

**Cấu hình đề xuất:**

| Tham số | Giá trị | Lý do |
| --- | --- | --- |
| `model` | `large-v3` | Tiếng Trung cần model lớn; `medium` chỉ dùng khi thử nhanh |
| `language` | `zh` | Ép cứng, không để auto-detect |
| `word_timestamps` | `True` | Bắt buộc, S4 cần |
| `vad_filter` | `True` | Chống ảo giác |
| `condition_on_previous_text` | `False` | Tránh lặp vô hạn khi gặp đoạn khó |
| `initial_prompt` | prompt tiếng Trung giản thể có dấu câu | Ép output đúng phong cách |
| `beam_size` | 5 | Cân bằng |

`initial_prompt` là đòn bẩy rẻ nhất để cải thiện chất lượng: viết một đoạn tiếng Trung
giản thể có dấu câu `。，？！`, kèm tên riêng của phim nếu biết trước.
Whisper sẽ bám theo phong cách và chính tả trong prompt.

**Phần cứng:** macOS Apple Silicon dùng CPU int8 (Metal chưa ổn định với CTranslate2);
Windows có NVIDIA dùng `device=cuda, compute_type=float16`. Tool tự dò và báo rõ
đang chạy chế độ nào cùng ước lượng thời gian.

### S3 — Dọn output ASR

Whisper cho tiếng Trung có bốn vấn đề cố định, xử lý theo thứ tự:

1. **Phồn thể lẫn giản thể** → `opencc` chuyển `t2s`, ép toàn bộ về giản thể.
2. **Lặp ảo giác** — cùng một câu lặp 5-10 lần ở đoạn nhạc. Phát hiện bằng cách so
   segment liền kề, nếu trùng trên 90% và lặp quá 2 lần thì cắt, ghi log.
3. **Segment rỗng hoặc chỉ có dấu câu** → bỏ.
4. **Dấu câu ASCII lẫn lộn** → để S6 xử lý, không làm ở đây.

Ghi số lượng từng loại vào report. Tỉ lệ ảo giác cao là dấu hiệu cần bật `demucs` ở S1.

### S4 — Chia cue

Đây là chặng quyết định file có "giống bản chuẩn" hay không, và cũng là chặng dễ bị
bỏ qua nhất. Whisper cho segment dài và chia tuỳ tiện; file của bạn có block ngắn
theo nhịp thoại.

Thuật toán: gom word-level timestamp thành cue theo thứ tự ưu tiên điểm cắt:

1. Sau dấu kết câu `。？！`
2. Sau dấu ngắt `，、`
3. Tại khoảng lặng dài hơn ngưỡng (mặc định 300ms)
4. Ép cắt khi vượt giới hạn

Ràng buộc cấu hình được:

| Tham số | Mặc định | Ghi chú |
| --- | --- | --- |
| `max_chars` | 20 | Số Hán tự tối đa mỗi cue |
| `max_duration` | 6.0s | |
| `min_duration` | 0.8s | Cue ngắn hơn thì gộp với cue kề |
| `min_gap` | 0.08s | Khoảng cách tối thiểu giữa hai cue |

Cách hiệu chỉnh: chạy trên video đã có file completed, so phân bố độ dài cue
và độ dài ký tự với bản chuẩn, chỉnh tham số cho khớp. Làm một lần, dùng cho mọi video
cùng thể loại.

**Marker đổi người nói:** whisper không cho thông tin này. Hai lựa chọn — bỏ qua
(mặc định, chấp nhận mất tính năng), hoặc thêm `pyannote.audio` diarization
(cần token HuggingFace, nặng, chính xác trung bình với phim có nhạc nền).
Đề xuất bỏ qua ở v1 và ghi rõ trong tài liệu.

### S5 — Tokenize và sinh pinyin

Chặng lõi. Đầu vào là chuỗi Hán mỗi cue, đầu ra là `Token[]`.

**Bước 1 — tách dấu câu** thành token `punctuation` riêng trước khi làm gì khác.

**Bước 2 — tách từ** bằng `jieba`. Kiểm chứng trên dữ liệu của bạn: jieba cắt
`我来中国只有一个目的` thành `我/来/中国/只有/一个/目的`, trùng khớp với cách người
kiểm duyệt đã chọn trong file completed. Nạp thêm từ điển riêng qua `jieba.load_userdict()`
chứa tên riêng và thuật ngữ của phim.

**Bước 3 — gộp 儿化音.** Nếu token là `儿` đơn lẻ và token trước là danh từ/đại từ,
gộp vào token trước. Đây chính là lỗi `#729` trong file completed
(`远点儿` bị tách thành `yuǎn diǎnér`), nên phải xử lý ở tầng tokenizer chứ không phải đi sửa sau.

**Bước 4 — sinh pinyin** bằng `pypinyin.lazy_pinyin(token, style=Style.TONE)`,
gọi **theo từng token** chứ không theo cả câu, để pypinyin dùng đúng ngữ cảnh từ.
Với 儿化音, ghép thành dạng rút gọn (`diǎnr`) theo bảng quy tắc riêng.

**Bước 5 — biến điệu.** Cấu hình được vì đây là quy ước nội bộ, không có chuẩn tuyệt đối:

```yaml
sandhi:
  bu:  true      # 不 → bú trước thanh 4
  yi:  false     # 一 → giữ yī (khớp với file completed hiện tại)
  third_tone: false
```

File completed của bạn dùng `búyào` nhưng `yītiáo`, tức là bật `bu` và tắt `yi`.
Mặc định đặt theo đó.

**Bước 6 — đánh dấu độ tin cậy.** Token chứa đa âm tự (`得 着 了 长 行 还 差 数 重`)
đánh `confidence` thấp và ghi vào hàng đợi cho S7.

### S6 — Chuẩn hoá

Áp dụng A1–A6 đã phân tích, theo đúng thứ tự:

1. Bảo vệ marker `-` bằng sentinel (nếu có từ S4)
2. Đổi dấu gạch ngắt lời còn lại → `——`
3. `…`/`...` → `……`
4. Dấu ASCII → dấu tiếng Trung, theo dõi trạng thái cho cặp ngoặc
5. Khoảng trắng: quanh dấu câu, kép, đầu/cuối dòng
6. **Viết hoa đầu câu** — viết hoa nếu cue trước kết thúc bằng `。？！”`, giữ thường
   nếu kết thúc bằng `，、……`. Đúng 97.6% trên dữ liệu của bạn. Trường hợp sau `……`
   đánh `LOW_CONFIDENCE` cho S7.
7. **Viết hoa tên riêng** theo `names.json`
8. Trả sentinel về `-`, ép đúng 1 khoảng trắng hai bên

Vì hai dòng render từ cùng token list, mọi thao tác về dấu câu và khoảng trắng
làm trên token rồi render, không dùng regex trên chuỗi đã ghép. Điều này loại bỏ
toàn bộ nhóm lỗi đồng bộ hai dòng.

### S7 — Tầng AI (Gemini / AI Studio)

Kiến trúc proposer–verifier: AI đề xuất, rule engine kiểm lại, người duyệt. AI không ghi file.

**Bốn nhiệm vụ, xếp theo giá trị:**

| # | Nhiệm vụ | Khi nào gọi | Số lần gọi/video |
| --- | --- | --- | --- |
| T1 | Phát hiện tên riêng toàn phim | 1 lần, sau S5 | 1-2 |
| T2 | Giải đa âm tự | Token confidence thấp | 3-6 |
| T3 | Viết hoa nhập nhằng sau `……` | ~33 cue/file | 1-2 |
| T4 | Dấu câu cuối câu thiếu | Cue không có dấu kết | 1-2 |

**T1 là nhiệm vụ đáng giá nhất và nên chạy đầu tiên.** Gửi toàn bộ text đã transcribe
(hoặc 200 cue đầu), yêu cầu trả danh sách tên nhân vật/địa danh kèm pinyin viết hoa
và cách tách. Kết quả ghi vào `names.json` của phim, dùng cho A6 và nạp ngược vào
`jieba.load_userdict()` rồi **chạy lại S5**. Vòng lặp hai lượt này cải thiện cả tách từ
lẫn viết hoa cùng lúc.

**Nguyên tắc gọi API:**

- `temperature = 0`, dùng `responseSchema` để ép JSON, không parse văn xuôi.
- **Bắt model chọn giữa các phương án có sẵn**, không để nó tự sinh. Với T2, gửi kèm
  các âm khả dĩ từ `pypinyin(heteronym=True)` và bảo nó chọn một, kèm lý do ngắn.
  Bài toán chọn dễ hơn bài toán sinh nhiều lần.
- Gộp 20-30 mục mỗi request kèm cue lân cận làm ngữ cảnh.
- Cache theo hash `(zh_line, task_type)`, lưu ở thư mục `platformdirs`, dùng chung mọi video.
- Model: `gemini-2.5-flash` cho T2-T4, `gemini-2.5-pro` cho T1 (chỉ 1-2 lần gọi nên
  chênh lệch chi phí không đáng kể, mà chất lượng nhận diện tên riêng quan trọng).
  Tên model thay đổi theo thời gian, để trong config chứ không hardcode.

Ước lượng: **8-12 lần gọi cho một video 40 phút**, giảm còn 3-5 sau khi cache ấm.
Nằm gọn trong hạn mức miễn phí một tài khoản. Nếu vẫn muốn nhiều key thì viết `KeyPool`
có trạng thái từng key, gặp 429 thì backoff cấp số nhân chứ không nhảy key ngay.

**Bắt buộc có `NullProvider`** để chạy toàn pipeline offline. Tất cả test tất định
chạy với provider này.

### S8 — Kiểm và xuất

Chạy toàn bộ validator của README trên kết quả. Vì token list là nguồn chung nên
các rule về số cụm và đồng bộ hai dòng chỉ còn là assert phòng thủ — nếu chúng fail
thì có bug ở renderer.

Xuất ba file:

- `<title>.srt` — UTF-8 có BOM, newline theo cấu hình (mặc định LF), khớp định dạng file hiện tại của bạn
- `<title>.bundle.json` — token list cho backend
- `<title>.report.html` — thống kê, danh sách finding, các mục AI đề xuất chưa duyệt

---

## 3. Cấu trúc dự án

```
srtgen/
├── cli.py              # entry point
├── io_utils.py         # DUY NHẤT được gọi open(); encoding, newline, NFC
├── stages/
│   ├── s0_fetch.py     ├── s4_cue.py
│   ├── s1_audio.py     ├── s5_tokenize.py
│   ├── s2_asr.py       ├── s6_normalize.py
│   ├── s3_cleanup.py   ├── s7_ai.py
│   └── s8_emit.py
├── core/
│   ├── token.py        # cấu trúc Token, renderer
│   ├── rules.py        # validator theo README
│   └── sandhi.py
├── providers/
│   ├── base.py  gemini.py  null.py
├── config/
│   ├── default.yaml    names/<movie>.json    exceptions.json
└── tests/
```

**CLI:**

```
srtgen run <url|file> [--out DIR] [--model large-v3] [--no-ai] [--profile drama]
srtgen resume <video_id> --from s5      # chạy lại từ chặng bất kỳ
srtgen check <file.srt>                 # chỉ kiểm, không sửa
srtgen fix <file.srt>                   # chuẩn hoá file có sẵn
srtgen names <video_id>                 # chạy riêng T1
srtgen doctor                           # kiểm ffmpeg, yt-dlp, model, GPU, API key
```

`srtgen doctor` quan trọng hơn vẻ ngoài của nó — phần lớn báo lỗi từ người dùng sẽ là
thiếu ffmpeg hoặc yt-dlp cũ, và lệnh này trả lời được ngay mà không cần bạn hỗ trợ.

Lệnh `fix` giữ nguyên giá trị của tool cũ: chuẩn hoá file `.srt` bên dịch gửi sang,
không liên quan đến whisper.

---

## 4. Chạy local trên hai hệ

**Phụ thuộc ngoài:** `ffmpeg` và `yt-dlp`. Không bundle, mà kiểm tra khi khởi động
và hướng dẫn cài (`brew install ffmpeg` / `winget install ffmpeg`).

**Model whisper:** tải lần đầu về thư mục cache của `platformdirs`, `large-v3` khoảng 3GB.
Hiện thanh tiến trình, cho phép chỉ định đường dẫn có sẵn.

**Bốn cái bẫy nền tảng** đã nêu ở kế hoạch trước vẫn áp dụng nguyên vẹn:
encoding tường minh UTF-8, xử lý CRLF/LF, `sys.stdout.reconfigure` cho console Windows,
chuẩn hoá NFC cho tên file. Thêm một điểm mới: đường dẫn có dấu tiếng Việt hoặc
khoảng trắng khi truyền vào subprocess `ffmpeg`/`yt-dlp` — luôn truyền dạng list,
không nối chuỗi shell.

**Đóng gói:** ưu tiên `pipx install` từ Git. PyInstaller khó vì `faster-whisper` kéo theo
CTranslate2 nhị phân nặng; nếu vẫn cần thì build riêng trên từng hệ qua GitHub Actions.

---

## 5. Cổng chất lượng

Tái sử dụng kế hoạch kiểm thử trước, thêm ba tầng đặc thù:

**Tầng 1 — bất biến của renderer.** Số token = số cụm ở cả hai dòng, luôn đúng.
Chạy trên mọi output. Fail nghĩa là renderer có bug.

**Tầng 2 — regression trên corpus có sẵn.** Chạy `srtgen fix` trên file `filter.srt`,
so với `completed.srt` đã audit. Đây là bộ đo trực tiếp chất lượng S5+S6, không dính ASR.
Chỉ số theo kế hoạch cũ: false positive = 0, độ chính xác viết hoa ≥ 97%.

**Tầng 3 — end-to-end trên video đã biết đáp án.** Lấy chính video Long quyền tiểu tử,
chạy full pipeline, so với `completed.srt`:

| Chỉ số | Cách đo | Mục đích |
| --- | --- | --- |
| CER | ký tự Hán, sau khi bỏ khoảng trắng | Chất lượng ASR |
| Cue IoU | độ trùng timestamp | Chất lượng S4 |
| Format pass | checklist README | **Phải 100%** |

Ba chỉ số này đo ba thứ khác nhau và phải báo cáo riêng. CER kém thì đổi model
hoặc sửa `initial_prompt`; cue IoU kém thì chỉnh tham số S4; format pass không bao giờ
được dưới 100% vì đó là phần tool tự kiểm soát hoàn toàn.

---

## 6. Lộ trình

| GĐ | Nội dung | Kết quả |
| --- | --- | --- |
| 1 | `io_utils`, `Token`, renderer, `rules.py` | Nền tảng, test bất biến chạy được |
| 2 | S5 + S6 + lệnh `fix` | **Dùng được ngay** cho file bên dịch gửi |
| 3 | Corpus test tầng 2, audit 10 block trong completed | Có số đo chất lượng |
| 4 | S0-S3, lệnh `run` không có S4 tinh chỉnh | Chạy end-to-end thô |
| 5 | S4 hiệu chỉnh theo bản chuẩn | Chia block giống bản chuẩn |
| 6 | S7 với T1 (tên riêng) | Cải thiện lớn nhất từ AI |
| 7 | S7 T2-T4 + cache + KeyPool | Hoàn thiện |
| 8 | Report HTML, `doctor`, CI hai nền tảng | Bàn giao |

Giai đoạn 2 là mốc quan trọng: xong nó bạn đã có tool thay thế toàn bộ chặng
`filter → completed` thủ công, độc lập với phần whisper. Nếu phần ASR gặp khó khăn
kỹ thuật thì giá trị đó vẫn còn nguyên.

Giai đoạn 6 nên làm trước 7 vì T1 chạy một lần mỗi phim, cải thiện đồng thời
tách từ và viết hoa, mà chi phí gần như bằng không.

---

## 7. Rủi ro đã lường trước

**Chất lượng ASR tiếng Trung với phim có nhạc nền** là rủi ro lớn nhất và nằm ngoài
tầm kiểm soát của tool. Giảm thiểu bằng `initial_prompt` có tên riêng, `vad_filter`,
và `demucs` khi cần. Đo bằng CER trước khi đầu tư thêm vào các chặng sau.

**Marker đổi người nói không tái tạo được** nếu không có diarization. Chấp nhận ở v1.

**jieba tách sai với thoại khẩu ngữ và tên riêng** — giải bằng `userdict` sinh từ T1,
và cho phép người thêm từ vào `names.json` thủ công.

**Biến điệu là quy ước, không phải chân lý.** Đặt trong config, hiệu chỉnh theo file
completed hiện có, đừng cứng hoá trong code.

**Model whisper 3GB và thời gian chạy** — video 40 phút trên CPU có thể mất 20-40 phút.
Phải có thanh tiến trình và khả năng resume, nếu không người dùng sẽ tưởng tool treo.
