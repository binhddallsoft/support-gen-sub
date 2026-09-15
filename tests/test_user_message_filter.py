"""Câu tiếng Anh của thư viện không được lọt lên màn hình lỗi.

Màn hình lỗi hiện `err.user_message` nếu có, nếu không thì hiện `str(err)` —
miễn là câu đó trông không phải câu kỹ thuật. Bộ lọc cũ chỉ tìm sáu mẩu chữ
(`traceback`, `error:`, `exception`, `none type`, `nonetype`, `0x`) nên
``list index out of range`` và ``Read timed out`` đi thẳng lên màn hình của một
nhân viên không biết tiếng Anh.

Luật mới có thêm một tầng: câu nào không mang **một** chữ tiếng Việt có dấu nào
thì coi là câu của thư viện. Mọi câu do dự án này viết đều có dấu. Tầng đó không
được bắt nhầm câu tiếng Việt chỉ vì trong câu có một đường dẫn file.
"""

from __future__ import annotations

import pytest

from srtgen.web.jobs import _looks_technical

CUA_THU_VIEN = [
    "list index out of range",
    "Connection reset by peer",
    "Read timed out",
    "KeyError: 'segments'",
    "Traceback (most recent call last):",
    "'NoneType' object has no attribute 'cues'",
    "[Errno 2] No such file or directory: '/Users/binh/Phim.mp4'",
    "invalid literal for int() with base 10: 'x'",
    "Permission denied",
]

CUA_DU_AN = [
    "Không tìm thấy file phụ đề trên máy.",
    "Máy chưa cài ffmpeg. Bấm nút bên cạnh để cài giúp.",
    "Đường dẫn /Users/binh/Phim.mp4 không đọc được. Hãy kiểm tra lại.",
    "Video này dài hơn mức tool chạy được. Hãy cắt thành nhiều phần.",
    "Mã API chưa đúng. Vào Cài đặt và dán lại mã.",
]


@pytest.mark.parametrize("text", CUA_THU_VIEN)
def test_cau_cua_thu_vien_bi_giu_lai(text: str) -> None:
    assert _looks_technical(text) is True, f"câu này sẽ lọt lên màn hình: {text}"


@pytest.mark.parametrize("text", CUA_DU_AN)
def test_cau_cua_du_an_van_duoc_hien(text: str) -> None:
    assert _looks_technical(text) is False, f"câu tiếng Việt bị giấu mất: {text}"


def test_ten_phim_tieng_viet_trong_duong_dan_khong_cuu_duoc_cau_tieng_anh() -> None:
    """Đường dẫn hay mang tên phim tiếng Việt; đừng vì thế mà tưởng cả câu là tiếng Việt."""
    assert _looks_technical(
        "[Errno 2] No such file or directory: '/Users/binh/Phim Hàn Quốc.mp4'"
    ) is True


def test_bai_log_dai_nhieu_dong_bi_giu_lai() -> None:
    assert _looks_technical("dòng\n" * 80 + "x" * 250) is True
