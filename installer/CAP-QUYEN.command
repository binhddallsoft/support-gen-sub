#!/bin/bash
# ===========================================================================
# CAP-QUYEN.command — mở khoá cho các file .command của SrtGen
#
# CHỈ CẦN DÙNG KHI: bấm đúp vào CaiDat.command hoặc KhoiDong.command mà máy
# báo "The file couldn't be opened", hoặc không có gì xảy ra cả.
#
# ---------------------------------------------------------------------------
# CHUYỆN GÌ ĐÃ XẢY RA
#
# Trên máy Mac, một file chỉ chạy được khi nó mang "quyền chạy". Quyền này hay
# bị rơi mất trên đường đi: gửi qua email, nén rồi giải nén bằng phần mềm lạ,
# tải về từ kho mã nguồn, hoặc chép qua ổ USB định dạng Windows. File vẫn còn
# nguyên nội dung, chỉ là máy không cho bấm đúp nữa.
#
# File này gắn lại quyền đó cho tất cả các file .command nằm cùng thư mục, và
# gỡ luôn nhãn "tải từ Internet" mà macOS dán lên. Nó KHÔNG cài gì, KHÔNG xoá
# gì, KHÔNG hỏi mật khẩu, và chạy lại bao nhiêu lần cũng không sao.
#
# ---------------------------------------------------------------------------
# CÁCH CHẠY FILE NÀY
#
# Cách 1 — bấm chuột phải vào file, chọn Open, rồi bấm Open lần nữa.
#
# Cách 2 — nếu cách 1 cũng không được (chính file này cũng mất quyền chạy):
#   1. Mở ứng dụng Terminal (bấm ⌘ + phím cách, gõ Terminal, nhấn Enter).
#   2. Gõ:  bash    <- chữ bash rồi MỘT DẤU CÁCH, chưa nhấn Enter.
#   3. Kéo chính file CAP-QUYEN.command này thả vào cửa sổ Terminal.
#   4. Nhấn Enter.
#
# Cách 2 chạy được kể cả khi file không có quyền chạy — đó là lý do nó tồn tại.
# ===========================================================================

# Cố ý KHÔNG dùng "set -e": file này là phao cứu sinh, nó phải nói cho xong câu
# rồi mới được dừng, chứ không được đóng sập cửa sổ giữa chừng.
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

CO_MAU=0
[ -t 1 ] && CO_MAU=1
if [ "$CO_MAU" = "1" ]; then
    DAM=$'\033[1m'; XANH=$'\033[32m'; DO=$'\033[31m'; VANG=$'\033[33m'; XAM=$'\033[90m'; HET=$'\033[0m'
else
    DAM=""; XANH=""; DO=""; VANG=""; XAM=""; HET=""
fi

VACH="------------------------------------------------------------------"

ok()   { printf '   %s✓%s  %s\n' "$XANH" "$HET" "$1"; }
hong() { printf '   %s✗%s  %s\n' "$DO" "$HET" "$1"; }
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

clear 2>/dev/null || true
printf '\n%s\n' "$VACH"
printf '%s\n' "${DAM}MỞ KHOÁ CÁC FILE CỦA SRTGEN${HET}"
printf '%s\n\n' "$VACH"
printf '   %s\n' "Thư mục đang xử lý:"
printf '   %s%s%s\n\n' "$XAM" "$SCRIPT_DIR" "$HET"

# --------------------------------------------------------------------------- #
# 1. Gắn lại quyền chạy cho từng file .command
# --------------------------------------------------------------------------- #
# Duyệt bằng vòng lặp thay vì một lệnh chmod cho cả nhóm, để nói được đúng tên
# file nào đã xong và file nào hỏng. Người dùng cuối cần biết "file của tôi đã
# dùng được chưa", không cần biết lệnh nào vừa chạy.
SO_FILE=0
SO_HONG=0
# File SrtGen.command nằm ở thư mục CHA (ngoài cùng thư mục tải về), và đó là
# file người dùng bấm đầu tiên — nên phải gắn quyền cho nó trước tiên.
for duong_dan in "$SCRIPT_DIR/../SrtGen.command" "$SCRIPT_DIR"/*.command; do
    # Thư mục không có file .command nào thì bash trả về nguyên chuỗi mẫu.
    [ -e "$duong_dan" ] || continue
    SO_FILE=$((SO_FILE + 1))
    ten="$(basename "$duong_dan")"
    if chmod +x "$duong_dan" 2>/dev/null && [ -x "$duong_dan" ]; then
        ok "$ten — đã dùng được"
    else
        hong "$ten — không gắn được quyền chạy"
        SO_HONG=$((SO_HONG + 1))
    fi
done

if [ "$SO_FILE" = "0" ]; then
    printf '\n'
    hong "Không thấy file .command nào trong thư mục này."
    tin "Hãy chắc chắn file CAP-QUYEN.command đang nằm CÙNG thư mục với"
    tin "CaiDat.command và KhoiDong.command (thư mục tên là installer)."
    exit 1
fi

# --------------------------------------------------------------------------- #
# 2. Gỡ nhãn "tải từ Internet" của Gatekeeper
# --------------------------------------------------------------------------- #
# Đây là nguyên nhân của thông báo "không mở được vì không rõ nhà phát triển".
# Gỡ được thì lần bấm đúp đầu tiên đã chạy luôn; không gỡ được cũng không chặn
# ai cả, vì vẫn còn đường bấm chuột phải rồi chọn Open.
printf '\n'
[ -e "$SCRIPT_DIR/../SrtGen.command" ] && xattr -d com.apple.quarantine "$SCRIPT_DIR/../SrtGen.command" 2>/dev/null
if xattr -r -d com.apple.quarantine "$SCRIPT_DIR" 2>/dev/null; then
    ok "Đã gỡ nhãn “tải từ Internet” của macOS."
else
    nhac "Không gỡ được nhãn “tải từ Internet” (không sao)."
    tin "Nếu máy còn hỏi, hãy bấm chuột phải vào file → chọn Open → Open."
fi

# --------------------------------------------------------------------------- #
# 3. Nói rõ việc tiếp theo phải làm
# --------------------------------------------------------------------------- #
printf '\n%s\n' "$VACH"
if [ "$SO_HONG" != "0" ]; then
    printf '%s\n' "${DO}${DAM}  ✗  CÒN $SO_HONG FILE CHƯA MỞ ĐƯỢC${HET}"
    printf '%s\n\n' "$VACH"
    printf '   %s\n' "Thường là vì thư mục SrtGen đang nằm ở nơi máy không cho ghi:"
    printf '   %s\n' "ổ USB định dạng Windows, ổ đĩa mạng, hoặc ảnh đĩa .dmg chưa chép ra."
    printf '\n   %s\n' "${DAM}Cách xử lý:${HET} chép cả thư mục SrtGen vào thư mục Documents"
    printf '   %s\n' "hoặc Desktop của máy, rồi chạy lại file này ở chỗ mới."
    exit 1
fi

printf '%s\n' "${XANH}${DAM}  ✓  XONG. CÁC FILE ĐÃ DÙNG ĐƯỢC${HET}"
printf '%s\n\n' "$VACH"

if [ -x "$SCRIPT_DIR/CaiDat.command" ]; then
    printf '   %s\n' "${DAM}Việc tiếp theo:${HET}"
    printf '   %s\n' "• Máy chưa cài SrtGen  → bấm đúp vào  CaiDat.command"
    printf '   %s\n' "• Máy đã cài rồi       → bấm đúp vào  KhoiDong.command"
    printf '\n   %s\n' "${XAM}Không phải chạy lại file CAP-QUYEN.command này nữa.${HET}"
fi

exit 0
