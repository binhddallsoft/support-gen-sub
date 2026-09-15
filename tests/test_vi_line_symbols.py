"""Dòng tiếng Việt chỉ có ký hiệu hoặc con số không được nuốt vào dòng pinyin.

File ba dòng (Hán / pinyin / Việt) là thứ bên dịch gửi sang rất hay dùng. Bộ tách
quyết định dòng thứ ba là pinyin hay tiếng Việt bằng CHÍNH TẢ; dòng nào không còn
chữ nào để kiểm thì mặc định coi là pinyin, vì "không mang ngôn ngữ nào có thể
mất". Mặc định đó đúng cho phần đuôi của một dòng pinyin bị ngắt (``……``), nhưng
sai hẳn cho một câu phụ đề thật chỉ gồm ``♪`` (đang có nhạc) hoặc một con số
(``1994``): câu đó bị dính vào cuối dòng pinyin và biến mất khỏi file ``_vi.srt``,
không một lời báo.

Luật mới, chỉ áp cho DÒNG CUỐI của block ba dòng trở lên: đòi bằng chứng. Dòng
chỉ gồm dấu câu vẫn theo luật cũ.
"""

from __future__ import annotations

import pytest

from srtgen.core.srt import split_block_content

HAN = "你好 世界。"
PINYIN = "Nǐhǎo shìjiè。"


def _vi_of(third: str) -> str | None:
    return split_block_content([HAN, PINYIN, third]).vi_line


@pytest.mark.parametrize("third", ["♪", "♪♪", "♫", "1994", "2019", "#1"])
def test_ky_hieu_va_con_so_van_la_cau_tieng_viet(third: str) -> None:
    assert _vi_of(third) == third, "câu này vừa biến mất khỏi file tiếng Việt"


@pytest.mark.parametrize("third", ["Hà Nội", "Ừ.", "Ok.", "Chào anh."])
def test_cau_tieng_viet_binh_thuong_khong_doi(third: str) -> None:
    assert _vi_of(third) == third


@pytest.mark.parametrize("third", ["……", "…", "...", "——", "- ?"])
def test_dong_chi_co_dau_cau_van_la_phan_duoi_cua_dong_pinyin(third: str) -> None:
    """Luật cũ phải giữ nguyên: đây thật sự là đuôi của một dòng pinyin bị ngắt."""
    out = split_block_content([HAN, PINYIN, third])
    assert out.vi_line is None
    assert third in out.lines[1]


def test_pinyin_bi_ngat_dong_van_duoc_noi_lai() -> None:
    out = split_block_content([HAN, "Nǐhǎo", "shìjiè。"])
    assert out.vi_line is None
    assert out.lines == [HAN, "Nǐhǎo shìjiè。"]


def test_block_hai_dong_khong_bi_dung_toi() -> None:
    out = split_block_content([HAN, PINYIN])
    assert out.lines == [HAN, PINYIN]
    assert out.vi_line is None


def test_dong_pinyin_that_van_la_pinyin() -> None:
    """Không được siết tay tới mức đẩy một dòng pinyin thật sang cột tiếng Việt."""
    out = split_block_content(["我 来 了。", "Wǒ lái le。", "Tôi đến rồi."])
    assert out.lines == ["我 来 了。", "Wǒ lái le。"]
    assert out.vi_line == "Tôi đến rồi."
