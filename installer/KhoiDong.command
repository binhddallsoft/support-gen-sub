#!/bin/bash
# ===========================================================================
# KhoiDong.command — mở SrtGen
#
# Bấm đúp vào file này để dùng app. Một cửa sổ chữ đen sẽ hiện ra và trình
# duyệt sẽ tự mở. Cửa sổ chữ đen đó chính là "động cơ" của app: đừng đóng nó
# trong lúc đang dùng, đóng lại là app tắt.
#
# File này KHÔNG cài gì cả. Nếu máy chưa cài, nó chỉ nhắc chạy CaiDat.command.
# ===========================================================================

# Cố ý KHÔNG dùng "set -e": mỗi lỗi ở đây đều phải được dịch thành một câu
# tiếng Việt có ích, chứ không phải làm cửa sổ đóng sập không nói gì.
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"

APP_SUPPORT="$HOME/Library/Application Support/SrtGen"
BIN_DIR="$APP_SUPPORT/bin"
VENV_DIR="$APP_SUPPORT/venv"
VPY="$VENV_DIR/bin/python"
LOG_DIR="$APP_SUPPORT/logs"
URL_FILE="$APP_SUPPORT/dang-chay.url"

CHO_TOI_DA=180          # số lần chờ, mỗi lần nửa giây => tối đa 90 giây
CO_MAU=0
[ -t 1 ] && CO_MAU=1

if [ "$CO_MAU" = "1" ]; then
    DAM=$'\033[1m'; XANH=$'\033[32m'; DO=$'\033[31m'; VANG=$'\033[33m'; XAM=$'\033[90m'; HET=$'\033[0m'
else
    DAM=""; XANH=""; DO=""; VANG=""; XAM=""; HET=""
fi

ok()   { printf '   %s✓%s  %s\n' "$XANH" "$HET" "$1"; }
nhac() { printf '   %s•%s  %s\n' "$VANG" "$HET" "$1"; }
tin()  { printf '      %s\n' "$1"; }

# Báo lỗi rồi giữ cửa sổ lại, nếu không người dùng chỉ thấy màn hình nháy một cái.
dung_lai() {
    printf '\n%s\n' "${DO}${DAM}  ✗  KHÔNG MỞ ĐƯỢC SRTGEN${HET}"
    printf '%s\n\n' "------------------------------------------------------------------"
    for dong in "$@"; do
        printf '   %s\n' "$dong"
    done
    printf '\n'
    if [ -t 0 ]; then
        printf '%s' "Nhấn phím bất kỳ để đóng cửa sổ này… "
        read -r -n 1 -s _phim || true
        printf '\n'
    fi
    exit 1
}

clear 2>/dev/null || true
printf '\n%s\n\n' "${DAM}SrtGen — đang khởi động…${HET}"

# --------------------------------------------------------------------------- #
# 1. Máy đã cài chưa?
# --------------------------------------------------------------------------- #
if [ ! -x "$VPY" ]; then
    dung_lai \
        "Máy này chưa cài SrtGen." \
        "" \
        "Hãy bấm đúp vào file ${DAM}CaiDat.command${HET} nằm cùng thư mục với file này," \
        "chờ cài xong (khoảng 15-30 phút), rồi mở lại." \
        "" \
        "Thư mục cần tìm:" \
        "$SCRIPT_DIR"
fi

if ! "$VPY" -c 'pass' 2>/dev/null; then
    dung_lai \
        "Phần cài đặt của SrtGen bị hỏng." \
        "Thường là do máy vừa cập nhật hệ điều hành, làm đứt liên kết cũ." \
        "" \
        "Cách sửa: bấm đúp vào file ${DAM}CaiDat.command${HET} để cài lại." \
        "Việc cài lại giữ nguyên mô hình đã tải và các gói đã tải về," \
        "nên nhanh hơn lần đầu rất nhiều."
fi

# --------------------------------------------------------------------------- #
# 2. Đang chạy sẵn rồi thì chỉ mở lại trình duyệt
# --------------------------------------------------------------------------- #
if [ -f "$URL_FILE" ]; then
    URL_CU="$(cat "$URL_FILE" 2>/dev/null || true)"
    if [ -n "$URL_CU" ] && curl -sf --max-time 3 -o /dev/null "$URL_CU" 2>/dev/null; then
        ok "SrtGen đang chạy sẵn rồi, mở lại trang cho bạn."
        tin "$URL_CU"
        open "$URL_CU" 2>/dev/null || true
        printf '\n   %s\n\n' "${XAM}Cửa sổ này đóng được. App vẫn chạy ở cửa sổ mở trước đó.${HET}"
        exit 0
    fi
    rm -f "$URL_FILE"
fi

# --------------------------------------------------------------------------- #
# 3. Chuẩn bị môi trường chạy
# --------------------------------------------------------------------------- #
# Thứ tự trong PATH là có chủ ý:
#   venv/bin  — nơi có yt-dlp và liên kết tới ffmpeg mà bộ cài đã chuẩn bị,
#               cũng là nơi nút "Cập nhật yt-dlp" trong app ghi vào
#   bin       — ffmpeg/ffprobe của riêng SrtGen, và lối tắt srtgen
#   phần còn lại — bản ffmpeg dùng chung của máy, nếu máy có sẵn
# Khi app được mở từ biểu tượng trên Desktop, macOS chỉ đưa cho một PATH tối
# thiểu; không có dòng này thì ffmpeg "biến mất" dù vẫn nằm nguyên trên đĩa.
export PATH="$VENV_DIR/bin:$BIN_DIR:/usr/local/bin:/opt/homebrew/bin:/opt/local/bin:$PATH"

# Chạy thẳng từ thư mục mã nguồn. Nhờ dòng này, di chuyển thư mục SrtGen sang
# chỗ khác vẫn chạy được mà không phải cài lại.
export PYTHONPATH="$PROJECT_DIR${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUNBUFFERED=1
# Mở app từ biểu tượng trên Desktop thì macOS không truyền biến ngôn ngữ vào.
# Thiếu dòng này, một dòng chữ tiếng Việt trong nhật ký đủ làm app tắt ngang.
export PYTHONIOENCODING=utf-8

# Nút "Cập nhật yt-dlp" trong app, và mục "uv" của lệnh kiểm tra máy, cần biết
# đường tới uv và tới môi trường riêng. Truyền qua biến môi trường để chúng khỏi
# phải đi đoán: mở app từ biểu tượng Desktop thì macOS chỉ đưa một PATH tối
# thiểu, uv nằm nguyên trên đĩa vẫn coi như "không có".
# Nơi đọc SRTGEN_UV: hàm _find_uv() trong srtgen/cli.py.
export SRTGEN_UV="$BIN_DIR/uv"
export SRTGEN_VENV="$VENV_DIR"

# Trình duyệt do chính file này mở, sau khi đã chắc chắn máy chủ lên rồi.
# Nếu app cũng tự mở trình duyệt thì người dùng sẽ thấy hai thẻ giống nhau.
# Nơi đọc: hàm _no_browser_env() trong srtgen/cli.py.
export SRTGEN_NO_BROWSER=1

mkdir -p "$LOG_DIR" 2>/dev/null || true
LOG_FILE="$LOG_DIR/khoidong-$(date '+%Y%m%d-%H%M%S').log"
: > "$LOG_FILE" 2>/dev/null || LOG_FILE="$(mktemp -t srtgen-log)"

# --------------------------------------------------------------------------- #
# 4. Chạy máy chủ ở nền, vừa hiện ra màn hình vừa ghi vào nhật ký
# --------------------------------------------------------------------------- #
printf '   %s\n\n' "${XAM}Nhật ký lần chạy này: $LOG_FILE${HET}"

# Đưa cả cờ --no-browser lẫn biến môi trường ở trên: hai đường độc lập cho cùng
# một ý. Bản cài cũ không hiểu cờ này vẫn còn biến môi trường, và ngược lại.
"$VPY" -m srtgen.cli ui --no-browser > "$LOG_FILE" 2>&1 &
MAY_CHU=$!

don_dep() {
    rm -f "$URL_FILE"
    if kill -0 "$MAY_CHU" 2>/dev/null; then
        kill "$MAY_CHU" 2>/dev/null || true
        wait "$MAY_CHU" 2>/dev/null || true
    fi
}

tam_biet() {
    printf '\n\n   %s\n\n' "${XAM}Đã tắt SrtGen. Cửa sổ này đóng được rồi.${HET}"
    don_dep
    exit 0
}
trap tam_biet INT TERM
trap don_dep EXIT

# --------------------------------------------------------------------------- #
# 5. Chờ máy chủ báo địa chỉ, rồi mở trình duyệt
# --------------------------------------------------------------------------- #
DIA_CHI=""
LAN=0
while [ "$LAN" -lt "$CHO_TOI_DA" ]; do
    DIA_CHI="$(grep -Eo 'http://[0-9A-Za-z._-]+:[0-9]+' "$LOG_FILE" 2>/dev/null | head -n 1 || true)"
    [ -n "$DIA_CHI" ] && break
    if ! kill -0 "$MAY_CHU" 2>/dev/null; then
        break
    fi
    sleep 0.5
    LAN=$((LAN + 1))
done

if [ -z "$DIA_CHI" ]; then
    # Máy chủ chết ngay, hoặc chạy mãi không lên. Đưa ra đúng mấy dòng cuối của
    # nhật ký: đó là thứ duy nhất nói được vì sao.
    printf '\n%s\n' "${DO}${DAM}  ✗  APP KHÔNG LÊN ĐƯỢC${HET}"
    printf '%s\n\n' "------------------------------------------------------------------"
    printf '   %s\n\n' "Máy báo như sau (mấy dòng cuối cùng):"
    tail -n 20 "$LOG_FILE" 2>/dev/null | sed 's/^/      /'
    printf '\n   %s\n' "Nên thử theo thứ tự:"
    printf '   %s\n' "1. Đóng cửa sổ này rồi bấm đúp KhoiDong.command lần nữa."
    printf '   %s\n' "2. Nếu vẫn vậy, chạy lại CaiDat.command để cài bù phần còn thiếu."
    printf '   %s\n' "3. Nếu vẫn vậy, gửi file nhật ký này cho người hỗ trợ:"
    printf '      %s\n\n' "$LOG_FILE"
    if [ -t 0 ]; then
        printf '%s' "Nhấn phím bất kỳ để đóng cửa sổ này… "
        read -r -n 1 -s _phim || true
        printf '\n'
    fi
    exit 1
fi

# 0.0.0.0 là cách máy chủ nói "nghe trên mọi đường", nhưng trình duyệt không mở
# được địa chỉ đó — phải đổi về địa chỉ của chính máy này.
DIA_CHI="${DIA_CHI/0.0.0.0/127.0.0.1}"
printf '%s' "$DIA_CHI" > "$URL_FILE" 2>/dev/null || true

ok "SrtGen đã sẵn sàng."
tin "$DIA_CHI"
open "$DIA_CHI" 2>/dev/null || nhac "Không tự mở được trình duyệt — hãy tự gõ địa chỉ ở trên vào Safari/Chrome."

cat <<HUONGDAN

   ${DAM}Cửa sổ này phải mở trong suốt thời gian dùng app.${HET}
   Xong việc thì đóng cửa sổ, hoặc nhấn Control + C để tắt app.

   ${XAM}Từ đây trở xuống là nhật ký hoạt động. Không cần đọc.${HET}

HUONGDAN

# --------------------------------------------------------------------------- #
# 6. Hiện nhật ký ra màn hình cho tới khi người dùng tắt
# --------------------------------------------------------------------------- #
tail -f "$LOG_FILE" 2>/dev/null &
THEO_DOI=$!

wait "$MAY_CHU" 2>/dev/null
KET_QUA=$?

kill "$THEO_DOI" 2>/dev/null || true
rm -f "$URL_FILE"

if [ "$KET_QUA" -ne 0 ] && [ "$KET_QUA" -ne 130 ] && [ "$KET_QUA" -ne 143 ]; then
    printf '\n   %s\n' "${DO}App dừng đột ngột.${HET} Mấy dòng cuối của nhật ký:"
    tail -n 15 "$LOG_FILE" 2>/dev/null | sed 's/^/      /'
    printf '\n   %s\n' "Nhật ký đầy đủ: $LOG_FILE"
    if [ -t 0 ]; then
        printf '\n%s' "Nhấn phím bất kỳ để đóng cửa sổ này… "
        read -r -n 1 -s _phim || true
        printf '\n'
    fi
else
    printf '\n   %s\n\n' "${XAM}Đã tắt SrtGen. Cửa sổ này đóng được rồi.${HET}"
fi

exit 0
