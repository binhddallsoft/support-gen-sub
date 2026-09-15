"""Gạch nối nằm trong một từ không được biến thành dấu ngắt lời.

Lỗi đã đo được: `srtgen fix` đổi mọi dấu `-` dính giữa hai chữ thành `——`, nên
phụ đề ghi ra `Wi——Fi`, `2019——2020`, `Jean——Luc`. Người dùng nhìn thấy chữ hỏng
và không có cách nào đoán ra vì sao, vì chính họ gõ đúng.

Luật: `-` có chữ cái Latin hoặc chữ số ASCII ở cả hai bên, không khoảng trắng,
là **chính tả của từ đó** — giữ nguyên, và cả cụm tính là MỘT cụm. Ba trường hợp
còn lại không đổi: đầu lượt thoại vẫn là marker, `-` giữa hai chữ Hán vẫn là dấu
ngắt lời, `--` trở lên vẫn là dấu ngắt lời.

Phải đúng ở CẢ HAI bộ tách — bộ của `srt.py` (dùng để sửa) và bộ của `rules.py`
(dùng để kiểm). Lệch nhau thì `fix` chạy xong `check` vẫn báo lỗi.
"""

from __future__ import annotations

import pytest

from srtgen.core.rules import _lex_line, validate_text
from srtgen.pipeline import run_fix


def _srt(*lines: tuple[str, str]) -> str:
    return "".join(
        f"{i}\n00:00:0{i},000 --> 00:00:0{i + 1},000\n{zh}\n{py}\n\n"
        for i, (zh, py) in enumerate(lines, start=1)
    )


GIU_NGUYEN = [
    ("我 用 Wi-Fi 上网。", "Wǒ yòng Wi-Fi shàngwǎng。"),
    ("2019-2020 年 的 事。", "2019-2020 nián de shì。"),
    ("他 叫 Jean-Luc。", "Tā jiào Jean-Luc。"),
    ("COVID-19 来 了。", "COVID-19 lái le。"),
]


@pytest.mark.parametrize("zh, py", GIU_NGUYEN)
def test_sua_xong_van_giu_nguyen_gach_noi(zh: str, py: str) -> None:
    out, findings = run_fix(_srt((zh, py)))
    assert findings == [], [f.code for f in findings]
    assert zh in out, f"gạch nối trong “{zh}” đã bị đổi"
    assert py in out


@pytest.mark.parametrize("zh, py", GIU_NGUYEN)
def test_bo_kiem_khong_bao_loi_gia(zh: str, py: str) -> None:
    assert validate_text(_srt((zh, py))) == []


@pytest.mark.parametrize(
    "line, cum",
    [
        ("我 用 Wi-Fi 上网。", ["我", "用", "Wi-Fi", "上网"]),
        ("Jǐnguǎn Měi-Yī shuāngfāng，", ["Jǐnguǎn", "Měi-Yī", "shuāngfāng"]),
        ("2019-2020 年 的 事。", ["2019-2020", "年", "的", "事"]),
    ],
)
def test_ca_cum_tinh_la_mot(line: str, cum: list[str]) -> None:
    """Cắt "Wi-Fi" thành ba mảnh làm dòng pinyin nhiều cụm hơn dòng Hán."""
    assert [x.text for x in _lex_line(line) if x.kind == "word"] == cum


def test_marker_doi_nguoi_noi_khong_bi_dung_toi() -> None:
    out, findings = run_fix(_srt(("- 你 好。 - 再见。", "- Nǐ hǎo。 - Zàijiàn。")))
    assert findings == []
    assert "- 你 好。 - 再见。" in out


def test_gach_giua_hai_chu_han_van_thanh_dau_ngat_loi() -> None:
    out, _ = run_fix(_srt(("好-坏 都 有。", "hǎo——huài dōu yǒu。")))
    assert "好——坏" in out, "gạch giữa hai chữ Hán vẫn là dấu ngắt lời theo README"


def test_hai_gach_tro_len_van_thanh_dau_ngat_loi() -> None:
    out, _ = run_fix(_srt(("他 说--我 走 了。", "tā shuō——wǒ zǒu le。")))
    assert "说——我" in out


@pytest.mark.parametrize("line", ["-好", "好-", "-", "- 好"])
def test_gach_o_mep_dong_khong_bao_gio_la_gach_noi_trong_tu(line: str) -> None:
    """Thiếu một bên thì không phải gạch nối trong từ, dù bên kia là chữ gì."""
    kinds = [x.kind for x in _lex_line(line)]
    assert "word" not in kinds or all(
        "-" not in x.text for x in _lex_line(line) if x.kind == "word"
    )
