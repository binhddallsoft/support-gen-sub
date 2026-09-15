#!/bin/bash
# ===========================================================================
# SrtGen.command — FILE DUY NHẤT người dùng cần bấm
#
#   Lần đầu tiên : chép chương trình vào một chỗ cố định trong máy, rồi cài.
#   Những lần sau: mở app.
#   Có bản mới   : tải bản mới về, bấm file này trong thư mục mới — nó tự cập
#                  nhật rồi mở app.
#
# Không cần mật khẩu quản trị. Không đụng tới phụ đề, cài đặt hay khoá API đã có.
# ---------------------------------------------------------------------------
# VÌ SAO PHẢI CHÉP CHƯƠNG TRÌNH RA CHỖ KHÁC
#
# Bộ cài (installer/CaiDat.command) đăng ký SrtGen theo kiểu "liên kết thẳng tới
# thư mục mã nguồn". Nếu chạy nó ngay trong thư mục vừa tải về (thường là
# Downloads), app sẽ sống nhờ vào thư mục đó: người dùng dọn thư mục Tải về là
# app hỏng, và biểu tượng trên màn hình nền trỏ vào một chỗ không còn tồn tại.
#
# File này chép phần chương trình vào
#     ~/Library/Application Support/SrtGen/app
# rồi mới gọi bộ cài TỪ CHỖ ĐÓ. Sau khi cài xong, thư mục tải về xoá lúc nào
# cũng được.
#
# VÌ SAO SO "DẤU VÂN TAY" CHỨ KHÔNG SO SỐ PHIÊN BẢN
#
# Số phiên bản chỉ đổi khi có người nhớ đổi. Dấu vân tay tính từ chính nội dung
# các file chương trình, nên bản tải về khác bản đang cài dù chỉ một chữ thì cũng
# biết mà cập nhật, và giống hệt thì mở app ngay, không làm gì thừa.
# ===========================================================================

# Cố ý KHÔNG dùng "set -e": mỗi lỗi phải thành một câu tiếng Việt có cách xử lý,
# không phải một cửa sổ đóng sập. Chỉ dùng cú pháp của bash 3.2 (bản có sẵn
# trên macOS), không dùng tính năng của bash mới.
set -uo pipefail

NGUON="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
APP_SUPPORT="$HOME/Library/Application Support/SrtGen"
DICH="$APP_SUPPORT/app"
VPY="$APP_SUPPORT/venv/bin/python"
MOC_DIR="$APP_SUPPORT/.tien-do"

# Những thứ thật sự cần để chạy app. Thư mục tests/, corpus/, docs/, tools/ là
# đồ của người phát triển, không cần chép vào máy người dùng.
CAN_CHEP="srtgen installer pyproject.toml README.md HUONG-DAN.md SrtGen.command"

if [ -t 1 ]; then
    DAM=$'\033[1m'; XANH=$'\033[32m'; DO=$'\033[31m'; VANG=$'\033[33m'
    XAM=$'\033[90m'; HET=$'\033[0m'
else
    DAM=""; XANH=""; DO=""; VANG=""; XAM=""; HET=""
fi
VACH="------------------------------------------------------------------"

ok()   { printf '   %s✓%s  %s\n' "$XANH" "$HET" "$1"; }
nhac() { printf '   %s•%s  %s\n' "$VANG" "$HET" "$1"; }
tin()  { printf '      %s\n' "$1"; }

# Báo lỗi rồi GIỮ cửa sổ lại: không giữ thì người dùng chỉ thấy màn hình nháy.
dung_lai() {
    printf '\n%s\n' "${DO}${DAM}  ✗  CHƯA LÀM TIẾP ĐƯỢC${HET}"
    printf '%s\n\n' "$VACH"
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

# Dấu vân tay của phần chương trình trong một thư mục. Chỉ tính các loại file
# thật sự là chương trình, và bỏ qua những thứ máy tự sinh ra lúc chạy
# (__pycache__, .DS_Store), nếu không lần mở nào cũng tưởng là có bản mới.
van_tay() {
    (
        cd -- "$1" 2>/dev/null || exit 0
        find srtgen installer pyproject.toml SrtGen.command -type f \
            \( -name '*.py' -o -name '*.command' -o -name '*.txt' -o -name '*.yaml' \
               -o -name '*.html' -o -name '*.js' -o -name '*.css' -o -name '*.toml' \) \
            ! -path '*/__pycache__/*' 2>/dev/null \
          | LC_ALL=C sort \
          | while IFS= read -r f; do shasum "$f"; done \
          | shasum | cut -d' ' -f1
    )
}

# "Đã cài" nghĩa là môi trường riêng CHẠY ĐƯỢC và có đủ các thư viện chính, chứ
# không chỉ là có file python. Chỉ kiểm "có file" thì một lần cài đứt giữa bước
# thư viện sẽ bị coi là xong: bấm file này chỉ mở app, app hỏng, và lời khuyên
# "bấm lại SrtGen.command để sửa" trong tab Hướng dẫn thành nói sai.
# `find_spec` chỉ dò chỗ của thư viện, không nạp nó, nên mất chưa tới một giây
# kể cả trên iMac 2017.
da_cai() {
    [ -x "$VPY" ] || return 1
    "$VPY" -c 'import importlib.util as u, sys
sys.exit(0 if all(u.find_spec(m) for m in ("srtgen", "fastapi", "uvicorn", "jieba", "faster_whisper", "yt_dlp")) else 1)' 2>/dev/null
}

clear 2>/dev/null || true
printf '\n%s\n\n' "${DAM}SrtGen${HET}"

# --------------------------------------------------------------------------- #
# 0. Có đúng là máy Mac, và thư mục tải về có đủ file không
# --------------------------------------------------------------------------- #
if [ "$(uname -s)" != "Darwin" ]; then
    dung_lai "SrtGen chỉ cài được trên máy Mac."
fi

for f in srtgen/cli.py installer/CaiDat.command installer/KhoiDong.command pyproject.toml; do
    if [ ! -f "$NGUON/$f" ]; then
        dung_lai \
            "Thư mục này thiếu file: $f" \
            "" \
            "Có vẻ bản tải về chưa đầy đủ, hoặc file này đã bị kéo ra khỏi thư mục của nó." \
            "Hãy tải lại trọn bộ, giải nén, rồi bấm lại file SrtGen.command nằm ở" \
            "ngoài cùng thư mục vừa giải nén."
    fi
done

# --------------------------------------------------------------------------- #
# 1. Đang chạy từ chính bản đã chép vào máy (ví dụ bấm lại từ Finder)
# --------------------------------------------------------------------------- #
if [ "$NGUON" = "$DICH" ]; then
    if da_cai; then
        exec bash "$DICH/installer/KhoiDong.command"
    fi
    exec bash "$DICH/installer/CaiDat.command"
fi

# --------------------------------------------------------------------------- #
# 2. Đã cài, và bản tải về giống hệt bản đang cài: mở app luôn
# --------------------------------------------------------------------------- #
VT_NGUON="$(van_tay "$NGUON")"
VT_DICH=""
[ -d "$DICH" ] && VT_DICH="$(van_tay "$DICH")"

if da_cai && [ -n "$VT_DICH" ] && [ "$VT_NGUON" = "$VT_DICH" ]; then
    exec bash "$DICH/installer/KhoiDong.command"
fi

# --------------------------------------------------------------------------- #
# 3. Lần đầu, hoặc có bản mới: chép chương trình vào chỗ cố định
# --------------------------------------------------------------------------- #
CAP_NHAT=0
if da_cai; then
    CAP_NHAT=1
    printf '%s\n' "${DAM}Có bản SrtGen mới — đang cập nhật.${HET}"
    tin "Phụ đề, cài đặt và khoá API của bạn giữ nguyên, không bị đụng tới."
elif [ -e "$VPY" ]; then
    # Có dấu vết cài rồi mà kiểm tra không đạt: cài dở, hoặc hỏng sau khi máy
    # cập nhật. Chạy lại bộ cài là sửa được; nó bỏ qua phần còn tốt.
    CAP_NHAT=1
    printf '%s\n' "${DAM}Phần cài SrtGen trên máy này chưa đủ — đang sửa lại.${HET}"
    tin "Phụ đề, cài đặt và khoá API của bạn giữ nguyên, không bị đụng tới."
else
    printf '%s\n' "${DAM}Lần đầu dùng SrtGen trên máy này — chuẩn bị cài đặt.${HET}"
fi
printf '%s\n' "$VACH"

TAM="$APP_SUPPORT/app.moi"
CU="$APP_SUPPORT/app.cu"

mkdir -p "$APP_SUPPORT" 2>/dev/null || dung_lai \
    "Không tạo được thư mục:" "$APP_SUPPORT" "" \
    "Ổ đĩa có thể đã đầy. Hãy dọn bớt vài GB rồi bấm lại file này."

rm -rf "$TAM" 2>/dev/null || true
mkdir -p "$TAM" 2>/dev/null || dung_lai "Không tạo được thư mục tạm: $TAM"

for muc in $CAN_CHEP; do
    [ -e "$NGUON/$muc" ] || continue
    # ditto giữ nguyên quyền chạy của file và chép được cả thư mục lẫn file lẻ.
    if ! ditto "$NGUON/$muc" "$TAM/$muc" 2>/dev/null; then
        rm -rf "$TAM" 2>/dev/null || true
        dung_lai \
            "Không chép được “$muc” vào máy." \
            "" \
            "Ổ đĩa có thể đã đầy, hoặc thư mục tải về đang nằm ở nơi không đọc được" \
            "(ổ USB, ổ mạng). Hãy chép cả thư mục vào Desktop rồi bấm lại file này."
    fi
done

# File tải từ Internet mang nhãn "quarantine": macOS sẽ chặn mọi file .command
# bên trong. Gỡ nhãn và gắn lại quyền chạy ngay trên bản chép, để từ giờ bấm đúp
# biểu tượng là chạy, không bị hỏi lại lần nào nữa.
xattr -r -d com.apple.quarantine "$TAM" 2>/dev/null || true
chmod +x "$TAM"/installer/*.command "$TAM/SrtGen.command" 2>/dev/null || true

# Đổi bản cũ sang bản mới bằng hai lần đổi tên, không phải chép đè: đứt giữa
# chừng thì bản cũ vẫn còn nguyên để trả lại.
rm -rf "$CU" 2>/dev/null || true
if [ -d "$DICH" ]; then
    mv "$DICH" "$CU" 2>/dev/null || dung_lai \
        "Không thay được bản cũ bằng bản mới." \
        "Có thể SrtGen đang mở. Hãy đóng cửa sổ SrtGen rồi bấm lại file này."
fi
if ! mv "$TAM" "$DICH" 2>/dev/null; then
    [ -d "$CU" ] && mv "$CU" "$DICH" 2>/dev/null
    dung_lai "Không đặt được bản mới vào chỗ: $DICH"
fi
rm -rf "$CU" 2>/dev/null || true

ok "Đã chép chương trình vào máy."
tin "${XAM}$DICH${HET}"

if [ "$CAP_NHAT" = "1" ]; then
    # Bản mới có thể cần thêm thư viện. Xoá dấu mốc để bộ cài soát lại danh sách
    # thư viện; thứ gì đã có thì chỉ mất vài giây kiểm tra.
    rm -f "$MOC_DIR/4-thu-vien" 2>/dev/null || true
fi

printf '\n'
tin "Từ giờ, thư mục bạn vừa tải về xoá lúc nào cũng được."
tin "Lần sau muốn mở SrtGen: bấm đúp biểu tượng ${DAM}SrtGen${HET} trên màn hình nền."
printf '\n'

exec bash "$DICH/installer/CaiDat.command"
