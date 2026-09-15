"""Bộ tách dùng chung (hợp đồng H5): S5 và nút "Sinh lại pinyin" tách một câu một kiểu.

Hai điều được khoá lại ở đây:

* jieba chạy với ``HMM=False``, và công tắc nằm trong ``default.yaml``
  (``tokenize.jieba_hmm``) chứ không nằm cứng trong code. HMM tự bịa từ không có
  thật: 我来中国只有一个目的 → 我来 | 中国, 光头强来了 → 强来.
* ``segment_line`` là ĐÚNG bộ tách của chặng S5 — cùng từ điển tên, cùng công tắc.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from srtgen import io_utils
from srtgen.core.context import load_config
from srtgen.core.srt import parse_srt
from srtgen.core.token import KIND_WORD, Cue, Document, Token
from srtgen.stages import s5_tokenize
from srtgen.stages.s5_tokenize import jieba_hmm, segment_line, segment_tokens, tokenize_document

REVIEWED = "我来中国只有一个目的"
REVIEWED_SPLIT = ["我", "来", "中国", "只有", "一个", "目的"]


def _words_of_stage(lines: list[str], cfg: dict[str, Any]) -> list[list[str]]:
    """Cho các dòng chữ Hán thô đi qua đúng ``tokenize_document`` của S5."""
    doc = Document(
        cues=[
            Cue(index=i, start=float(i), end=float(i) + 1.0, tokens=[Token(kind=KIND_WORD, zh=line)])
            for i, line in enumerate(lines, start=1)
        ],
        meta={"segmented": False},
    )
    tokenize_document(doc, cfg)
    return [[t.zh for t in cue.tokens if t.kind == KIND_WORD] for cue in doc.cues]


def test_reviewer_sentence_has_six_clusters() -> None:
    assert segment_line(REVIEWED) == REVIEWED_SPLIT


def test_whole_name_is_never_glued_to_the_next_word() -> None:
    pieces = segment_line("光头强来了", {"光头强": ["光头", "强"]})
    assert "强来" not in pieces
    assert pieces[:2] == ["光头", "强"]


def test_names_json_payload_is_accepted_as_is(tmp_path: Path) -> None:
    """Bảng tên đọc từ names.json (định dạng S7 ghi) dùng thẳng được, không phải đổi."""
    from srtgen.stages import s7_ai

    path = tmp_path / "names.json"
    entry = s7_ai.NameEntry(han="光头强", split=["光头", "强"], pinyin=["Guāngtóu", "Qiáng"], type="person", vi="")
    s7_ai.save_names(path, {"光头": "Guāngtóu", "强": "Qiáng"}, [entry])

    pieces = segment_line("他是光头强吗", io_utils.read_json(path))
    assert "强吗" not in pieces and pieces[-3:] == ["光头", "强", "吗"]


def test_punctuation_and_markers_come_back_in_order() -> None:
    assert segment_line("你好，世界。") == ["你好", "，", "世界", "。"]
    kinds = [t.kind for t in segment_tokens("- 你好")]
    assert kinds[0] != KIND_WORD and kinds[-1] == KIND_WORD


def test_hmm_switch_lives_in_default_yaml_and_is_off() -> None:
    assert load_config()["tokenize"][s5_tokenize.HMM_KEY] is False
    assert jieba_hmm() is False
    assert jieba_hmm({}) is False, "Cấu hình thiếu khoá thì theo default.yaml."
    assert jieba_hmm({"tokenize": {"jieba_hmm": "false"}}) is False
    assert jieba_hmm({"tokenize": {"jieba_hmm": True}}) is True


def test_stage_cuts_without_hmm_and_honours_the_config(cfg: dict[str, Any]) -> None:
    """S5 tách 6 cụm như người soát; bật khoá lên thì HMM quay lại (khoá được đọc thật)."""
    assert _words_of_stage([REVIEWED], cfg) == [REVIEWED_SPLIT]
    hmm_on = {**cfg, "tokenize": {**dict(cfg.get("tokenize") or {}), "jieba_hmm": True}}
    assert _words_of_stage([REVIEWED], hmm_on)[0][0] == "我来"
    assert segment_line(REVIEWED, cfg=hmm_on)[0] == "我来", "segment_line đọc cùng khoá."


def test_segment_line_is_exactly_the_stage_cutter(raw_text: str, cfg: dict[str, Any]) -> None:
    """Trên 300 câu thô đầu corpus: segment_line và S5 cho cùng từng cụm.

    Tắt gộp 儿化音 để so riêng phần tách từ (gộp 儿 là bước 3, sau bước tách).
    """
    lines = [" ".join(block.lines) for block in parse_srt(raw_text)[:300]]
    settings = {**cfg, "erhua": {**dict(cfg.get("erhua") or {}), "enabled": False}}
    stage = _words_of_stage(lines, settings)
    shared = [[t.zh for t in segment_tokens(line, cfg=settings) if t.kind == KIND_WORD] for line in lines]
    diff = [(i + 1, a, b) for i, (a, b) in enumerate(zip(stage, shared)) if a != b]
    assert not diff, diff[:5]


# --------------------------------------------------------------------------- #
# số đo trên toàn corpus
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def han_match(raw_text: str, completed_text: str) -> dict[str, float]:
    """Tỉ lệ khớp của fix(raw.srt) với completed.srt trên 1312 cue sạch.

    "Cue sạch" = cue không nằm trong ``KNOWN_ERRORS`` (lỗi đã biết của corpus).
    Dòng Hán so nguyên văn; dòng pinyin so không phân biệt hoa/thường.
    """
    from srtgen.pipeline import run_fix
    from tests.known_corpus_errors import KNOWN_ERRORS

    fixed, _ = run_fix(raw_text)
    ours, ref = parse_srt(fixed), parse_srt(completed_text)
    known = set().union(*KNOWN_ERRORS.values())
    clean = [i for i in range(1, len(ref) + 1) if i not in known]
    han = sum(ours[i - 1].lines[0] == ref[i - 1].lines[0] for i in clean)
    py = sum(ours[i - 1].lines[1].lower() == ref[i - 1].lines[1].lower() for i in clean)
    return {"clean": len(clean), "han": han / len(clean), "py": py / len(clean)}


def test_corpus_match_with_hmm_off(han_match: dict[str, float]) -> None:
    """Đo được: HMM bật 75.8% / 74.4%, HMM tắt 78.4% / 76.8%. Tụt về mức cũ là hỏng."""
    assert han_match["clean"] == 1312
    assert han_match["han"] >= 0.78, han_match
    assert han_match["py"] >= 0.765, han_match
