# Vấn đề còn lại sau vòng soát xét đợt 2

Do 6 người soát độc lập tìm ra, mỗi mục đều có bằng chứng chạy thật.
Tổng 27 mục chưa sửa. Sắp theo mức độ.


## Mức nặng

### `D:\Binh\SrtGen\srtgen\web\server.py (thiếu route) + app.js:1335`

POST /api/actions/{key} không tồn tại -> 5 nút hành động khi gặp lỗi ("Cài ffmpeg giúp tôi", "Cập nhật yt-dlp giúp tôi", "Tải model gỡ băng giúp tôi"…), nút sửa trong kết quả Kiểm tra máy và nút "Cập nhật yt-dlp" ở tab Cài đặt đều chết. build-spec mục 10 bắt buộc phải có nút hành động này. Không sửa vì phải thiết kế logic chạy trình cài đặt.

### `D:\Binh\SrtGen\srtgen\web\server.py:2456`

PUT /api/names/{video_id} lệch hợp đồng với app.js:1896: client gửi {entries:[...]}, server lấy nguyên body làm bảng "chữ Hán -> pinyin". Kết quả thật: names.json = {"names":{"entries":"[{'zh': '张三', ...}]"}} — tên người dùng vừa gõ bị vứt sạch, file bảng tên bị bơm khoá rác, mà giao diện vẫn toast "Đã lưu N tên". Mất dữ liệu trong im lặng.

### `D:\Binh\SrtGen\srtgen\web\static\app.js`

Tự lưu nháp chỉ GHI, không bao giờ ĐỌC LẠI. app.js chỉ gọi PUT /api/jobs/{id}/draft (saveDraft, dòng 3106-3120; setInterval dòng 3320); không có một chỗ nào gọi GET /api/jobs/{id}/draft (grep 'draft' toàn file: chỉ có PUT). Máy chủ đã làm đủ cả hai chiều (server.py:2267 get_draft, 2277 put_draft, 1382 read_draft). Kiểm trên trình duyệt thật: sửa cue 3, chờ 5 giây -> work/<video_id>/edit_draft.json có đúng nội dung đang sửa ({"zh_line": "- 你 是 谁？你 是 谁？", rev 5}); F5 rồi bấm 'Mở lại kết quả gần nhất' -> bảng hiện lại nội dung file trên đĩa ('- 你 是 谁？'), không có một lời nhắc nào về bản nháp đang nằm trên đĩa. Trong khi index.html:368 hứa nguyên văn với người dùng: 'Mọi thay đổi được tự lưu nháp mỗi 5 giây, đóng nhầm tab cũng không mất công.' Chưa sửa vì phải quyết thiết kế (hỏi khôi phục hay tự khôi phục, xử lý sao khi file trên đĩa đã đổi sau lúc ghi nháp) - đúng loại 'cần đổi thiết kế' nên chỉ báo.


## Mức vừa

### `D:\Binh\SrtGen\srtgen\stages\s8_translate.py`

build_glossary mặc định sinh pinyin bỏ dấu (光头强 = Guangtouqiang, 熊二 = Xionger, 李老板 = Li Laoban) rồi _glossary_block trình nó cho model là "fixed tiếng Việt spelling ... never invent a different one". File _vi.srt thật của người dùng viết "Cường đầu trọc", "Gấu Ú", "sếp Lý". Phần prompt có đòn bẩy lớn nhất đang ép model đi ngược văn phong đích, và đặc tả build-spec-v2 mục 3 đòi glossary mang bản tiếng Việt cố định chứ không phải pinyin. Cần quyết định thiết kế (chỉ đưa mục có vi, hoặc thêm bảng Hán-Việt) nên không tự sửa.

### `D:\Binh\SrtGen\srtgen\stages\s7_ai.py`

Kênh duy nhất cho tên tiếng Việt đúng (entries[].vi mà _explicit_vietnamese đọc) không dùng được thật: NameEntry không có trường vi, to_dict/from_dict đều rụng nó. Chạy thử: sửa tay names.json thêm "vi":"Cường đầu trọc" -> glossary đúng, manual=1; sau đúng một lần S7 chạy lại qua _merge_names + save_names -> quay về Guangtouqiang, manual=0. Ngoài ra web write_names gọi save_names(..., entries=[]) (srtgen/web/server.py:636) xoá sạch entries, và tab đó là bảng Hán->pinyin nên tiếng Việt gõ vào bị strip_tones cắt dấu ('Cường đầu trọc' -> 'Cuong Đau Troc'). Sửa đúng phải thêm trường vi vào NameEntry và một ô nhập riêng trên UI — đổi thiết kế, không tự sửa.

### `D:\Binh\SrtGen\srtgen\core\rules.py:196-199`

ASCII_OTHER = '()<>[]{}"' được tách thành punct nhưng KHÔNG bao giờ báo ASCII_PUNCT, trong khi docs/format-contract.md mục 3 (bảng dấu, dòng 117-130) bắt buộc '()'→'（）', '""'→'“”', '<>'→'《》' và dòng 138 cấm trộn dấu ASCII với dấu tiếng Trung trong cùng file. Đo được: block '他 说(好)' / 'tā shuō(hǎo)' -> validate_text trả về [] và format_report_lines in 'File đạt toàn bộ 12 mục của quy chuẩn'; y hệt với '"' và '<>'. Trong khi đó srtgen/core/srt.py:94-105 (ASCII_PUNCT_MAP) lại đổi '(' thành '（' khi chạy fix — tức check và fix đang nói ngược nhau về cùng một file. KHÔNG tự sửa vì (a) checklist mục 6 chỉ liệt kê đúng 6 dấu nên là chuyện diễn giải hợp đồng, (b) thêm '<' '>' sẽ báo oan thẻ nghiêng SRT '<i>...</i>' (đã đo: run_fix biến nó thành '《i》...《/ i》'). Cần chủ dự án chốt: hoặc thêm '(' ')' '"' vào luật và xử lý riêng thẻ <i>, hoặc ghi rõ vào docs rằng validator cố ý bỏ qua.

### `D:\Binh\SrtGen\srtgen\web\server.py:316-321`

save_settings chỉ nhận 9 khoá, nên "Dịch tiếng Việt bằng…" (translate_provider), "Tách giọng khỏi nhạc nền" (demucs), bilingual_ass và bom bị vứt lặng lẽ dù toast báo "Đã lưu cài đặt" — tải lại trang là về mặc định. build-spec-v2 mục 8 bắt buộc phải có 2 mục đầu. Client còn gửi khoá 'translate_provider' trong khi server chờ 'ai_provider'.

### `D:\Binh\SrtGen\srtgen\web\server.py:217`

_config_for_job không ánh xạ options 'demucs' và 'no_translate', nên 2 ô tick ở màn hình chính là nút giả. Chạy thẳng hàm với đúng options của collectOptions(): audio.demucs=False dù người dùng bật, translate.enabled=True dù người dùng tắt. Khoá cấu hình đã có sẵn (default.yaml audio.demucs:56, translate.enabled) và S1 có đọc (s1_audio.py:133) — chỉ thiếu đoạn nối ở tầng web.

### `D:\Binh\SrtGen\srtgen\web\server.py (thiếu route) + app.js:2111`

POST /api/settings/test-key không tồn tại -> nút "Kiểm tra key" (index.html:565) trả 405. build-spec-v2 mục 8 bắt buộc, HUONG-DAN.md mục 11 dặn người dùng bấm nút này.

### `D:\Binh\SrtGen\srtgen\web\server.py (thiếu route) + app.js:1735`

GET /api/names (danh sách phim) không tồn tại -> tab "Tên riêng" hiện "Không có chức năng này." thay cho ô chọn phim, không dùng được.

### `D:\Binh\SrtGen\installer\KhoiDong.command:130`

export SRTGEN_NO_BROWSER=1 không được đọc ở bất kỳ đâu trong srtgen/. Cờ --no-browser cũng vô hiệu: srtgen/web/server.py:2720 khai báo serve(open_browser=True, port=None) không có tham số host, nên vòng thử chữ ký trong srtgen/cli.py:_call_server rơi xuống lời gọi {port} và open_browser trở về mặc định True (đã mô phỏng bằng python, in ra 'serve() CHAY THAT: open_browser=True'). Hậu quả: app tự mở 1 tab, KhoiDong 'open' thêm 1 tab nữa — đúng cái mà chú thích trong KhoiDong.command nói là phải tránh. Sửa đúng phải chạm code app nên tôi không tự sửa.

### `D:\Binh\SrtGen\installer\CaiDat.command`

Bit quyền chạy sẽ mất khi giao qua git: repo đang core.fileMode=false và installer/ chưa được track. Thử trong repo tạm: 'git add CaiDat.command' cho ra mode 100644 (không có bit x), nên sau khi clone trên Mac bấm đúp không chạy được. HUONG-DAN.md §2 Cách 3 có dạy chmod +x nên không chết người. Lúc commit nên chạy: git update-index --chmod=+x installer/*.command


## Mức nhẹ

### `D:\Binh\SrtGen\srtgen\stages\s9_emit.py:1285 (hàm vi_line)`

Cue giữ nguyên văn tiếng Trung vẫn bị _vi_punct đổi dấu câu: 'chen lu zhou，ni chuang huo le。' ghi vào _vi.srt thành '... , ... .' (dấu ASCII), '……' thành '…'. Không ảnh hưởng số cue hay timestamp, chỉ làm người soát diff hai file thấy dòng 'chưa dịch' không giống hệt dòng gốc bên .srt. Đây là lựa chọn thiết kế (file tiếng Việt dùng dấu ASCII) chứ không rõ ràng là lỗi, nên tôi KHÔNG sửa — cần anh chốt: fallback nên chép nguyên xi dòng Hán hay vẫn chuẩn hoá dấu.

### `D:\Binh\SrtGen\srtgen\stages\s8_translate.py`

Khoá cache (zh, target, model, glossary_hash) không mang phiên bản prompt. Mọi cải tiến prompt văn phong sau này sẽ vô hình với người đã có cache ấm ở user_cache_dir()/translate. Khoá hiện tại đúng y đặc tả nên chỉ là ghi chú, không phải vi phạm.

### `D:\Binh\SrtGen\srtgen\stages\s8_translate.py`

Dòng 600: model = getattr(translator,'model','') or settings['model'] — nhà cung cấp không có model vẫn bị gán tên model của Gemini. Chạy thử: provider=null -> model báo cáo 'gemini-2.5-flash'; provider=google_free -> 'gemini-2.5-flash'. S8_translate.json vì thế nói sai và bucket cache bản miễn phí mang tên translate.google_free.gemini-2.5-flash.

### `D:\Binh\SrtGen\srtgen\core\rules.py:1558-1574`

looks_like_vi_document đòi ĐA SỐ block phải là block 3 dòng (three_line*2 > len(cues)). Một file _vi.srt mà phần lớn lời dịch bị ngắt xuống 2 dòng sẽ bị nhận nhầm là file Hán–pinyin và ăn trọn bộ luật tiếng Trung. Dựng thử file vi 3 block trong đó 2 block bị ngắt dòng: kết quả ['ASCII_PUNCT','BLOCK_SHAPE','CUM_MISMATCH','PUNCT_SYNC'] với những câu vô nghĩa như 'Cue 2: dòng Hán còn dấu ":" kiểu Latin. Thấy "Tập 1:", phải đổi thành "："' và 'dòng Hán có 2 cụm nhưng dòng pinyin có 3 cụm. Hán: "Tập | 1" — Pinyin: "Hàng | xóm | mới"'. Dữ liệu thật hiện chưa dính (BonnieBears_tap1_vi.srt 115/116 block đúng 3 dòng, hai file kia 0/187 và 0/299) và đường validate_pair miễn nhiễm hoàn toàn vì nó không dùng heuristic này. KHÔNG sửa vì nới điều kiện đếm dòng là đổi thiết kế bộ nhận diện — docstring nói rõ điều kiện này cố ý dùng để một tài liệu chỉ có pinyin (Latin, không Hán) không bị nhận nhầm là tiếng Việt.

### `D:\Binh\SrtGen\srtgen\core\rules.py:1174-1181`

Với block HỎNG mà mốc thời gian rơi xuống vị trí dòng 3, dấu phẩy của mốc thời gian bị đem đi soi như nội dung. Ví dụ block '1 / 2 / 00:00:01,000 --> 00:00:02,000 / 好' sinh ra 'Cue 1: dòng Hán còn dấu "," kiểu Latin. Thấy "00:00:01,000 --> ", phải đổi thành "，"' cùng 5 finding ASCII/DASH/CUM khác. Lời khuyên này nếu người dùng làm theo sẽ phá hỏng file, đúng cái mà ghi chú cuối docs/format-contract.md (dòng 345) cấm. Ảnh hưởng thấp: cùng block đó đã bị TIMESTAMP_FORMAT bắt trước, và mọi file đúng hình dạng đều an toàn. KHÔNG sửa vì cách chữa đúng (bỏ qua luật nội dung khi mốc thời gian của block không đọc được) là một quyết định thiết kế về thứ tự luật, nên để chủ dự án chốt.

### `D:\Binh\SrtGen\srtgen\core\rules.py:926-971`

NAME_INCONSISTENT (checklist mục 12 — 'tên riêng') thực chất bắt mọi chuỗi Hán lặp lại lúc gộp lúc tách, nên phần lớn cảnh báo không phải tên riêng: đo trên completed.srt sau khi fix ra 8 cảnh báo thì là 一百万 (một triệu), 就让, 好好, 干什么, 多年, 别闹, 别过来, 龙拳. Câu thông báo vẫn khẳng định 'Cùng một tên riêng phải giữ một cách tách trong cả file', dễ khiến người soát đi gộp một cụm mà README mục 7 lại yêu cầu tách theo nhịp pinyin. Đây là đánh đổi ĐÃ ĐƯỢC GHI RÕ trong docstring của hàm và chỉ ở mức warn nên không chặn cổng; KHÔNG sửa vì thu hẹp luật (chỉ soi chuỗi có trong names.json) là đổi thiết kế. Đề xuất tối thiểu nếu muốn: đổi chữ 'tên riêng' trong thông báo thành 'cụm từ' cho khỏi đánh lừa.

### `D:\Binh\SrtGen\srtgen\web\server.py:371`

GET /api/settings không trả danh sách model và cờ model nào đã tải, dù build-spec-v2 mục 8 và index.html:585 đều hứa hiện "đã tải rồi". Giao diện xuống bản dự phòng và nói "Chương trình chưa cho biết model nào đã tải".

### `D:\Binh\SrtGen\srtgen\web\server.py:334`

Câu lỗi thư mục có lẫn nguyên văn tiếng Anh của hệ điều hành: "Không dùng được thư mục “Z:/…”: [WinError 3] The system cannot find the path specified…". Câu bao ngoài vẫn dẫn được hành động nên chỉ là gợn.

### `D:\Binh\SrtGen\srtgen\web\server.py:2694`

Hàm sinh câu lỗi tiếng Việt được đặt tên bằng chữ Nga: def _читать(err) (gọi ở :2097). Chạy đúng, nhưng là một cái tên lạc lõng giữa code base tiếng Anh/tiếng Việt.

### `D:\Binh\SrtGen\installer\KhoiDong.command:126`

SRTGEN_UV và SRTGEN_VENV được export nhưng không có ai đọc; nút 'Cập nhật yt-dlp' mà build-spec-v2 §6 yêu cầu chưa tồn tại trong web UI (grep 'yt-dlp' trong srtgen/web/server.py không ra kết quả nào).

### `D:\Binh\SrtGen\srtgen\cli.py:685`

doctor_checks() chưa kiểm 'uv có chưa' như build-spec-v2 §6 yêu cầu (hiện có python, machine, ffmpeg, ffprobe, yt-dlp, packages, model, directories/disk, ai). Không chặn cài đặt vì model chưa tải chỉ là WARN nên doctor vẫn trả 0.

### `D:\Binh\SrtGen\installer\CaiDat.command:752`

chmod +x và xattr -r -d com.apple.quarantine cho các file .command anh em chỉ chạy ở Bước 7. Nếu cài đứt ở bước 1-6 thì KhoiDong.command và GoCaiDat.command vẫn còn nhãn quarantine của Gatekeeper. Nên dời 2 dòng đó lên ngay sau câu hỏi 'Bắt đầu?'.

### `D:\Binh\SrtGen\installer\GoCaiDat.command:30`

OUT_DIR đóng cứng '$APP_SUPPORT/output'. Đúng với mặc định (platformdirs user_data_dir/output), nhưng nếu người dùng đổi paths.out_dir trong tab Cài đặt sang một chỗ nằm trong $APP_SUPPORT/work thì các file .srt sẽ bị xoá cùng WORK_DIR mà không được hỏi câu 'giữ hay xoá kết quả'.

### `D:\Binh\SrtGen\srtgen\web\static\app.js`

Kiểm chồng lấn mốc thời gian chỉ vẽ lại đúng hàng vừa sửa: sửa cue 2 thành 00:00:02,000 --> 00:00:07,000 thì hàng 2 hiện 'Mốc thời gian chồng lên dòng 1.' nhưng hàng 3 (đang bị cue 2 đè lên) vẫn trống vì refreshRow chỉ gọi cho hàng i. Không mất dữ liệu: validator lúc Lưu vẫn bắt (rules.py:1168 TIMESTAMP_ORDER, 'Hai cue chồng lấn thời gian, Aegisub sẽ báo lỗi'). Không sửa vì chỉ là lúc vẽ lại, và cách sửa (vẽ lại cả hai hàng kề) nằm ngoài phần được giao.

### `D:\Binh\SrtGen\srtgen\stages\s5_tokenize.py`

Sinh lại pinyin trả 谁 = 'shuí' trong khi bản đã soát tay dùng 'shéi' (corpus/completed.srt: 5 lần 'shéi', 0 lần 'shuí'), và chính rules.py:845 lấy '谁 đọc shéi' làm ví dụ cách đọc đúng ngữ cảnh. Đây KHÔNG phải lỗi riêng của trình sửa: chạy thẳng S5+S6 trên '你是谁？' cũng ra 'Nǐ shì shuí？'. Hệ quả với người dùng là bấm nút một cái thì 'shéi' của biên tập viên bị đổi thành 'shuí'. Không sửa: nằm ở chặng S5/S6, ngoài phạm vi được giao.
