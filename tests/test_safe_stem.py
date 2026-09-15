"""``io_utils.safe_stem`` — tên file an toàn cho cả Windows lẫn macOS.

Vì sao có file này: tên video/tên file tải lên đi thẳng tới cuối pipeline làm
tên file kết quả. Một ký tự điều khiển như ``\\x00`` (``"a\\x00b.wav"``) không bị
lọc, và ``open()`` nổ ``ValueError: embedded null character`` ở chặng xuất file —
tức là SAU khi người dùng đã chờ hết phần gỡ băng. Người dùng không rành máy chỉ
thấy "lỗi" sau nửa tiếng chờ, không có file nào.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from srtgen import io_utils
from srtgen.io_utils import safe_stem

CONTROLS = [chr(c) for c in range(0x20)] + ["\x7f"]


def test_null_character_is_removed() -> None:
    stem = safe_stem("a\x00b.wav")
    assert "\x00" not in stem
    assert stem == "a b.wav"


@pytest.mark.parametrize("ch", CONTROLS, ids=[f"U+{ord(c):04X}" for c in CONTROLS])
def test_every_c0_control_and_del_is_removed(ch: str) -> None:
    stem = safe_stem(f"tap{ch}1")
    assert not any(ord(c) < 0x20 or c == "\x7f" for c in stem), repr(stem)
    assert stem in ("tap 1", "tap1")


def test_only_control_characters_falls_back() -> None:
    assert safe_stem("\x00\x01\x1f\x7f") == "output"
    assert safe_stem("\x00", fallback="phu-de") == "phu-de"


def test_vietnamese_and_chinese_titles_are_kept() -> None:
    assert safe_stem("Phim hay: tập 1 / 熊出没") == "Phim hay tập 1 熊出没"


def test_result_can_actually_be_written(tmp_path: Path) -> None:
    """Đúng chỗ từng nổ: ghi file bằng tên đã làm sạch phải chạy được."""
    stem = safe_stem("a\x00b\x07c\x7fd.wav")
    target = tmp_path / f"{stem}.srt"
    io_utils.write_text(target, "1\n00:00:01,000 --> 00:00:02,000\n好\nHǎo\n")
    assert target.is_file()
    assert io_utils.read_text(target).startswith("1\n")


# --------------------------------------------------------------------------- #
# Tran do dai tinh theo BYTE (macOS)
# --------------------------------------------------------------------------- #
#
# macOS (APFS/HFS+) gioi han moi phan cua ten file la 255 BYTE, khong phai 255
# ky tu. Mot chu Han ton 3 byte UTF-8, nen tran 120 ky tu cu cho ra ten 360 byte
# - cong them duoi ``_song-ngu.ass`` hay ``.truoc-<15 ky tu>.srt`` la vuot han.
# Lan hong xay ra o BUOC CUOI, sau khi may da go bang xong ca phim.


def test_ten_toan_chu_han_van_ghi_duoc_tren_macos() -> None:
    stem = safe_stem("一个人去韩国的旅行日记" * 12)
    assert len(stem.encode("utf-8")) <= 200, "than ten da vuot tran byte"
    duoi_dai_nhat = f"{stem}.truoc-20260914-143000.srt"
    assert len(duoi_dai_nhat.encode("utf-8")) < 255, (
        "cong duoi vao la vuot tran cua macOS - ghi file se hong voi ENAMETOOLONG"
    )
    assert len(f"{stem}_song-ngu.ass".encode("utf-8")) < 255


def test_cat_theo_byte_khong_lam_vo_chu() -> None:
    """Cat giua mot chu Han se sinh ra byte hong; ket qua phai luon giai ma duoc."""
    stem = safe_stem("中" * 200)
    assert stem == stem.encode("utf-8").decode("utf-8")
    assert "�" not in stem


def test_ten_latin_dai_van_theo_tran_ky_tu_cu() -> None:
    stem = safe_stem("a" * 300)
    assert len(stem) == 120, "chu Latin 1 byte thi tran 120 ky tu van la tran chat hon"


def test_cat_xong_khong_de_lai_khoang_trang_hay_dau_cham_o_cuoi() -> None:
    """Windows tu choi ten ket thuc bang khoang trang hoac dau cham."""
    stem = safe_stem("中" * 66 + " tap 1")
    assert not stem.endswith((" ", "."))


def test_ten_dai_ghi_duoc_that(tmp_path: Path) -> None:
    stem = safe_stem("一个人去韩国" * 30)
    target = tmp_path / f"{stem}_song-ngu.ass"
    io_utils.write_text(target, "x")
    assert target.is_file()
