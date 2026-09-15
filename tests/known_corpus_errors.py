"""Danh sách lỗi ĐÃ XÁC ĐỊNH của ``corpus/completed.srt``.

Vì sao file này tồn tại
=======================
``corpus/completed.srt`` là bản đã được người biên tập soát tay, nên theo phản xạ
tự nhiên ai cũng coi nó là "đáp án đúng". Nhưng nguồn sự thật về định dạng đầu ra
là ``docs/format-contract.md`` (README), **không phải** corpus. Ở đúng những chỗ
hai thứ đó nói khác nhau, README thắng — và corpus sai.

Nếu không ghi lại danh sách này thì bộ hồi quy chỉ có hai lựa chọn, cả hai đều tệ:

* coi mọi khác biệt so với corpus là lỗi của tool → phải sửa tool cho *sai theo*
  corpus, tức là cố tình vi phạm README;
* hoặc bỏ hẳn phép so với corpus → mất luôn thước đo duy nhất có thật.

Danh sách dưới đây là lối thoát thứ ba: **trừ đi đúng những ca đã biết, rồi bắt
buộc phần còn lại phải bằng 0**. Một cảnh báo mới xuất hiện ngoài danh sách này
nghĩa là validator vừa báo oan (false positive) — và đó là lỗi phải sửa ngay.

Mọi con số dưới đây là ĐO ĐƯỢC, không phải đoán: chúng là kết quả của
``validate_text(read_text("corpus/completed.srt"))`` gom theo ``(code, cue_index)``.

Cách dùng
=========
    from tests.known_corpus_errors import unexpected
    lạ = unexpected(validate_text(text))
    assert not lạ, ...
"""

from __future__ import annotations

from typing import Any, Iterable

__all__ = [
    "CUM_MISMATCH_CUES",
    "MARKER_SPACING_CUES",
    "ERHUA_CONTRACTION_CUES",
    "ERHUA_CONTRACTION_EXTRA_CUES",
    "ALL_ERHUA_CONTRACTION_CUES",
    "PUNCT_SYNC_CUES",
    "MARKER_SYNC_CUES",
    "NAME_INCONSISTENT_CUES",
    "KNOWN_ERRORS",
    "KNOWN_WARNINGS",
    "KNOWN_BY_CODE",
    "CASING_AMBIGUOUS_NOTE",
    "is_known",
    "unexpected",
    "group_by_code",
]


# --------------------------------------------------------------------------- #
# 1. Lệch số cụm giữa dòng Hán và dòng pinyin
# --------------------------------------------------------------------------- #

#: 8 cue mà dòng Hán và dòng pinyin không còn cùng số cụm.
#:
#: Đây là lỗi corpus, không phải lỗi tool, vì README mục 1 nói thẳng: "Nếu số
#: lượng cụm Chinese và pinyin không bằng nhau sau khi loại dấu câu, phải báo lỗi
#: để kiểm tra thủ công". Tool làm đúng như vậy — nó BÁO chứ không đoán bừa.
#: ``merge_zh_py`` trả ``pinyin=None`` cho toàn bộ cue, và S5 sinh lại pinyin.
#: Nếu tool "sửa" được các cue này bằng cách kéo pinyin của cụm trước sang cụm
#: sau thì đó mới là lỗi — README cấm đúng hành vi đó.
CUM_MISMATCH_CUES: tuple[int, ...] = (2, 89, 340, 369, 729, 1038, 1280, 1324)


# --------------------------------------------------------------------------- #
# 2. Marker đổi người nói thiếu khoảng trắng
# --------------------------------------------------------------------------- #

#: 11 cue mà corpus viết dính ``。-`` thay vì ``。 - ``.
#:
#: README mục 3, phần "Marker đổi người nói", nói rõ đây là **ngoại lệ cấu trúc**:
#: "Nếu ngay trước marker ``-`` là dấu câu như ``……`` hoặc ``！``, vẫn phải giữ
#: đúng 1 dấu cách giữa dấu câu và marker." Nghĩa là luật "không có space sau dấu
#: câu" KHÔNG áp cho marker. Corpus bỏ mất khoảng trắng đó ở 11 chỗ; renderer của
#: tool phát ra đúng 1 space nên hai bên lệch nhau — và tool là bên đúng.
#:
#: Cue 1296 có hai marker và chỉ cái thứ hai bị báo. Cái đầu đứng ngay sau dấu
#: mở ``‘`` — đó vẫn là chỗ mở lượt thoại nên ĐƯỢC miễn trừ, vì chèn khoảng
#: trắng vào đó sẽ phạm luật "không có khoảng trắng sau dấu mở". Cái thứ hai viết
#: dính ``。-`` giữa dòng mới là vi phạm, đúng cùng một kiểu với 10 cue còn lại.
MARKER_SPACING_CUES: tuple[int, ...] = (
    84, 103, 550, 580, 654, 1071, 1079, 1080, 1138, 1178, 1296,
)


# --------------------------------------------------------------------------- #
# 3. 儿化音 viết dạng đầy đủ thay vì dạng rút gọn
# --------------------------------------------------------------------------- #

#: 7 cue mà ``docs/build-spec.md`` mục 13 đã liệt kê sẵn.
#:
#: Corpus ghi ``nǎér``/``zhèér``/``yīhuìer``; README mục 1 chốt dạng rút gọn
#: ``nǎr``/``zhèr``/``yíhuìr`` và nói thẳng "không được tách ``儿`` thành một từ
#: hoặc một segment riêng". Đây là lỗi corpus.
ERHUA_CONTRACTION_CUES: tuple[int, ...] = (143, 158, 348, 349, 350, 510, 729)

#: 12 cue **cùng loại lỗi** mà build-spec chưa liệt kê, đo được khi chạy validator
#: trên toàn bộ file: ``méishìér``, ``zhèhuìer``, ``nàér``, ``jīnér``,
#: ``dàihuìer``, ``xiǎoháiér``, ``děnghuìer``, ``yìdiǎnér``, ``shìér``…
#:
#: Tách riêng khỏi danh sách trên để giữ nguyên con số trong build-spec (một tài
#: liệu là hợp đồng, không sửa lén), đồng thời vẫn cho bộ hồi quy biết sự thật.
#: Ai cập nhật build-spec thì gộp hai danh sách này lại.
ERHUA_CONTRACTION_EXTRA_CUES: tuple[int, ...] = (
    536, 617, 727, 753, 774, 814, 825, 826, 858, 1016, 1142, 1309,
)

#: Toàn bộ 19 cue 儿化音 sai của corpus — con số dùng cho phép trừ.
ALL_ERHUA_CONTRACTION_CUES: tuple[int, ...] = tuple(
    sorted(set(ERHUA_CONTRACTION_CUES) | set(ERHUA_CONTRACTION_EXTRA_CUES))
)


# --------------------------------------------------------------------------- #
# 4. Hai lỗi corpus phát hiện thêm khi soi round-trip
# --------------------------------------------------------------------------- #

#: Cue 268 — dấu câu lệch giữa hai dòng.
#: Dòng Hán ``“爆破” - 马 拉松 著。`` có cặp ngoặc kép ``“”``; dòng pinyin
#: ``Bàopò - Mǎ Lāsōng zhù。`` đánh rơi cả cặp. README checklist mục 9 đòi
#: "vị trí và loại dấu câu đồng bộ giữa Chinese và pinyin".
PUNCT_SYNC_CUES: tuple[int, ...] = (268,)

#: Cue 895 — marker lệch giữa hai dòng.
#: Dòng Hán ``- 袁来。”`` có marker đổi người nói; dòng pinyin ``Yuánlái。”``
#: không có. README checklist mục 10 đòi hai dòng đồng bộ marker.
MARKER_SYNC_CUES: tuple[int, ...] = (895,)


# --------------------------------------------------------------------------- #
# 5. Tên riêng / cụm từ lúc gộp lúc tách (mức cảnh báo)
# --------------------------------------------------------------------------- #

#: 14 cue sinh cảnh báo ``NAME_INCONSISTENT`` (15 finding — cue 4 sinh 2).
#:
#: Đây là lỗi thật của corpus theo README mục 6 ("Không được cùng một tên mà lúc
#: gộp, lúc tách khác nhau trong cùng file"), ví dụ ``龙拳`` ở cue 4 viết tách
#: nhưng cue 1280 viết liền. Mức ``warn`` nên không chặn cổng cứng, nhưng vẫn
#: liệt kê ở đây để một cảnh báo MỚI không lọt qua bộ hồi quy mà không ai biết.
NAME_INCONSISTENT_CUES: tuple[int, ...] = (
    4, 79, 124, 125, 178, 188, 224, 237, 249, 701, 769, 878, 942, 963,
)


# --------------------------------------------------------------------------- #
# 6. Ghi chú không phải finding
# --------------------------------------------------------------------------- #

#: 37 cue đứng ngay sau một cue kết thúc bằng ``……``.
#:
#: Corpus tự mâu thuẫn ở đây (54% viết hoa / 46% viết thường) nên không có đáp
#: án đúng để so. Bộ hồi quy LOẠI TRỪ các cue này khỏi phép đo độ chính xác viết
#: hoa; ``apply_casing`` gắn cờ ``CASE_AMBIG`` để người duyệt tự quyết.
CASING_AMBIGUOUS_NOTE = (
    "Cue đứng sau '……' được loại khỏi phép đo viết hoa: corpus 54/46, "
    "không có đáp án đúng."
)


# --------------------------------------------------------------------------- #
# bảng tra
# --------------------------------------------------------------------------- #

#: mã lỗi mức ``error`` -> tập cue đã biết là lỗi corpus.
KNOWN_ERRORS: dict[str, frozenset[int]] = {
    "CUM_MISMATCH": frozenset(CUM_MISMATCH_CUES),
    "MARKER_SPACING": frozenset(MARKER_SPACING_CUES),
    "ERHUA_SPLIT": frozenset(ALL_ERHUA_CONTRACTION_CUES),
    "PUNCT_SYNC": frozenset(PUNCT_SYNC_CUES),
    "MARKER_SYNC": frozenset(MARKER_SYNC_CUES),
}

#: mã lỗi mức ``warn`` -> tập cue đã biết.
KNOWN_WARNINGS: dict[str, frozenset[int]] = {
    "NAME_INCONSISTENT": frozenset(NAME_INCONSISTENT_CUES),
}

KNOWN_BY_CODE: dict[str, frozenset[int]] = {**KNOWN_ERRORS, **KNOWN_WARNINGS}


def is_known(code: str, cue_index: int | None) -> bool:
    """Cặp ``(mã lỗi, số cue)`` này đã được xác định là lỗi của corpus chưa?

    Kiểm theo cặp chứ không chỉ theo mã: một ``ERHUA_SPLIT`` ở cue 999 vẫn phải
    làm test đỏ, dù mã ``ERHUA_SPLIT`` có mặt trong bảng. Bỏ qua theo mã sẽ biến
    danh sách này thành cái van xả cho mọi lỗi cùng loại về sau.
    """
    cues = KNOWN_BY_CODE.get(code)
    return cues is not None and cue_index in cues


def unexpected(findings: Iterable[Any]) -> list[Any]:
    """Lọc ra những finding KHÔNG nằm trong danh sách đã biết.

    Danh sách trả về rỗng chính là chỉ số "false positive = 0" của kế hoạch.
    Nhận bất kỳ đối tượng nào có ``.code`` và ``.cue_index`` để không buộc
    ``tests`` phải import ``srtgen.core.rules`` chỉ vì một cái type hint.
    """
    return [f for f in findings if not is_known(f.code, f.cue_index)]


def group_by_code(findings: Iterable[Any]) -> dict[str, list[int | None]]:
    """Gom finding theo mã, giữ số cue đã sắp xếp — dùng để in số liệu.

    Cần thiết vì ``MARKER_SPACING`` sinh 2 finding cho mỗi cue (một cho dòng Hán,
    một cho dòng pinyin): đếm finding sẽ ra 22 còn đếm cue mới ra 11, và mọi con
    số trong build-spec là **đếm theo cue**.
    """
    out: dict[str, set[int | None]] = {}
    for f in findings:
        out.setdefault(f.code, set()).add(f.cue_index)
    return {
        code: sorted(cues, key=lambda v: (v is None, v))
        for code, cues in sorted(out.items())
    }
