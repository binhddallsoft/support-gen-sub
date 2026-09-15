#!/bin/bash
# ===========================================================================
# CaiDat.command — cài SrtGen lên máy Mac
#
# Bấm đúp vào file này để chạy. Nếu máy không cho bấm đúp, xem mục
# "Máy báo không mở được file" trong HUONG-DAN.md.
#
# KHÔNG cần mật khẩu quản trị. KHÔNG cài Homebrew. KHÔNG cài Xcode.
# ---------------------------------------------------------------------------
# VÌ SAO BỎ HOMEBREW
# Cài Homebrew lên một máy trắng kéo theo Xcode Command Line Tools (~1GB), hỏi
# mật khẩu quản trị, và mất 10-20 phút trước khi làm được việc gì có ích. Với
# người dùng không phải dân IT, đó là chỗ hỏng đầu tiên và cũng là chỗ hỏng
# hay gặp nhất. Bộ cài này đi đường khác:
#
#   uv        một file nhị phân, tải thẳng về, không cần quyền quản trị
#   Python    bản standalone do uv tải, KHÔNG đụng Python có sẵn của macOS
#   thư viện  cài vào một môi trường riêng của SrtGen
#   ffmpeg    bản tĩnh, tải về thư mục của SrtGen, không cài vào hệ thống
#   yt-dlp    cài bằng pip trong môi trường riêng
#   mô hình   tải về thư mục của SrtGen
#
# Mọi thứ nằm gọn trong  ~/Library/Application Support/SrtGen/
# Gỡ cài đặt = xoá đúng một thư mục (file GoCaiDat.command làm việc đó).
# ---------------------------------------------------------------------------
# Chạy lại lần thứ hai an toàn: bước nào đã xong thì bỏ qua trong vài giây.
# Hỏng giữa chừng thì lần sau chạy tiếp từ đúng bước bị hỏng.
# ===========================================================================

# Cố ý KHÔNG dùng "set -e": mỗi lỗi ở đây phải được dịch thành một câu tiếng
# Việt có ích kèm cách xử lý, chứ không phải làm cửa sổ đóng sập không nói gì.
set -uo pipefail

# --------------------------------------------------------------------------- #
# Đường dẫn — tất cả nằm trong một thư mục duy nhất
# --------------------------------------------------------------------------- #
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"

APP_SUPPORT="$HOME/Library/Application Support/SrtGen"
BIN_DIR="$APP_SUPPORT/bin"            # uv, ffmpeg, ffprobe, lối tắt srtgen
VENV_DIR="$APP_SUPPORT/venv"          # môi trường Python riêng
PY_DIR="$APP_SUPPORT/python"          # Python 3.12 standalone do uv tải
UV_CACHE="$APP_SUPPORT/uv-cache"      # kho gói đã tải, để lần cài sau nhanh hơn
MODEL_DIR="$APP_SUPPORT/models"       # mô hình nghe
LOG_DIR="$APP_SUPPORT/logs"
MOC_DIR="$APP_SUPPORT/.tien-do"       # đánh dấu bước nào đã xong
RECEIPT="$APP_SUPPORT/cai-dat.json"

VPY="$VENV_DIR/bin/python"
SRTGEN="$BIN_DIR/srtgen"

# App tìm mô hình ở thư mục cache chuẩn của macOS. Ta tải về thư mục của SrtGen
# rồi nối hai chỗ đó lại bằng một liên kết, xem hàm chuan_bi_thu_muc_model.
CACHE_DIR="$HOME/Library/Caches/SrtGen"
CACHE_MODEL_DIR="$CACHE_DIR/models"

DESKTOP_APP="$HOME/Desktop/SrtGen.app"
REQ_FILE="$SCRIPT_DIR/requirements-macos.txt"
KHOIDONG="$SCRIPT_DIR/KhoiDong.command"

MODEL_MAC_DINH="large-v3-turbo"

TONG=8
STEP=0
CO_MAU=0
CO_BAN_PHIM=0
[ -t 1 ] && CO_MAU=1
[ -t 0 ] && CO_BAN_PHIM=1

# Mọi lệnh uv trong file này đều dùng thư mục riêng của SrtGen, không đụng vào
# thư mục dùng chung của máy. Nhờ vậy gỡ cài đặt mới thật sự sạch.
export UV_CACHE_DIR="$UV_CACHE"
export UV_PYTHON_INSTALL_DIR="$PY_DIR"
export UV_PYTHON_PREFERENCE=only-managed   # cấm dùng Python hệ thống
export UV_NO_PROGRESS=0

# Ghi lại toàn bộ những gì hiện trên màn hình vào một file log, để khi gặp sự cố
# người dùng chỉ cần gửi đúng một file thay vì chụp mười cái ảnh màn hình.
# Không ghi được log thì vẫn chạy tiếp: log là thứ để gỡ rối, không phải điều
# kiện để cài.
LOG_FILE="/dev/null"
if mkdir -p "$LOG_DIR" 2>/dev/null; then
    LOG_FILE="$LOG_DIR/caidat-$(date '+%Y%m%d-%H%M%S').log"
    exec > >(tee -a "$LOG_FILE") 2>&1
fi

# --------------------------------------------------------------------------- #
# In ra màn hình
# --------------------------------------------------------------------------- #
if [ "$CO_MAU" = "1" ]; then
    DAM=$'\033[1m'; XANH=$'\033[32m'; DO=$'\033[31m'; VANG=$'\033[33m'
    LAM=$'\033[36m'; XAM=$'\033[90m'; HET=$'\033[0m'
else
    DAM=""; XANH=""; DO=""; VANG=""; LAM=""; XAM=""; HET=""
fi

VACH="------------------------------------------------------------------"

# Tên bước + thời gian ước tính. Người chờ trước màn hình cần biết "còn bao lâu"
# hơn là cần biết tên kỹ thuật của việc đang chạy.
buoc() {
    STEP=$((STEP + 1))
    printf '\n%s\n' "$VACH"
    printf '%s\n' "${DAM}Bước $STEP/$TONG — $1${HET}"
    printf '%s\n' "${XAM}mất khoảng $2${HET}"
    printf '%s\n' "$VACH"
}
ok()   { printf '   %s✓%s  %s\n' "$XANH" "$HET" "$1"; }
hong() { printf '   %s✗%s  %s\n' "$DO" "$HET" "$1"; }
nhac() { printf '   %s•%s  %s\n' "$VANG" "$HET" "$1"; }
tin()  { printf '      %s\n' "$1"; }
mo()   { printf '   %s%s%s\n' "$XAM" "$1" "$HET"; }

# --------------------------------------------------------------------------- #
# Đánh dấu tiến độ
#
# Mỗi bước làm xong ghi lại một dấu mốc. Lần chạy sau, bước nào đã có dấu mốc
# VÀ kiểm tra lại vẫn thấy kết quả còn nguyên thì bỏ qua trong một giây.
# Dấu mốc một mình không đủ để bỏ qua — nếu người dùng lỡ xoá mất thư mục thì
# phải cài lại chứ không được tin vào tờ giấy cũ.
# --------------------------------------------------------------------------- #
xong_roi()  { [ -f "$MOC_DIR/$1" ]; }
ghi_xong()  { mkdir -p "$MOC_DIR" 2>/dev/null || true; date '+%Y-%m-%d %H:%M:%S' > "$MOC_DIR/$1" 2>/dev/null || true; }
xoa_moc()   { rm -f "$MOC_DIR/$1" 2>/dev/null || true; }

BUOC_HONG=""

# Dừng hẳn, kèm lời giải thích, cách xử lý, và lời hứa "lần sau chạy tiếp".
bo_cuoc() {
    printf '\n%s\n' "$VACH"
    printf '%s\n' "${DO}${DAM}KHÔNG CÀI TIẾP ĐƯỢC — dừng ở bước $STEP/$TONG${HET}"
    printf '%s\n' "$VACH"
    printf '\n%s\n' "$1"
    shift
    for dong in "$@"; do
        printf '   %s\n' "$dong"
    done
    cat <<GHINHO

   ${DAM}Những bước đã xong vẫn được giữ nguyên.${HET}
   Sửa xong nguyên nhân ở trên rồi bấm đúp lại vào CaiDat.command:
   nó sẽ chạy tiếp từ bước $STEP, không làm lại từ đầu.

GHINHO
    exit 1
}

ket_thuc() {
    printf '\n%s\n' "${XAM}Nhật ký lần cài này: $LOG_FILE${HET}"
    if [ "$CO_BAN_PHIM" = "1" ]; then
        printf '\n%s' "Nhấn phím bất kỳ để đóng cửa sổ này… "
        read -r -n 1 -s _phim || true
        printf '\n'
    fi
}
trap ket_thuc EXIT

# Hỏi có/không. Mặc định là "c" (có) trừ khi truyền tham số thứ hai là "k".
hoi() {
    local cau_hoi="$1" mac_dinh="${2:-c}" tra_loi=""
    local goi_y="[C/k]"
    [ "$mac_dinh" = "k" ] && goi_y="[c/K]"
    if [ "$CO_BAN_PHIM" != "1" ]; then
        # Không có bàn phím (ví dụ chạy tự động): dùng mặc định, và nói rõ ra.
        tin "Không có ai trả lời, tự chọn: $mac_dinh"
        [ "$mac_dinh" = "c" ]
        return
    fi
    printf '\n   %s%s%s %s ' "$DAM" "$cau_hoi" "$HET" "$goi_y"
    read -r tra_loi || tra_loi=""
    tra_loi="$(printf '%s' "$tra_loi" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
    [ -z "$tra_loi" ] && tra_loi="$mac_dinh"
    case "$tra_loi" in
        c|co|có|y|yes|ok|1) return 0 ;;
        *) return 1 ;;
    esac
}

# Tìm một công cụ có sẵn trên máy. macOS mở app từ Finder chỉ đưa cho một PATH
# tối thiểu, nên phải dò tay mấy thư mục quen thuộc.
tim_lenh() {
    local ten="$1" duong_dan
    duong_dan="$(command -v "$ten" 2>/dev/null || true)"
    if [ -n "$duong_dan" ]; then printf '%s' "$duong_dan"; return 0; fi
    for thu_muc in /usr/local/bin /opt/homebrew/bin /opt/local/bin /usr/bin; do
        [ -x "$thu_muc/$ten" ] && { printf '%s' "$thu_muc/$ten"; return 0; }
    done
    return 1
}

# --------------------------------------------------------------------------- #
# Lời chào
# --------------------------------------------------------------------------- #
clear 2>/dev/null || true
cat <<'LOICHAO'

  ####  ####  #####  ####  ##### #   #
  #     #  #    #   #      #     ##  #
  ###   ####    #   #  ##  ###   # # #
    #   #  #    #   #   #  #     #  ##
  ####  #  #    #    ####  ##### #   #

  Công cụ tạo phụ đề tiếng Trung kèm pinyin và bản dịch tiếng Việt

LOICHAO

DA_CAI_TRUOC=0
if [ -d "$MOC_DIR" ] && [ -n "$(ls -A "$MOC_DIR" 2>/dev/null || true)" ]; then
    DA_CAI_TRUOC=1
fi

if [ "$DA_CAI_TRUOC" = "1" ]; then
cat <<LOIGIOITHIEU
  Máy này đã cài SrtGen trước đó (có thể chưa xong).

  Chương trình sẽ xem lại từng bước: bước nào đã xong thì bỏ qua trong vài
  giây, bước nào còn dở thì làm tiếp. Không có gì bị cài đè hay xoá đi.

LOIGIOITHIEU
else
cat <<LOIGIOITHIEU
  Chương trình này sẽ cài SrtGen vào máy của bạn. Việc cài gồm ${TONG} bước và
  mất khoảng 15-30 phút, phần lớn thời gian là chờ tải về.

  ${DAM}Máy sẽ KHÔNG hỏi mật khẩu.${HET} Mọi thứ được cài vào một thư mục riêng
  của SrtGen, không đụng gì tới phần mềm sẵn có của máy. Muốn gỡ về sau thì
  bấm đúp file GoCaiDat.command là sạch.

  Bạn có thể để máy chạy và đi làm việc khác. Đừng đóng cửa sổ này.

LOIGIOITHIEU
fi

if ! hoi "Bắt đầu?"; then
    printf '\n   %s\n' "Đã dừng. Chưa có gì được cài vào máy."
    exit 0
fi

mkdir -p "$APP_SUPPORT" "$BIN_DIR" 2>/dev/null || true

# --------------------------------------------------------------------------- #
# Mở khoá cho hai file .command anh em — làm NGAY, trước mọi việc có thể hỏng
#
# Hai lệnh dưới đây cho phép chạy và gỡ nhãn "tải từ Internet" của Gatekeeper
# khỏi cả ba file .command. Đó chính là thứ sinh ra thông báo "không mở được vì
# không rõ nhà phát triển" ở lần bấm đúp đầu tiên.
#
# VÌ SAO PHẢI NẰM Ở ĐÂY chứ không phải ở bước cuối: nếu việc cài đứt giữa chừng
# (mất mạng ở bước tải Python, tải ffmpeg hay tải mô hình), người dùng vẫn còn
# hai file KhoiDong.command và GoCaiDat.command mang nguyên nhãn quarantine —
# bấm đúp không chạy, và họ kẹt hẳn ở đúng lúc đang bối rối nhất. Đặt ở đây thì
# chỉ cần chạy tới câu "Bắt đầu?" là hai file kia đã dùng được.
#
# Không kiểm lỗi: máy nào không có xattr, hoặc thư mục nằm trên ổ chỉ-đọc, thì
# việc cài vẫn phải chạy tiếp — người dùng còn cách bấm chuột phải rồi chọn Open.
# --------------------------------------------------------------------------- #
chmod +x "$SCRIPT_DIR"/*.command 2>/dev/null || true
xattr -r -d com.apple.quarantine "$SCRIPT_DIR" 2>/dev/null || true

# =========================================================================== #
buoc "Xem qua máy" "10 giây"
# =========================================================================== #

MACOS_VER="$(sw_vers -productVersion 2>/dev/null || echo "?")"
KIEN_TRUC="$(uname -m)"
SO_NHAN="$(sysctl -n hw.physicalcpu 2>/dev/null || echo "?")"
RAM_BYTE="$(sysctl -n hw.memsize 2>/dev/null || echo 0)"
RAM_GB=$(( RAM_BYTE / 1024 / 1024 / 1024 ))
TRONG_GB="$(df -g "$HOME" 2>/dev/null | awk 'NR==2 {print $4}' || echo "?")"

ok "macOS $MACOS_VER"
ok "Số nhân xử lý: $SO_NHAN — bộ nhớ: ${RAM_GB}GB"

# Kiến trúc quyết định chuyện ffmpeg ở bước 5, nên nói rõ ngay từ đây.
CAN_ROSETTA=0
case "$KIEN_TRUC" in
    x86_64)
        ok "Máy dùng chip Intel (x86_64) — đúng loại máy mà bộ cài này nhắm tới."
        ;;
    arm64)
        nhac "Máy dùng chip Apple (M1/M2/M3/M4…)."
        tin "Vẫn cài và chạy được, phần nghe còn nhanh hơn máy Intel 2-3 lần."
        tin "Riêng ffmpeg tải về là bản dành cho chip Intel, nên nó chạy qua lớp"
        tin "chuyển đổi Rosetta của Apple. Chậm hơn một chút, không ảnh hưởng"
        tin "chất lượng phụ đề. Nếu máy đã có sẵn ffmpeg thì bước 5 dùng luôn"
        tin "bản đó và chuyện Rosetta không còn liên quan."
        CAN_ROSETTA=1
        ;;
    *)
        nhac "Không nhận ra loại chip: $KIEN_TRUC. Cứ thử cài tiếp."
        ;;
esac

VER_CHINH="$(printf '%s' "$MACOS_VER" | cut -d. -f1)"
if [ "$VER_CHINH" != "?" ] && [ "$VER_CHINH" -lt 11 ] 2>/dev/null; then
    nhac "macOS $MACOS_VER khá cũ."
    tin "Phần nhận dạng giọng nói có thể không cài được trên bản này."
    tin "Phần kiểm tra, sửa và chuẩn hoá file .srt thì vẫn chạy tốt."
fi

if [ "$TRONG_GB" != "?" ] && [ "$TRONG_GB" -lt 8 ] 2>/dev/null; then
    hong "Ổ đĩa chỉ còn ${TRONG_GB}GB trống."
    bo_cuoc "Máy cần ít nhất 8GB trống để cài (riêng mô hình nghe đã ~1.6GB)." \
        "Hãy xoá bớt file rồi chạy lại CaiDat.command."
elif [ "$TRONG_GB" = "?" ]; then
    nhac "Không đọc được dung lượng trống của ổ đĩa — cứ cài tiếp."
    tin "Chỉ cần chắc là máy còn khoảng 8GB trống."
else
    ok "Ổ đĩa còn ${TRONG_GB}GB trống — đủ chỗ."
fi

# Hỏi PyPI một trang nhỏ, KHÔNG hỏi trang mục lục gốc: https://pypi.org/simple/
# là một tài liệu ~46MB. Tải hết chỗ đó trong 10 giây đòi hỏi đường truyền
# ~37Mbps, nên trên mạng bình thường phép thử này sẽ hết giờ và bộ cài kết luận
# nhầm là "máy không có mạng" rồi dừng, dù mạng vẫn tốt.
# Trang của gói pip chỉ ~100KB và luôn tồn tại.
if curl -sSf --max-time 10 -o /dev/null https://pypi.org/simple/pip/ 2>/dev/null; then
    ok "Máy có kết nối mạng."
else
    hong "Không kết nối được ra Internet."
    bo_cuoc "Việc cài đặt cần tải khoảng 2-3GB từ mạng về." \
        "Hãy kiểm tra lại Wi-Fi hoặc dây mạng rồi chạy lại CaiDat.command."
fi

ghi_xong "1-xem-may"

# =========================================================================== #
buoc "Cài uv — chương trình lo việc tải Python và thư viện" "30 giây, ~15MB"
# =========================================================================== #

# uv là một file nhị phân duy nhất. Nó thay thế toàn bộ vai trò của Homebrew
# trong bộ cài cũ, mà không cần quyền quản trị và không cần Xcode.
tim_uv() {
    for ung_vien in "$BIN_DIR/uv" "$HOME/.local/bin/uv" "$(command -v uv 2>/dev/null || true)"; do
        [ -n "$ung_vien" ] && [ -x "$ung_vien" ] && { printf '%s' "$ung_vien"; return 0; }
    done
    return 1
}

UV="$(tim_uv || true)"

if [ -n "$UV" ] && "$UV" --version >/dev/null 2>&1; then
    ok "Máy đã có uv: $("$UV" --version 2>/dev/null | head -n 1)"
    mo "$UV"
else
    tin "Đang tải uv…"
    # UV_INSTALL_DIR: đặt uv vào thư mục của SrtGen thay vì ~/.local/bin, để lúc
    # gỡ chỉ cần xoá một thư mục.
    # INSTALLER_NO_MODIFY_PATH: không sửa file cấu hình Terminal của người dùng.
    if curl -LsSf --max-time 300 https://astral.sh/uv/install.sh \
        | env UV_INSTALL_DIR="$BIN_DIR" INSTALLER_NO_MODIFY_PATH=1 sh >/dev/null 2>&1; then
        UV="$(tim_uv || true)"
    else
        UV=""
    fi

    if [ -z "$UV" ] || ! "$UV" --version >/dev/null 2>&1; then
        hong "Không cài được uv."
        bo_cuoc "Không tải được uv từ https://astral.sh" \
            "Nguyên nhân hay gặp: mạng công ty chặn, hoặc đang bật VPN." \
            "Hãy thử đổi mạng (ví dụ dùng 4G điện thoại) rồi chạy lại file này."
    fi
    ok "Đã cài uv: $("$UV" --version 2>/dev/null | head -n 1)"
    mo "$UV"
fi

ghi_xong "2-uv"

# =========================================================================== #
buoc "Cài Python 3.12 riêng cho SrtGen" "1-3 phút, ~65MB"
# =========================================================================== #

# Bản Python này là bản standalone do uv tải về, nằm trong thư mục của SrtGen.
# Python có sẵn của macOS không bị đụng tới — đó là điều kiện để gỡ cài đặt
# không làm hỏng thứ gì khác trên máy.
if "$UV" python find 3.12 >/dev/null 2>&1 && xong_roi "3-python"; then
    ok "Python 3.12 riêng đã có sẵn."
    mo "$("$UV" python find 3.12 2>/dev/null | head -n 1)"
else
    tin "Đang tải Python 3.12 (bản riêng, không đụng Python của macOS)…"
    if "$UV" python install 3.12; then
        ok "Python 3.12 đã sẵn sàng."
        mo "$("$UV" python find 3.12 2>/dev/null | head -n 1)"
        ghi_xong "3-python"
    else
        hong "Không tải được Python 3.12."
        bo_cuoc "uv không tải được bản Python dành riêng cho SrtGen." \
            "Thường là do mạng đứt giữa chừng." \
            "Hãy kiểm tra mạng rồi chạy lại CaiDat.command."
    fi
fi

# =========================================================================== #
buoc "Cài các thư viện của SrtGen" "3-8 phút, ~700MB"
# =========================================================================== #

if [ ! -f "$REQ_FILE" ]; then
    bo_cuoc "Thiếu file danh sách thư viện: $REQ_FILE" \
        "Có vẻ thư mục cài đặt bị thiếu file. Hãy tải lại trọn bộ thư mục SrtGen."
fi

# Môi trường cũ có thể hỏng sau khi máy cập nhật (đây là sự cố rất hay gặp và
# rất khó đoán từ triệu chứng). Thử chạy thử, hỏng thì dựng lại từ đầu.
TAO_MOI=1
if [ -x "$VPY" ] && "$VPY" -c 'pass' 2>/dev/null; then
    PHIEN_BAN_PY="$("$VPY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "?")"
    if [ "$PHIEN_BAN_PY" = "3.12" ]; then
        ok "Môi trường riêng của SrtGen đã có (Python $PHIEN_BAN_PY)."
        TAO_MOI=0
    else
        nhac "Môi trường cũ dùng Python $PHIEN_BAN_PY, không phải 3.12 — dựng lại."
    fi
elif [ -e "$VENV_DIR" ]; then
    nhac "Môi trường cũ bị hỏng (thường do máy vừa cập nhật) — dựng lại."
fi

if [ "$TAO_MOI" = "1" ]; then
    rm -rf "$VENV_DIR"
    if "$UV" venv --python 3.12 "$VENV_DIR" >/dev/null 2>&1 && [ -x "$VPY" ]; then
        ok "Đã tạo môi trường riêng cho SrtGen."
    else
        bo_cuoc "Không tạo được môi trường Python riêng." \
            "Hãy chạy lại CaiDat.command. Nếu vẫn lỗi, gửi file nhật ký này:" \
            "$LOG_FILE"
    fi
    xoa_moc "4-thu-vien"
fi

CO_PHAN_NGHE=0
if xong_roi "4-thu-vien" && "$VPY" -c 'import faster_whisper, fastapi, jieba' 2>/dev/null; then
    ok "Các thư viện đã cài đủ từ lần trước."
    CO_PHAN_NGHE=1
else
    tin "Đang cài thư viện. Đây là bước lâu nhất trong phần cài đặt."
    tin "Màn hình sẽ chạy nhiều dòng chữ — đó là bình thường."
    echo
    if "$UV" pip install --python "$VPY" --requirement "$REQ_FILE"; then
        echo
        ok "Đã cài đủ thư viện, gồm cả phần nhận dạng giọng nói."
        CO_PHAN_NGHE=1
        ghi_xong "4-thu-vien"
    else
        echo
        nhac "Có thư viện cài không được. Đang thử lại với phần lõi thôi…"
        tin "Phần lõi đủ để kiểm tra, sửa và chuẩn hoá file .srt — phần dùng"
        tin "được nhiều nhất, và cũng là phần không bao giờ hỏng."
        echo

        # Phần lõi là mọi dòng nằm trước DÒNG MỐC trong file requirements.
        # Mẫu dò phải neo vào cả dòng: phần đầu file requirements có một câu
        # chú thích nhắc tới tên mốc, dò lỏng sẽ dừng ngay ở câu chú thích đó
        # và cắt ra một file rỗng — cài "thành công" mà không cài gì cả.
        # Viết đủ chữ X trong khuôn tên: mktemp của macOS và của Linux hiểu
        # tham số -t khác nhau, dạng đầy đủ này thì cả hai đều chạy đúng.
        REQ_LOI="$(mktemp "${TMPDIR:-/tmp}/srtgen-core-XXXXXX")"
        awk '/^[[:space:]]*#[[:space:]]*@@CORE_END@@[[:space:]]*$/ {exit} {print}' "$REQ_FILE" > "$REQ_LOI"
        if "$UV" pip install --python "$VPY" --requirement "$REQ_LOI"; then
            rm -f "$REQ_LOI"
            echo
            nhac "Đã cài xong phần lõi, nhưng THIẾU phần nhận dạng giọng nói."
            tin "Nghĩa là: bạn dùng được tab Kiểm tra file, tab Sửa phụ đề và"
            tin "chức năng chuẩn hoá .srt, nhưng chưa tạo được phụ đề từ video."
            tin "Nguyên nhân hay gặp nhất: máy Mac Intel đời cũ không còn bản"
            tin "thư viện biên dịch sẵn. Hãy gửi file nhật ký này cho người hỗ trợ:"
            tin "$LOG_FILE"
        else
            rm -f "$REQ_LOI"
            bo_cuoc "Không cài được thư viện." \
                "Hãy kiểm tra kết nối mạng rồi chạy lại CaiDat.command." \
                "Nếu vẫn lỗi, gửi file nhật ký này cho người hỗ trợ:" \
                "$LOG_FILE"
        fi
    fi
fi

# Đăng ký chính chương trình SrtGen vào môi trường vừa tạo, liên kết thẳng tới
# thư mục mã nguồn. Dùng --no-deps vì thư viện đã cài ở trên với đúng phiên bản
# đã ghim; để trình cài tự chọn lại sẽ phá vỡ các bản ghim đó.
if "$UV" pip install --python "$VPY" --quiet --no-deps --editable "$PROJECT_DIR" >/dev/null 2>&1; then
    ok "Đã đăng ký SrtGen vào môi trường riêng."
else
    mo "Không đăng ký được theo cách thông thường — dùng lối tắt bên dưới, vẫn chạy đủ."
fi

# Lối tắt "srtgen" luôn được tạo, kể cả khi bước đăng ký ở trên thất bại. Nhờ nó,
# câu lệnh srtgen doctor ở bước 8 và trong tài liệu luôn dùng được.
cat > "$SRTGEN" <<SHIM
#!/bin/bash
# Lối tắt gọi SrtGen từ Terminal. File này do CaiDat.command tạo ra.
# Dùng: "$SRTGEN" doctor
export PYTHONPATH="$PROJECT_DIR\${PYTHONPATH:+:\$PYTHONPATH}"
export PATH="$BIN_DIR:$VENV_DIR/bin:\$PATH"
export PYTHONIOENCODING=utf-8
exec "$VPY" -m srtgen.cli "\$@"
SHIM
chmod +x "$SRTGEN" 2>/dev/null || true

# =========================================================================== #
buoc "Chuẩn bị ffmpeg — công cụ xử lý âm thanh" "0-2 phút, ~120MB"
# =========================================================================== #

# Máy đã có sẵn thì dùng luôn, không tải gì cả. Đây là trường hợp tốt nhất:
# nhanh hơn, và không thêm một bản ffmpeg thứ hai vào máy.
FFMPEG_HE_THONG="$(tim_lenh ffmpeg || true)"
FFPROBE_HE_THONG="$(tim_lenh ffprobe || true)"

lien_ket_vao_venv() {
    # srtgen tìm công cụ ở thư mục bin của môi trường riêng TRƯỚC TIÊN.
    # Đặt một liên kết ở đó là cách chắc chắn nhất để app luôn thấy đúng bản
    # ffmpeg mà bộ cài này chuẩn bị, kể cả khi app được mở từ biểu tượng Desktop
    # (lúc đó macOS chỉ đưa cho một PATH tối thiểu).
    local nguon="$1" ten="$2"
    [ -x "$nguon" ] || return 1
    ln -sf "$nguon" "$VENV_DIR/bin/$ten" 2>/dev/null || return 1
    return 0
}

kiem_ffmpeg() {
    [ -x "$1" ] && "$1" -version >/dev/null 2>&1
}

FFMPEG_DUNG=""
if [ -n "$FFMPEG_HE_THONG" ] && [ -n "$FFPROBE_HE_THONG" ] \
   && kiem_ffmpeg "$FFMPEG_HE_THONG"; then
    ok "Máy đã có sẵn ffmpeg — dùng luôn, không phải tải."
    mo "$FFMPEG_HE_THONG"
    FFMPEG_DUNG="$FFMPEG_HE_THONG"
    lien_ket_vao_venv "$FFMPEG_HE_THONG" ffmpeg || true
    lien_ket_vao_venv "$FFPROBE_HE_THONG" ffprobe || true
    ghi_xong "5-ffmpeg"
elif kiem_ffmpeg "$BIN_DIR/ffmpeg" && kiem_ffmpeg "$BIN_DIR/ffprobe"; then
    ok "ffmpeg của SrtGen đã tải từ lần trước, còn dùng tốt."
    mo "$BIN_DIR/ffmpeg"
    FFMPEG_DUNG="$BIN_DIR/ffmpeg"
    lien_ket_vao_venv "$BIN_DIR/ffmpeg" ffmpeg || true
    lien_ket_vao_venv "$BIN_DIR/ffprobe" ffprobe || true
    ghi_xong "5-ffmpeg"
else
    # Trên máy chip Apple, bản tĩnh của evermeet là bản Intel nên cần Rosetta.
    if [ "$CAN_ROSETTA" = "1" ]; then
        if /usr/bin/arch -x86_64 /usr/bin/true >/dev/null 2>&1; then
            ok "Máy đã có Rosetta — bản ffmpeg Intel sẽ chạy được."
        else
            nhac "Máy chip Apple nhưng chưa bật Rosetta."
            tin "ffmpeg tải về là bản Intel nên cần Rosetta mới chạy."
            tin "Mở ứng dụng Terminal và chạy đúng dòng sau (máy sẽ hỏi mật khẩu):"
            tin "   softwareupdate --install-rosetta --agree-to-license"
            tin "Xong rồi chạy lại CaiDat.command. Bộ cài vẫn tải tiếp bây giờ."
        fi
    fi

    tai_va_giai_nen() {
        local ten="$1" dia_chi="$2" tam
        tam="$(mktemp -d "${TMPDIR:-/tmp}/srtgen-$ten-XXXXXX")" || return 1
        tin "Đang tải ${ten}…"
        if ! curl -fL --retry 3 --retry-delay 3 --max-time 900 \
             -o "$tam/$ten.zip" "$dia_chi" 2>/dev/null; then
            rm -rf "$tam"; return 1
        fi
        if ! unzip -o -q "$tam/$ten.zip" -d "$BIN_DIR" 2>/dev/null; then
            rm -rf "$tam"; return 1
        fi
        rm -rf "$tam"
        chmod +x "$BIN_DIR/$ten" 2>/dev/null || true
        # Bỏ nhãn "tải từ Internet" của macOS, nếu không Gatekeeper sẽ chặn và
        # người dùng thấy một hộp thoại khó hiểu ngay giữa lúc tạo phụ đề.
        xattr -d com.apple.quarantine "$BIN_DIR/$ten" 2>/dev/null || true
        return 0
    }

    LOI_FFMPEG=0
    tai_va_giai_nen ffmpeg  "https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip"  || LOI_FFMPEG=1
    tai_va_giai_nen ffprobe "https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip" || LOI_FFMPEG=1

    if [ "$LOI_FFMPEG" = "0" ] && kiem_ffmpeg "$BIN_DIR/ffmpeg" && kiem_ffmpeg "$BIN_DIR/ffprobe"; then
        ok "Đã cài ffmpeg vào thư mục riêng của SrtGen."
        mo "$BIN_DIR/ffmpeg"
        FFMPEG_DUNG="$BIN_DIR/ffmpeg"
        lien_ket_vao_venv "$BIN_DIR/ffmpeg" ffmpeg || true
        lien_ket_vao_venv "$BIN_DIR/ffprobe" ffprobe || true
        ghi_xong "5-ffmpeg"
    else
        hong "Chưa có ffmpeg."
        tin "Không tải được từ https://evermeet.cx (mạng chặn, hoặc trang đang bận)."
        if [ "$CAN_ROSETTA" = "1" ] && ! /usr/bin/arch -x86_64 /usr/bin/true >/dev/null 2>&1; then
            tin "Cũng có thể do máy chưa bật Rosetta như nhắc ở trên."
        fi
        tin "Thiếu ffmpeg thì KHÔNG tạo được phụ đề từ video, nhưng tab Kiểm tra"
        tin "file và tab Sửa phụ đề vẫn dùng được bình thường."
        tin "Cách xử lý: chạy lại CaiDat.command sau ít phút — nó sẽ tải tiếp"
        tin "đúng bước này, những bước đã xong không phải làm lại."
    fi
fi

# =========================================================================== #
buoc "Tải deno (bộ chạy JavaScript mà yt-dlp cần để đọc YouTube)" "dưới 1 phút, ~40MB"
# =========================================================================== #

# Từ giữa 2025 YouTube bắt yt-dlp chạy một đoạn JavaScript để lấy đường video.
# Không có bộ chạy JS thì yt-dlp chỉ cảnh báo, nhưng thực tế nhiều video trả về
# lỗi 403 hoặc thiếu hẳn phần tiếng. Máy trắng không có deno lẫn node, nên tải
# deno (một file duy nhất, không cần cài) vào thư mục riêng của SrtGen; app sẽ tự
# tìm thấy ở đó và truyền cho yt-dlp bằng --js-runtimes.
if [ -x "$BIN_DIR/deno" ] && "$BIN_DIR/deno" --version >/dev/null 2>&1; then
    ok "Đã có deno trong thư mục riêng của SrtGen."
elif command -v deno >/dev/null 2>&1 || command -v node >/dev/null 2>&1; then
    ok "Máy đã có sẵn $(command -v deno >/dev/null 2>&1 && echo deno || echo node), dùng luôn."
else
    DENO_GOI="deno-x86_64-apple-darwin.zip"
    if [ "$(uname -m)" = "arm64" ]; then DENO_GOI="deno-aarch64-apple-darwin.zip"; fi
    DENO_DIA_CHI="https://github.com/denoland/deno/releases/latest/download/$DENO_GOI"
    tam_deno="$(mktemp -d "${TMPDIR:-/tmp}/srtgen-deno-XXXXXX")" || tam_deno=""
    tin "Đang tải deno…"
    if [ -n "$tam_deno" ] \
       && curl -fL --retry 3 --retry-delay 3 --max-time 600 -o "$tam_deno/deno.zip" "$DENO_DIA_CHI" 2>/dev/null \
       && unzip -o -q "$tam_deno/deno.zip" -d "$BIN_DIR" 2>/dev/null \
       && chmod +x "$BIN_DIR/deno" 2>/dev/null \
       && { xattr -d com.apple.quarantine "$BIN_DIR/deno" 2>/dev/null || true; } \
       && "$BIN_DIR/deno" --version >/dev/null 2>&1; then
        ok "Đã cài deno vào thư mục riêng của SrtGen."
    else
        hong "Chưa có deno."
        tin "Không tải được từ github.com (mạng chặn, hoặc trang đang bận)."
        tin "Thiếu deno thì yt-dlp vẫn thử tải video, nhưng một số video sẽ báo lỗi 403."
        tin "Cách xử lý: chạy lại CaiDat.command sau ít phút — nó sẽ tải tiếp đúng bước này."
    fi
    rm -rf "$tam_deno" 2>/dev/null || true
fi

# =========================================================================== #
buoc "Tải mô hình nghe (bộ não nhận dạng giọng nói)" "5-20 phút, ~1.6GB"
# =========================================================================== #

# App tìm mô hình ở ~/Library/Caches/SrtGen/models, còn ta muốn mọi thứ nằm
# trong một thư mục duy nhất để gỡ cho gọn. Nối hai chỗ đó bằng một liên kết.
chuan_bi_thu_muc_model() {
    if [ -d "$CACHE_MODEL_DIR" ] && [ ! -L "$CACHE_MODEL_DIR" ] \
       && [ -n "$(ls -A "$CACHE_MODEL_DIR" 2>/dev/null || true)" ]; then
        # Máy đã có mô hình từ lần cài trước (bộ cài cũ để ở đây). Giữ nguyên,
        # đừng bắt người dùng tải lại 1.6GB chỉ vì ta đổi chỗ để file.
        MODEL_DIR="$CACHE_MODEL_DIR"
        if [ ! -e "$APP_SUPPORT/models" ]; then
            ln -s "$CACHE_MODEL_DIR" "$APP_SUPPORT/models" 2>/dev/null || true
        fi
        return 0
    fi
    mkdir -p "$MODEL_DIR" 2>/dev/null || true
    mkdir -p "$CACHE_DIR" 2>/dev/null || true
    if [ ! -e "$CACHE_MODEL_DIR" ]; then
        ln -s "$MODEL_DIR" "$CACHE_MODEL_DIR" 2>/dev/null || true
    elif [ -d "$CACHE_MODEL_DIR" ] && [ ! -L "$CACHE_MODEL_DIR" ]; then
        rmdir "$CACHE_MODEL_DIR" 2>/dev/null && ln -s "$MODEL_DIR" "$CACHE_MODEL_DIR" 2>/dev/null || true
    fi
    return 0
}
chuan_bi_thu_muc_model

MODEL_CHON="$MODEL_MAC_DINH"

if [ "$CO_PHAN_NGHE" != "1" ]; then
    nhac "Bỏ qua bước này vì phần nhận dạng giọng nói chưa cài được."
    MODEL_CHON=""
else
    cat <<'GIAITHICHMODEL'

      Mô hình là tệp dữ liệu máy dùng để nghe và ghi lại lời thoại.
      Thời gian ghi dưới đây là cho một video 40 phút trên iMac 2017.

        1) Cân bằng   large-v3-turbo  ~1.6GB   nghe xong sau 10-15 phút
                      Khuyên dùng. Đây cũng là lựa chọn mặc định của app.

        2) Chính xác nhất  large-v3   ~3GB     nghe xong sau 30-40 phút
                      Chỉ hơn lựa chọn 1 rất ít, mà chờ lâu gấp ba.

        3) Thử nhanh  small          ~0.5GB   nghe xong sau 4-6 phút
                      Sai nhiều, chỉ hợp khi muốn xem thử tool chạy ra sao.

        4) Bỏ qua     không tải bây giờ. Lần đầu tạo phụ đề app sẽ tự tải.

      Tải một lần rồi dùng mãi. Tải dở bị đứt mạng thì lần sau tải tiếp,
      không mất phần đã tải.

GIAITHICHMODEL

    LUA_CHON="1"
    if [ "$CO_BAN_PHIM" = "1" ]; then
        printf '   %sChọn 1, 2, 3 hay 4?%s [1] ' "$DAM" "$HET"
        read -r LUA_CHON || LUA_CHON="1"
        LUA_CHON="$(printf '%s' "$LUA_CHON" | tr -d '[:space:]')"
        [ -z "$LUA_CHON" ] && LUA_CHON="1"
    fi

    case "$LUA_CHON" in
        2) MODEL_CHON="large-v3" ;;
        3) MODEL_CHON="small" ;;
        4) MODEL_CHON="" ;;
        *) MODEL_CHON="$MODEL_MAC_DINH" ;;
    esac

    if [ -z "$MODEL_CHON" ]; then
        nhac "Chưa tải mô hình. Lần đầu tạo phụ đề, app sẽ tự tải và báo tiến trình."
    else
        tin "Đang chuẩn bị mô hình “${MODEL_CHON}”. Nếu phải tải, sẽ có thanh tiến trình."
        echo
        TRANG_THAI=0
        # PYTHONIOENCODING: khi app được mở từ biểu tượng trên Desktop, macOS
        # không truyền biến ngôn ngữ vào, và mấy dòng thông báo tiếng Việt bên
        # dưới có thể làm Python nổ ngay lúc in ra. Ép UTF-8 là hết chuyện.
        HF_HUB_DISABLE_TELEMETRY=1 PYTHONIOENCODING=utf-8 \
            "$VPY" - "$MODEL_CHON" "$MODEL_DIR" <<'PYCODE' || TRANG_THAI=$?
"""Tải một mô hình Whisper về thư mục của SrtGen.

Ba việc mà bản trước không làm, và đều là việc người dùng cuối thấy được:

1. Kiểm tra toàn vẹn trước khi tin là "đã có". Một thư mục mô hình tải dở vẫn
   trông y hệt thư mục tải xong, nhưng lúc chạy sẽ nổ giữa chừng — tức là hỏng
   sau khi người dùng đã chờ 20 phút.
2. Tải nối tiếp. huggingface_hub giữ phần đã tải trong thư mục blobs, nên gọi
   lại là nó chạy tiếp từ chỗ đứt chứ không tải lại từ đầu.
3. Thử nhiều nguồn cho large-v3-turbo: tên này chỉ có ở faster-whisper đời mới,
   máy nào cài bản cũ hơn thì lui về kho chứa trên Hugging Face.
"""
import sys
from pathlib import Path

ten_model = sys.argv[1]
thu_muc = Path(sys.argv[2])
thu_muc.mkdir(parents=True, exist_ok=True)

try:
    from faster_whisper import download_model
except Exception as err:  # thiếu thư viện: bước trên đã báo rồi, đừng báo lại kiểu khó hiểu
    print(f"[thiếu thư viện] {err}")
    raise SystemExit(3)

NGUON = {
    "large-v3-turbo": [
        "large-v3-turbo",
        "deepdml/faster-whisper-large-v3-turbo-ct2",
        "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    ],
}
ung_vien = NGUON.get(ten_model, [ten_model])


def du_file(duong_dan: str) -> bool:
    """Thư mục mô hình có đủ file và file trọng số có kích thước thật hay không."""
    d = Path(duong_dan)
    if not d.is_dir() or not (d / "config.json").is_file():
        return False
    trong_so = d / "model.bin"
    if not trong_so.is_file() or trong_so.stat().st_size < 50_000_000:
        return False
    if not any((d / t).is_file() for t in ("tokenizer.json", "vocabulary.json", "vocabulary.txt")):
        return False
    return True


# Hỏi trước xem đã có sẵn trong máy chưa: rẻ hơn nhiều so với việc gọi ra mạng.
for ten in ung_vien:
    try:
        co_san = download_model(ten, cache_dir=str(thu_muc), local_files_only=True)
    except Exception:
        continue
    if du_file(co_san):
        print(f"[đã có sẵn] {co_san}")
        raise SystemExit(0)

print(f"[đang tải mô hình {ten_model}] — thanh tiến trình bên dưới là của bộ tải")
loi_cuoi = None
for ten in ung_vien:
    try:
        duong_dan = download_model(ten, cache_dir=str(thu_muc), local_files_only=False)
    except Exception as err:
        loi_cuoi = err
        continue
    if du_file(duong_dan):
        print(f"[tải xong] {duong_dan}")
        raise SystemExit(0)
    loi_cuoi = RuntimeError("tải xong nhưng thiếu file")

print(f"[lỗi] {loi_cuoi}")
raise SystemExit(4)
PYCODE
        echo
        case "$TRANG_THAI" in
            0)
                ok "Mô hình “${MODEL_CHON}” đã sẵn sàng trong máy."
                ghi_xong "6-model"
                ;;
            3)
                nhac "Chưa tải được vì thiếu thư viện nhận dạng giọng nói."
                ;;
            *)
                nhac "Tải mô hình chưa xong (thường do mạng chập chờn giữa chừng)."
                tin "Không sao: phần đã tải vẫn nằm trong máy."
                tin "Chạy lại CaiDat.command là nó tải tiếp từ đúng chỗ đứt."
                tin "Hoặc cứ mở app dùng — app cũng tự tải nốt ở lần chạy đầu."
                ;;
        esac

        if [ -n "$MODEL_CHON" ] && [ "$MODEL_CHON" != "$MODEL_MAC_DINH" ]; then
            echo
            nhac "Bạn chọn “${MODEL_CHON}”, nhưng app mặc định dùng “${MODEL_MAC_DINH}”."
            tin "Sau khi mở app, vào tab Cài đặt và chọn đúng mô hình “${MODEL_CHON}”,"
            tin "nếu không app sẽ tải thêm “${MODEL_MAC_DINH}” ở lần chạy đầu tiên."
        fi
    fi
fi

# =========================================================================== #
buoc "Tạo biểu tượng SrtGen trên màn hình nền" "5 giây"
# =========================================================================== #

# Quyền chạy và nhãn quarantine của các file .command đã được mở ngay sau câu
# "Bắt đầu?" ở đầu file, để cài đứt giữa chừng cũng không làm người dùng kẹt.
# Làm lại một lần nữa ở đây cho chắc: bước 5 và bước 6 có tải file mới về thư
# mục này, mà file vừa tải từ Internet thì lại mang nhãn quarantine mới.
chmod +x "$SCRIPT_DIR"/*.command 2>/dev/null || true
xattr -r -d com.apple.quarantine "$SCRIPT_DIR" 2>/dev/null || true

THU_MUC_TAM=""
[ -f "$KHOIDONG" ] && THU_MUC_TAM="$(mktemp -d "${TMPDIR:-/tmp}/srtgen-app-XXXXXX" 2>/dev/null || true)"

if [ ! -f "$KHOIDONG" ]; then
    nhac "Không thấy file KhoiDong.command nên bỏ qua bước tạo biểu tượng."
elif [ -z "$THU_MUC_TAM" ]; then
    nhac "Không tạo được thư mục tạm nên bỏ qua bước tạo biểu tượng."
    tin "Bạn mở app bằng cách bấm đúp vào file KhoiDong.command."
else
    # Chèn đường dẫn vào mã AppleScript: phải rào hai ký tự \ và " lại, nếu không
    # một thư mục có dấu nháy kép trong tên sẽ làm hỏng cả đoạn mã.
    DUONG_DAN_AS="${KHOIDONG//\\/\\\\}"
    DUONG_DAN_AS="${DUONG_DAN_AS//\"/\\\"}"

    NGUON_AS="$THU_MUC_TAM/SrtGen.applescript"
    cat > "$NGUON_AS" <<APPLESCRIPT
on run
	set duongDan to "$DUONG_DAN_AS"
	tell application "Terminal"
		activate
		do script "clear; " & quoted form of duongDan
	end tell
end run
APPLESCRIPT

    rm -rf "$DESKTOP_APP"
    if osacompile -o "$DESKTOP_APP" "$NGUON_AS" 2>/dev/null; then
        ok "Đã tạo biểu tượng SrtGen trên màn hình nền (Desktop)."
        tin "Lần đầu bấm vào nó, máy sẽ hỏi “SrtGen muốn điều khiển Terminal”."
        tin "Hãy bấm OK — không có bước này thì biểu tượng không mở được app."
        ghi_xong "7-bieu-tuong"
    else
        nhac "Không tạo được biểu tượng trên màn hình nền."
        tin "Không sao: bạn mở app bằng cách bấm đúp vào file KhoiDong.command"
        tin "trong thư mục installer."
    fi
    rm -rf "$THU_MUC_TAM"
fi

# =========================================================================== #
buoc "Kiểm tra máy lần cuối (srtgen doctor)" "30 giây"
# =========================================================================== #

# Đây là câu trả lời cho câu hỏi duy nhất mà người dùng thật sự quan tâm:
# "cài xong rồi thì máy có chạy được không?". Chạy thẳng lệnh kiểm tra của app,
# in nguyên kết quả ra, không tóm tắt lại — người hỗ trợ cần thấy bản gốc.
DOCTOR_MA=0
if [ -x "$SRTGEN" ]; then
    echo
    PATH="$BIN_DIR:$VENV_DIR/bin:$PATH" "$SRTGEN" doctor || DOCTOR_MA=$?
    echo
else
    DOCTOR_MA=1
    hong "Không chạy được lệnh kiểm tra."
fi

if [ "$DOCTOR_MA" -eq 0 ]; then
    ok "Máy đạt hết các mục kiểm tra."
    ghi_xong "8-kiem-tra"
else
    nhac "Có mục chưa đạt ở bảng trên."
    tin "Mỗi dòng chưa đạt đều kèm sẵn cách xử lý ngay bên dưới nó."
    tin "Phần lớn trường hợp chỉ cần chạy lại CaiDat.command là xong."
fi

# Hồ sơ cài đặt: để GoCaiDat.command và người hỗ trợ biết lần cài này đã làm gì.
mkdir -p "$APP_SUPPORT" 2>/dev/null || true
cat > "$RECEIPT" <<RECEIPTJSON
{
  "cai_luc": "$(date '+%Y-%m-%d %H:%M:%S')",
  "thu_muc_ma_nguon": "$PROJECT_DIR",
  "cach_cai": "uv",
  "uv": "$UV",
  "python": "$("$VPY" -V 2>&1 || echo '?')",
  "kien_truc": "$KIEN_TRUC",
  "macos": "$MACOS_VER",
  "ffmpeg": "${FFMPEG_DUNG:-}",
  "co_phan_nghe": $( [ "$CO_PHAN_NGHE" = "1" ] && echo true || echo false ),
  "mo_hinh_da_tai": "${MODEL_CHON:-}",
  "thu_muc_mo_hinh": "$MODEL_DIR",
  "doctor": $DOCTOR_MA,
  "nhat_ky": "$LOG_FILE"
}
RECEIPTJSON

# =========================================================================== #
# Xong
# =========================================================================== #

printf '\n%s\n' "$VACH"
if [ "$DOCTOR_MA" -eq 0 ]; then
    printf '%s\n' "${XANH}${DAM}  ✓  CÀI ĐẶT XONG${HET}"
else
    printf '%s\n' "${VANG}${DAM}  !  CÀI XONG NHƯNG CÒN MỤC CHƯA ĐẠT${HET}"
fi
printf '%s\n' "$VACH"

cat <<KETQUA

  ${DAM}Cách mở app${HET}, chọn một trong hai:

     1. Bấm đúp vào biểu tượng ${DAM}SrtGen${HET} trên màn hình nền.
     2. Hoặc bấm đúp vào file ${DAM}KhoiDong.command${HET} trong thư mục installer.

  App sẽ tự mở trình duyệt. Cửa sổ chữ đen hiện ra là bình thường —
  đó là "động cơ" của app, đừng đóng nó trong lúc đang dùng.

  ${DAM}Nếu macOS báo "không mở được vì không rõ nhà phát triển"${HET}
     Bấm chuột phải vào file, chọn ${DAM}Open${HET}, rồi bấm ${DAM}Open${HET} lần nữa
     trong hộp thoại. Chỉ phải làm một lần cho mỗi file.
     Không thấy nút đó thì vào  Cài đặt hệ thống > Quyền riêng tư & Bảo mật,
     kéo xuống cuối, bấm ${DAM}Open Anyway${HET}.

  ${DAM}Toàn bộ SrtGen nằm trong một thư mục duy nhất${HET}
     $APP_SUPPORT
     Gỡ đi: bấm đúp file GoCaiDat.command. Máy của bạn không bị đụng gì khác.

  Hướng dẫn sử dụng chi tiết:
     $PROJECT_DIR/HUONG-DAN.md

KETQUA

if [ "$DOCTOR_MA" -ne 0 ]; then
    cat <<CANHBAOCUOI
  Còn mục chưa đạt ở bảng kiểm tra. App vẫn mở được, nhưng có thể thiếu chức
  năng. Chạy lại CaiDat.command thường là đủ. Nếu vẫn vậy, gửi file nhật ký
  này cho người hỗ trợ:
     $LOG_FILE

CANHBAOCUOI
fi

if [ "$DOCTOR_MA" -eq 0 ] && [ -f "$KHOIDONG" ] && hoi "Mở SrtGen ngay bây giờ?"; then
    printf '\n   %s\n' "Đang mở SrtGen trong một cửa sổ mới…"
    # Mở cửa sổ Terminal riêng thay vì chạy đè lên cửa sổ này: người dùng còn đọc
    # được phần tổng kết ở trên, và nhật ký cài đặt không bị lẫn nhật ký chạy app.
    open -a Terminal "$KHOIDONG" 2>/dev/null || nhac "Không mở được tự động — bạn bấm đúp vào KhoiDong.command giúp."
fi

exit 0
