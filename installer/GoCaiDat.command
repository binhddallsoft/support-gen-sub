#!/bin/bash
# ===========================================================================
# GoCaiDat.command — gỡ SrtGen khỏi máy
#
# SrtGen ghi vào đúng ba chỗ, và file này dọn cả ba:
#
#     ~/Library/Application Support/SrtGen   gần như mọi thứ
#     ~/Library/Caches/SrtGen                thứ nhớ tạm: câu trả lời AI, bản
#                                            dịch, lối tắt tới mô hình nghe
#     ~/Desktop/SrtGen.app                   biểu tượng trên màn hình nền
#
# Danh sách những gì nằm trong đó được rà từ chính mã nguồn: mọi chỗ gọi
# user_data_dir() / user_cache_dir() trong srtgen/, cộng mọi thứ CaiDat.command
# và KhoiDong.command tạo ra. App thêm một chỗ ghi mới thì phải thêm vào đây.
# Lỡ quên thì cũng không được nói dối: cuối file có bước KIỂM LẠI, liệt kê
# những gì còn sót, thay vì tuyên bố "đã gỡ sạch" mà không nhìn.
#
# MẶC ĐỊNH GIỮ LẠI toàn bộ file .srt đã tạo — muốn xoá phải trả lời thêm một
# câu hỏi nữa, và phải gõ tay chữ xác nhận.
#
# BẢNG TÊN RIÊNG (work/<phim>/names.json) là thứ người dùng tự gõ, máy không
# tạo lại được. Nó nằm lẫn trong thư mục làm việc — nơi mọi thứ khác đều xoá
# được — nên người dùng rất dễ tưởng nó cũng là rác. Vì vậy TRƯỚC mọi lệnh xoá,
# file này chép từng bảng tên sang thư mục kết quả (thứ được giữ), kiểm lại
# từng byte, và chép hỏng thì KHÔNG xoá thư mục làm việc.
#
# Thư mục kết quả KHÔNG được đoán. Người dùng đổi được nó trong tab "Cài đặt"
# của app, và chỗ mới có thể nằm ngay bên trong một thư mục mà file này sắp xoá.
# Đoán sai ở đây nghĩa là xoá mất công sức nhiều ngày của họ, trong khi màn hình
# vẫn báo "giữ nguyên các file .srt của bạn". Vì vậy phải đọc cấu hình thật.
# ===========================================================================

set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"

APP_SUPPORT="$HOME/Library/Application Support/SrtGen"
CACHE_DIR="$HOME/Library/Caches/SrtGen"
DESKTOP_APP="$HOME/Desktop/SrtGen.app"

# Do bộ cài (CaiDat.command, KhoiDong.command) tạo ra.
BIN_DIR="$APP_SUPPORT/bin"          # uv, ffmpeg — app cũng tìm ffmpeg ở đây
VENV_DIR="$APP_SUPPORT/venv"
PY_DIR="$APP_SUPPORT/python"
UV_CACHE="$APP_SUPPORT/uv-cache"
MODEL_DIR="$APP_SUPPORT/models"
LOG_DIR="$APP_SUPPORT/logs"
MOC_DIR="$APP_SUPPORT/.tien-do"
RECEIPT="$APP_SUPPORT/cai-dat.json"
URL_FILE="$APP_SUPPORT/dang-chay.url"

# Do app ghi — rà từ mọi chỗ gọi user_data_dir() trong srtgen/.
SETTINGS_JSON="$APP_SUPPORT/settings.json"   # web/server.py: tab Cài đặt
SETTINGS_TMP="$SETTINGS_JSON.tmp"            # bản ghi dở của atomic_write_text
JOBS_DIR="$APP_SUPPORT/jobs"                 # web/jobs.py: lịch sử các lần chạy
WORK_MAC_DINH="$APP_SUPPORT/work"            # work_root(): thư mục làm việc
OUT_MAC_DINH="$APP_SUPPORT/output"           # thư mục kết quả mặc định

# Do app ghi — rà từ mọi chỗ gọi user_cache_dir() trong srtgen/.
CACHE_MODEL_DIR="$CACHE_DIR/models"          # s2_asr: bộ cài để lối tắt về MODEL_DIR
CACHE_AI="$CACHE_DIR/ai"                     # s7_ai: câu trả lời AI đã nhớ
CACHE_DICH="$CACHE_DIR/translate"            # s8_translate: bản dịch đã nhớ

# Nơi cất bảng tên riêng, nằm TRONG thư mục kết quả để được giữ cùng file .srt.
TEN_THU_MUC_BANG="bang-ten-rieng"
# Lấy một lần lúc bắt đầu, để mọi thứ của lần gỡ này mang cùng một dấu thời gian.
THOI_DIEM="$(date '+%Y%m%d-%H%M%S')"

# Hai giá trị mặc định. Chúng được ĐỌC LẠI TỪ CẤU HÌNH ngay bên dưới, sau khi
# các hàm phụ trợ đã được khai báo; ở đây chỉ đặt sẵn để không có biến rỗng nếu
# việc đọc cấu hình hỏng.
WORK_DIR="$WORK_MAC_DINH"
OUT_DIR="$OUT_MAC_DINH"
# Thư mục tạm mà người dùng tự chọn ra ngoài thư mục của SrtGen: chỉ liệt kê,
# không xoá. Xoá một thư mục nằm ngoài vùng của mình là việc không được phép.
WORK_NGOAI=0
# Câu trả lời cho câu hỏi "xoá cả file .srt?". Đặt sẵn là GIỮ ngay từ đầu: bảng
# liệt kê dùng nó trước khi câu hỏi được đặt ra, và set -u không cho biến rỗng.
XOA_KET_QUA=0

CO_MAU=0
[ -t 1 ] && CO_MAU=1
if [ "$CO_MAU" = "1" ]; then
    DAM=$'\033[1m'; XANH=$'\033[32m'; DO=$'\033[31m'; VANG=$'\033[33m'; XAM=$'\033[90m'; HET=$'\033[0m'
else
    DAM=""; XANH=""; DO=""; VANG=""; XAM=""; HET=""
fi

VACH="------------------------------------------------------------------"

ok()   { printf '   %s✓%s  %s\n' "$XANH" "$HET" "$1"; }
nhac() { printf '   %s•%s  %s\n' "$VANG" "$HET" "$1"; }
tin()  { printf '      %s\n' "$1"; }

giu_cua_so() {
    if [ -t 0 ]; then
        printf '\n%s' "Nhấn phím bất kỳ để đóng cửa sổ này… "
        read -r -n 1 -s _phim || true
        printf '\n'
    fi
}
trap giu_cua_so EXIT

# --------------------------------------------------------------------------- #
# Đọc thư mục kết quả và thư mục tạm THẬT SỰ đang dùng
#
# Thứ tự ưu tiên phải giống hệt thứ tự mà app dùng khi ghi file, nếu không thì
# câu "giữ nguyên các file .srt của bạn" là một lời nói dối:
#
#   1. settings.json  — ô "Thư mục lưu kết quả" trong tab Cài đặt
#   2. paths.out_dir  — trong srtgen/config/default.yaml
#   3. thư mục mặc định của app (~/Library/Application Support/SrtGen/output)
#
# Đọc bằng chính Python của SrtGen để dùng lại đúng bộ luật của app. Không có
# Python nào chạy được (venv đã hỏng, máy trắng) thì lui về đọc thô settings.json
# bằng sed — thà đọc kém chính xác còn hơn mặc định đoán bừa.
# --------------------------------------------------------------------------- #
PY_DOC_CAU_HINH=$(cat <<'PYTHON'
import json, sys
from pathlib import Path

app = Path(sys.argv[1])
proj = Path(sys.argv[2])

def sach(gia_tri):
    return str(gia_tri or "").strip()

out = ""
work = ""

# settings.json thắng, vì đó là lựa chọn người dùng bấm Lưu trong tab Cài đặt.
try:
    data = json.loads((app / "settings.json").read_text(encoding="utf-8-sig"))
    if isinstance(data, dict):
        out = sach(data.get("out_dir"))
except Exception:
    pass

goc = app
try:
    sys.path.insert(0, str(proj))
    from srtgen.core.context import load_config, user_data_dir

    paths = load_config().get("paths") or {}
    out = out or sach(paths.get("out_dir"))
    work = sach(paths.get("work_dir"))
    # `user_data_dir()` lui về ~/.srtgen khi thiếu platformdirs. Chỉ tin nó khi
    # thư mục đó có thật, nếu không thì giữ thư mục app mà file .command biết
    # chắc — thà lấy chỗ đúng của bộ cài còn hơn một chỗ chưa từng tồn tại.
    thu = Path(user_data_dir())
    if thu.is_dir() or not app.is_dir():
        goc = thu
except Exception:
    pass

print(str(Path(out).expanduser()) if out else str(goc / "output"))
print(str(Path(work).expanduser()) if work else str(goc / "work"))
PYTHON
)

# --------------------------------------------------------------------------- #
# Đọc tên phim từ work/<id>/S0_info.json để đặt tên cho bảng tên riêng đã cất.
#
# Người dùng nhận ra phim qua tên phim, không qua video_id kiểu "a1B2c3D4e5F".
# Việc lọc ký tự làm theo đúng luật `io_utils.safe_stem` của app (NFC, thay ký
# tự Windows cấm và ký tự điều khiển bằng dấu cách, gộp khoảng trắng, bỏ dấu
# chấm/cách ở hai đầu). Cắt theo SỐ BYTE chứ không theo số chữ: ổ đĩa của macOS
# giới hạn tên file theo byte, và một chữ tiếng Việt có dấu chiếm tới 3 byte.
# Ghi thẳng byte UTF-8 ra để không phụ thuộc bảng mã của cửa sổ dòng lệnh.
# --------------------------------------------------------------------------- #
PY_DOC_TEN=$(cat <<'PYTHON'
import json, sys, unicodedata
from pathlib import Path

try:
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
    ten = data.get("title") if isinstance(data, dict) else ""
except Exception:
    ten = ""
ten = unicodedata.normalize("NFC", str(ten or ""))
cam = set('<>:"/\\|?*')
ten = "".join(" " if (c in cam or unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp")) else c for c in ten)
ten = " ".join(ten.split()).strip(" .")
while len(ten.encode("utf-8")) > 150:
    ten = ten[:-1].rstrip(" .")
sys.stdout.buffer.write(ten.encode("utf-8"))
PYTHON
)

doc_cau_hinh() {
    local py ket dong1 dong2
    for py in "$VENV_DIR/bin/python" python3 python; do
        case "$py" in
            /*) [ -x "$py" ] || continue ;;
            *)  command -v "$py" >/dev/null 2>&1 || continue ;;
        esac
        # </dev/null: Python không được đụng tới bàn phím, vì câu trả lời XOA
        # người dùng gõ sau này cũng đi qua đúng đường đó. Bỏ \r: một Python
        # dựng cho Windows (lúc thử file này) in xuống dòng kiểu CRLF, và một \r
        # dính cuối đường dẫn là đủ để mọi phép so sánh đường dẫn sai.
        ket="$("$py" -c "$PY_DOC_CAU_HINH" "$APP_SUPPORT" "$PROJECT_DIR" 2>/dev/null </dev/null | tr -d '\r')" || continue
        dong1="$(printf '%s\n' "$ket" | sed -n '1p')"
        dong2="$(printf '%s\n' "$ket" | sed -n '2p')"
        [ -n "$dong1" ] && OUT_DIR="$dong1"
        [ -n "$dong2" ] && WORK_DIR="$dong2"
        [ -n "$dong1" ] && return 0
    done

    # Đường lui: bóc chuỗi out_dir ra khỏi settings.json bằng sed. Chỉ đúng với
    # đường dẫn không có dấu nháy kép trong tên — đủ cho mọi trường hợp thật.
    local tho
    if [ -f "$SETTINGS_JSON" ]; then
        tho="$(sed -n 's/.*"out_dir"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$SETTINGS_JSON" | head -n 1)"
        tho="$(printf '%s' "$tho" | sed 's|\\/|/|g')"
        case "$tho" in
            "~")   tho="$HOME" ;;
            "~/"*) tho="$HOME/${tho#\~/}" ;;
        esac
        [ -n "$tho" ] && OUT_DIR="$tho"
    fi
    return 1
}

# Bỏ dấu / thừa ở cuối để hai đường dẫn so sánh được với nhau.
gon_duong_dan() {
    local p="$1"
    while [ "${#p}" -gt 1 ] && [ "${p%/}" != "$p" ]; do
        p="${p%/}"
    done
    printf '%s' "$p"
}

# "$1" có nằm bên trong (hoặc chính là) "$2" không?
nam_trong() {
    local con cha
    con="$(gon_duong_dan "$1")"
    cha="$(gon_duong_dan "$2")"
    [ -n "$con" ] && [ -n "$cha" ] || return 1
    [ "$con" = "$cha" ] && return 0
    case "$con" in "$cha"/*) return 0 ;; esac
    return 1
}

# Dung lượng của một thư mục, dạng người đọc được. Không có thì trả về rỗng.
co_nang() {
    [ -e "$1" ] || return 1
    du -sh "$1" 2>/dev/null | awk '{print $1}'
}

# In một dòng trong bảng liệt kê. Trả về 0 nếu thứ đó thật sự tồn tại.
liet_ke() {
    local duong_dan="$1" mo_ta="$2" nang
    nang="$(co_nang "$duong_dan" || true)"
    if [ -z "$nang" ]; then
        printf '   %s(không có)%s  %s\n' "$XAM" "$HET" "$mo_ta"
        return 1
    fi
    printf '   %s%8s%s  %s\n' "$DAM" "$nang" "$HET" "$mo_ta"
    printf '             %s%s%s\n' "$XAM" "$duong_dan" "$HET"
    return 0
}

# Như liet_ke, nhưng im lặng khi không có: dành cho những file sổ sách nhỏ mà
# một dòng "(không có)" chỉ làm bảng rối thêm.
liet_ke_neu_co() {
    [ -e "$1" ] || [ -L "$1" ] || return 1
    liet_ke "$1" "$2"
}

# In từng file khớp mẫu "$2" ở tầng work/<phim>/<file> của thư mục làm việc "$1".
#
# Bỏ qua thứ nằm trong thư mục kết quả CHỈ KHI thư mục kết quả chắc chắn còn
# lại sau khi xoá "$1": nó nằm bên trong (hoặc chính là) "$1" — xoa() chừa nó
# ra — và người dùng không chọn xoá kết quả. Còn khi thư mục kết quả là CHA của
# "$1" (người dùng chọn chính thư mục SrtGen, hay thư mục nhà, làm chỗ lưu kết
# quả) thì "$1" vẫn bị xoá trọn; bỏ qua ở đây là để bảng tên mất theo trong im
# lặng trong khi màn hình báo "bảng tên riêng đã cất ở trên".
tep_trong_thu_muc() {
    local tep ket_qua_con=0
    [ -d "$1" ] || return 0
    if [ "$XOA_KET_QUA" != "1" ] && nam_trong "$OUT_DIR" "$1"; then
        ket_qua_con=1
    fi
    while IFS= read -r tep; do
        [ -n "$tep" ] || continue
        [ "$ket_qua_con" = "1" ] && nam_trong "$tep" "$OUT_DIR" && continue
        printf '%s\n' "$tep"
    done <<EOF
$(find "$1" -mindepth 2 -maxdepth 2 -type f -name "$2" 2>/dev/null | LC_ALL=C sort)
EOF
    return 0
}

# Đếm file khớp mẫu "$1" trong mọi thư mục làm việc sắp bị xoá.
dem_trong_work() {
    local w so tong=0
    for w in "$WORK_XOA1" "$WORK_XOA2"; do
        [ -n "$w" ] || continue
        so="$(tep_trong_thu_muc "$w" "$1" | grep -c .)"
        tong=$((tong + ${so:-0}))
    done
    printf '%s' "$tong"
}

# Lọc một chuỗi thành tên file dùng được, bằng công cụ sẵn có của máy.
#
# Là lớp chặn thứ hai sau Python (và là lớp duy nhất khi venv đã hỏng): thay
# ký tự cấm bằng dấu cách, bỏ ký tự điều khiển, gộp dấu cách, bỏ dấu chấm/cách
# ở hai đầu — tên bắt đầu bằng dấu chấm sẽ bị Finder giấu đi. Chạy tr với
# LC_ALL=C để nó làm việc trên từng byte: mọi byte bị đụng tới đều là ASCII,
# nên chữ tiếng Việt và chữ Hán (toàn byte >= 0x80) đi qua nguyên vẹn.
loc_ten_file() {
    printf '%s' "$1" \
        | LC_ALL=C tr -d '\000-\037\177' \
        | LC_ALL=C tr '<>:"/\\|?*' '         ' \
        | LC_ALL=C tr -s ' ' \
        | sed -e 's/^[ .]*//' -e 's/[ .]*$//'
}

# Tên để đặt cho bảng tên riêng của một phim: tên phim nếu đọc được, không thì
# video_id (chính tên thư mục), và không bao giờ rỗng.
ten_phim() {
    local thu_muc="$1" id ten=""
    id="$(basename -- "$thu_muc")"
    # Chỉ dùng Python của venv, không dùng python3 của máy: trên máy chưa cài
    # công cụ dòng lệnh của Apple, gọi python3 sẽ bật lên hộp thoại đòi cài.
    if [ -x "$VENV_DIR/bin/python" ] && [ -f "$thu_muc/S0_info.json" ]; then
        ten="$("$VENV_DIR/bin/python" -c "$PY_DOC_TEN" "$thu_muc/S0_info.json" 2>/dev/null </dev/null | tr -d '\r\n')" || ten=""
    fi
    ten="$(loc_ten_file "$ten")"
    [ -n "$ten" ] || ten="$(loc_ten_file "$id")"
    [ -n "$ten" ] || ten="phim"
    printf '%s' "$ten"
}

# Đường dẫn đích cho một bảng tên: KHÔNG BAO GIỜ đè lên file đã có.
#
# Hai phim trùng tên, hoặc một lần gỡ trước đã cất bảng vào đây: lấy tên kế tiếp
# "… (2)", "… (3)". Riêng khi file đã có giống hệt từng byte thì dùng lại chính
# nó — chạy lại file này sau một lần gỡ dở không đẻ ra bản sao thừa.
cho_dich() {
    local nguon="$1" thu_muc="$2" goc="$3" duoi="$4" dich so=1
    dich="$thu_muc/$goc$duoi"
    while [ -e "$dich" ] || [ -L "$dich" ]; do
        if [ -f "$dich" ] && cmp -s "$nguon" "$dich"; then
            break
        fi
        so=$((so + 1))
        dich="$thu_muc/$goc ($so)$duoi"
    done
    printf '%s' "$dich"
}

SO_DA_CHEP=0
LOI_CHEP=0

# Chép một bảng tên rồi KIỂM LẠI TỪNG BYTE. Chỉ bản chép đã được so khớp mới
# được tính là "đã cất"; bản chép dở do chính lần này tạo ra thì dọn đi.
chep_mot_bang() {
    local nguon="$1" thu_muc="$2" goc="$3" duoi="$4" ghi_chu="$5" dich tao=0
    dich="$(cho_dich "$nguon" "$thu_muc" "$goc" "$duoi")"
    if [ ! -e "$dich" ]; then
        tao=1
        cp -p "$nguon" "$dich" 2>/dev/null || cp "$nguon" "$dich" 2>/dev/null
    fi
    if [ -f "$dich" ] && cmp -s "$nguon" "$dich"; then
        SO_DA_CHEP=$((SO_DA_CHEP + 1))
        tin "• $(basename -- "$dich")$ghi_chu"
        return 0
    fi
    [ "$tao" = "1" ] && rm -f "$dich" 2>/dev/null
    LOI_CHEP=1
    nhac "Không chép được bảng tên: $nguon"
    return 1
}

# Mỗi thứ nằm ngay trong thư mục SrtGen thuộc loại nào:
#   giu — được giữ có chủ ý (thư mục kết quả, nhánh dẫn tới nó, kết quả mặc định cũ,
#         và mọi thứ nằm trong thư mục kết quả đang được giữ)
#   xoa — thứ của SrtGen, có trong danh sách xoá
#   la  — thứ file này không biết là gì: không bao giờ đụng tới
loai_trong_app() {
    local p ten
    p="$(gon_duong_dan "$1")"
    ten="$(basename -- "$p")"
    if [ "$XOA_KET_QUA" != "1" ] && nam_trong "$OUT_DIR" "$p"; then echo giu; return 0; fi
    if [ "$p" = "$OUT_DIR" ]; then echo xoa; return 0; fi
    if [ "$p" = "$OUT_MAC_DINH" ]; then echo giu; return 0; fi
    if [ "$p" = "$WORK_XOA1" ] || [ "$p" = "$WORK_XOA2" ]; then echo xoa; return 0; fi
    case "$ten" in
        venv|python|bin|uv-cache|models|logs|.tien-do|cai-dat.json|dang-chay.url|jobs|settings.json|settings.json.tmp|.DS_Store)
            echo xoa; return 0 ;;
    esac
    # Nằm trong thư mục kết quả đang được giữ (người dùng chọn chính thư mục
    # SrtGen, hay một thư mục cha của nó, làm chỗ lưu kết quả): đó là file .srt
    # và bảng tên vừa cất của họ, không phải "thứ lạ" để bảo họ tự xoá.
    if [ "$XOA_KET_QUA" != "1" ] && nam_trong "$p" "$OUT_DIR"; then echo giu; return 0; fi
    echo la
}

[ -t 1 ] && { clear 2>/dev/null || true; }
printf '\n%s\n' "$VACH"
printf '%s\n' "${DAM}GỠ SRTGEN KHỎI MÁY${HET}"
printf '%s\n\n' "$VACH"

# --------------------------------------------------------------------------- #
# 0. Hỏi cấu hình xem thư mục kết quả thật sự nằm ở đâu
# --------------------------------------------------------------------------- #
doc_cau_hinh || true
OUT_DIR="$(gon_duong_dan "$OUT_DIR")"
WORK_DIR="$(gon_duong_dan "$WORK_DIR")"

# Thư mục làm việc chỉ bị xoá khi nó nằm HẲN bên trong thư mục của SrtGen. Nằm
# ngoài thì có thể là thư mục dùng chung với thứ khác; còn nếu nó CHÍNH LÀ thư
# mục SrtGen thì xoá nó là xoá luôn file riêng người dùng để trong đó.
WORK_XOA1=""
if [ "$WORK_DIR" != "$APP_SUPPORT" ] && nam_trong "$WORK_DIR" "$APP_SUPPORT"; then
    WORK_XOA1="$WORK_DIR"
else
    WORK_NGOAI=1
fi
# Đã đổi thư mục làm việc mà chỗ mặc định cũ vẫn còn: đó vẫn là dữ liệu làm
# việc của SrtGen (và có thể còn bảng tên riêng cũ), nên dọn cùng.
WORK_XOA2=""
if [ "$WORK_DIR" != "$WORK_MAC_DINH" ] && [ -d "$WORK_MAC_DINH" ]; then
    WORK_XOA2="$WORK_MAC_DINH"
fi

# Chỉ xoá được thư mục kết quả khi nó nằm HẲN bên trong thư mục của SrtGen.
# Người dùng đặt kết quả vào ~/Desktop hay ~/Documents mà file này "xoá thư mục
# kết quả" thì mất sạch những thứ khác trong đó — không đường lùi.
OUT_XOA_DUOC=0
if [ "$OUT_DIR" != "$APP_SUPPORT" ] && nam_trong "$OUT_DIR" "$APP_SUPPORT"; then
    OUT_XOA_DUOC=1
fi

# Thư mục kết quả nằm lồng bên trong một thứ sắp bị xoá — đúng cái bẫy khiến
# file .srt biến mất trong im lặng. Bật cờ để nói rõ ngay ở bảng liệt kê.
KET_QUA_LONG_NHAU=0
for _cha in "$WORK_XOA1" "$WORK_XOA2" "$MODEL_DIR" "$UV_CACHE" "$VENV_DIR" "$PY_DIR" "$BIN_DIR" \
            "$LOG_DIR" "$JOBS_DIR" "$CACHE_DIR" "$MOC_DIR"; do
    if nam_trong "$OUT_DIR" "$_cha"; then
        KET_QUA_LONG_NHAU=1
        break
    fi
done

# Mô hình nghe: bản cài mới để mô hình thật ở Application Support và một lối
# tắt ở Caches; bản cài cũ thì ngược lại. Chỉ liệt kê chỗ chứa THẬT, để con số
# dung lượng không bị đếm hai lần hay hiện 0 byte của một lối tắt.
if [ -L "$MODEL_DIR" ]; then
    MODEL_THAT="$CACHE_MODEL_DIR"
else
    MODEL_THAT="$MODEL_DIR"
fi

SO_BANG="$(dem_trong_work 'names.json')"
SO_BANG_HONG="$(dem_trong_work 'names.json.hong-*')"
SO_NHAP="$(dem_trong_work 'edit_draft.json')"
SO_SRT_WORK="$(dem_trong_work '*.srt')"

CO_KET_QUA=0
[ -e "$OUT_DIR" ] && CO_KET_QUA=1
HOI_XOA_KET_QUA=0
[ "$CO_KET_QUA" = "1" ] && [ "$OUT_XOA_DUOC" = "1" ] && HOI_XOA_KET_QUA=1

# --------------------------------------------------------------------------- #
# 1. Liệt kê chính xác những gì sẽ bị xoá
# --------------------------------------------------------------------------- #
printf '%s\n\n' "   ${DAM}Những thứ sau đây SẼ BỊ XOÁ:${HET}"

CO_GI_DE_XOA=0
liet_ke "$VENV_DIR"    "Các thư viện của SrtGen"                          && CO_GI_DE_XOA=1
liet_ke "$PY_DIR"      "Bản Python 3.12 riêng của SrtGen"                 && CO_GI_DE_XOA=1
liet_ke "$BIN_DIR"     "uv và ffmpeg của riêng SrtGen"                    && CO_GI_DE_XOA=1
liet_ke "$MODEL_THAT"  "Mô hình nghe đã tải về (tải lại được, ~1.6GB)"    && CO_GI_DE_XOA=1
liet_ke "$UV_CACHE"    "Kho gói đã tải để cài nhanh hơn lần sau"          && CO_GI_DE_XOA=1

for _w in "$WORK_XOA1" "$WORK_XOA2"; do
    [ -n "$_w" ] || continue
    if [ "$_w" = "$WORK_XOA2" ]; then
        liet_ke "$_w" "Dữ liệu làm việc cũ ở chỗ mặc định (cùng loại như dưới)" && CO_GI_DE_XOA=1
    else
        liet_ke "$_w" "Dữ liệu làm việc của các phim" && CO_GI_DE_XOA=1
    fi
done
if [ -e "$WORK_XOA1" ] || [ -n "$WORK_XOA2" ]; then
    # Nói đúng sự thật về thư mục này. Gọi nó là "dữ liệu tạm" là lừa người
    # dùng: trong đó có bản sửa dở và bảng tên riêng họ tự gõ.
    tin "Gồm: âm thanh đã tách, kết quả trung gian của từng bước, bản sửa dở"
    tin "chưa lưu của tab Sửa phụ đề, và bản chép của file bạn kéo vào app"
    tin "(file gốc của bạn không bị đụng tới)."
    if [ "$SO_BANG" -gt 0 ] || [ "$SO_BANG_HONG" -gt 0 ]; then
        if [ "$SO_BANG" -gt 0 ]; then
            tin "${XANH}Có $SO_BANG bảng tên riêng bạn đã lập. Bảng tên KHÔNG bị mất:${HET}"
        else
            tin "${XANH}Có bản cất của bảng tên riêng từng bị hỏng. Chúng KHÔNG bị mất:${HET}"
        fi
        tin "${XANH}trước khi xoá, từng bảng được chép sang thư mục kết quả:${HET}"
        tin "  $OUT_DIR/$TEN_THU_MUC_BANG"
        if [ "$SO_BANG" -gt 0 ] && [ "$SO_BANG_HONG" -gt 0 ]; then
            tin "(kèm $SO_BANG_HONG bản cất của bảng tên từng bị hỏng)"
        fi
        if [ "$HOI_XOA_KET_QUA" = "1" ]; then
            tin "Nếu ở câu hỏi sau bạn chọn xoá cả thư mục kết quả, bảng tên sẽ"
            tin "được chép ra màn hình nền thay vào đó."
        fi
    fi
    if [ "$SO_NHAP" -gt 0 ]; then
        tin "${VANG}Có $SO_NHAP bản sửa dở CHƯA LƯU — những bản này sẽ mất.${HET}"
        tin "Muốn giữ: mở app, vào tab Sửa phụ đề, bấm Lưu, rồi mới chạy lại file này."
    fi
    if [ "$SO_SRT_WORK" -gt 0 ]; then
        tin "${VANG}Có $SO_SRT_WORK file .srt là bản sao của file bạn mở bằng nút Chọn file .srt…${HET}"
        tin "Bản sao này sẽ bị xoá. File bạn đã lấy về máy bằng nút Lưu và tải về"
        tin "(nằm trong thư mục Tải về) thì không bị ảnh hưởng."
    fi
fi

liet_ke "$JOBS_DIR"    "Lịch sử các lần chạy (danh sách việc trong app)"  && CO_GI_DE_XOA=1
liet_ke "$CACHE_AI"    "Câu trả lời của AI đã nhớ sẵn (lần sau hỏi lại)"  && CO_GI_DE_XOA=1
liet_ke "$CACHE_DICH"  "Bản dịch tiếng Việt đã nhớ sẵn (lần sau dịch lại)" && CO_GI_DE_XOA=1
# Phần còn lại của thư mục nhớ tạm cũng là của SrtGen. Liệt kê ra, để dòng
# "xoá thư mục nhớ tạm" không cuốn theo thứ gì mà màn hình chưa hề nhắc tới.
while IFS= read -r _con; do
    [ -n "$_con" ] || continue
    case "$(basename -- "$_con")" in ai|translate) continue ;; esac
    [ "$_con" = "$MODEL_THAT" ] && continue
    if [ "$(basename -- "$_con")" = "models" ]; then
        # Lối tắt về thư mục mô hình ở trên: không chiếm chỗ, không cần một dòng.
        [ -L "$_con" ] && continue
        liet_ke "$_con" "Mô hình nghe app tự tải về (tải lại được)" && CO_GI_DE_XOA=1
        continue
    fi
    liet_ke "$_con" "Thứ nhớ tạm khác của SrtGen" && CO_GI_DE_XOA=1
done <<EOF
$(find "$CACHE_DIR" -mindepth 1 -maxdepth 1 2>/dev/null | LC_ALL=C sort)
EOF
liet_ke "$LOG_DIR"     "Nhật ký hoạt động"                               && CO_GI_DE_XOA=1
liet_ke "$SETTINGS_JSON" "Cài đặt của bạn — ${VANG}kể cả khoá API đã nhập${HET}" && CO_GI_DE_XOA=1
liet_ke_neu_co "$SETTINGS_TMP" "Bản ghi dở của cài đặt (có thể chứa khoá API)" && CO_GI_DE_XOA=1
liet_ke_neu_co "$MOC_DIR"  "Dấu mốc tiến độ của bộ cài"                  && CO_GI_DE_XOA=1
liet_ke_neu_co "$RECEIPT"  "Hồ sơ lần cài đặt"                           && CO_GI_DE_XOA=1
liet_ke_neu_co "$URL_FILE" "Địa chỉ của lần mở app gần nhất"             && CO_GI_DE_XOA=1
liet_ke "$DESKTOP_APP" "Biểu tượng SrtGen trên màn hình nền"             && CO_GI_DE_XOA=1

printf '\n%s\n\n' "   ${DAM}Những thứ sau đây ĐƯỢC GIỮ LẠI:${HET}"

if liet_ke "$OUT_DIR" "${XANH}Các file phụ đề .srt bạn đã tạo${HET}"; then
    SO_SRT="$(find "$OUT_DIR" -type f -name '*.srt' 2>/dev/null | wc -l | tr -d ' ')"
    tin "Đang có $SO_SRT file .srt trong đó."
    if [ -d "$OUT_DIR/$TEN_THU_MUC_BANG" ]; then
        tin "Kèm thư mục $TEN_THU_MUC_BANG: bảng tên riêng đã cất từ lần gỡ trước."
    fi
    if [ "$OUT_DIR" != "$OUT_MAC_DINH" ]; then
        tin "(Đây là thư mục bạn tự chọn trong tab Cài đặt, không phải chỗ mặc định.)"
    fi
    if [ "$OUT_XOA_DUOC" = "0" ]; then
        tin "Thư mục này nằm ngoài thư mục của SrtGen nên file này KHÔNG BAO GIỜ"
        tin "xoá nó. Muốn xoá các file .srt trong đó thì bạn tự xoá bằng Finder."
    fi
fi
if [ "$OUT_DIR" != "$OUT_MAC_DINH" ] && [ -d "$OUT_MAC_DINH" ] && ! nam_trong "$OUT_DIR" "$OUT_MAC_DINH"; then
    if liet_ke "$OUT_MAC_DINH" "${XANH}Thư mục kết quả mặc định cũ (trước khi bạn đổi chỗ lưu)${HET}"; then
        tin "Đang có $(find "$OUT_MAC_DINH" -type f -name '*.srt' 2>/dev/null | wc -l | tr -d ' ') file .srt trong đó. File này không xoá nó."
    fi
fi
if [ "$WORK_NGOAI" = "1" ] && liet_ke "$WORK_DIR" "${VANG}Thư mục làm việc tạm bạn tự chọn${HET}"; then
    tin "Không phải thư mục riêng của SrtGen nên file này KHÔNG xoá."
    tin "Trong đó có thể có bảng tên riêng (names.json) — hãy giữ những file đó."
fi
# Thứ lạ trong thư mục SrtGen (thường là file người dùng tự để vào): nói ra
# ngay từ đầu rằng nó được giữ, để lúc kết thúc thư mục còn đó không gây bất ngờ.
while IFS= read -r _con; do
    [ -n "$_con" ] || continue
    [ "$(loai_trong_app "$_con")" = "la" ] || continue
    liet_ke "$_con" "${VANG}Thứ không phải do SrtGen tạo ra — không đụng tới${HET}"
done <<EOF
$(find "$APP_SUPPORT" -mindepth 1 -maxdepth 1 2>/dev/null | LC_ALL=C sort)
EOF
printf '   %s(giữ)%s     Thư mục mã nguồn SrtGen mà bạn đang mở file này từ đó\n' "$XAM" "$HET"

if [ "$KET_QUA_LONG_NHAU" = "1" ] && [ "$CO_KET_QUA" = "1" ]; then
    printf '\n'
    nhac "Thư mục kết quả của bạn nằm BÊN TRONG một thư mục sắp bị xoá."
    tin "$OUT_DIR"
    tin "Chương trình sẽ xoá vòng quanh nó và giữ nguyên thư mục kết quả,"
    tin "nên các file .srt vẫn còn nguyên sau khi gỡ."
fi

# Bộ cài này không thêm bất cứ thứ gì vào phần dùng chung của máy. Nói rõ ra,
# vì đó chính là lý do người dùng chọn cách cài này.
cat <<KHONGDUNG

   ${DAM}Máy của bạn không có gì khác bị đụng tới.${HET}
   Bộ cài của SrtGen không cài Homebrew, không cài Xcode, không sửa Python
   sẵn có của macOS và không thêm gì vào thư mục dùng chung của hệ thống.

KHONGDUNG

# Trường hợp riêng: máy đã có sẵn uv ở ~/.local/bin từ trước (do người dùng
# hoặc phần mềm khác cài). Không được xoá — nó không phải của SrtGen.
if [ -x "$HOME/.local/bin/uv" ]; then
    nhac "Máy có sẵn uv ở ~/.local/bin — thứ này KHÔNG bị xoá."
    tin "Nó có từ trước hoặc do phần mềm khác cài, có thể đang được dùng chung."
fi

if [ "$CO_GI_DE_XOA" = "0" ] && [ "$CO_KET_QUA" = "0" ]; then
    printf '\n'
    ok "Máy này chưa cài SrtGen, hoặc đã gỡ rồi. Không có gì để làm."
    exit 0
fi

# --------------------------------------------------------------------------- #
# 2. Hỏi xác nhận — phải gõ tay, không bấm Enter cho qua được
# --------------------------------------------------------------------------- #
printf '%s\n' "$VACH"
cat <<'CANHBAO'

   Xoá xong thì không lấy lại được. Muốn dùng lại SrtGen sau này, bạn chạy
   file CaiDat.command để cài lại từ đầu (phải tải lại mô hình ~1.6GB).

CANHBAO

printf '   %sGõ đúng chữ  XOA  rồi nhấn Enter để xoá.%s\n' "$DAM" "$HET"
printf '   %s\n' "Gõ bất cứ thứ gì khác, hoặc chỉ nhấn Enter, thì không xoá gì cả."
printf '\n   Trả lời: '
# Không có một dòng trả lời trọn vẹn (không ai ngồi trước máy, hoặc gõ mà chưa
# nhấn Enter) thì coi như KHÔNG đồng ý. Chỉ một dòng gõ đúng chữ XOA mới cho xoá.
if ! read -r TRA_LOI; then
    printf '\n\n'
    ok "Không nhận được câu trả lời nên dừng lại. Chưa xoá gì cả."
    exit 1
fi
# Chỉ bỏ khoảng trắng ở HAI ĐẦU (và \r). Không gộp chữ lại: "X O A" không phải
# là "XOA" — HUONG-DAN hứa gõ bất cứ thứ gì khác thì không xoá gì cả.
TRA_LOI="$(printf '%s' "$TRA_LOI" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"

if [ "$TRA_LOI" != "XOA" ]; then
    printf '\n'
    ok "Đã dừng. Không có gì bị xoá."
    exit 0
fi

# Câu hỏi thứ hai, chỉ hỏi khi thật sự có file kết quả VÀ thư mục đó là của
# SrtGen. Mặc định là GIỮ.
if [ "$HOI_XOA_KET_QUA" = "1" ]; then
    printf '\n%s\n' "$VACH"
    printf '\n   %s\n' "${DAM}Còn các file phụ đề .srt đã tạo thì sao?${HET}"
    printf '   %s\n' "Mặc định là GIỮ LẠI. Đây thường là thứ duy nhất đáng giá trong máy."
    printf '   %s\n' "Chỉ khi bạn chắc chắn muốn xoá luôn, hãy gõ:  XOA-KET-QUA"
    printf '   %s\n' "(Khi đó bảng tên riêng được chép ra màn hình nền để không mất theo.)"
    printf '\n   Trả lời (nhấn Enter để giữ lại): '
    read -r TRA_LOI2 || TRA_LOI2=""
    TRA_LOI2="$(printf '%s' "$TRA_LOI2" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [ "$TRA_LOI2" = "XOA-KET-QUA" ] && XOA_KET_QUA=1
fi

# --------------------------------------------------------------------------- #
# 3a. Cất bảng tên riêng — TRƯỚC mọi lệnh xoá
#
# Làm trước tiên vì hai lẽ: tên phim được đọc bằng Python của venv (sắp bị
# xoá), và nếu chép hỏng thì chưa có gì bị mất để phải hối.
# --------------------------------------------------------------------------- #
if [ "$XOA_KET_QUA" = "1" ]; then
    BANG_TEN_DICH="$HOME/Desktop/SrtGen-bang-ten-rieng-$THOI_DIEM"
else
    BANG_TEN_DICH="$OUT_DIR/$TEN_THU_MUC_BANG"
fi

DS_BANG=""
for _w in "$WORK_XOA1" "$WORK_XOA2"; do
    [ -n "$_w" ] || continue
    DS_BANG="$DS_BANG$(tep_trong_thu_muc "$_w" 'names.json')
$(tep_trong_thu_muc "$_w" 'names.json.hong-*')
"
done
# Sắp xoá cả thư mục kết quả: bảng tên đã cất vào đó từ lần gỡ trước cũng phải
# được mang ra màn hình nền, nếu không nó mất theo đúng thư mục ấy.
DS_BANG_CU=""
if [ "$XOA_KET_QUA" = "1" ] && [ -d "$OUT_DIR/$TEN_THU_MUC_BANG" ]; then
    DS_BANG_CU="$(find "$OUT_DIR/$TEN_THU_MUC_BANG" -mindepth 1 -maxdepth 1 -type f 2>/dev/null | LC_ALL=C sort)"
fi

GIU_WORK=0
if [ -n "$(printf '%s' "$DS_BANG$DS_BANG_CU" | tr -d '[:space:]')" ]; then
    printf '\n%s\n' "$VACH"
    printf '%s\n\n' "${DAM}Cất bảng tên riêng trước khi xoá…${HET}"
    if mkdir -p "$BANG_TEN_DICH" 2>/dev/null && [ -d "$BANG_TEN_DICH" ]; then
        tin "Chép vào: $BANG_TEN_DICH"
        while IFS= read -r _tep; do
            [ -n "$_tep" ] || continue
            _phim="$(dirname -- "$_tep")"
            _ten="$(ten_phim "$_phim")"
            case "$(basename -- "$_tep")" in
                names.json) _duoi=".names.json" ;;
                *)          _duoi=".names.json.hong-${_tep##*.hong-}" ;;
            esac
            chep_mot_bang "$_tep" "$BANG_TEN_DICH" "$_ten" "$_duoi" "   (phim $(basename -- "$_phim"))"
        done <<EOF
$DS_BANG
EOF
        while IFS= read -r _tep; do
            [ -n "$_tep" ] || continue
            _ten="$(basename -- "$_tep")"
            _goc="${_ten%%.names.json*}"
            chep_mot_bang "$_tep" "$BANG_TEN_DICH" "$_goc" "${_ten#"$_goc"}" "   (cất từ lần gỡ trước)"
        done <<EOF
$DS_BANG_CU
EOF
    else
        LOI_CHEP=1
        nhac "Không tạo được thư mục để cất bảng tên riêng:"
        tin "$BANG_TEN_DICH"
    fi

    printf '\n'
    if [ "$LOI_CHEP" = "1" ]; then
        # Chép hỏng dù chỉ một bảng: thư mục làm việc giữ nguyên, để bảng tên
        # gốc vẫn còn đó. Mất một lần gỡ còn hơn mất bảng tên gõ tay nhiều ngày.
        GIU_WORK=1
        nhac "${DO}Chưa cất được đủ bảng tên riêng.${HET} Vì vậy thư mục làm việc được GIỮ NGUYÊN."
        tin "Hãy chép thư mục đó ra chỗ khác (ví dụ vào Tài liệu), rồi chạy lại file này:"
        for _w in "$WORK_XOA1" "$WORK_XOA2"; do
            [ -n "$_w" ] && tin "  $_w"
        done
        if [ "$XOA_KET_QUA" = "1" ] && [ -n "$DS_BANG_CU" ]; then
            XOA_KET_QUA=0
            tin "Thư mục kết quả cũng được GIỮ NGUYÊN, vì bảng tên đã cất trong đó"
            tin "chưa chép ra được."
        fi
    else
        ok "Đã chép $SO_DA_CHEP bảng tên riêng vào:"
        tin "$BANG_TEN_DICH"
        tin "Từng bảng đã được so lại với bản gốc: giống nhau từng byte."
    fi
    # Bảng tên vừa cất nằm trong thư mục kết quả: từ giờ thư mục đó có thứ phải
    # giữ, kể cả khi trước đây nó chưa tồn tại. Bật cờ để chốt chặn trong xoa()
    # bảo vệ nó như bảo vệ file .srt.
    if [ "$SO_DA_CHEP" -gt 0 ] && [ "$XOA_KET_QUA" != "1" ] && nam_trong "$BANG_TEN_DICH" "$OUT_DIR"; then
        CO_KET_QUA=1
    fi
fi

# --------------------------------------------------------------------------- #
# 3b. Xoá
# --------------------------------------------------------------------------- #
printf '\n%s\n' "$VACH"
printf '%s\n\n' "${DAM}Đang xoá…${HET}"

# Xoá sạch bên trong "$1" NHƯNG chừa lại nhánh dẫn tới thư mục kết quả.
#
# Đi từng tầng một thay vì `rm -rf` cả cụm: thư mục kết quả có thể nằm sâu bên
# trong (ví dụ người dùng đặt out_dir là …/work/ket-qua), nên phải giữ nguyên cả
# chuỗi thư mục cha dẫn xuống nó, đồng thời vẫn dọn hết những thứ khác.
xoa_tru_ket_qua() {
    local goc="$1" con
    [ "$(gon_duong_dan "$goc")" = "$OUT_DIR" ] && return 0
    while IFS= read -r con; do
        [ -n "$con" ] || continue
        if nam_trong "$OUT_DIR" "$con"; then
            xoa_tru_ket_qua "$con"
        else
            rm -rf "$con" 2>/dev/null || true
        fi
    done <<EOF
$(find "$goc" -mindepth 1 -maxdepth 1 2>/dev/null)
EOF
    return 0
}

xoa() {
    local duong_dan="$1" mo_ta="$2"
    if [ ! -e "$duong_dan" ] && [ ! -L "$duong_dan" ]; then
        return 0
    fi
    # Chốt chặn cuối cùng: không bao giờ để một lệnh xoá cuốn theo thư mục kết
    # quả. Người dùng đã nói "giữ file .srt" thì câu trả lời đó phải thắng mọi
    # thứ khác, kể cả khi họ tự đặt out_dir vào giữa vùng làm việc của tool.
    if [ "$XOA_KET_QUA" != "1" ] && [ "$CO_KET_QUA" = "1" ] && nam_trong "$OUT_DIR" "$duong_dan"; then
        if [ "$(gon_duong_dan "$duong_dan")" = "$OUT_DIR" ]; then
            nhac "Giữ nguyên: $mo_ta"
            tin "$duong_dan"
            tin "Đây cũng chính là thư mục chứa file .srt của bạn."
        else
            xoa_tru_ket_qua "$duong_dan"
            nhac "Xoá một phần: $mo_ta"
            tin "Giữ lại nhánh dẫn tới thư mục kết quả: $OUT_DIR"
        fi
        return 0
    fi
    if rm -rf "$duong_dan" 2>/dev/null; then
        ok "Đã xoá: $mo_ta"
    else
        nhac "Không xoá được: $mo_ta"
        tin "$duong_dan"
        tin "Có thể app đang chạy. Hãy đóng SrtGen rồi chạy lại file này."
    fi
}

xoa "$VENV_DIR"    "các thư viện"
xoa "$PY_DIR"      "bản Python riêng"
xoa "$BIN_DIR"     "uv và ffmpeg của SrtGen"
xoa "$MODEL_DIR"   "mô hình nghe"
xoa "$UV_CACHE"    "kho gói đã tải"
if [ "$WORK_NGOAI" = "1" ]; then
    nhac "Giữ nguyên thư mục làm việc tạm do bạn tự chọn (không phải thư mục riêng của SrtGen):"
    tin "$WORK_DIR"
fi
for _w in "$WORK_XOA1" "$WORK_XOA2"; do
    [ -n "$_w" ] || continue
    if [ "$GIU_WORK" = "1" ]; then
        nhac "Giữ nguyên thư mục làm việc (chưa cất được đủ bảng tên riêng):"
        tin "$_w"
    else
        xoa "$_w" "dữ liệu làm việc của các phim (bảng tên riêng đã cất ở trên)"
    fi
done
xoa "$JOBS_DIR"    "lịch sử các lần chạy"
xoa "$LOG_DIR"     "nhật ký"
xoa "$SETTINGS_JSON" "cài đặt của bạn (kể cả khoá API)"
xoa "$SETTINGS_TMP" "bản ghi dở của cài đặt"
xoa "$MOC_DIR"     "dấu mốc tiến độ cài đặt"
xoa "$DESKTOP_APP" "biểu tượng trên màn hình nền"
xoa "$RECEIPT"     "hồ sơ cài đặt"
xoa "$URL_FILE"    "dấu vết phiên chạy"

# Thư mục nhớ tạm trong ~/Library/Caches: câu trả lời AI, bản dịch đã nhớ, và
# lối tắt tới mô hình nghe (hoặc chính mô hình, nếu máy cài từ bản trước).
xoa "$CACHE_DIR"   "thứ nhớ tạm của SrtGen (câu trả lời AI, bản dịch đã nhớ)"

if [ "$XOA_KET_QUA" = "1" ]; then
    xoa "$OUT_DIR" "thư mục kết quả .srt"
elif [ "$CO_KET_QUA" = "1" ]; then
    printf '\n'
    ok "Giữ nguyên các file .srt của bạn tại:"
    tin "$OUT_DIR"
fi

# Bản chép chương trình mà SrtGen.command đặt vào máy. Xoá SAU CÙNG, vì file
# gỡ cài đặt này rất có thể đang chạy từ chính thư mục đó; bash vẫn đọc tiếp
# được file đang mở dù thư mục đã bị xoá, nhưng để nó cuối cho chắc.
xoa "$APP_SUPPORT/app"     "bản chép chương trình SrtGen"
xoa "$APP_SUPPORT/app.moi" "bản chép dở của lần cập nhật trước"
xoa "$APP_SUPPORT/app.cu"  "bản cũ còn sót của lần cập nhật trước"

# File hiển thị mà Finder tự đặt vào thư mục nào từng được mở ra xem. Không
# chứa gì của người dùng, nhưng để lại thì thư mục SrtGen không bao giờ rỗng.
rm -f "$APP_SUPPORT/.DS_Store" 2>/dev/null || true
# Thư mục cha chỉ xoá khi đã rỗng: người dùng có thể để file riêng trong đó.
rmdir "$APP_SUPPORT" 2>/dev/null && ok "Đã dọn nốt thư mục rỗng của SrtGen." || true

# --------------------------------------------------------------------------- #
# 4. Kiểm lại — nhìn tận mắt xem còn sót gì, rồi mới được nói "đã gỡ"
# --------------------------------------------------------------------------- #
printf '\n%s\n' "$VACH"
printf '%s\n\n' "${DAM}Kiểm lại sau khi xoá…${HET}"

CON_SOT=0
bao_sot() {
    local nang
    nang="$(co_nang "$1" || true)"
    printf '   %s•%s  %s%s\n' "$VANG" "$HET" "$1" "${nang:+   ($nang)}"
    tin "$2"
    CON_SOT=1
}

if [ -d "$APP_SUPPORT" ]; then
    while IFS= read -r _con; do
        [ -n "$_con" ] || continue
        case "$(loai_trong_app "$_con")" in
            giu) continue ;;
            xoa)
                if [ "$GIU_WORK" = "1" ] && { [ "$_con" = "$WORK_XOA1" ] || [ "$_con" = "$WORK_XOA2" ]; }; then
                    bao_sot "$_con" "Giữ lại vì chưa cất được đủ bảng tên riêng (xem phía trên)."
                else
                    bao_sot "$_con" "Không xoá được — có thể app đang chạy. Đóng SrtGen rồi chạy lại file này."
                fi
                ;;
            *)
                bao_sot "$_con" "Không phải thứ SrtGen tạo ra nên file này không đụng tới. Bạn tự xem và xoá nếu không cần."
                ;;
        esac
    done <<EOF
$(find "$APP_SUPPORT" -mindepth 1 -maxdepth 1 2>/dev/null | LC_ALL=C sort)
EOF
fi
if [ -e "$CACHE_DIR" ] || [ -L "$CACHE_DIR" ]; then
    bao_sot "$CACHE_DIR" "Không xoá được — có thể app đang chạy. Đóng SrtGen rồi chạy lại file này."
fi
if [ -e "$DESKTOP_APP" ] || [ -L "$DESKTOP_APP" ]; then
    bao_sot "$DESKTOP_APP" "Không xoá được. Kéo biểu tượng này vào Thùng rác là xong."
fi
[ "$CON_SOT" = "0" ] && ok "Không còn sót thứ gì của SrtGen."

# --------------------------------------------------------------------------- #
# 5. Xong — câu kết phải đúng với những gì vừa kiểm được ở trên
# --------------------------------------------------------------------------- #
printf '\n%s\n' "$VACH"
if [ "$CON_SOT" = "0" ]; then
    printf '%s\n' "${XANH}${DAM}  ✓  ĐÃ GỠ SRTGEN${HET}"
else
    printf '%s\n' "${VANG}${DAM}  !  GỠ XONG NHƯNG CÒN SÓT MỘT SỐ THỨ${HET}"
fi
printf '%s\n\n' "$VACH"

if [ "$CON_SOT" = "1" ]; then
    printf '   %s\n' "Những thứ còn sót đã được liệt kê ngay phía trên, kèm lý do và cách xử lý."
    printf '\n'
fi

printf '   %s\n' "Những thứ còn lại trên máy mà file này GIỮ có chủ ý:"
if [ -e "$OUT_DIR" ]; then
    tin "• Thư mục kết quả ($(find "$OUT_DIR" -type f -name '*.srt' 2>/dev/null | wc -l | tr -d ' ') file .srt):"
    tin "  $OUT_DIR"
fi
if [ "$SO_DA_CHEP" -gt 0 ] && [ -d "$BANG_TEN_DICH" ]; then
    tin "• Bảng tên riêng đã cất ($SO_DA_CHEP bảng) — mở bằng TextEdit để xem lại:"
    tin "  $BANG_TEN_DICH"
fi
if [ "$OUT_DIR" != "$OUT_MAC_DINH" ] && [ -d "$OUT_MAC_DINH" ]; then
    tin "• Thư mục kết quả mặc định cũ:"
    tin "  $OUT_MAC_DINH"
fi
if [ "$WORK_NGOAI" = "1" ] && [ -e "$WORK_DIR" ]; then
    tin "• Thư mục làm việc tạm bạn tự chọn:"
    tin "  $WORK_DIR"
fi
tin "• Thư mục mã nguồn SrtGen (nơi có file này). Không cần nữa thì kéo nó vào"
tin "  Thùng rác:"
tin "  $PROJECT_DIR"
if [ "$CON_SOT" = "0" ] && [ -d "$APP_SUPPORT" ]; then
    printf '\n'
    tin "Thư mục $APP_SUPPORT vẫn còn"
    tin "vì trong đó có thứ được giữ ở trên. Ngoài những thứ đó, trong máy không"
    tin "còn gì của SrtGen."
fi

cat <<CONLAI

   Bộ cài của SrtGen không thêm gì vào phần dùng chung của máy, nên không có
   lệnh dọn dẹp nào khác phải chạy.

   Cài lại SrtGen bất cứ lúc nào: bấm đúp vào file CaiDat.command.

CONLAI

exit 0
