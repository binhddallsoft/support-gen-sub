/* ==========================================================================
   SrtGen — giao diện web (JavaScript thuần, không framework, không CDN)
   --------------------------------------------------------------------------
   Vì sao viết tay thay vì dùng framework: bộ cài không có bước build và máy
   đích có thể không có mạng. Một file .js tĩnh là thứ chắc chắn chạy được.

   NGUYÊN TẮC XUYÊN SUỐT
     * Người dùng không phải dân IT. Không hiện traceback, không hiện mã lỗi
       trần trụi. Mỗi lỗi phải là một câu tiếng Việt + (nếu có) một nút bấm
       để sửa.
     * Không alert()/confirm() của trình duyệt — dùng dialog() ở cuối file.
     * Giao diện không được vỡ khi máy chủ thiếu một đường dẫn API: mọi lời
       gọi đi qua api(), lỗi biến thành thông báo tiếng Việt.

   HỢP ĐỒNG VỚI MÁY CHỦ (build-spec mục 10 + build-spec-v2 mục 4)
   ---------------------------------------------------------------------------
     GET  /api/settings              -> {settings:{...}, models:[{id,label,eta,size,downloaded}],
                                         api_key_masked, api_key_set, work_dir}
     POST /api/settings              <- {settings lồng như default.yaml}
     POST /api/settings/test-key     <- {api_key?} -> {ok, message}
                                        (không gửi api_key = thử khoá đã lưu)
     GET  /api/doctor                -> {checks:[{id,label,status,detail,hint,fix_action}]}
     POST /api/actions/<key>         -> {job_id} (việc chạy lâu) hoặc {ok, message}
                                        key CHỈ nằm trong ACTIONS bên dưới
     GET  /api/jobs                  -> {jobs:[{id,status,title,source,created_at}]}
     POST /api/jobs                  <- JSON {source, source_kind, mode, options}  (dán link / đường dẫn)
                                     <- multipart/form-data, ĐÚNG hai trường: file (nhị phân)
                                        + options (chuỗi JSON) — xem uploadAndCreate()
                                     -> {job_id, ...} hoặc {job:{id,...}}; 400/413 = {error:{message}}
     GET  /api/jobs/<id>             -> trạng thái đầy đủ (dùng để khôi phục)
     GET  /api/jobs/<id>/events      -> SSE
     POST /api/jobs/<id>/cancel
     GET  /api/jobs/<id>/result      -> {files:{srt,vi,bundle,report,ass,backups}, findings, stats, video_id}
                                        files.backups = đường dẫn các bản cũ S9 vừa cất thành
                                        "<tên>.truoc-<ngày giờ>…" — xem renderBackups()
     POST /api/jobs/<id>/rerun       <- {from_step?} (mặc định 5 = tách cụm) -> 202 {job_id, job}
                                        dùng lại video_id + work/ của việc cũ, không cần file gốc;
                                        400/409 = câu tiếng Việt — xem rerunJob()
     GET  /api/download/<id>/<kind>
     POST /api/reveal                <- {path}
     POST /api/check                 <- {filename, text} -> {findings, blocks}
     POST /api/fix                   <- {filename, text} -> {text, filename, has_vi, vi_text (str|null),
                                         vi_filename (str|null|""), findings, summary, set_aside}
                                        has_vi=true (file gốc ba dòng Hán/pinyin/Việt) = HAI file tải
                                        về: `text` -> filename, `vi_text` -> vi_filename. Xem fixFile().
     GET  /api/health                -> {max_media_bytes, max_upload_bytes, ...} — xem loadHealth()
     GET  /api/names                 -> {movies:[{video_id,title,count}]}
     GET  /api/names/<video_id>      -> {video_id, entries:[{zh,pinyin,vi}]}
     PUT  /api/names/<video_id>      <- {entries:[{zh,pinyin,vi}]} -> {ok, count, backup_name?}
                                        GET trả thêm corrupt, corrupt_message, backup_name, path:
                                        file hỏng thì hiện khung vàng, KHÔNG hiện "bảng đang trống"
     GET  /guide                     -> trang HTML của HUONG-DAN.md, mở ở thẻ mới — xem openGuide()

   TRÌNH SỬA (build-spec-v2 mục 4)
     GET  /api/jobs/<id>/doc         -> {cues:[{index,start,end,zh,py,vi,flag}], findings:[...]}
     GET  /api/jobs/<id>/doc?original=1 -> bản máy tạo, cho nút "Hoàn nguyên"
     PUT  /api/jobs/<id>/doc         <- {cues:[...]} -> {findings, summary, saved_to,
                                         downloads:{srt, vi_srt|null}, origin:"content"|"path"|"job"}
                                        origin "content" (mở bằng nội dung file) = nút Lưu thành
                                        "Lưu và tải về" — xem saveDoc()
     PUT  /api/jobs/<id>/draft       <- {cues:[...], rev, base_hash} (tự lưu mỗi 5 giây)
     GET  /api/jobs/<id>/draft       -> {exists, saved_at, rev, doc} | {exists:false}
     GET  /api/jobs/<id>/audio       -> wav đã xử lý ở S1, có hỗ trợ HTTP Range
     POST /api/retokenize            <- {zh, video_id, capitalize} -> {tokens, zh_line, py_line}
     POST /api/open-local            <- {path} hoặc {name, content, vi_content}

   Máy chủ có thể gói dữ liệu công việc trong {"job": {...}} hoặc trả thẳng ra
   ngoài; unwrapJob() nhận cả hai để giao diện không phụ thuộc chi tiết đó.

   SỰ KIỆN SSE — chấp nhận cả hai kiểu: sự kiện có tên (`event: progress`) và
   sự kiện mặc định có trường `type`. Các kiểu hiểu được:
     state|snapshot   {status, steps:[...], progress, current, result, error}
     stage|step       {stage, status, message}
     progress         {stage, progress:0..1, eta, message, label}
     log              {text, level}
     done|result      {result:{...}}
     error|failed     {message, fix_action, detail}
     cancelled        {}
     ping|heartbeat   (bỏ qua)
   ========================================================================== */

'use strict';

/* --------------------------------------------------------------------------
   0. Tiện ích nhỏ
   -------------------------------------------------------------------------- */

const $  = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.prototype.slice.call((root || document).querySelectorAll(sel));

/** Dựng phần tử. Dùng thay innerHTML để không bao giờ nhúng nhầm chuỗi của
 *  người dùng vào HTML — nội dung phụ đề có đủ loại ký tự lạ. */
function h(tag, props, ...kids) {
  const node = document.createElementNS(
    tag === 'svg' || tag === 'use' ? 'http://www.w3.org/2000/svg' : 'http://www.w3.org/1999/xhtml',
    tag
  );
  for (const key in (props || {})) {
    const val = props[key];
    if (val === null || val === undefined || val === false) continue;
    if (key === 'class') node.setAttribute('class', val);
    else if (key === 'text') node.textContent = val;
    else if (key === 'html') node.innerHTML = val;
    else if (key.slice(0, 2) === 'on') node.addEventListener(key.slice(2).toLowerCase(), val);
    else if (key === 'href' && tag === 'use') node.setAttribute('href', val);
    else if (key === 'dataset') Object.assign(node.dataset, val);
    else if (val === true) node.setAttribute(key, '');
    else node.setAttribute(key, val);
  }
  for (const kid of kids.flat(9)) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.appendChild(typeof kid === 'object' ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

/** Biểu tượng lấy từ bộ sprite nhúng trong index.html. */
function icon(name, cls) {
  return h('svg', { class: 'icon ' + (cls || '') }, h('use', { href: '#i-' + name }));
}

function clear(node) { while (node && node.firstChild) node.removeChild(node.firstChild); }

function show(node, on) { if (node) node.hidden = !on; }

const NBSP = '\u00a0';

/** "12 phút", "1 giờ 5 phút" — luôn tròn, người dùng không cần con số chính xác. */
function humanDuration(sec) {
  sec = Math.max(0, Math.round(Number(sec) || 0));
  if (sec < 45) return 'chưa đầy 1 phút';
  const mins = Math.round(sec / 60);
  if (mins < 60) return mins + ' phút';
  const hrs = Math.floor(mins / 60);
  const rest = mins % 60;
  return rest ? hrs + ' giờ ' + rest + ' phút' : hrs + ' giờ';
}

function clockText(sec) {
  sec = Math.max(0, Math.round(Number(sec) || 0));
  const m = Math.floor(sec / 60), s = sec % 60;
  return m + ':' + String(s).padStart(2, '0');
}

function humanSize(bytes) {
  bytes = Number(bytes) || 0;
  if (bytes < 1024) return bytes + ' B';
  const units = ['KB', 'MB', 'GB'];
  let v = bytes / 1024, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return (v >= 10 ? Math.round(v) : v.toFixed(1)).toString().replace('.', ',') + ' ' + units[i];
}

function plural(n, word) { return n + ' ' + word; }

function baseName(path) {
  return String(path || '').split(/[\\/]/).pop() || String(path || '');
}

function dirName(path) {
  const s = String(path || '');
  const i = Math.max(s.lastIndexOf('/'), s.lastIndexOf('\\'));
  return i > 0 ? s.slice(0, i) : s;
}

/** Đọc một khoá lồng nhau kiểu "ai.api_key" mà không nổ khi thiếu tầng giữa. */
function pick(obj, path, fallback) {
  let cur = obj;
  for (const part of String(path).split('.')) {
    if (cur === null || typeof cur !== 'object' || !(part in cur)) return fallback;
    cur = cur[part];
  }
  return cur === undefined || cur === null ? fallback : cur;
}

/* Link video không có "https://" ở đầu: người dùng hay chép từ thanh địa chỉ
   của điện thoại hoặc từ tin nhắn, nơi phần đó bị ẩn — "youtube.com/watch?v=…",
   "m.youtube.com/…", "youtu.be/…". Không nhận ra thì chuỗi bị gửi đi như tên
   file và máy chủ báo "Không tìm thấy file", người dùng không hiểu sai ở đâu. */
const BARE_VIDEO_URL_RE = /^(?:www\.|(?:(?:www|m|music)\.)?youtube\.com\/|youtu\.be\/)/i;

function looksLikeUrl(text) {
  const t = String(text === null || text === undefined ? '' : text).trim();
  return /^https?:\/\//i.test(t) || BARE_VIDEO_URL_RE.test(t);
}

/** Link gửi cho máy chủ luôn có "https://": máy chủ chỉ nhận http/https, còn
 *  "youtu.be/abc" trần thì nó hiểu là đường dẫn file. Không phải link: trả nguyên. */
function normalizeSourceUrl(text) {
  const t = String(text === null || text === undefined ? '' : text).trim();
  if (/^https?:\/\//i.test(t)) return t;
  return BARE_VIDEO_URL_RE.test(t) ? 'https://' + t : t;
}

/** Đường dẫn tuyệt đối trên máy: "/Users/…", "~/…", "C:\…". Dán đường dẫn
 *  thay vì kéo thả giúp khỏi phải tải lên một file video 2GB. */
function looksLikePath(text) {
  const t = String(text).trim();
  return /^(~|\/|[A-Za-z]:[\\/]|\\\\)/.test(t) && t.length > 2;
}

/* --------------------------------------------------------------------------
   1. Bảng chữ dùng chung
   -------------------------------------------------------------------------- */

/* Nhãn tiếng Việt của từng mã lỗi — BẢN DỰ PHÒNG.

   Nguồn thật là `RULES` + `VI_RULES` trong srtgen/core/rules.py. Finding máy
   chủ gửi về hiện chưa mang trường `label` (Finding.to_dict chỉ có code,
   severity, cue_index, message, line) và server.py không thuộc giao diện, nên
   giao diện vẫn phải giữ một bản chép. Thứ tự ưu tiên ở ruleLabel():
     1. `label` do máy chủ gửi kèm finding — có là dùng, bảng này chịu thua;
     2. bảng dưới đây;
     3. câu chung chung, còn mã lỗi vẫn hiện riêng ở góc để tra.
   Đã đồng bộ từng chữ với rules.py lúc sửa (đợt 3): NAME_INCONSISTENT nay là
   "Cùng một cụm từ lúc gộp lúc tách" (luật bắt mọi cụm, không riêng tên), và
   năm mã VI_* theo đúng VI_RULES. Đổi nhãn bên Python thì sửa luôn ở đây. */
const RULE_LABELS = {
  BLOCK_SHAPE: 'Một đoạn phụ đề không đủ 4 dòng',
  INDEX_SEQ: 'Số thứ tự không liên tục',
  TIMESTAMP_FORMAT: 'Mốc thời gian sai định dạng',
  TIMESTAMP_ORDER: 'Mốc thời gian sai thứ tự',
  CUM_MISMATCH: 'Dòng Hán và dòng pinyin lệch số cụm',
  PAIR_MISMATCH: 'Cụm Hán và cụm pinyin cùng vị trí không khớp phát âm',
  LEADING_SPACE: 'Thừa khoảng trắng đầu dòng',
  TRAILING_SPACE: 'Thừa khoảng trắng cuối dòng',
  DOUBLE_SPACE: 'Hai khoảng trắng liền nhau',
  ASCII_PUNCT: 'Còn dấu câu kiểu Latin trong nội dung',
  SPACE_AROUND_PUNCT: 'Khoảng trắng sát dấu câu tiếng Trung',
  ELLIPSIS_FORM: 'Dấu lửng không đúng dạng ……',
  DASH_FORM: 'Dấu ngắt lời không đúng dạng ——',
  MARKER_SPACING: 'Dấu gạch đổi người nói thiếu hoặc thừa khoảng trắng',
  MARKER_SYNC: 'Dấu gạch đổi người nói lệch vị trí giữa hai dòng',
  PUNCT_SYNC: 'Dấu câu lệch giữa hai dòng',
  NAME_INCONSISTENT: 'Cùng một cụm từ lúc gộp lúc tách',
  ERHUA_SPLIT: 'Âm nhi hoá (儿化音) bị tách rời',
  EMPTY_CUE: 'Một đoạn phụ đề không có chữ nào',
  VI_BLOCK_COUNT: 'Hai file có số đoạn phụ đề khác nhau',
  VI_TIMESTAMP_DRIFT: 'Mốc thời gian hai file lệch nhau',
  VI_EMPTY_LINE: 'Đoạn phụ đề chưa có dòng tiếng Việt',
  VI_TRAILING_SPACE: 'Thừa khoảng trắng đầu hoặc cuối dòng tiếng Việt',
  VI_MARKER_SYNC: 'Dấu gạch đổi người nói lệch giữa hai file'
};

/** Nhãn hiển thị của một finding. Máy chủ gửi `label` thì dùng của máy chủ —
 *  hai nơi chép cùng một bảng là hai nơi sẽ lệch nhau (đã lệch một lần). */
function ruleLabel(fd) {
  fd = fd || {};
  const own = typeof fd.label === 'string' ? fd.label.trim() : '';
  return own || RULE_LABELS[fd.code] || 'Chỗ cần xem lại';
}

const SEVERITY_LABEL = { error: 'Lỗi', warn: 'Cảnh báo', info: 'Ghi chú' };

/* Tám (hoặc mười) bước của pipeline. Danh sách này chỉ là bản dự phòng: nếu
   máy chủ gửi kèm danh sách bước riêng thì dùng của máy chủ. Bước nào chạy
   xong cả job mà vẫn chưa được nhắc tới sẽ bị đánh dấu "bỏ qua" chứ không
   để quay mãi — người dùng nhìn vòng xoay không dừng sẽ tưởng máy treo. */
const STEP_DEFS = [
  { key: 'fetch', alias: ['s0', 'info', 'tai', 'download', 'source'],
    name: 'Tải video và tách âm thanh', why: 'Lấy phần tiếng của video về máy.' },
  { key: 'audio', alias: ['s1', 'amthanh'],
    name: 'Xử lý âm thanh', why: 'Cân lại âm lượng để máy nghe rõ hơn.' },
  { key: 'asr', alias: ['s2', 'whisper', 'nghe', 'transcribe'],
    name: 'Nghe và gỡ băng', why: 'Máy nghe từng câu rồi ghi lại thành chữ Hán.', slow: true },
  { key: 'clean', alias: ['s3', 'cleanup', 'don'],
    name: 'Dọn kết quả gỡ băng', why: 'Bỏ những đoạn lặp và đoạn máy nghe nhầm.' },
  { key: 'cues', alias: ['s4', 'cue', 'chia', 'split'],
    name: 'Chia câu theo nhịp thoại', why: 'Cắt thành từng dòng phụ đề ngắn, vừa nhịp nói.' },
  { key: 'tokens', alias: ['s5', 'tokenize', 'token', 'pinyin'],
    name: 'Tách cụm và sinh pinyin', why: 'Tách từng cụm chữ Hán và ghi pinyin đúng theo cụm đó.' },
  { key: 'norm', alias: ['s6', 'normalize', 'chuanhoa'],
    name: 'Chuẩn hoá định dạng', why: 'Đổi dấu câu, sửa khoảng trắng, viết hoa đầu câu.' },
  { key: 'ai', alias: ['s7', 'assist'],
    name: 'Nhờ AI soát tên riêng và chữ khó', why: 'Chỉ chạy khi bạn bật và đã nhập khoá API.' },
  { key: 'translate', alias: ['s8', 'dich', 'vi', 'translation'],
    name: 'Dịch sang tiếng Việt', why: 'Dịch theo lô có ngữ cảnh, giữ tên nhân vật nhất quán cả phim.' },
  { key: 'emit', alias: ['s9', 'export', 'xuat', 'write'],
    name: 'Xuất file', why: 'Kiểm lại toàn bộ quy chuẩn rồi ghi ra .srt, _vi.srt và báo cáo.' }
];

/* Loại file âm thanh/video máy chủ nhận ở POST /api/jobs dạng multipart — chép
   ĐÚNG danh sách trong hợp đồng tải lên (không phân biệt hoa thường). Kiểm ngay
   ở trình duyệt để người dùng không phải chờ đưa lên 2 GB rồi mới nghe câu
   "không nhận loại file này". Máy chủ vẫn là người quyết cuối cùng. */
const MEDIA_EXTS = ['.mp3', '.mp4', '.m4a', '.wav', '.mkv', '.mov', '.webm', '.flac', '.aac', '.ogg', '.avi'];

/* Mức mặc định của web.max_media_bytes (4 GiB) — KHÁC mức 20 MB của file .srt.
   Máy chủ báo con số riêng (qua /api/health hoặc cài đặt) thì dùng con số đó. */
const DEFAULT_MAX_MEDIA_BYTES = 4 * 1024 * 1024 * 1024;

/** ".MP4" -> ".mp4"; không có đuôi thì chuỗi rỗng. */
function fileExt(name) {
  const m = String(name || '').match(/\.[^.\\/]+$/);
  return m ? m[0].toLowerCase() : '';
}

function mediaTypesText() {
  return MEDIA_EXTS.map((e) => e.slice(1)).join(', ');
}

/** Giới hạn dung lượng file video tải lên mà máy chủ đang áp. */
function maxMediaBytes() {
  const fromSettings = Number(setting(['max_media_bytes', 'web.max_media_bytes'], 0));
  return state.maxMediaBytes || (fromSettings > 0 ? fromSettings : 0) || DEFAULT_MAX_MEDIA_BYTES;
}

/** Câu báo file video quá nặng, dùng khi chặn ngay ở trình duyệt lúc bấm Bắt đầu. */
function mediaTooBigText(file, limit) {
  return '“' + file.name + '” nặng ' + humanSize(file.size) + ', vượt mức ' + humanSize(limit) +
    ' mà chương trình nhận qua đường chọn file, nên chưa đưa lên byte nào. Cách làm: dán thẳng đường dẫn ' +
    'của file trên máy vào ô phía trên (ví dụ /Users/ban/Movies/phim.mp4) — chương trình đọc thẳng file, ' +
    'không phải đưa lên. Hoặc cắt video thành các đoạn ngắn hơn, hay chỉ lấy phần âm thanh (mp3, m4a).';
}

/* Bảng model — dự phòng khi máy chủ chưa trả danh sách. Số liệu lấy từ
   build-spec-v2 mục 5.1, đo cho video 40 phút trên iMac 2017. */
const MODELS = [
  { id: 'large-v3-turbo', name: 'Cân bằng (khuyên dùng)', tag: 'khuyên dùng', time: '~10-15 phút', size: '~1.6 GB' },
  { id: 'large-v3', name: 'Chính xác nhất', tag: '', time: '~30-40 phút', size: '~3 GB' },
  { id: 'medium', name: 'Nhanh, độ chính xác vừa', tag: '', time: '~8-12 phút', size: '~1.5 GB' },
  { id: 'small', name: 'Thử nhanh', tag: '', time: '~4-6 phút', size: '~0.5 GB' }
];

/** Bảng model của máy chủ (cfg.asr.models) -> hình dạng giao diện cần.
 *  Đọc thẳng từ cấu hình chứ không viết cứng trong JS: thêm model mới thì sửa
 *  default.yaml là xong, không phải đụng vào giao diện. */
function normModels(list) {
  return (list || []).map((m) => {
    const id = String(m.id || m.model || m.name || '');
    const known = MODELS.find((k) => k.id === id) || {};
    return {
      id,
      name: String(m.label || m.name || known.name || id),
      tag: m.recommended ? 'khuyên dùng' : String(m.tag || known.tag || ''),
      time: String(m.eta || m.eta_40min || m.time || known.time || ''),
      size: String(m.size || m.download_size || known.size || ''),
      // null = máy chủ không nói gì; đừng bịa ra chữ "Chưa tải" khi không biết.
      downloaded: typeof m.downloaded === 'boolean' ? m.downloaded : null,
      problem: String(m.problem || '')
    };
  }).filter((m) => m.id);
}

/* DANH SÁCH TRẮNG các việc gửi tới POST /api/actions/{key}.
   ------------------------------------------------------------------------
   Giao diện KHÔNG BAO GIỜ tự nghĩ ra tên việc rồi gửi đi: chỉ đúng năm khoá
   dưới đây, khớp với danh sách máy chủ nhận. Máy chủ từ chối mọi khoá lạ bằng
   một câu tiếng Việt, nhưng chặn ngay từ đây thì người dùng không phải nhìn
   thấy câu từ chối đó.

   `long: true` = việc chạy vài phút, máy chủ trả {job_id} và đẩy tiến trình
   qua SSE. Bấm xong mà màn hình im lặng là lỗi nặng nhất với người dùng không
   phải dân IT — họ sẽ bấm lại nhiều lần rồi tưởng tool hỏng. Vì vậy mọi việc
   dài đều mở hộp có thanh chạy và nhật ký. */
const ACTIONS = {
  install_ffmpeg: {
    long: true,
    label: 'Cài ffmpeg giúp tôi',
    busy: 'Đang cài ffmpeg…',
    title: 'Đang cài ffmpeg',
    note: 'Tool đang tải bản ffmpeg gọn nhẹ về thư mục của chính chương trình. ' +
          'Không đụng gì tới phần còn lại của máy và không cần mật khẩu quản trị.',
    okTitle: 'Đã cài xong ffmpeg',
    okText: 'Máy đã đọc được file video rồi. Bạn thử chạy lại video vừa nãy.'
  },
  update_ytdlp: {
    long: true,
    label: 'Cập nhật yt-dlp giúp tôi',
    busy: 'Đang cập nhật yt-dlp…',
    title: 'Đang cập nhật yt-dlp',
    note: 'yt-dlp là phần tải video từ YouTube. YouTube hay đổi cách hoạt động nên ' +
          'thỉnh thoảng phải cập nhật; việc này thường mất chưa tới một phút.',
    okTitle: 'Đã cập nhật yt-dlp',
    okText: 'Xong rồi. Bạn dán lại link và bấm Bắt đầu.'
  },
  download_model: {
    long: true,
    refreshModels: true,
    label: 'Tải model gỡ băng giúp tôi',
    busy: 'Đang tải model…',
    title: 'Đang tải model gỡ băng',
    note: 'Model là phần máy dùng để nghe. Nặng khoảng 0,5–3 GB tuỳ loại bạn chọn, ' +
          'tải một lần rồi dùng mãi. Đứt mạng giữa chừng thì lần sau tải tiếp, không mất công.',
    okTitle: 'Đã tải xong model',
    okText: 'Model đã nằm sẵn trên máy, lần chạy tới sẽ vào việc ngay.'
  },
  fetch_preview: {
    long: true,
    label: 'Tải video để xem trước',
    busy: 'Đang tải video…',
    title: 'Đang tải video để xem trước',
    note: 'Lần chạy trước tool chỉ tải phần tiếng. Giờ tải thêm bản có hình (tối đa 720p, ' +
          'đủ nhìn chữ) để bạn xem phụ đề đè lên video ngay trong app. Video 40 phút ' +
          'thường mất vài phút, tuỳ tốc độ mạng.',
    okTitle: 'Đã có video',
    okText: 'Quay lại tab Sửa phụ đề: khung xem trước sẽ tự chuyển sang có hình. Bấm ▶ để xem.'
  },
  open_output_dir: { long: false, label: 'Mở thư mục kết quả', busy: 'Đang mở…' },
  open_guide:      { long: false, label: 'Mở hướng dẫn sử dụng', busy: 'Đang mở…' }
};

/* Mã fix_action máy chủ có thể phát ra -> thứ giao diện phải dựng cho mã đó.

   Mỗi mục có thể có:
     kind   : loại NÚT bấm. null = KHÔNG có nút, vì không có việc gì máy tự làm
              được (đăng nhập, video bị chặn theo vùng...) — khi đó lời khuyên là
              thứ duy nhất người dùng nhận được, nên nó phải cụ thể;
     label  : chữ trên nút;
     action : khoá trong ACTIONS ở trên, cho nút kind 'server' / 'guide';
     advice : câu mở đầu khối "Cách xử lý", hiện NGAY trên màn hình lỗi;
     steps  : từng bước cụ thể, hiện thành danh sách đánh số dưới `advice`.

   Vì sao lời khuyên hiện thẳng thay vì giấu sau một nút "Vì sao lại thế?": với
   mã như need_login hay geo_blocked máy không làm gì được, một cái nút ở đó chỉ
   thêm một lần bấm để đọc đúng câu người dùng cần đọc — và người không phải
   dân IT ít khi bấm vào một nút mang dáng câu hỏi.

   Danh sách này phải ĐỦ. Thiếu một mã thì màn hình lỗi chỉ còn nút "Thử lại từ
   đầu" — người dùng không biết rằng có một cách sửa đích danh. Nguồn của từng mã
   (đã grep `fix_action` toàn bộ srtgen/):
     * srtgen/stages/s0_fetch.py (FIX_*) và s1_audio.py: update_ytdlp,
       install_ytdlp, install_ffmpeg, check_network, check_link, check_file,
       need_login, geo_blocked, video_too_long, free_space, none;
     * srtgen/web/jobs.py `FIX_ACTIONS_EMITTED`: restart_job (app bị đóng khi
       việc còn đang chạy), reinstall (thiếu thư viện của một chặng), check_file;
     * srtgen/web/server.py `_DOCTOR_FIX_ACTIONS` (nút trong "Kiểm tra máy"):
       update_ytdlp, install_ffmpeg, download_model, install_uv.
   "none", "" và mọi mã lạ: không nút, không lời khuyên — fixActionFor() trả
   null, và renderError() luôn có sẵn nút "Thử lại từ đầu" làm đường lui. */
const FIX_ACTIONS = {
  update_ytdlp: {
    kind: 'server', action: 'update_ytdlp', label: 'Cập nhật yt-dlp giúp tôi',
    advice: 'YouTube vừa đổi cách hoạt động nên phần tải video trong máy (yt-dlp) đã cũ.',
    steps: ['Bấm nút “Cập nhật yt-dlp giúp tôi” bên dưới — thường mất chưa tới một phút.',
            'Xong thì bấm “Thử lại từ đầu” rồi bấm Bắt đầu lần nữa.']
  },
  install_ytdlp: {
    kind: 'server', action: 'update_ytdlp', label: 'Cài yt-dlp giúp tôi',
    advice: 'Máy chưa có phần tải video từ YouTube (yt-dlp).',
    steps: ['Bấm nút “Cài yt-dlp giúp tôi” bên dưới — không cần mật khẩu quản trị.',
            'Xong thì dán lại link và bấm Bắt đầu.']
  },
  install_ffmpeg: {
    kind: 'server', action: 'install_ffmpeg', label: 'Cài ffmpeg giúp tôi',
    advice: 'Máy chưa có ffmpeg — phần dùng để đọc file video và tách lấy tiếng.',
    steps: ['Bấm nút “Cài ffmpeg giúp tôi” bên dưới. Tool tải về thư mục riêng của chương trình, không cần mật khẩu quản trị.',
            'Xong thì chạy lại video vừa nãy.']
  },
  download_model: {
    kind: 'server', action: 'download_model', label: 'Tải model gỡ băng giúp tôi',
    advice: 'Máy chưa có model gỡ băng (phần máy dùng để nghe tiếng Trung).',
    steps: ['Bấm nút “Tải model gỡ băng giúp tôi” bên dưới. Nặng khoảng 0,5–3 GB, tải một lần rồi dùng mãi.',
            'Đứt mạng giữa chừng cũng không sao: lần sau tải tiếp từ chỗ dở.']
  },
  install_uv: {
    kind: null,
    advice: 'Chương trình thiếu bộ công cụ nền được cài kèm lúc đầu. Phần này tool không tự dựng lại được từ bên trong.',
    steps: ['Đóng chương trình SrtGen.',
            'Bấm đúp file CaiDat.command trong thư mục cài đặt — nó chạy tiếp từ đúng bước đang thiếu, không làm lại từ đầu.',
            'Mở lại chương trình rồi bấm “Kiểm tra máy” trong tab Cài đặt.']
  },
  check_network: {
    kind: 'retry', label: 'Thử lại',
    advice: 'Máy không kết nối được tới nơi chứa video. Việc này tool không tự sửa được, bạn kiểm tra giúp:',
    steps: ['Mở thử một trang web bất kỳ (ví dụ youtube.com) xem máy có vào mạng được không.',
            'Nếu đang bật VPN hoặc dùng mạng công ty/trường học, thử tắt VPN hoặc đổi sang mạng khác (ví dụ phát Wi-Fi từ điện thoại).',
            'Mạng chập chờn thì đợi vài phút rồi bấm “Thử lại”.']
  },
  check_link: {
    kind: 'focus-source', label: 'Sửa lại đường dẫn',
    advice: 'Link hoặc đường dẫn này không mở ra được video.',
    steps: ['Mở thử link trong trình duyệt: video có bị xoá, bị để “riêng tư”, hay link bị thiếu chữ khi sao chép không.',
            'Xem được thì sao chép lại nguyên link từ thanh địa chỉ, dán vào ô rồi bấm Bắt đầu.']
  },
  check_file: {
    kind: 'pick-file', label: 'Chọn file khác',
    advice: 'Chương trình không đọc được file này.',
    steps: ['Mở thử file bằng trình phát video trên máy. Không phát được nghĩa là file bị hỏng hoặc tải về chưa xong — hãy lấy lại bản khác.',
            'Phát được bình thường thì bấm “Chọn file khác” và chọn lại đúng file đó.']
  },
  free_space: {
    kind: 'reveal', label: 'Mở thư mục lưu',
    advice: 'Ổ đĩa sắp hết chỗ trống. Một video 40 phút cần vài GB trống trong lúc xử lý.',
    steps: ['Xoá bớt file không cần (nhớ dọn cả Thùng rác), hoặc chuyển video cũ trong thư mục kết quả sang ổ khác.',
            'Xong thì chạy lại video vừa nãy.']
  },
  need_login: {
    kind: null,
    advice: 'Video này yêu cầu đăng nhập mới xem được, nên tool không tải về được. Việc này tool không tự làm được.',
    steps: ['Tải video về máy bằng tài khoản của bạn.',
            'Kéo thả chính file đó vào ô ở màn hình đầu rồi bấm Bắt đầu.']
  },
  geo_blocked: {
    kind: null,
    advice: 'Video bị chặn ở khu vực của bạn nên máy không tải được. Việc này tool không tự làm được.',
    steps: ['Nhờ người ở khu vực khác tải giúp file video.',
            'Kéo thả file đó vào ô ở màn hình đầu rồi bấm Bắt đầu.']
  },
  video_too_long: {
    kind: null,
    advice: 'Video dài hơn giới hạn an toàn của tool — thường là do dán nhầm link buổi phát trực tiếp.',
    steps: ['Kiểm tra lại link: có đúng là tập phim/video bạn cần không.',
            'Nếu đúng là video dài thật, hãy cắt thành nhiều phần rồi chạy từng phần.']
  },
  // jobs.py: chương trình bị đóng khi việc còn chạy. Câu báo lỗi của máy chủ
  // nói đúng chữ "Bấm “Chạy lại”", nên nút phải mang đúng tên đó.
  restart_job: { kind: 'restart', label: 'Chạy lại' },
  // jobs.py: chương trình bị đóng khi đang CHẠY LẠI một phim (ví dụ sau khi sửa
  // bảng tên riêng). Phải đi qua /rerun trên đúng việc cũ, KHÔNG tạo việc mới:
  // nguồn của lần chạy lại thường là bản tải lên đã bị dọn sau bước tách tiếng.
  // Thiếu mục này thì màn hình lỗi chỉ còn nút "Thử lại từ đầu" — ngõ cụt, vì
  // bấm vào là về ô dán link trống trơn.
  rerun_job: {
    kind: 'rerun', label: 'Chạy lại',
    advice: 'Lần chạy lại trước bị dở dang vì chương trình bị đóng. Những bước đã xong vẫn còn trong thư mục làm việc.',
    steps: ['Bấm “Chạy lại” để làm tiếp từ đúng bước còn dở. Không cần file video gốc.']
  },
  // jobs.py: thiếu thư viện của một bước. Không tự sửa được từ trong app —
  // phải chạy lại bộ cài, và bộ cài chạy tiếp từ đúng bước còn thiếu.
  reinstall: {
    kind: 'guide', action: 'open_guide', label: 'Mở hướng dẫn cài đặt',
    advice: 'Chương trình đang thiếu một phần thư viện cần cho bước này, thường là do lần cài trước bị ngắt giữa chừng.',
    steps: ['Đóng chương trình SrtGen.',
            'Bấm đúp lại file CaiDat.command trong thư mục cài đặt — nó chỉ cài nốt phần còn thiếu, không làm lại từ đầu.',
            'Mở lại chương trình và bấm Bắt đầu; những bước đã chạy xong vẫn được dùng lại.']
  }
};

/** Mục FIX_ACTIONS của một mã, hoặc null khi không có gì để dựng ("none", "",
 *  mã lạ). Dùng hasOwnProperty chứ không `FIX_ACTIONS[key]` trần: một mã lạ tên
 *  "constructor" hay "toString" sẽ vớ phải hàm của Object.prototype. */
function fixActionFor(key) {
  const k = String(key === null || key === undefined ? '' : key).trim();
  if (!k || k === 'none') return null;
  return Object.prototype.hasOwnProperty.call(FIX_ACTIONS, k) ? FIX_ACTIONS[k] : null;
}

/** Khối "Cách xử lý" dựng từ `advice` + `steps`; null khi mã không có lời khuyên. */
function adviceBox(fix, cls) {
  if (!fix || !fix.advice) return null;
  return h('div', { class: 'advice ' + (cls || ''), role: 'note' },
    icon('info'),
    h('div', {},
      h('div', { class: 'advice-title', text: 'Cách xử lý' }),
      h('p', { class: 'advice-text', text: fix.advice }),
      fix.steps && fix.steps.length
        ? h('ol', { class: 'advice-steps' }, ...fix.steps.map((step) => h('li', { text: step })))
        : null));
}

/* --------------------------------------------------------------------------
   2. Gọi máy chủ
   -------------------------------------------------------------------------- */

class ApiError extends Error {
  constructor(message, opts) {
    super(message);
    Object.assign(this, { detail: '', fixAction: 'none', status: 0 }, opts || {});
  }
}

/**
 * Đổi một phản hồi lỗi của máy chủ thành ApiError mang câu tiếng Việt.
 *
 * Tách riêng vì có HAI đường gọi máy chủ: fetch (api) và XMLHttpRequest (tải
 * file video lên — fetch không báo được tiến độ tải lên). Hai đường tự đọc lỗi
 * mỗi đường một kiểu thì sớm muộn sẽ lệch: bản cũ của đường tải lên đọc
 * `data.message`, trong khi máy chủ gói lỗi thành {"error": {"message"}} — câu
 * 400/413 của máy chủ vì thế không bao giờ tới được mắt người dùng.
 *
 * `fallbacks` là câu thay thế theo mã trạng thái khi thân phản hồi không có câu
 * nào đọc được (ví dụ kết nối bị cắt trước khi máy chủ kịp trả JSON).
 */
function apiErrorFrom(status, text, path, fallbacks) {
  let data = null;
  if (text) { try { data = JSON.parse(text); } catch (_) { data = null; } }
  if (status === 404 && data === null) {
    return new ApiError('Bản chương trình đang chạy chưa có chức năng này.', { status: 404, detail: path });
  }
  // Máy chủ gói lỗi thành {"error": {message, fix_action, ...}}; bản cũ trả
  // thẳng {message}. Nhận cả hai, nếu không người dùng sẽ thấy "[object Object]".
  const errObj = data && typeof data.error === 'object' && data.error ? data.error : null;
  const fallback = (fallbacks && fallbacks[status]) ||
    (status === 413 ? 'Dữ liệu gửi lên quá lớn nên chương trình không nhận.' : '') ||
    'Chương trình gặp trục trặc khi xử lý yêu cầu (mã ' + status + ').';
  const msg = (errObj && (errObj.message || errObj.detail)) ||
              (data && (data.message || data.detail)) ||
              (data && typeof data.error === 'string' ? data.error : '') ||
              fallback;
  return new ApiError(String(msg), {
    status,
    detail: (errObj && (errObj.detail_text || errObj.detail)) ||
            (data && data.detail_text) || (data === null ? String(text || '').slice(0, 4000) : ''),
    fixAction: (errObj && errObj.fix_action) || (data && data.fix_action) || 'none'
  });
}

async function api(path, opts) {
  opts = opts || {};
  const init = { method: opts.method || 'GET', headers: {} };
  if (opts.body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(opts.body);
  }
  if (opts.signal) init.signal = opts.signal;

  let res;
  try {
    res = await fetch(path, init);
  } catch (err) {
    throw new ApiError(
      'Không liên lạc được với chương trình SrtGen. Hãy kiểm tra xem cửa sổ chương trình còn đang chạy không, rồi thử lại.',
      { detail: String(err && err.message || err) }
    );
  }

  const text = await res.text();
  if (!res.ok) throw apiErrorFrom(res.status, text, path);
  let data = null;
  if (text) { try { data = JSON.parse(text); } catch (_) { data = null; } }
  return data === null ? {} : data;
}

/* --------------------------------------------------------------------------
   3. Thông báo nổi và hộp thoại
   -------------------------------------------------------------------------- */

const TOAST_ICON = { ok: 'ok', err: 'err', warn: 'warn', info: 'info' };

function toast(message, kind, ms) {
  kind = kind || 'info';
  const node = h('div', { class: 'toast ' + kind, role: 'status' },
    icon(TOAST_ICON[kind] || 'info'),
    h('div', { text: message })
  );
  $('#toasts').appendChild(node);
  setTimeout(() => {
    node.style.transition = 'opacity .2s';
    node.style.opacity = '0';
    setTimeout(() => node.remove(), 220);
  }, ms || (kind === 'err' ? 7000 : 4000));
}

/**
 * Hộp thoại riêng thay cho alert()/confirm(). Trả về Promise<string|null> —
 * giá trị của nút được bấm, hoặc null nếu đóng bằng Esc / bấm ra ngoài.
 */
function dialog(opts) {
  return new Promise((resolve) => {
    const buttons = opts.buttons || [{ id: 'ok', label: 'Đã hiểu', primary: true }];
    let done = false;

    const finish = (value) => {
      if (done) return;
      done = true;
      document.removeEventListener('keydown', onKey, true);
      back.remove();
      resolve(value);
    };
    const onKey = (ev) => {
      if (ev.key === 'Escape') { ev.stopPropagation(); finish(null); }
    };

    const box = h('div', { class: 'modal', role: 'dialog', 'aria-modal': 'true' },
      h('h2', { class: 'modal-title ' + (opts.kind || '') },
        opts.kind ? icon(opts.kind === 'err' ? 'err' : opts.kind === 'warn' ? 'warn' : 'ok') : null,
        opts.title || 'Thông báo'),
      h('div', { class: 'modal-body' },
        ...(Array.isArray(opts.body) ? opts.body
            : [h('p', { text: String(opts.body || '') })]),
        opts.detail ? h('details', { class: 'disclosure disclosure-flat' },
          h('summary', {}, 'Chi tiết kỹ thuật'),
          h('div', { class: 'disclosure-body' }, h('pre', { class: 'log log-sm', text: opts.detail }))
        ) : null
      ),
      h('div', { class: 'modal-actions' },
        ...buttons.map((b) => h('button', {
          type: 'button',
          class: 'btn ' + (b.primary ? 'btn-primary' : b.danger ? 'btn-danger' : 'btn-ghost'),
          onclick: () => finish(b.id)
        }, b.label))
      )
    );

    const back = h('div', { class: 'modal-back', onclick: (ev) => { if (ev.target === back) finish(null); } }, box);
    document.getElementById('modal-root').appendChild(back);
    document.addEventListener('keydown', onKey, true);
    const first = box.querySelector('.btn-primary') || box.querySelector('.btn');
    if (first) first.focus();
  });
}

function confirmBox(title, message, okLabel, danger) {
  return dialog({
    title, body: message, kind: 'warn',
    buttons: [
      { id: 'no', label: 'Quay lại' },
      { id: 'yes', label: okLabel || 'Đồng ý', primary: !danger, danger: !!danger }
    ]
  }).then((v) => v === 'yes');
}

/** Mọi chỗ bắt lỗi đều đi qua đây, để người dùng không bao giờ thấy traceback. */
function reportError(err, title) {
  const isApi = err instanceof ApiError;
  return dialog({
    title: title || 'Có trục trặc',
    kind: 'err',
    body: isApi ? err.message : 'Có lỗi ngoài dự tính. Hãy thử lại; nếu vẫn vậy, gửi phần chi tiết kỹ thuật cho người hỗ trợ.',
    detail: isApi ? (err.detail || '') : String(err && err.stack || err)
  });
}

/* --------------------------------------------------------------------------
   4. Trạng thái chung + lưu nhớ giữa các lần mở
   -------------------------------------------------------------------------- */

const store = {
  get(key, fallback) {
    try {
      const raw = localStorage.getItem('srtgen.' + key);
      return raw === null ? fallback : JSON.parse(raw);
    } catch (_) { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem('srtgen.' + key, JSON.stringify(value)); } catch (_) { /* chế độ riêng tư */ }
  },
  del(key) { try { localStorage.removeItem('srtgen.' + key); } catch (_) {} }
};

const state = {
  settings: {},          // bản cấu hình lấy từ máy chủ
  models: MODELS.slice(),
  workDir: '',
  job: null,             // job đang theo dõi
  source: null,          // {kind:'url'|'path'|'file', value, file, label, meta}
  uploading: null,       // {xhr, cancel} trong lúc đưa file video lên máy chủ
  maxMediaBytes: 0,      // giới hạn máy chủ báo; 0 = chưa biết, dùng mặc định
  healthPromise: null,   // lời gọi /api/health đang chạy/đã xong — xem loadHealth()
  files: [],             // tab Kiểm tra file
  // items = phim đã có bảng tên (GET /api/names -> movies); others = phim đã
  // chạy trong app mà chưa có bảng; readFailed = chưa đọc được bảng đang có,
  // lúc đó cấm lưu vì lưu một bảng rỗng là xoá mất bảng cũ.
  names: { videoId: '', entries: [], items: [], others: [], loaded: false, dirty: false,
           exists: false, readFailed: false,
           // corrupt = file names.json CÓ mà HỎNG (máy chủ đọc ra bảng rỗng): hiện
           // khung vàng thay cho "bảng đang trống". rerun = công việc để chạy lại
           // sau khi lưu; rerunFor = mã phim mà lời mời chạy lại đang nói tới.
           corrupt: false, corruptMessage: '', backupName: '', path: '', rerun: null, rerunFor: '' }
};

/* --------------------------------------------------------------------------
   5. Tab và giao diện chung
   -------------------------------------------------------------------------- */

function switchTab(name) {
  $$('.tab').forEach((btn) => {
    const on = btn.dataset.tab === name;
    btn.classList.toggle('is-active', on);
    btn.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  $$('.panel').forEach((panel) => {
    const on = panel.id === 'tab-' + name;
    panel.classList.toggle('is-active', on);
    panel.hidden = !on;
  });
  // Bàn làm việc chạy ở chế độ "khung cửa sổ cố định" (trang không cuộn, bảng
  // tự cuộn). CSS nhận ra chế độ đó bằng :has(#tab-edit.is-active); lớp này là
  // đường lùi cho engine không có :has().
  document.documentElement.classList.toggle('app-fixed', name === 'edit');
  store.set('tab', name);
  if (name === 'names') {
    // Lần đầu: nạp cả danh sách lẫn bảng. Những lần sau chỉ làm mới danh sách
    // phim (phim vừa chạy xong phải hiện ra) — KHÔNG nạp lại bảng, vì bảng có
    // thể đang có phần người dùng gõ dở mà chưa lưu.
    if (!state.names.loaded) loadNamesIndex();
    else loadNamesIndex({ noLoad: true, keepStatus: true, select: state.names.videoId });
  }
  if (name === 'settings' && !state.settings.__loaded) loadSettings();
  if (name === 'edit') refreshEditEntry();
  if (name !== 'edit' && typeof stopPlay === 'function') stopPlay();
}

function applyTheme(mode) {
  if (mode === 'light' || mode === 'dark') document.documentElement.setAttribute('data-theme', mode);
  else document.documentElement.removeAttribute('data-theme');
  store.set('theme', mode || 'auto');
}

function setConnPill(kind, text) {
  const pill = $('#conn-pill');
  if (!text) { pill.hidden = true; return; }
  pill.hidden = false;
  pill.className = 'pill pill-' + kind;
  pill.textContent = text;
}

/* --------------------------------------------------------------------------
   6. TAB 1 — chọn nguồn
   -------------------------------------------------------------------------- */

function setSource(src) {
  state.source = src;
  const chip = $('#source-chip');
  if (!src) {
    show(chip, false);
    $('#btn-start').disabled = true;
    $('#start-note').textContent = 'Dán link hoặc chọn file trước, rồi bấm Bắt đầu.';
    return;
  }
  $('#chosen-name').textContent = src.label;
  $('#chosen-meta').textContent = src.meta || '';
  show(chip, true);
  $('#btn-start').disabled = false;
  $('#start-note').textContent = 'Máy sẽ chạy qua nhiều bước và tự làm hết. Bạn có thể làm việc khác trong lúc chờ.';
}

function readSourceInput() {
  let raw = $('#source-url').value.trim();
  // Hai link dính liền (dán hai lần) -> chỉ lấy link đầu, và sửa luôn ô nhập cho người dùng thấy.
  const doubled = raw.match(/^(https?:\/\/\S+?)(?=https?:\/\/)/i);
  if (doubled) { raw = doubled[1]; $('#source-url').value = raw; }
  if (!raw) { if (state.source && state.source.kind !== 'file') setSource(null); return; }
  if (looksLikeUrl(raw)) {
    setSource({ kind: 'url', value: normalizeSourceUrl(raw), label: raw, meta: 'Link video — máy sẽ tải phần tiếng về trước.' });
  } else if (looksLikePath(raw)) {
    setSource({ kind: 'path', value: raw, label: baseName(raw), meta: 'File có sẵn trên máy — ' + raw });
  } else {
    setSource({ kind: 'url', value: raw, label: raw, meta: 'Sẽ thử coi đây là link video.' });
  }
}

/**
 * Người dùng vừa chọn / kéo thả một file video hoặc âm thanh.
 *
 * Kiểm loại file và dung lượng NGAY Ở ĐÂY, trước khi bấm Bắt đầu: đưa lên 2 GB
 * mất cả phút, bị từ chối ở byte cuối cùng là mất công vô ích. Bị từ chối thì
 * nói rõ vì sao và chỉ đường khác (dán đường dẫn file — khỏi phải tải lên).
 */
async function chooseFile(file) {
  if (!file) return;
  if (state.uploading) {
    toast('Đang đưa một file vào chương trình. Chờ xong, hoặc bấm “Huỷ” ở thanh tiến trình trước đã.', 'warn', 7000);
    return;
  }
  const ext = fileExt(file.name);
  if (MEDIA_EXTS.indexOf(ext) < 0) {
    $('#file-input').value = '';
    await dialog({
      title: 'Chưa nhận loại file này',
      kind: 'warn',
      body: [
        h('p', { text: '“' + file.name + '” ' + (ext ? 'là file ' + ext + ', ' : 'không có đuôi file, ') +
          'chương trình chưa đọc được loại này.' }),
        h('p', { text: 'Các loại được nhận: ' + mediaTypesText() + '.' }),
        h('p', { class: 'field-help', text: 'Nếu là video tải từ nơi khác về, bạn có thể đổi sang mp4 bằng phần mềm chuyển đổi rồi chọn lại.' })
      ]
    });
    return;
  }
  if (!file.size) {
    $('#file-input').value = '';
    await dialog({ title: 'File trống', kind: 'warn', body: '“' + file.name + '” không có dữ liệu (0 byte). Hãy chọn đúng file video hoặc âm thanh.' });
    return;
  }
  // Chờ /api/health một chút (tối đa 2 giây) để so với đúng mức máy chủ đang áp,
  // chứ không phải mức mặc định 4 GiB lúc trang vừa mở.
  await healthReady(2000);
  const limit = maxMediaBytes();
  if (file.size > limit) {
    $('#file-input').value = '';
    await dialog({
      title: 'File quá nặng để đưa vào',
      kind: 'warn',
      body: [
        h('p', { text: '“' + file.name + '” nặng ' + humanSize(file.size) + ', vượt mức ' + humanSize(limit) +
          ' mà chương trình nhận qua đường chọn file.' }),
        h('p', { text: 'Cách làm: dán thẳng đường dẫn của file trên máy vào ô phía trên (ví dụ /Users/ban/Movies/phim.mp4). ' +
          'Như vậy chương trình đọc thẳng file, không phải đưa lên, và không bị giới hạn này.' }),
        h('p', { class: 'field-help', text: 'Trên máy Mac: bấm chuột phải vào file trong Finder, giữ phím Option rồi chọn “Copy … as Pathname”.' })
      ]
    });
    return;
  }
  setSource({
    kind: 'file', file, value: file.name, label: file.name,
    meta: humanSize(file.size) + ' — bấm Bắt đầu thì file được đưa vào chương trình (có thanh tiến trình), rồi mới xử lý.'
  });
  $('#source-url').value = '';
}

/** Gắn xử lý kéo thả cho một ô. `onFiles` nhận mảng File. */
function wireDropzone(zone, onFiles, opts) {
  opts = opts || {};
  let depth = 0;
  const over = (on) => zone.classList.toggle('is-over', on);

  ['dragenter', 'dragover'].forEach((name) => zone.addEventListener(name, (ev) => {
    ev.preventDefault();
    if (name === 'dragenter') depth++;
    over(true);
  }));
  zone.addEventListener('dragleave', (ev) => { ev.preventDefault(); if (--depth <= 0) { depth = 0; over(false); } });
  zone.addEventListener('drop', (ev) => {
    ev.preventDefault();
    depth = 0; over(false);
    const files = Array.from((ev.dataTransfer && ev.dataTransfer.files) || []);
    if (files.length) { onFiles(files); return; }
    // Kéo một đoạn text (ví dụ link từ thanh địa chỉ) cũng phải nhận.
    const text = ev.dataTransfer && ev.dataTransfer.getData('text');
    if (text && opts.onText) opts.onText(text.trim());
  });

  // Dán vào vùng thả. Nếu đích dán là chính ô nhập bên trong vùng thì ĐỨNG NGOÀI:
  // sự kiện paste nổ TRƯỚC khi trình duyệt chèn chữ, nên đặt value ở đây rồi trình
  // duyệt chèn thêm lần nữa = link bị dán hai lần dính liền (lỗi người dùng đã gặp).
  // Ô nhập đã có listener 'input' lo phần đọc giá trị.
  zone.addEventListener('paste', (ev) => {
    const t = ev.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
    const text = (ev.clipboardData || window.clipboardData).getData('text');
    if (text && opts.onText) { ev.preventDefault(); opts.onText(text.trim()); }
  });
}

/* --------------------------------------------------------------------------
   7. TAB 1 — bảng 8 bước
   -------------------------------------------------------------------------- */

/** Tìm định nghĩa bước theo tên máy chủ gửi lên. Chấp nhận "s2", "asr",
 *  "s2_asr", "S2 ASR"… vì mỗi chặng tự đặt tên hơi khác nhau. */
function stepDefFor(raw) {
  const key = String(raw || '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
  if (!key) return null;
  for (const def of STEP_DEFS) if (def.key === key) return def;
  for (const def of STEP_DEFS) if (def.alias.indexOf(key) >= 0) return def;
  // "s2_asr" -> thử từng mảnh
  for (const part of key.split('_')) {
    for (const def of STEP_DEFS) if (def.key === part || def.alias.indexOf(part) >= 0) return def;
  }
  return null;
}

function newJobState(id, extra) {
  return Object.assign({
    id,
    status: 'running',
    title: '',
    steps: STEP_DEFS.map((def) => ({
      key: def.key, name: def.name, why: def.why, slow: def.slow,
      status: 'waiting', progress: 0, eta: null, message: '', startedAt: 0, elapsed: 0
    })),
    current: null,
    progress: 0,
    eta: null,
    startedAt: Date.now(),
    log: [],
    result: null,
    error: null
  }, extra || {});
}

function stepByKey(job, key) {
  for (const st of job.steps) if (st.key === key) return st;
  return null;
}

/** Bước máy chủ nhắc tới mà bản dự phòng không có -> chèn thêm, không bỏ qua. */
function ensureStep(job, rawName, label) {
  const def = stepDefFor(rawName);
  if (def) {
    const found = stepByKey(job, def.key);
    if (found) return found;
  }
  const key = def ? def.key : String(rawName || 'khac').toLowerCase();
  const created = {
    key,
    name: label || (def && def.name) || String(rawName || 'Bước khác'),
    why: (def && def.why) || '',
    slow: !!(def && def.slow),
    status: 'waiting', progress: 0, eta: null, message: '', startedAt: 0, elapsed: 0
  };
  job.steps.push(created);
  return created;
}

function renderSteps() {
  const job = state.job;
  const list = $('#steps');
  if (!job) { clear(list); return; }
  clear(list);

  const total = job.steps.length;
  job.steps.forEach((st, i) => {
    const badgeIcon = st.status === 'done' ? 'ok'
      : st.status === 'error' ? 'err'
      : st.status === 'running' ? 'refresh'
      : st.status === 'skipped' ? 'skip' : 'wait';

    const li = h('li', { class: 'step is-' + st.status },
      h('div', { class: 'step-badge' }, icon(badgeIcon, st.status === 'running' ? 'spin' : '')),
      h('div', { class: 'step-body' },
        h('div', { class: 'step-name' }, 'Bước ' + (i + 1) + '/' + total + ': ' + st.name),
        st.why ? h('div', { class: 'step-why', text: st.why }) : null,
        st.message ? h('div', { class: 'step-msg', text: st.message }) : null,
        st.status === 'running' && st.eta
          ? h('div', { class: 'step-eta' }, icon('clock'), 'Còn khoảng ' + humanDuration(st.eta))
          : null,
        st.status === 'skipped' ? h('div', { class: 'step-msg', text: 'Bước này không cần chạy lần này.' }) : null
      ),
      h('div', { class: 'step-time', text: st.status === 'done' && st.elapsed ? clockText(st.elapsed) : '' })
    );

    if (st.status === 'running') {
      const fill = h('div', {
        class: 'bar-fill' + (st.progress > 0 ? '' : ' indeterminate'),
        style: 'width:' + Math.round((st.progress || 0) * 100) + '%'
      });
      li.appendChild(h('div', { class: 'bar step-bar' }, fill));
    }

    // Bước gỡ băng: nói thẳng là lâu, và nói rõ có thể bỏ đi làm việc khác.
    if (st.slow && (st.status === 'running' || st.status === 'waiting')) {
      const guess = st.eta ? humanDuration(st.eta) : humanDuration(estimateAsrSeconds());
      li.appendChild(h('div', { class: 'step-note' }, icon('clock'),
        h('div', {}, h('b', {}, 'Bước này lâu nhất, khoảng ' + guess + '. '),
          'Bạn có thể để máy chạy và làm việc khác, không cần ngồi chờ. ' +
          'Đóng tab cũng không sao — công việc vẫn chạy tiếp, mở lại là thấy.')));
    }

    list.appendChild(li);
  });
}

/** Ước tính thời gian gỡ băng khi máy chủ chưa gửi con số thật. */
function estimateAsrSeconds() {
  const model = currentModelId();
  const table = { 'large-v3-turbo': 12 * 60, 'large-v3': 35 * 60, medium: 10 * 60, small: 5 * 60 };
  return table[model] || 15 * 60;
}

function renderRunHead() {
  const job = state.job;
  if (!job) return;
  const running = job.steps.find((s) => s.status === 'running');
  const doneCount = job.steps.filter((s) => s.status === 'done' || s.status === 'skipped').length;
  const total = job.steps.length;
  const pct = job.progress > 0 ? job.progress : (doneCount + (running ? (running.progress || 0) : 0)) / total;

  $('#run-title').textContent = job.title
    ? job.title
    : (running ? running.name : 'Đang chuẩn bị…');

  const bits = [];
  bits.push(running ? 'Bước ' + (job.steps.indexOf(running) + 1) + '/' + total : doneCount + '/' + total + ' bước xong');
  if (job.eta) bits.push('còn khoảng ' + humanDuration(job.eta));
  else if (running && running.eta) bits.push('còn khoảng ' + humanDuration(running.eta));
  const elapsed = (Date.now() - job.startedAt) / 1000;
  if (elapsed > 20) bits.push('đã chạy ' + clockText(elapsed));
  $('#run-sub').textContent = bits.join(' · ');

  $('#run-pct').textContent = Math.round(Math.min(1, Math.max(0, pct)) * 100) + '%';
  $('#run-bar').style.width = (Math.min(1, Math.max(0, pct)) * 100) + '%';
}

let runTicker = 0;
function startTicker() {
  stopTicker();
  runTicker = setInterval(() => { if (state.job && state.job.status === 'running') renderRunHead(); }, 1000);
}
function stopTicker() { if (runTicker) { clearInterval(runTicker); runTicker = 0; } }

function appendLog(text, level) {
  const job = state.job;
  if (!job) return;
  const line = (level && level !== 'info' ? '[' + level + '] ' : '') + text;
  job.log.push(line);
  if (job.log.length > 800) job.log.splice(0, job.log.length - 800);
  const box = $('#log');
  box.appendChild(document.createTextNode(line + '\n'));
  while (box.childNodes.length > 800) box.removeChild(box.firstChild);
  if ($('#log-follow').checked) box.scrollTop = box.scrollHeight;
}

/* --------------------------------------------------------------------------
   8. TAB 1 — vòng đời job + SSE
   -------------------------------------------------------------------------- */

function showCreateScreen(which) {
  show($('#create-input'), which === 'input');
  show($('#create-run'), which === 'run');
  show($('#create-done'), which === 'done');
  show($('#create-error'), which === 'error');
}

function currentModelId() {
  return store.get('model', setting(['model', 'asr.model'], 'large-v3-turbo'));
}

function collectOptions() {
  const ai = $('#opt-ai').checked;
  const translate = $('#opt-translate').checked;
  const demucs = $('#opt-demucs').checked;
  return {
    model: currentModelId(),
    profile: store.get('profile', String(setting(['profile', 'default_profile'], 'drama'))),
    // `ai` bật và `no_ai` tắt là hai câu khác nhau với bên chạy việc: thiếu vế
    // sau thì bỏ tick ô "Nhờ AI" không có tác dụng gì.
    ai: ai,
    no_ai: !ai,
    translate: translate,
    no_translate: !translate,
    demucs: demucs,
    ass_export: $('#opt-ass').checked
  };
}

async function startJob() {
  if (!state.source || state.uploading) return;
  const btn = $('#btn-start');
  btn.disabled = true;

  const src = state.source;
  const options = collectOptions();
  try {
    let created;
    if (src.kind === 'file') {
      await healthReady(3000);
      created = await uploadAndCreate(src.file, options);
      if (!created) return;               // người dùng tự bấm Huỷ
    } else {
      created = await api('/api/jobs', {
        method: 'POST',
        body: { source: src.value, source_kind: src.kind, mode: 'run', options }
      });
    }
    beginJob(created, '', src.label);
  } catch (err) {
    await reportError(err, src.kind === 'file' ? 'Chưa đưa được file vào chương trình' : 'Không bắt đầu được');
  } finally {
    btn.disabled = !state.source || !!state.uploading;
  }
}

/** Máy chủ đã nhận việc: chuyển sang màn hình 10 bước và nghe tiến trình.
 *  Dùng chung cho Bắt đầu, file tải lên và nút "Chạy lại". */
function beginJob(created, title, label) {
  const info = unwrapJob(created) || {};
  const id = info.id || info.job_id || (created && (created.id || created.job_id));
  if (!id) throw new ApiError('Chương trình không trả về mã công việc nên không theo dõi được tiến trình.');

  state.job = newJobState(String(id), { title: info.title || title || '', source: label || '' });
  store.set('lastJob', String(id));
  clear($('#log'));
  showCreateScreen('run');
  renderSteps();
  renderRunHead();
  startTicker();
  if (info.steps || info.status) applyState(info);
  connectEvents(String(id));
}

/**
 * Đưa file video/âm thanh lên máy chủ theo ĐÚNG hợp đồng tải lên:
 * multipart/form-data với đúng HAI trường —
 *   file    : nội dung file (nhị phân), kèm tên gốc chỉ để làm tiêu đề;
 *   options : chuỗi JSON, cùng dạng trường `options` của đường dán link.
 * Không gửi thêm trường nào khác: máy chủ ghi luồng ra đĩa theo khối và chỉ đọc
 * đúng hai trường này.
 *
 * Dùng XMLHttpRequest chứ không dùng fetch: fetch không báo được đã gửi đi bao
 * nhiêu byte, mà video 2 GB mất cả phút mới đưa lên xong — không có thanh tiến
 * trình thì người dùng tưởng máy treo, bấm lại, hoặc tắt luôn tab.
 *
 * Trả về Promise<data | null>; null = người dùng tự bấm Huỷ (không phải lỗi).
 */
function uploadAndCreate(file, options) {
  // Chặn file quá nặng TRƯỚC khi gửi byte nào, theo mức `max_media_bytes` mà
  // /api/health báo. Không chặn ở đây thì máy chủ trả 413 ngay khi thấy
  // Content-Length — nhưng lúc đó trình duyệt vẫn đang đẩy dữ liệu, kết nối bị
  // cắt ngang và XMLHttpRequest thường chỉ báo "lỗi mạng" chung chung (onerror),
  // câu tiếng Việt của máy chủ không bao giờ tới được mắt người dùng.
  const limit = maxMediaBytes();
  if (file && file.size > limit) {
    return Promise.reject(new ApiError(mediaTooBigText(file, limit), {
      status: 413,
      detail: 'Chặn ở trình duyệt: ' + file.size + ' byte > max_media_bytes = ' + limit + ' byte.'
    }));
  }
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append('file', file, file.name);
    form.append('options', JSON.stringify(options || {}));

    const xhr = new XMLHttpRequest();
    const ui = uploadPanel(file);
    let cancelled = false;
    state.uploading = { xhr, cancel: () => { cancelled = true; xhr.abort(); } };
    ui.onCancel(async () => {
      const ok = await confirmBox(
        'Huỷ đưa file vào?',
        'Phần đã đưa lên sẽ bị bỏ; file gốc trên máy của bạn không bị đụng tới. Muốn làm lại thì bấm Bắt đầu lần nữa.',
        'Huỷ đưa file', true
      );
      if (ok && state.uploading && state.uploading.xhr === xhr) state.uploading.cancel();
    });
    const finish = () => {
      if (state.uploading && state.uploading.xhr === xhr) state.uploading = null;
      ui.close();
    };

    xhr.open('POST', '/api/jobs');
    xhr.upload.onprogress = (ev) => ui.progress(ev.loaded, ev.lengthComputable ? ev.total : file.size);
    xhr.upload.onload = () => ui.sent();
    xhr.onload = () => {
      finish();
      const text = xhr.responseText || '';
      if (xhr.status >= 200 && xhr.status < 300) {
        let data = {};
        try { data = JSON.parse(text || '{}'); } catch (_) { data = {}; }
        resolve(data);
        return;
      }
      // Mức máy chủ đang áp có thể vừa đổi: hỏi lại để lần chọn file sau chặn
      // đúng ngay ở trình duyệt.
      if (xhr.status === 413) { state.healthPromise = null; loadHealth(); }
      // 400 (sai loại file) / 413 (quá nặng): máy chủ trả câu tiếng Việt trong
      // {"error": {"message"}} — hiện ĐÚNG câu đó, không tự bịa câu khác.
      reject(apiErrorFrom(xhr.status, text, '/api/jobs', {
        400: 'Chương trình không nhận file “' + file.name + '”. Các loại được nhận: ' + mediaTypesText() + '.',
        413: 'File “' + file.name + '” nặng ' + humanSize(file.size) + ', vượt mức chương trình nhận. ' +
             'Hãy dán thẳng đường dẫn file trên máy vào ô phía trên thay vì chọn file.'
      }));
    };
    xhr.onerror = () => {
      finish();
      reject(new ApiError(
        'Kết nối với chương trình bị ngắt khi đang đưa file vào. Có thể chương trình vừa bị đóng, ' +
        'hoặc file vượt mức chương trình nhận. Cách chắc ăn nhất: dán thẳng đường dẫn của file trên máy ' +
        'vào ô phía trên — chương trình đọc thẳng file, không phải đưa lên.',
        { detail: 'XMLHttpRequest error khi POST /api/jobs (' + humanSize(file.size) + ')' }
      ));
    };
    xhr.onabort = () => {
      finish();
      if (cancelled) {
        toast('Đã huỷ đưa file vào. File gốc trên máy của bạn vẫn nguyên.', 'info', 6000);
        resolve(null);
      } else {
        reject(new ApiError('Việc đưa file vào bị ngắt giữa chừng. Bạn bấm Bắt đầu để thử lại.'));
      }
    };
    xhr.send(form);
  });
}

/**
 * Khung tiến trình tải lên: thanh chạy + "1,2 GB / 2 GB · còn khoảng 1 phút"
 * + nút Huỷ. Chỉ tính "còn bao lâu" sau vài giây đầu, vì tốc độ đo trong một
 * giây đầu tiên nhảy lung tung và một con số sai còn tệ hơn không có số.
 */
function uploadPanel(file) {
  const box = $('#upload-box');
  const bar = $('#upload-bar');
  const wrap = $('#upload-bar-wrap');
  const cancelBtn = $('#btn-upload-cancel');
  const started = Date.now();
  let cancelHandler = null;

  $('#upload-title').textContent = 'Đang đưa “' + file.name + '” vào chương trình…';
  $('#upload-sub').textContent = '0 B / ' + humanSize(file.size);
  $('#upload-pct').textContent = '0%';
  bar.style.width = '0%';
  wrap.setAttribute('aria-valuenow', '0');
  cancelBtn.disabled = false;
  cancelBtn.onclick = () => { if (cancelHandler) cancelHandler(); };
  $('#btn-clear-source').disabled = true;
  $('#start-note').textContent = 'Đang đưa file vào chương trình — xong bước này máy mới bắt đầu xử lý. Đừng đóng tab.';
  show(box, true);

  return {
    onCancel(fn) { cancelHandler = fn; },
    progress(loaded, total) {
      total = total || file.size || 1;
      const frac = Math.max(0, Math.min(1, loaded / total));
      bar.style.width = (frac * 100).toFixed(1) + '%';
      // Làm tròn xuống: chỉ hiện 100% khi đã gửi hết thật.
      const pct = Math.floor(frac * 100);
      $('#upload-pct').textContent = pct + '%';
      wrap.setAttribute('aria-valuenow', String(pct));
      const secs = (Date.now() - started) / 1000;
      const bits = [humanSize(loaded) + ' / ' + humanSize(total)];
      if (secs >= 3 && loaded > 0 && frac < 1) bits.push('còn khoảng ' + humanDuration((total - loaded) / (loaded / secs)));
      $('#upload-sub').textContent = bits.join(' · ');
    },
    sent() {
      bar.style.width = '100%';
      $('#upload-pct').textContent = '100%';
      wrap.setAttribute('aria-valuenow', '100');
      $('#upload-title').textContent = 'Đã đưa file lên xong — đang chờ chương trình nhận việc…';
      $('#upload-sub').textContent = humanSize(file.size);
      // Gửi hết rồi thì máy chủ có thể đã tạo việc; huỷ lúc này là nói dối.
      cancelBtn.disabled = true;
    },
    close() {
      show(box, false);
      cancelBtn.onclick = null;
      $('#btn-clear-source').disabled = false;
      if (state.source) setSource(state.source);
    }
  };
}

/* ---- SSE, có tự kết nối lại ---- */

let sse = null;
let sseRetry = 0;
let sseTimer = 0;

function closeEvents() {
  if (sse) { try { sse.close(); } catch (_) {} sse = null; }
  if (sseTimer) { clearTimeout(sseTimer); sseTimer = 0; }
  sseRetry = 0;
}

function connectEvents(jobId) {
  closeEvents();
  let source;
  try {
    source = new EventSource('/api/jobs/' + encodeURIComponent(jobId) + '/events');
  } catch (_) {
    scheduleReconnect(jobId);
    return;
  }
  sse = source;

  const names = ['state', 'snapshot', 'stage', 'step', 'progress', 'log', 'done', 'result',
                 'error', 'failed', 'cancelled', 'closed', 'created', 'ping'];
  // `addEventListener('error')` nghe CẢ HAI thứ: sự kiện "error" do máy chủ gửi
  // (MessageEvent, có `data`) và sự kiện mất kết nối của chính EventSource (Event
  // trơn, không có `data`). Không tách hai thứ ra thì mạng chập chờn một giây là
  // màn hình nhảy sang "Không hoàn thành được" trong khi công việc vẫn chạy ở nền.
  names.forEach((name) => source.addEventListener(name, (ev) => {
    if (typeof ev.data !== 'string') return;       // mất kết nối: onerror bên dưới lo
    onServerEvent(name, ev.data);
  }));
  source.onmessage = (ev) => onServerEvent(null, ev.data);

  source.onopen = () => {
    sseRetry = 0;
    show($('#reconnect-banner'), false);
    setConnPill('run', 'Đang chạy');
  };

  source.onerror = () => {
    // EventSource tự thử lại, nhưng nó không lấy lại được trạng thái đã lỡ.
    // Vì vậy tự đóng, hỏi lại trạng thái đầy đủ, rồi mở kết nối mới.
    if (!state.job || state.job.id !== jobId) return;
    if (state.job.status !== 'running') { closeEvents(); return; }
    show($('#reconnect-banner'), true);
    setConnPill('warn', 'Mất kết nối');
    try { source.close(); } catch (_) {}
    if (sse === source) sse = null;
    scheduleReconnect(jobId);
  };
}

function scheduleReconnect(jobId) {
  if (sseTimer) return;
  const wait = Math.min(15000, 1000 * Math.pow(2, sseRetry++));
  sseTimer = setTimeout(async () => {
    sseTimer = 0;
    if (!state.job || state.job.id !== jobId) return;
    try {
      const snapshot = await api('/api/jobs/' + encodeURIComponent(jobId));
      applyState(snapshot);
      if (state.job && state.job.status !== 'running') { closeEvents(); return; }
    } catch (_) { /* máy chủ chưa lên lại — cứ thử tiếp */ }
    connectEvents(jobId);
  }, wait);
}

function onServerEvent(name, raw) {
  let data = {};
  if (raw) { try { data = JSON.parse(raw); } catch (_) { data = { text: String(raw) }; } }
  const type = name || data.type || data.event || 'state';
  if (type === 'ping' || type === 'heartbeat') return;

  switch (type) {
    case 'state': case 'snapshot': case 'job': case 'closed': case 'created':
      applyState(data); break;
    case 'stage': case 'step':
      applyStage(data); break;
    case 'progress':
      applyProgress(data); break;
    case 'log': {
      // Máy chủ gửi {line:{text,level}} kèm ảnh chụp job; bản cũ gửi thẳng text.
      const line = (data.line && typeof data.line === 'object') ? data.line : data;
      appendLog(String(line.text || line.message || ''), line.level);
      break;
    }
    case 'done': case 'result': case 'finished':
      finishJob(data.result || data); break;
    case 'error': case 'failed':
      failJob(errorOf(data)); break;
    case 'cancelled': case 'canceled':
      cancelledJob(); break;
    default:
      // Sự kiện lạ nhưng có nội dung tiến trình thì vẫn dùng được.
      if (data.stage !== undefined) applyProgress(data);
      else if (data.status) applyState(data);
  }
}

/** Bóc lớp {"job": {...}} nếu có. Máy chủ gói dữ liệu công việc vào khoá `job`
 *  ở hầu hết đường dẫn, nhưng bản cũ trả thẳng ra ngoài — nhận cả hai để giao
 *  diện không đứng im trong khi công việc vẫn chạy tốt ở nền. */
/** Lấy đúng khối lỗi {message, detail, fix_action} từ mọi dạng máy chủ gửi.
 *
 *  Sự kiện "error" qua SSE có dạng {job_id, job: {status, message, error: {...}}}:
 *  khối lỗi nằm trong `job`, KHÔNG nằm ở ngoài cùng. Bản trước đọc `data.error`,
 *  không thấy, rồi đưa nguyên `data` cho màn hình lỗi — thế là mọi lỗi thật đều
 *  hiện thành "Chương trình dừng lại vì một trục trặc chưa rõ", và phần "Chi tiết
 *  kỹ thuật" cũng biến mất. Đo được trên iMac thật: tải mô hình hỏng vì mạng mà
 *  người dùng không có một manh mối nào để biết. */
function errorOf(data) {
  if (!data || typeof data !== 'object') return {};
  const job = data.job && typeof data.job === 'object' ? data.job : null;
  const err = data.error || (job && job.error) || null;
  if (err && typeof err === 'object') {
    return Object.assign({}, err, {
      message: err.message || err.user_message || (job && job.message) || data.message || ''
    });
  }
  if (typeof err === 'string' && err) return { message: err };
  return { message: (job && job.message) || data.message || '' };
}

function unwrapJob(data) {
  if (!data || typeof data !== 'object') return null;
  if (data.job && typeof data.job === 'object') {
    const job = Object.assign({}, data.job);
    if (Array.isArray(data.log) && !Array.isArray(job.log)) job.log = data.log;
    return job;
  }
  return data;
}

/** Nạp một ảnh chụp trạng thái đầy đủ (lúc mở lại tab, hoặc sau khi nối lại). */
function applyState(raw) {
  const data = unwrapJob(raw);
  if (!data || typeof data !== 'object') return;
  const id = data.id || data.job_id || (state.job && state.job.id);
  if (!id) return;
  if (!state.job || state.job.id !== id) {
    state.job = newJobState(id);
    clear($('#log'));
  }
  const job = state.job;
  if (data.title) job.title = data.title;
  if (data.source) job.source = data.source;
  if (data.started_at) job.startedAt = Number(data.started_at) * 1000 || job.startedAt;
  if (typeof data.progress === 'number') job.progress = data.progress;
  if (data.eta !== undefined || data.eta_seconds !== undefined) job.eta = data.eta || data.eta_seconds || null;

  if (Array.isArray(data.steps) && data.steps.length) {
    // Máy chủ có danh sách bước riêng -> lấy của máy chủ, nhưng giữ tên tiếng
    // Việt và câu giải thích của mình khi máy chủ chỉ gửi mã chặng.
    job.steps = data.steps.map((rawStep) => {
      const def = stepDefFor(rawStep.key || rawStep.id || rawStep.stage || rawStep.name) || {};
      return {
        key: def.key || String(rawStep.key || rawStep.id || rawStep.stage || rawStep.name || ''),
        name: rawStep.label || rawStep.title || def.name || String(rawStep.name || ''),
        why: def.why || rawStep.why || '',
        slow: def.slow || !!rawStep.slow,
        status: normStatus(rawStep.status),
        progress: Number(rawStep.progress) || 0,
        eta: rawStep.eta || rawStep.eta_seconds || null,
        message: rawStep.message || '',
        startedAt: 0,
        elapsed: Number(rawStep.elapsed) || 0
      };
    });
  } else if (typeof data.step === 'number') {
    applyStepNumber(job, data);
  }
  if (Array.isArray(data.log) && data.log.length && job.log.length === 0) {
    data.log.forEach((line) => appendLog(typeof line === 'string' ? line : (line.text || ''), line && line.level));
  }

  const status = String(data.status || '').toLowerCase();
  if (status === 'done' || status === 'finished' || status === 'success') { finishJob(data); return; }
  if (status === 'error' || status === 'failed') { failJob(errorOf(data)); return; }
  if (status === 'cancelled' || status === 'canceled') { cancelledJob(); return; }

  job.status = 'running';
  showCreateScreen('run');
  renderSteps();
  renderRunHead();
  startTicker();
}

/** Máy chủ chỉ gửi SỐ của chặng đang chạy (0..9) chứ không gửi cả danh sách.
 *  Suy ra trạng thái từng bước: trước đó là xong, sau đó là chờ. Nhờ vậy thanh
 *  tiến trình vẫn đúng dù hai bên không cùng một cách mô tả. */
function applyStepNumber(job, data) {
  const idx = Math.max(0, Math.min(job.steps.length - 1, Number(data.step) || 0));
  const active = String(data.status || '').toLowerCase();
  const running = active === 'running' || active === 'queued' || !!data.active;

  job.steps.forEach((st, i) => {
    if (i < idx) {
      if (st.status !== 'skipped') { st.status = 'done'; st.progress = 1; }
    } else if (i === idx) {
      st.status = running ? 'running' : st.status;
      if (typeof data.step_progress === 'number') st.progress = Math.max(0, Math.min(1, data.step_progress));
      if (data.message) st.message = String(data.message);
      if (data.step_note && !st.why) st.why = String(data.step_note);
      if (data.eta !== undefined && data.eta !== null) st.eta = Number(data.eta) || null;
      if (!st.name && data.step_label) st.name = String(data.step_label);
      if (!st.startedAt && running) st.startedAt = Date.now();
      job.current = st.key;
    } else if (st.status === 'running') {
      st.status = 'waiting';
    }
  });
}

function normStatus(raw) {
  const s = String(raw || '').toLowerCase();
  if (['done', 'ok', 'finished', 'success', 'complete', 'completed'].indexOf(s) >= 0) return 'done';
  if (['running', 'active', 'busy', 'start', 'started'].indexOf(s) >= 0) return 'running';
  if (['error', 'failed', 'fail'].indexOf(s) >= 0) return 'error';
  if (['skip', 'skipped', 'disabled', 'cached'].indexOf(s) >= 0) return 'skipped';
  return 'waiting';
}

function applyStage(data) {
  const job = state.job;
  if (!job) return;
  const st = ensureStep(job, data.stage || data.key || data.name, data.label);
  const status = normStatus(data.status || 'running');

  if (status === 'running') {
    // Mọi bước trước bước đang chạy mà vẫn "chờ" nghĩa là đã bị bỏ qua
    // (ví dụ tắt AI, hoặc dùng lại kết quả cũ). Đánh dấu cho đúng.
    const idx = job.steps.indexOf(st);
    for (let i = 0; i < idx; i++) if (job.steps[i].status === 'waiting') job.steps[i].status = 'skipped';
    st.startedAt = Date.now();
    job.current = st.key;
  }
  if (status === 'done' && st.startedAt) st.elapsed = (Date.now() - st.startedAt) / 1000;
  if (status === 'done') st.progress = 1;

  st.status = status;
  if (data.message) st.message = String(data.message);
  if (data.elapsed) st.elapsed = Number(data.elapsed);
  renderSteps();
  renderRunHead();
}

function applyProgress(data) {
  const job = state.job;
  if (!job) return;
  const st = ensureStep(job, data.stage || data.key || job.current, data.label);
  if (st.status !== 'running') {
    const idx = job.steps.indexOf(st);
    for (let i = 0; i < idx; i++) if (job.steps[i].status === 'waiting') job.steps[i].status = 'skipped';
    st.status = 'running';
    st.startedAt = st.startedAt || Date.now();
    job.current = st.key;
  }
  if (typeof data.progress === 'number') st.progress = Math.max(0, Math.min(1, data.progress));
  const eta = data.eta !== undefined ? data.eta : data.eta_seconds;
  if (eta !== undefined && eta !== null) st.eta = Number(eta) || null;
  if (data.message) {
    st.message = String(data.message);
    appendLog(st.name + ': ' + st.message);
  }
  if (typeof data.total_progress === 'number') job.progress = data.total_progress;
  if (data.total_eta !== undefined) job.eta = Number(data.total_eta) || null;
  renderSteps();
  renderRunHead();
}

async function finishJob(raw) {
  const job = state.job;
  if (!job) return;
  const data = unwrapJob(raw) || {};
  job.status = 'done';
  job.steps.forEach((st) => {
    if (st.status === 'running') { st.status = 'done'; st.progress = 1; }
    else if (st.status === 'waiting') st.status = 'skipped';
  });
  job.progress = 1;
  closeEvents();
  stopTicker();
  setConnPill('ok', 'Xong');

  // Sự kiện "done" đôi khi chỉ báo xong; danh sách file đầy đủ nằm ở /result.
  let payload = null;
  try { payload = await api('/api/jobs/' + encodeURIComponent(job.id) + '/result'); }
  catch (_) { payload = null; }
  if (!payload || !(payload.files || payload.srt)) {
    payload = payload || {};
    if (data.result && typeof data.result === 'object') payload.files = data.result;
    if (!payload.findings && data.findings) payload.findings = data.findings;
    if (!payload.summary && data.summary) payload.summary = data.summary;
    if (!payload.out_dir && data.out_dir) payload.out_dir = data.out_dir;
    if (!payload.title && data.title) payload.title = data.title;
    if (!payload.video_id && data.video_id) payload.video_id = data.video_id;
  }
  job.result = payload || {};
  if (job.result.title) job.title = job.result.title;
  store.del('lastJob');
  // Nhớ lại công việc này để tab "Sửa phụ đề" mở thẳng vào được.
  store.set('editJob', { id: job.id, title: job.title || job.result.title || '' });
  renderResult(job.result);
  showCreateScreen('done');
}

function failJob(err) {
  const job = state.job;
  if (job) {
    job.status = 'error';
    const running = job.steps.find((s) => s.status === 'running');
    if (running) running.status = 'error';
    job.error = err;
  }
  closeEvents();
  stopTicker();
  setConnPill('err', 'Có lỗi');
  store.del('lastJob');
  renderError(err || {});
  showCreateScreen('error');
}

function cancelledJob() {
  const job = state.job;
  if (job) {
    job.status = 'cancelled';
    job.steps.forEach((st) => { if (st.status === 'running') st.status = 'skipped'; });
  }
  closeEvents();
  stopTicker();
  setConnPill('muted', 'Đã dừng');
  store.del('lastJob');
  toast('Đã dừng theo yêu cầu của bạn. Những bước đã xong vẫn được giữ lại, lần sau chạy sẽ nhanh hơn.', 'info', 8000);
  showCreateScreen('input');
}

async function cancelJob() {
  if (!state.job) return;
  const ok = await confirmBox(
    'Dừng công việc đang chạy?',
    'Những bước đã xong vẫn được giữ lại, nên nếu chạy lại video này thì máy sẽ tiếp tục từ chỗ dở chứ không làm lại từ đầu.',
    'Dừng lại', true
  );
  if (!ok) return;
  try {
    await api('/api/jobs/' + encodeURIComponent(state.job.id) + '/cancel', { method: 'POST' });
    cancelledJob();
  } catch (err) { await reportError(err, 'Không dừng được'); }
}

/* --------------------------------------------------------------------------
   9. TAB 1 — kết quả và lỗi
   -------------------------------------------------------------------------- */

/* Các file một lần chạy có thể sinh ra. `alias` là vì tên khoá của chặng xuất
   file (`vi_srt`, `bilingual_ass`) không trùng tên id tải về mà giao diện dùng —
   liệt kê cả hai ở đây rẻ hơn nhiều so với việc hai bên phải đổi tên cho khớp. */
const FILE_KINDS = [
  { key: 'srt', alias: [], name: 'Phụ đề tiếng Trung + pinyin', ext: '.srt',
    desc: 'File chính để giao đi, mở được bằng Aegisub và mọi trình phát.' },
  { key: 'vi', alias: ['vi_srt', 'srt_vi'], name: 'Phụ đề tiếng Việt', ext: '_vi.srt',
    desc: 'Cùng số dòng và cùng mốc thời gian với bản tiếng Trung.' },
  { key: 'ass_bilingual', alias: ['bilingual_ass', 'ass_bilingual'], name: 'Bản song ngữ để soát', ext: '_song-ngu.ass',
    desc: 'Ba tầng chữ trên một dòng — mở file này trong Aegisub khi soát cả hai ngôn ngữ.' },
  { key: 'ass', alias: [], name: 'Bản .ass một ngôn ngữ', ext: '.ass',
    desc: 'Đã đặt sẵn font đọc được chữ Hán và dấu thanh.' },
  { key: 'bundle', alias: [], name: 'Dữ liệu cho phần mềm hiển thị', ext: '.bundle.json',
    desc: 'Danh sách từng cụm chữ và pinyin, dành cho bên lập trình.' },
  { key: 'report', alias: [], name: 'Báo cáo kiểm tra', ext: '.report.html',
    desc: 'Thống kê và danh sách lỗi, mở bằng trình duyệt.' }
];

/** {khoá giao diện: {path, kind}} — `kind` là tên máy chủ dùng ở /api/download. */
function resultFiles(result) {
  const files = Object.assign({}, result.files || {});
  const out = {};
  FILE_KINDS.forEach((k) => {
    for (const name of [k.key].concat(k.alias)) {
      const path = files[name] || result[name];
      if (path && typeof path === 'string') { out[k.key] = { path, kind: name }; break; }
    }
  });
  return out;
}

function summarizeFindings(findings) {
  const out = { error: 0, warn: 0, info: 0, by_code: {} };
  (findings || []).forEach((f) => {
    const sev = f.severity || 'error';
    out[sev] = (out[sev] || 0) + 1;
    out.by_code[f.code] = (out.by_code[f.code] || 0) + 1;
  });
  return out;
}

function renderResult(result) {
  const files = resultFiles(result);
  const findings = result.findings || [];
  const sum = result.summary || summarizeFindings(findings);
  const stats = result.stats || {};
  const outDir = result.out_dir || dirName((files.srt && files.srt.path) || '');

  const cueCount = stats.cues || stats.cue_count || stats.blocks || 0;
  const bits = [];
  if (cueCount) bits.push('Đã tạo ' + plural(cueCount, 'dòng phụ đề') + '.');
  bits.push(files.vi
    ? 'Có đủ hai file: bản tiếng Trung và bản tiếng Việt.'
    : 'Mới có bản tiếng Trung — chưa có file tiếng Việt.');
  if (!sum.error) bits.push('File đạt toàn bộ quy chuẩn định dạng.');
  $('#done-summary').textContent = bits.join(' ');

  const hasErr = !!(result.has_errors || sum.error);
  show($('#done-errors'), hasErr);
  $('#done-banner').classList.toggle('banner-ok', !hasErr);
  $('#done-banner').classList.toggle('banner-warn', hasErr);
  if (hasErr) {
    $('#done-errors-title').textContent =
      'Có ' + plural(sum.error, 'chỗ chưa đạt quy chuẩn') +
      (sum.warn ? ' và ' + plural(sum.warn, 'chỗ cần liếc lại') : '') + '.';
  }

  // Nút to. "Kiểm tra và sửa" đứng đầu: soát lại quan trọng hơn tải về.
  const job = state.job;
  $('#btn-open-editor').onclick = () => openEditorForJob(job && job.id, (job && job.title) || result.title || '');
  $('#btn-open-editor').disabled = !(job && job.id);
  // Biết đường dẫn thì mở thẳng chỗ đó; không biết thì nhờ máy chủ mở thư mục
  // kết quả — nút này không bao giờ được phép trơ ra không bấm được.
  $('#btn-reveal').onclick = () => { if (outDir) revealPath(outDir); else runAction('open_output_dir', null); };
  $('#btn-reveal').disabled = false;
  $('#btn-dl-srt').onclick = () => downloadResult(files.srt);
  $('#btn-dl-srt').disabled = !files.srt;
  $('#btn-dl-vi').onclick = () => downloadResult(files.vi);
  $('#btn-dl-vi').disabled = !files.vi;
  const openReport = () => openResult(files.report);
  $('#btn-report').onclick = openReport;
  $('#btn-report').disabled = !files.report;
  $('#btn-errors-report').onclick = openReport;
  renderBackups(result);
  renderRerunCard(result);

  // Danh sách file
  const list = $('#file-list');
  clear(list);
  let any = false;
  FILE_KINDS.forEach((k) => {
    const entry = files[k.key];
    if (!entry) return;
    any = true;
    list.appendChild(h('div', { class: 'filerow' },
      icon(k.key === 'report' ? 'report' : k.key === 'vi' ? 'globe' : 'check-file'),
      h('div', { class: 'filerow-body' },
        h('div', { class: 'filerow-name', text: k.name + ' (' + k.ext + ')' }),
        h('div', { class: 'filerow-desc', text: k.desc }),
        h('div', { class: 'filerow-path', text: entry.path })
      ),
      h('button', { type: 'button', class: 'btn btn-quiet btn-sm', onclick: () => downloadResult(entry) },
        icon('download'), 'Tải về')
    ));
  });
  if (!any) list.appendChild(h('div', { class: 'emptybox', text: 'Chương trình chưa cho biết đường dẫn file. Bấm "Mở thư mục" để xem trực tiếp.' }));
}

/** Đường tải của một file kết quả. Máy chủ có gửi sẵn bảng `downloads` thì tin
 *  bảng đó, vì chỉ nó biết chắc file nào đã thật sự ghi ra đĩa. */
function downloadUrl(entry) {
  const job = state.job;
  if (!job || !entry) return '';
  const table = (job.result && job.result.downloads) || {};
  return table[entry.kind] ||
    '/api/download/' + encodeURIComponent(job.id) + '/' + encodeURIComponent(entry.kind);
}

/** Tải một file về máy bằng một thẻ <a download> tạm. Dùng chung cho nút tải
 *  kết quả và "Lưu và tải về" của trình sửa. */
function triggerDownload(url, name) {
  if (!url) return;
  const a = h('a', { href: url, download: name || '' });
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function downloadResult(entry) {
  triggerDownload(downloadUrl(entry), baseName((entry && entry.path) || ''));
}

function openResult(entry) {
  const url = downloadUrl(entry);
  if (!url) return;
  window.open(url, '_blank', 'noopener');
}

/** Mở thư mục chứa `path` bằng trình quản lý file của máy. `opts.jobId` gửi kèm
 *  để máy chủ cho mở cả thư mục riêng của công việc đó (bản mở bằng nội dung file
 *  nằm trong work/); `opts.okText` là câu báo khi mở được. */
async function revealPath(path, opts) {
  opts = opts || {};
  if (!path) { toast('Chưa biết thư mục kết quả nằm ở đâu.', 'warn'); return; }
  try {
    const body = { path };
    if (opts.jobId) body.job = opts.jobId;
    await api('/api/reveal', { method: 'POST', body });
    toast(opts.okText || 'Đã mở thư mục kết quả.', 'ok');
  } catch (err) {
    await dialog({
      title: 'Không mở được thư mục',
      kind: 'warn',
      body: [
        h('p', { text: 'Bạn có thể mở tay bằng đường dẫn sau (bấm giữ để chọn rồi sao chép):' }),
        h('pre', { class: 'log log-sm', text: path })
      ]
    });
  }
}

function renderError(err) {
  const message = err.message || err.user_message || 'Chương trình dừng lại vì một trục trặc chưa rõ.';
  const detail = err.detail || '';
  const fixKey = err.fix_action || err.fixAction || 'none';

  $('#err-title').textContent = 'Không hoàn thành được';
  $('#err-msg').textContent = message;

  const box = $('#err-detail-box');
  show(box, !!detail);
  $('#err-detail').textContent = detail;

  const fix = fixActionFor(fixKey);

  // Lời khuyên cụ thể hiện thẳng trên màn hình, ngay dưới câu báo lỗi — kể cả
  // (nhất là) với mã không có nút nào, vì khi đó đây là thứ duy nhất giúp được.
  const adviceSlot = $('#err-advice');
  if (adviceSlot) {
    clear(adviceSlot);
    const box = adviceBox(fix);
    if (box) adviceSlot.appendChild(box);
    show(adviceSlot, !!box);
  }

  const actions = $('#err-actions');
  clear(actions);

  // Mã lạ, "none", hay mã không có việc máy làm được thì không có nút đích
  // danh, nhưng nút "Thử lại từ đầu" bên dưới luôn có — màn hình lỗi không bao
  // giờ được là ngõ cụt.
  const fixIcon = { server: 'refresh', restart: 'refresh', retry: 'refresh', 'pick-file': 'folder',
                    reveal: 'folder', 'focus-source': 'link', guide: 'info' };
  if (fix && fix.kind) actions.appendChild(h('button', {
    type: 'button', class: 'btn btn-primary',
    onclick: (ev) => runFixAction(fixKey, ev.currentTarget)
  }, icon(fixIcon[fix.kind] || 'info'), fix.label));

  actions.appendChild(h('button', {
    type: 'button', class: 'btn btn-ghost',
    onclick: () => { showCreateScreen('input'); setConnPill(null, ''); }
  }, icon('refresh'), 'Thử lại từ đầu'));
}

/* ---- 9a. Bản cũ đã được cất lại, và "Chạy lại với bảng tên mới" ----
   Chủ đề của đợt này: người dùng KHÔNG BAO GIỜ được mất bản sửa tay mà không
   biết. Chặng xuất file (S9) giờ cất bản cũ thành "<tên>.truoc-<ngày giờ><đuôi>"
   trước khi ghi đè (hợp đồng H1) và báo danh sách ở `backups`. Cất mà không nói
   thì cũng như mất: người dùng mở file ra thấy công sửa cả buổi biến mất và không
   biết nó đang nằm ngay bên cạnh.

   Nút "Chạy lại" dùng POST /api/jobs/{id}/rerun (hợp đồng H3) thay cho lời
   khuyên cũ "chọn lại chính file đó rồi bấm Bắt đầu": file video tải lên đã bị
   dọn ngay sau bước tách âm thanh (đúng thiết kế) nên người dùng phải đi tìm lại
   file gốc, và cách đó tạo một công việc MỚI chạy từ đầu — trước đây ghi đè mất
   bản sửa tay. /rerun dùng lại video_id và work/ của lần chạy cũ: âm thanh và
   kết quả gỡ băng vẫn còn đó, chạy lại từ bước tách cụm (5) là đủ để bảng tên
   mới có hiệu lực. */

/** Đường dẫn các bản cũ vừa được cất lại (hàm thuần). Máy chủ có thể đặt danh
 *  sách ở `files.backups` (GET /result), ở `backups`, hoặc trong `result` của
 *  bản chụp công việc — nhận cả ba, bỏ trùng, giữ thứ tự. */
function backupPaths(result) {
  const r = result && typeof result === 'object' ? result : {};
  const out = [];
  [r.backups, r.files && r.files.backups, r.result && r.result.backups].forEach((list) => {
    if (!Array.isArray(list)) return;
    list.forEach((p) => {
      const s = typeof p === 'string' ? p.trim() : '';
      if (s && out.indexOf(s) < 0) out.push(s);
    });
  });
  return out;
}

/** Câu đầu của khối "bản cũ không mất" (hàm thuần). Một file thì nêu thẳng tên
 *  trong câu; nhiều file thì câu nói số lượng, tên từng file nằm ở danh sách. */
function backupHeadline(paths) {
  const list = Array.isArray(paths) ? paths : [];
  if (!list.length) return '';
  if (list.length === 1) return 'Bản cũ của bạn không bị mất: đã được cất thành “' + baseName(list[0]) + '”.';
  return 'Bản cũ của bạn không bị mất: đã được cất thành ' + list.length + ' file dưới đây.';
}

function renderBackups(result) {
  const box = $('#done-backups');
  if (!box) return;
  const paths = backupPaths(result);
  show(box, paths.length > 0);
  if (!paths.length) return;
  $('#done-backups-title').textContent = backupHeadline(paths);
  const list = $('#done-backups-list');
  clear(list);
  if (paths.length > 1) paths.forEach((p) => list.appendChild(h('li', {}, h('code', { text: baseName(p) }))));
  show(list, paths.length > 1);
  const folder = dirName(paths[0]);
  $('#done-backups-where').textContent = folder && folder !== paths[0]
    ? 'Nằm ngay cạnh file mới, trong thư mục: ' + folder : '';
  const job = state.job;
  $('#btn-backups-reveal').onclick = () => revealPath(paths[0], {
    jobId: job && job.id, okText: 'Đã mở thư mục chứa bản cũ.'
  });
}

/** Thẻ "Chạy lại với bảng tên mới" trên màn hình kết quả. Chỉ hiện khi biết
 *  video_id — thiếu nó thì máy chủ không biết dùng lại thư mục work/ nào. */
function renderRerunCard(result) {
  const card = $('#done-rerun');
  if (!card) return;
  const job = state.job;
  const videoId = String((result && result.video_id) || '');
  show(card, !!(job && job.id && videoId));
  if (!job || !job.id || !videoId) return;
  $('#btn-rerun-done').onclick = (ev) => rerunJob(job.id, {
    btn: ev.currentTarget, videoId, title: job.title || (result && result.title) || '', label: job.source || ''
  });
  $('#btn-done-names').onclick = () => openNamesFor(videoId);
}

const RERUN_FROM_STEP = 5;   // bước tách cụm từ: bảng tên có hiệu lực từ đây
const RERUN_MIN_STEP = 1;    // hợp đồng H3: from_step trong 1..9
const RERUN_MAX_STEP = 9;

/** Thời điểm tạo của một công việc, dạng số giây, để so mới/cũ. Máy chủ gửi số
 *  (time.time()) nhưng bản cũ có thể gửi chuỗi ngày — nhận cả hai. */
function jobTime(j) {
  const t = j && (j.created_at || j.started_at || j.finished_at);
  const n = Number(t);
  if (isFinite(n) && n > 0) return n;
  const d = Date.parse(String(t || ''));
  return isFinite(d) ? d / 1000 : 0;
}

/**
 * Chọn công việc để chạy lại cho một phim (hàm thuần, kiểm bằng node).
 * Trả {job, busy}: `busy` = một lần chạy của CHÍNH phim này còn đang chạy (máy
 * chủ sẽ từ chối 409); `job` = lần chạy đã dừng gần nhất để gọi /rerun. Ưu tiên
 * lần đã chạy XONG: nó chắc chắn có đủ các bước trước bước 5 trên đĩa, còn lần
 * hỏng giữa chừng có thể chưa có.
 */
function pickRerunJob(jobs, videoId) {
  const id = String(videoId || '');
  const out = { job: null, busy: null };
  if (!id || !Array.isArray(jobs)) return out;
  const mine = jobs
    .filter((j) => j && typeof j === 'object' && String(j.video_id || '') === id && !isActionJob(j) && isPipelineJob(j))
    .sort((a, b) => jobTime(b) - jobTime(a));
  out.busy = mine.find(isActiveJob) || null;
  const idle = mine.filter((j) => !isActiveJob(j));
  out.job = idle.find((j) => String(j.status || '').toLowerCase() === 'done') || idle[0] || null;
  return out;
}

/** Bước để chạy tiếp một công việc HỎNG bằng /rerun: đúng bước bị hỏng (các bước
 *  trước đó đã nằm trên đĩa). 0 = không chạy tiếp được theo cách này — hỏng ngay
 *  ở bước lấy video thì trên đĩa chưa có gì để dùng lại. Hàm thuần. */
function rerunStepForFailed(snap) {
  if (!snap || !snap.video_id) return 0;
  const step = Math.floor(Number(snap.step));
  if (!isFinite(step) || step < RERUN_MIN_STEP) return 0;
  return Math.min(RERUN_MAX_STEP, step);
}

function postRerun(jobId, fromStep) {
  return api('/api/jobs/' + encodeURIComponent(jobId) + '/rerun', {
    method: 'POST', body: { from_step: fromStep || RERUN_FROM_STEP }
  });
}

/**
 * Trước khi chạy lại: không để phần đang gõ dở bị bỏ quên. Trả false khi người
 * dùng muốn quay lại.
 *   * Bảng tên chưa lưu: lần chạy lại chỉ đọc bảng trên đĩa, nên phải lưu trước
 *     thì bảng mới có tác dụng.
 *   * Trình sửa đang có phần chưa lưu của chính phim này: phần ĐÃ lưu sẽ được cất
 *     lại thành bản .truoc-, còn phần chưa lưu thì không nằm ở file nào để cất.
 */
async function guardBeforeRerun(videoId, jobId) {
  if (state.names.dirty && videoId && state.names.videoId === videoId) {
    const choice = await dialog({
      title: 'Bảng tên chưa lưu', kind: 'warn',
      body: 'Bạn đã sửa bảng tên của phim này mà chưa bấm Lưu. Lần chạy lại chỉ dùng bảng đã lưu trên máy, ' +
            'nên phần vừa sửa sẽ chưa có tác dụng.',
      buttons: [
        { id: 'back', label: 'Quay lại' },
        { id: 'go', label: 'Chạy lại luôn' },
        { id: 'save', label: 'Lưu rồi chạy lại', primary: true }
      ]
    });
    if (choice === 'save') { if (!(await saveNames())) return false; }
    else if (choice !== 'go') return false;
  }
  const sameDoc = editor.loaded && (editor.jobId === jobId || (videoId && editor.videoId === videoId));
  if (sameDoc) commitActiveCell();
  if (sameDoc && editor.dirty) {
    const choice = await dialog({
      title: 'Phụ đề đang sửa chưa lưu', kind: 'warn',
      body: 'Trong tab “Sửa phụ đề” bạn còn phần sửa chưa bấm Lưu. Hãy lưu trước: phần đã lưu sẽ được cất lại ' +
            'nguyên vẹn thành bản “.truoc-…” khi chạy lại, còn phần chưa lưu thì không có file nào để cất.',
      buttons: [
        { id: 'back', label: 'Quay lại' },
        { id: 'save', label: 'Lưu rồi chạy lại', primary: true }
      ]
    });
    if (choice !== 'save') return false;
    await saveDoc();
    if (editor.dirty) return false;
  }
  return true;
}

/** Trình sửa đang mở đúng phụ đề sắp chạy lại: đóng nó. Để nguyên thì bảng trên
 *  màn hình là bản CŨ, và bấm Lưu ở đó sau khi chạy lại xong là ghi bản cũ đè lên
 *  bản mới mà không qua bước cất .truoc- nào. Chỉ đóng khi không còn phần chưa
 *  lưu (guardBeforeRerun đã lo việc đó). */
function unloadEditorFor(jobId, videoId) {
  if (!editor.loaded || editor.dirty) return;
  if (editor.jobId !== jobId && !(videoId && editor.videoId === videoId)) return;
  if (typeof stopPlay === 'function') stopPlay();
  editor.loaded = false;
  editor.cues = [];
  editor.baseline = [];
  editor.jobId = '';
  hideDraftBar();
  hideSavedNote();
  setDraftStatus('', '');
  clear($('#cue-rows'));
  show($('#edit-work'), false);
  show($('#edit-empty'), true);
  refreshEditEntry();
}

/** Máy chủ đã nhận lần chạy lại: chuyển sang màn hình tiến trình và theo dõi. */
function startRerunView(created, jobId, opts) {
  opts = opts || {};
  unloadEditorFor(jobId, opts.videoId || '');
  hideNamesRerun();
  switchTab('create');
  beginJob(created, opts.title || '', opts.label || opts.title || '');
}

/**
 * Nút "Chạy lại với bảng tên mới" (màn hình kết quả, tab Tên riêng).
 * opts: {btn, videoId, title, label, fromStep}. Trả true khi đã bắt đầu chạy.
 */
async function rerunJob(jobId, opts) {
  opts = opts || {};
  if (!jobId) return false;
  const videoId = String(opts.videoId || '');
  if (!(await guardBeforeRerun(videoId, jobId))) return false;
  const watchingOther = !!(state.job && state.job.status === 'running' && state.job.id !== jobId);
  const choice = await dialog({
    title: opts.dialogTitle || 'Chạy lại phim này với bảng tên mới?',
    body: [
      h('p', { text: opts.dialogBody || ('Máy sẽ tách cụm từ, phiên âm và dịch lại cả phim theo bảng tên riêng đang lưu. ' +
        'Không cần file video gốc và không phải gỡ băng lại, nên nhanh hơn nhiều so với chạy từ đầu.') }),
      h('p', { text: 'Các file phụ đề đang có — kể cả chỗ bạn đã sửa tay và bấm Lưu — sẽ được cất lại thành ' +
        'bản có chữ “.truoc-<ngày giờ>” ngay trong cùng thư mục trước khi ghi file mới. Không mất gì cả.' }),
      watchingOther ? h('p', { class: 'field-help', text: 'Màn hình tiến trình đang theo dõi một việc khác. ' +
        'Việc đó vẫn chạy tiếp trong chương trình; màn hình sẽ chuyển sang theo dõi lần chạy lại này.' }) : null
    ],
    buttons: [{ id: 'no', label: 'Quay lại' }, { id: 'yes', label: 'Chạy lại', primary: true }]
  });
  if (choice !== 'yes') return false;
  const restore = busyButton(opts.btn, 'Đang chạy lại…');
  try {
    const created = await postRerun(jobId, opts.fromStep || RERUN_FROM_STEP);
    restore();
    startRerunView(created, jobId, { videoId, title: opts.title || '', label: opts.label || '' });
    toast(opts.startToast || 'Đang chạy lại từ bước tách cụm từ. Bản cũ sẽ được cất lại chứ không bị ghi đè.', 'info', 8000);
    return true;
  } catch (err) {
    restore();
    if (err instanceof ApiError && (err.status === 400 || err.status === 409)) {
      await dialog({ title: 'Chưa chạy lại được', kind: 'warn', body: err.message, detail: err.detail || '' });
    } else {
      await reportError(err, 'Không chạy lại được');
    }
    return false;
  }
}

/** Nút "Mở bảng tên của phim này": sang tab Tên riêng, chọn đúng phim. */
async function openNamesFor(videoId) {
  const id = String(videoId || '');
  const changing = !!id && state.names.videoId !== id;
  if (changing && !(await confirmNamesLeave('Mở bảng tên của phim khác'))) return;
  const wasLoaded = state.names.loaded;
  if (changing) {
    state.names.videoId = id;
    state.names.dirty = false;
    store.set('namesVideo', id);
  }
  switchTab('names');
  if (wasLoaded && changing) await loadNamesIndex({ select: id });
}

/* Trang hướng dẫn (hợp đồng H7): máy chủ dựng HTML từ HUONG-DAN.md ở /guide.
   Trước đây nút nhờ hệ điều hành mở file .md thô; máy không có app đọc .md thì
   báo "Đã mở" mà không có gì hiện ra. Mở bằng một thẻ <a target=_blank> thật chứ
   không window.open: window.open(…, 'noopener') luôn trả null nên không phân
   biệt được bị chặn hay không, còn một cú bấm vào <a> thì trình duyệt không chặn. */
const GUIDE_URL = '/guide';

/** Mở tab Hướng dẫn ngay trong app, nhảy tới đúng mục nếu có.
 *
 *  Trước đây nút này mở /guide ở một thẻ mới của trình duyệt: người không rành
 *  máy tính bị lạc sang một trang khác, đọc xong không biết quay về app bằng
 *  cách nào. Bản đầy đủ để in vẫn còn ở /guide (nút trong tab Cài đặt). */
function openGuide(section) {
  switchTab('guide');
  const target = section ? document.getElementById(section) : null;
  if (target) {
    target.scrollIntoView({ block: 'start' });
    const link = document.querySelector('.guide-layout .toc-link[href="#' + section + '"]');
    if (link) link.click();
  } else {
    window.scrollTo(0, 0);
  }
}

/* ---- 9b. Năm việc hành động (POST /api/actions/{key}) ---- */

/** Đưa một nút vào trạng thái "đang chạy" và trả về hàm khôi phục nguyên trạng.
 *  Giữ lại đúng các nút con cũ (kể cả biểu tượng) chứ không chép textContent:
 *  chép chữ thì nút mất luôn biểu tượng sau lần bấm đầu tiên. */
function busyButton(btn, busyText) {
  if (!btn) return () => {};
  const kept = Array.prototype.slice.call(btn.childNodes);
  btn.disabled = true;
  clear(btn);
  btn.appendChild(icon('refresh', 'spin'));
  btn.appendChild(document.createTextNode(' ' + (busyText || 'Đang chạy…')));
  return () => {
    btn.disabled = false;
    clear(btn);
    kept.forEach((node) => btn.appendChild(node));
  };
}

/**
 * Hộp theo dõi một việc chạy lâu: thanh chạy + dòng đang làm gì + nhật ký.
 *
 * Vì sao phải có: với người dùng không phải dân IT, bấm một nút rồi màn hình
 * đứng im là dấu hiệu "hỏng" — họ sẽ bấm thêm vài lần nữa, rồi bỏ cuộc. Thà
 * hiện một thanh chạy chưa biết bao lâu còn hơn im lặng.
 */
function progressModal(opts) {
  opts = opts || {};
  const bar = h('div', { class: 'bar-fill indeterminate', style: 'width:40%' });
  const logBox = h('pre', { class: 'log log-sm' });
  const stepLine = h('div', { class: 'jobmodal-step', text: 'Đang bắt đầu…' });
  const resultBox = h('div', { class: 'jobmodal-result' });
  resultBox.hidden = true;
  const actions = h('div', { class: 'modal-actions' });
  const titleNode = h('h2', { class: 'modal-title' }, icon('refresh', 'spin'),
    h('span', { text: opts.title || 'Đang chạy…' }));

  const box = h('div', { class: 'modal jobmodal', role: 'dialog', 'aria-modal': 'true' },
    titleNode,
    h('div', { class: 'modal-body' },
      opts.note ? h('p', { class: 'jobmodal-note', text: opts.note }) : null,
      stepLine,
      h('div', { class: 'bar bar-lg' }, bar),
      logBox,
      resultBox
    ),
    actions
  );
  let visible = false;
  // Bấm ra ngoài hoặc Esc = ẩn hộp đi, KHÔNG dừng việc: việc đang chạy ở máy
  // chủ, đóng cái hộp lại không huỷ được nó, và nói ngược lại là nói dối.
  const back = h('div', { class: 'modal-back', onclick: (ev) => { if (ev.target === back) detach(); } }, box);
  const onKey = (ev) => { if (ev.key === 'Escape' && visible) { ev.stopPropagation(); detach(); } };
  const detach = () => {
    if (!visible) return;
    back.remove();
    visible = false;
    document.removeEventListener('keydown', onKey, true);
  };
  const setActions = (nodes) => { clear(actions); nodes.forEach((n) => actions.appendChild(n)); };
  const pushLog = (text) => {
    const line = String(text === null || text === undefined ? '' : text).trim();
    if (!line) return;
    logBox.appendChild(document.createTextNode(line + '\n'));
    while (logBox.childNodes.length > 400) logBox.removeChild(logBox.firstChild);
    logBox.scrollTop = logBox.scrollHeight;
  };

  setActions([h('button', { type: 'button', class: 'btn btn-ghost', onclick: detach },
    'Ẩn đi, cứ chạy tiếp')]);
  document.getElementById('modal-root').appendChild(back);
  visible = true;
  document.addEventListener('keydown', onKey, true);

  return {
    setStep(text) { stepLine.textContent = String(text || ''); },
    setProgress(value) {
      const p = Number(value);
      if (!isFinite(p)) return;
      bar.classList.remove('indeterminate');
      bar.style.width = (Math.max(0, Math.min(1, p)) * 100).toFixed(1) + '%';
    },
    log: pushLog,
    finish(kind, message, detail) {
      const bad = kind === 'err';
      titleNode.className = 'modal-title ' + (bad ? 'err' : 'ok');
      clear(titleNode);
      titleNode.appendChild(icon(bad ? 'err' : 'ok'));
      titleNode.appendChild(h('span', { text: bad ? (opts.failTitle || 'Chưa làm được') : (opts.okTitle || 'Đã xong') }));
      bar.classList.remove('indeterminate');
      bar.style.width = '100%';
      stepLine.textContent = '';
      clear(resultBox);
      resultBox.hidden = false;
      resultBox.className = 'jobmodal-result ' + (bad ? 'err' : 'ok');
      resultBox.appendChild(icon(bad ? 'err' : 'ok'));
      resultBox.appendChild(h('div', { text: String(message || '') }));
      if (detail) pushLog(detail);
      setActions([h('button', { type: 'button', class: 'btn btn-primary', onclick: detach }, 'Đóng')]);
      if (!visible) toast(String(message || ''), bad ? 'err' : 'ok', 8000);
    },
    close: detach
  };
}

/** Theo dõi một việc dài đã được máy chủ nhận ({job_id}) cho tới lúc xong.
 *  SSE là đường chính; vẫn hỏi lại trạng thái mỗi vài giây để không bao giờ
 *  đứng hình vì lỡ một sự kiện. */
function followActionJob(jobId, meta) {
  const box = progressModal({
    title: meta.title || meta.label || 'Đang chạy…',
    note: meta.note || '',
    okTitle: meta.okTitle || 'Đã xong',
    failTitle: 'Chưa làm được'
  });

  let finished = false;
  let heard = false;
  let misses = 0;
  let source = null;
  let poller = 0;

  const stop = () => {
    if (source) { try { source.close(); } catch (_) {} source = null; }
    if (poller) { clearInterval(poller); poller = 0; }
  };
  const succeed = (message) => {
    if (finished) return;
    finished = true; stop();
    box.finish('ok', message || meta.okText || 'Đã chạy xong.');
    if (meta.refreshModels) refreshModels();
  };
  const failWith = (message, detail) => {
    if (finished) return;
    finished = true; stop();
    box.finish('err', message || 'Việc này chưa chạy xong được. Bạn thử lại sau ít phút.', detail || '');
  };

  const applySnap = (raw) => {
    const job = unwrapJob(raw) || {};
    heard = true;
    if (typeof job.progress === 'number') box.setProgress(job.progress);
    if (typeof job.total_progress === 'number') box.setProgress(job.total_progress);
    const msg = job.message || (job.step && job.step.message) || '';
    if (msg) { box.setStep(String(msg)); box.log(String(msg)); }
    if (job.status === undefined || job.status === null) return;
    const status = normStatus(job.status);
    if (status === 'done') succeed(job.message || (job.result && job.result.message));
    else if (status === 'error') {
      const err = job.error && typeof job.error === 'object' ? job.error : {};
      failWith(err.message || job.message, err.detail_text || err.detail || '');
    }
  };

  const onEvent = (name, raw) => {
    let data = {};
    if (raw) { try { data = JSON.parse(raw); } catch (_) { data = { message: String(raw) }; } }
    const type = name || data.type || data.event || 'state';
    if (type === 'ping' || type === 'heartbeat') return;
    heard = true;
    if (type === 'log') {
      const line = (data.line && typeof data.line === 'object') ? data.line : data;
      box.log(line.text || line.message || '');
      return;
    }
    if (type === 'error' || type === 'failed') {
      const err = (data.error && typeof data.error === 'object') ? data.error : data;
      failWith(err.message || err.detail, err.detail_text || '');
      return;
    }
    if (type === 'cancelled' || type === 'canceled') { failWith('Việc này đã bị dừng giữa chừng.'); return; }
    if (type === 'done' || type === 'result' || type === 'finished') {
      const res = (data.result && typeof data.result === 'object') ? data.result : data;
      succeed(res.message || data.message);
      return;
    }
    applySnap(data);
  };

  try {
    source = new EventSource('/api/jobs/' + encodeURIComponent(jobId) + '/events');
    ['state', 'snapshot', 'stage', 'step', 'progress', 'log', 'done', 'result',
     'error', 'failed', 'cancelled', 'closed'].forEach((name) => {
      source.addEventListener(name, (ev) => {
        // Mất kết nối (Event trơn, không có `data`) KHÔNG phải việc bị hỏng —
        // vòng hỏi lại bên dưới vẫn theo dõi tiếp. Xem chú thích ở connectEvents.
        if (typeof ev.data !== 'string') return;
        onEvent(name, ev.data);
      });
    });
    source.onmessage = (ev) => onEvent(null, ev.data);
    source.onerror = () => { /* vòng hỏi lại bên dưới lo tiếp */ };
  } catch (_) { source = null; }

  poller = setInterval(async () => {
    if (finished) { stop(); return; }
    try {
      applySnap(await api('/api/jobs/' + encodeURIComponent(jobId)));
      misses = 0;
    } catch (_) {
      misses++;
      // Không hỏi được trạng thái mà cũng chưa nghe được gì: đừng để hộp quay
      // mãi. Nói thật là không theo dõi được, việc vẫn chạy ở nền.
      if (misses >= 4 && !heard) {
        finished = true; stop();
        box.finish('ok',
          'Chương trình đã nhận việc và đang làm ở nền, nhưng không theo dõi được chi tiết. ' +
          'Chờ vài phút rồi bấm “Kiểm tra máy” để xem đã xong chưa.');
      }
    }
  }, 3000);
}

/** Gọi một việc trong DANH SÁCH TRẮNG. Không có khoá nào khác được gửi đi. */
async function runAction(key, btn, extra) {
  const act = ACTIONS[key];
  if (!act) {
    await dialog({
      title: 'Không có việc này',
      kind: 'warn',
      body: 'Bản chương trình đang chạy không làm được việc này từ bên trong app. ' +
            'Hãy đóng chương trình rồi chạy lại file CaiDat.command trong thư mục cài đặt.'
    });
    return;
  }
  const restore = busyButton(btn, act.busy);
  try {
    const res = await api('/api/actions/' + encodeURIComponent(key), {
      method: 'POST', body: Object.assign({}, extra || {})
    });
    const jobId = res.job_id || res.jobId || (res.job && res.job.id) || '';
    if (jobId) { followActionJob(String(jobId), act); return; }
    if (res.ok === false) {
      await dialog({
        title: 'Chưa làm được', kind: 'warn',
        body: res.message || 'Việc này chưa chạy được. Bạn thử lại sau ít phút.',
        detail: res.detail || ''
      });
      return;
    }
    if (act.long) {
      await dialog({
        title: act.okTitle || 'Đã xong', kind: 'ok',
        body: res.message || act.okText || 'Đã chạy xong. Bạn thử lại được rồi.',
        detail: res.detail || ''
      });
    } else {
      toast(res.message || (act.label + ': đã xong.'), 'ok');
    }
  } catch (err) {
    await reportError(err, 'Không tự làm được việc này');
  } finally {
    restore();
  }
}

async function runFixAction(key, btn) {
  const fix = fixActionFor(key);
  if (!fix || !fix.kind) { showCreateScreen('input'); setConnPill(null, ''); return; }

  if (fix.kind === 'guide') { openGuide('g-truc-trac'); return; }
  if (fix.kind === 'focus-source') { showCreateScreen('input'); $('#source-url').focus(); $('#source-url').select(); return; }
  if (fix.kind === 'pick-file') { showCreateScreen('input'); $('#file-input').click(); return; }
  if (fix.kind === 'restart') { await restartFailedJob(btn, 'resume'); return; }
  if (fix.kind === 'rerun') { await restartFailedJob(btn, 'resume', true); return; }
  if (fix.kind === 'retry') {
    // Sau khi tải lại trang thì ô nguồn đã trống: startJob() sẽ không làm gì cả,
    // tức là một cái nút bấm vào im re. Khi đó hỏi máy chủ nguồn của việc cũ.
    if (state.source) { showCreateScreen('input'); startJob(); }
    else await restartFailedJob(btn, 'run');
    return;
  }
  if (fix.kind === 'reveal') { revealPath(pick(state.settings, 'paths.out_dir', '') || state.workDir); return; }

  // Mọi việc chạy thật đều đi qua danh sách trắng, kèm model đang chọn cho
  // đúng việc tải model.
  const extra = fix.action === 'download_model' ? { model: currentModelId() } : {};
  await runAction(fix.action, btn, extra);
}

/** File tải lên nằm ở `<work_dir>/_uploads/` và máy chủ xoá nó ngay sau khi tách
 *  xong âm thanh — chạy lại từ đường dẫn đó chắc chắn hỏng. */
function isUploadedSource(source) {
  return /(^|[\\/])_uploads[\\/]/.test(String(source || ''));
}

/**
 * Nút "Chạy lại" (restart_job) và "Thử lại" khi ô nguồn đã trống.
 *
 * Hỏi máy chủ đúng nguồn + tuỳ chọn của việc hỏng rồi gửi lại. Không đoán từ
 * trạng thái trên màn hình: sau khi tải lại trang, trình duyệt không còn nhớ
 * link hay file nào — chính vì thế mà việc bị đứt giữa chừng mới cần nút này.
 */
async function restartFailedJob(btn, mode, forceRerun) {
  const job = state.job;
  if (!job || !job.id) {
    showCreateScreen('input');
    setConnPill(null, '');
    toast('Hãy dán lại link hoặc chọn lại file, rồi bấm Bắt đầu.', 'info', 6000);
    return;
  }
  const restore = busyButton(btn, 'Đang chạy lại…');
  try {
    const snap = unwrapJob(await api('/api/jobs/' + encodeURIComponent(job.id))) || {};
    const source = String(snap.source || '').trim();
    if (forceRerun || !source || isUploadedSource(source)) {
      // File tải lên đã bị dọn ngay sau bước tách âm thanh — đúng thiết kế. Nhưng
      // âm thanh và mọi bước đã xong vẫn nằm trong work/<video_id>/, nên chạy tiếp
      // bằng /rerun từ đúng bước bị hỏng. Câu cũ ở đây bảo người dùng "chọn lại
      // chính file đó rồi bấm Bắt đầu": cách đó tạo một việc MỚI chạy từ đầu, và
      // (trước khi có bản cất .truoc-) ghi đè mất bản họ đã sửa tay.
      const step = rerunStepForFailed(snap);
      const videoId = String(snap.video_id || '');
      let why = '';
      restore();
      if (step) {
        if (!(await guardBeforeRerun(videoId, job.id))) return;
        const again = busyButton(btn, 'Đang chạy lại…');
        try {
          const created = await postRerun(job.id, step);
          startRerunView(created, job.id, { videoId, title: snap.title || '' });
          toast('Đang chạy tiếp từ bước bị dở. Những bước đã xong được dùng lại, không cần file video gốc.', 'info', 8000);
          return;
        } catch (err) {
          again();
          // 400 = trên đĩa thiếu phần của các bước trước: chỉ khi đó mới cần file gốc.
          if (!(err instanceof ApiError) || err.status !== 400) { await reportError(err, 'Không chạy lại được'); return; }
          why = err.message;
        }
      }
      await dialog({
        title: 'Cần chọn lại video',
        kind: 'warn',
        body: [
          why ? h('p', { text: why }) : null,
          h('p', { text: source
            ? 'File video bạn đưa vào lần trước đã được dọn khỏi chương trình sau khi tách phần âm thanh, ' +
              'và lần đó chưa chạy đủ xa để làm tiếp từ phần đã có. Hãy chọn lại chính file đó rồi bấm Bắt đầu.'
            : 'Chương trình không còn nhớ video lần trước lấy từ đâu. Hãy dán lại link hoặc chọn lại file rồi bấm Bắt đầu.' }),
          h('p', { class: 'field-help', text: 'Phụ đề cũ của phim này (nếu có, kể cả chỗ bạn đã sửa tay) không bị ghi đè: ' +
            'trước khi ghi file mới, chương trình cất bản cũ thành file có chữ “.truoc-<ngày giờ>” ngay cạnh nó.' })
        ]
      });
      showCreateScreen('input');
      setConnPill(null, '');
      $('#btn-pick-file').focus();
      return;
    }
    const created = await api('/api/jobs', {
      method: 'POST',
      body: { source, mode: mode || 'resume', options: snap.options || {} }
    });
    beginJob(created, snap.title || '', baseName(source) || source);
    toast('Đã chạy lại. Những bước đã xong lần trước được dùng lại nên sẽ nhanh hơn.', 'info', 7000);
  } catch (err) {
    restore();
    await reportError(err, 'Không chạy lại được');
  }
}

/* ---- khôi phục sau khi tải lại trang ---- */

const ACTIVE_STATUSES = ['running', 'queued', 'pending', 'starting'];

function isActiveJob(j) {
  return !!j && ACTIVE_STATUSES.indexOf(String(j.status || '').toLowerCase()) >= 0;
}

/** Việc "hành động" (cài ffmpeg, cập nhật yt-dlp, tải model) cũng là một Job ở
 *  máy chủ, nhưng không có "Bước 3/10" nào. Vẽ nó bằng màn hình 10 bước là nói
 *  sai với người dùng rằng máy đang gỡ băng một video. */
function isActionJob(j) {
  return !!j && String(j.mode || '').toLowerCase() === 'action';
}

/** Chỉ việc chạy pipeline (chạy mới / chạy tiếp) mới dùng màn hình 10 bước. */
function isPipelineJob(j) {
  const mode = String((j && j.mode) || 'run').toLowerCase();
  return mode === 'run' || mode === 'resume' || mode === 'rerun';
}

/** Việc mở được vào trình sửa: tin cờ `can_edit` của máy chủ nếu có. */
function canEditJob(j) {
  if (!j || isActionJob(j)) return false;
  return j.can_edit !== undefined ? !!j.can_edit : isPipelineJob(j);
}

/** Khoá ACTIONS của một việc hành động. Máy chủ đặt tiêu đề việc bằng đúng câu
 *  "Đang cài ffmpeg"… trong `_ACTION_TITLES`, trùng `title` của ACTIONS. */
function actionKeyOfJob(j) {
  const res = (j && j.result) || {};
  if (res.action && ACTIONS[res.action]) return res.action;
  const title = String((j && (j.title || j.source)) || '');
  for (const key in ACTIONS) if (ACTIONS[key].title && ACTIONS[key].title === title) return key;
  return '';
}

/** Mở lại hộp tiến trình của một việc hành động còn đang chạy. */
function resumeActionJob(j) {
  const id = String(j.id || j.job_id || '');
  if (!id) return;
  const key = actionKeyOfJob(j);
  const meta = key ? ACTIONS[key] : { title: String(j.title || j.source || 'Đang chạy…') };
  followActionJob(id, meta);
}

/** Khi mở lại tab: tìm xem có công việc nào đang chạy dở không. */
async function restoreJob() {
  let candidate = null;
  try {
    const data = await api('/api/jobs');
    const jobs = data.jobs || data.items || (Array.isArray(data) ? data : []);
    candidate = jobs.find((j) => isActiveJob(j) && isPipelineJob(j)) || null;
    // Việc cài/cập nhật/tải đang chạy: mở lại ĐÚNG hộp tiến trình của nó.
    jobs.filter((j) => isActiveJob(j) && isActionJob(j)).forEach(resumeActionJob);

    // Bản cũ của giao diện có thể đã nhớ nhầm một việc hành động làm "kết quả
    // gần nhất" — bấm "Mở lại" khi đó chỉ ra một câu lỗi khó hiểu.
    const remembered = store.get('editJob', null);
    if (remembered && remembered.id) {
      const known = jobs.find((j) => (j.id || j.job_id) === remembered.id);
      if (known && !canEditJob(known)) store.del('editJob');
    }
    // Không có việc đang chạy thì vẫn nhớ việc xong gần nhất, để trình sửa mở được.
    const lastDone = jobs.find((j) => String(j.status || '').toLowerCase() === 'done' && canEditJob(j));
    if (lastDone && !store.get('editJob', null)) {
      store.set('editJob', { id: lastDone.id || lastDone.job_id, title: lastDone.title || '' });
    }
    refreshEditEntry();
  } catch (_) { /* máy chủ chưa có đường dẫn này cũng không sao */ }

  if (!candidate) {
    const last = store.get('lastJob', null);
    if (!last) return;
    let snap;
    try {
      snap = unwrapJob(await api('/api/jobs/' + encodeURIComponent(last))) || {};
    } catch (_) { store.del('lastJob'); return; }

    if (isActionJob(snap)) { store.del('lastJob'); return; }
    if (isActiveJob(snap)) {
      candidate = snap;
    } else if (String(snap.status || '').toLowerCase() === 'error' && snap.error) {
      // Cố ý KHÔNG lọc theo mã fix_action nữa. jobs.py phát ba mã khác nhau cho
      // cùng một tình huống "ứng dụng đóng khi việc còn chạy" (check_file cho
      // việc chạy từ file tải lên, rerun_job cho việc chạy lại, restart_job cho
      // việc chạy từ link); lọc theo một mã làm hai trường hợp kia biến mất
      // không một lời. Khoá `lastJob` chỉ do beginJob đặt, nên tới được đây
      // nghĩa là chính trình duyệt này đã khởi động việc đó — đủ để hiện lại.
      // Chương trình bị đóng khi việc của CHÍNH trình duyệt này còn đang chạy.
      // Im lặng bỏ qua thì người dùng tưởng công việc biến mất; hiện màn hình
      // lỗi có nút "Chạy lại" để họ đi tiếp từ chỗ dở.
      state.job = newJobState(snap.id || last, { title: snap.title || '' });
      applyState(snap);
      // Câu chữ không nhắc tên một nút cụ thể: tuỳ mã lỗi mà màn hình dựng ra
      // "Chạy lại" hay "Chọn lại file", nói sai tên nút còn khó chịu hơn im lặng.
      toast('Lần trước chương trình bị đóng khi đang chạy dở. Màn hình dưới đây có nút để làm tiếp.', 'warn', 9000);
      return;
    } else {
      store.del('lastJob');
    }
  }
  if (!candidate) return;

  const id = candidate.id || candidate.job_id;
  state.job = newJobState(id);
  store.set('lastJob', id);
  applyState(candidate);
  connectEvents(id);
  toast('Có một công việc đang chạy dở — đã mở lại để bạn theo dõi tiếp.', 'info', 6000);
}

/* --------------------------------------------------------------------------
   10. TAB 2 — kiểm tra và sửa file .srt
   -------------------------------------------------------------------------- */

/** Bộ tách block rất đơn giản, CHỈ để hiển thị so sánh trên giao diện.
 *  Thước đo thật vẫn là validator ở máy chủ (srtgen/core/rules.py). */
function parseSrtBlocks(text) {
  const lines = String(text || '').replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n').split('\n');
  const isTime = (s) => /\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}/.test(s);
  const blocks = [];
  for (let i = 0; i < lines.length; i++) {
    if (!isTime(lines[i])) continue;
    const indexText = i > 0 ? lines[i - 1].trim() : '';
    const body = [];
    for (let j = i + 1; j < lines.length; j++) {
      if (isTime(lines[j])) break;
      if (lines[j].trim() === '' && body.length && j + 1 < lines.length && /^\s*\d+\s*$/.test(lines[j + 1] || '') && isTime(lines[j + 2] || '')) break;
      if (lines[j].trim() === '' && body.length) { break; }
      if (lines[j].trim() !== '') body.push(lines[j]);
    }
    blocks.push({ index: parseInt(indexText, 10) || blocks.length + 1, time: lines[i].trim(), lines: body });
  }
  return blocks;
}

async function addCheckFiles(fileList) {
  const wanted = fileList.filter((f) => /\.(srt|txt)$/i.test(f.name));
  const skipped = fileList.length - wanted.length;
  if (skipped) toast(plural(skipped, 'file bị bỏ qua') + ' vì không phải file .srt.', 'warn');
  if (!wanted.length) return;

  for (const file of wanted) {
    try {
      // Cùng một cửa đọc với nút "Mở file có sẵn": thử UTF-8 rồi GB18030, để
      // file bên dịch Trung Quốc gửi sang không hiện thành chữ lạ.
      const text = await readSubtitleFile(file);
      state.files.push({
        id: 'f' + Date.now() + Math.random().toString(36).slice(2, 7),
        name: file.name, size: file.size, text,
        blocks: parseSrtBlocks(text),
        status: 'new', findings: null, fixed: null, open: {}
      });
    } catch (_) {
      toast('Không đọc được file ' + file.name + '.', 'err');
    }
  }
  renderCheckFiles();
  checkAllFiles();
}

function renderCheckFiles() {
  const wrap = $('#check-files');
  clear(wrap);
  show($('#check-toolbar'), state.files.length > 0);
  if (!state.files.length) return;
  state.files.forEach((f) => wrap.appendChild(renderCheckCard(f)));
}

function renderCheckCard(f) {
  const sum = f.findings ? summarizeFindings(f.findings) : null;
  const card = h('div', {
    class: 'srtcard' + (sum ? (sum.error ? ' has-error' : ' is-clean') : '')
  });

  const meta = [plural(f.blocks.length, 'dòng phụ đề'), humanSize(f.size)];
  const badges = [];
  if (f.status === 'checking') badges.push(h('span', { class: 'pill pill-run' }, icon('refresh', 'spin'), 'Đang kiểm…'));
  if (sum) {
    if (sum.error) badges.push(h('span', { class: 'pill pill-err' }, icon('err'), plural(sum.error, 'lỗi')));
    if (sum.warn) badges.push(h('span', { class: 'pill pill-warn' }, icon('warn'), plural(sum.warn, 'cảnh báo')));
    if (!sum.error && !sum.warn) badges.push(h('span', { class: 'pill pill-ok' }, icon('ok'), 'Đạt chuẩn'));
  }
  if (f.fixed) badges.push(h('span', { class: 'pill pill-ok' }, icon('ok'), 'Đã có bản sửa'));

  card.appendChild(h('div', { class: 'srtcard-head' },
    h('div', { class: 'srtcard-title' },
      h('div', { class: 'srtcard-name', text: f.name }),
      h('div', { class: 'srtcard-meta' }, ...meta.map((m) => h('span', { text: m })))
    ),
    h('div', { class: 'srtcard-badges' }, ...badges),
    h('div', { class: 'srtcard-actions' },
      h('button', { type: 'button', class: 'btn btn-ghost btn-sm', onclick: () => checkFile(f) }, icon('ok'), 'Chỉ kiểm tra'),
      h('button', { type: 'button', class: 'btn btn-primary btn-sm', onclick: () => fixFile(f, true) }, icon('download'), 'Sửa và tải về'),
      h('button', {
        type: 'button', class: 'btn btn-ghost btn-sm',
        title: 'Mở file này sang tab “Sửa phụ đề” để sửa từng dòng và nghe lại',
        onclick: () => openCheckFileInEditor(f)
      }, icon('edit'), 'Mở vào trình sửa'),
      h('button', { type: 'button', class: 'btn btn-quiet btn-sm', 'aria-label': 'Bỏ file khỏi danh sách',
        onclick: () => { state.files = state.files.filter((x) => x !== f); renderCheckFiles(); } }, icon('trash'))
    )
  ));

  const fixedBox = renderFixedOutput(f);
  if (fixedBox) card.appendChild(fixedBox);
  if (f.findings) card.appendChild(renderFindingGroups(f));
  return card;
}

function renderFindingGroups(f) {
  const body = h('div', { class: 'srtcard-body' });
  if (!f.findings.length) {
    body.appendChild(h('div', { class: 'emptybox' },
      icon('ok'), ' ', 'File này đạt toàn bộ quy chuẩn. Không cần sửa gì.'));
    return body;
  }

  // Gom theo mã lỗi: 1351 dòng "block sai hình dạng" là bản báo cáo không ai đọc nổi.
  const groups = {};
  f.findings.forEach((fd) => { (groups[fd.code] = groups[fd.code] || []).push(fd); });

  const order = { error: 0, warn: 1, info: 2 };
  Object.keys(groups)
    .sort((a, b) => {
      const sa = order[(groups[a][0] || {}).severity] ?? 3;
      const sb = order[(groups[b][0] || {}).severity] ?? 3;
      return sa - sb || groups[b].length - groups[a].length;
    })
    .forEach((code) => {
      const items = groups[code];
      const sev = items[0].severity || 'error';
      const det = h('details', { class: 'rulegroup' });
      det.open = !!f.open[code];
      det.addEventListener('toggle', () => { f.open[code] = det.open; });

      det.appendChild(h('summary', {},
        icon(sev === 'error' ? 'err' : sev === 'warn' ? 'warn' : 'info', sev === 'error' ? 'err' : ''),
        h('span', { text: ruleLabel(items[0]) }),
        h('span', { class: 'rulegroup-code', text: code }),
        h('span', { class: 'rulegroup-count', text: SEVERITY_LABEL[sev] + ' · ' + plural(items.length, 'chỗ') })
      ));

      const list = h('ul', { class: 'findlist' });
      items.slice(0, 200).forEach((fd) => list.appendChild(renderFindingItem(f, fd)));
      if (items.length > 200) {
        list.appendChild(h('li', { class: 'finditem' },
          h('div', { class: 'compare-empty', text: 'Còn ' + (items.length - 200) + ' chỗ nữa cùng loại — sửa một lần là hết cả.' })));
      }
      det.appendChild(list);
      body.appendChild(det);
    });

  return body;
}

function renderFindingItem(f, fd) {
  const li = h('li', { class: 'finditem' });
  const panel = h('div', { class: 'compare' });
  panel.hidden = true;

  li.appendChild(h('button', {
    type: 'button', class: 'findbtn',
    onclick: () => {
      panel.hidden = !panel.hidden;
      if (!panel.hidden && !panel.dataset.filled) { fillCompare(panel, f, fd); panel.dataset.filled = '1'; }
    }
  },
    h('span', { class: 'findbtn-cue', text: fd.cue_index ? 'Dòng ' + fd.cue_index : '—' }),
    h('span', { class: 'findbtn-msg', text: fd.message || ruleLabel(fd) })
  ));
  li.appendChild(panel);
  return li;
}

/** Hai cột: bản hiện tại và bản sau khi sửa, khác nhau chỗ nào thì tô vàng. */
function fillCompare(panel, f, fd) {
  clear(panel);
  const pos = findBlockPos(f.blocks, fd.cue_index);
  const before = pos >= 0 ? f.blocks[pos] : null;
  let after = f.fixed && pos >= 0 ? f.fixed.blocks[pos] : null;
  // File ba dòng: dòng Việt đi sang file _vi.srt. Ghép lại vào cột "Sau khi sửa"
  // để người dùng thấy nó VẪN CÒN, chứ không tưởng tool đã xoá mất.
  const viBlock = after && f.fixed.viBlocks ? f.fixed.viBlocks[pos] : null;
  if (viBlock) after = { index: after.index, time: after.time, lines: after.lines.concat(viBlock.lines) };

  const grid = h('div', { class: 'compare-grid' },
    h('div', { class: 'compare-col' },
      h('div', { class: 'compare-head bad' }, icon('err'), 'Đang là'),
      before ? blockView(before, fd, after) : h('div', { class: 'compare-empty', text: 'Không tìm thấy dòng phụ đề này trong file.' })
    ),
    h('div', { class: 'compare-col good' },
      h('div', { class: 'compare-head good' }, icon('ok'), 'Sau khi sửa'),
      after ? blockView(after, null, before) : null,
      after && viBlock ? h('div', { class: 'field-help', text: 'Dòng tiếng Việt nằm trong file “' + f.fixed.viName + '” đi kèm.' }) : null,
      after ? null
            : h('div', { class: 'compare-empty' },
                h('div', { text: 'Chưa có bản sửa để so sánh.' }),
                h('div', { class: 'compare-actions' },
                  h('button', {
                    type: 'button', class: 'btn btn-ghost btn-sm',
                    onclick: async (ev) => {
                      ev.currentTarget.disabled = true;
                      await fixFile(f, false);
                      panel.dataset.filled = '';
                      fillCompare(panel, f, fd);
                      panel.dataset.filled = '1';
                    }
                  }, icon('eye'), 'Xem thử bản đã sửa')))
    )
  );
  panel.appendChild(grid);

  if (fd.line) {
    panel.appendChild(h('div', { class: 'compare-actions' },
      h('div', { class: 'field-help', text: 'Quy chuẩn: ' + ruleLabel(fd) + '.' })));
  }
}

function findBlockPos(blocks, cueIndex) {
  if (!cueIndex) return -1;
  for (let i = 0; i < blocks.length; i++) if (blocks[i].index === cueIndex) return i;
  return cueIndex - 1 >= 0 && cueIndex - 1 < blocks.length ? cueIndex - 1 : -1;
}

/** Dựng 4 dòng của một block, đánh dấu dòng vi phạm và tô chỗ khác biệt. */
function blockView(block, finding, other) {
  const box = h('div', { class: 'compare-block' });
  box.appendChild(h('span', { class: 'ln ln-num', text: String(block.index) }));
  box.appendChild(h('span', { class: 'ln ln-time', text: block.time }));
  block.lines.forEach((line, i) => {
    const cls = 'ln ' + (i === 0 ? 'ln-zh' : i === 1 ? 'ln-py' : 'ln-vi');
    const hit = finding && finding.line && finding.line.trim() === line.trim();
    const span = h('span', { class: cls + (hit ? ' is-hit' : '') });
    const counterpart = other && other.lines ? other.lines[i] : null;
    if (counterpart !== null && counterpart !== undefined && counterpart !== line) {
      markInto(span, line, counterpart);
    } else {
      span.textContent = line;
    }
    box.appendChild(span);
  });
  return box;
}

/** Tô phần khác nhau giữa hai chuỗi: bỏ đầu chung và đuôi chung, phần giữa
 *  bọc <mark>. Đủ để mắt người thấy ngay chỗ sai, không cần diff đầy đủ. */
function markInto(node, text, other) {
  const A = Array.from(text), B = Array.from(other);
  let p = 0;
  while (p < A.length && p < B.length && A[p] === B[p]) p++;
  let s = 0;
  while (s < A.length - p && s < B.length - p && A[A.length - 1 - s] === B[B.length - 1 - s]) s++;
  const head = A.slice(0, p).join('');
  const mid = A.slice(p, A.length - s).join('');
  const tail = A.slice(A.length - s).join('');
  if (head) node.appendChild(document.createTextNode(head));
  if (mid) node.appendChild(h('mark', { text: mid }));
  else if (!head && !tail) node.appendChild(document.createTextNode(text));
  if (tail) node.appendChild(document.createTextNode(tail));
}

async function checkFile(f) {
  f.status = 'checking';
  renderCheckFiles();
  try {
    // Máy chủ đọc khoá `filename`/`text` (xem `_read_upload` trong web/server.py).
    // Gửi kèm `name`/`content` để bản máy chủ cũ hơn cũng hiểu.
    const res = await api('/api/check', {
      method: 'POST',
      body: { filename: f.name, text: f.text, name: f.name, content: f.text }
    });
    f.findings = res.findings || [];
    if (res.blocks && typeof res.blocks === 'number' && res.blocks !== f.blocks.length) {
      // Máy chủ đếm chuẩn hơn bộ tách nháp ở đây; tin máy chủ cho phần con số.
      f.serverBlocks = res.blocks;
    }
    f.status = 'checked';
  } catch (err) {
    f.status = 'error';
    f.findings = null;
    await reportError(err, 'Không kiểm tra được ' + f.name);
  }
  renderCheckFiles();
}

/**
 * Phản hồi /api/fix -> những file sẽ tải về. Hàm thuần, không đụng DOM, để
 * kiểm được bằng node.
 *
 * Vì sao phải có: file bên dịch gửi sang hay là ba dòng Hán / pinyin / Việt.
 * Máy chủ tách dòng Việt ra `vi_text` (file `_vi.srt` đi kèm, cùng số khối và
 * cùng mốc thời gian). Bản cũ chỉ lưu `text` — người dùng tải về một file mất
 * sạch dòng tiếng Việt mà không ai báo. Ở đây: has_vi thì LUÔN là hai file.
 *
 * Tên file lấy của máy chủ (`filename`, `vi_filename`) để hai file đi thành cặp
 * `<tên>.srt` + `<tên>_vi.srt` — đúng thói quen đặt tên mà chính tab này và
 * trình sửa dùng để tự ghép cặp (srtNameParts). Máy chủ cũ không gửi tên thì
 * tự đặt theo cùng quy tắc. `vi_filename` rỗng hoặc null đều nghĩa là "không có".
 */
function fixDownloadPlan(sourceName, res) {
  res = res || {};
  const text = String(res.content || res.text || '');
  const stem = String(sourceName || '').replace(/\.(srt|txt)$/i, '').trim() || 'phu-de';
  const cleanName = (n) => baseName(String(n === null || n === undefined ? '' : n)).trim();

  let zhName = cleanName(res.filename);
  if (!/\.srt$/i.test(zhName) || zhName.length <= 4) zhName = stem + '_da-sua.srt';
  let viName = cleanName(res.vi_filename);
  if (!/\.srt$/i.test(viName) || viName.length <= 4 || viName === zhName) {
    viName = zhName.replace(/\.srt$/i, '') + '_vi.srt';
  }

  const viText = typeof res.vi_text === 'string' ? res.vi_text : '';
  // Máy chủ đời cũ không gửi `has_vi`: khi đó có `vi_text` là đủ.
  const saysVi = res.has_vi === true || (res.has_vi === undefined && viText.trim() !== '');
  const hasVi = saysVi && viText.trim() !== '';

  const files = [{ name: zhName, text, what: 'zh' }];
  if (hasVi) files.push({ name: viName, text: viText, what: 'vi' });
  return {
    text,
    zhName,
    viName: hasVi ? viName : '',
    viText: hasVi ? viText : '',
    hasVi,
    // Máy chủ nói có dòng Việt mà không gửi kèm nội dung: KHÔNG được coi như
    // file hai dòng bình thường — người dùng sẽ tưởng đủ rồi và xoá file gốc.
    viMissing: saysVi && !hasVi,
    files,
    setAside: normSetAside(res.set_aside),
    savedZh: String(res.path || ''),
    savedVi: hasVi ? String(res.vi_path || '') : ''
  };
}

/** `set_aside` = những dòng máy chủ không xếp được vào đâu, KHÔNG có trong file
 *  đã sửa. Hợp đồng pipeline ghi list[str], máy chủ web ghi list[{cue, text,
 *  reason}] — nhận cả hai, bỏ dòng trắng (không phải dữ liệu). */
function normSetAside(list) {
  if (!Array.isArray(list)) return [];
  return list.map((item) => {
    if (item === null || item === undefined) return null;
    if (typeof item !== 'object') return { cue: 0, text: String(item), reason: '' };
    const raw = item.text !== undefined && item.text !== null ? item.text : item.line;
    return {
      cue: Number(item.cue || item.cue_index || 0) || 0,
      text: String(raw === undefined || raw === null ? '' : raw),
      reason: String(item.reason || '')
    };
  }).filter((item) => item && item.text.trim() !== '');
}

async function fixFile(f, download) {
  try {
    if (!f.fixed) {
      const res = await api('/api/fix', {
        method: 'POST',
        body: { filename: f.name, text: f.text, name: f.name, content: f.text }
      });
      const plan = fixDownloadPlan(f.name, res);
      if (!plan.text) throw new ApiError('Chương trình không trả về nội dung đã sửa.');
      f.fixed = Object.assign(plan, {
        blocks: parseSrtBlocks(plan.text),
        viBlocks: plan.hasVi ? parseSrtBlocks(plan.viText) : null
      });
      // Máy chủ trả `findings` = lỗi CÒN LẠI của bản đã sửa, `findings_before` =
      // lỗi của bản GỐC (server.py, fix_srt_payload). Trước đây app đọc nhầm tên
      // trường: `findings_after` không tồn tại nên thành mã chết, còn `f.findings`
      // — vốn là lỗi của bản gốc, thứ huy hiệu trên thẻ đang nói tới — bị ghi đè
      // bằng danh sách rỗng của bản đã sửa. Kết quả: thẻ đổi thành "Đạt chuẩn"
      // trong khi file gốc trên đĩa vẫn nguyên lỗi, bấm "Chỉ kiểm tra" lần nữa
      // thì lỗi quay lại — hai câu trả lời trái ngược về cùng một file.
      f.findingsAfter = Array.isArray(res.findings) ? res.findings : [];
      if (Array.isArray(res.findings_before)) f.findings = res.findings_before;
    }
    if (download) downloadFixed(f);
    renderCheckFiles();
  } catch (err) {
    await reportError(err, 'Không sửa được ' + f.name);
  }
}

/** Tải bản đã sửa về máy: một file, hoặc HAI file khi bản gốc có dòng Việt. */
function downloadFixed(f) {
  const fx = f.fixed;
  if (!fx) return;
  saveTextFile(fx.zhName, fx.text);
  if (fx.hasVi) {
    // Lần tải thứ hai cách lần đầu một nhịp: vài trình duyệt bỏ qua lần tải đến
    // cùng lúc. Trình duyệt hỏi "cho phép tải nhiều file" thì phải bấm Cho phép —
    // câu nhắc dưới đây nói điều đó, và thẻ file còn nút tải riêng từng file.
    setTimeout(() => saveTextFile(fx.viName, fx.viText), 700);
    toast('Đang tải về HAI file: “' + fx.zhName + '” (Hán + pinyin) và “' + fx.viName + '” (tiếng Việt). ' +
          'Nếu trình duyệt hỏi có cho tải nhiều file không, hãy bấm Cho phép. File gốc của bạn không bị đụng tới.',
          'ok', 12000);
  } else {
    toast('Đã tải về “' + fx.zhName + '”. File gốc của bạn không bị đụng tới.', 'ok', 6000);
  }
  if (fx.viMissing) {
    toast('File gốc có dòng tiếng Việt nhưng chương trình chưa tạo được file _vi.srt. Đừng xoá file gốc — xem thông báo đỏ trên thẻ file.', 'err', 12000);
  }
  if (fx.setAside.length) {
    toast('Có ' + fx.setAside.length + ' dòng không có trong file đã sửa — xem khung vàng trên thẻ file để chép lại.', 'warn', 12000);
  }
}

/** Chép chữ vào bộ nhớ tạm; trình duyệt không cho thì chọn sẵn để bấm Cmd+C. */
async function copyText(text, selectNode) {
  try {
    await navigator.clipboard.writeText(text);
    toast('Đã chép. Dán vào đâu cũng được (Cmd+V).', 'ok');
  } catch (_) {
    if (selectNode && window.getSelection) {
      const range = document.createRange();
      range.selectNodeContents(selectNode);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    }
    toast('Trình duyệt không cho chép tự động. Phần chữ đã được chọn sẵn — bấm Cmd+C để chép.', 'warn', 8000);
  }
}

/** Khối "Bản đã sửa" trên thẻ file: nói rõ tải về mấy file, file nào chứa gì,
 *  và mọi dòng tool không xếp được (không có trong file đã sửa). Null = không có
 *  gì cần nói thêm (file hai dòng bình thường). */
function renderFixedOutput(f) {
  const fx = f.fixed;
  if (!fx) return null;
  const parts = [];

  // Huy hiệu trên thẻ nói về file GỐC. Dòng này nói về BẢN ĐÃ SỬA — hai con số
  // khác nhau, nên phải có nhãn rõ ràng, không được để người dùng tự đoán.
  const after = Array.isArray(f.findingsAfter) ? f.findingsAfter : null;
  if (after) {
    const conLai = after.filter((x) => (x.severity || 'error') === 'error').length;
    parts.push(h('div', { class: 'banner ' + (conLai ? 'banner-warn' : 'banner-ok') },
      icon(conLai ? 'warn' : 'ok'),
      h('div', {}, h('b', {
        text: conLai
          ? 'Bản đã sửa vẫn còn ' + plural(conLai, 'chỗ') + ' tool không tự sửa được.'
          : 'Bản đã sửa đạt toàn bộ quy chuẩn.'
      }), h('div', {
        class: 'banner-sub',
        text: 'File gốc trên máy bạn KHÔNG bị thay đổi. Bản đã sửa là các file tải về bên dưới.'
      }))));
  }
  const fileBtn = (name, text) => h('button', {
    type: 'button', class: 'btn btn-ghost btn-sm', onclick: () => saveTextFile(name, text)
  }, icon('download'), 'Tải “' + name + '”');

  if (fx.hasVi) {
    parts.push(h('div', { class: 'banner banner-info' }, icon('info'),
      h('div', {},
        h('b', { text: 'Bản đã sửa gồm HAI file — hãy tải và giữ cả hai trong cùng một thư mục.' }),
        h('div', { class: 'banner-sub', text: 'File gốc có thêm dòng tiếng Việt. Tool tách ra theo đúng thói quen “tên.srt” + “tên_vi.srt”; hai file cùng số dòng phụ đề và cùng mốc thời gian:' }),
        h('ul', { class: 'fixout-files' },
          h('li', {}, h('b', { text: fx.zhName }), ' — dòng Hán và dòng pinyin.'),
          h('li', {}, h('b', { text: fx.viName }), ' — dòng tiếng Việt. Thiếu file này là mất phần tiếng Việt.')),
        h('div', { class: 'fixout-actions' },
          fileBtn(fx.zhName, fx.text),
          fileBtn(fx.viName, fx.viText),
          fx.savedZh ? h('button', {
            type: 'button', class: 'btn btn-quiet btn-sm', onclick: () => revealPath(dirName(fx.savedZh))
          }, icon('folder'), 'Mở thư mục kết quả') : null),
        fx.savedZh && fx.savedVi
          ? h('div', { class: 'banner-sub', text: 'Cả hai file cũng đã được lưu sẵn trong thư mục kết quả của chương trình.' })
          : null
      )));
  }

  if (fx.viMissing) {
    parts.push(h('div', { class: 'banner banner-err' }, icon('err'),
      h('div', {},
        h('b', { text: 'Chưa có bản tiếng Việt' }),
        h('div', { class: 'banner-sub', text: 'File gốc có dòng tiếng Việt nhưng chương trình không trả về file _vi.srt. File vừa sửa chỉ có dòng Hán và pinyin. ĐỪNG xoá file gốc — phần tiếng Việt vẫn còn nguyên trong đó. Hãy thử bấm “Sửa và tải về” lần nữa; nếu vẫn vậy, báo cho người hỗ trợ.' })
      )));
  }

  if (fx.setAside.length) {
    const lines = fx.setAside.map((x) => (x.cue ? 'Dòng phụ đề ' + x.cue + ': ' : '') + x.text).join('\n');
    const pre = h('pre', { class: 'log log-sm setaside-lines', text: lines });
    parts.push(h('div', { class: 'banner banner-warn' }, icon('warn'),
      h('div', {},
        h('b', { text: 'Có ' + fx.setAside.length + ' dòng tool không biết xếp vào đâu — các dòng này KHÔNG có trong file đã sửa.' }),
        h('div', { class: 'banner-sub', text: 'Tool giữ nguyên văn ở dưới để bạn chép lại vào đúng chỗ (hoặc mở file vào trình sửa). File gốc của bạn vẫn còn đủ.' }),
        pre,
        h('div', { class: 'fixout-actions' },
          h('button', { type: 'button', class: 'btn btn-ghost btn-sm', onclick: () => copyText(lines, pre) },
            icon('report'), 'Chép các dòng này'))
      )));
  }

  return parts.length ? h('div', { class: 'fixout' }, ...parts) : null;
}

/** Ghi file xuống máy. Thêm BOM vì Aegisub và các editor trên Windows cần nó
 *  để hiện đúng chữ Hán (build-spec mục 11). */
function saveTextFile(name, text) {
  const body = text.charCodeAt(0) === 0xfeff ? text : '\ufeff' + text;
  const blob = new Blob([body], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = h('a', { href: url, download: name });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

/** Mở một file trong danh sách của tab Kiểm tra vào trình sửa.
 *
 *  File kéo thả / chọn trong trình duyệt KHÔNG mang đường dẫn, nên gửi thẳng nội
 *  dung {name, content, vi_content}. Có file tiếng Việt đi cùng (x_vi.srt) trong
 *  danh sách thì ghép cặp luôn — bấm trên file nào của cặp cũng được. */
async function openCheckFileInEditor(f) {
  const parts = srtNameParts(f.name);
  const mate = state.files.find((x) => {
    if (x === f) return false;
    const p = srtNameParts(x.name);
    return p.vi !== parts.vi && p.base.toLowerCase() === parts.base.toLowerCase();
  });
  const zh = parts.vi ? mate : f;
  const vi = parts.vi ? f : mate;
  if (!zh) {
    await dialog({
      title: 'Cần thêm file tiếng Trung',
      kind: 'warn',
      body: '“' + f.name + '” là bản tiếng Việt. Trình sửa cần file tiếng Trung đi cùng (ví dụ “' +
            parts.base + '.srt”). Hãy kéo thả thêm file đó vào danh sách rồi bấm lại.'
    });
    return;
  }
  const payload = { name: zh.name, content: zh.text };
  if (vi) payload.vi_content = vi.text;
  await openLocalDoc(payload, zh.name + (vi ? ' + ' + vi.name : ''));
}

async function checkAllFiles() {
  for (const f of state.files) if (f.status !== 'checked') await checkFile(f);
}

async function fixAllFiles() {
  if (!state.files.length) return;
  for (const f of state.files) await fixFile(f, true);
}

/* --------------------------------------------------------------------------
   11. TAB 3 — bảng tên riêng
   -------------------------------------------------------------------------- */

/* Hợp đồng chốt với máy chủ — CHỈ dạng này (dạng cũ {han, split, pinyin:[…]} và
   khoá `items` đã bỏ hẳn ở phía giao diện):
     GET  /api/names            -> {movies:[{video_id,title,count}]}
     GET  /api/names/{video_id} -> {video_id, exists, entries:[{zh,pinyin,vi}]}
     PUT  /api/names/{video_id} <- {entries:[{zh,pinyin,vi}]} -> {ok, count}

   Ba cột của bảng đúng bằng ba thứ máy chủ giữ, không hơn: thêm cột mà máy chủ
   không lưu thì người dùng gõ vào rồi mất trắng mà không biết.

   Cột "Tên tiếng Việt" là kênh DUY NHẤT để ép một tên cố định cho cả phim
   (光头强 -> "Cường đầu trọc"). Nó là một ô chữ thường: gõ tiếng Việt có dấu bình
   thường, giao diện không cắt dấu, không đổi chữ hoa. Bỏ trống thì máy tự phiên
   âm, và cùng một nhân vật có thể mang hai cái tên khác nhau ở hai đoạn phim.

   Phim CHƯA có bảng: không bật AI thì S7 không bao giờ tạo names.json, nên
   trước đây ô chọn phim trống trơn và người dùng không có cách nào tự lập bảng.
   Nay ô chọn liệt kê thêm những phim đã chạy trong app (GET /api/jobs, trường
   video_id) mà chưa có bảng; PUT vào phim đó là tạo bảng mới. */

/* Mã video hợp lệ — chép đúng VIDEO_ID_RE của server.py. Máy chủ từ chối mã khác,
   nên đừng để người dùng chọn một phim mà bấm Lưu chắc chắn hỏng. */
const NAMES_VIDEO_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;

function namesStatus(text, kind) {
  const node = $('#names-status');
  if (!node) return;
  node.className = 'savebar-status' + (kind ? ' ' + kind : '');
  node.textContent = text || '';
}

/** Số cụm của một chuỗi ngăn bằng dấu cách. Dòng Hán và dòng pinyin phải bằng
 *  nhau thì bộ ghép cụm mới dùng được tên này. */
function wordCount(text) {
  return String(text || '').trim().split(/\s+/).filter(Boolean).length;
}

function markNamesDirty() {
  state.names.dirty = true;
  namesStatus('Có thay đổi chưa lưu — bấm “Lưu bảng tên”.', '');
}

/** Phim đã chạy trong app mà chưa có bảng tên (để lập bảng mới). */
async function moviesWithoutTable(withTable) {
  const have = {};
  withTable.forEach((m) => { have[m.video_id] = true; });
  let jobs = [];
  try {
    const data = await api('/api/jobs?limit=200');
    jobs = data.jobs || (Array.isArray(data) ? data : []);
  } catch (_) { return []; }
  const out = [];
  jobs.forEach((j) => {
    const id = String(j.video_id || '');
    if (!NAMES_VIDEO_ID_RE.test(id) || have[id] || isActionJob(j)) return;
    const mode = String(j.mode || 'run').toLowerCase();
    if (mode === 'check' || mode === 'fix') return;
    have[id] = true;
    out.push({ video_id: id, title: String(j.title || '') || id, count: 0 });
  });
  return out;
}

/** Dựng ô chọn phim: nhóm "đã có bảng" và nhóm "chưa có bảng". */
function fillNamesSelect(selected) {
  const sel = $('#names-video');
  clear(sel);
  const withTable = state.names.items;
  const without = state.names.others;
  if (!withTable.length && !without.length) {
    sel.appendChild(h('option', { value: '' }, 'Chưa có phim nào'));
    sel.disabled = true;
    return;
  }
  sel.disabled = false;
  if (withTable.length) {
    sel.appendChild(h('optgroup', { label: 'Phim đã có bảng tên' },
      withTable.map((it) => h('option', { value: it.video_id },
        it.title + (it.corrupt ? ' — file bảng tên bị hỏng'
          : it.count ? ' — ' + plural(it.count, 'tên') : ' — bảng đang trống')))));
  }
  if (without.length) {
    sel.appendChild(h('optgroup', { label: 'Phim chưa có bảng tên' },
      without.map((it) => h('option', { value: it.video_id }, it.title + ' — chưa có bảng'))));
  }
  const all = withTable.concat(without);
  sel.value = all.some((it) => it.video_id === selected) ? selected : all[0].video_id;
}

/**
 * Nạp danh sách phim (movies) và — trừ khi `noLoad` — bảng tên của phim đang chọn.
 * `noLoad` dùng khi chỉ cần làm mới ô chọn mà không được đụng vào bảng đang sửa.
 */
async function loadNamesIndex(opts) {
  opts = opts || {};
  if (!opts.keepStatus) namesStatus('', '');
  let movies;
  try {
    const data = await api('/api/names');
    movies = Array.isArray(data.movies) ? data.movies : [];
  } catch (err) {
    if (opts.noLoad) return false;
    const sel = $('#names-video');
    clear(sel);
    sel.appendChild(h('option', { value: '' }, '—'));
    renderNamesEmpty(err instanceof ApiError ? err.message : 'Không đọc được danh sách phim.');
    return false;
  }
  state.names.items = movies
    .map((it) => {
      const id = String(it.video_id || '');
      return { video_id: id, title: String(it.title || '') || id, count: Number(it.count) || 0, corrupt: !!it.corrupt };
    })
    .filter((it) => NAMES_VIDEO_ID_RE.test(it.video_id));
  state.names.others = await moviesWithoutTable(state.names.items);
  state.names.loaded = true;

  fillNamesSelect(opts.select || state.names.videoId || store.get('namesVideo', ''));
  if (opts.noLoad) return true;

  const sel = $('#names-video');
  if (!sel.value) {
    state.names.videoId = '';
    state.names.entries = [];
    state.names.dirty = false;
    renderNamesEmpty('Chưa có phim nào để lập bảng tên. Hãy chạy một video ở tab “Tạo phụ đề” trước — ' +
      'phim đó sẽ hiện trong ô “Phim” ở đây, kể cả khi bạn không bật AI.');
    return true;
  }
  await loadNames(sel.value);
  return true;
}

function renderNamesEmpty(message) {
  const wrap = $('#names-wrap');
  clear(wrap);
  wrap.appendChild(h('div', { class: 'card' }, h('div', { class: 'emptybox', text: message })));
}

/** Đọc bảng tên của một phim. Trả về số dòng đọc được (-1 nếu không đọc được),
 *  để chỗ gọi sau khi Lưu đối chiếu xem máy chủ có giữ đủ không. */
async function loadNames(videoId) {
  if (!videoId) return -1;
  state.names.videoId = videoId;
  store.set('namesVideo', videoId);
  if (state.names.rerunFor !== videoId) hideNamesRerun();
  try {
    const data = await api('/api/names/' + encodeURIComponent(videoId));
    const entries = Array.isArray(data.entries) ? data.entries : [];
    state.names.entries = entries.map(normNameEntry);
    state.names.exists = data.exists === undefined ? entries.length > 0 : !!data.exists;
    state.names.dirty = false;
    state.names.readFailed = false;
    applyNamesHealth(data);
    renderNamesTable();
    return state.names.entries.length;
  } catch (err) {
    state.names.entries = [];
    state.names.dirty = false;
    state.names.readFailed = true;
    applyNamesHealth(null);
    renderNamesEmpty((err instanceof ApiError ? err.message : 'Không đọc được bảng tên của phim này.') +
      ' Bấm “Nạp lại” để thử lần nữa.');
    return -1;
  }
}

/**
 * Một dòng bảng tên theo hợp đồng {zh, pinyin, vi}.
 *
 * Ô "Chữ Hán" hiện các cụm cách nhau bằng dấu cách, đúng như trong file phụ đề
 * ("陈 路周"). GET lại trả `zh` viết liền ("陈路周"): ranh giới cụm khi đó chỉ
 * còn nằm ở `split` mà máy chủ gửi kèm. Vì vậy `split` được đọc ĐÚNG MỘT việc —
 * đặt lại dấu cách, và chỉ khi ghép lại khớp từng chữ với `zh`. Bỏ bước này thì
 * lần Lưu sau gửi "陈路周" + "Chén Lùzhōu" (1 cụm Hán, 2 cụm pinyin), máy chủ gộp
 * tên thành một cụm và jieba thôi tách 陈 | 路周 — hỏng dữ liệu mà không ai hay.
 */
function normNameEntry(raw) {
  raw = raw || {};
  let zh = String(raw.zh || '').trim();
  const pinyin = typeof raw.pinyin === 'string' ? raw.pinyin.trim().replace(/\s+/g, ' ') : '';
  const vi = typeof raw.vi === 'string' ? raw.vi.trim() : '';
  if (zh && !/\s/.test(zh) && Array.isArray(raw.split) && raw.split.length > 1) {
    const parts = raw.split.map((s) => String(s).trim()).filter(Boolean);
    if (parts.join('') === zh) zh = parts.join(' ');
  }
  return { zh, pinyin, vi };
}

function renderNamesTable() {
  const wrap = $('#names-wrap');
  clear(wrap);
  const entries = state.names.entries;

  if (!entries.length) {
    const msg = state.names.corrupt
      ? 'Chưa hiện được tên nào vì file bảng tên đang bị hỏng (xem khung vàng phía trên) — không phải vì bảng trống. ' +
        'Bạn có thể ghi lại tên ngay ở đây: bấm “Thêm tên”. Khi bấm Lưu, file hỏng được giữ lại chứ không bị xoá.'
      : state.names.exists
      ? 'Bảng tên của phim này đang trống. Bấm “Thêm tên” để ghi tên nhân vật đầu tiên.'
      : 'Phim này chưa có bảng tên. Bấm “Thêm tên” để ghi tên đầu tiên — bảng được tạo khi bạn bấm “Lưu bảng tên”.';
    wrap.appendChild(h('div', { class: 'card' }, h('div', { class: 'emptybox' },
      h('p', { text: msg }),
      h('button', { type: 'button', class: 'btn btn-ghost', onclick: addNameRow }, icon('plus'), 'Thêm tên'))));
    return;
  }

  const tbody = h('tbody');
  entries.forEach((entry) => tbody.appendChild(renderNameRow(entry)));

  wrap.appendChild(h('div', { class: 'tablewrap' },
    h('table', { class: 'ntable' },
      h('thead', {}, h('tr', {},
        h('th', { text: 'Chữ Hán' }),
        h('th', { text: 'Pinyin' }),
        h('th', { text: 'Tên tiếng Việt' }),
        h('th', { class: 'cell-act' })
      )),
      tbody
    )
  ));
}

function renderNameRow(entry) {
  const warnCell = h('span', { class: 'row-warn' });
  const refreshWarn = () => {
    const zhWords = wordCount(entry.zh);
    const pyWords = wordCount(entry.pinyin);
    const bad = !!(String(entry.zh || '').trim() && String(entry.pinyin || '').trim()) && zhWords !== pyWords;
    tr.classList.toggle('is-bad', bad);
    warnCell.textContent = bad
      ? 'Chữ Hán có ' + zhWords + ' cụm nhưng pinyin có ' + pyWords + ' cụm — phải bằng nhau.'
      : '';
  };

  const tr = h('tr', {},
    h('td', { class: 'cell-zh' }, h('input', {
      type: 'text', value: entry.zh, 'aria-label': 'Chữ Hán', lang: 'zh-Hans',
      placeholder: 'ví dụ: 陈 路周', spellcheck: 'false', autocomplete: 'off',
      oninput: (ev) => { entry.zh = ev.target.value; markNamesDirty(); refreshWarn(); }
    })),
    h('td', { class: 'cell-py' },
      h('input', {
        type: 'text', value: entry.pinyin, 'aria-label': 'Pinyin',
        placeholder: 'ví dụ: Chén Lùzhōu', spellcheck: 'false', autocomplete: 'off',
        oninput: (ev) => { entry.pinyin = ev.target.value; markNamesDirty(); refreshWarn(); }
      }),
      warnCell
    ),
    // Ô tiếng Việt: lang="vi" để bộ gõ và kiểm chính tả của máy hiểu đây là
    // tiếng Việt. Không cắt dấu, không đổi hoa/thường — gõ sao lưu vậy.
    h('td', { class: 'cell-vi' },
      h('input', {
        type: 'text', value: entry.vi, 'aria-label': 'Tên tiếng Việt', lang: 'vi',
        placeholder: 'ví dụ: Cường đầu trọc', autocomplete: 'off',
        oninput: (ev) => { entry.vi = ev.target.value; markNamesDirty(); }
      }),
      h('span', { class: 'cell-hint', text: 'Để trống thì máy tự phiên âm tên này.' })
    ),
    h('td', { class: 'cell-act' }, h('button', {
      type: 'button', class: 'btn btn-quiet btn-sm', 'aria-label': 'Xoá tên này', title: 'Xoá tên này',
      onclick: () => {
        const idx = state.names.entries.indexOf(entry);
        if (idx < 0) return;
        state.names.entries.splice(idx, 1);
        if (String(entry.zh || entry.pinyin || entry.vi || '').trim()) markNamesDirty();
        renderNamesTable();
      }
    }, icon('trash')))
  );
  refreshWarn();
  return tr;
}

function addNameRow() {
  if (!state.names.videoId) { toast('Chọn phim trước đã, ở ô “Phim” phía trên.', 'warn'); return; }
  if (state.names.readFailed) {
    toast('Chưa đọc được bảng tên đang có của phim này. Bấm “Nạp lại” trước đã.', 'warn', 7000);
    return;
  }
  state.names.entries.unshift({ zh: '', pinyin: '', vi: '' });
  renderNamesTable();
  const first = $('#names-wrap input');
  if (first) first.focus();
}

/** Đối chiếu thứ vừa gửi với thứ đọc lại từ máy chủ; trả về danh sách câu mô
 *  tả từng chỗ lệch (rỗng = máy giữ đúng hết). So sau khi chuẩn hoá NFC: bộ gõ
 *  tiếng Việt trên Mac có lúc ra dạng tổ hợp, máy chủ lại lưu dạng dựng sẵn. */
function verifyNamesSaved(sent, got) {
  const key = (s) => String(s || '').normalize('NFC').replace(/\s+/g, '');
  const norm = (s) => String(s || '').normalize('NFC').replace(/\s+/g, ' ').trim();
  const showRow = (e) => [e.zh, e.pinyin, e.vi].filter(Boolean).join(' · ');
  const gotMap = {};
  got.forEach((e) => { gotMap[key(e.zh)] = e; });
  const sentKeys = {};
  const out = [];
  sent.forEach((e) => {
    sentKeys[key(e.zh)] = true;
    const g = gotMap[key(e.zh)];
    if (!g) out.push('Không thấy trên máy: ' + showRow(e));
    else if (norm(g.vi) !== norm(e.vi)) out.push('Tên tiếng Việt khác: ' + showRow(e) + ' → máy giữ “' + (g.vi || 'trống') + '”');
    else if (norm(g.pinyin) !== norm(e.pinyin)) out.push('Pinyin khác: ' + showRow(e) + ' → máy giữ “' + (g.pinyin || 'trống') + '”');
  });
  got.forEach((g) => { if (!sentKeys[key(g.zh)]) out.push('Máy vẫn giữ tên bạn đã xoá: ' + showRow(g)); });
  return out;
}

/**
 * Lưu bảng tên, rồi ĐỌC LẠI từ máy chủ và vẽ lại bảng.
 *
 * Vì sao phải đọc lại: đã từng có lúc giao diện báo "Đã lưu N tên" trong khi
 * máy chủ vứt sạch phần người dùng vừa gõ. Một câu toast không chứng minh được
 * gì cả — thứ chứng minh được là bảng hiện lên đúng bằng thứ đang nằm trong
 * máy, và từng dòng gửi đi đều tìm thấy trong bảng đọc về. Lệch dòng nào thì
 * nói thẳng dòng đó, không được im. Trả về true khi đã lưu và kiểm chứng xong.
 */
async function saveNames() {
  const videoId = state.names.videoId;
  if (!videoId) { toast('Chưa chọn phim nào.', 'warn'); return false; }
  if (state.names.readFailed) {
    await dialog({
      title: 'Chưa lưu được', kind: 'warn',
      body: 'Tool chưa đọc được bảng tên đang có của phim này, nên chưa cho lưu — lưu lúc này có thể ' +
            'xoá mất bảng cũ trên máy. Bấm “Nạp lại” rồi thử lại.'
    });
    return false;
  }

  const clean = (s) => String(s || '').normalize('NFC').replace(/\s+/g, ' ').trim();
  const rows = state.names.entries.map((e) => ({ zh: clean(e.zh), pinyin: clean(e.pinyin), vi: clean(e.vi) }));
  const noZh = rows.filter((e) => !e.zh && (e.pinyin || e.vi)).length;
  const mismatch = rows.filter((e) => e.zh && e.pinyin && wordCount(e.zh) !== wordCount(e.pinyin)).length;
  const seen = {};
  const dups = [];
  rows.forEach((e) => {
    if (!e.zh) return;
    const k = e.zh.replace(/\s+/g, '');
    if (seen[k]) dups.push(e.zh);
    seen[k] = true;
  });

  if (mismatch) {
    const ok = await confirmBox(
      'Có ' + plural(mismatch, 'dòng chưa khớp số cụm'),
      'Ở những dòng đó, số cụm chữ Hán không bằng số cụm pinyin nên tool sẽ không ghép được. ' +
      'Vẫn lưu thì phần tên tiếng Việt của dòng đó vẫn dùng được, còn cách tách thì không. Lưu luôn chứ?',
      'Vẫn lưu'
    );
    if (!ok) return false;
  }
  if (noZh) {
    const ok = await confirmBox(
      'Có ' + plural(noZh, 'dòng chưa có chữ Hán'),
      'Dòng không có chữ Hán thì máy không biết gắn tên tiếng Việt vào đâu, nên sẽ bị bỏ đi. ' +
      'Bạn muốn lưu phần còn lại và điền nốt sau không?',
      'Lưu phần đã đủ'
    );
    if (!ok) return false;
  }
  if (dups.length) {
    const ok = await confirmBox(
      'Có ' + plural(dups.length, 'tên bị ghi hai lần'),
      'Tên ' + dups.slice(0, 5).join(', ') + ' xuất hiện hơn một dòng. Mỗi tên chỉ giữ được một cách viết, ' +
      'nên chỉ dòng đầu tiên được lưu, các dòng trùng phía dưới sẽ bị bỏ. Lưu luôn chứ?',
      'Lưu dòng đầu tiên'
    );
    if (!ok) return false;
  }

  const entries = [];
  const taken = {};
  rows.forEach((e) => {
    const k = e.zh.replace(/\s+/g, '');
    if (!k || taken[k]) return;
    taken[k] = true;
    entries.push({ zh: e.zh, pinyin: e.pinyin, vi: e.vi });
  });

  const restore = busyButton($('#btn-names-save'), 'Đang lưu…');
  namesStatus('Đang lưu…', '');
  const backup = state.names.entries.slice();
  try {
    const res = await api('/api/names/' + encodeURIComponent(videoId), { method: 'PUT', body: { entries } });
    if (res.ok === false) {
      namesStatus('Chưa lưu được.', 'err');
      await dialog({
        title: 'Chưa lưu được bảng tên', kind: 'warn',
        body: res.message || 'Máy chủ không nhận bảng tên này. Bảng cũ trên đĩa vẫn còn nguyên.'
      });
      return false;
    }

    // Máy chủ phải cất file bảng tên hỏng trước khi ghi bảng mới (hợp đồng H4):
    // nói cho người dùng biết nó nằm đâu, sau khi đã kiểm chứng xong.
    const keptName = String(res.backup_name || '');
    namesStatus('Đang đọc lại từ máy để kiểm chứng…', '');
    // Phim vừa lập bảng mới phải chuyển sang nhóm "đã có bảng" trong ô chọn.
    await loadNamesIndex({ select: videoId, noLoad: true, keepStatus: true });
    const back = await loadNames(videoId);
    if (back < 0) {
      // Không đọc lại được: đừng để bảng biến mất khỏi màn hình — trả lại đúng
      // những dòng người dùng vừa gõ, để họ còn thấy và bấm Lưu lại được.
      state.names.entries = backup;
      state.names.readFailed = false;
      state.names.dirty = true;
      renderNamesTable();
      namesStatus('Đã gửi đi nhưng chưa đọc lại được để kiểm chứng.', 'err');
      await dialog({
        title: 'Chưa kiểm chứng được', kind: 'warn',
        body: 'Bảng tên đã được gửi tới chương trình nhưng đọc lại thì không được, nên chưa chắc đã lưu. ' +
              'Bảng bạn vừa gõ vẫn đang hiện bên dưới — chờ một lát rồi bấm “Lưu bảng tên” lần nữa.'
      });
      return false;
    }

    const problems = verifyNamesSaved(entries, state.names.entries);
    if (problems.length) {
      namesStatus('Máy không giữ đúng ' + plural(problems.length, 'dòng') + '.', 'err');
      await dialog({
        title: 'Máy không giữ đủ bảng tên', kind: 'warn',
        body: [
          h('p', { text: 'Đọc lại từ máy thấy ' + plural(problems.length, 'chỗ') + ' khác với thứ bạn vừa gửi:' }),
          h('ul', {}, problems.slice(0, 8).map((p) => h('li', { text: p }))),
          problems.length > 8 ? h('p', { text: '… và ' + (problems.length - 8) + ' chỗ nữa.' }) : null,
          h('p', { class: 'field-help', text: 'Bảng đang hiện bên dưới là đúng thứ máy đang giữ. ' +
            'Hãy điền lại phần thiếu rồi bấm Lưu lần nữa; nếu vẫn mất thì báo cho người hỗ trợ.' })
        ]
      });
      return false;
    }

    namesStatus('Đã lưu và đọc lại từ máy: ' + plural(back, 'tên') + '.', 'ok');
    toast('Đã lưu ' + plural(back, 'tên') + ' và đọc lại từ máy để chắc chắn. Lần chạy sau sẽ dùng bảng này.', 'ok', 6000);
    // Phim đã chạy trong app: mời chạy lại ngay với bảng mới (không chờ).
    offerNamesRerun(videoId);
    if (keptName) {
      await dialog({
        title: 'Đã lưu bảng tên mới', kind: 'ok',
        body: 'File bảng tên cũ bị hỏng không bị xoá: chương trình đã giữ nó lại với tên “' + keptName +
              '” trong cùng thư mục, phòng khi cần cứu lại tên trong đó.'
      });
    }
    return true;
  } catch (err) {
    namesStatus('Chưa lưu được.', 'err');
    await reportError(err, 'Không lưu được bảng tên');
    return false;
  } finally {
    restore();
  }
}

/** Tình trạng file bảng tên theo GET /api/names/{id} (hàm thuần). File hỏng thì
 *  máy chủ vẫn trả 200 với bảng rỗng — chỉ có cờ `corrupt` phân biệt được "hỏng"
 *  với "trống", và hai thứ đó phải hiện khác nhau. */
function namesHealth(data) {
  const d = data && typeof data === 'object' ? data : {};
  const corrupt = d.corrupt === true;
  return {
    corrupt,
    message: corrupt ? String(d.corrupt_message || d.message || '').trim() : '',
    backupName: String(d.backup_name || '').trim(),
    path: String(d.path || '').trim()
  };
}

function applyNamesHealth(data) {
  const hs = namesHealth(data);
  state.names.corrupt = hs.corrupt;
  state.names.corruptMessage = hs.message;
  state.names.backupName = hs.backupName;
  state.names.path = hs.path;
  renderNamesCorrupt();
}

/** Khung vàng "file bảng tên bị hỏng". */
function renderNamesCorrupt() {
  const box = $('#names-corrupt');
  if (!box) return;
  const n = state.names;
  show(box, !!n.corrupt);
  if (!n.corrupt) return;
  $('#names-corrupt-msg').textContent = n.corruptMessage ||
    'File names.json của phim này có trên máy nhưng không đọc được — có thể nó bị sửa tay sai cú pháp hoặc bị ghi dở.';
  $('#names-corrupt-keep').textContent = n.backupName
    ? 'Chương trình không xoá file hỏng: một bản của nó đang được giữ với tên “' + n.backupName + '” trong cùng thư mục.'
    : 'Chương trình không xoá nó. Khi bạn bấm “Lưu bảng tên”, file hỏng được giữ lại với tên ' +
      '“names.json.hong-<ngày giờ>” trong cùng thư mục rồi mới ghi bảng mới — người hỗ trợ vẫn cứu được tên trong đó.';
  const btn = $('#btn-names-corrupt-reveal');
  show(btn, !!n.path);
  btn.onclick = () => revealPath(n.path, { okText: 'Đã mở thư mục chứa bảng tên.' });
}

function hideNamesRerun() {
  state.names.rerun = null;
  state.names.rerunFor = '';
  show($('#names-rerun'), false);
}

/**
 * Sau khi lưu bảng tên: nếu phim này đã chạy trong app thì mời chạy lại ngay —
 * phụ đề đã tạo vẫn theo bảng CŨ cho tới khi chạy lại. Phim chưa từng chạy
 * trong app thì không có gì để chạy lại: lần chạy đầu tiên sẽ tự dùng bảng này.
 */
async function offerNamesRerun(videoId) {
  const box = $('#names-rerun');
  if (!box || !videoId) return;
  hideNamesRerun();
  let jobs = [];
  try {
    const data = await api('/api/jobs?limit=200');
    jobs = data.jobs || data.items || (Array.isArray(data) ? data : []);
  } catch (_) { return; }
  if (state.names.videoId !== videoId) return;   // người dùng đã chuyển phim khác
  const found = pickRerunJob(jobs, videoId);
  const btn = $('#btn-names-rerun');
  if (found.busy) {
    $('#names-rerun-title').textContent = 'Đã lưu bảng tên. Phim này đang chạy.';
    $('#names-rerun-sub').textContent = 'Lần chạy đang dở có thể chưa kịp dùng bảng mới. Đợi nó xong rồi bấm ' +
      '“Chạy lại với bảng tên mới” ở màn hình kết quả.';
    show(btn, false);
  } else if (found.job) {
    state.names.rerun = found.job;
    $('#names-rerun-title').textContent = 'Đã lưu bảng tên. Phụ đề đã tạo của phim này vẫn theo bảng cũ.';
    $('#names-rerun-sub').textContent = 'Bấm “Chạy lại với bảng tên mới” để máy tách cụm, phiên âm và dịch lại cả phim — ' +
      'không cần file video gốc. Bản đang có (kể cả chỗ bạn đã sửa tay) được cất lại thành “.truoc-…”, không mất.';
    show(btn, true);
  } else {
    return;
  }
  state.names.rerunFor = videoId;
  show(box, true);
}

/** Bảng đang có phần gõ dở mà chưa lưu: hỏi trước khi làm việc sẽ thay bảng. */
async function confirmNamesLeave(actionText) {
  if (!state.names.dirty) return true;
  const choice = await dialog({
    title: 'Bảng tên chưa lưu', kind: 'warn',
    body: 'Bạn đã sửa bảng tên của phim đang mở mà chưa bấm Lưu. ' + actionText + ' thì phần vừa sửa sẽ mất.',
    buttons: [
      { id: 'stay', label: 'Quay lại', primary: true },
      { id: 'save', label: 'Lưu trước đã' },
      { id: 'drop', label: 'Bỏ phần vừa sửa', danger: true }
    ]
  });
  if (choice === 'save') return saveNames();
  return choice === 'drop';
}

async function onNamesVideoChange(ev) {
  const want = ev.target.value;
  const current = state.names.videoId;
  if (!want || want === current) return;
  if (!(await confirmNamesLeave('Chuyển sang phim khác'))) { $('#names-video').value = current; return; }
  $('#names-video').value = want;
  await loadNames(want);
}

async function reloadNames() {
  if (!(await confirmNamesLeave('Nạp lại'))) return;
  state.names.dirty = false;
  await loadNamesIndex();
}

/** Nút "Lập bảng cho phim khác": chọn một phim đã chạy mà chưa có bảng tên. */
async function newNamesTable() {
  // Làm mới danh sách trước: phim vừa chạy xong cũng phải có mặt.
  await loadNamesIndex({ noLoad: true, keepStatus: true, select: state.names.videoId });
  const others = state.names.others;
  if (!others.length) {
    await dialog({
      title: 'Chưa có phim nào để lập bảng mới',
      body: state.names.items.length
        ? 'Mọi phim đã chạy trong app đều đã có bảng tên — chọn phim ở ô “Phim” để sửa. ' +
          'Muốn lập bảng cho phim khác thì chạy phim đó ở tab “Tạo phụ đề” trước.'
        : 'Hãy chạy một video ở tab “Tạo phụ đề” trước; phim đó sẽ hiện ở đây để bạn lập bảng tên, ' +
          'kể cả khi không bật AI.'
    });
    return;
  }
  const pickSel = h('select', { class: 'select select-wide', 'aria-label': 'Phim cần lập bảng tên' },
    others.map((it) => h('option', { value: it.video_id }, it.title)));
  const choice = await dialog({
    title: 'Lập bảng tên cho phim khác',
    body: [
      h('p', { text: 'Chọn phim chưa có bảng tên. Bảng mới được tạo khi bạn ghi ít nhất một tên và bấm “Lưu bảng tên”.' }),
      pickSel
    ],
    buttons: [{ id: 'no', label: 'Thôi' }, { id: 'yes', label: 'Lập bảng', primary: true }]
  });
  if (choice !== 'yes') return;
  const id = pickSel.value;
  if (!id || !(await confirmNamesLeave('Mở phim khác'))) return;
  fillNamesSelect(id);
  await loadNames(id);
  if (!state.names.entries.length && !state.names.readFailed) addNameRow();
}

/* --------------------------------------------------------------------------
   12. TAB 4 — cài đặt và kiểm tra máy
   -------------------------------------------------------------------------- */

/** Đọc một khoá cài đặt. Máy chủ thật trả bảng PHẲNG (`model`, `profile`,
 *  `ass_export`…), còn file cấu hình đầy đủ thì lồng (`asr.model`). Thử lần
 *  lượt từng đường cho tới khi có giá trị — giao diện không cần biết bản chương
 *  trình đang chạy là bản nào. */
function setting(paths, fallback) {
  for (const path of paths) {
    const value = pick(state.settings, path, undefined);
    if (value !== undefined && value !== null && value !== '') return value;
  }
  return fallback;
}

function settingBool(paths, fallback) {
  for (const path of paths) {
    const value = pick(state.settings, path, undefined);
    if (typeof value === 'boolean') return value;
  }
  return fallback;
}

function renderModelList(container, name) {
  if (!container) return;
  clear(container);
  const chosen = currentModelId();
  state.models.forEach((m) => {
    const id = m.id;
    const on = id === chosen;
    const facts = [
      h('span', {}, icon('clock'), ' ', m.time || 'chưa rõ thời gian'),
      h('span', {}, icon('download'), ' ', m.size || 'chưa rõ dung lượng')
    ];
    if (m.downloaded === true) facts.push(h('span', { class: 'pill pill-ok' }, icon('ok'), 'Đã tải rồi'));
    else if (m.downloaded === false) {
      facts.push(h('span', { class: 'pill pill-muted' }, 'Chưa tải'));
      // Chỉ mời tải sẵn ở tab Cài đặt: màn hình chính là chỗ để bấm Bắt đầu,
      // thêm nút tải vào đó chỉ làm người dùng phân vân.
      if (container.id === 'model-list-settings') {
        facts.push(h('button', {
          type: 'button', class: 'btn btn-ghost btn-sm',
          title: 'Tải model này về ngay bây giờ',
          onclick: (ev) => { ev.preventDefault(); ev.stopPropagation(); runAction('download_model', ev.currentTarget, { model: id }); }
        }, icon('download'), ' Tải về'));
      }
    }

    const label = h('label', { class: 'model' + (on ? ' is-on' : '') },
      h('input', {
        type: 'radio', name, value: id, checked: on,
        onchange: () => {
          store.set('model', id);
          renderModelList($('#model-list'), 'model-a');
          renderModelList($('#model-list-settings'), 'model-b');
        }
      }),
      h('div', {},
        h('div', { class: 'model-name' }, m.name, m.tag ? h('span', { class: 'pill pill-ok', style: 'margin-left:8px' }, m.tag) : null),
        h('div', { class: 'model-id', text: id }),
        m.problem ? h('div', { class: 'row-warn', text: m.problem }) : null
      ),
      h('div', { class: 'model-facts' }, ...facts)
    );
    container.appendChild(label);
  });

  const note = $('#model-note-settings');
  if (note && container.id === 'model-list-settings') {
    const ready = state.models.filter((m) => m.downloaded === true).map((m) => m.name);
    note.textContent = ready.length
      ? 'Đã tải sẵn trên máy: ' + ready.join(', ') + '. Chọn model khác thì lần chạy đầu tiên sẽ tải thêm.'
      : 'Chương trình chưa cho biết model nào đã tải. Cứ chạy bình thường, thiếu thì máy tự tải về một lần rồi thôi.';
  }
}

/** Đọc lại RIÊNG bảng model sau khi tải xong một model, để chữ "Đã tải rồi"
 *  hiện lên ngay. Cố ý không gọi loadSettings(): hàm đó nạp lại cả biểu mẫu,
 *  và người dùng có thể đang gõ dở khoá API ở ngay bên trên. */
async function refreshModels() {
  try {
    const data = await api('/api/settings');
    const cfg = data.settings || data.config || data;
    const models = data.models || cfg.models || cfg.asr_models || pick(cfg, 'asr.models', null);
    if (Array.isArray(models) && models.length) {
      const normed = normModels(models);
      if (normed.length) state.models = normed;
    }
    renderModelList($('#model-list'), 'model-a');
    renderModelList($('#model-list-settings'), 'model-b');
  } catch (_) { /* không quan trọng tới mức phải làm phiền người dùng */ }
}

async function loadSettings() {
  try {
    const data = await api('/api/settings');
    const cfg = data.settings || data.config || data;
    state.settings = Object.assign({}, cfg || {});
    // Máy chủ để vài khoá NGOÀI gói `settings` (api_key_masked, api_key_set…).
    // Không gộp vào thì ô khoá API hiện trống trơn dù máy đang có khoá, và
    // người dùng sẽ dán lại khoá một cách vô ích.
    ['api_key_masked', 'api_key_set', 'api_key_from_env', 'api_key_env',
     'out_dir_effective', 'work_dir'].forEach((key) => {
      if (data[key] !== undefined && data[key] !== null && state.settings[key] === undefined) {
        state.settings[key] = data[key];
      }
    });
    state.settings.__loaded = true;
    state.workDir = data.work_dir || cfg.work_dir || pick(cfg, 'paths.work_dir', '') || '';

    // Bảng model: ưu tiên bảng của máy chủ (models / cfg.asr.models), rồi mới
    // tới bản dự phòng viết cứng trong file này.
    const models = data.models || cfg.models || cfg.asr_models || pick(cfg, 'asr.models', null);
    if (Array.isArray(models) && models.length) {
      const normed = normModels(models);
      if (normed.length) state.models = normed;
    }
    if (!store.get('model', null)) store.set('model', setting(['model', 'asr.model'], 'large-v3-turbo'));

    fillSettingsForm();
  } catch (err) {
    $('#settings-status').textContent = err instanceof ApiError ? err.message : 'Không đọc được cài đặt.';
  }
  renderModelList($('#model-list'), 'model-a');
  renderModelList($('#model-list-settings'), 'model-b');
}

function fillSettingsForm() {
  if ($('#set-aimodel')) $('#set-aimodel').value = String(setting(['ai_model', 'ai.model_other'], '') || '');
  const keyField = $('#set-apikey');
  // Máy chủ chỉ trả bản che (AIza••••1234). Giữ lại để biết người dùng có gõ
  // khoá mới hay không — gửi nhầm chuỗi che lên sẽ phá mất khoá thật.
  const masked = String(setting(['api_key_masked', 'ai.api_key'], ''));
  keyField.value = masked;
  keyField.dataset.masked = masked;

  const fromEnv = !!setting(['api_key_from_env'], false);
  const keyStatus = $('#key-status');
  if (fromEnv && !masked) {
    keyStatus.className = 'field-status ok';
    keyStatus.textContent = 'Đang dùng khoá lấy từ máy (biến môi trường ' +
      setting(['api_key_env'], 'GEMINI_API_KEY') + '), không cần nhập lại ở đây.';
  }

  $('#set-translator').value = String(setting(['translate_provider', 'translate.provider'], 'auto'));
  $('#set-demucs').checked = settingBool(['demucs', 'audio.demucs'], false);
  $('#set-ass').checked = settingBool(['ass_export', 'emit.ass_export'], false);
  $('#set-bilingual').checked = settingBool(['bilingual_ass', 'emit.bilingual_ass'], true);
  $('#set-bom').checked = settingBool(['bom', 'emit.bom'], true);
  $('#set-profile').value = store.get('profile', String(setting(['profile', 'default_profile'], 'drama')));
  $('#set-outdir').value = String(setting(['out_dir', 'paths.out_dir'], ''));
  $('#workdir-value').textContent = state.workDir || 'Thư mục mặc định của chương trình';

  const effective = String(setting(['out_dir_effective'], ''));
  if (effective) {
    $('#outdir-help').textContent = 'Kết quả đang được ghi vào: ' + effective;
  }
  updateTranslatorHelp();

  $('#opt-ai').checked = settingBool(['ai_enabled', 'ai.enabled'], false);
  $('#opt-demucs').checked = $('#set-demucs').checked;
  $('#opt-ass').checked = $('#set-ass').checked;
  const profile = $('#set-profile').value;
  $$('#profile-seg .seg-item').forEach((b) => {
    const on = b.dataset.profile === profile;
    b.classList.toggle('is-on', on);
    b.setAttribute('aria-checked', on ? 'true' : 'false');
  });
}

function updateTranslatorHelp() {
  const v = $('#set-translator').value;
  const field = $('#set-apikey');
  const typed = field.value.trim();
  const hasKey = !!typed && typed !== (field.dataset.masked || '') ? true
               : !!setting(['api_key_set'], false) || !!typed;
  const help = {
    auto: hasKey ? 'Đang có khoá API nên sẽ dùng Gemini — bản dịch có ngữ cảnh, tên nhân vật nhất quán cả phim.'
                 : 'Chưa có khoá API nên sẽ dùng bản miễn phí của Google. Chạy được ngay, nhưng dịch từng dòng rời rạc nên chất lượng thấp hơn rõ rệt.',
    gemini: 'Dịch theo lô có ngữ cảnh và có bảng tên riêng, chất lượng cao nhất. Cần khoá API ở trên.',
    google_free: 'Không cần đăng ký gì. Dịch từng dòng rời rạc nên hay sai xưng hô và tên nhân vật — chỉ nên dùng khi cần gấp.',
    'null': 'Bỏ hẳn bước dịch. Chỉ tạo file phụ đề tiếng Trung.'
  };
  $('#translator-help').textContent = help[v] || '';
}

/** Gửi cả hai hình dạng: khoá phẳng cho máy chủ hiện tại, khoá lồng cho bản
 *  cấu hình đầy đủ. Bên nào không hiểu khoá nào thì bỏ qua khoá đó. */
function settingsPayload() {
  const apiKey = $('#set-apikey').value.trim();
  const model = currentModelId();
  const profile = $('#set-profile').value;
  const provider = $('#set-translator').value;
  const demucs = $('#set-demucs').checked;
  const assExport = $('#set-ass').checked;
  const bilingual = $('#set-bilingual').checked;
  const bom = $('#set-bom').checked;
  const outDir = $('#set-outdir').value.trim();
  const aiEnabled = $('#opt-ai').checked;
  const aiModel = ($('#set-aimodel') ? $('#set-aimodel').value.trim() : '');

  return {
    api_key: apiKey,
    model,
    ai_model: aiModel,
    profile,
    ai_enabled: aiEnabled,
    translate_provider: provider,
    demucs,
    ass_export: assExport,
    bilingual_ass: bilingual,
    bom,
    out_dir: outDir,
    ai: { api_key: apiKey, enabled: aiEnabled, model_other: aiModel },
    translate: { provider },
    asr: { model },
    audio: { demucs },
    emit: { ass_export: assExport, bilingual_ass: bilingual, bom },
    paths: { out_dir: outDir },
    default_profile: profile
  };
}

async function saveSettings() {
  const status = $('#settings-status');
  status.textContent = 'Đang lưu…';
  try {
    const res = await api('/api/settings', { method: 'POST', body: settingsPayload() });
    if (res.settings) state.settings = Object.assign(state.settings, res.settings);
    store.set('profile', $('#set-profile').value);
    status.textContent = 'Đã lưu lúc ' + new Date().toLocaleTimeString('vi-VN');
    toast('Đã lưu cài đặt.', 'ok');
    fillSettingsForm();
  } catch (err) {
    status.textContent = '';
    await reportError(err, 'Không lưu được cài đặt');
  }
}

/**
 * Thử khoá API bằng một yêu cầu nhỏ nhất.
 *
 * Ô trống, hoặc ô đang hiện chuỗi che (AIza••••1234) mà người dùng chưa gõ gì
 * mới, thì KHÔNG gửi gì cả — máy chủ tự thử khoá đang lưu. Gửi chuỗi che lên
 * là gửi một khoá sai, rồi báo "khoá hỏng" trong khi khoá thật vẫn tốt.
 */
async function testApiKey() {
  const btn = $('#btn-test-key');
  const out = $('#key-status');
  const field = $('#set-apikey');
  const typed = field.value.trim();
  const masked = String(field.dataset.masked || '');
  const isNew = !!typed && typed !== masked;
  const hasSaved = !!setting(['api_key_set'], false) || !!masked || !!setting(['api_key_from_env'], false);

  if (!isNew && !hasSaved) {
    out.className = 'field-status err';
    out.textContent = 'Chưa có khoá nào để thử. Dán khoá Gemini vào ô bên trên rồi bấm lại nút này.';
    return;
  }

  const restore = busyButton(btn, 'Đang thử…');
  out.className = 'field-status';
  out.textContent = isNew
    ? 'Đang thử khoá bạn vừa dán bằng một yêu cầu nhỏ nhất…'
    : 'Đang thử khoá đang lưu trong máy bằng một yêu cầu nhỏ nhất…';
  try {
    const res = await api('/api/settings/test-key', {
      method: 'POST', body: isNew ? { api_key: typed } : {}
    });
    const ok = res.ok !== false;
    out.className = 'field-status ' + (ok ? 'ok' : 'err');
    out.textContent = res.message || (ok
      ? 'Khoá dùng được.' + (isNew ? ' Nhớ bấm “Lưu cài đặt” để giữ lại khoá này.' : '')
      : 'Khoá này không dùng được.');
  } catch (err) {
    out.className = 'field-status err';
    out.textContent = err instanceof ApiError ? err.message : 'Không thử được khoá lúc này. Bạn thử lại sau ít phút.';
  } finally {
    restore();
  }
}

const DOCTOR_ICON = { ok: 'ok', warn: 'warn', fail: 'err' };

async function runDoctor() {
  const btn = $('#btn-doctor');
  const out = $('#doctor-out');
  btn.disabled = true;
  clear(out);
  out.appendChild(h('div', { class: 'dcheck' }, icon('refresh', 'spin'),
    h('div', { class: 'dcheck-label', text: 'Đang kiểm tra máy…' }), h('span')));
  try {
    const data = await api('/api/doctor');
    const checks = data.checks || data.items || (Array.isArray(data) ? data : []);
    clear(out);
    if (!checks.length) { out.appendChild(h('div', { class: 'emptybox', text: 'Không có mục nào để kiểm.' })); return; }

    // Máy chủ tự nói mục nào là ổn: `ok: true` cho cả mục chỉ để thông tin
    // (status "info", ví dụ "chưa có khoá API — vẫn chạy được"). Bỏ qua cờ đó
    // thì mục thông tin bị vẽ thành lỗi đỏ và bị đếm vào "Có N mục cần xử lý".
    const kindOf = (c) => {
      const status = String(c.status || 'ok').toLowerCase();
      if (c.ok === true || status === 'ok' || status === 'info') return 'ok';
      return (status === 'warn' || status === 'warning') ? 'warn' : 'fail';
    };
    checks.forEach((c) => {
      const kind = kindOf(c);
      const hint = c.hint || c.fix || '';
      const fix = kind !== 'ok' ? fixActionFor(c.fix_action) : null;
      // Trong "Kiểm tra máy" chỉ nút làm việc thật (cài / tải / cập nhật / mở
      // hướng dẫn) mới có nghĩa. Mã không có việc máy làm được (install_uv) thì
      // hiện lời khuyên ngay dưới mục thay cho nút.
      const canRun = !!(fix && (fix.kind === 'server' || fix.kind === 'guide'));
      out.appendChild(h('div', { class: 'dcheck ' + kind },
        icon(DOCTOR_ICON[kind]),
        h('div', {},
          h('div', { class: 'dcheck-label', text: c.label || c.name || c.id }),
          c.detail ? h('div', { class: 'dcheck-detail', text: c.detail }) : null,
          hint && kind !== 'ok' ? h('div', { class: 'dcheck-hint' }, 'Cách sửa: ', h('code', { text: hint })) : null,
          fix && !canRun ? adviceBox(fix, 'advice-sm') : null
        ),
        canRun
          ? h('button', { type: 'button', class: 'btn btn-ghost btn-sm', onclick: (ev) => runFixAction(c.fix_action, ev.currentTarget) }, fix.label)
          : h('span')
      ));
    });

    const bad = checks.filter((c) => kindOf(c) !== 'ok').length;
    toast(bad ? 'Có ' + plural(bad, 'mục cần xử lý') + '.' : 'Máy đã đủ mọi thứ cần thiết.', bad ? 'warn' : 'ok');
  } catch (err) {
    clear(out);
    out.appendChild(h('div', { class: 'emptybox', text: err instanceof ApiError ? err.message : 'Không kiểm tra được máy.' }));
  } finally {
    btn.disabled = false;
  }
}

/* --------------------------------------------------------------------------
   12b. TAB 2 — TRÌNH SỬA PHỤ ĐỀ  (build-spec-v2 mục 4)
   --------------------------------------------------------------------------
   Điều quan trọng nhất ở đây KHÔNG phải là ô sửa chữ, mà là nút ▶ của từng
   dòng: soát phụ đề mà không nghe lại được đúng đoạn tiếng của dòng đó thì
   chỉ là đoán. Vì vậy chỉ dùng MỘT thẻ <audio> duy nhất, đặt currentTime =
   cue.start rồi tự dừng ở cue.end bằng sự kiện timeupdate — không cắt file,
   không tải lại, bấm dòng nào nghe được ngay dòng đó.

   Ba nguyên tắc còn lại:
     * Không tự động chạy lại pinyin khi người dùng sửa chữ Hán. Người ta vừa
       gõ tay xong mà máy đè lên là mất công gõ. Chỉ hiện nút mời bấm.
     * Mọi thay đổi đi qua pushEdit() nên Ctrl+Z hoàn tác được nhiều bước, và
       bản nháp tự gửi lên máy chủ mỗi 5 giây.
     * Bảng có thể dài 1351 dòng: dựng hàng theo từng đợt trong
       requestAnimationFrame để trang không đứng hình lúc mở.
   -------------------------------------------------------------------------- */

/* Chu kỳ tự lưu nháp, tính bằng mili giây. build-spec-v2 mục 4 ghi rõ 5 giây. */
const DRAFT_INTERVAL_MS = 5000;

/* Số hàng dựng trong mỗi đợt. 200 hàng vẽ xong trong khoảng một khung hình
   trên máy iMac 2017; cao hơn nữa là thấy trang khựng lúc mở. */
const ROWS_PER_CHUNK = 200;

const editor = {
  jobId: '',
  title: '',
  videoId: '',           // mã video, để "Sinh lại pinyin" dùng đúng bảng tên riêng
  source: '',            // đường dẫn file .srt đang sửa (nếu mở từ đĩa)
  viPath: '',            // đường dẫn file _vi.srt, rỗng nghĩa là chưa có bản dịch
  cues: [],
  baseline: [],          // bản máy tạo, để nút "Hoàn nguyên" có chỗ quay về
  findings: [],
  byCue: {},             // số dòng -> danh sách lỗi của dòng đó
  rows: [],
  selected: -1,
  filter: 'all',
  query: '',
  undo: [],
  redo: [],
  rev: 0,                // tăng sau mỗi lần sửa; so với draftRev để khỏi gửi thừa
  draftRev: 0,
  dirty: false,
  saving: false,
  draftOk: true,
  baseHash: '',          // dấu vân tay của bản ĐANG NẰM TRÊN ĐĨA lúc mở trình sửa
  draftOffer: null,      // bản nháp đang chờ người dùng quyết: {cues, savedAt, newer}
  draftChecking: false,  // đang hỏi máy chủ có nháp không — chưa được ghi nháp đè lên
  audioOk: false,
  audioUrl: '',
  hasAudio: false,
  hasVideo: false,       // có bản có hình phát thẳng được trên trình duyệt
  videoUrl: '',
  media: null,           // thông tin GET /api/jobs/{id}/media
  current: -1,           // dòng đang trùng với vị trí video (khác dòng đang chọn)
  follow: true,          // bảng tự cuộn theo video
  freePlay: false,       // đang phát liên tục (không phải nghe một dòng)
  canRevert: true,
  playing: -1,
  stopAt: null,
  renderTimer: 0,
  loaded: false
};

/* ---- 12b.1 thời gian ---- */

/** "00:01:22,100" -> 82.1. Trả về null khi không đọc được, để chỗ gọi biết
 *  đường trả lại giá trị cũ thay vì ghi 0 giây đè lên mốc thời gian đúng. */
function parseClock(raw) {
  const s = String(raw === null || raw === undefined ? '' : raw).trim().replace(/\u00a0/g, ' ');
  if (!s) return null;
  const m = s.match(/^(?:(\d{1,3}):)?(\d{1,2}):(\d{1,2})(?:[.,](\d{1,3}))?$/);
  if (m) {
    const hh = Number(m[1] || 0), mm = Number(m[2]), ss = Number(m[3]);
    const ms = Number(String(m[4] || '0').padEnd(3, '0'));
    if (mm > 59 || ss > 59) return null;
    return hh * 3600 + mm * 60 + ss + ms / 1000;
  }
  if (/^\d+(?:[.,]\d+)?$/.test(s)) return parseFloat(s.replace(',', '.'));
  return null;
}

/** Số giây -> "00:01:22,100" đúng dạng SubRip. */
function clockOf(sec) {
  let v = Number(sec);
  if (!isFinite(v) || v < 0) v = 0;
  const ms = Math.round(v * 1000);
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  const s = Math.floor((ms % 60000) / 1000);
  const r = ms % 1000;
  return String(h).padStart(2, '0') + ':' + String(m).padStart(2, '0') + ':' +
         String(s).padStart(2, '0') + ',' + String(r).padStart(3, '0');
}

/** "lúc 14:35 hôm nay" / "lúc 14:35 ngày 09/09". Người dùng cần biết bản nháp
 *  cũ tới mức nào để quyết có khôi phục hay không; một con số unix thì không. */
function whenText(stamp) {
  let value = Number(stamp);
  if (!isFinite(value) || value <= 0) return '';
  if (value < 1e11) value *= 1000;          // giây -> mili giây
  const when = new Date(value);
  if (isNaN(when.getTime())) return '';
  const clock = when.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });
  const now = new Date();
  const sameDay = when.getFullYear() === now.getFullYear() &&
                  when.getMonth() === now.getMonth() && when.getDate() === now.getDate();
  if (sameDay) return 'lúc ' + clock + ' hôm nay';
  const day = String(when.getDate()).padStart(2, '0');
  const month = String(when.getMonth() + 1).padStart(2, '0');
  return 'lúc ' + clock + ' ngày ' + day + '/' + month;
}

/** Dấu vân tay nội dung của một bảng cue (FNV-1a 32 bit).
 *
 *  Dùng để trả lời hai câu hỏi mà không phải so từng dòng: (1) bản nháp có
 *  khác thứ đang nằm trên đĩa không — giống thì đừng làm phiền người dùng;
 *  (2) file trên đĩa có bị ghi lại sau lúc ghi nháp không — vân tay của bản
 *  gốc được gửi kèm bản nháp nên so lại được. */
function cueFingerprint(cues) {
  let hash = 0x811c9dc5;
  const feed = (raw) => {
    const text = String(raw === null || raw === undefined ? '' : raw);
    for (let i = 0; i < text.length; i++) {
      hash ^= text.charCodeAt(i);
      hash = (hash + ((hash << 1) + (hash << 4) + (hash << 7) + (hash << 8) + (hash << 24))) >>> 0;
    }
  };
  (cues || []).forEach((c) => {
    feed(clockOf(c.start)); feed('>'); feed(clockOf(c.end)); feed('|');
    feed(c.zh); feed('|'); feed(c.py); feed('|'); feed(c.vi); feed('\n');
  });
  return (hash >>> 0).toString(16);
}

/* ---- 12b.2 đếm cụm ----
   Chép đúng cách tách của srtgen/core/rules.py (_lex_line): dấu câu và marker
   đổi người nói KHÔNG được tính là cụm. Hai bên đếm khác nhau thì người dùng
   sẽ thấy giao diện báo một đằng, báo cáo của máy chủ báo một nẻo. */

const PUNCT_OPEN = '《（〈「『“‘【〔';
const PUNCT_CLOSE = '》）〉」』”’】〕';
const PUNCT_MID = '。，、？！：；·';
const ASCII_FORBIDDEN = ',.?!:;';
const ASCII_OTHER = '()<>[]{}"';
const DASH_WRONG = '-‐‑‒–―－−';
const SPACE_CHARS = ' \t\u00a0\u3000\u2009\u200a';
const ELLIPSIS_CHAR = '…';
const DASH_CHAR = '—';

function isSpaceChar(ch) { return SPACE_CHARS.indexOf(ch) >= 0; }

function isPunctChar(ch) {
  return PUNCT_OPEN.indexOf(ch) >= 0 || PUNCT_CLOSE.indexOf(ch) >= 0 ||
         PUNCT_MID.indexOf(ch) >= 0 || ASCII_FORBIDDEN.indexOf(ch) >= 0 ||
         ASCII_OTHER.indexOf(ch) >= 0 || ch === ELLIPSIS_CHAR ||
         ch === DASH_CHAR || DASH_WRONG.indexOf(ch) >= 0;
}

/** `-` mở một lượt thoại (marker) hay là dấu ngắt lời viết sai? */
function isMarkerDash(line, start, end) {
  if (line[start] !== '-') return false;
  if (start === 0) return true;
  const leftSpace = isSpaceChar(line[start - 1]);
  const rightSpace = end < line.length && isSpaceChar(line[end]);
  let j = start - 1;
  while (j >= 0 && isSpaceChar(line[j])) j--;
  const prev = j >= 0 ? line[j] : '';
  if (prev && (PUNCT_OPEN.indexOf(prev) >= 0 || PUNCT_MID.indexOf(prev) >= 0 ||
               PUNCT_CLOSE.indexOf(prev) >= 0 || prev === ELLIPSIS_CHAR)) return true;
  return leftSpace || rightSpace;
}

/** Số cụm chữ của một dòng đã render (bỏ dấu câu và marker). */
function countClusters(line) {
  const text = String(line || '');
  let n = 0, i = 0;
  while (i < text.length) {
    const ch = text[i];
    if (isSpaceChar(ch)) { i++; continue; }
    if (ch === ELLIPSIS_CHAR || ch === DASH_CHAR || ch === '.' || DASH_WRONG.indexOf(ch) >= 0) {
      let j = i;
      while (j < text.length && text[j] === ch) j++;
      i = j;
      continue;                       // dấu lửng / gạch ngang / marker: không tính
    }
    if (isPunctChar(ch)) {
      let j = i;
      while (j < text.length && text[j] === ch) j++;
      i = j;
      continue;
    }
    let j = i;
    while (j < text.length && !isSpaceChar(text[j]) && !isPunctChar(text[j])) j++;
    n++;
    i = j;
  }
  return n;
}

/* ---- 12b.3 đọc dữ liệu từ máy chủ ---- */

/* Cờ của token do S5/S7 gắn (srtgen/core/token.py). Chỉ ba cờ đầu là "cần
   người duyệt" nên mới bôi vàng cả dòng; NAME chỉ là ghi chú, bôi vàng theo nó
   thì gần như cả phim vàng khè và người soát mất luôn thứ để chú ý. */
const FLAG_LABELS = {
  AI_APPLIED: 'AI đã sửa dòng này, cần bạn duyệt lại',
  HETERONYM: 'Có chữ đa âm — cần xác nhận cách đọc',
  CASE_AMBIG: 'Chưa chắc viết hoa hay viết thường',
  NAME: 'Có tên riêng'
};
const REVIEW_FLAGS = ['AI_APPLIED', 'HETERONYM', 'CASE_AMBIG'];

/** Một cue từ máy chủ, chấp nhận nhiều cách đặt tên khoá.
 *  Máy chủ thật gửi zh_line/py_line/vi_line; bản cấu hình khác có thể gửi
 *  zh/py/vi. Giao diện không được vỡ chỉ vì bên kia gọi tên khoá khác. */
function normEditCue(raw, i) {
  raw = raw || {};
  const first = (...keys) => {
    for (const k of keys) {
      if (raw[k] !== undefined && raw[k] !== null && raw[k] !== '') return raw[k];
    }
    return '';
  };
  const start = parseClock(first('start', 'start_sec', 'start_text', 'from', 'begin'));
  const end = parseClock(first('end', 'end_sec', 'end_text', 'to', 'finish'));

  const flags = Array.isArray(raw.flags) ? raw.flags.map(String) : [];
  const legacy = raw.ai_flag || raw.flag || raw.flagged || raw.needs_review;
  const note = flags.map((f) => FLAG_LABELS[f] || f).join(' · ');

  return {
    index: Number(first('index', 'n', 'cue_index', 'number')) || i + 1,
    start: start === null ? 0 : start,
    end: end === null ? 0 : end,
    zh: String(first('zh_line', 'zh', 'han', 'chinese') || ''),
    py: String(first('py_line', 'py', 'pinyin') || ''),
    vi: String(first('vi_line', 'vi', 'viet', 'vietnamese', 'translation') || ''),
    flags,
    flag: flags.some((f) => REVIEW_FLAGS.indexOf(f) >= 0) || !!legacy,
    flagNote: note || (typeof legacy === 'string' ? legacy : ''),
    findings: Array.isArray(raw.findings) ? raw.findings : null,
    reviewed: !!raw.reviewed,
    zhTouched: false
  };
}

function indexFindings() {
  const map = {};
  (editor.findings || []).forEach((f) => {
    const n = Number(f.cue_index || f.cue || f.index || 0);
    if (!n) return;
    (map[n] = map[n] || []).push(f);
  });
  editor.byCue = map;
}

function findingsOf(i) {
  const cue = editor.cues[i];
  if (!cue) return [];
  // Máy chủ gắn sẵn lỗi vào từng cue; chỉ khi không có mới tra theo số dòng
  // (số dòng ghi trong file có thể không liên tục, tra theo nó dễ lệch hàng).
  if (cue.findings) return cue.findings;
  return editor.byCue[cue.index] || [];
}

/* ---- 12b.4 dựng bảng ---- */

function cellText(node) {
  // KHÔNG dùng innerText: bảng bật `content-visibility: auto`, và với phần đang
  // bị trình duyệt bỏ qua thì innerText trả về chuỗi RỖNG — sửa một dòng ở xa
  // chỗ đang nhìn sẽ bị ghi thành rỗng, mất chữ mà không báo gì. textContent
  // thì mất dấu xuống dòng của ô thời gian hai dòng. Nên tự duyệt cây con.
  let out = '';
  const walk = (el) => {
    for (const kid of el.childNodes) {
      if (kid.nodeType === 3) { out += kid.data; continue; }
      if (kid.nodeType !== 1) continue;
      if (kid.tagName === 'BR') { out += '\n'; continue; }
      const block = kid.tagName === 'DIV' || kid.tagName === 'P';
      if (block && out && out.slice(-1) !== '\n') out += '\n';
      walk(kid);
      if (block && out && out.slice(-1) !== '\n') out += '\n';
    }
  };
  walk(node);
  return out.replace(/\u00a0/g, ' ').replace(/\r\n?/g, '\n').replace(/\n+$/, '');
}

function setCellText(node, text) {
  node.textContent = String(text === null || text === undefined ? '' : text);
}

function editCell(field, value, cls, placeholder) {
  const node = h('div', {
    class: 'cue-cell ' + cls,
    contenteditable: 'true',
    spellcheck: 'false',
    role: 'textbox',
    'aria-label': placeholder,
    dataset: { field, empty: placeholder }
  });
  setCellText(node, value);
  if (!String(value || '').trim()) node.classList.add('is-empty');
  return node;
}

function buildRow(i) {
  const c = editor.cues[i];
  const row = h('div', { class: 'cuerow', dataset: { i: String(i) } },
    h('div', { class: 'cue-n', text: String(c.index) }),
    editCell('time', clockOf(c.start) + '\n' + clockOf(c.end), 'cue-time', 'Mốc thời gian'),
    h('button', {
      type: 'button', class: 'cue-play', tabindex: '-1',
      'aria-label': 'Nghe dòng ' + c.index, title: 'Nghe dòng này'
    }, icon('play')),
    editCell('zh', c.zh, 'cue-zh zh', 'Dòng chữ Hán'),
    editCell('py', c.py, 'cue-py py', 'Dòng pinyin'),
    editCell('vi', c.vi, 'cue-vi', 'Câu tiếng Việt'),
    h('div', { class: 'cue-warn' }),
    h('div', { class: 'cue-note' })
  );
  decorateRow(i, row);
  if (!matchRow(i)) row.classList.add('is-hidden');
  return row;
}

/** Vẽ lại phần "trạng thái" của một hàng: viền màu, ô lỗi, dải ghi chú.
 *  Không đụng vào ô đang được sửa để không cướp con trỏ của người dùng. */
function decorateRow(i, rowNode) {
  const row = rowNode || editor.rows[i];
  if (!row) return;
  const c = editor.cues[i];
  const finds = findingsOf(i);
  const errs = finds.filter((f) => (f.severity || 'error') === 'error').length;
  const warns = finds.length - errs;

  row.classList.toggle('has-error', errs > 0);
  row.classList.toggle('has-warn', errs === 0 && warns > 0);
  row.classList.toggle('is-flag', !!c.flag);
  row.classList.toggle('is-reviewed', !!c.reviewed);
  row.classList.toggle('is-selected', editor.selected === i);
  row.classList.toggle('is-playing', editor.playing === i);

  // ô cảnh báo
  const warnCell = row.querySelector('.cue-warn');
  clear(warnCell);
  if (finds.length) {
    warnCell.appendChild(h('button', {
      type: 'button', tabindex: '-1',
      class: 'cue-warnbtn' + (errs ? '' : ' warn'),
      'aria-label': 'Xem ' + plural(finds.length, 'lỗi') + ' của dòng ' + c.index,
      title: 'Bấm để xem chi tiết'
    }, String(finds.length)));
  } else if (c.flag) {
    warnCell.appendChild(h('span', { class: 'cue-flag', title: c.flagNote || 'AI đề nghị soát lại dòng này' }, icon('flag')));
  }

  // dải ghi chú dưới hàng
  const note = row.querySelector('.cue-note');
  clear(note);
  const bits = [];
  const zhCount = countClusters(c.zh);
  const pyCount = countClusters(c.py);
  const mismatch = !!(c.zh.trim() && c.py.trim()) && zhCount !== pyCount;

  row.querySelector('.cue-py').classList.toggle('is-bad', mismatch);
  if (mismatch) {
    bits.push(h('span', { class: 'cue-note-msg', text: 'Dòng Hán có ' + zhCount + ' cụm, dòng pinyin có ' + pyCount + ' cụm.' }));
  }
  const prev = editor.cues[i - 1];
  const next = editor.cues[i + 1];
  if (c.end > 0 && c.start >= c.end) {
    bits.push(h('span', { text: 'Mốc kết thúc phải sau mốc bắt đầu.' }));
  } else {
    // Hai vế rời nhau chứ không phải else-if: một dòng có thể vừa đè lên dòng
    // trên vừa bị dòng dưới đè, và người soát cần biết cả hai.
    if (prev && prev.end > c.start + 0.0005) {
      bits.push(h('span', { text: 'Mốc thời gian chồng lên dòng ' + prev.index + '.' }));
    }
    if (next && c.end > next.start + 0.0005) {
      bits.push(h('span', { text: 'Mốc thời gian chồng lên dòng ' + next.index + '.' }));
    }
  }
  if (c.vi !== c.vi.trim()) {
    bits.push(h('span', { text: 'Câu tiếng Việt còn thừa khoảng trắng ở đầu hoặc cuối dòng.' }));
    bits.push(h('button', {
      type: 'button', class: 'btn btn-quiet btn-sm', tabindex: '-1',
      onclick: () => pushEdit(i, { vi: c.vi.trim() })
    }, 'Cắt bỏ giúp tôi'));
  }
  if (bits.length) note.classList.add('bad');
  else note.classList.remove('bad');

  if (c.zhTouched) {
    // Cố ý KHÔNG tự chạy: người dùng vừa sửa tay dòng Hán, chạy lại pinyin
    // ngay lập tức sẽ đè mất phần pinyin họ cũng vừa sửa.
    bits.push(h('button', {
      type: 'button', class: 'btn btn-ghost btn-sm', tabindex: '-1',
      dataset: { act: 'retok' }
    }, icon('refresh'), 'Sinh lại pinyin cho câu này'));
  }
  if (c.flag && c.flagNote) bits.push(h('span', { text: 'AI ghi chú: ' + c.flagNote }));
  bits.forEach((b) => note.appendChild(b));
}

/** Vẽ lại toàn bộ một hàng, kể cả chữ trong ô (dùng sau khi hoàn tác/lưu). */
function refreshRow(i) {
  const row = editor.rows[i];
  if (!row) return;
  const c = editor.cues[i];
  const active = document.activeElement;
  const put = (sel, value) => {
    const node = row.querySelector(sel);
    if (!node || node === active) return;      // đang gõ dở thì để yên
    if (cellText(node) !== value) setCellText(node, value);
    node.classList.toggle('is-empty', !String(value || '').trim());
  };
  put('.cue-time', clockOf(c.start) + '\n' + clockOf(c.end));
  put('.cue-zh', c.zh);
  put('.cue-py', c.py);
  put('.cue-vi', c.vi);
  row.querySelector('.cue-n').textContent = String(c.index);
  decorateRow(i, row);
  row.classList.toggle('is-hidden', !matchRow(i));
}

/** Vẽ lại một hàng CÙNG hai hàng kề nó.
 *
 *  Chồng lấn thời gian là chuyện của hai hàng, không phải một: kéo dài dòng 2
 *  thì dòng bị đè là dòng 3. Chỉ vẽ lại dòng 2 thì dòng 3 vẫn sạch trơn và
 *  người soát tưởng chỗ đó không sao — tới lúc bấm Lưu mới bị validator chặn,
 *  vừa muộn vừa khó hiểu. */
function refreshRowAround(i) {
  refreshRow(i - 1);
  refreshRow(i);
  refreshRow(i + 1);
}

/** Dựng bảng theo từng đợt để trang không đứng hình với phụ đề 1351 dòng.
 *
 *  Dùng setTimeout chứ KHÔNG dùng requestAnimationFrame: rAF ngừng chạy khi
 *  cửa sổ bị che và có lúc không chạy lại — đo được cảnh bảng dừng ở dòng 1200
 *  trong khi phụ đề có 1351 dòng, nhìn y như file bị thiếu. setTimeout khi tab
 *  ẩn thì chậm lại nhưng luôn chạy cho hết. */
function renderCues() {
  const box = $('#cue-rows');
  if (editor.renderTimer) { clearTimeout(editor.renderTimer); editor.renderTimer = 0; }
  clear(box);
  editor.rows = new Array(editor.cues.length);

  let i = 0;
  const step = () => {
    const frag = document.createDocumentFragment();
    const stop = Math.min(editor.cues.length, i + ROWS_PER_CHUNK);
    for (; i < stop; i++) {
      const row = buildRow(i);
      editor.rows[i] = row;
      frag.appendChild(row);
    }
    box.appendChild(frag);
    if (i < editor.cues.length) editor.renderTimer = setTimeout(step, 0);
    else { editor.renderTimer = 0; updateEmptyBox(); }
  };
  step();
  updateStats();
}

/* ---- 12b.5 lọc và tìm ---- */

function matchRow(i) {
  const c = editor.cues[i];
  if (!c) return false;
  const f = editor.filter;
  if (f === 'error' && !findingsOf(i).length) return false;
  if (f === 'flag' && !c.flag) return false;
  if (f === 'novi' && c.vi.trim()) return false;
  if (f === 'unreviewed' && c.reviewed) return false;
  const q = editor.query;
  if (!q) return true;
  return (c.zh + '\n' + c.py + '\n' + c.vi + '\n' + c.index).toLowerCase().indexOf(q) >= 0;
}

function applyFilter() {
  editor.rows.forEach((row, i) => { if (row) row.classList.toggle('is-hidden', !matchRow(i)); });
  updateEmptyBox();
}

function visibleCount() {
  let n = 0;
  for (let i = 0; i < editor.cues.length; i++) if (matchRow(i)) n++;
  return n;
}

function updateEmptyBox() {
  const box = $('#cue-empty');
  if (!box) return;
  const n = visibleCount();
  const hasFilter = editor.filter !== 'all' || !!editor.query;
  show(box, editor.cues.length > 0 && n === 0);
  box.textContent = hasFilter
    ? 'Không có dòng nào khớp. Bấm “Tất cả” hoặc xoá ô tìm kiếm để xem lại toàn bộ.'
    : 'Chưa có dòng phụ đề nào.';
}

/** Băng đỏ "file có chỗ tool không đọc được".
 *
 *  Ca thật đã đo được: một file 1500 đoạn, trong đó 145 đoạn có mốc thời gian
 *  viết sai kiểu `00:00:30,1000` (mili giây bốn chữ số, do một số công cụ xuất
 *  ra). Bộ tách không nhận những dòng đó là đoạn mới nên dính chúng vào đoạn
 *  ngay trước — bảng hiện ra 1355 dòng, dòng thống kê ghi "không còn lỗi", và
 *  bấm Lưu là ghi đè mất 145 đoạn. Băng này là chỗ duy nhất người dùng biết
 *  chuyện đó đã xảy ra, nên nó nói thẳng con số và nói thẳng đừng lưu vội. */
function renderIntegrityNote() {
  const box = $('#edit-parse-note');
  if (!box) return;
  const it = editor.integrity || {};
  const missing = Number(it.missing_cues || 0);
  const extra = (editor.fileFindings || []).length;
  if (!missing && !extra) { show(box, false); return; }

  const title = $('#edit-parse-title');
  const why = $('#edit-parse-why');
  if (missing) {
    title.textContent = 'File này có ' + it.expected_cues + ' đoạn, tool chỉ đọc được ' +
      it.parsed_cues + '. Thiếu ' + missing + ' đoạn.';
  } else {
    title.textContent = 'File này có ' + plural(extra, 'chỗ') + ' tool không xếp được vào dòng nào.';
  }
  const lines = [];
  if (Number(it.bad_timestamp_count || 0)) {
    lines.push(plural(it.bad_timestamp_count, 'dòng mốc thời gian') + ' viết sai định dạng' +
      (Array.isArray(it.bad_timestamp_lines) && it.bad_timestamp_lines.length
        ? ' (dòng ' + it.bad_timestamp_lines.slice(0, 6).join(', ') +
          (it.bad_timestamp_lines.length > 6 ? '…' : '') + ' trong file)'
        : '') + '.');
  }
  if (missing) {
    lines.push('Những đoạn đó đã bị dính vào đoạn ngay trước, nên đoạn trước lẫn cả số thứ tự và dòng giờ vào phần chữ.');
    lines.push('ĐỪNG bấm Lưu ngay: bản lưu sẽ chỉ còn ' + it.parsed_cues + ' đoạn. ' +
      'Hãy sửa mốc thời gian trong file gốc bằng một trình soạn thảo văn bản rồi mở lại, ' +
      'hoặc đưa file qua tab “Kiểm tra file” trước.');
  }
  why.textContent = lines.join(' ');
  show(box, true);
}

function updateStats() {
  const stats = $('#edit-stats');
  if (!stats) return;
  let errs = 0, warns = 0, flags = 0, novi = 0;
  editor.cues.forEach((c, i) => {
    findingsOf(i).forEach((f) => { if ((f.severity || 'error') === 'error') errs++; else warns++; });
    if (c.flag) flags++;
    if (!c.vi.trim()) novi++;
  });
  const it = editor.integrity || {};
  const missing = Number(it.missing_cues || 0);
  errs += (editor.fileFindings || []).length;
  const bits = [plural(editor.cues.length, 'dòng phụ đề')];
  if (missing) bits.push('THIẾU ' + plural(missing, 'đoạn') + ' so với file gốc');
  bits.push(errs ? plural(errs, 'lỗi') : (missing ? 'các dòng đọc được thì không lỗi' : 'không còn lỗi'));
  if (warns) bits.push(plural(warns, 'cảnh báo'));
  if (flags) bits.push(plural(flags, 'dòng AI đề nghị soát'));
  if (novi) bits.push(plural(novi, 'dòng chưa có tiếng Việt'));
  const done = editor.cues.filter((c) => c.reviewed).length;
  bits.push('đã soát ' + done + '/' + editor.cues.length);
  stats.textContent = bits.join(' · ');
  updateProgressBar(done, editor.cues.length);
}

/* ---- 12b.6 chọn dòng, nghe lại ---- */

function selectCue(i, scroll) {
  if (i < 0 || i >= editor.cues.length) return;
  const old = editor.selected;
  editor.selected = i;
  if (old >= 0 && editor.rows[old]) editor.rows[old].classList.remove('is-selected');
  const row = editor.rows[i];
  if (row) {
    row.classList.add('is-selected');
    if (scroll) row.scrollIntoView({ block: 'nearest' });
  }
  loadCuebox(i);
}

function moveSelection(delta) {
  let i = editor.selected;
  const n = editor.cues.length;
  if (i < 0) { i = delta > 0 ? -1 : n; }
  for (let k = 0; k < n; k++) {
    i += delta;
    if (i < 0 || i >= n) return;
    if (matchRow(i)) { selectCue(i, true); return; }
  }
}

function setPlaying(i) {
  const old = editor.playing;
  editor.playing = i;
  if (old >= 0 && editor.rows[old]) {
    editor.rows[old].classList.remove('is-playing');
    const b = editor.rows[old].querySelector('.cue-play');
    if (b) { clear(b); b.appendChild(icon('play')); }
  }
  if (i >= 0 && editor.rows[i]) {
    editor.rows[i].classList.add('is-playing');
    const b = editor.rows[i].querySelector('.cue-play');
    if (b) { clear(b); b.appendChild(icon('pause')); }
  }
}

/** Phần tử đang phát: video nếu có hình, không thì audio. Một chỗ quyết định
 *  để mọi nút nghe/tua/đặt mốc dùng chung, không mỗi nơi tự đoán. */
function mediaEl() {
  return editor.hasVideo ? $('#edit-video') : $('#edit-audio');
}

function stopPlay() {
  const audio = mediaEl();
  try { audio.pause(); } catch (_) { /* chưa nạp được thì thôi */ }
  editor.stopAt = null;
  editor.freePlay = false;
  setPlaying(-1);
  updatePlayButton();
}

function playCue(i) {
  const c = editor.cues[i];
  if (!c) return;
  const audio = mediaEl();
  editor.freePlay = false;
  if (!editor.audioOk) {
    toast('Chưa nghe lại được đoạn tiếng của dòng này. Xem lời giải thích ở khung màu vàng phía trên bảng.', 'warn', 6000);
    return;
  }
  if (editor.playing === i && !audio.paused) { stopPlay(); return; }

  const from = Math.max(0, c.start - 0.05);
  const to = (c.end > c.start ? c.end : c.start + 3) + 0.15;
  editor.stopAt = to;
  const go = () => {
    try { audio.currentTime = from; } catch (_) { /* trình duyệt chưa cho tua */ }
    const p = audio.play();
    if (p && p.catch) p.catch(() => {
      setPlaying(-1);
      toast('Trình duyệt không cho phát âm thanh. Hãy bấm lại nút ▶ một lần nữa.', 'warn');
    });
  };
  setPlaying(i);
  selectCue(i, false);
  if (audio.readyState >= 1) go();
  else audio.addEventListener('loadedmetadata', go, { once: true });
}

function setAudioSource() {
  // Tên cũ giữ lại cho các chỗ gọi sẵn có; việc thật nằm ở setMediaSource.
  setMediaSource();
}

/**
 * Hỏi máy chủ công việc này có gì để phát: hình (mp4/webm phát thẳng), hay chỉ
 * tiếng, hay chưa có gì. Rồi nạp đúng phần tử và bật khung xem trước.
 *
 * Vì sao hỏi riêng một lượt chứ không đoán từ đuôi file: chỉ máy chủ biết file
 * còn nằm trên đĩa không và link gốc có tải lại được không (nút "Tải video").
 */
async function setMediaSource() {
  const audio = $('#edit-audio');
  const video = $('#edit-video');
  const note = $('#edit-audio-note');
  const box = $('#edit-preview');
  editor.audioOk = false;
  editor.hasVideo = false;
  editor.media = null;
  editor.current = -1;
  stopPlay();
  setOverlay(null);

  const noJob = !editor.jobId || editor.hasAudio === false && !editor.audioUrl;
  if (noJob) {
    audio.removeAttribute('src');
    video.removeAttribute('src');
    show(box, false);
    show(note, true);
    $('#edit-audio-why').textContent = editor.jobId
      ? 'File phụ đề này mở từ đĩa nên không có video hay bản ghi âm đi kèm. Muốn vừa xem vừa sửa, hãy chạy chính video đó ở tab “Tạo phụ đề” rồi bấm “Xem trước và sửa”.'
      : 'Bản phụ đề này không gắn với công việc nào trong chương trình nên không có tiếng để nghe lại.';
    return;
  }

  let info = null;
  try {
    info = await api('/api/jobs/' + encodeURIComponent(editor.jobId) + '/media');
  } catch (_) {
    info = null;
  }
  editor.media = info;
  const playable = !!(info && info.playable && info.video_url);
  const audioUrl = editor.audioUrl || (info && info.audio_url) ||
    '/api/jobs/' + encodeURIComponent(editor.jobId) + '/audio';

  show(box, true);
  show(note, false);
  box.classList.toggle('no-image', !playable);
  show($('#preview-noimage'), !playable);
  const pnote = $('#preview-note');
  const fetchBtn = $('#btn-fetch-preview');
  const noteText = info && info.note ? String(info.note) : '';
  show(pnote, !!noteText);
  $('#preview-note-text').textContent = noteText;
  show(fetchBtn, !!(info && info.can_fetch_preview && editor.videoId));

  if (playable) {
    editor.hasVideo = true;
    editor.videoUrl = info.video_url;
    audio.removeAttribute('src');
    video.preload = 'metadata';
    video.src = info.video_url;
    // Khớp khung đúng tỉ lệ thật của video: hết viền đen hai bên, và phim dọc
    // (9:16) tự kéo cột trái hẹp lại thay vì ăn cả chiều cao màn hình.
    video.addEventListener('loadedmetadata', () => {
      const w = video.videoWidth, h = video.videoHeight;
      if (!w || !h) return;
      const stage = $('#preview-stage');
      if (stage) stage.style.setProperty('--stage-ar', w + ' / ' + h);
      $('#edit-preview').classList.toggle('is-portrait', h > w);
    }, { once: true });
    try { video.load(); } catch (_) { /* Safari */ }
  } else {
    video.removeAttribute('src');
    audio.preload = 'metadata';
    audio.src = audioUrl;
    try { audio.load(); } catch (_) { /* Safari */ }
  }
  updatePlayButton();
  updateTimeLabel();
}

/* ---- 12b.6b khung xem trước: phụ đề đè lên video, chạy theo bảng ---- */

/** Tìm dòng trùng với thời điểm t (giây). Dòng đã xếp theo start nên tìm nhị phân;
 *  trả -1 khi t rơi vào khoảng lặng giữa hai dòng. */
function cueAt(t) {
  const cues = editor.cues;
  let lo = 0, hi = cues.length - 1, best = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (cues[mid].start <= t) { best = mid; lo = mid + 1; } else { hi = mid - 1; }
  }
  if (best < 0) return -1;
  const c = cues[best];
  const end = c.end > c.start ? c.end : c.start + 3;
  return t < end + 0.001 ? best : -1;
}

function setOverlay(c) {
  $('#ov-zh').textContent = c ? c.zh : '';
  $('#ov-py').textContent = c ? c.py : '';
  $('#ov-vi').textContent = c ? c.vi : '';
}

/** Vẽ lại chữ đè lên hình cho dòng hiện tại — gọi sau mỗi lần sửa ô, để người
 *  dùng thấy ngay câu vừa sửa nằm trên hình ra sao. */
function refreshOverlay() {
  if (editor.current >= 0) setOverlay(editor.cues[editor.current] || null);
}

function setCurrentCue(i) {
  if (i === editor.current) return;
  const old = editor.current;
  editor.current = i;
  if (old >= 0 && editor.rows[old]) editor.rows[old].classList.remove('is-current');
  if (i >= 0 && editor.rows[i]) {
    editor.rows[i].classList.add('is-current');
    if (editor.follow && editor.freePlay) {
      // Cuộn mượt qua hàng trăm hàng trên iMac 2017 là vài giây giật, mà ở
      // khoảng cách đó chuyển động cũng không truyền đạt gì. Gần thì mượt (nói
      // "bảng đang đi theo video"), xa thì nhảy thẳng.
      const xa = Math.abs(i - (old < 0 ? i : old)) > 40;
      editor.rows[i].scrollIntoView({ block: 'center', behavior: xa ? 'auto' : 'smooth' });
    }
  }
  setOverlay(i >= 0 ? editor.cues[i] : null);
}

function fmtClock(sec) {
  sec = Math.max(0, sec || 0);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.floor(sec % 60);
  const mm = String(m).padStart(2, '0'), ss = String(s).padStart(2, '0');
  return h ? h + ':' + mm + ':' + ss : mm + ':' + ss;
}

function updateTimeLabel() {
  const m = mediaEl();
  const dur = isFinite(m.duration) ? m.duration : 0;
  $('#pv-time').textContent = fmtClock(m.currentTime) + ' / ' + fmtClock(dur);
  const seek = $('#pv-seek');
  if (!seek.matches(':active') && dur > 0) seek.value = String(Math.round(m.currentTime / dur * 1000));
}

function updatePlayButton() {
  const btn = $('#pv-play');
  if (!btn) return;
  const m = mediaEl();
  const playing = m && !m.paused && !m.ended;
  clear(btn);
  btn.appendChild(icon(playing ? 'pause' : 'play'));
  btn.title = playing ? 'Dừng (Shift+Space)' : 'Phát (Shift+Space)';
}

/** Một nhịp timeupdate dùng chung cho cả <audio> lẫn <video>. */
function onMediaTime() {
  const m = mediaEl();
  if (editor.stopAt !== null && m.currentTime >= editor.stopAt) {
    const c = CB.loop && editor.playing >= 0 ? editor.cues[editor.playing] : null;
    if (c) { try { m.currentTime = Math.max(0, c.start - 0.05); } catch (_) { /* */ } }
    else stopPlay();
  }
  setCurrentCue(cueAt(m.currentTime));
  updateTimeLabel();
  drawWave();
}

function togglePlay() {
  const m = mediaEl();
  if (!editor.audioOk) {
    toast('Chưa phát được. Xem ghi chú trong khung xem trước.', 'warn', 5000);
    return;
  }
  if (!m.paused && !m.ended) { m.pause(); editor.freePlay = false; editor.stopAt = null; setPlaying(-1); updatePlayButton(); return; }
  editor.stopAt = null;          // phát liên tục, không dừng ở cuối dòng
  editor.freePlay = true;
  setPlaying(-1);
  const p = m.play();
  if (p && p.catch) p.catch(() => toast('Trình duyệt không cho phát. Hãy bấm lại nút ▶ một lần nữa.', 'warn'));
  updatePlayButton();
}

function seekTo(t) {
  const m = mediaEl();
  try { m.currentTime = Math.max(0, t); } catch (_) { /* chưa nạp */ }
  setCurrentCue(cueAt(Math.max(0, t)));
  updateTimeLabel();
}

/** Nhảy tới dòng kề: tính từ dòng đang trùng video, không có thì từ dòng đang chọn. */
function jumpCue(delta) {
  const base = editor.current >= 0 ? editor.current : (editor.selected >= 0 ? editor.selected : -1);
  let i = base + delta;
  if (base < 0) i = delta > 0 ? 0 : editor.cues.length - 1;
  if (i < 0 || i >= editor.cues.length) return;
  selectCue(i, true);
  seekTo(editor.cues[i].start);
}

/** Đặt mốc của dòng đang chọn theo vị trí video. Đi qua pushEdit để hoàn tác
 *  được và để kiểm chồng lấn chạy như khi sửa tay. */
function setTimingFromVideo(which) {
  const i = editor.selected;
  const c = editor.cues[i];
  if (!c) { toast('Hãy bấm chọn một dòng trong bảng trước.', 'info'); return; }
  const t = Math.round(mediaEl().currentTime * 1000) / 1000;
  if (which === 'start') {
    if (t >= c.end) { toast('Mốc bắt đầu phải nhỏ hơn mốc kết thúc (' + clockOf(c.end) + ').', 'warn'); return; }
    pushEdit(i, { start: t });
  } else {
    if (t <= c.start) { toast('Mốc kết thúc phải lớn hơn mốc bắt đầu (' + clockOf(c.start) + ').', 'warn'); return; }
    pushEdit(i, { end: t });
  }
  toast('Đã đặt mốc ' + (which === 'start' ? 'bắt đầu' : 'kết thúc') + ' của dòng ' + c.index + ' = ' + clockOf(t) + '.', 'ok', 2500);
}

function nudgeTiming(delta) {
  const i = editor.selected;
  const c = editor.cues[i];
  if (!c) { toast('Hãy bấm chọn một dòng trong bảng trước.', 'info'); return; }
  const start = Math.max(0, Math.round((c.start + delta) * 1000) / 1000);
  const end = Math.max(start + 0.1, Math.round((c.end + delta) * 1000) / 1000);
  pushEdit(i, { start, end });
}

/** Tải bản có hình cho phim đã chạy mà mới chỉ có tiếng, rồi nạp lại khung xem trước. */
async function fetchPreviewVideo() {
  const btn = $('#btn-fetch-preview');
  if (!editor.videoId) return;
  const restore = busyButton(btn, 'Đang tải…');
  try {
    await runAction('fetch_preview', btn, { video_id: editor.videoId });
    // Việc chạy dài: chờ tới khi máy chủ báo đã có hình (tối đa ~30 phút).
    for (let k = 0; k < 360; k++) {
      await new Promise((r) => setTimeout(r, 5000));
      let info = null;
      try { info = await api('/api/jobs/' + encodeURIComponent(editor.jobId) + '/media'); } catch (_) { info = null; }
      if (info && info.playable) { await setMediaSource(); toast('Đã có video. Bấm ▶ để xem trước có phụ đề.', 'ok', 6000); return; }
      if (!isEditTabOpen()) return;
    }
  } catch (err) {
    await reportError(err, 'Chưa tải được video để xem trước');
  } finally {
    restore();
  }
}

/** Hộp "Xuất file": danh sách file của công việc, tải về là tuỳ chọn cuối. */
async function toggleExportBox(force) {
  const box = $('#edit-export');
  const open = force === undefined ? box.hidden : !!force;
  if (!open) { show(box, false); return; }
  const list = $('#edit-export-list');
  clear(list);
  if (!editor.jobId) {
    list.appendChild(h('span', { class: 'exportbox-sub', text: 'File mở từ máy: bấm “Lưu và tải về” ở trên để lấy bản đã sửa.' }));
    show(box, true);
    return;
  }
  list.appendChild(h('span', { class: 'exportbox-sub', text: 'Đang lấy danh sách file…' }));
  show(box, true);
  let result = null;
  try { result = await api('/api/jobs/' + encodeURIComponent(editor.jobId) + '/result'); } catch (_) { result = null; }
  clear(list);
  const files = (result && (result.files || (result.result && result.result.files))) || {};
  const downloads = (result && (result.downloads || (result.result && result.result.downloads))) || {};
  const items = [
    ['srt', 'Phụ đề tiếng Trung + pinyin (.srt)'],
    ['vi', 'Phụ đề tiếng Việt (_vi.srt)'],
    ['bilingual_ass', 'Bản song ngữ cho Aegisub (_song-ngu.ass)'],
    ['ass', 'Bản .ass một ngôn ngữ'],
    ['report', 'Báo cáo (.html)'],
    ['bundle', 'Dữ liệu cho backend (.bundle.json)']
  ];
  let any = false;
  for (const [kind, label] of items) {
    const entry = files[kind];
    const url = downloads[kind] || downloads[kind === 'vi' ? 'vi_srt' : kind] ||
      (entry ? '/api/download/' + encodeURIComponent(editor.jobId) + '/' + encodeURIComponent(kind) : '');
    if (!entry && !url) continue;
    any = true;
    list.appendChild(h('a', { class: 'btn btn-ghost btn-sm', href: url, download: '' }, icon('download'), ' ' + label));
  }
  if (!any) list.appendChild(h('span', { class: 'exportbox-sub', text: 'Chưa có file nào — công việc này chưa chạy tới bước xuất file.' }));
}


/* ---- 12c. bàn làm việc: ô sửa dòng đang chọn, dạng sóng, tách/gộp, tìm-thay, đã soát ---- */

const CB = { i: -1, loop: false, autoplay: true, filling: false, reviewTimer: 0 };
const WAVE = { peaks: null, rate: 50, duration: 0, loading: false, drag: null, dragT: 0 };

function cbEl(k) { return $('#cb-' + k); }

/** Sau khi NGƯỜI DÙNG chọn một dòng (bấm hàng, phím, Xong-dòng-sau): nạp ô sửa và tự phát. */
function afterUserSelect(i) {
  if (i !== undefined && i >= 0) selectCue(i, true);
  const k = editor.selected;
  if (k < 0) return;
  loadCuebox(k);
  if (CB.autoplay && editor.audioOk && editor.playing !== k) playCue(k);
  else if (editor.audioOk) seekTo(editor.cues[k].start);
}

function stepCue(delta) {
  cbCommit();
  const n = editor.cues.length;
  if (!n) return;
  let i = editor.selected < 0 ? 0 : editor.selected + delta;
  i = Math.max(0, Math.min(n - 1, i));
  afterUserSelect(i);
}

/** Đổ dòng i vào ô sửa. i = -1 là xoá trắng. */
function loadCuebox(i) {
  const c = editor.cues[i];
  CB.filling = true;
  CB.i = c ? i : -1;
  cbEl('n').textContent = c ? 'Dòng ' + c.index + ' / ' + editor.cues.length : 'Chưa chọn dòng';
  cbEl('start').value = c ? clockOf(c.start) : '';
  cbEl('end').value = c ? clockOf(c.end) : '';
  cbEl('zh').value = c ? c.zh : '';
  cbEl('py').value = c ? c.py : '';
  cbEl('vi').value = c ? c.vi : '';
  for (const k of ['zh', 'py', 'vi', 'start', 'end']) cbEl(k).disabled = !c;
  cbEl('done').textContent = c && c.reviewed ? '✓ Đã soát — dòng sau' : '✓ Xong, dòng sau';
  CB.filling = false;
  cbLiveCheck();
  drawWave();
}

/** Ô sửa phản ánh lại dòng sau khi cue đổi từ chỗ khác (hoàn tác, kéo mép sóng, nút mốc). */
function syncCuebox() {
  const c = editor.cues[CB.i];
  if (!c) return;
  const active = document.activeElement;
  CB.filling = true;
  if (active !== cbEl('zh')) cbEl('zh').value = c.zh;
  if (active !== cbEl('py')) cbEl('py').value = c.py;
  if (active !== cbEl('vi')) cbEl('vi').value = c.vi;
  if (active !== cbEl('start')) cbEl('start').value = clockOf(c.start);
  if (active !== cbEl('end')) cbEl('end').value = clockOf(c.end);
  cbEl('done').textContent = c.reviewed ? '✓ Đã soát — dòng sau' : '✓ Xong, dòng sau';
  CB.filling = false;
  cbLiveCheck();
}

/** Gõ tới đâu thấy tới đó: chữ đè trên hình, đếm cụm, tốc độ đọc. Chưa ghi vào bảng. */
function cbLiveCheck() {
  const c = editor.cues[CB.i];
  const zh = cbEl('zh').value.replace(/\n+/g, ' ');
  const py = cbEl('py').value.replace(/\n+/g, ' ');
  const vi = cbEl('vi').value.replace(/\n+/g, ' ');
  const zc = zh.trim() ? zh.trim().split(/\s+/).length : 0;
  const pc = py.trim() ? py.trim().split(/\s+/).length : 0;
  const bad = !!(zh.trim() && py.trim()) && zc !== pc;
  cbEl('zh-count').textContent = c ? zc + ' cụm' : '';
  cbEl('py-count').textContent = c ? (bad ? pc + ' cụm — lệch với dòng Hán' : pc + ' cụm') : '';
  cbEl('py-count').classList.toggle('is-bad', bad);
  cbEl('py').classList.toggle('is-bad', bad);
  const a = parseClock(cbEl('start').value.trim()), b = parseClock(cbEl('end').value.trim());
  const okTime = a !== null && b !== null && b > a;
  cbEl('start').classList.toggle('is-bad', c && a === null);
  cbEl('end').classList.toggle('is-bad', c && (b === null || (a !== null && b <= a)));
  const dur = okTime ? b - a : 0;
  cbEl('dur').textContent = c && okTime ? dur.toFixed(2) + 's' : '';
  const han = zh.replace(/[^\u4e00-\u9fff]/g, '').length;
  const cps = dur > 0 ? han / dur : 0;
  const cpsEl = cbEl('cps');
  cpsEl.textContent = c && dur > 0 ? cps.toFixed(1) + ' chữ/giây' : '';
  cpsEl.classList.toggle('is-fast', cps > 9 && cps <= 12);
  cpsEl.classList.toggle('is-toofast', cps > 12);
  if (c && CB.i === editor.current) setOverlay({ zh, py, vi });
}

/** Ghi những gì đang gõ trong ô sửa vào bảng (một bước hoàn tác cho mỗi lần). */
function cbCommit() {
  const i = CB.i;
  const c = editor.cues[i];
  if (!c || CB.filling) return;
  const patch = {};
  const zh = cbEl('zh').value.replace(/\n+/g, ' ').trim();
  const py = cbEl('py').value.replace(/\n+/g, ' ').trim();
  const vi = cbEl('vi').value.replace(/\n+/g, ' ').trim();
  if (zh !== c.zh) { patch.zh = zh; patch.zhTouched = true; }
  if (py !== c.py) patch.py = py;
  if (vi !== c.vi) patch.vi = vi;
  const a = parseClock(cbEl('start').value.trim()), b = parseClock(cbEl('end').value.trim());
  if (a !== null && b !== null && b > a && (a !== c.start || b !== c.end)) { patch.start = a; patch.end = b; }
  if (!Object.keys(patch).length) return;
  const moiThem = c.autoPinyin && patch.zh && !c.py.trim() && !py;
  pushEdit(i, patch);
  if (moiThem) {
    // Dòng vừa thêm, người dùng vừa gõ xong chữ Hán, ô pinyin còn trống: sinh
    // ngay. Chỉ làm ĐÚNG MỘT LẦN cho mỗi dòng mới — sau đó pinyin là của họ.
    editor.cues[i].autoPinyin = false;
    retokenizeCue(i, null);
  }
}

function updateProgressBar(done, total) {
  const bar = $('#cb-progress-bar'), txt = $('#cb-progress-text');
  if (!bar) return;
  bar.style.width = total ? Math.round(done / total * 100) + '%' : '0%';
  txt.textContent = total ? 'Đã soát ' + done + '/' + total + ' dòng' : '';
}

function doneAndNext() {
  const i = CB.i;
  const c = editor.cues[i];
  if (!c) return;
  cbCommit();
  if (!c.reviewed) { c.reviewed = true; decorateRow(i); scheduleReviewSave(); updateStats(); }
  if (i + 1 < editor.cues.length) afterUserSelect(i + 1);
  else { stopPlay(); toast('Đã tới dòng cuối. Bấm Lưu để ghi lại.', 'ok', 4000); }
}

function scheduleReviewSave() {
  if (!editor.jobId) return;
  clearTimeout(CB.reviewTimer);
  CB.reviewTimer = setTimeout(async () => {
    const reviewed = editor.cues.filter((c) => c.reviewed).map((c) => c.index);
    try { await api('/api/jobs/' + encodeURIComponent(editor.jobId) + '/review', { method: 'PUT', body: { reviewed } }); }
    catch (_) { /* dấu đã soát là tiện ích, mất thì soát lại, không chặn việc chính */ }
  }, 800);
}

async function loadReviewMarks() {
  if (!editor.jobId) return;
  try {
    const res = await api('/api/jobs/' + encodeURIComponent(editor.jobId) + '/review');
    const set = new Set((res.reviewed || []).map(Number));
    editor.cues.forEach((c, i) => { c.reviewed = set.has(c.index); if (editor.rows[i]) decorateRow(i); });
    updateStats();
    if (CB.i >= 0) syncCuebox();
  } catch (_) { /* không có cũng được */ }
}

/* ---- tách / gộp / xoá ---- */

function renumberCues(list) { list.forEach((c, k) => { c.index = k + 1; }); return list; }

function splitCueAtCursor() {
  cbCommit();
  const i = CB.i;
  const c = editor.cues[i];
  if (!c) return;
  const ta = cbEl('zh');
  const zh = c.zh;
  // Bấm nút "Tách tại con trỏ" bằng chuột làm ô chữ Hán mất con trỏ trước khi
  // hàm này chạy (Chrome/Edge chuyển focus sang nút), nên `activeElement` không
  // còn là ô đó và mọi lần tách đều rơi vào giữa dòng. Vị trí con trỏ được ghi
  // lại mỗi lần người dùng gõ hoặc bấm trong ô, và dùng lại ở đây.
  const pos = document.activeElement === ta
    ? (ta.selectionStart || 0)
    : (Number.isInteger(CB.zhCaret) ? CB.zhCaret : Math.floor(zh.length / 2));
  const left = zh.lastIndexOf(' ', Math.max(0, pos - 1));
  const right = zh.indexOf(' ', pos);
  if (left < 0 && right < 0) { toast('Dòng này chỉ có một cụm, không tách được.', 'warn'); return; }
  const cut = right < 0 ? left : left < 0 ? right : (pos - left <= right - pos ? left : right);
  const zhA = zh.slice(0, cut).trim(), zhB = zh.slice(cut + 1).trim();
  if (!zhA || !zhB) { toast('Đặt con trỏ vào giữa dòng chữ Hán rồi bấm Tách.', 'warn'); return; }
  const nA = zhA.split(' ').length;
  const pyToks = c.py.trim() ? c.py.trim().split(' ') : [];
  const pyA = pyToks.slice(0, nA).join(' '), pyB = pyToks.slice(nA).join(' ');
  const total = Math.max(0.2, c.end - c.start);
  const frac = zhA.replace(/\s/g, '').length / Math.max(1, zh.replace(/\s/g, '').length);
  const mid = Math.round((c.start + total * frac) * 1000) / 1000;
  // `findings: []` chứ KHÔNG phải null. `findingsOf()` coi null là "chưa có" rồi
  // đi tra bảng lỗi theo SỐ DÒNG — mà số dòng vừa bị đánh lại, nên dòng mới sẽ
  // đeo danh sách lỗi của một câu hoàn toàn khác. Mảng rỗng nghĩa là "đã biết:
  // chưa kiểm", đúng thực tế sau khi cắt đôi một câu.
  const a = Object.assign({}, c, { zh: zhA, py: pyA, end: mid, reviewed: false, findings: [] });
  const b = Object.assign({}, c, { zh: zhB, py: pyB, start: mid, vi: '', reviewed: false, flags: [], flag: false, flagNote: '', findings: [] });
  const list = snapshotCues(editor.cues);
  list.splice(i, 1, a, b);
  replaceAllCues(renumberCues(list), 'tách dòng ' + c.index, i);
  selectCue(i, true);
  toast('Đã tách thành dòng ' + (i + 1) + ' và ' + (i + 2) + '. Dòng sau chưa có tiếng Việt — bấm "Dịch lại câu này".', 'ok', 6000);
}

/** Khoảng trống nhỏ nhất đáng để chen một dòng phụ đề vào (giây). */
const MIN_NEW_CUE = 0.30;

/** Độ dài mặc định của một dòng vừa thêm, khi còn đủ chỗ (giây). */
const NEW_CUE_LEN = 1.60;

/** Thêm một dòng phụ đề mới ngay sau dòng đang chọn.
 *
 *  Vì sao cần: máy gỡ băng bỏ sót cả câu là chuyện thường — người nói lí nhí,
 *  hai người nói chồng lời, hoặc có tiếng nhạc đè. Trước đây trình sửa chỉ có
 *  tách, gộp và xoá, nên chỗ bị sót KHÔNG có đường nào chen thêm: tách thì phải
 *  cắt một dòng đang đúng làm đôi, và mốc giờ vẫn nằm sai chỗ.
 *
 *  Mốc giờ của dòng mới, theo thứ tự ưu tiên:
 *    1. Vị trí video đang dừng, nếu nó nằm sau chỗ dòng đang chọn bắt đầu. Đây là
 *       cách người soát thật sự làm: nghe thấy câu bị sót thì dừng ngay ở đó.
 *    2. Không có video thì lấy ngay sau chỗ dòng đang chọn kết thúc.
 *  Điểm kết thúc lấy 1,6 giây, nhưng bị cắt lại để KHÔNG BAO GIỜ đè lên dòng sau.
 *  Không còn chỗ thì nói thẳng là không còn chỗ, chứ không tạo ra một dòng chồng
 *  mốc giờ rồi để bộ kiểm báo lỗi.
 */
function insertCueAfter() {
  if (!editor.loaded || !editor.cues.length) return;
  commitActiveCell();
  const i = CB.i >= 0 ? CB.i : editor.selected;
  const c = editor.cues[i];
  if (!c) { toast('Hãy chọn một dòng trước, dòng mới sẽ nằm ngay sau nó.', 'warn'); return; }
  const next = editor.cues[i + 1] || null;

  let start = c.end;
  const media = mediaEl();
  const at = media && isFinite(media.currentTime) ? Number(media.currentTime) : NaN;
  if (isFinite(at) && at > c.start + 0.01) start = at;
  if (next && start < c.end) start = c.end;

  // Trần cứng: không được lấn sang dòng sau, và không được lùi vào dòng trước.
  const ceiling = next ? next.start : start + NEW_CUE_LEN;
  if (start < c.end) start = c.end;
  if (next && start >= next.start - 0.001) start = Math.max(c.end, next.start - MIN_NEW_CUE);

  const room = (next ? next.start : start + NEW_CUE_LEN) - start;
  if (room < MIN_NEW_CUE - 0.001) {
    dialog({
      title: 'Không còn chỗ để chen dòng mới',
      kind: 'warn',
      body: [
        h('p', { text: 'Dòng ' + c.index + ' và dòng ngay sau nó dính liền nhau, không còn khe trống nào ' +
          'đủ cho một dòng phụ đề.' }),
        h('p', { class: 'field-help', text: 'Cách làm: kéo mép phải của vùng xanh trên dạng sóng để dòng ' +
          'này kết thúc sớm hơn một chút, hoặc bấm “Kết thúc = vị trí video” ở chỗ hẹp bạn muốn chen, rồi ' +
          'bấm lại “Thêm dòng”.' })
      ]
    });
    return;
  }
  const end = Math.round((start + Math.min(NEW_CUE_LEN, room)) * 1000) / 1000;
  start = Math.round(start * 1000) / 1000;

  const moi = {
    index: c.index + 1, start, end,
    zh: '', py: '', vi: '',
    flags: [], flag: false, flagNote: '',
    findings: [], reviewed: false, zhTouched: false,
    // Dòng do người dùng tự thêm và chưa có pinyin: lần đầu gõ xong chữ Hán thì
    // sinh pinyin ngay, vì không có pinyin nào của họ để mà đè lên.
    autoPinyin: true
  };
  const list = snapshotCues(editor.cues);
  list.splice(i + 1, 0, moi);
  replaceAllCues(renumberCues(list), 'thêm dòng sau dòng ' + c.index, i + 1);
  afterUserSelect(i + 1);
  // Con trỏ vào thẳng ô chữ Hán: gõ được ngay, không phải bấm thêm lần nào.
  const ta = cbEl('zh');
  if (ta) { try { ta.focus(); } catch (_) { /* cửa sổ chưa được chọn */ } }
  toast('Đã thêm dòng ' + (i + 2) + ' (' + clockOf(start) + ' → ' + clockOf(end) +
        '). Gõ chữ Hán vào ô Tiếng Trung; pinyin sẽ tự sinh.', 'ok', 7000);
}

function joinLine(x, y) {
  x = (x || '').trim(); y = (y || '').trim();
  if (!x) return y;
  if (!y) return x;
  return /[。，、？！：；…—”’》）]$/.test(x) ? x + y : x + ' ' + y;
}

function mergeWithNext() {
  cbCommit();
  const i = CB.i;
  const a = editor.cues[i], b = editor.cues[i + 1];
  if (!a) return;
  if (!b) { toast('Đây là dòng cuối, không có dòng sau để gộp.', 'warn'); return; }
  const m = Object.assign({}, a, {
    zh: joinLine(a.zh, b.zh), py: joinLine(a.py, b.py),
    vi: [a.vi, b.vi].map((x) => (x || '').trim()).filter(Boolean).join(' '),
    end: b.end, reviewed: false, findings: []
  });
  const list = snapshotCues(editor.cues);
  list.splice(i, 2, m);
  replaceAllCues(renumberCues(list), 'gộp dòng ' + a.index + ' và ' + b.index, i);
  selectCue(i, true);
  toast('Đã gộp hai dòng.', 'ok', 3000);
}

async function deleteCue() {
  const i = CB.i;
  const c = editor.cues[i];
  if (!c) return;
  const ok = await dialog({
    title: 'Xoá dòng ' + c.index + '?', kind: 'warn',
    body: 'Dòng “' + (c.zh || '').slice(0, 40) + '” sẽ bị xoá khỏi cả bản tiếng Trung lẫn tiếng Việt. Hoàn tác được bằng Ctrl+Z.',
    buttons: [{ id: 'del', label: 'Xoá', danger: true }, { id: 'cancel', label: 'Huỷ', primary: true }]
  });
  if (ok !== 'del') return;
  const list = snapshotCues(editor.cues);
  list.splice(i, 1);
  replaceAllCues(renumberCues(list), 'xoá dòng ' + c.index, i);
  if (editor.cues.length) selectCue(Math.min(i, editor.cues.length - 1), true);
}

/* ---- sinh lại pinyin / dịch lại một câu ---- */

async function cbRegenPinyin() {
  cbCommit();
  const i = CB.i;
  const c = editor.cues[i];
  if (!c) return;
  const restore = busyButton(cbEl('regen'), 'Đang sinh…');
  try {
    const prev = i > 0 ? (editor.cues[i - 1].py || '').trim() : '';
    const body = { zh: c.zh, video_id: editor.videoId, capitalize: !prev || /[。？！”》）]$/.test(prev) };
    const res = await api('/api/retokenize', { method: 'POST', body });
    const patch = {};
    if (res.zh_line && res.zh_line !== c.zh) patch.zh = res.zh_line;
    if (res.py_line) patch.py = res.py_line;
    if (Object.keys(patch).length) { pushEdit(i, patch); toast('Đã sinh lại pinyin.', 'ok', 2500); }
    else toast('Pinyin không đổi.', 'info', 2500);
  } catch (err) {
    await reportError(err, 'Chưa sinh lại được pinyin');
  } finally { restore(); }
}

async function cbRetranslate() {
  cbCommit();
  const i = CB.i;
  const c = editor.cues[i];
  if (!c) return;
  if (!editor.jobId) { toast('File mở từ máy không gắn với công việc nào nên chưa dịch lại được.', 'warn'); return; }
  const restore = busyButton(cbEl('retranslate'), 'Đang dịch…');
  try {
    const before = editor.cues.slice(Math.max(0, i - 3), i).map((x) => x.zh + (x.vi ? '  =>  ' + x.vi : ''));
    const after = editor.cues.slice(i + 1, i + 4).map((x) => x.zh);
    const res = await api('/api/translate-line', { method: 'POST', body: { job_id: editor.jobId, zh: c.zh, before, after } });
    if (res.vi) {
      pushEdit(i, { vi: res.vi });
      toast('Đã dịch lại' + (res.provider ? ' bằng ' + res.provider : '') + '.', 'ok', 3000);
    }
  } catch (err) {
    await reportError(err, 'Chưa dịch lại được câu này');
  } finally { restore(); }
}

/* ---- dạng sóng ---- */

async function loadPeaks() {
  if (!editor.jobId || WAVE.loading) { drawWave(); return; }
  WAVE.loading = true;
  try {
    const r = await api('/api/jobs/' + encodeURIComponent(editor.jobId) + '/peaks');
    const bin = atob(r.peaks_b64 || '');
    const arr = new Uint8Array(bin.length);
    for (let k = 0; k < bin.length; k++) arr[k] = bin.charCodeAt(k);
    WAVE.peaks = arr;
    WAVE.rate = Number(r.rate) || 50;
    WAVE.duration = Number(r.duration) || 0;
  } catch (_) {
    WAVE.peaks = null;
  } finally {
    WAVE.loading = false;
  }
  show($('#wave-empty'), !WAVE.peaks);
  drawWave();
}

const WAVE_SPAN = 10;   // giây hiện trên khung
const WAVE_LEAD = 3;    // giây phía trước vị trí phát

function waveTimeAt(x) {
  const cv = $('#wave-canvas');
  const w = cv.clientWidth || 1;
  const t = mediaEl().currentTime || 0;
  return (t - WAVE_LEAD) + x / w * WAVE_SPAN;
}

function drawWave() {
  const cv = $('#wave-canvas');
  if (!cv || !editor.loaded || cv.closest('#edit-work').hidden) return;
  const box = cv.parentElement;
  const w = box.clientWidth, hgt = box.clientHeight || 84;
  if (!w) return;
  const dpr = window.devicePixelRatio || 1;
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(hgt * dpr)) {
    cv.width = Math.round(w * dpr); cv.height = Math.round(hgt * dpr);
  }
  const ctx = cv.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, hgt);
  const now = mediaEl().currentTime || 0;
  const t0 = now - WAVE_LEAD;
  const pps = w / WAVE_SPAN;
  const css = getComputedStyle(document.documentElement);
  const accent = css.getPropertyValue('--accent').trim() || '#1f6feb';
  const muted = css.getPropertyValue('--muted').trim() || '#888';

  // dải các dòng: dòng đang chọn tô đậm, các dòng khác nhạt
  const sel = CB.i;
  const dragged = WAVE.drag;
  for (let i = 0; i < editor.cues.length; i++) {
    const c = editor.cues[i];
    let a = c.start, b = c.end;
    if (dragged && i === sel) { if (dragged === 'start') a = WAVE.dragT; else b = WAVE.dragT; }
    if (b < t0 || a > t0 + WAVE_SPAN) continue;
    const x1 = (a - t0) * pps, x2 = (b - t0) * pps;
    ctx.fillStyle = i === sel ? accent : muted;
    ctx.globalAlpha = i === sel ? 0.22 : 0.10;
    ctx.fillRect(x1, 0, Math.max(1, x2 - x1), hgt);
    ctx.globalAlpha = i === sel ? 0.9 : 0.35;
    ctx.fillRect(x1, 0, 1, hgt); ctx.fillRect(x2 - 1, 0, 1, hgt);
    if (i === sel) { ctx.fillStyle = accent; ctx.fillRect(x1, 0, 3, hgt); ctx.fillRect(x2 - 3, 0, 3, hgt); }
  }
  ctx.globalAlpha = 1;

  // dạng sóng
  if (WAVE.peaks) {
    ctx.fillStyle = css.getPropertyValue('--text-2').trim() || '#666';
    const mid = hgt / 2;
    for (let x = 0; x < w; x++) {
      const sec = t0 + x / pps;
      if (sec < 0) continue;
      const idx = Math.floor(sec * WAVE.rate);
      if (idx >= WAVE.peaks.length) break;
      const v = WAVE.peaks[idx] / 255;
      const hh = Math.max(1, v * hgt * 0.46);
      ctx.fillRect(x, mid - hh, 1, hh * 2);
    }
  }

  // vạch giây
  ctx.fillStyle = muted; ctx.globalAlpha = 0.5; ctx.font = '10px ' + (css.getPropertyValue('--font-mono').trim() || 'monospace');
  for (let sec = Math.ceil(t0); sec < t0 + WAVE_SPAN; sec++) {
    if (sec < 0) continue;
    const x = (sec - t0) * pps;
    ctx.fillRect(x, hgt - 6, 1, 6);
    if (sec % 2 === 0) ctx.fillText(fmtClock(sec), x + 2, hgt - 8);
  }
  ctx.globalAlpha = 1;

  // đầu đọc
  const px = (now - t0) * pps;
  ctx.fillStyle = '#e5484d';
  ctx.fillRect(px - 1, 0, 2, hgt);
}

function wireWave() {
  const el = $('#wave');
  if (!el) return;
  const hitEdge = (t) => {
    const c = editor.cues[CB.i];
    if (!c) return null;
    const tol = WAVE_SPAN / (el.clientWidth || 400) * 6;   // 6px
    if (Math.abs(t - c.start) <= tol) return 'start';
    if (Math.abs(t - c.end) <= tol) return 'end';
    return null;
  };
  el.addEventListener('pointerdown', (ev) => {
    if (!editor.loaded || !editor.audioOk) return;
    const rect = el.getBoundingClientRect();
    const t = waveTimeAt(ev.clientX - rect.left);
    const edge = hitEdge(t);
    if (edge) {
      WAVE.drag = edge; WAVE.dragT = t; el.classList.add('is-dragging'); el.setPointerCapture(ev.pointerId);
    } else {
      seekTo(Math.max(0, t));
    }
  });
  el.addEventListener('pointermove', (ev) => {
    if (!WAVE.drag) {
      const rect = el.getBoundingClientRect();
      el.style.cursor = hitEdge(waveTimeAt(ev.clientX - rect.left)) ? 'ew-resize' : 'crosshair';
      return;
    }
    const rect = el.getBoundingClientRect();
    WAVE.dragT = Math.max(0, waveTimeAt(ev.clientX - rect.left));
    drawWave();
  });
  const finish = () => {
    if (!WAVE.drag) return;
    const c = editor.cues[CB.i];
    const t = Math.round(WAVE.dragT * 1000) / 1000;
    const which = WAVE.drag;
    WAVE.drag = null; el.classList.remove('is-dragging');
    if (!c) return;
    if (which === 'start' && t < c.end - 0.1) pushEdit(CB.i, { start: t });
    else if (which === 'end' && t > c.start + 0.1) pushEdit(CB.i, { end: t });
    else toast('Mốc bắt đầu phải nhỏ hơn mốc kết thúc.', 'warn');
    drawWave();
  };
  el.addEventListener('pointerup', finish);
  el.addEventListener('pointercancel', finish);
  window.addEventListener('resize', () => drawWave());
  // Bắt cả những lần cột đổi bề rộng mà cửa sổ không đổi (nút Thu nhỏ / Phóng to,
  // băng cảnh báo hiện ra đẩy bố cục). Không vi phạm luật cấm nghe sự kiện cuộn.
  if (window.ResizeObserver) new ResizeObserver(() => drawWave()).observe($('#wave'));
}

/* ---- tìm và thay thế ---- */

async function openFindReplace() {
  cbCommit();
  const f = h('input', { class: 'input', placeholder: 'Chữ cần tìm…', autocomplete: 'off' });
  const r = h('input', { class: 'input', placeholder: 'Thay bằng… (để trống = xoá)', autocomplete: 'off' });
  const cz = h('input', { type: 'checkbox', checked: 'checked' });
  const cp = h('input', { type: 'checkbox', checked: 'checked' });
  const cv = h('input', { type: 'checkbox', checked: 'checked' });
  const cs = h('input', { type: 'checkbox' });
  const body = [
    h('div', { class: 'frdialog' },
      h('label', { class: 'field-label', text: 'Tìm' }), f,
      h('label', { class: 'field-label', text: 'Thay bằng' }), r,
      h('div', { class: 'pv-group' },
        h('b', { text: 'Trong cột:' }),
        h('label', { class: 'pv-check' }, cz, ' Hán'),
        h('label', { class: 'pv-check' }, cp, ' Pinyin'),
        h('label', { class: 'pv-check' }, cv, ' Việt'),
        h('label', { class: 'pv-check' }, cs, ' Phân biệt hoa/thường')),
      h('p', { class: 'field-help', text: 'Dùng khi một tên riêng bị viết lệch ở nhiều dòng. Thay xong hoàn tác được bằng Ctrl+Z.' }))
  ];
  setTimeout(() => f.focus(), 50);
  const id = await dialog({ title: 'Tìm và thay thế', body, buttons: [{ id: 'all', label: 'Thay tất cả', primary: true }, { id: 'cancel', label: 'Huỷ' }] });
  if (id !== 'all') return;
  const needle = f.value;
  if (!needle) { toast('Chưa nhập chữ cần tìm.', 'warn'); return; }
  const rep = r.value;
  const esc = needle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const re = new RegExp(esc, cs.checked ? 'g' : 'gi');
  let hits = 0;
  const list = snapshotCues(editor.cues).map((c) => {
    for (const [on, k] of [[cz.checked, 'zh'], [cp.checked, 'py'], [cv.checked, 'vi']]) {
      if (!on) continue;
      const n = (c[k].match(re) || []).length;
      if (n) { hits += n; c[k] = c[k].replace(re, rep); }
    }
    return c;
  });
  if (!hits) { toast('Không thấy “' + needle + '” ở cột đã chọn.', 'info'); return; }
  replaceAllCues(list, 'thay thế “' + needle + '”');
  if (CB.i >= 0) selectCue(Math.min(CB.i, editor.cues.length - 1), true);
  toast('Đã thay ' + hits + ' chỗ.', 'ok', 4000);
}

async function openShortcuts() {
  const rows = [
    ['Ctrl + Enter', 'Đánh dấu ✓ đã soát, sang dòng sau (tự phát)'],
    ['Ctrl + Shift + Enter', 'Chen một dòng mới ngay sau, lấy mốc giờ ở vị trí video'],
    ['Ctrl + ↑ / ↓', 'Dòng trước / dòng sau'],
    ['Ctrl + Space', 'Nghe / xem đúng đoạn của dòng đang chọn'],
    ['Shift + Space', 'Phát / dừng video liên tục'],
    ['← / →', 'Tua lùi / tới 2 giây (khi không gõ)'],
    ['Ctrl + [ / ]', 'Mốc bắt đầu / kết thúc = vị trí video'],
    ['Ctrl + L', 'Bật / tắt lặp dòng'],
    ['Ctrl + H', 'Tìm và thay thế'],
    ['Ctrl + Z / Ctrl + Shift + Z', 'Hoàn tác / làm lại'],
    ['Ctrl + S', 'Lưu'],
    ['Tab', 'Sang ô kế trong ô sửa'],
    ['↑ / ↓ (trong bảng)', 'Đổi dòng đang chọn'],
    ['?', 'Bảng này']
  ];
  const table = h('table', { class: 'keys-table' }, ...rows.map(([k, v]) => h('tr', {}, h('td', {}, h('kbd', { text: k })), h('td', { text: v }))));
  await dialog({ title: 'Phím tắt bàn làm việc', body: [table] });
}

function wireCuebox() {
  if (!$('#cuebox')) return;
  for (const k of ['zh', 'py', 'vi']) {
    const ta = cbEl(k);
    ta.addEventListener('input', cbLiveCheck);
    ta.addEventListener('blur', cbCommit);
    if (k === 'zh') {
      // Nút "Tách tại con trỏ" cần biết con trỏ đứng đâu, mà lúc bấm nút thì ô
      // này đã mất focus rồi. Ghi lại mỗi lần con trỏ đổi chỗ.
      const remember = () => { CB.zhCaret = ta.selectionStart; };
      ['keyup', 'click', 'select', 'input', 'blur'].forEach((e) => ta.addEventListener(e, remember));
    }
    ta.addEventListener('keydown', (ev) => {
      if (ev.key !== 'Escape') return;
      // `syncCuebox()` cố ý bỏ qua ô đang có con trỏ (để không giật chữ khi người
      // dùng đang gõ), nên trước đây Esc không trả lại được gì: ô giữ nguyên chữ
      // mới, rồi blur gọi `cbCommit()` và GHI nó vào. Phải tự trả ô này về.
      ev.preventDefault();
      const c = editor.cues[CB.i];
      if (c) { CB.filling = true; ta.value = c[k]; CB.filling = false; }
      syncCuebox();
      ta.blur();
    });
  }
  for (const k of ['start', 'end']) {
    const inp = cbEl(k);
    inp.addEventListener('input', cbLiveCheck);
    inp.addEventListener('change', cbCommit);
    inp.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') { ev.preventDefault(); cbCommit(); return; }
      if (ev.key !== 'Escape') return;
      ev.preventDefault();
      const c = editor.cues[CB.i];
      if (c) { CB.filling = true; inp.value = clockOf(k === 'start' ? c.start : c.end); CB.filling = false; }
      inp.blur();
    });
  }
  cbEl('autoplay').addEventListener('change', () => { CB.autoplay = cbEl('autoplay').checked; store.set('cbAutoplay', CB.autoplay); });
  cbEl('loop').addEventListener('change', () => { CB.loop = cbEl('loop').checked; });
  CB.autoplay = store.get('cbAutoplay', true) !== false;
  cbEl('autoplay').checked = CB.autoplay;
  cbEl('insert').addEventListener('click', insertCueAfter);
  cbEl('play').addEventListener('click', () => { cbCommit(); if (CB.i >= 0) playCue(CB.i); });
  cbEl('prev').addEventListener('click', () => stepCue(-1));
  cbEl('next').addEventListener('click', () => stepCue(1));
  cbEl('done').addEventListener('click', doneAndNext);
  cbEl('split').addEventListener('click', splitCueAtCursor);
  cbEl('merge').addEventListener('click', mergeWithNext);
  cbEl('delete').addEventListener('click', deleteCue);
  cbEl('regen').addEventListener('click', cbRegenPinyin);
  cbEl('retranslate').addEventListener('click', cbRetranslate);
  $('#btn-edit-findreplace').addEventListener('click', openFindReplace);
  $('#btn-edit-keys').addEventListener('click', openShortcuts);
  $('#btn-edit-retranslate-all').addEventListener('click', retranslateAll);
  wireWave();
  loadCuebox(-1);
}


/** Chặng dịch đã dịch được bao nhiêu dòng — hiện băng báo nếu còn dòng nguyên chữ Hán.
 *  Trước đây con số này chỉ nằm trong báo cáo: người dùng mở trình sửa thấy cột
 *  tiếng Việt toàn chữ Hán mà không biết vì sao (máy thiếu deep-translator, 336/336). */
async function loadTranslationStatus() {
  const note = $('#edit-translate-note');
  if (!note) return;
  show(note, false);
  if (!editor.jobId) return;
  let st = null;
  try { st = await api('/api/jobs/' + encodeURIComponent(editor.jobId) + '/translation'); } catch (_) { return; }
  if (!st || !st.available || !(st.kept_original > 0)) return;
  const all = st.kept_original >= st.total;
  $('#edit-translate-title').textContent = all
    ? 'Chưa có dòng nào được dịch sang tiếng Việt (' + st.total + ' dòng còn nguyên chữ Hán).'
    : st.kept_original + '/' + st.total + ' dòng còn nguyên chữ Hán, chưa dịch được.';
  $('#edit-translate-why').textContent = (st.reason || st.warning || '') +
    ' Bấm “Dịch lại toàn bộ tiếng Việt” để dịch lại (chỉ chạy bước dịch, không gỡ băng lại). ' +
    'Có mã API Gemini trong Cài đặt thì bản dịch có ngữ cảnh, tốt hơn nhiều so với bản miễn phí.';
  show(note, true);
}

async function retranslateAll() {
  if (!editor.jobId) return;
  await rerunJob(editor.jobId, {
    fromStep: 8, videoId: editor.videoId, title: editor.title, btn: $('#btn-edit-retranslate-all'),
    dialogTitle: 'Dịch lại toàn bộ tiếng Việt?',
    dialogBody: 'Máy chỉ chạy lại bước dịch (và xuất file) cho cả phim, không gỡ băng lại. Dùng mã API Gemini nếu đã có trong Cài đặt, ' +
      'không thì dùng bản miễn phí của Google. Mất khoảng vài phút.',
    startToast: 'Đang dịch lại toàn bộ. Bản cũ được cất lại thành file .truoc-, không mất gì.'
  });
}

/* ---- 12b.7 sửa và hoàn tác ---- */

function markDirty() {
  editor.dirty = true;
  editor.rev++;
  setDraftStatus(editor.draftOffer
    ? 'Có thay đổi chưa lưu · tự lưu nháp đang tạm dừng cho tới khi bạn chọn ở thanh màu vàng.'
    : 'Có thay đổi chưa lưu.', '');
}

function setDraftStatus(text, kind) {
  const node = $('#edit-draft-status');
  if (!node) return;
  node.className = 'editbar-status' + (kind ? ' ' + kind : '');
  node.textContent = text || '';
}

function updateUndoButtons() {
  $('#btn-edit-undo').disabled = !editor.undo.length;
  $('#btn-edit-redo').disabled = !editor.redo.length;
}

/** Mọi thay đổi nội dung đều đi qua đây — nhờ vậy Ctrl+Z hoàn tác được nhiều
 *  bước, và bản nháp gửi lên máy chủ luôn đúng bằng thứ đang hiện trên màn hình. */
function pushEdit(i, patch) {
  const cue = editor.cues[i];
  if (!cue) return;
  const prev = {};
  let changed = false;
  for (const k in patch) {
    prev[k] = cue[k];
    if (cue[k] !== patch[k]) changed = true;
  }
  if (!changed) return;
  Object.assign(cue, patch);
  editor.undo.push({ i, patch: Object.assign({}, patch), prev });
  if (editor.undo.length > 400) editor.undo.shift();
  editor.redo.length = 0;
  markDirty();
  refreshRowAround(i);
  refreshOverlay();
  if (i === CB.i) syncCuebox();
  drawWave();
  updateStats();
  updateUndoButtons();
}

function undoEdit() {
  commitActiveCell();
  const entry = editor.undo.pop();
  if (!entry) return;
  editor.redo.push(entry);
  markDirty();
  if (entry.kind === 'doc') {
    applyWholeTable(entry.before);
    toast('Đã hoàn tác: bảng quay về đúng lúc trước khi ' + entry.label + '.', 'info', 6000);
  } else {
    Object.assign(editor.cues[entry.i], entry.prev);
    refreshRowAround(entry.i);
    selectCue(entry.i, true);
    updateStats();
  }
  updateUndoButtons();
}

function redoEdit() {
  commitActiveCell();
  const entry = editor.redo.pop();
  if (!entry) return;
  editor.undo.push(entry);
  markDirty();
  if (entry.kind === 'doc') {
    applyWholeTable(entry.after);
    toast('Đã làm lại: ' + entry.label + '.', 'info', 5000);
  } else {
    Object.assign(editor.cues[entry.i], entry.patch);
    refreshRowAround(entry.i);
    selectCue(entry.i, true);
    updateStats();
  }
  updateUndoButtons();
}

/** Bản chụp độc lập của cả bảng. Phải chép từng cue: pushEdit() sửa thẳng vào
 *  object cue, dùng chung object thì bản chụp sẽ "trôi" theo mọi lần gõ sau. */
function snapshotCues(list) {
  return (list || []).map((c) => Object.assign({}, c));
}

/** Đổ một bản chụp vào bảng (dùng khi hoàn tác/làm lại một bước "cả bảng"). */
function applyWholeTable(list) {
  const keep = editor.selected;
  editor.cues = snapshotCues(list);
  editor.selected = -1;
  renderCues();
  if (editor.cues.length) selectCue(Math.max(0, Math.min(keep, editor.cues.length - 1)), true);
}

/**
 * Thay CẢ BẢNG bằng `nextCues` và ghi đúng một bước hoàn tác cho việc đó.
 *
 * Vì sao phải có: khôi phục bản nháp (hay hoàn nguyên) trước đây xoá sạch ngăn
 * hoàn tác rồi mới đổ bảng mới vào — ai lỡ gõ thêm vài dòng trước khi bấm là
 * mất hẳn, không có đường lui (audit đợt 3, ca G). Nay bảng hiện tại được cất
 * nguyên vào ngăn hoàn tác: một lần Ctrl+Z là quay về đúng thứ vừa gõ.
 */
function replaceAllCues(nextCues, label, keepAt) {
  const before = snapshotCues(editor.cues);
  editor.cues = snapshotCues(nextCues);
  editor.undo.push({ kind: 'doc', label: label || 'thay cả bảng', before, after: snapshotCues(editor.cues) });
  if (editor.undo.length > 400) editor.undo.shift();
  editor.redo.length = 0;
  editor.selected = -1;
  markDirty();
  updateUndoButtons();
  renderCues();
  // Số dòng vừa đổi, nên danh sách dấu ✓ trên máy chủ (ghi theo SỐ DÒNG) đã trỏ
  // sai hàng. Ghi lại ngay theo bảng mới; nếu không, mở lại file ngày mai sẽ thấy
  // dấu đã soát nằm lệch và người dùng bỏ qua những dòng chưa ai đọc.
  scheduleReviewSave();
  const at = Number.isInteger(keepAt) ? Math.max(0, Math.min(keepAt, editor.cues.length - 1)) : 0;
  if (editor.cues.length) selectCue(at, false);
}

/** Số dòng khác nhau giữa hai bảng (so chữ và mốc thời gian). */
function countDiffRows(a, b) {
  a = a || []; b = b || [];
  let n = 0;
  const len = Math.max(a.length, b.length);
  for (let i = 0; i < len; i++) {
    const x = a[i], y = b[i];
    if (!x || !y) { n++; continue; }
    if (x.zh !== y.zh || x.py !== y.py || x.vi !== y.vi ||
        clockOf(x.start) !== clockOf(y.start) || clockOf(x.end) !== clockOf(y.end)) n++;
  }
  return n;
}

function commitActiveCell() {
  const node = document.activeElement;
  if (node && node.classList && node.classList.contains('cue-cell')) { node.blur(); return; }
  // Năm ô của khung sửa dòng KHÔNG phải `.cue-cell`. Thiếu nhánh này thì Ctrl+S
  // ghi file thiếu đúng dòng đang gõ rồi báo "Đã lưu", và nhịp tự lưu nháp 5 giây
  // cũng bỏ sót nó — trái hẳn lời hứa "đóng nhầm tab cũng không mất công".
  // Cố ý KHÔNG blur: nhịp tự lưu chạy ngầm, cướp con trỏ giữa lúc gõ còn tệ hơn.
  if (node && node.closest && node.closest('#cuebox')) cbCommit();
}

/** Ghi lại nội dung một ô sau khi người dùng bấm Enter hoặc rời ô. */
function commitCell(cell) {
  const row = cell.closest('.cuerow');
  if (!row) return;
  const i = Number(row.dataset.i);
  const field = cell.dataset.field;
  const before = cell.dataset.orig === undefined ? '' : cell.dataset.orig;
  const after = cellText(cell);
  delete cell.dataset.orig;

  if (cell.dataset.cancel === '1') {
    delete cell.dataset.cancel;
    setCellText(cell, before);
    refreshRow(i);
    return;
  }
  if (after === before) { refreshRow(i); return; }

  if (field === 'time') {
    const parts = after.split('\n').map((s) => s.trim()).filter(Boolean);
    const a = parseClock(parts[0]);
    const b = parseClock(parts[1]);
    if (a === null || b === null) {
      setCellText(cell, before);
      refreshRow(i);
      toast('Mốc thời gian phải viết theo dạng 00:01:22,100 — mỗi mốc một dòng. Đã giữ nguyên mốc cũ.', 'warn', 7000);
      return;
    }
    pushEdit(i, { start: a, end: b });
    return;
  }

  const value = after.replace(/\n+/g, ' ');
  if (field === 'zh') {
    pushEdit(i, { zh: value, zhTouched: true });
    return;
  }
  const patch = {};
  patch[field] = value;
  pushEdit(i, patch);
}

/** Gõ tới đâu kiểm tới đó: lệch số cụm phải thấy ngay, không đợi bấm Lưu. */
function liveCheck(cell) {
  const row = cell.closest('.cuerow');
  if (!row) return;
  const i = Number(row.dataset.i);
  const c = editor.cues[i];
  if (!c) return;
  const field = cell.dataset.field;
  cell.classList.toggle('is-empty', !cellText(cell).trim());
  if (field !== 'zh' && field !== 'py') return;

  const zh = field === 'zh' ? cellText(cell).replace(/\n+/g, ' ') : c.zh;
  const py = field === 'py' ? cellText(cell).replace(/\n+/g, ' ') : c.py;
  const zhCount = countClusters(zh);
  const pyCount = countClusters(py);
  const bad = !!(zh.trim() && py.trim()) && zhCount !== pyCount;

  row.querySelector('.cue-py').classList.toggle('is-bad', bad);
  const note = row.querySelector('.cue-note');
  let msg = note.querySelector('.cue-note-msg');
  if (bad) {
    if (!msg) {
      msg = h('span', { class: 'cue-note-msg' });
      note.insertBefore(msg, note.firstChild);
    }
    note.classList.add('bad');
    msg.textContent = 'Dòng Hán có ' + zhCount + ' cụm, dòng pinyin có ' + pyCount + ' cụm.';
  } else if (msg) {
    msg.remove();
  }
}

/* ---- 12b.8 sinh lại pinyin cho một câu ---- */

/** Chữ có phân biệt hoa/thường đầu tiên của một dòng, bỏ qua marker `- ` và dấu câu.
 *  So `toLowerCase() !== toUpperCase()` chứ không dùng bảng chữ cái: dòng pinyin
 *  có ā í ǚ… nằm ngoài A–Z, mà chúng vẫn là chữ có hoa/thường. */
function firstCasedLetter(text) {
  for (const ch of String(text || '')) {
    if (ch.toLowerCase() !== ch.toUpperCase()) return ch;
  }
  return '';
}

async function retokenizeCue(i, btn) {
  const c = editor.cues[i];
  if (!c || !c.zh.trim()) return;
  const restore = busyButton(btn, 'Đang sinh…');
  try {
    // Chữ hoa đầu dòng pinyin do cue ĐỨNG TRƯỚC quyết định (core/casing.py), mà
    // máy chủ chỉ nhìn thấy đúng một câu nên mặc định của nó là viết hoa. Giữ lại
    // quyết định đang có của chính dòng này — sửa chữ Hán không đổi dấu câu của
    // cue trước, nên không có lý do gì để đổi chữ hoa đầu dòng. Không gửi cờ này
    // thì mỗi lần bấm nút là một cue giữa câu bị viết hoa lây, mà validator
    // không có luật nào bắt được lỗi ấy.
    const letter = firstCasedLetter(c.py);
    // `video_id` để bảng tên riêng (names.json) được dùng lại: thiếu nó thì
    // 张伟 bị cắt thành 张 | 伟 — đúng cái lỗi người dùng vừa bấm nút để sửa.
    const res = await api('/api/retokenize', {
      method: 'POST',
      body: {
        zh: c.zh,
        index: c.index,
        video_id: editor.videoId || '',
        capitalize: letter ? letter === letter.toUpperCase() : true
      }
    });
    let py = res.py_line || res.pinyin || res.py || '';
    if (!py && Array.isArray(res.tokens)) {
      py = res.tokens.map((t) => String(t.pinyin || t.py || '')).filter(Boolean).join(' ');
    }
    if (!py) throw new ApiError('Chương trình không trả về dòng pinyin cho câu này.');
    const patch = { py: String(py), zhTouched: false };
    if (res.zh_line) patch.zh = String(res.zh_line);
    pushEdit(i, patch);
    toast('Đã sinh lại pinyin cho dòng ' + c.index + '.', 'ok');
  } catch (err) {
    await reportError(err, 'Không sinh lại được pinyin');
    // Chỉ trả nút về như cũ khi hỏng: chạy được thì cả hàng đã được vẽ lại,
    // nút cũ không còn nằm trong trang nữa.
    restore();
  }
}

/* ---- 12b.9 xem lỗi của một dòng ---- */

function showCueFindings(i) {
  const c = editor.cues[i];
  const finds = findingsOf(i);
  const body = [h('p', { class: 'field-help', text: 'Dòng ' + c.index + ' — ' + clockOf(c.start) + ' → ' + clockOf(c.end) })];
  if (!finds.length) body.push(h('p', { text: 'Dòng này không còn lỗi nào.' }));
  finds.forEach((f) => {
    const sev = f.severity || 'error';
    body.push(h('div', { class: 'dcheck ' + (sev === 'error' ? 'fail' : sev === 'warn' ? 'warn' : 'ok') },
      icon(sev === 'error' ? 'err' : sev === 'warn' ? 'warn' : 'info'),
      h('div', {},
        h('div', { class: 'dcheck-label', text: ruleLabel(f) }),
        f.message ? h('div', { class: 'dcheck-detail', text: f.message }) : null
      ),
      h('span')
    ));
  });
  if (c.flag && c.flagNote) {
    body.push(h('div', { class: 'dcheck warn' }, icon('flag'),
      h('div', {}, h('div', { class: 'dcheck-label', text: 'AI đề nghị soát lại dòng này' }),
        h('div', { class: 'dcheck-detail', text: c.flagNote })), h('span')));
  }
  dialog({ title: 'Chi tiết dòng ' + c.index, body, buttons: [{ id: 'ok', label: 'Đóng', primary: true }] });
}

/* ---- 12b.10 nạp, lưu, hoàn nguyên ---- */

/** Gói dữ liệu gửi lên máy chủ.
 *  Ghi cả `zh_line` lẫn `zh`: bộ đọc bên máy chủ (`_edit_row`) dùng tên có hậu
 *  tố `_line`, gửi thiếu là mọi dòng thành rỗng và cả lần lưu bị từ chối. */
function docPayload() {
  return editor.cues.map((c) => ({
    index: c.index,
    start: c.start,
    end: c.end,
    start_text: clockOf(c.start),
    end_text: clockOf(c.end),
    zh_line: c.zh,
    py_line: c.py,
    vi_line: c.vi,
    zh: c.zh,
    py: c.py,
    vi: c.vi
  }));
}

/** Đưa dữ liệu từ máy chủ vào bảng. Chấp nhận cả gói lồng {doc:{cues:[]}}. */
function applyDoc(data, meta) {
  data = data || {};
  const doc = (data.doc && typeof data.doc === 'object') ? data.doc
            : (data.document && typeof data.document === 'object') ? data.document
            : data;
  let list = doc.cues || doc.blocks || doc.items || (Array.isArray(doc) ? doc : []);
  if (!Array.isArray(list)) list = [];

  const jobInfo = unwrapJob(data) || {};
  editor.jobId = (meta && meta.jobId) || doc.job_id || data.job_id || jobInfo.id || editor.jobId || '';
  editor.title = (meta && meta.title) || doc.title || data.title || jobInfo.title || editor.title || 'Phụ đề';
  editor.videoId = String(doc.video_id || data.video_id || '');
  editor.source = (meta && meta.source) || doc.srt_path || data.path || '';
  editor.viPath = doc.vi_path || '';
  editor.canRevert = doc.can_revert !== false;
  editor.audioUrl = String(doc.audio_url || '');
  editor.hasAudio = doc.has_audio === undefined ? !!editor.jobId : !!doc.has_audio;
  // "content" = mở bằng nội dung file (chọn file / kéo thả): không có đường dẫn
  // gốc để ghi đè, nên Lưu phải tải bản đã sửa về máy (hợp đồng H6).
  editor.origin = String(data.origin || doc.origin || (meta && meta.origin) || rememberedOrigin(editor.jobId) || '');
  editor.cues = list.map(normEditCue);
  editor.baseline = editor.cues.map((c) => Object.assign({}, c));
  // Vân tay của đúng thứ vừa đọc từ đĩa. Mọi so sánh với bản nháp dựa vào đây.
  editor.baseHash = cueFingerprint(editor.cues);
  editor.findings = doc.findings || data.findings || [];
  // Kết quả đối chiếu "file định có bao nhiêu đoạn" với "tool đọc được bao nhiêu".
  // Thiếu đoạn nghĩa là một mốc thời gian viết sai đã nuốt mất đoạn sau nó.
  editor.fileFindings = doc.file_findings || [];
  editor.integrity = doc.integrity || null;
  editor.undo.length = 0;
  editor.redo.length = 0;
  editor.selected = -1;
  editor.dirty = false;
  editor.rev = 0;
  editor.draftRev = 0;
  editor.draftOk = true;
  editor.loaded = true;
  indexFindings();
  renderIntegrityNote();

  $('#edit-title').textContent = 'Sửa phụ đề — ' + editor.title;
  const bits = [plural(editor.cues.length, 'dòng')];
  if (!editor.viPath) bits.push('chưa có bản tiếng Việt');
  if (editor.origin === 'content') bits.push('mở từ file bạn chọn — bấm “Lưu và tải về” để lấy bản đã sửa');
  // Chỉ tên file. Đường dẫn tuyệt đối là dòng chữ dài nhất màn hình và không nói
  // thêm được gì; bản đầy đủ vẫn còn ở tooltip ngay dưới đây và ở băng "Đã lưu vào".
  else if (editor.source) bits.push(baseName(editor.source) || editor.source);
  else if (editor.jobId) bits.push('kết quả vừa tạo trong app');
  $('#edit-sub').textContent = bits.join(' · ');
  $('#edit-sub').title = editor.source || '';
  $('#btn-edit-revert').disabled = false;
  hideSavedNote();
  updateSaveLabel();

  show($('#edit-empty'), false);
  show($('#edit-work'), true);
  hideDraftBar();
  setDraftStatus('', '');
  updateUndoButtons();
  renderCues();
  setAudioSource();
  CB.i = -1;
  loadCuebox(-1);
  loadReviewMarks();
  loadTranslationStatus();
  WAVE.peaks = null;
  loadPeaks();
  if (editor.cues.length) selectCue(0, false);

  const notes = Array.isArray(doc.notes) ? doc.notes.filter(Boolean) : [];
  notes.forEach((note) => toast(String(note), 'warn', 8000));
}

async function openEditorForJob(jobId, title) {
  if (!jobId) return;
  // Chính phụ đề này đang mở sẵn: chỉ chuyển tab, đừng nạp lại đè phần đang sửa.
  if (editor.loaded && editor.jobId === jobId) { switchTab('edit'); return; }
  if (!(await confirmLeaveEditor())) return;
  switchTab('edit');
  setDraftStatus('Đang mở phụ đề…', '');
  try {
    const data = await api('/api/jobs/' + encodeURIComponent(jobId) + '/doc');
    applyDoc(data, { jobId, title: title || (data.title || '') });
    store.set('editJob', { id: jobId, title: title || data.title || '', origin: editor.origin });
    await checkDraft();
  } catch (err) {
    show($('#edit-work'), false);
    show($('#edit-empty'), true);
    setDraftStatus('', '');
    await reportError(err, 'Chưa mở được trình sửa');
  }
}

/* ---- 12b.10a mở file chọn trong trình duyệt ----
   Trình duyệt KHÔNG cho trang web biết đường dẫn của file người dùng chọn, nên
   không gửi được {path}. Gửi thẳng nội dung: {name, content, vi_content}
   (hợp đồng POST /api/open-local). Chọn cùng lúc x.srt và x_vi.srt thì tự ghép
   cặp — đúng thói quen đặt tên `<title>.srt` + `<title>_vi.srt` (build-spec-v2). */

/* Mức máy chủ nhận cho một file .srt (MAX_UPLOAD_BYTES = 20 MB). */
const SRT_MAX_BYTES = 20 * 1024 * 1024;

/** "Phim_vi.srt" -> {base:"Phim", vi:true}; "Phim.srt" -> {base:"Phim", vi:false}. */
function srtNameParts(name) {
  const stem = String(name || '').replace(/\.(srt|txt)$/i, '');
  const m = stem.match(/^(.+?)[_.-]vi$/i);
  return m ? { base: m[1], vi: true } : { base: stem, vi: false };
}

/** Đọc file phụ đề thành chữ. Thử UTF-8 trước; hỏng thì GB18030 — file bên dịch
 *  Trung Quốc gửi sang hay dùng bảng mã đó, đọc nhầm là ra toàn ký tự lạ. */
async function readSubtitleFile(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  if (bytes.length >= 2 && ((bytes[0] === 0xff && bytes[1] === 0xfe) || (bytes[0] === 0xfe && bytes[1] === 0xff))) {
    return new TextDecoder(bytes[0] === 0xff ? 'utf-16le' : 'utf-16be').decode(bytes);
  }
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  } catch (_) {
    try { return new TextDecoder('gb18030').decode(bytes); }
    catch (__) { return new TextDecoder('utf-8').decode(bytes); }
  }
}

/** Ghép x.srt với x_vi.srt (không phân biệt hoa thường). */
function pairSubtitleFiles(files) {
  const zh = [], vi = [];
  files.forEach((f) => (srtNameParts(f.name).vi ? vi : zh).push(f));
  const usedVi = new Set();
  const pairs = zh.map((f) => {
    const base = srtNameParts(f.name).base.toLowerCase();
    const mate = vi.find((v) => !usedVi.has(v) && srtNameParts(v.name).base.toLowerCase() === base) || null;
    if (mate) usedVi.add(mate);
    return { zh: f, vi: mate };
  });
  return { pairs, orphans: vi.filter((v) => !usedVi.has(v)) };
}

/** Nút "Chọn file .srt…": đọc file đã chọn, ghép cặp, rồi mở vào trình sửa. */
async function openPickedSubtitles(list) {
  const files = Array.from(list || []);
  const srt = files.filter((f) => /\.srt$/i.test(f.name));
  const skipped = files.length - srt.length;
  if (skipped) toast(plural(skipped, 'file bị bỏ qua') + ' vì không phải file .srt.', 'warn');
  if (!srt.length) return;

  const big = srt.find((f) => f.size > SRT_MAX_BYTES);
  if (big) {
    await dialog({
      title: 'File phụ đề quá lớn', kind: 'warn',
      body: '“' + big.name + '” nặng ' + humanSize(big.size) + ', vượt mức ' + humanSize(SRT_MAX_BYTES) +
            ' mà chương trình nhận cho một file phụ đề. Có thể bạn chọn nhầm file video?'
    });
    return;
  }

  const { pairs, orphans } = pairSubtitleFiles(srt);
  if (!pairs.length) {
    await dialog({
      title: 'Cần thêm file tiếng Trung', kind: 'warn',
      body: 'Bạn mới chọn bản tiếng Việt (“' + orphans[0].name + '”). Hãy chọn thêm file tiếng Trung đi cùng — ví dụ “' +
            srtNameParts(orphans[0].name).base + '.srt” — trong CÙNG một lần chọn (giữ phím Cmd để chọn nhiều file).'
    });
    return;
  }

  let pair = pairs[0];
  if (pairs.length > 1) {
    const shown = pairs.slice(0, 6);
    const choice = await dialog({
      title: 'Mở phụ đề nào?',
      body: 'Trình sửa mở mỗi lần một phụ đề. Bạn chọn một:' +
            (pairs.length > shown.length ? ' (chỉ hiện ' + shown.length + ' file đầu)' : ''),
      buttons: shown.map((p, i) => ({ id: String(i), label: p.zh.name + (p.vi ? ' + bản tiếng Việt' : ''), primary: i === 0 }))
        .concat([{ id: 'cancel', label: 'Thôi' }])
    });
    if (choice === null || choice === 'cancel') return;
    pair = shown[Number(choice)];
  }
  if (orphans.length) {
    toast('Không tìm thấy file tiếng Trung đi cùng: ' + orphans.map((o) => o.name).join(', ') + '.', 'warn', 8000);
  }

  let payload;
  try {
    payload = { name: pair.zh.name, content: await readSubtitleFile(pair.zh) };
    if (pair.vi) payload.vi_content = await readSubtitleFile(pair.vi);
  } catch (err) {
    await dialog({ title: 'Không đọc được file', kind: 'err', body: 'Trình duyệt không đọc được file bạn chọn. Hãy thử chọn lại.', detail: String(err && err.message || err) });
    return;
  }
  await openLocalDoc(payload, pair.zh.name + (pair.vi ? ' + ' + pair.vi.name : ''));
}

/** Trình sửa đang có phần chưa lưu mà sắp bị thay bằng phụ đề khác: hỏi trước. */
async function confirmLeaveEditor() {
  if (!editor.loaded || !editor.dirty) return true;
  commitActiveCell();
  const choice = await dialog({
    title: 'Phụ đề đang sửa chưa lưu', kind: 'warn',
    body: 'Bạn đang sửa “' + (editor.title || 'phụ đề') + '” mà chưa bấm Lưu. Mở phụ đề khác thì bảng đang hiện sẽ bị thay.',
    buttons: [
      { id: 'back', label: 'Quay lại', primary: true },
      { id: 'save', label: 'Lưu rồi mở' },
      { id: 'drop', label: 'Bỏ phần chưa lưu', danger: true }
    ]
  });
  if (choice === 'save') { await saveDoc(); return !editor.dirty; }
  return choice === 'drop';
}

async function openLocalDoc(payload, label) {
  if (!(await confirmLeaveEditor())) return;
  const status = $('#edit-open-status');
  const status2 = $('#open-local-status');
  const say = (text, kind) => {
    [status, status2].forEach((n) => { if (n) { n.className = 'field-status' + (kind ? ' ' + kind : ''); n.textContent = text; } });
  };
  say('Đang mở file…', '');
  try {
    const res = await api('/api/open-local', { method: 'POST', body: payload });
    const doc = res.doc || res;
    const id = (res.job && res.job.id) || res.id || res.job_id || doc.job_id || '';
    const title = doc.title || res.title || label;
    applyDoc(res, {
      jobId: id, title, source: doc.srt_path || payload.path || '',
      origin: payload.content !== undefined ? 'content' : 'path'
    });
    if (id) store.set('editJob', { id, title, origin: editor.origin });
    say('', '');
    switchTab('edit');
    toast('Đã mở “' + (res.title || label) + '” vào trình sửa.', 'ok');
    await checkDraft();
  } catch (err) {
    say(err instanceof ApiError ? err.message : 'Không mở được file này.', 'err');
    if (!(err instanceof ApiError) || err.status !== 404) await reportError(err, 'Không mở được file');
  }
}

/* ---- 12b.10c "Lưu và tải về" (hợp đồng H6) ----
   File mở bằng nội dung được máy chủ chép vào một thư mục riêng trong work/, và
   Lưu ghi vào ĐÓ — file gốc trên máy người dùng không đổi. Nếu nút vẫn chỉ ghi
   "Lưu", người dùng tưởng đã sửa xong file của họ, đóng app, và mất cả buổi sửa.
   Vì vậy: nhãn nút nói rõ "Lưu và tải về", lưu xong tải ngay .srt (và _vi.srt)
   về máy, và hiện rõ "Đã lưu vào: …" kèm nút "Mở thư mục". */

/** Nhãn nút Lưu theo nguồn gốc bản đang sửa (hàm thuần). */
function saveLabelFor(origin) {
  return origin === 'content' ? 'Lưu và tải về' : 'Lưu';
}

function updateSaveLabel() {
  const label = $('#btn-edit-save-label');
  if (label) label.textContent = saveLabelFor(editor.origin);
  const btn = $('#btn-edit-save');
  if (btn) {
    btn.title = editor.origin === 'content'
      ? 'File bạn chọn nằm trên máy bạn và trình duyệt không cho ghi đè thẳng lên nó. ' +
        'Bấm để lưu vào thư mục của chương trình rồi tải bản đã sửa về máy.'
      : 'Ghi lại file phụ đề (và bản tiếng Việt nếu có)';
  }
}

/** Nguồn gốc đã nhớ của công việc gần nhất — khi mở lại sau khi tải lại trang,
 *  máy chủ có thể không nói lại nguồn gốc ở GET /doc. */
function rememberedOrigin(jobId) {
  const last = store.get('editJob', null);
  return last && jobId && last.id === jobId ? String(last.origin || '') : '';
}

function rememberOrigin() {
  const last = store.get('editJob', null);
  if (last && last.id === editor.jobId) store.set('editJob', Object.assign({}, last, { origin: editor.origin }));
}

/** Danh sách file để tải sau khi lưu, từ `downloads` của PUT /doc (hàm thuần).
 *  vi_srt null/thiếu = không có bản tiếng Việt, chỉ tải một file. */
function savedDownloads(downloads, srtPath, viPath) {
  const d = downloads && typeof downloads === 'object' ? downloads : {};
  const url = (v) => (typeof v === 'string' ? v.trim() : '');
  const out = [];
  const srt = url(d.srt);
  const vi = url(d.vi_srt) || url(d.vi);
  if (srt) out.push({ kind: 'srt', url: srt, name: baseName(srtPath || ''), label: '.srt' });
  if (vi) out.push({ kind: 'vi_srt', url: vi, name: baseName(viPath || ''), label: '_vi.srt' });
  return out;
}

/** Tải lần lượt, cách nhau một chút: nhiều trình duyệt bỏ qua lượt tải thứ hai
 *  nếu hai lượt bắt đầu cùng một lúc. Bị chặn thì còn nút "Tải lại" trên khối. */
function downloadSavedFiles(files) {
  (files || []).forEach((f, i) => setTimeout(() => triggerDownload(f.url, f.name), i * 700));
}

function hideSavedNote() {
  show($('#edit-saved-note'), false);
}

function showSavedNote(savedTo, files) {
  const box = $('#edit-saved-note');
  if (!box) return;
  const list = files || [];
  $('#edit-saved-title').textContent = list.length > 1
    ? 'Đã lưu và tải về máy cả hai file (.srt và _vi.srt).'
    : list.length === 1
      ? 'Đã lưu và tải về máy file .srt.'
      : 'Đã lưu, nhưng chương trình chưa gửi được file để tải về — bấm “Mở thư mục” để lấy bản đã sửa.';
  $('#edit-saved-path').textContent = savedTo || '';
  show($('#edit-saved-where'), !!savedTo);
  const dl = $('#edit-saved-dl');
  clear(dl);
  list.forEach((f) => dl.appendChild(h('button', {
    type: 'button', class: 'btn btn-quiet btn-sm', onclick: () => triggerDownload(f.url, f.name)
  }, icon('download'), 'Tải lại ' + f.label)));
  const reveal = $('#btn-saved-reveal');
  reveal.disabled = !savedTo;
  reveal.onclick = () => revealPath(savedTo, { jobId: editor.jobId, okText: 'Đã mở thư mục chứa bản đã lưu.' });
  show(box, true);
}

async function saveDoc() {
  if (!editor.loaded || editor.saving) return;
  commitActiveCell();
  if (!editor.jobId) {
    await dialog({
      title: 'Chưa lưu được',
      kind: 'warn',
      body: 'Bản phụ đề này chưa gắn với công việc nào trong chương trình nên không biết ghi đè vào đâu. ' +
            'Hãy mở lại file bằng nút “Mở vào trình sửa” ở tab Kiểm tra file.'
    });
    return;
  }
  const it = editor.integrity || {};
  if (Number(it.missing_cues || 0) > 0) {
    // Bảng đang hiện ít đoạn hơn file trên đĩa. Ghi đè bây giờ là xoá hẳn phần
    // chênh lệch đó. Người dùng phải nhìn thấy đúng hai con số trước khi quyết.
    const go = await dialog({
      title: 'Lưu bây giờ sẽ mất ' + plural(Number(it.missing_cues), 'đoạn'),
      kind: 'warn',
      buttons: [
        { id: 'no', label: 'Quay lại', primary: true },
        { id: 'yes', label: 'Vẫn lưu, tôi chấp nhận mất', danger: true }
      ],
      body: [
        h('p', { text: 'File trên đĩa có ' + it.expected_cues + ' đoạn. Tool chỉ đọc được ' +
          it.parsed_cues + ' đoạn vì ' + plural(Number(it.bad_timestamp_count || 0), 'dòng mốc thời gian') +
          ' viết sai định dạng. Bấm Lưu là ghi đè bản ' + it.parsed_cues + ' đoạn lên file gốc.' }),
        h('p', { class: 'field-help', text: 'Nên làm: bấm “Quay lại”, mở file gốc bằng một trình soạn thảo văn bản, ' +
          'sửa những dòng giờ sai rồi mở lại vào đây. Nếu lỡ lưu rồi thì nút “Hoàn nguyên về bản máy tạo” ' +
          'vẫn lấy lại được bản đầy đủ, miễn là chưa lưu lần thứ hai.' })
      ]
    });
    if (go !== 'yes') return;
  }
  // Máy chủ bỏ mọi dòng không có chữ Hán lẫn pinyin khi ghi file (luật của
  // `emit_srt`: hai file phải cùng số đoạn). Từ khi có nút "Thêm dòng", người
  // dùng có thể tạo ra một dòng mới rồi chỉ gõ câu tiếng Việt và đi lưu — dòng
  // đó sẽ biến mất mà không ai nói gì. Đếm trước và nói thẳng.
  const dongHut = editor.cues
    .map((c, k) => ({ so: k + 1, c }))
    .filter(({ c }) => !c.zh.trim() && !c.py.trim());
  if (dongHut.length) {
    const coViet = dongHut.filter(({ c }) => c.vi.trim());
    const go = await dialog({
      title: coViet.length
        ? plural(coViet.length, 'dòng') + ' chỉ có tiếng Việt, chưa có chữ Hán'
        : plural(dongHut.length, 'dòng') + ' còn để trống',
      kind: 'warn',
      buttons: [
        { id: 'no', label: 'Quay lại điền nốt', primary: true },
        { id: 'yes', label: 'Vẫn lưu, bỏ những dòng đó', danger: true }
      ],
      body: [
        h('p', { text: 'Hai file phụ đề phải có cùng số đoạn, nên một dòng không có chữ Hán thì ' +
          'không ghi ra file được. Lưu bây giờ là bỏ hẳn ' +
          (coViet.length ? plural(coViet.length, 'câu tiếng Việt bạn vừa gõ') : plural(dongHut.length, 'dòng trống')) + '.' }),
        h('p', { class: 'field-help', text: 'Dòng: ' + dongHut.map((x) => x.so).slice(0, 12).join(', ') +
          (dongHut.length > 12 ? '…' : '') })
      ]
    });
    if (go !== 'yes') {
      const dau = dongHut[0].so - 1;
      afterUserSelect(dau);
      const ta = cbEl('zh');
      if (ta) { try { ta.focus(); } catch (_) { /* cửa sổ chưa được chọn */ } }
      return;
    }
  }
  if (editor.draftOffer) {
    // Lưu xong thì máy chủ xoá bản nháp. Thanh vàng còn đang hỏi mà lưu luôn là
    // vứt bản nháp cũ khi người dùng chưa kịp quyết — nên phải hỏi.
    const choice = await dialog({
      title: 'Còn bản nháp cũ chưa xử lý',
      kind: 'warn',
      body: [
        h('p', { text: 'Thanh màu vàng phía trên vẫn đang hỏi về phần bạn sửa dở từ lần trước. ' +
          'Lưu bây giờ sẽ ghi bảng đang hiện vào file và BỎ bản nháp cũ đó — không lấy lại được.' }),
        h('p', { class: 'field-help', text: 'Muốn xem bản nháp trước thì bấm “Quay lại” rồi chọn “Khôi phục bản sửa”. ' +
          'Phần bạn đang sửa vẫn lấy lại được bằng Ctrl+Z ngay sau khi khôi phục.' })
      ],
      buttons: [
        { id: 'no', label: 'Quay lại', primary: true },
        { id: 'yes', label: 'Lưu và bỏ bản nháp cũ', danger: true }
      ]
    });
    if (choice !== 'yes' || editor.saving) return;
  }
  editor.saving = true;
  const btn = $('#btn-edit-save');
  btn.disabled = true;
  setDraftStatus('Đang lưu…', '');
  try {
    const res = await api('/api/jobs/' + encodeURIComponent(editor.jobId) + '/doc', {
      method: 'PUT', body: { cues: docPayload(), title: editor.title }
    });
    const doc = res.doc || res;
    const back = Array.isArray(doc.cues) ? doc.cues : null;
    const sum = doc.summary || res.summary || summarizeFindings(doc.findings || []);

    // Chặng lưu đánh số lại từ 1 và bỏ cue rỗng (đúng luật của emit_srt), nên
    // bảng trả về có thể khác bảng đang hiện. Khác thì nạp lại nguyên bảng —
    // để màn hình luôn là thứ đang nằm trên đĩa, không phải thứ vừa gõ.
    if (back && back.length !== editor.cues.length) {
      const keepSelected = editor.selected;
      applyDoc(res, { jobId: editor.jobId, title: editor.title });
      if (keepSelected >= 0) selectCue(Math.min(keepSelected, editor.cues.length - 1), true);
    } else {
      editor.findings = doc.findings || [];
      if (back) back.forEach((raw, i) => { if (editor.cues[i]) editor.cues[i].findings = Array.isArray(raw.findings) ? raw.findings : null; });
      indexFindings();
      editor.baseline = editor.cues.map((c) => Object.assign({}, c));
      // Vân tay phải là của thứ ĐANG NẰM TRÊN ĐĨA, tức bảng máy chủ trả về —
      // không phải bảng trên màn hình: máy chủ cắt khoảng trắng thừa, chuẩn hoá
      // dòng tiếng Việt… Lấy nhầm bảng màn hình thì lần mở sau bản nháp mang
      // vân tay lệch với file, và thanh nháp báo oan "file đã được ghi lại".
      editor.baseHash = cueFingerprint(back ? back.map(normEditCue) : editor.cues);
      editor.cues.forEach((c, i) => { c.zhTouched = false; refreshRow(i); });
      updateStats();
    }
    // Lưu xong thì máy chủ xoá bản nháp — thanh hỏi về nháp cũ phải biến mất
    // theo, không thì lần bấm sau người dùng khôi phục lại đúng bản vừa ghi đè.
    hideDraftBar();
    editor.dirty = false;
    editor.draftRev = editor.rev;
    const savedTo = String(res.saved_to || doc.saved_to || '');
    const origin = String(res.origin || doc.origin || editor.origin || '');
    if (origin !== editor.origin) { editor.origin = origin; updateSaveLabel(); rememberOrigin(); }
    setDraftStatus('Đã lưu lúc ' + new Date().toLocaleTimeString('vi-VN'), 'ok');
    const statusNode = $('#edit-draft-status');
    if (statusNode) statusNode.title = savedTo ? 'Đã lưu vào: ' + savedTo : '';
    toast(doc.message || (sum.error
      ? 'Đã lưu. Còn ' + plural(sum.error, 'chỗ chưa đạt quy chuẩn') + ' — xem cột lỗi bên phải.'
      : 'Đã lưu. File đạt toàn bộ quy chuẩn định dạng.'), sum.error ? 'warn' : 'ok', 6000);
    if (origin === 'content') {
      const files = savedDownloads(res.downloads || doc.downloads,
        doc.srt_path || res.srt_path || '', doc.vi_path || res.vi_path || '');
      showSavedNote(savedTo, files);
      downloadSavedFiles(files);
    }
  } catch (err) {
    setDraftStatus('Chưa lưu được.', 'err');
    await reportError(err, 'Không lưu được bản sửa');
  } finally {
    editor.saving = false;
    btn.disabled = false;
  }
}

/* ---- 12b.10b bản nháp: ĐỌC LẠI, không chỉ ghi ----
   index.html hứa với người dùng "tự lưu nháp mỗi 5 giây, đóng nhầm tab cũng
   không mất công". Lời hứa đó chỉ thành thật khi mở lại trình sửa có đọc bản
   nháp ra. Quy tắc:
     * nháp trùng file trên đĩa -> im lặng, đừng làm phiền;
     * nháp khác -> HỎI, không tự khôi phục. Người dùng mở file ra mà thấy nội
       dung khác thứ họ vừa mở thì sẽ tưởng tool tự đổi bậy;
     * file trên đĩa đã bị ghi lại sau lúc ghi nháp -> nói rõ khôi phục là đè
       lên bản mới hơn. */

function hideDraftBar() {
  editor.draftOffer = null;
  show($('#edit-draft-bar'), false);
  show($('#edit-draft-newer'), false);
}

/** Bóc bảng cue ra khỏi gói nháp, chấp nhận vài cách gói khác nhau. */
function draftCuesOf(doc) {
  if (!doc) return null;
  if (Array.isArray(doc)) return doc;
  if (Array.isArray(doc.cues)) return doc.cues;
  if (doc.doc && typeof doc.doc === 'object') return draftCuesOf(doc.doc);
  if (Array.isArray(doc.blocks)) return doc.blocks;
  return null;
}

// Số thứ tự của lần hỏi nháp mới nhất. Mở phụ đề A rồi mở ngay B khi câu hỏi
// của A chưa về: lần hỏi A kết thúc trước KHÔNG được mở lại tự lưu, vì lần hỏi
// của B vẫn đang chờ (nếu không, nhịp tự lưu có thể đè nháp cũ của B).
let draftCheckSeq = 0;

/** Hỏi máy chủ xem có bản nháp nào chưa lưu không. Mọi trục trặc ở đây đều im
 *  lặng: không có nháp cũng là chuyện bình thường, không phải lỗi để hù người dùng. */
async function checkDraft() {
  hideDraftBar();
  if (!editor.loaded || !editor.jobId) return;
  // Trong lúc chờ câu trả lời, nhịp tự lưu không được ghi nháp đè lên bản cũ.
  const seq = ++draftCheckSeq;
  editor.draftChecking = true;
  try {
    await offerDraft();
  } finally {
    if (seq === draftCheckSeq) editor.draftChecking = false;
  }
}

async function offerDraft() {
  const jobId = editor.jobId;
  let data;
  try {
    data = await api('/api/jobs/' + encodeURIComponent(jobId) + '/draft');
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) editor.draftOk = false;
    return;
  }
  if (editor.jobId !== jobId) return;          // người dùng đã mở phụ đề khác
  if (!data || data.exists === false) return;

  const doc = data.doc !== undefined ? data.doc : data.draft;
  const rawCues = draftCuesOf(doc);
  if (!rawCues || !rawCues.length) return;

  const cues = rawCues.map(normEditCue);
  if (cueFingerprint(cues) === editor.baseHash) return;   // nháp y hệt file: thôi

  // File trên đĩa đổi sau lúc ghi nháp? Vân tay bản gốc đi kèm nháp là bằng
  // chứng chắc nhất; không có thì mới nhìn tới mốc thời gian của file.
  const baseHash = String((doc && doc.base_hash) || (data && data.base_hash) || '');
  let newer = false;
  if (baseHash) {
    newer = baseHash !== editor.baseHash;
  } else {
    const savedAt = Number(data.saved_at || (doc && doc.saved_at) || 0);
    const fileAt = Number(data.srt_mtime || data.file_mtime || data.mtime || 0);
    newer = !!(savedAt && fileAt && fileAt > savedAt + 2);
  }

  const differs = countDiffRows(cues, editor.baseline);
  editor.draftOffer = { cues, savedAt: Number(data.saved_at || (doc && doc.saved_at) || 0), newer };

  const when = whenText(editor.draftOffer.savedAt);
  $('#edit-draft-title').textContent = when
    ? 'Bạn có bản sửa chưa lưu từ ' + when + '.'
    : 'Bạn có một bản sửa chưa lưu.';
  $('#edit-draft-detail').textContent =
    'Bản nháp khác file đang mở ở ' + plural(differs, 'dòng') + '. ' +
    'Bấm “Khôi phục bản sửa” để lấy lại phần đã gõ dở, hoặc “Bỏ bản nháp” nếu bạn không cần nữa. ' +
    'Trong lúc bạn chưa chọn, tool tạm dừng tự lưu nháp để bản nháp cũ không bị ghi đè; file trên đĩa vẫn giữ nguyên.';
  show($('#edit-draft-newer'), newer);
  show($('#edit-draft-bar'), true);
  setDraftStatus(editor.dirty
    ? 'Có thay đổi chưa lưu · tự lưu nháp đang tạm dừng cho tới khi bạn chọn ở thanh màu vàng.'
    : 'Tự lưu nháp đang tạm dừng — hãy chọn ở thanh màu vàng phía trên.', '');
}

/** Khôi phục: chỉ đổ vào bảng đang mở, KHÔNG ghi đĩa. Người dùng vẫn phải bấm
 *  Lưu — nhờ vậy họ còn đường lùi nếu khôi phục nhầm.
 *
 *  Bảng đang có phần vừa gõ (chưa lưu) thì HỎI trước, và dù trả lời thế nào
 *  bảng hiện tại cũng được cất vào ngăn hoàn tác chứ không bị vứt: bấm Ctrl+Z
 *  là lấy lại đúng thứ vừa gõ. */
async function restoreDraft() {
  const offer = editor.draftOffer;
  if (!offer || !offer.cues.length) return;
  commitActiveCell();

  if (editor.dirty) {
    const typed = countDiffRows(editor.cues, editor.baseline);
    const choice = await dialog({
      title: 'Bảng đang có phần bạn vừa sửa',
      kind: 'warn',
      body: [
        h('p', { text: 'Từ lúc mở trình sửa, bạn đã sửa ' + plural(typed || 1, 'dòng') + ' mà chưa lưu. ' +
          'Khôi phục bản nháp cũ sẽ thay cả bảng bằng bản nháp đó.' }),
        h('p', { text: 'Phần vừa sửa KHÔNG mất: nó được cất vào ngăn hoàn tác — ngay sau khi khôi phục, ' +
          'bấm Ctrl+Z (hoặc nút Hoàn tác trên thanh công cụ) là lấy lại đúng bảng hiện tại.' })
      ],
      buttons: [
        { id: 'no', label: 'Quay lại' },
        { id: 'yes', label: 'Vẫn khôi phục bản nháp', primary: true }
      ]
    });
    if (choice !== 'yes') return;
    if (editor.draftOffer !== offer) return;     // thanh đã đổi trong lúc hỏi
  }

  const hadTyped = editor.dirty;
  hideDraftBar();
  replaceAllCues(offer.cues.map((c) => Object.assign({}, c, { zhTouched: false })), 'khôi phục bản nháp');
  setDraftStatus('Đã khôi phục bản nháp — bấm Lưu để ghi vào file.', '');
  toast(hadTyped
    ? 'Đã khôi phục bản nháp. Phần bạn vừa sửa đang nằm trong ngăn hoàn tác: bấm Ctrl+Z để lấy lại. File trên đĩa CHƯA đổi.'
    : 'Đã lấy lại phần bạn sửa dở. File trên đĩa CHƯA đổi: bấm Lưu khi bạn thấy đúng.', 'ok', 10000);
}

/** Bỏ bản nháp: hỏi trước, rồi xoá hẳn trên máy chủ. */
async function dropDraft() {
  if (!editor.draftOffer) return;
  const ok = await confirmBox(
    'Bỏ bản nháp chưa lưu?',
    'Phần bạn gõ dở lần trước sẽ bị xoá hẳn và không lấy lại được. ' +
    (editor.dirty
      ? 'Những gì bạn vừa sửa trong bảng đang hiện thì vẫn giữ nguyên và sẽ được tự lưu nháp tiếp.'
      : 'File phụ đề trên đĩa không bị đụng tới — bảng đang hiện vẫn là file đó.'),
    'Bỏ bản nháp', true
  );
  if (!ok) return;

  const url = '/api/jobs/' + encodeURIComponent(editor.jobId) + '/draft';
  try {
    await api(url, { method: 'PUT', body: { draft: null, cues: null } });
  } catch (_) {
    try { await api(url, { method: 'DELETE' }); }
    catch (err2) {
      hideDraftBar();
      setDraftStatus('Chưa xoá được bản nháp trên máy.', 'err');
      toast('Đã bỏ qua bản nháp ở màn hình này, nhưng chưa xoá được nó trên máy. Lần mở sau có thể tool lại hỏi.', 'warn', 8000);
      return;
    }
  }
  hideDraftBar();
  // KHÔNG đặt draftRev = rev ở đây: nếu người dùng đã gõ trong lúc tự lưu tạm
  // dừng, nhịp tự lưu kế tiếp phải ghi phần đó xuống — nếu không nó chỉ còn nằm
  // trong bộ nhớ trình duyệt, đóng tab là mất.
  setDraftStatus(editor.dirty ? 'Có thay đổi chưa lưu.' : '', '');
  toast(editor.dirty
    ? 'Đã bỏ bản nháp cũ. Phần bạn đang sửa vẫn giữ nguyên — nhớ bấm Lưu khi xong.'
    : 'Đã bỏ bản nháp. Bảng đang hiện là đúng file trên đĩa.', 'ok');
}

async function saveDraft() {
  if (!editor.loaded || !editor.jobId || !editor.draftOk) return;
  // Thanh "Bạn có bản sửa chưa lưu" còn chờ người dùng quyết (hoặc đang hỏi máy
  // chủ có nháp không): TẠM DỪNG. Ghi lúc này là đè bản nháp cũ trên đĩa bằng
  // phần vừa gõ trước khi người dùng kịp chọn — bấm "Khôi phục" sau đó chỉ còn
  // lấy lại được chính thứ vừa gõ, bản nháp cũ thì mất hẳn (audit đợt 3, ca G).
  if (editor.draftOffer || editor.draftChecking) return;
  // Ghi nốt ô đang gõ vào bảng trước khi chụp. Thiếu dòng này thì bản nháp luôn
  // thiếu đúng dòng người dùng đang làm dở — tức là thiếu đúng thứ đáng giữ nhất.
  commitActiveCell();
  if (!editor.dirty || editor.rev === editor.draftRev || editor.saving) return;
  const rev = editor.rev;
  try {
    // `base_hash` = vân tay bản gốc lúc mở trình sửa. Gửi kèm để lần mở sau
    // biết được file trên đĩa có bị ghi lại sau lúc ghi nháp hay không.
    await api('/api/jobs/' + encodeURIComponent(editor.jobId) + '/draft', {
      method: 'PUT', body: { cues: docPayload(), rev, base_hash: editor.baseHash }
    });
    editor.draftRev = rev;
    setDraftStatus('Đã giữ bản nháp lúc ' + new Date().toLocaleTimeString('vi-VN') + ' — nhớ bấm Lưu khi xong.', 'ok');
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      editor.draftOk = false;
      setDraftStatus('Bản chương trình này chưa tự giữ nháp được — nhớ bấm Lưu.', 'err');
    } else {
      setDraftStatus('Chưa giữ được bản nháp, lát nữa sẽ thử lại.', 'err');
    }
  }
}

async function revertDoc() {
  const ok = await confirmBox(
    'Hoàn nguyên về bản máy tạo?',
    'Toàn bộ phần bạn đã sửa trong lần mở này sẽ mất, bảng quay về đúng bản máy vừa tạo ra. ' +
    'Sau khi hoàn nguyên vẫn phải bấm Lưu thì file trên đĩa mới đổi theo.' +
    (editor.draftOffer ? ' Bản nháp cũ đang chờ ở thanh màu vàng cũng sẽ bị bỏ qua.' : ''),
    'Hoàn nguyên', true
  );
  if (!ok) return;

  // Máy chủ giữ một bản chụp của bản máy tạo và ghi đè thẳng lên file, nên
  // đường này là thật nhất. Nó không làm được (bản cũ không còn, hoặc bản
  // chương trình chưa có chức năng) thì lùi về bản chụp lúc mở trình sửa.
  if (editor.jobId) {
    try {
      const data = await api('/api/jobs/' + encodeURIComponent(editor.jobId) + '/doc', {
        method: 'PUT', body: { revert: true }
      });
      const doc = data.doc || data;
      if (Array.isArray(doc.cues) && doc.cues.length) {
        applyDoc(data, { jobId: editor.jobId, title: editor.title });
        toast('Đã hoàn nguyên về bản máy tạo và ghi lại file.', 'ok', 6000);
        return;
      }
    } catch (err) {
      if (err instanceof ApiError && err.status && err.status !== 404 && err.status !== 409) {
        await reportError(err, 'Không hoàn nguyên được');
        return;
      }
    }
  }
  hideDraftBar();
  replaceAllCues(editor.baseline.map((c) => Object.assign({}, c, { zhTouched: false })), 'hoàn nguyên');
  setDraftStatus('Đã hoàn nguyên. Bấm Lưu để ghi đè file.', '');
  toast('Đã quay về bản lúc mới mở trình sửa. Bấm Lưu nếu muốn ghi đè lên file, hoặc Ctrl+Z để lấy lại phần vừa sửa.', 'info', 8000);
}

/* ---- 12b.11 nối dây ---- */

function isEditTabOpen() {
  const panel = $('#tab-edit');
  return !!panel && panel.classList.contains('is-active');
}

function wireEditTab() {
  const rows = $('#cue-rows');

  rows.addEventListener('click', (ev) => {
    const row = ev.target.closest ? ev.target.closest('.cuerow') : null;
    if (!row) return;
    const i = Number(row.dataset.i);
    if (ev.target.closest('.cue-play')) { playCue(i); return; }
    if (ev.target.closest('.cue-warnbtn')) { showCueFindings(i); return; }
    const retok = ev.target.closest('[data-act="retok"]');
    if (retok) { retokenizeCue(i, retok); return; }
    selectCue(i, false);
  });

  rows.addEventListener('focusin', (ev) => {
    const cell = ev.target.closest ? ev.target.closest('.cue-cell') : null;
    if (!cell) return;
    cell.dataset.orig = cellText(cell);
    cell.classList.remove('is-empty');
    const row = cell.closest('.cuerow');
    if (row) selectCue(Number(row.dataset.i), false);
  });

  rows.addEventListener('focusout', (ev) => {
    const cell = ev.target.closest ? ev.target.closest('.cue-cell') : null;
    if (cell && cell.dataset.orig !== undefined) commitCell(cell);
  });

  rows.addEventListener('input', (ev) => {
    const cell = ev.target.closest ? ev.target.closest('.cue-cell') : null;
    if (cell) liveCheck(cell);
  });

  // Dán chữ: chỉ lấy phần văn bản. Dán từ Word vào contenteditable mà không
  // chặn thì kéo theo cả thẻ HTML, file .srt sẽ có rác không nhìn thấy được.
  rows.addEventListener('paste', (ev) => {
    const cell = ev.target.closest ? ev.target.closest('.cue-cell') : null;
    if (!cell) return;
    ev.preventDefault();
    const text = ((ev.clipboardData || window.clipboardData).getData('text') || '')
      .replace(/\r\n?/g, '\n');
    const clean = cell.dataset.field === 'time' ? text : text.replace(/\n+/g, ' ');
    document.execCommand('insertText', false, clean);
  });

  rows.addEventListener('keydown', (ev) => {
    const cell = ev.target.closest ? ev.target.closest('.cue-cell') : null;
    if (!cell) return;
    if (ev.key === 'Escape') {
      ev.preventDefault();
      ev.stopPropagation();
      cell.dataset.cancel = '1';
      cell.blur();
      return;
    }
    if (ev.key === 'Enter' && !ev.shiftKey && cell.dataset.field !== 'time') {
      ev.preventDefault();
      cell.blur();
    }
  });

  $('#edit-filters').addEventListener('click', (ev) => {
    const chip = ev.target.closest('.chip');
    if (!chip) return;
    editor.filter = chip.dataset.filter;
    $$('#edit-filters .chip').forEach((c) => {
      const on = c === chip;
      c.classList.toggle('is-on', on);
      c.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    applyFilter();
  });

  let searchTimer = 0;
  $('#edit-search').addEventListener('input', (ev) => {
    const value = String(ev.target.value || '').trim().toLowerCase();
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { editor.query = value; applyFilter(); }, 160);
  });

  $('#btn-edit-save').addEventListener('click', saveDoc);
  $('#btn-edit-revert').addEventListener('click', revertDoc);
  $('#btn-edit-undo').addEventListener('click', undoEdit);
  $('#btn-edit-redo').addEventListener('click', redoEdit);
  $('#btn-draft-restore').addEventListener('click', restoreDraft);
  $('#btn-draft-drop').addEventListener('click', dropDraft);
  $('#btn-saved-hide').addEventListener('click', hideSavedNote);

  $('#btn-edit-last').addEventListener('click', () => {
    const last = store.get('editJob', null);
    if (last && last.id) openEditorForJob(last.id, last.title);
  });
  $('#btn-edit-open-path').addEventListener('click', () => {
    const path = $('#edit-open-path').value.trim();
    if (!path) { $('#edit-open-status').className = 'field-status err'; $('#edit-open-status').textContent = 'Chưa nhập đường dẫn nào.'; return; }
    openLocalDoc({ path }, baseName(path));
  });
  $('#edit-open-path').addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') { ev.preventDefault(); $('#btn-edit-open-path').click(); }
  });
  $('#btn-edit-pick').addEventListener('click', () => $('#edit-open-files').click());
  $('#edit-open-files').addEventListener('change', (ev) => {
    const list = Array.from(ev.target.files || []);
    ev.target.value = '';
    openPickedSubtitles(list);
  });

  for (const m of [$('#edit-audio'), $('#edit-video')]) {
    m.addEventListener('timeupdate', onMediaTime);
    m.addEventListener('seeked', onMediaTime);
    m.addEventListener('play', updatePlayButton);
    m.addEventListener('pause', updatePlayButton);
    m.addEventListener('ended', () => { stopPlay(); });
    m.addEventListener('loadedmetadata', () => {
      if (m !== mediaEl()) return;
      editor.audioOk = true;
      show($('#edit-audio-note'), false);
      updateTimeLabel();
    });
    m.addEventListener('error', () => {
      if (!editor.jobId || m !== mediaEl()) return;
      editor.audioOk = false;
      setPlaying(-1);
      show($('#edit-audio-note'), true);
      $('#edit-audio-why').textContent =
        'Không lấy được video/bản ghi âm của công việc này. Thường là do thư mục làm việc đã bị dọn. ' +
        'Bạn vẫn sửa và lưu chữ bình thường được.';
    });
  }

  // Khung xem trước
  const pv = $('#edit-preview');
  $('#pv-play').addEventListener('click', togglePlay);
  $('#pv-prev').addEventListener('click', () => jumpCue(-1));
  $('#pv-next').addEventListener('click', () => jumpCue(1));
  $('#pv-seek').addEventListener('input', () => {
    const m = mediaEl();
    if (isFinite(m.duration) && m.duration > 0) seekTo(Number($('#pv-seek').value) / 1000 * m.duration);
  });
  $('#pv-rate').addEventListener('change', () => { mediaEl().playbackRate = Number($('#pv-rate').value) || 1; });
  $('#pv-size').addEventListener('click', () => {
    const small = pv.classList.toggle('is-small');
    $('#pv-size').textContent = small ? 'Phóng to' : 'Thu nhỏ';
    store.set('pvSmall', small);
    // Nút này đổi TỈ LỆ CỘT chứ không bóp khung video, nên canvas dạng sóng vừa
    // đổi bề rộng. drawWave() chỉ tự chạy theo timeupdate: lúc video đang dừng,
    // không gọi ở đây thì sóng méo cho tới lần tua sau.
    drawWave();
  });
  if (store.get('pvSmall', false)) { pv.classList.add('is-small'); $('#pv-size').textContent = 'Phóng to'; }
  for (const k of ['zh', 'py', 'vi']) {
    const cb = $('#pv-show-' + k);
    const saved = store.get('pvShow_' + k, true);
    cb.checked = saved !== false;
    pv.classList.toggle('hide-' + k, !cb.checked);
    cb.addEventListener('change', () => { pv.classList.toggle('hide-' + k, !cb.checked); store.set('pvShow_' + k, cb.checked); });
  }
  $('#pv-follow').addEventListener('change', () => { editor.follow = $('#pv-follow').checked; });
  $('#pv-set-start').addEventListener('click', () => setTimingFromVideo('start'));
  $('#pv-set-end').addEventListener('click', () => setTimingFromVideo('end'));
  $('#pv-nudge-back').addEventListener('click', () => nudgeTiming(-0.1));
  $('#pv-nudge-fwd').addEventListener('click', () => nudgeTiming(0.1));
  $('#btn-fetch-preview').addEventListener('click', fetchPreviewVideo);
  // Bấm vào ô thời gian của một dòng thì tua video tới dòng đó; bấm vào chữ thì chỉ chọn.
  $('#cue-rows').addEventListener('click', (ev) => {
    const row = ev.target.closest('.cuerow');
    if (!row) return;
    if (ev.target.closest('.cue-play') || ev.target.closest('.cue-warn')) return;
    const i = Number(row.dataset.i);
    if (!editor.cues[i]) return;
    // Bấm vào ô đang sửa tại chỗ thì để yên; bấm phần còn lại của hàng = chọn + tự phát.
    const cell = ev.target.closest('[contenteditable]');
    if (cell && cell === document.activeElement) return;
    afterUserSelect(i);
  });
  $('#btn-edit-export').addEventListener('click', () => toggleExportBox());
  wireCuebox();
  $('#btn-export-close').addEventListener('click', () => toggleExportBox(false));

  // Bàn phím: chỉ bắt khi tab này đang mở, để không cướp phím của tab khác.
  document.addEventListener('keydown', (ev) => {
    if (!isEditTabOpen() || !editor.loaded) return;
    if ($('#modal-root').firstChild) return;         // đang mở hộp thoại
    const mod = ev.ctrlKey || ev.metaKey;
    const key = String(ev.key || '').toLowerCase();
    const code = String(ev.code || '');
    const target = ev.target;
    const typing = !!(target && (target.isContentEditable ||
      target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT'));

    // Con trỏ đang trong một ô nhập: Ctrl+Z là "hoàn tác chữ vừa gõ" theo thói
    // quen của mọi phần mềm, không phải "hoàn tác thao tác trong bảng". Cướp nó
    // rồi chạy undoEdit() sẽ xoá trắng chữ đang gõ mà không có đường lấy lại —
    // undoEdit() chỉ biết các bản vá đã ghi, chữ chưa ghi thì không ai giữ.
    if (typing && mod && (key === 'z' || key === 'y')) return;

    if (mod && key === 'z') { ev.preventDefault(); if (ev.shiftKey) redoEdit(); else undoEdit(); return; }
    if (mod && key === 'y') { ev.preventDefault(); redoEdit(); return; }
    // Ghi nốt ô đang gõ TRƯỚC khi lưu / sang dòng khác, nếu không thì lần sửa
    // cuối cùng rơi mất trong im lặng.
    if (mod && key === 's') { ev.preventDefault(); commitActiveCell(); saveDoc(); return; }
    // Ctrl+Shift+Enter chen một dòng mới; Ctrl+Enter là "xong, dòng sau". Hai
    // việc trái nhau nên phải kiểm cái có Shift TRƯỚC.
    if (mod && ev.shiftKey && key === 'enter') { ev.preventDefault(); insertCueAfter(); return; }
    if (mod && key === 'enter') { ev.preventDefault(); commitActiveCell(); doneAndNext(); return; }
    if (mod && (key === 'arrowdown' || code === 'ArrowDown')) { ev.preventDefault(); commitActiveCell(); stepCue(1); return; }
    if (mod && (key === 'arrowup' || code === 'ArrowUp')) { ev.preventDefault(); commitActiveCell(); stepCue(-1); return; }
    if (mod && (key === ' ' || code === 'Space')) { ev.preventDefault(); if (editor.selected >= 0) playCue(editor.selected); return; }
    if (mod && key === '[') { ev.preventDefault(); setTimingFromVideo('start'); return; }
    if (mod && key === ']') { ev.preventDefault(); setTimingFromVideo('end'); return; }
    if (mod && key === 'l') { ev.preventDefault(); $('#cb-loop').checked = !$('#cb-loop').checked; CB.loop = $('#cb-loop').checked; return; }
    if (mod && key === 'h') { ev.preventDefault(); openFindReplace(); return; }

    if (typing) return;

    if ((key === ' ' || key === 'spacebar' || code === 'Space') && ev.shiftKey) {
      ev.preventDefault(); togglePlay(); return;
    }
    if (key === ' ' || key === 'spacebar' || code === 'Space') {
      ev.preventDefault();
      if (editor.selected >= 0) playCue(editor.selected);
      else if (editor.cues.length) { selectCue(0, true); playCue(0); }
      return;
    }
    if (key === 'arrowleft' || code === 'ArrowLeft') { ev.preventDefault(); seekTo(mediaEl().currentTime - 2); return; }
    if (key === 'arrowright' || code === 'ArrowRight') { ev.preventDefault(); seekTo(mediaEl().currentTime + 2); return; }
    if (key === 'arrowdown' || code === 'ArrowDown') { ev.preventDefault(); moveSelection(1); afterUserSelect(); return; }
    if (key === 'arrowup' || code === 'ArrowUp') { ev.preventDefault(); moveSelection(-1); afterUserSelect(); return; }
    if (key === '?' || (ev.shiftKey && key === '/')) { ev.preventDefault(); openShortcuts(); return; }
    if (key === 'escape' || code === 'Escape') { stopPlay(); }
  });

  // Tự lưu nháp. Một nhịp duy nhất cho cả trang; không có gì đổi thì không gửi.
  setInterval(saveDraft, DRAFT_INTERVAL_MS);

  refreshEditEntry();
}

/** Nút "Mở lại kết quả gần nhất" chỉ hiện khi thật sự có việc để mở lại. */
function refreshEditEntry() {
  const btn = $('#btn-edit-last');
  if (!btn) return;
  const last = store.get('editJob', null);
  const has = !!(last && last.id);
  show(btn, has && !editor.loaded);
  if (has) {
    btn.lastChild.textContent = last.title ? ' Mở lại “' + last.title + '”' : ' Mở lại kết quả gần nhất';
  }
}

/* --------------------------------------------------------------------------
   13. Nối dây
   -------------------------------------------------------------------------- */

function wireCreateTab() {
  const zone = $('#dropzone');
  wireDropzone(zone, (files) => chooseFile(files[0]), {
    onText: (text) => { $('#source-url').value = text; readSourceInput(); }
  });
  zone.addEventListener('click', (ev) => { if (ev.target === zone) $('#source-url').focus(); });

  $('#source-url').addEventListener('input', readSourceInput);
  $('#source-url').addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' && state.source) { ev.preventDefault(); startJob(); }
  });
  $('#btn-pick-file').addEventListener('click', () => $('#file-input').click());
  $('#file-input').addEventListener('change', (ev) => chooseFile(ev.target.files[0]));
  $('#btn-clear-source').addEventListener('click', () => { $('#source-url').value = ''; $('#file-input').value = ''; setSource(null); });
  $('#btn-start').addEventListener('click', startJob);
  $('#btn-cancel').addEventListener('click', cancelJob);
  $('#btn-again').addEventListener('click', () => {
    state.job = null;
    setSource(null);
    $('#source-url').value = '';
    $('#file-input').value = '';
    setConnPill(null, '');
    showCreateScreen('input');
  });

  $$('#profile-seg .seg-item').forEach((btn) => btn.addEventListener('click', () => {
    $$('#profile-seg .seg-item').forEach((b) => {
      const on = b === btn;
      b.classList.toggle('is-on', on);
      b.setAttribute('aria-checked', on ? 'true' : 'false');
    });
    store.set('profile', btn.dataset.profile);
  }));

  $('#btn-copy-log').addEventListener('click', async () => {
    const text = (state.job && state.job.log.join('\n')) || '';
    try { await navigator.clipboard.writeText(text); toast('Đã chép nhật ký.', 'ok'); }
    catch (_) { await dialog({ title: 'Nhật ký', body: [h('pre', { class: 'log log-sm', text })] }); }
  });
}

function wireCheckTab() {
  const zone = $('#check-drop');
  wireDropzone(zone, (files) => addCheckFiles(files));
  $('#btn-pick-srt').addEventListener('click', () => $('#srt-input').click());
  $('#srt-input').addEventListener('change', (ev) => { addCheckFiles(Array.from(ev.target.files || [])); ev.target.value = ''; });
  $('#btn-check-all').addEventListener('click', checkAllFiles);
  $('#btn-fix-all').addEventListener('click', fixAllFiles);
  $('#btn-clear-files').addEventListener('click', () => { state.files = []; renderCheckFiles(); });

  $('#btn-open-local').addEventListener('click', () => {
    const path = $('#open-local-path').value.trim();
    if (!path) {
      $('#open-local-status').className = 'field-status err';
      $('#open-local-status').textContent = 'Chưa nhập đường dẫn file .srt nào.';
      return;
    }
    openLocalDoc({ path }, baseName(path));
  });
  $('#open-local-path').addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') { ev.preventDefault(); $('#btn-open-local').click(); }
  });
  $('#btn-open-local-pick').addEventListener('click', () => $('#open-local-files').click());
  $('#open-local-files').addEventListener('change', (ev) => {
    const list = Array.from(ev.target.files || []);
    ev.target.value = '';
    openPickedSubtitles(list);
  });
}

function wireNamesTab() {
  $('#names-video').addEventListener('change', onNamesVideoChange);
  $('#btn-names-reload').addEventListener('click', reloadNames);
  $('#btn-names-new').addEventListener('click', newNamesTable);
  $('#btn-names-add').addEventListener('click', addNameRow);
  $('#btn-names-save').addEventListener('click', saveNames);
  $('#btn-names-rerun').addEventListener('click', (ev) => {
    const j = state.names.rerun;
    if (!j) return;
    rerunJob(String(j.id || j.job_id || ''), {
      btn: ev.currentTarget, videoId: state.names.videoId, title: String(j.title || '')
    });
  });
}

/** Mục lục tab Cài đặt: sáng đúng nhóm đang xem.
 *
 *  Dùng IntersectionObserver chứ KHÔNG nghe sự kiện cuộn của cửa sổ: nghe cuộn
 *  là chạy mã ở mọi khung hình, và trên máy 2017 thì thấy được. Bộ quan sát chỉ
 *  báo khi một nhóm thật sự đi qua vạch, và nếu trình duyệt không có nó thì mục
 *  lục vẫn bấm được như một danh sách liên kết bình thường.
 */
function wireSettingsToc() {
  // Tab Cài đặt và tab Hướng dẫn cùng dùng khung mục lục này. Gắn RIÊNG cho
  // từng khung: gắn chung thì bấm một mục bên Hướng dẫn sẽ tắt đèn mọi mục bên
  // Cài đặt (vì không mục nào bên đó trùng địa chỉ), quay lại thấy mục lục trống.
  document.querySelectorAll('.settings-layout').forEach(wireOneToc);
}

function wireOneToc(root) {
  const links = Array.from(root.querySelectorAll('.toc-link'));
  if (!links.length) return;
  const sections = links
    .map((a) => document.querySelector(a.getAttribute('href') || ''))
    .filter(Boolean);
  if (!sections.length) return;

  const sang = (id) => links.forEach((a) => {
    a.classList.toggle('is-current', a.getAttribute('href') === '#' + id);
  });

  // Bấm vào mục lục thì sáng ngay, không chờ bộ quan sát. Đây cũng là đường duy
  // nhất còn chạy trên engine không có IntersectionObserver.
  links.forEach((a) => a.addEventListener('click', () => {
    sang(String(a.getAttribute('href') || '').slice(1));
  }));
  if (!window.IntersectionObserver) return;

  const thay = new Map();
  const watcher = new IntersectionObserver((entries) => {
    entries.forEach((e) => thay.set(e.target.id, e.isIntersecting ? e.intersectionRatio : 0));
    // Nhóm đầu tiên còn nhìn thấy được tính là nhóm đang xem — đọc từ trên xuống
    // nên đó là nhóm người dùng đang ở.
    const dang = sections.find((s) => (thay.get(s.id) || 0) > 0);
    if (dang) sang(dang.id);
  }, { rootMargin: '-88px 0px -55% 0px', threshold: [0, 0.01] });
  sections.forEach((s) => watcher.observe(s));
}

function wireSettingsTab() {
  wireSettingsToc();
  $('#btn-save-settings').addEventListener('click', saveSettings);
  $('#btn-test-key').addEventListener('click', testApiKey);
  $('#btn-doctor').addEventListener('click', runDoctor);
  $('#set-translator').addEventListener('change', updateTranslatorHelp);
  $('#set-apikey').addEventListener('input', updateTranslatorHelp);

  // Năm việc trong danh sách trắng.
  $('#btn-install-ffmpeg').addEventListener('click', (ev) => runAction('install_ffmpeg', ev.currentTarget));
  $('#btn-update-ytdlp').addEventListener('click', (ev) => runAction('update_ytdlp', ev.currentTarget));
  $('#btn-download-model').addEventListener('click', (ev) => runAction('download_model', ev.currentTarget, { model: currentModelId() }));
  $('#btn-open-output').addEventListener('click', (ev) => runAction('open_output_dir', ev.currentTarget));
  // "Mở hướng dẫn sử dụng" là thẻ <a href="/guide" target="_blank"> trong
  // index.html: trình duyệt tự mở trang hướng dẫn ở thẻ mới (hợp đồng H7).

  // Nút "Mở" cạnh ô thư mục: có đường dẫn cụ thể thì mở đúng chỗ đó, để trống
  // thì nhờ máy chủ mở thư mục kết quả đang dùng thật.
  $('#btn-open-outdir').addEventListener('click', (ev) => {
    const typed = $('#set-outdir').value.trim() || String(pick(state.settings, 'paths.out_dir', '') || '');
    if (typed) revealPath(typed);
    else runAction('open_output_dir', ev.currentTarget);
  });
  $('#set-theme').addEventListener('change', (ev) => applyTheme(ev.target.value));

  $('#btn-toggle-key').addEventListener('click', (ev) => {
    const input = $('#set-apikey');
    const showing = input.type === 'text';
    input.type = showing ? 'password' : 'text';
    clear(ev.currentTarget);
    ev.currentTarget.appendChild(icon(showing ? 'eye' : 'eye-off'));
  });
}

function wireGlobal() {
  $$('.tab').forEach((btn) => btn.addEventListener('click', () => switchTab(btn.dataset.tab)));

  // Thả file ra ngoài ô nhận: đừng để trình duyệt mở file đè lên giao diện.
  ['dragover', 'drop'].forEach((name) => window.addEventListener(name, (ev) => {
    if (!ev.target.closest || !ev.target.closest('.dropzone')) ev.preventDefault();
  }));

  // Dán link ở bất cứ đâu trên tab đầu -> coi như dán vào ô nguồn.
  document.addEventListener('paste', (ev) => {
    const panel = $('#tab-create');
    if (!panel.classList.contains('is-active')) return;
    if ($('#create-input').hidden) return;
    const target = ev.target;
    if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) return;
    const text = (ev.clipboardData || window.clipboardData).getData('text');
    if (text && (looksLikeUrl(text) || looksLikePath(text))) {
      $('#source-url').value = text.trim();
      readSourceInput();
      toast('Đã nhận đường dẫn. Bấm "Bắt đầu" khi bạn sẵn sàng.', 'info');
    }
  });

  window.addEventListener('beforeunload', (ev) => {
    // Job chạy ở máy chủ nên đóng tab không mất gì; phần sửa tay thì có.
    if ((state.job && state.job.status === 'running') || state.uploading ||
        (editor.loaded && editor.dirty) || state.names.dirty) {
      ev.preventDefault();
      ev.returnValue = '';
    }
  });
}

function boot() {
  applyTheme(store.get('theme', 'auto'));
  const themeSel = $('#set-theme');
  if (themeSel) themeSel.value = store.get('theme', 'auto');

  wireGlobal();
  wireCreateTab();
  wireEditTab();
  wireCheckTab();
  wireNamesTab();
  wireSettingsTab();

  renderModelList($('#model-list'), 'model-a');
  renderModelList($('#model-list-settings'), 'model-b');
  setSource(null);
  showCreateScreen('input');

  const tab = store.get('tab', 'create');
  switchTab(['create', 'edit', 'check', 'names', 'settings', 'guide'].indexOf(tab) >= 0 ? tab : 'create');

  loadSettings();
  loadHealth();
  restoreJob();
}

/** Hỏi máy chủ vài con số giao diện cần biết trước (giới hạn dung lượng file
 *  tải lên). Máy chủ không trả thì dùng mặc định trong hợp đồng — im lặng, vì
 *  đây không phải thứ người dùng cần được báo. */
function loadHealth() {
  if (!state.healthPromise) {
    state.healthPromise = api('/api/health').then((data) => {
      const limit = Number((data && data.max_media_bytes) || pick(data, 'limits.max_media_bytes', 0));
      if (limit > 0) state.maxMediaBytes = limit;
      return data;
    }).catch(() => {
      // Lần sau hỏi lại (máy chủ có thể chỉ chậm khởi động); lần này dùng mặc định.
      state.healthPromise = null;
      return null;
    });
  }
  return state.healthPromise;
}

/** Chờ /api/health tối đa `ms` mili-giây. Không bao giờ ném lỗi: máy chủ chậm
 *  thì dùng mức đang biết, còn máy chủ vẫn chặn lại ở phía nó. */
function healthReady(ms) {
  return Promise.race([
    loadHealth(),
    new Promise((resolve) => setTimeout(resolve, ms || 3000))
  ]).catch(() => null);
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
else boot();
