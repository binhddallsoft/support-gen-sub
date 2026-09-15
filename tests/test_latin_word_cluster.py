"""Từ Latin có gạch nối hay dấu nháy phải đi qua S5 + S6 thành MỘT cụm.

Ca thật (15-09-2026): máy gỡ băng ghi ``bye-bye原``. Bước 1 của S5 giữ ``bye-bye``
là một từ, nhưng jieba cắt nó thành ``bye`` | ``-`` | ``bye``, nên dấu ``-`` thành
một "từ" riêng. Dòng Hán được viết ra là ``bye - bye 原``, đọc lại thì ``-`` là dấu
câu, và phép kiểm của S6 dừng cả việc với "Lỗi nội bộ của tool: hai dòng của cùng
một câu không còn khớp số cụm". Wi-Fi, T-shirt, e-mail, don't đều chết y như vậy.
"""

from __future__ import annotations

import pytest

from srtgen.core.srt import tokenize_line
from srtgen.core.token import KIND_WORD, Cue, Document, Token
from srtgen.stages.s5_tokenize import _rejoin_latin, tokenize_document
from srtgen.stages.s6_normalize import normalize_document, verify_invariant


def test_noi_lai_manh_latin_bi_cat_giua():
    assert _rejoin_latin(["bye", "-", "bye", "原"]) == ["bye-bye", "原"]
    assert _rejoin_latin(["don", "'", "t", "担心"]) == ["don't", "担心"]


def test_khong_dung_vao_ranh_gioi_canh_chu_han():
    assert _rejoin_latin(["我", "来", "中国"]) == ["我", "来", "中国"]
    assert _rejoin_latin(["哆啦", "A", "梦"]) == ["哆啦", "A", "梦"]
    assert _rejoin_latin(["iPhone15", "手机"]) == ["iPhone15", "手机"]


@pytest.mark.parametrize(
    "raw, word",
    [
        ("bye-bye原来是你。", "bye-bye"),
        ("Wi-Fi密码是多少？", "Wi-Fi"),
        ("这件T-shirt很好看", "T-shirt"),
        ("发e-mail给我", "e-mail"),
        ("don't担心", "don't"),
        ("COVID-19疫情", "COVID-19"),
    ],
)
def test_qua_tron_s5_s6_khong_dung_viec(raw: str, word: str) -> None:
    doc = Document(cues=[Cue(index=29, start=221.99, end=223.41, tokens=[Token(kind=KIND_WORD, zh=raw)])])
    tokenize_document(doc)
    normalize_document(doc)
    verify_invariant(doc)

    cue = doc.cues[0]
    words = [t.zh for t in cue.tokens if t.kind == KIND_WORD]
    assert word in words, words
    assert "-" not in words and "'" not in words
    for line in (cue.zh_text(), cue.py_text()):
        assert sum(1 for t in tokenize_line(line) if t.kind == KIND_WORD) == len(words), line
