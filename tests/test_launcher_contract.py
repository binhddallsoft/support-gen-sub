"""SrtGen.command: file duy nhất người dùng bấm để cài, mở và cập nhật.

Không có máy Mac trong bộ kiểm này, nên test đọc chính file bash và kiểm những
lời hứa mà nếu mất đi thì người dùng thật sẽ gặp chuyện:

* Bộ cài đăng ký SrtGen theo kiểu liên kết thẳng tới thư mục mã nguồn. Chạy từ
  thư mục vừa tải về thì dọn thư mục Tải về là app hỏng. Nên file khởi động PHẢI
  chép chương trình vào ``~/Library/Application Support/SrtGen/app`` rồi mới gọi
  bộ cài TỪ CHỖ ĐÓ.
* "Đã cài" phải nghĩa là có đủ thư viện, không chỉ là có file python — nếu không
  thì lời khuyên "bấm lại SrtGen.command để sửa" trong tab Hướng dẫn là sai.
* macOS có sẵn bash 3.2: không được dùng tính năng của bash mới.
* Một ký tự CR là file chết ngay dòng đầu với lỗi "bad interpreter".

Nhánh logic (lần đầu / mở lại / có bản mới / cài hỏng) đã được chạy thử trong một
môi trường giả lập macOS lúc viết; test này giữ cho những mấu chốt đó không bị
lẳng lặng gỡ đi.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "SrtGen.command"


@pytest.fixture(scope="module")
def src() -> str:
    return LAUNCHER.read_text(encoding="utf-8")


def test_file_khoi_dong_nam_o_ngoai_cung() -> None:
    assert LAUNCHER.is_file(), "người dùng giải nén ra phải thấy ngay SrtGen.command"


def test_khong_co_ky_tu_cr() -> None:
    assert b"\r" not in LAUNCHER.read_bytes(), "CRLF làm file .command chết ở dòng shebang"


def test_bat_dau_bang_bash(src: str) -> None:
    assert src.startswith("#!/bin/bash\n")


@pytest.mark.parametrize("bash4", ["declare -A", "mapfile", "readarray", ",,}", "^^}", "|&"])
def test_chi_dung_cu_phap_bash_32(src: str, bash4: str) -> None:
    assert bash4 not in src, f"“{bash4}” không có trong bash 3.2 của macOS"


def test_chep_chuong_trinh_ra_cho_co_dinh_roi_moi_cai(src: str) -> None:
    assert 'APP_SUPPORT="$HOME/Library/Application Support/SrtGen"' in src
    assert 'DICH="$APP_SUPPORT/app"' in src
    assert 'exec bash "$DICH/installer/CaiDat.command"' in src
    assert 'exec bash "$DICH/installer/KhoiDong.command"' in src
    assert 'exec bash "$NGUON/installer' not in src, (
        "không được chạy bộ cài từ thư mục tải về — dọn thư mục đó là app hỏng"
    )


def test_da_cai_nghia_la_du_thu_vien(src: str) -> None:
    start = src.index("da_cai() {")
    body = src[start : start + 400]
    assert "find_spec" in body
    for mod in ("srtgen", "fastapi", "faster_whisper", "yt_dlp"):
        assert f'"{mod}"' in body, f"không kiểm thư viện {mod}"


def test_cap_nhat_thi_soat_lai_thu_vien(src: str) -> None:
    assert 'rm -f "$MOC_DIR/4-thu-vien"' in src


def test_doi_ban_bang_doi_ten_khong_chep_de(src: str) -> None:
    """Đứt giữa chừng thì bản cũ phải còn nguyên để trả lại."""
    assert 'mv "$DICH" "$CU"' in src
    assert 'mv "$TAM" "$DICH"' in src


def test_go_nhan_quarantine_tren_ban_chep(src: str) -> None:
    assert 'xattr -r -d com.apple.quarantine "$TAM"' in src


def test_go_cai_dat_xoa_ca_ban_chep() -> None:
    go = (ROOT / "installer" / "GoCaiDat.command").read_text(encoding="utf-8")
    assert 'xoa "$APP_SUPPORT/app"' in go


def test_goi_phat_hanh_co_file_khoi_dong() -> None:
    mr = (ROOT / "tools" / "make_release.py").read_text(encoding="utf-8")
    assert '"SrtGen.command"' in mr.split("INCLUDE_FILES", 1)[1].split("\n", 1)[0]
    assert '"SrtGen.command",' in mr.split("REQUIRED = (", 1)[1].split(")", 1)[0]


# --------------------------------------------------------------------------- #
# Lỗi đo được trên iMac thật, 15-09-2026
# --------------------------------------------------------------------------- #

import re  # noqa: E402

TAT_CA_FILE_BASH = [LAUNCHER, *sorted((ROOT / "installer").glob("*.command"))]

#: Tên biến viết trần ($ten) đứng DÍNH ngay trước một ký tự không phải ASCII.
_BIEN_DINH_CHU = re.compile(rb"\$([A-Za-z_][A-Za-z0-9_]*)(?=[\x80-\xff])")


@pytest.mark.parametrize("path", TAT_CA_FILE_BASH, ids=lambda p: p.name)
def test_ten_bien_khong_dinh_lien_chu_tieng_viet(path: Path) -> None:
    """``tin "Đang tải $ten…"`` làm bộ cài dừng hẳn ở bước 5 trên iMac thật.

    Bash 3.2 có sẵn trên macOS đọc byte đầu tiên của ``…`` (hay một chữ tiếng
    Việt có dấu) như một phần của tên biến, đi tìm biến tên ``ten?`` không tồn tại,
    và vì file bật ``set -u`` nên dừng với lỗi ``ten?: unbound variable``. Bash mới
    trên Windows/Linux đọc đúng, nên chạy thử ở đó không thấy lỗi. Luôn viết
    ``${ten}`` khi ngay sau tên biến là một ký tự không phải ASCII.
    """
    b = path.read_bytes()
    loi = []
    for m in _BIEN_DINH_CHU.finditer(b):
        dong = b.count(b"\n", 0, m.start()) + 1
        ten = m.group(1).decode()
        loi.append(f"dòng {dong}: ${ten} phải viết thành ${{{ten}}}")
    assert not loi, "\n".join(loi)


def test_da_cai_nghia_la_bo_cai_da_chay_toi_cuoi(src: str) -> None:
    """Bộ cài dừng ở bước 5 sau khi bước 4 đã cài đủ thư viện. Chỉ kiểm thư viện
    thì bấm lại sẽ mở thẳng app thiếu ffmpeg và mô hình nghe."""
    start = src.index("da_cai() {")
    body = src[start : start + 500]
    assert "cai-dat.json" in body
    cai_dat = (ROOT / "installer" / "CaiDat.command").read_text(encoding="utf-8")
    assert 'RECEIPT="$APP_SUPPORT/cai-dat.json"' in cai_dat
    assert cai_dat.rindex('cat > "$RECEIPT"') > cai_dat.rindex("buoc "), (
        "hồ sơ cai-dat.json phải được ghi SAU bước cuối cùng của bộ cài"
    )

