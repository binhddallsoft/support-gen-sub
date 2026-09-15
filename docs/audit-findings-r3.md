# Vấn đề còn mở sau vòng soát lại (đợt 3)

28 mục. Sắp theo mức độ.


## nặng

### `srtgen/web/server.py (create_job ~dòng 3465) + srtgen/web/static/app.js (uploadAndCreate ~dòng 850)`

Chọn file hoặc kéo thả video rồi bấm Bắt đầu thì chết. Client gửi multipart/form-data (file, options là chuỗi JSON), còn create_job chỉ đọc _json_body, nên nhận {} và trả 400 'Bạn chưa dán link YouTube hoặc chọn file âm thanh.'; jobs.submit không bao giờ được gọi (đã gọi thật bằng testclient). Chưa sửa vì cần chốt thiết kế: lưu file tải lên ở đâu, dọn khi nào, giới hạn dung lượng (MAX_UPLOAD_BYTES 20MB là cho file .srt), và đặt tên file an toàn. python-multipart đã có trong requirements-macos.txt.


## vừa

### `srtgen/pipeline.py:798-830`

Docstring của run_fix nói giữ nguyên ranh giới cụm và chỉ sinh lại pinyin cho cụm thiếu, nhưng run_fix không đặt meta['segmented'] (parse_srt_document ở srt.py:422 cũng không đặt). Vì vậy tokenize_document chạy nhánh segmented=False: jieba cắt lại 16/1351 cue của filter.srt so với file gốc (ví dụ 接住->接 住, 哪种->哪 种, 翻到->翻 到), và fill_pinyin(overwrite=True) bỏ toàn bộ pinyin người biên tập đã ghi. Cổng vẫn 0 lỗi, và bản sửa shéi hiện hiệu lực trên CLI chính NHỜ việc ghi đè này. Nếu sau này sửa run_fix cho đúng docstring, các chữ 'shuí' mà người biên tập viết trong filter.srt sẽ được giữ nguyên. Lỗi có từ trước đợt sửa (mtime của pipeline.py là 13:28, sớm hơn audit-findings.md lúc 15:28). Chưa sửa vì đây là đổi hành vi CLI, cần chủ dự án chốt nên theo docstring hay theo code.

### `srtgen/web/static/app.js:3707 (restoreDraft) + checkDraft`

Ca G: sau F5, người dùng chưa quyết thanh báo mà gõ tiếp một dòng. Tick 5 giây ghi đè nháp cũ trên đĩa (GET draft trả cue1 'Chào thế giới.', cue2 'SỬA MỚI SAU F5') nhưng thanh báo vẫn hiện. Bấm 'Khôi phục' thì bảng thành ["NHÁP CŨ", "Tôi đến Trung Quốc chỉ có một mục đích."], hoàn tác còn 0 bước: phần vừa gõ mất hẳn, không lấy lại được. Chưa sửa vì là chuyện thiết kế: ẩn thanh báo khi bắt đầu sửa, hỏi xác nhận khi editor.dirty, hay đưa việc khôi phục vào ngăn hoàn tác.

### `srtgen/web/server.py (/api/fix)`

Lỗi phần mềm, ngoài trọng tâm tài liệu: /api/fix gặp block có 3 dòng chữ (Hán/pinyin/Việt) thì âm thầm vứt dòng thứ ba và báo 0 lỗi. Đã tái hiện bằng TestClient. Tài liệu đã cảnh báo người dùng, còn code thì chưa sửa.

### `srtgen/stages/s7_ai.py:1724-1744 + server.py list_name_tables`

Lỗ hổng thiết kế, ngoài trọng tâm tài liệu: không có AI kèm khoá thì không bao giờ có names.json, mà ô chọn Phim chỉ liệt kê thư mục có names.json. Người dùng mặc định vì thế không thể tự lập bảng tên. Cần quyết thiết kế, nên chỉ báo.


## nhẹ

### `srtgen/core/srt.py (ASCII_PUNCT_MAP) vs srtgen/core/rules.py:223`

Mục ASCII_OTHER mới sửa được một nửa. Validator nay báo ASCII_PUNCT cho ( ) " < > và miễn trừ thẻ <i> đúng như mong đợi: '<i>他 说 好。</i>' ra []. Nhưng run_fix trên chính khối đó vẫn cho '《i》他 说 好。《/ i》' và '《I》Tā shuō hǎo。《/ i》', và validate_text trên kết quả này cũng ra [] (không bắt được). Như vậy lệnh check và lệnh fix vẫn nói ngược nhau về thẻ nghiêng. Corpus không có <i> nên cổng không bị ảnh hưởng. Chưa sửa vì nằm ở đường chuẩn hoá của srt.py, ngoài phạm vi trọng tâm.

### `srtgen/config/default.yaml:207`

Mục exact 地='de' chỉ áp khi cụm đứng riêng. Với chữ Hán liền, jieba có thể gộp 地 với chữ sau: run_fix trên raw '我认真地看地图。' ra '我 认真 地看 地图。' và 'Wǒ rènzhēn dìkàn dìtú。'. Đây là giới hạn đã được ghi rõ trong thiết kế, không phải hỏng do đợt sửa. Trên corpus, cả 6 chỗ 地 đọc 'de' vẫn ra đúng, và số lỗi cổng không đổi. Chỉ ghi lại, không sửa.

### `srtgen/web/server.py:4082 (_hostname) + :141 ALLOWED_HOSTS`

Phép kiểm Origin chỉ so tên host và bỏ qua cổng, nên một trang web do ứng dụng khác phục vụ trên 127.0.0.1/localhost ở cổng khác vẫn gửi POST /api/actions/* được. Kết luận này rút từ đọc code (_hostname cắt bỏ phần ':port'), tôi chưa chạy thử. Muốn khai thác thì kẻ tấn công phải đã có một dịch vụ web chạy trên máy nạn nhân. Không sửa vì phải biết cổng thật trong middleware, tức là đổi thiết kế.

### `srtgen/web/server.py:3903 (route /api/actions/{key})`

Khoá rỗng hoặc có dấu '/' (ví dụ '../', 'install_ffmpeg; rm -rf /') bị trả 405 'Bản chương trình đang chạy chưa làm được việc này.' chứ không phải 400, vì router không khớp {key}. Handler và runner không hề được gọi (syscalls: []), nên không có ảnh hưởng an toàn; chỉ là mã trạng thái khác với 400 mà đề bài chờ đợi.

### `srtgen/stages/s0_fetch.py:290-292`

Câu báo thiếu ffmpeg bảo người dùng chạy 'brew install ffmpeg', trái với chủ trương không dùng Homebrew của bộ cài và của HUONG-DAN (tài liệu bảo chạy lại CaiDat.command). Nằm ngoài trọng tâm, chưa sửa.

### `srtgen/web/static/app.js (FIX_ACTIONS) + srtgen/web/jobs.py:1037,1132`

jobs.py phát fix_action 'restart_job' và 'reinstall' nhưng FIX_ACTIONS không có hai khoá này. Màn hình lỗi vì thế chỉ có nút 'Thử lại từ đầu', không có nút đích danh; câu thông báo vẫn hiện bình thường. Chưa sửa.

### `srtgen/stages/s5_tokenize.py (install_userdict)`

Ngoài phạm vi giao, chỉ ghi nhận: tên có cụm một chữ ('光头强' = [光头, 强]) không ép jieba tách đúng, vì cụm dài dưới 2 ký tự được bỏ qua ở suggest_freq. '光头强来了' vẫn bị cắt thành ['光头','强来'], kể cả với names.json do S7 ghi. Chưa sửa.


## ghi-chú-agent-sửa

### `(báo bởi agent sửa)`

Danh sách chữ CÒN LỆCH sau khi có bảng (17/5242 cụm) — cố ý KHÔNG đưa vào bảng vì cách đọc phụ thuộc ngữ cảnh thật, ép một cách đọc là làm sai 1/3 số ca: 得 (corpus děi 6 lần / dé 3 lần), 着 (zhe 11 / zhuó 1), 要 (yào 34 / yāo 1). Chúng đã nằm trong hàng đợi HETERONYM để người hoặc AI T2 chọn theo từng câu. Còn 不 (bù 30 / bú 15) và 一 (yī 8 / yì 1) thuộc mục `sandhi` của config, không phải bảng đọc. Mấy ca cụm dài (对不对, 好好, 不着, 管得着, 不一样) là hệ quả của cùng hai nhóm trên.

### `(báo bởi agent sửa)`

s7_ai.py — hai nửa còn lại của mục 'tên tiếng Việt' nằm NGOÀI file được giao, phải báo chủ dự án: (1) `srtgen/web/server.py:636` gọi `save_names(..., entries=[])`, tức mỗi lần lưu từ tab 'Tên riêng' là xoá sạch `entries` — nay xoá luôn cả trường `vi` vừa được thêm; (2) tab đó là bảng 'chữ Hán -> pinyin' nên chuỗi tiếng Việt gõ vào bị `strip_tones` cắt dấu ('Cường đầu trọc' -> 'Cuong Đau Troc'). Cần một ô nhập 'Tên tiếng Việt' riêng và server phải ghi nó vào `entries[].vi`. Phía thư viện đã sẵn sàng: NameEntry có trường vi, load_names đọc được cả dạng lồng viết tay, _merge_names không xoá nữa.

### `(báo bởi agent sửa)`

rules.py — `srtgen/core/srt.py` (không thuộc file được giao) vẫn biến thẻ nghiêng `<i>他 说</i>` thành `《i》他 说《/ i》` khi chạy `run_fix`. Validator nay đã im lặng đúng chỗ (markup không phải dấu câu), nhưng bản thân `fix` vẫn phá thẻ. Cần chủ file `srt.py` cho `ASCII_PUNCT_MAP`/`smart_quotes` bỏ qua đoạn khớp thẻ SRT — có thể dùng lại đúng regex `_MARKUP_RE` vừa thêm trong rules.py.

### `(báo bởi agent sửa)`

rules.py — `srtgen/web/static/app.js:192` giữ một bản sao nhãn tiếng Việt của mã lỗi ('Tên riêng lúc gộp lúc tách'). Nhãn phía Python đã đổi thành 'Cùng một cụm từ lúc gộp lúc tách'; chủ file app.js nên đồng bộ, hoặc tốt hơn là lấy nhãn từ API thay vì chép cứng.

### `(báo bởi agent sửa)`

s8_translate.py — chưa đụng tới `srtgen/providers/translate.py` (không thuộc file được giao). Câu tiêu đề khối glossary trong prompt ('these names have a fixed {target} spelling') nay đã ĐÚNG vì bảng chỉ còn tên có bản tiếng Việt thật, nên không cần sửa gấp. Nếu sau này sửa `_PROMPT` thì nhớ tăng `PROMPT_VERSION` trong s8_translate.py.

### `(báo bởi agent sửa)`

docs/audit-findings.md và docs/build-spec.md chưa cập nhật (không thuộc file được giao). Sau đợt này nên đánh dấu đã xử lý: mục s5_tokenize (谁/shuí), 3 mục s8_translate, mục s7_ai (NameEntry.vi, phần thư viện), và 4 mục rules.py.

### `(báo bởi agent sửa)`

Không có mục nào thuộc phần giao diện bị bỏ lại. Hai lưu ý phụ thuộc phía máy chủ (giao diện đã chịu được cả hai chiều): (1) câu cảnh báo 'file đã được ghi lại sau lúc bạn sửa dở' dựa vào base_hash mà chính giao diện gửi kèm lúc PUT nháp, nên chỉ đúng khi máy chủ trả lại nguyên gói nháp ở trường doc của GET /api/jobs/{id}/draft — nếu máy chủ chỉ giữ cues/rev thì giao diện đọc srt_mtime/file_mtime/mtime nếu có, không có thì im lặng chứ không bịa cảnh báo; (2) khi tải model, giao diện gửi kèm {model: <id đang chọn>} trong body POST /api/actions/download_model — máy chủ bỏ qua cũng không sao vì hợp đồng nói tải model 'đang chọn'.

### `(báo bởi agent sửa)`

Chỉ đụng 3 file được giao (app.js, index.html, style.css). Không sửa server.py, rules.py, s7/s8, installer — kể cả những mục audit-findings nằm ở đó.

### `(báo bởi agent sửa)`

Trong lúc tôi làm, agent khác đã đổi bảng tab 'Tên riêng' trong app.js (từ 5 cột Chữ Hán/Cách tách/Pinyin/Loại/Số lần sang 3 cột Chữ Hán/Pinyin/Tên tiếng Việt) và thêm dải khôi phục bản nháp. Tôi đã đọc lại và viết hướng dẫn theo bản HIỆN TẠI, kiểm lại lần cuối lúc 306 test xanh. Nếu các agent đó còn đổi tên nút sau thời điểm này thì phải chạy lại phép đối chiếu 74 tên.

### `(báo bởi agent sửa)`

Nút 'Kiểm tra key' và 'Cập nhật yt-dlp' được nhắc trong HUONG-DAN.md vì chúng CÓ THẬT trong giao diện, nhưng route máy chủ (POST /api/settings/test-key, POST /api/actions/{key}) là phần của agent khác. Tôi không đụng vào srtgen/web/.

### `(báo bởi agent sửa)`

Ghi chú 'người phát hành phải chạy git update-index --chmod=+x installer/*.command' mới chỉ là tài liệu trong README.md — tôi chưa chạy lệnh đó vì installer/ hiện chưa được git track (nhánh master chưa có commit nào). Người phát hành phải 'git add installer/' trước rồi mới chạy được lệnh chmod, và phải kiểm bằng 'git ls-files -s installer/' thấy 100755 ở cả bốn file .command.

### `(báo bởi agent sửa)`

Không sửa (ngoài file được giao): app.js hiện ghi hợp đồng POST /api/open-local <- {name, content, vi_content} nhưng server vẫn chỉ nhận {path}; mục này không có trong danh sách A–J nên chưa làm — nếu giao diện gửi content thì nút 'Mở file có sẵn' sẽ báo lỗi.

### `(báo bởi agent sửa)`

Không sửa (của agent giao diện): tab Tên riêng trong app.js vẫn gửi dạng cũ {han, split, pinyin[]} và đọc data.items; server đã nhận cả hai dạng và trả cả movies lẫn items nên vẫn chạy, nhưng nên chuyển hẳn sang {zh,pinyin,vi}/movies. restoreJob() trong app.js nên bỏ qua job có mode==='action', nếu không tải lại trang giữa lúc cài ffmpeg sẽ thấy màn hình 'Bước 1/10'.

### `(báo bởi agent sửa)`

Không sửa (ngoài file được giao): pyproject.toml [tool.setuptools] packages thiếu 'srtgen.web', nên bản cài bằng pip sẽ không có giao diện web.

### `(báo bởi agent sửa)`

Chưa kiểm chạy thật (cần mạng/máy Mac): tải ffmpeg từ evermeet.cx (trên Windows nút này cố ý từ chối bằng câu tiếng Việt), pip install -U yt-dlp, tải model faster-whisper, và gọi Gemini thật cho test-key. Logic giải nén, danh sách trắng, luồng job/SSE và các ca lỗi đã có test ngoại tuyến.

### `(báo bởi agent sửa)`

Tác dụng phụ lúc thử tay (không phải trong bộ test): một lần gọi install_ytdlp đã chạy thật 'python -m pip install -U yt-dlp' trong môi trường Python của máy (yt-dlp hiện là 2026.3.17); đã cài pyflakes để soát lỗi; open_output_dir/open_guide đã mở cửa sổ Explorer một lần. Bộ test mới thay mọi việc chạy lâu bằng hàm giả nên không gọi pip hay tải file.
