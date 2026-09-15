"""Cổng đo chất lượng thật — tầng 2 của build-spec mục 13.

Đây là file kiểm quan trọng nhất của dự án, và nó khác mọi file còn lại ở một
điểm: **nó không kiểm hành vi, nó đo chất lượng**. Ba con số dưới đây là ba câu
trả lời cho ba câu hỏi mà người dùng thật sự hỏi:

1. *"Tool sửa được file bên dịch gửi sang không?"*
   → chạy ``fix`` trên ``corpus/filter.srt`` rồi kiểm lại: phải **0 lỗi mức
   error**. Đây là **cổng cứng**, không có ngưỡng, không có ngoại lệ. File sau
   khi ``fix`` mà vẫn còn lỗi thì lệnh ``fix`` không hoàn thành việc của nó.

2. *"Tool đoán viết hoa giỏi bằng người biên tập không?"*
   → so từng cue với ``corpus/completed.srt``: phải đạt **≥ 97%**, mục tiêu đo
   được là 99.16%. Đây là ngưỡng chứ không phải cổng cứng, vì luật viết hoa dựa
   vào dấu câu của cue trước, mà chính corpus cũng có chỗ tự mâu thuẫn.

3. *"Validator có báo oan không?"*
   → chạy validator trên bản đã soát tay: mọi finding phải nằm trong
   ``tests/known_corpus_errors.py``. Một finding ngoài danh sách = **false
   positive**, và đó là lỗi phải sửa ngay, không phải thứ để nới ngưỡng.

Vì sao câu 3 lại được đặt ngược đời như vậy — lấy một file *có lỗi* làm thước đo
cho việc *không được báo oan*: ``completed.srt`` là bản người biên tập đã soát
tay, nên gần như mọi dòng của nó đều đúng. Nguồn sự thật vẫn là
``docs/format-contract.md``, và đúng ở những chỗ hai bên nói khác nhau thì corpus
sai — 51 finding mức ``error`` đã được soi tay từng cái và ghi vào
``known_corpus_errors.py``. Phần còn lại của file phải sạch. Nếu validator sinh
thêm một finding thứ 52 thì hoặc corpus có lỗi chưa ai biết (hiếm), hoặc
validator vừa báo oan (thường) — cả hai đều đáng dừng lại xem.

**Số liệu được in ra** chứ không chỉ pass/fail: chạy ``python -m pytest tests
-s`` để đọc. Một cổng chỉ nói "xanh" không cho biết nó đang ở sát ngưỡng hay còn
cách xa, mà đó lại là thứ duy nhất giúp thấy chất lượng đang trôi đi trước khi
nó trôi qua ngưỡng.
"""

from __future__ import annotations

import sys
from typing import Any, Sequence

import pytest

from srtgen.core.rules import SEVERITY_ERROR, validate_text
from tests.known_corpus_errors import (
    CASING_AMBIGUOUS_NOTE,
    KNOWN_BY_CODE,
    group_by_code,
    unexpected,
)

#: Ngưỡng của build-spec mục 13. Mục tiêu đo được là 99.16%; ngưỡng đặt ở 97% để
#: một thay đổi nhỏ trong jieba không làm đỏ cả bộ test, nhưng một luật viết hoa
#: bị hỏng thật thì rơi xuống dưới 90% ngay.
CASING_MIN_ACCURACY = 97.0

#: Dấu lửng: cue đứng ngay sau nó bị **loại khỏi phép đo**, xem
#: ``CASING_AMBIGUOUS_NOTE``. Corpus chia 54/46 nên không có đáp án đúng để so.
ELLIPSIS = "……"


# --------------------------------------------------------------------------- #
# in số liệu
# --------------------------------------------------------------------------- #

def say(text: str) -> None:
    """In một dòng số liệu, chịu được cửa sổ lệnh không hiểu Unicode.

    Terminal Windows mặc định dùng bảng mã cp1252, và một chữ ``ǎ`` lọt vào
    ``print()`` sẽ ném ``UnicodeEncodeError`` — tức là bộ test đỏ vì cái máy in
    chữ chứ không phải vì chất lượng phụ đề. Thay ký tự không in được còn hơn
    mất cả con số.
    """
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
    print(text)


def say_table(title: str, rows: Sequence[tuple[str, Any]]) -> None:
    say("")
    say(f"--- {title} ---")
    width = max((len(name) for name, _ in rows), default=0)
    for name, value in rows:
        say(f"  {name.ljust(width)} : {value}")


# --------------------------------------------------------------------------- #
# 1. CỔNG CỨNG — fix(filter.srt) phải sạch lỗi
# --------------------------------------------------------------------------- #

def test_fix_of_filter_srt_has_zero_errors(fixed_filter_srt: str) -> None:
    """``fix(corpus/filter.srt)`` rồi kiểm lại phải ra **0 finding mức error**.

    Không có ngưỡng ở đây, và cố ý không có: lệnh ``fix`` tồn tại để giao ra một
    file dùng được ngay. "Còn 3 lỗi" nghĩa là người dùng vẫn phải mở Aegisub sửa
    tay, tức là lệnh ấy chưa làm xong việc của nó.

    Thông báo lỗi liệt kê từng finding kèm số cue: một cổng đỏ mà chỉ nói
    "10 != 0" buộc người sửa phải tự đi đo lại từ đầu.
    """
    findings = validate_text(fixed_filter_srt)
    errors = [f for f in findings if f.severity == SEVERITY_ERROR]

    say_table(
        "CỔNG CỨNG — fix(corpus/filter.srt)",
        [
            ("lỗi mức error", f"{len(errors)}   (bắt buộc = 0)"),
            ("tổng số finding", len(findings)),
            ("theo mã", {code: len(cues) for code, cues in group_by_code(findings).items()}),
        ],
    )

    assert not errors, "\n".join(
        [f"Còn {len(errors)} lỗi sau khi chuẩn hoá — cổng cứng không đạt:"]
        + [f"  cue {f.cue_index}: [{f.code}] {f.message}" for f in errors[:20]]
    )


def test_fix_keeps_every_cue(fixed_filter_srt: str, filter_text: str) -> None:
    """Chuẩn hoá **không được** làm mất hay thêm cue.

    Đi kèm cổng cứng vì nó chặn đúng một cách gian lận: xoá những cue có lỗi thì
    "0 lỗi" đạt ngay, mà phim thì mất thoại. Số cue là bất biến rẻ nhất để chốt
    lại chuyện đó.
    """
    from srtgen.core.srt import parse_srt

    before = len(parse_srt(filter_text))
    after = len(parse_srt(fixed_filter_srt))
    say_table("Số cue qua lệnh fix", [("trước", before), ("sau", after)])
    assert after == before


# --------------------------------------------------------------------------- #
# 2. Độ chính xác viết hoa
# --------------------------------------------------------------------------- #

def first_letter(line: str) -> str:
    """Chữ cái đầu tiên của một dòng pinyin, bỏ qua marker và dấu câu mở đầu."""
    for ch in line:
        if ch.isalpha():
            return ch
    return ""


def measure_casing(fixed_text: str, reference_text: str) -> dict[str, Any]:
    """So chữ hoa/thường của chữ cái đầu dòng pinyin, cue với cue.

    Ba quyết định đáng ghi lại:

    * So **hoa hay thường**, không so cả chuỗi: chỗ này chỉ đo luật viết hoa,
      còn cách tách cụm hay chọn âm là việc của phép đo khác. Trộn chúng vào một
      con số sẽ cho một con số không sửa được gì.
    * **Loại cue đứng sau ``……``**: corpus tự mâu thuẫn 54/46 ở đó, nên mỗi ca
      trong nhóm ấy vừa có thể tính là đúng vừa có thể tính là sai. Giữ lại chỉ
      làm con số nhiễu thêm 37 ca vô nghĩa.
    * Dấu lửng đọc từ **bản tham chiếu**: đó là bản người biên tập đã chốt, và
      phép đo phải hỏi đáp án chứ không hỏi bài làm.
    """
    from srtgen.core.srt import parse_srt

    ours = parse_srt(fixed_text)
    theirs = parse_srt(reference_text)

    compared = same = skipped = 0
    misses: list[tuple[int, str, str]] = []

    for position, (mine, ref) in enumerate(zip(ours, theirs), start=1):
        previous = theirs[position - 2].lines[0] if position > 1 else ""
        if previous.rstrip().endswith(ELLIPSIS):
            skipped += 1
            continue
        my_letter = first_letter(mine.lines[1] if len(mine.lines) > 1 else "")
        ref_letter = first_letter(ref.lines[1] if len(ref.lines) > 1 else "")
        if not my_letter or not ref_letter:
            continue
        compared += 1
        if my_letter.isupper() == ref_letter.isupper():
            same += 1
        else:
            misses.append((position, mine.lines[1], ref.lines[1]))

    return {
        "cues_ours": len(ours),
        "cues_reference": len(theirs),
        "compared": compared,
        "same": same,
        "skipped": skipped,
        "accuracy": (100.0 * same / compared) if compared else 0.0,
        "misses": misses,
    }


def test_casing_accuracy_against_the_hand_checked_corpus(
    fixed_filter_srt: str, completed_text: str
) -> None:
    """Viết hoa phải khớp bản soát tay ở mức ≥ 97% (đo được: 99.16%).

    Ngưỡng chứ không phải cổng cứng: luật viết hoa suy từ dấu câu của cue trước,
    mà chính người biên tập cũng không nhất quán tuyệt đối. Đòi 100% ở đây là
    đòi tool sai theo đúng những chỗ corpus sai.
    """
    result = measure_casing(fixed_filter_srt, completed_text)

    say_table(
        "Độ chính xác viết hoa so với corpus/completed.srt",
        [
            ("số cue so được", result["compared"]),
            ("khớp", result["same"]),
            ("lệch", len(result["misses"])),
            ("bỏ qua (sau '……')", f"{result['skipped']}   — {CASING_AMBIGUOUS_NOTE}"),
            ("độ chính xác", f"{result['accuracy']:.2f}%   (ngưỡng ≥ {CASING_MIN_ACCURACY}%)"),
        ],
    )
    for cue_index, mine, ref in result["misses"][:10]:
        say(f"      cue {cue_index}: tool='{mine[:38]}' | corpus='{ref[:38]}'")

    assert result["compared"] > 1000, (
        f"Chỉ so được {result['compared']} cue — quá ít để con số có nghĩa. "
        "Nhiều khả năng hai file đã lệch cue chứ không phải viết hoa sai."
    )
    assert result["accuracy"] >= CASING_MIN_ACCURACY, (
        f"Độ chính xác viết hoa tụt xuống {result['accuracy']:.2f}%, "
        f"dưới ngưỡng {CASING_MIN_ACCURACY}%."
    )


def test_fixed_file_lines_up_with_the_reference(
    fixed_filter_srt: str, completed_text: str
) -> None:
    """Hai file phải cùng số cue thì phép đo trên mới có nghĩa.

    Tách khỏi phép đo vì hai thứ hỏng theo hai kiểu: lệch cue làm con số vô
    nghĩa (mọi cue sau đó bị so với cue khác), còn viết hoa sai thì chỉ làm con
    số thấp đi. Trộn vào một test sẽ báo "viết hoa kém" cho một lỗi lệch dòng.
    """
    from srtgen.core.srt import parse_srt

    ours = len(parse_srt(fixed_filter_srt))
    theirs = len(parse_srt(completed_text))
    assert ours == theirs, (
        f"fix(filter.srt) ra {ours} cue nhưng completed.srt có {theirs} cue — "
        "hai file không so được với nhau nữa."
    )


# --------------------------------------------------------------------------- #
# 3. False positive của validator = 0
# --------------------------------------------------------------------------- #

def test_validator_reports_no_false_positive_on_the_reference(
    completed_findings: list[Any],
) -> None:
    """Mọi finding trên bản soát tay phải nằm trong danh sách lỗi corpus đã biết.

    Đây là phép đo "validator có báo oan không", và nó phải bằng **0**. Danh
    sách đã biết được soi tay từng cue một; một finding ngoài danh sách nghĩa là
    validator vừa bắt một dòng mà người biên tập viết đúng — mức nguy hiểm cao
    hơn bỏ sót, vì nó dạy người dùng thói quen bỏ qua báo cáo.

    Kiểm theo cặp ``(mã, số cue)`` chứ không theo mã: một ``ERHUA_SPLIT`` ở cue
    lạ vẫn phải đỏ, dù mã đó có mặt trong bảng.
    """
    strangers = unexpected(completed_findings)

    say_table(
        "False positive trên corpus/completed.srt",
        [
            ("tổng finding", len(completed_findings)),
            ("ngoài danh sách đã biết", f"{len(strangers)}   (bắt buộc = 0)"),
        ],
    )

    assert not strangers, "\n".join(
        ["Validator báo oan (finding không có trong known_corpus_errors.py):"]
        + [f"  cue {f.cue_index}: [{f.code}] {f.message}" for f in strangers[:20]]
    )


def test_known_corpus_errors_are_all_still_there(completed_findings: list[Any]) -> None:
    """Chiều ngược lại: 51 lỗi thật của corpus vẫn phải bị bắt.

    Không có test này thì cách dễ nhất để làm mọi thứ xanh là tắt bớt luật —
    "0 false positive" đạt ngay lập tức, và validator thành một hàm trả về danh
    sách rỗng. Danh sách đã biết vì vậy vừa là trần vừa là sàn.
    """
    found = group_by_code(completed_findings)
    missing: list[str] = []
    for code, cues in KNOWN_BY_CODE.items():
        got = set(found.get(code, ()))
        gone = sorted(cues - got)
        if gone:
            missing.append(f"{code}: không còn bắt được cue {gone}")

    say_table(
        "Lỗi corpus đã biết (đếm theo cue)",
        [(code, f"{len(found.get(code, ()))} / {len(cues)}") for code, cues in KNOWN_BY_CODE.items()],
    )

    assert not missing, "\n".join(["Validator đã ngừng bắt lỗi corpus đã biết:"] + missing)


def test_error_and_warning_counts_match_the_audit(completed_findings: list[Any]) -> None:
    """Con số đã soi tay: 51 finding mức ``error``, phần còn lại là cảnh báo.

    Ghi lại con số tuyệt đối chứ không chỉ ghi "mọi thứ đều đã biết": hai lỗi
    mới xuất hiện đúng lúc hai lỗi cũ biến mất sẽ lọt qua mọi phép kiểm theo tập
    hợp, nhưng không lọt qua một phép đếm.
    """
    errors = [f for f in completed_findings if f.severity == SEVERITY_ERROR]
    warns = [f for f in completed_findings if f.severity != SEVERITY_ERROR]

    say_table(
        "Phân loại finding trên corpus/completed.srt",
        [
            ("mức error", f"{len(errors)}   (đã soi tay: 51)"),
            ("mức warn/info", len(warns)),
            ("theo mã (đếm theo cue)", {c: len(v) for c, v in group_by_code(completed_findings).items()}),
        ],
    )

    assert len(errors) == 51, (
        f"Số lỗi mức error trên corpus đổi từ 51 thành {len(errors)}. "
        "Hoặc validator vừa đổi hành vi, hoặc corpus vừa bị sửa — cả hai đều "
        "cần người xem lại trước khi cập nhật con số này."
    )


# --------------------------------------------------------------------------- #
# 4. Bảng tổng kết
# --------------------------------------------------------------------------- #

def test_print_regression_summary(
    fixed_filter_srt: str, completed_text: str, completed_findings: list[Any]
) -> None:
    """Không kiểm gì mới — chỉ in gọn ba con số vào một chỗ để đọc.

    Ba phép đo ở trên nằm rải trong ba test và mỗi test chỉ in phần của nó. Khi
    ai đó chạy bộ hồi quy để trả lời câu "chất lượng đang ở đâu", họ cần ba con
    số cạnh nhau chứ không phải ba khối rời nhau giữa hàng trăm dòng.
    """
    errors = [f for f in validate_text(fixed_filter_srt) if f.severity == SEVERITY_ERROR]
    casing = measure_casing(fixed_filter_srt, completed_text)
    strangers = unexpected(completed_findings)

    say_table(
        "TỔNG KẾT HỒI QUY",
        [
            ("1. Cổng cứng fix(filter.srt)", f"{len(errors)} lỗi   -> {'ĐẠT' if not errors else 'KHÔNG ĐẠT'}"),
            (
                "2. Độ chính xác viết hoa",
                f"{casing['accuracy']:.2f}%  ({casing['same']}/{casing['compared']} cue, "
                f"bỏ {casing['skipped']} ca nhập nhằng)",
            ),
            (
                "3. False positive validator",
                f"{len(strangers)} / {len(completed_findings)} finding",
            ),
        ],
    )
    say("")


# --------------------------------------------------------------------------- #
# 5. Cổng cứng trên ĐÚNG đường của lệnh `srtgen fix` — pipeline.run_fix
# --------------------------------------------------------------------------- #
#
# Vì sao cần thêm khi đã có mục 1: trước đây fixture ``fixed_filter_srt`` dựng từ
# một bản sao tay của lệnh fix (đặt ``meta["segmented"] = True`` cho cả file) và
# lệch ``pipeline.run_fix`` ở 43/1351 cue của filter.srt. Nay ``conftest.fix_srt_text``
# và ``fixed_filter_srt`` chỉ gọi ``run_fix`` -> ``run_fix_bilingual`` (một logic duy
# nhất, xem ``test_bilingual_fix.py``), nhưng cổng vẫn gọi thẳng ``run_fix`` trên cả
# filter.srt lẫn raw.srt: README ghi cổng là ``run_fix(corpus/filter.srt)``, và kiểm
# đúng hàm người dùng chạy thì không phụ thuộc vào việc fixture có còn trung thành.

def _run_fix_errors(text: str, cfg: dict[str, Any]) -> tuple[str, list[Any]]:
    from srtgen.pipeline import run_fix

    fixed, _ = run_fix(text, cfg)
    return fixed, [f for f in validate_text(fixed) if f.severity == SEVERITY_ERROR]


@pytest.mark.parametrize("which", ["filter", "raw"])
def test_run_fix_on_corpus_has_zero_errors(
    which: str, filter_text: str, raw_text: str, cfg: dict[str, Any]
) -> None:
    """``run_fix`` trên filter.srt và raw.srt rồi kiểm lại: 0 lỗi, không mất cue."""
    from srtgen.core.srt import parse_srt

    source = filter_text if which == "filter" else raw_text
    fixed, errors = _run_fix_errors(source, cfg)

    say_table(
        f"CỔNG CỨNG — run_fix(corpus/{which}.srt)",
        [("lỗi mức error", f"{len(errors)}   (bắt buộc = 0)")],
    )
    assert not errors, "\n".join(
        [f"run_fix({which}.srt) còn {len(errors)} lỗi:"]
        + [f"  cue {f.cue_index}: [{f.code}] {f.message}" for f in errors[:20]]
    )
    assert len(parse_srt(fixed)) == len(parse_srt(source))


# --------------------------------------------------------------------------- #
# 6. Bảng ưu tiên đọc (tokenize.reading_prefs) phải đi tới file kết quả
# --------------------------------------------------------------------------- #
#
# Trước khi có mục này, thay bảng bằng một bảng rỗng mà cả bộ test vẫn xanh —
# tức bản sửa 谁 = shéi không được bất kỳ test nào giữ.

def test_generated_pinyin_reads_shei_like_the_reviewed_corpus(
    raw_text: str, completed_text: str, cfg: dict[str, Any]
) -> None:
    """raw.srt không có pinyin nào, nên mọi pinyin đều do tool sinh ra.

    Corpus soát tay viết 谁 = ``shéi`` 13/13 lần; file tool sinh ra phải khớp.
    """
    import re

    fixed, _ = _run_fix_errors(raw_text, cfg)
    reference = len(re.findall(r"[Ss]héi", completed_text))
    assert reference == 13
    assert len(re.findall(r"[Ss]huí", fixed)) == 0
    assert len(re.findall(r"[Ss]héi", fixed)) == reference


def test_reading_prefs_always_exact_and_blank_override() -> None:
    """``always`` áp cả trong cụm dài; ``exact`` chỉ áp cho cụm đứng một mình;
    giá trị rỗng trong cấu hình là tắt một mục."""
    from srtgen.stages.s5_tokenize import pinyin_of, reading_prefs

    assert pinyin_of("谁") == "shéi"
    assert pinyin_of("谁知道") == "shéizhīdào"
    assert pinyin_of("嗯") == "ǹg"
    assert pinyin_of("地") == "de"
    assert pinyin_of("地图") == "dìtú"   # exact không được lan vào cụm dài

    off = reading_prefs({"tokenize": {"reading_prefs": {"always": {"谁": ""}}}})
    assert "谁" not in off.always
    assert pinyin_of("谁", off) != "shéi"
