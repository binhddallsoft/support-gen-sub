"""Thiếu dòng trống giữa hai block: không được nuốt mất một câu phụ đề.

File người ta sửa tay, hoặc ghép từ hai nguồn, hay thiếu dòng trống ngăn cách.
Bộ tách cũ lấy ĐÚNG MỘT mốc thời gian trong mỗi nhóm dòng liền nhau, nên hai
block dính vào nhau thành một: mất hẳn một câu phụ đề cùng mốc thời gian của nó,
còn số thứ tự và dòng giờ của block sau thì lọt vào giữa CHỮ của block trước.
Không một lời cảnh báo, và bấm Lưu là ghi đè.

Luật mới: mọi dòng thời gian trong nhóm đều mở một block. Dòng số thứ tự đứng
sát ngay trên một mốc giờ thì thuộc về block của mốc đó; mọi thứ khác ở lại với
block đứng trước.
"""

from __future__ import annotations

from srtgen.core.srt import parse_srt

NIHAO = ["你好。", "Nǐhǎo。"]
ZAIJIAN = ["再见。", "Zàijiàn。"]


def test_thieu_dong_trong_van_ra_hai_khoi() -> None:
    text = (
        "1\n00:00:01,000 --> 00:00:02,000\n你好。\nNǐhǎo。\n"
        "2\n00:00:02,000 --> 00:00:03,000\n再见。\nZàijiàn。\n"
    )
    blocks = parse_srt(text)
    assert len(blocks) == 2, "hai block dính vào nhau thành một là mất một câu"
    assert blocks[0].lines == NIHAO
    assert blocks[1].lines == ZAIJIAN
    assert blocks[1].index == 2
    assert blocks[1].start == 2.0


def test_thieu_ca_dong_trong_lan_so_thu_tu() -> None:
    text = (
        "1\n00:00:01,000 --> 00:00:02,000\n你好。\nNǐhǎo。\n"
        "00:00:02,000 --> 00:00:03,000\n再见。\nZàijiàn。\n"
    )
    blocks = parse_srt(text)
    assert len(blocks) == 2
    assert blocks[0].lines == NIHAO
    assert blocks[1].lines == ZAIJIAN


def test_ba_khoi_dinh_lien_nhau() -> None:
    text = "".join(
        f"{n}\n00:00:0{n},000 --> 00:00:0{n + 1},000\n好{n}\nHǎo{n}\n" for n in (1, 2, 3)
    )
    blocks = parse_srt(text)
    assert [b.index for b in blocks] == [1, 2, 3]
    assert [b.lines[0] for b in blocks] == ["好1", "好2", "好3"]


def test_dong_phu_de_la_chu_so_khong_bi_cuop_lam_so_thu_tu() -> None:
    """Một dòng phụ đề chỉ có chữ số (ví dụ năm "1994") đứng sát trên block sau."""
    text = (
        "1\n00:00:01,000 --> 00:00:02,000\n1994\n1994\n"
        "2\n00:00:02,000 --> 00:00:03,000\n好\nHǎo\n"
    )
    blocks = parse_srt(text)
    assert len(blocks) == 2
    assert blocks[0].lines == ["1994", "1994"], "dòng chữ số của block 1 phải ở lại block 1"
    assert blocks[1].index == 2


def test_dong_trong_thua_van_khong_tach_khoi() -> None:
    """Luật cũ phải giữ nguyên: dòng trống rơi VÀO GIỮA một block thì nối lại."""
    text = "1\n00:00:01,000 --> 00:00:02,000\n你好。\n\nNǐhǎo。\n"
    blocks = parse_srt(text)
    assert len(blocks) == 1
    assert blocks[0].lines == NIHAO


def test_file_chuan_khong_doi_gi() -> None:
    text = (
        "1\n00:00:01,000 --> 00:00:02,000\n你好。\nNǐhǎo。\n\n"
        "2\n00:00:02,000 --> 00:00:03,000\n再见。\nZàijiàn。\n"
    )
    blocks = parse_srt(text)
    assert len(blocks) == 2
    assert [b.lines for b in blocks] == [NIHAO, ZAIJIAN]
