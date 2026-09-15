"""Ba lời hứa của bàn làm việc, kiểm bằng cách đọc chính app.js.

Không có bộ chạy JavaScript trong dự án này, nên đây là cách duy nhất để một lần
sửa app.js sau này không lẳng lặng mở lại ba lỗ hổng mất dữ liệu đã đo được:

1. ``commitActiveCell()`` phải biết tới khung sửa dòng (``#cuebox``). Thiếu nhánh
   đó thì Ctrl+S ghi file thiếu đúng dòng đang gõ rồi báo "Đã lưu", và nhịp tự
   lưu nháp 5 giây cũng bỏ sót nó.
2. Con trỏ đang trong ô nhập thì Ctrl+Z phải trả về cho trình duyệt. Trước đây
   nó chạy ``undoEdit()`` của bảng và xoá trắng chữ đang gõ, không có đường lấy lại.
3. Đổi số dòng (tách / gộp / xoá) phải ghi lại danh sách dấu ✓ lên máy chủ, vì
   danh sách đó lưu theo SỐ DÒNG. Không ghi lại thì mở file ngày mai thấy dấu
   đã soát nằm lệch hàng.

Phép kiểm cố ý tìm những mẩu chữ NGẮN và mang nghĩa, để đổi tên biến hay dọn dẹp
mã không làm test đỏ oan, nhưng bỏ hẳn cơ chế thì đỏ ngay.
"""

from __future__ import annotations

from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parents[1] / "srtgen" / "web" / "static" / "app.js"


@pytest.fixture(scope="module")
def source() -> str:
    return APP_JS.read_text(encoding="utf-8")


def test_ghi_not_o_dang_go_truoc_khi_luu(source: str) -> None:
    start = source.index("function commitActiveCell()")
    body = source[start : start + 900]
    assert "#cuebox" in body, (
        "commitActiveCell() không còn biết tới khung sửa dòng — "
        "Ctrl+S sẽ lại ghi file thiếu dòng người dùng đang gõ"
    )
    assert "cbCommit()" in body


def test_ctrl_s_va_ctrl_enter_deu_ghi_not_truoc(source: str) -> None:
    for phim in ("key === 's'", "key === 'enter'"):
        idx = source.index(phim)
        assert "commitActiveCell()" in source[idx : idx + 200], (
            f"phím tắt {phim} không ghi nốt ô đang gõ trước khi chạy"
        )


def test_ctrl_z_khi_dang_go_tra_ve_cho_trinh_duyet(source: str) -> None:
    assert "if (typing && mod && (key === 'z' || key === 'y')) return;" in source, (
        "mất chốt trả Ctrl+Z về cho trình duyệt — hoàn tác của bảng sẽ lại "
        "xoá trắng chữ đang gõ trong ô nhập"
    )


def test_doi_so_dong_thi_ghi_lai_dau_da_soat(source: str) -> None:
    start = source.index("function replaceAllCues(")
    body = source[start : start + 1400]
    assert "scheduleReviewSave()" in body, (
        "tách / gộp / xoá dòng không ghi lại dấu ✓ — lần mở sau dấu sẽ lệch hàng"
    )


def test_tach_gop_khong_de_lai_danh_sach_loi_rong_kieu_null(source: str) -> None:
    """`findings: null` nghĩa là "chưa biết" và khiến giao diện đi tra theo số dòng
    vừa bị đánh lại — dòng mới sẽ đeo lỗi của một câu khác. Phải là mảng rỗng."""
    start = source.index("function splitCueAtCursor")
    body = source[start : start + 2600]
    assert "findings: null" not in body
    assert body.count("findings: []") >= 2


# --------------------------------------------------------------------------- #
# Them mot dong phu de moi
# --------------------------------------------------------------------------- #
#
# May go bang bo sot ca cau la chuyen thuong. Truoc day trinh sua chi co tach,
# gop va xoa, nen cho bi sot khong co duong nao chen them: tach thi phai cat mot
# dong dang dung lam doi, va moc gio van nam sai cho.


def test_co_duong_them_mot_dong_moi(source: str) -> None:
    assert "function insertCueAfter()" in source
    assert "cbEl('insert').addEventListener('click', insertCueAfter)" in source, (
        "nut Them dong khong duoc noi day"
    )


def test_them_dong_khong_bao_gio_de_hai_dong_chong_moc_gio(source: str) -> None:
    start = source.index("function insertCueAfter()")
    body = source[start : start + 3200]
    assert "next.start" in body, "phai biet dong sau bat dau luc nao"
    assert "MIN_NEW_CUE" in body, "phai co nguong khe trong nho nhat"
    assert "Không còn chỗ để chen dòng mới" in body, (
        "het cho thi phai noi thang, khong duoc tao mot dong chong moc gio "
        "roi de bo kiem bao loi"
    )


def test_ctrl_shift_enter_duoc_kiem_truoc_ctrl_enter(source: str) -> None:
    """Hai viec trai nhau tren cung mot phim; nhanh co Shift phai dung truoc."""
    them = source.index("ev.shiftKey && key === 'enter'")
    xong = source.index("if (mod && key === 'enter')")
    assert them < xong, "Ctrl+Shift+Enter bi Ctrl+Enter nuot mat"


def test_luu_phai_canh_bao_dong_khong_co_chu_han(source: str) -> None:
    """`emit_srt` bo moi dong khong co Han lan pinyin. Nguoi dung phai biet truoc."""
    start = source.index("async function saveDoc()")
    body = source[start : start + 3000]
    assert "chưa có chữ Hán" in body, "luu im lang bo mat dong chi co tieng Viet"

