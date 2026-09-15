"""Thẻ định dạng SRT (``<i>``, ``</b>``, ``<font …>``) — hợp đồng C.

Vì sao có file này
==================
Bên dịch gửi file có ``<i>`` thường xuyên. Trước đợt này thẻ đi qua ``fix`` được
(không còn thành ``《i》``) nhưng vẫn sai ở ba chỗ:

* hằng ``KIND_MARKUP`` chỉ nằm ở ``srt.py``, ngoài ``token.KINDS``, nên mọi lần
  nạp lại (``bundle.json``, file chặng ``S*.json``) ép thẻ về ``word`` — ``<i>``
  thành một cụm chữ có pinyin;
* renderer coi thẻ như dấu câu: ``他 说 <i>好</i>。`` ra ``他 说<i>好</i>。``
  (mất khoảng trắng giữa hai cụm ``说`` và ``好``);
* marker ngay sau thẻ mở ở đầu dòng bị coi là giữa dòng:
  ``<i>- 你 好。</i>`` ra ``<i> - 你 好。</i>``.

Hợp đồng C: thẻ MỞ dính vào token SAU nó (khoảng trắng nếu có đứng TRƯỚC thẻ),
thẻ ĐÓNG dính vào token TRƯỚC nó, và thẻ không làm đổi luật khoảng trắng của các
token xung quanh. Hệ quả kiểm được bằng máy: xoá thẻ khỏi dòng đã render thì ra
đúng dòng render khi không có thẻ.

Mọi test chạy ngoại tuyến.
"""

from __future__ import annotations

import random
from typing import Any

import pytest

from srtgen.core.srt import SRT_MARKUP_RE, parse_srt, tokenize_line
from srtgen.core.token import (
    KIND_MARKER,
    KIND_MARKUP,
    KIND_PUNCT,
    KIND_WORD,
    KINDS,
    Cue,
    Document,
    Token,
    is_closing_tag,
    render_py,
    render_zh,
    word_count,
)

W, P, M, X = KIND_WORD, KIND_PUNCT, KIND_MARKER, KIND_MARKUP


def toks(*spec: tuple[str, str] | tuple[str, str, str | None]) -> list[Token]:
    out: list[Token] = []
    for item in spec:
        kind, zh = item[0], item[1]
        pinyin = item[2] if len(item) > 2 else None  # type: ignore[misc]
        out.append(Token(kind=kind, zh=zh, pinyin=pinyin))
    return out


# --------------------------------------------------------------------------- #
# 1. KIND_MARKUP là một kind thật, đi qua mọi vòng nạp/ghi
# --------------------------------------------------------------------------- #

def test_markup_is_a_real_kind_and_srt_reexports_the_same_constant() -> None:
    from srtgen.core import srt

    assert KIND_MARKUP == "markup"
    assert KIND_MARKUP in KINDS
    assert srt.KIND_MARKUP is KIND_MARKUP


def test_bundle_shape_of_a_tag() -> None:
    """``bundle.json`` ghi ``{"kind":"markup","text":"<i>","pinyin":null}``."""
    tag = Token(kind=KIND_MARKUP, zh="<i>", pinyin="rác")
    assert tag.to_dict() == {"kind": "markup", "text": "<i>", "pinyin": None}


@pytest.mark.parametrize("raw_kind", ["markup", "MARKUP", "tag"])
def test_bundle_reload_keeps_the_kind(raw_kind: str) -> None:
    back = Token.from_dict({"kind": raw_kind, "text": "</i>", "pinyin": "x"})
    assert back.kind == KIND_MARKUP
    assert back.pinyin is None
    assert back.zh == "</i>"


def test_stage_file_round_trip_keeps_the_kind() -> None:
    """Trước đây ``from_stage_dict`` ép ``markup`` về ``word``: thẻ thành cụm chữ."""
    tag = Token(kind=KIND_MARKUP, zh='<font color="#ff0000">', source="punct", flags=["X"])
    back = Token.from_stage_dict(tag.to_stage_dict())
    assert back.kind == KIND_MARKUP
    assert back.zh == '<font color="#ff0000">'
    assert (back.source, back.flags) == ("punct", ["X"])
    # Không ghi source thì loader điền "punct" chứ không phải "jieba": thẻ không
    # bao giờ do bộ tách từ sinh ra.
    assert Token.from_stage_dict({"kind": "markup", "zh": "<i>"}).source == "punct"


def test_document_round_trips_keep_tags() -> None:
    cue = Cue(
        index=1,
        start=1.0,
        end=2.0,
        tokens=toks((X, "<i>"), (W, "好", "hǎo"), (P, "。"), (X, "</i>")),
    )
    doc = Document(cues=[cue])
    for back in (
        Document.from_dict(doc.to_dict()),
        Document.from_stage_dict(doc.to_stage_dict()),
    ):
        kinds = [t.kind for t in back.cues[0].tokens]
        assert kinds == [X, W, P, X]
        assert render_zh(back.cues[0].tokens) == "<i>好。</i>"
        assert render_py(back.cues[0].tokens) == "<i>hǎo。</i>"


def test_word_count_does_not_count_tags() -> None:
    tokens = toks((X, "<i>"), (W, "他"), (W, "说"), (X, "<b>"), (W, "好"), (X, "</b>"), (X, "</i>"))
    assert word_count(tokens) == 3
    assert Cue(index=1, start=0.0, end=1.0, tokens=tokens).word_count() == 3


def test_is_closing_tag() -> None:
    assert is_closing_tag("</i>") and is_closing_tag("</font>")
    assert not is_closing_tag("<i>") and not is_closing_tag('<font color="red">')


def test_tokenize_line_emits_markup_tokens() -> None:
    assert [(t.kind, t.zh) for t in tokenize_line("他 说 <i>好</i>。")] == [
        (W, "他"), (W, "说"), (X, "<i>"), (W, "好"), (X, "</i>"), (P, "。"),
    ]
    assert [(t.kind, t.zh) for t in tokenize_line("<i>- 你 好。</i>")] == [
        (X, "<i>"), (M, "-"), (W, "你"), (W, "好"), (P, "。"), (X, "</i>"),
    ]


# --------------------------------------------------------------------------- #
# 2. Luật render của hợp đồng C
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        # Ví dụ của hợp đồng: trước đây ra "他 说<i>好</i>。"
        (toks((W, "他"), (W, "说"), (X, "<i>"), (W, "好"), (X, "</i>"), (P, "。")),
         "他 说 <i>好</i>。"),
        # Ví dụ của hợp đồng: trước đây ra "<i> - 你 好。</i>"
        (toks((X, "<i>"), (M, "-"), (W, "你"), (W, "好"), (P, "。"), (X, "</i>")),
         "<i>- 你 好。</i>"),
        (toks((X, "<i>"), (W, "他"), (W, "说"), (W, "好"), (P, "。"), (X, "</i>")),
         "<i>他 说 好。</i>"),
        # Marker giữa dòng sau thẻ mở: khoảng trắng đứng trước thẻ.
        (toks((W, "好"), (P, "。"), (X, "<i>"), (M, "-"), (W, "你"), (X, "</i>")),
         "好。 <i>- 你</i>"),
        # Thẻ không đổi khoảng trắng của marker hay của hai cụm hai bên.
        (toks((M, "-"), (X, "<i>"), (W, "你"), (X, "</i>"), (W, "好")),
         "- <i>你</i> 好"),
        # Thẻ đóng dính cụm trước, thẻ mở dính cụm sau, khoảng trắng ở giữa.
        (toks((W, "好"), (X, "</i>"), (X, "<i>"), (W, "你")),
         "好</i> <i>你"),
        # Thẻ đóng tới khi thẻ mở còn đang chờ: giữ đúng thứ tự, không nhảy lên trước.
        (toks((W, "好"), (X, "<i>"), (X, "</i>"), (W, "你")),
         "好 <i></i>你"),
        # Thẻ đứng cuối không có gì theo sau; thẻ đóng đứng đầu dòng.
        (toks((W, "好"), (X, "<i>")), "好<i>"),
        (toks((X, "</i>"), (M, "-"), (W, "你")), "</i>- 你"),
        # Dấu câu rồi thẻ đóng rồi cụm chữ: vẫn không có khoảng trắng sau dấu câu.
        (toks((W, "好"), (P, "。"), (X, "</i>"), (W, "你")), "好。</i>你"),
        # Thẻ font có dấu " bên trong vẫn nguyên văn.
        (toks((X, '<font color="#ff0000">'), (W, "他"), (W, "说"), (P, "："), (P, "“"),
              (W, "好"), (P, "”"), (X, "</font>")),
         '<font color="#ff0000">他 说：“好”</font>'),
        # Chỉ có thẻ.
        (toks((X, "<i>"), (X, "</i>")), "<i></i>"),
    ],
)
def test_contract_c_rendering(tokens: list[Token], expected: str) -> None:
    line = render_zh(tokens)
    assert line == expected
    assert line == line.strip()
    assert "  " not in line


def test_pinyin_line_follows_the_same_rule() -> None:
    tokens = toks((W, "他", "tā"), (W, "说", "shuō"), (X, "<i>"), (W, "好", "hǎo"),
                  (X, "</i>"), (P, "。"))
    assert render_py(tokens) == "tā shuō <i>hǎo</i>。"


@pytest.mark.parametrize(
    ("wrong", "right"),
    [
        # Thẻ đã ngăn 说 và 好 thành hai token, tức hai cụm: giữa hai cụm chữ luôn
        # có đúng một khoảng trắng, và khoảng trắng đó đứng TRƯỚC thẻ mở.
        ("他 说<i>好</i>。", "他 说 <i>好</i>。"),
        ("<i> - 你 好。</i>", "<i>- 你 好。</i>"),
        ("<i>- 你 好。- 再见。</i>", "<i>- 你 好。 - 再见。</i>"),
    ],
)
def test_render_of_a_read_line(wrong: str, right: str) -> None:
    """Đọc một dòng người khác viết rồi render lại ra đúng dạng hợp đồng C."""
    assert render_zh(tokenize_line(wrong, normalize=True)) == right


@pytest.mark.parametrize(
    "line",
    [
        "他 说 <i>好</i>。",
        "<i>- 你 好。</i>",
        "<i>- 你 好。 - 再见。</i>",
        "<i>他 说 好。</i>",
        '<font color="#ff0000">他 说：“好”</font>',
        "<b><i>你 好</i></b>",
    ],
)
def test_contract_lines_round_trip(line: str) -> None:
    """Dòng đã đúng hợp đồng C đi qua đọc -> render phải ra y hệt từng ký tự."""
    assert render_zh(tokenize_line(line)) == line
    assert render_zh(tokenize_line(line, normalize=True)) == line


# --------------------------------------------------------------------------- #
# 3. Tính chất trên token list ngẫu nhiên
# --------------------------------------------------------------------------- #

_TAGS = ["<i>", "</i>", "<b>", "</b>", "<u>", "</u>", '<font color="#ffffff">', "</font>"]
_WORDS = [("好", "hǎo"), ("你", "nǐ"), ("说", "shuō"), ("唱得", "chàngdé"), ("OK", "OK")]
_PUNCTS = ["。", "，", "……", "？", "《", "》", "——"]


def _random_tokens(rng: random.Random) -> list[Token]:
    out: list[Token] = []
    for _ in range(rng.randint(0, 12)):
        roll = rng.random()
        if roll < 0.4:
            zh, py = rng.choice(_WORDS)
            out.append(Token(kind=W, zh=zh, pinyin=py))
        elif roll < 0.6:
            out.append(Token(kind=P, zh=rng.choice(_PUNCTS)))
        elif roll < 0.72:
            out.append(Token(kind=M, zh="-"))
        elif roll < 0.76:
            out.append(Token(kind=W, zh="", pinyin=""))  # token rỗng: khiếm khuyết dữ liệu
        else:
            out.append(Token(kind=X, zh=rng.choice(_TAGS)))
    return out


def test_tags_are_transparent_to_spacing_on_random_token_lists() -> None:
    """Xoá thẻ khỏi dòng đã render == render khi không có thẻ; và mọi bảo đảm cũ vẫn giữ."""
    rng = random.Random(20260910)
    for case in range(3000):
        tokens = _random_tokens(rng)
        bare = [t for t in tokens if t.kind != X]
        tags = [t.zh for t in tokens if t.kind == X]
        for render in (render_zh, render_py):
            line = render(tokens)
            assert line == line.strip(), (case, line)
            assert "  " not in line, (case, line)
            assert SRT_MARKUP_RE.sub("", line) == render(bare), (case, line, render(bare))
            assert SRT_MARKUP_RE.findall(line) == tags, (case, line)
        zh_words = sum(1 for t in tokenize_line(render_zh(tokens)) if t.kind == W)
        py_words = sum(1 for t in tokenize_line(render_py(tokens)) if t.kind == W)
        assert zh_words == py_words == word_count(t for t in tokens if t.zh), (case, tokens)


# --------------------------------------------------------------------------- #
# 4. Qua đúng lệnh fix
# --------------------------------------------------------------------------- #

def _block(*lines: str) -> str:
    return "1\n00:00:01,000 --> 00:00:02,000\n" + "\n".join(lines) + "\n\n"


def _lines(text: str) -> tuple[str, str]:
    (block,) = parse_srt(text)
    return block.lines[0], block.lines[1]


def test_run_fix_renders_contract_c_on_a_chinese_only_block(cfg: dict[str, Any]) -> None:
    """Block 3 dòng (chưa có pinyin): sinh pinyin, thẻ đúng chỗ, file ra sạch lỗi."""
    from srtgen.core.rules import validate_text
    from srtgen.pipeline import run_fix

    fixed, _ = run_fix(_block("他 说 <i>好</i>。"), cfg)
    assert _lines(fixed) == ("他 说 <i>好</i>。", "Tā shuō <i>hǎo</i>。")
    assert [f for f in validate_text(fixed) if f.severity == "error"] == []


def test_run_fix_keeps_marker_after_opening_tag_at_line_start(cfg: dict[str, Any]) -> None:
    """``<i>- 你 好。</i>``: marker vẫn là marker, vẫn tính là đầu dòng, cụm sau nó viết hoa."""
    from srtgen.pipeline import run_fix

    fixed, _ = run_fix(_block("<i> - 你 好。</i>", "<i> - nǐ hǎo。</i>"), cfg)
    assert _lines(fixed) == ("<i>- 你 好。</i>", "<i>- Nǐ hǎo。</i>")


def test_run_fix_repairs_a_tag_glued_between_two_clusters(cfg: dict[str, Any]) -> None:
    from srtgen.pipeline import run_fix

    fixed, _ = run_fix(_block("他 说<i>好</i>。", "tā shuō<i>hǎo</i>。"), cfg)
    assert _lines(fixed) == ("他 说 <i>好</i>。", "Tā shuō <i>hǎo</i>。")


# --------------------------------------------------------------------------- #
# 5. Marker giữa dòng đứng sau thẻ mở — validator nhìn xuyên thẻ như renderer
# --------------------------------------------------------------------------- #
#
# Renderer gắn thẻ mở vào token sau nó và đặt khoảng trắng TRƯỚC thẻ, nên lượt
# thoại thứ hai in nghiêng ra `好！ <i>- 好。`. Trước đây validator nhìn đúng ký tự
# đứng trước `-` (là `>`), báo MARKER_SPACING + SPACE_AROUND_PUNCT cho chính file
# `srtgen fix` vừa ghi, và khuyên đổi sang dạng sai `<i> - 好`.

@pytest.mark.parametrize(
    "zh, py",
    [
        ("- 你 好！ <i>- 好。</i>", "- Nǐ hǎo！ <i>- Hǎo。</i>"),
        ("<i>- 你 好！</i> <i>- 好。</i>", "<i>- Nǐ hǎo！</i> <i>- Hǎo。</i>"),
        ("<i>- 你 好！</i> - 好。", "<i>- Nǐ hǎo！</i> - Hǎo。"),
        ("- 你 好！ <b><i>- 好。</i></b>", "- Nǐ hǎo！ <b><i>- Hǎo。</i></b>"),
    ],
)
def test_validator_accepts_a_mid_line_marker_after_an_opening_tag(zh: str, py: str) -> None:
    from srtgen.core.rules import validate_text

    assert [f for f in validate_text(_block(zh, py)) if f.severity == "error"] == []


def test_run_fix_output_with_a_tagged_mid_line_marker_is_clean(cfg: dict[str, Any]) -> None:
    """Block chỉ có dòng Hán: sinh pinyin, giữ thẻ, và file ra qua được bộ kiểm."""
    from srtgen.core.rules import validate_text
    from srtgen.pipeline import run_fix

    fixed, _ = run_fix(_block("<i>- 你 好！</i> <i>- 好。</i>"), cfg)
    assert _lines(fixed) == ("<i>- 你 好！</i> <i>- 好。</i>", "<i>- Nǐ hǎo！</i> <i>- Hǎo。</i>")
    assert [f for f in validate_text(fixed) if f.severity == "error"] == []


def test_validator_still_catches_a_missing_space_before_a_tagged_marker() -> None:
    """Nhìn xuyên thẻ không có nghĩa là bỏ qua: thiếu khoảng trắng trước thẻ vẫn là lỗi,
    và lời khuyên đặt khoảng trắng TRƯỚC thẻ chứ không phải `<i> - `."""
    from srtgen.core.rules import validate_text

    findings = validate_text(_block("- 你 好！<i>- 好。</i>", "- Nǐ hǎo！<i>- Hǎo。</i>"))
    spacing = [f for f in findings if f.code == "MARKER_SPACING"]
    assert len(spacing) == 2
    assert "好！ <i>- 好" in spacing[0].message
    assert "<i> -" not in spacing[0].message
