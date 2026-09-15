"""Validator — hiện thực đúng checklist 12 mục của `docs/format-contract.md`.

Vì sao module này soi **văn bản thô** chứ không đi qua tokenizer của `srtgen.core.srt`:
`tokenize_line(..., normalize=True)` được thiết kế để *sửa* (`...` → `……`, `-` dính
chữ → `——`).  Một validator dùng chính bộ tokenizer đó sẽ không bao giờ nhìn thấy
những lỗi mà bộ tokenizer đã lặng lẽ vá.  Nên ở đây có một bộ tách (`_lex_line`)
riêng, cố ý **không sửa gì cả**: nó chỉ mô tả dòng chữ đúng như nó đang nằm trên đĩa.

Hệ quả kiến trúc thứ hai: `rules.py` không import `srtgen.core.srt` ở cấp module.
Theo build-spec mục 2, `merge_zh_py` phải ghi finding `CUM_MISMATCH`, tức `srt.py`
import `Finding` từ đây — import ngược lại sẽ tạo vòng.  Mọi import nội bộ và mọi
thư viện nặng (`pypinyin`) đều nằm *bên trong* hàm, để `srtgen doctor` và web UI
khởi động được kể cả khi máy chưa cài đủ.

Hai đường vào, cùng một bộ luật:

* `validate_text(srt_text)` — dành cho lệnh `check`/`fix` trên file người khác gửi.
  Kiểm được cả những thứ chỉ tồn tại ở dạng thô: hình dạng block, số thứ tự,
  định dạng mốc thời gian, khoảng trắng thừa.
* `validate_document(doc)` — dành cho S8, chạy trên `Document` đã dựng.  Ở đường
  này phần lớn luật là *assert phòng thủ*: renderer đã bảo đảm không có khoảng
  trắng thừa, nên finding ở đây nghĩa là renderer có bug, không phải dữ liệu bẩn.

Mọi `Finding.message` viết bằng tiếng Việt cho người không phải dân IT: nói rõ cue
số mấy, sai chỗ nào, và trích đúng đoạn vi phạm để người đọc tìm được ngay trong file.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, Sequence

if TYPE_CHECKING:  # pragma: no cover - chỉ phục vụ type checker, không chạy lúc runtime
    from srtgen.core.token import Document

__all__ = [
    "Finding",
    "validate_document",
    "validate_text",
    "validate_vi_document",
    "validate_pair",
    "looks_like_vi_document",
    "summarize",
    "format_report_lines",
    "RULES",
    "VI_RULES",
    "ALL_RULES",
    "SEVERITY_ERROR",
    "SEVERITY_WARN",
    "SEVERITY_INFO",
]


# --------------------------------------------------------------------------- #
# mã lỗi và mức độ
# --------------------------------------------------------------------------- #

SEVERITY_ERROR = "error"
SEVERITY_WARN = "warn"
SEVERITY_INFO = "info"

_SEVERITY_ORDER = {SEVERITY_ERROR: 0, SEVERITY_WARN: 1, SEVERITY_INFO: 2}

BLOCK_SHAPE = "BLOCK_SHAPE"
INDEX_SEQ = "INDEX_SEQ"
TIMESTAMP_FORMAT = "TIMESTAMP_FORMAT"
TIMESTAMP_ORDER = "TIMESTAMP_ORDER"
CUM_MISMATCH = "CUM_MISMATCH"
PAIR_MISMATCH = "PAIR_MISMATCH"
LEADING_SPACE = "LEADING_SPACE"
TRAILING_SPACE = "TRAILING_SPACE"
DOUBLE_SPACE = "DOUBLE_SPACE"
ASCII_PUNCT = "ASCII_PUNCT"
SPACE_AROUND_PUNCT = "SPACE_AROUND_PUNCT"
ELLIPSIS_FORM = "ELLIPSIS_FORM"
DASH_FORM = "DASH_FORM"
MARKER_SPACING = "MARKER_SPACING"
MARKER_SYNC = "MARKER_SYNC"
PUNCT_SYNC = "PUNCT_SYNC"
NAME_INCONSISTENT = "NAME_INCONSISTENT"
ERHUA_SPLIT = "ERHUA_SPLIT"
EMPTY_CUE = "EMPTY_CUE"

#: code -> (severity mặc định, nhãn tiếng Việt ngắn dùng cho báo cáo/CLI)
RULES: dict[str, tuple[str, str]] = {
    BLOCK_SHAPE: (SEVERITY_ERROR, "Một đoạn phụ đề không đủ 4 dòng"),
    INDEX_SEQ: (SEVERITY_ERROR, "Số thứ tự không liên tục"),
    TIMESTAMP_FORMAT: (SEVERITY_ERROR, "Mốc thời gian sai định dạng"),
    TIMESTAMP_ORDER: (SEVERITY_ERROR, "Mốc thời gian sai thứ tự"),
    CUM_MISMATCH: (SEVERITY_ERROR, "Dòng Hán và dòng pinyin lệch số cụm"),
    PAIR_MISMATCH: (SEVERITY_WARN, "Cụm Hán và cụm pinyin cùng vị trí không khớp phát âm"),
    LEADING_SPACE: (SEVERITY_ERROR, "Thừa khoảng trắng đầu dòng"),
    TRAILING_SPACE: (SEVERITY_ERROR, "Thừa khoảng trắng cuối dòng"),
    DOUBLE_SPACE: (SEVERITY_ERROR, "Hai khoảng trắng liền nhau"),
    ASCII_PUNCT: (SEVERITY_ERROR, "Còn dấu câu kiểu Latin trong nội dung"),
    SPACE_AROUND_PUNCT: (SEVERITY_ERROR, "Khoảng trắng sát dấu câu tiếng Trung"),
    ELLIPSIS_FORM: (SEVERITY_ERROR, "Dấu lửng không đúng dạng ……"),
    DASH_FORM: (SEVERITY_ERROR, "Dấu ngắt lời không đúng dạng ——"),
    MARKER_SPACING: (SEVERITY_ERROR, "Dấu gạch đổi người nói thiếu hoặc thừa khoảng trắng"),
    MARKER_SYNC: (SEVERITY_ERROR, "Dấu gạch đổi người nói lệch vị trí giữa hai dòng"),
    PUNCT_SYNC: (SEVERITY_ERROR, "Dấu câu lệch giữa hai dòng"),
    NAME_INCONSISTENT: (SEVERITY_WARN, "Cùng một cụm từ lúc gộp lúc tách"),
    ERHUA_SPLIT: (SEVERITY_ERROR, "Âm nhi hoá (儿化音) bị tách rời"),
    EMPTY_CUE: (SEVERITY_WARN, "Một đoạn phụ đề không có chữ nào"),
}

# --------------------------------------------------------------------------- #
# mã lỗi của FILE PHỤ ĐỀ TIẾNG VIỆT — build-spec-v2 mục 2
# --------------------------------------------------------------------------- #
#
# Vì sao là một bảng riêng chứ không nhét thêm vào `RULES`: `RULES` ánh xạ 1-1 với
# checklist 12 mục của `docs/format-contract.md`, tức là hợp đồng định dạng của
# **dòng Hán và dòng pinyin**. Bảng dưới đây là hợp đồng của **file `_vi.srt`** —
# một tài liệu khác, một ngôn ngữ khác, một bộ ràng buộc khác. Trộn hai bảng làm
# một sẽ khiến câu hỏi "mã nào áp cho dòng nào" không còn trả lời được bằng cách
# nhìn vào code, và đó đúng là cái bẫy build-spec-v2 dặn phải tránh.
#
# `ALL_RULES` là bản gộp, dành cho những chỗ chỉ cần tra severity/nhãn theo mã mà
# không quan tâm mã thuộc bảng nào (report, CLI, `_mk`).

VI_BLOCK_COUNT = "VI_BLOCK_COUNT"
VI_TIMESTAMP_DRIFT = "VI_TIMESTAMP_DRIFT"
VI_EMPTY_LINE = "VI_EMPTY_LINE"
VI_TRAILING_SPACE = "VI_TRAILING_SPACE"
VI_MARKER_SYNC = "VI_MARKER_SYNC"

#: code -> (severity mặc định, nhãn tiếng Việt ngắn) cho file phụ đề tiếng Việt.
VI_RULES: dict[str, tuple[str, str]] = {
    VI_BLOCK_COUNT: (SEVERITY_ERROR, "Hai file có số đoạn phụ đề khác nhau"),
    VI_TIMESTAMP_DRIFT: (SEVERITY_ERROR, "Mốc thời gian hai file lệch nhau"),
    VI_EMPTY_LINE: (SEVERITY_WARN, "Đoạn phụ đề chưa có dòng tiếng Việt"),
    VI_TRAILING_SPACE: (SEVERITY_ERROR, "Thừa khoảng trắng đầu hoặc cuối dòng tiếng Việt"),
    VI_MARKER_SYNC: (SEVERITY_WARN, "Dấu gạch đổi người nói lệch giữa hai file"),
}

#: Tra cứu chung cho cả hai bảng. Hai bảng không được trùng mã — assert ngay khi
#: import để một mã trùng không lặng lẽ ghi đè severity của mã kia.
assert not (set(RULES) & set(VI_RULES)), "Mã lỗi trùng giữa RULES và VI_RULES"
ALL_RULES: dict[str, tuple[str, str]] = {**RULES, **VI_RULES}

_SEVERITY_LABEL = {
    SEVERITY_ERROR: "Lỗi",
    SEVERITY_WARN: "Cảnh báo",
    SEVERITY_INFO: "Ghi chú",
}


@dataclass
class Finding:
    """Một vi phạm cụ thể, đủ thông tin để người soát mở file và sửa tay.

    `line` giữ **nguyên dòng** vi phạm chứ không chỉ đoạn trích, vì giao diện web
    cần hiển thị cả dòng còn CLI chỉ in đoạn trích trong `message`.
    """

    code: str
    severity: str
    cue_index: int | None
    message: str
    line: str = ""

    def label(self) -> str:
        """Nhãn tiếng Việt của mức độ, dùng khi in ra cho người dùng cuối."""
        return _SEVERITY_LABEL.get(self.severity, self.severity)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "cue_index": self.cue_index,
            "message": self.message,
            "line": self.line,
        }


def _mk(code: str, cue_index: int | None, message: str, line: str = "") -> Finding:
    """Tạo Finding lấy severity từ bảng mã, để severity chỉ khai báo một chỗ."""
    severity = ALL_RULES.get(code, (SEVERITY_ERROR, ""))[0]
    return Finding(code=code, severity=severity, cue_index=cue_index, message=message, line=line)


# --------------------------------------------------------------------------- #
# bảng ký tự
# --------------------------------------------------------------------------- #

#: Dấu mở — được phép có khoảng trắng *trước*, cấm khoảng trắng *sau*.
PUNCT_OPEN = "《（〈「『“‘【〔"
#: Dấu đóng — cấm khoảng trắng *trước*, được phép có khoảng trắng *sau*.
PUNCT_CLOSE = "》）〉」』”’】〕"
#: Dấu giữa câu — cấm khoảng trắng cả hai bên (ngoại lệ duy nhất: trước marker "-").
PUNCT_MID = "。，、？！：；·"

#: Sáu dấu ASCII checklist mục 6 cấm tuyệt đối trong nội dung (dòng 3 và dòng 4).
ASCII_FORBIDDEN = ",.?!:;"

#: Dấu ASCII **có cặp** mà bảng dấu ở `docs/format-contract.md` mục 3 (dòng
#: 117-130) bắt buộc đổi sang dấu tiếng Trung, và `srtgen/core/srt.py`
#: (`ASCII_PUNCT_MAP`, `smart_quotes`) thật sự đổi khi chạy `fix`.
#:
#: Vì sao chúng phải bị báo dù checklist mục 6 chỉ kể tên sáu dấu kia: checklist
#: là bản tóm tắt của mục 3, không phải bản thu hẹp — và mục 3 còn cấm thẳng
#: "trộn dấu câu ASCII và dấu câu tiếng Trung trong cùng một file". Khi validator
#: im lặng còn `fix` thì đổi, hai lệnh nói ngược nhau về cùng một file: người
#: dùng bấm "Kiểm tra" thấy "File đạt toàn bộ 12 mục", bấm "Sửa" thì file đổi.
#: Đó đúng là kiểu hỏng mà người không rành máy tính không thể tự hiểu nổi.
ASCII_PAIRED: dict[str, str] = {
    "(": "（",
    ")": "）",
    "<": "《",
    ">": "》",
    '"': "“”",   # mở hay đóng tuỳ vị trí, xem `_quote_want`
}

#: Dấu ASCII còn lại: vẫn tách thành punct để so đồng bộ hai dòng, nhưng **không**
#: báo `ASCII_PUNCT`. `[] {}` không có trong bảng dấu của README và `srtgen fix`
#: cũng không đổi chúng — báo lỗi cho một thứ mà chính tool không sửa được là
#: đẩy người dùng vào ngõ cụt. Nháy đơn `'` thì không nằm ở đây chút nào: bộ tách
#: gộp nó vào cụm chữ để "don't" và dấu ngăn âm tiết pinyin (`Xī'ān`) đi qua yên ổn.
ASCII_OTHER = "".join(ASCII_PAIRED) + "[]{}"

#: Thẻ định dạng của chính SRT (`<i>`, `</i>`, `<b>`, `<font color="…">`).
#: Đây là **markup**, không phải dấu câu trong lời thoại: nó không được đếm là
#: cụm, không được đem so dấu giữa hai dòng, và tuyệt đối không được khuyên đổi
#: thành `《i》` — làm theo lời khuyên đó là hỏng thẻ nghiêng của cả file.
_MARKUP_RE = re.compile(r"</?(?:i|b|u|s|font|ruby|rt)\b[^<>]*>", re.IGNORECASE)

ELLIPSIS_CHAR = "…"  # U+2026
DASH_CHAR = "—"  # U+2014
#: Các biến thể gạch ngang sai chính tả cần quy về "——".
DASH_WRONG = "-‐‑‒–―－−"

#: Khoảng trắng "lạ" cũng phải coi là ranh giới cụm, nếu không một dấu U+3000 sẽ
#: dính vào cụm chữ và làm lệch số cụm một cách khó hiểu.
SPACE_CHARS = " \t\u00a0\u3000\u2009\u200a"

#: "Hai khoảng trắng liền nhau" (checklist mục 11) dựng thẳng từ `SPACE_CHARS`
#: để hai định nghĩa không bao giờ lệch nhau. Bản viết tay cũ bỏ sót U+2009 và
#: U+200A: bộ tách coi chúng là khoảng trắng nhưng luật lại không đếm, nên hai
#: dấu cách mảnh liền nhau lọt lưới trong khi hai dấu cách thường thì bị bắt.
_DOUBLE_SPACE_RE = re.compile(f"[{re.escape(SPACE_CHARS)}]{{2,}}")

#: Đúng chữ ký trong build-spec mục 2 — dùng lại y hệt để hai module không lệch nhau.
TIMESTAMP_RE = re.compile(
    r"^(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})$"
)

#: "... " hoặc ". . ." — dấu lửng viết kiểu Latin, bắt bằng regex trên dòng thô vì
#: dạng có khoảng trắng xen giữa không còn là một token liền.
_SPACED_DOTS_RE = re.compile(r"\.[ \t\u3000]+\.")


# --------------------------------------------------------------------------- #
# bộ tách dòng (không sửa gì, chỉ mô tả)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Item:
    """Một cụm trên dòng thô, kèm vị trí để trích đoạn vi phạm cho người đọc."""

    kind: str  # "word" | "punct" | "marker" | "markup"
    text: str
    start: int

    @property
    def end(self) -> int:
        return self.start + len(self.text)


def _is_space(ch: str) -> bool:
    return ch in SPACE_CHARS


def _is_punct_char(ch: str) -> bool:
    return (
        ch in PUNCT_OPEN
        or ch in PUNCT_CLOSE
        or ch in PUNCT_MID
        or ch in ASCII_FORBIDDEN
        or ch in ASCII_OTHER
        or ch == ELLIPSIS_CHAR
        or ch == DASH_CHAR
        or ch in DASH_WRONG
    )


def _prev_nonspace(line: str, i: int) -> str:
    j = i - 1
    while j >= 0 and _is_space(line[j]):
        j -= 1
    return line[j] if j >= 0 else ""


def _after_leading_markup(line: str) -> int:
    """Vị trí ký tự đầu tiên SAU chuỗi thẻ SRT đứng liền nhau ở đầu dòng; 0 nếu không có thẻ.

    Vì sao cần: `<i>- 你 是 谁？</i>` là dạng đúng mà `srtgen fix` xuất ra (hợp đồng
    C), và bộ tách của `srt.py` cũng coi thẻ là vô hình khi hỏi "đây có phải đầu
    lượt thoại không". Luật marker trước đây chỉ coi `start == 0` là đầu dòng, nên
    báo `MARKER_SPACING` oan cho chính file tool vừa ghi và khuyên đổi sang dạng
    sai `<i> - `. Chỉ nhận các thẻ đứng LIỀN nhau từ vị trí 0 (`<i><b>- `).
    """
    pos = 0
    while pos < len(line):
        match = _MARKUP_RE.match(line, pos)
        if match is None or match.end() == pos:
            break
        pos = match.end()
    return pos


def _lex_line(line: str) -> list[_Item]:
    """Tách một dòng đã render thành cụm chữ / dấu câu / marker, giữ nguyên sai sót.

    Quy tắc phân biệt `-` (theo README mục 3, phần marker đổi người nói):
    `-` là **marker** khi đứng đầu dòng, ngay sau một dấu mở/dấu câu, hoặc có
    khoảng trắng ở ít nhất một bên.  `-` dính giữa hai chữ là dấu ngắt lời viết
    sai và phải thành `——` — ở đây nó được xếp vào `punct` để luật DASH_FORM bắt.
    Chuỗi ký tự lặp (`……`, `——`, `...`) gom thành **một** item để luật hình dạng
    dấu đếm được độ dài thật.

    Thẻ định dạng SRT (`<i>`) được nhận nguyên khối thành item `markup`. Không
    luật nào đọc kind đó, nên thẻ vừa không bị coi là cụm chữ vừa không bị coi
    là dấu câu — đúng bản chất của nó.
    """
    items: list[_Item] = []
    markup = {m.start(): m.end() for m in _MARKUP_RE.finditer(line)}
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if _is_space(ch):
            i += 1
            continue

        end = markup.get(i)
        if end is not None:
            items.append(_Item("markup", line[i:end], i))
            i = end
            continue

        if ch in (ELLIPSIS_CHAR, DASH_CHAR, ".") or ch in DASH_WRONG:
            j = i
            while j < n and line[j] == ch:
                j += 1
            run = line[i:j]
            if ch in DASH_WRONG and len(run) == 1 and _is_marker_dash(line, i, j):
                items.append(_Item("marker", run, i))
            else:
                items.append(_Item("punct", run, i))
            i = j
            continue

        if _is_punct_char(ch):
            j = i
            while j < n and line[j] == ch:
                j += 1
            items.append(_Item("punct", line[i:j], i))
            i = j
            continue

        # Một cụm chữ chạy xuyên qua gạch nối nằm TRONG từ: "Wi-Fi", "Jean-Luc",
        # "2019-2020", "COVID-19", và cả pinyin của tên ghép như "Měi-Yī". Gạch
        # đó là chính tả của từ, nên "Wi-Fi" phải đếm là MỘT cụm — đúng như
        # `srt.tokenize_line` đọc nó. Cắt ra thành ba mảnh sẽ làm số cụm dòng
        # pinyin nhiều hơn dòng Hán và sinh ra một lỗi lệch cụm không có thật.
        j = i
        while j < n and not _is_space(line[j]):
            here = line[j]
            if here in DASH_WRONG and _is_inner_hyphen(line, j):
                j += 1
                continue
            if _is_punct_char(here) or here in DASH_WRONG:
                break
            if here in (ELLIPSIS_CHAR, DASH_CHAR, "."):
                break
            j += 1
        items.append(_Item("word", line[i:j], i))
        i = j
    return items


def _is_inner_hyphen(line: str, pos: int) -> bool:
    """Gach noi nam giua hai ky tu Latin/so, khong khoang trang hai ben.

    Cung mot luat voi `srt._is_inner_hyphen`. Hai ban lexer (mot de kiem, mot de
    sua) phai tra loi giong het nhau ve cau hoi "dau gach nay la gi", neu khong
    thi `srtgen fix` chay xong van con loi.
    """
    if pos <= 0 or pos + 1 >= len(line):
        return False

    def latin_or_digit(ch: str) -> bool:
        return ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ("0" <= ch <= "9")

    return latin_or_digit(line[pos - 1]) and latin_or_digit(line[pos + 1])


def _is_marker_dash(line: str, start: int, end: int) -> bool:
    """`-` này là marker đổi người nói hay là dấu ngắt lời viết sai?

    Chỉ nhận marker khi nó thực sự **mở một lượt thoại**: đầu dòng, ngay sau dấu
    mở/dấu câu, hoặc có khoảng trắng kèm bên cạnh.  Trả về False cho `我-你` để
    DASH_FORM bắt được — đó là chỗ README bắt buộc đổi sang `——`.
    """
    if line[start] != "-":  # các biến thể en-dash/gạch nối luôn là lỗi chính tả
        return False
    left_space = start > 0 and _is_space(line[start - 1])
    right_space = end < len(line) and _is_space(line[end])
    # Ngay sau thẻ SRT ở đầu dòng vẫn là đầu dòng: `<i>-你` là marker thiếu khoảng
    # trắng (srtgen fix viết lại thành `<i>- 你`), không phải dấu ngắt lời `——`.
    if start == 0 or start == _after_leading_markup(line):
        return True
    prev = _prev_nonspace(line, start)
    if prev and (prev in PUNCT_OPEN or prev in PUNCT_MID or prev in PUNCT_CLOSE or prev == ELLIPSIS_CHAR):
        return True
    return left_space or right_space


def _words(items: Sequence[_Item]) -> list[_Item]:
    return [it for it in items if it.kind == "word"]


def _excerpt(line: str, start: int, end: int, radius: int = 8) -> str:
    """Trích đoạn quanh chỗ sai, đủ ngắn để đọc trên một dòng terminal."""
    lo = max(0, start - radius)
    hi = min(len(line), end + radius)
    return line[lo:hi]


# --------------------------------------------------------------------------- #
# tiện ích pinyin
# --------------------------------------------------------------------------- #


def strip_tones(text: str) -> str:
    """Bỏ dấu thanh: NFD rồi lọc combining mark, xong ghép lại NFC.

    Dùng cho việc so khớp phát âm, nơi `wǒ` và `wo` phải bằng nhau.  Không dùng
    cho việc hiển thị — dấu thanh là nội dung, không phải trang trí.
    """
    decomposed = unicodedata.normalize("NFD", text)
    return unicodedata.normalize(
        "NFC", "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    )


def _pinyin_key(text: str) -> str:
    """Chuẩn hoá một cụm pinyin về dạng so sánh được: bỏ thanh, thường hoá, bỏ ký tự lạ.

    `ü` và `v` quy về `u` vì `pypinyin` trả `nv` cho 女 còn corpus viết `nǚ`;
    nếu không quy về một mối thì mọi chữ có `ü` đều bị báo sai oan.
    """
    key = strip_tones(text).lower()
    key = key.replace("ü", "u").replace("v", "u")
    return "".join(ch for ch in key if ch.isalpha())


def _is_han(ch: str) -> bool:
    return "一" <= ch <= "鿿" or "㐀" <= ch <= "䶿"


def _has_han(text: str) -> bool:
    return any(_is_han(ch) for ch in text)


# --------------------------------------------------------------------------- #
# luật mức DÒNG
# --------------------------------------------------------------------------- #

_LINE_NAME = {"zh": "dòng Hán", "py": "dòng pinyin"}


def _check_whitespace(cue: int | None, line: str, which: str) -> list[Finding]:
    """Checklist mục 4, 5, 11 — khoảng trắng đầu dòng, cuối dòng, và khoảng trắng kép."""
    out: list[Finding] = []
    name = _LINE_NAME[which]
    if line and _is_space(line[0]):
        out.append(
            _mk(
                LEADING_SPACE,
                cue,
                f"Dòng {cue}: {name} bắt đầu bằng khoảng trắng. "
                f'Thấy "{_excerpt(line, 0, 6)}", phải bỏ khoảng trắng đầu dòng.',
                line,
            )
        )
    if line and _is_space(line[-1]):
        out.append(
            _mk(
                TRAILING_SPACE,
                cue,
                f"Dòng {cue}: {name} kết thúc bằng khoảng trắng. "
                f'Thấy "{_excerpt(line, max(0, len(line) - 6), len(line))}", phải bỏ khoảng trắng cuối dòng.',
                line,
            )
        )
    for m in _DOUBLE_SPACE_RE.finditer(line):
        out.append(
            _mk(
                DOUBLE_SPACE,
                cue,
                f"Dòng {cue}: {name} có {m.end() - m.start()} khoảng trắng liền nhau. "
                f'Thấy "{_excerpt(line, m.start(), m.end())}", chỉ được dùng đúng 1 khoảng trắng.',
                line,
            )
        )
    return out


def _check_ascii_punct(cue: int | None, line: str, items: Sequence[_Item], which: str) -> list[Finding]:
    """Checklist mục 6 và bảng dấu mục 3 — không còn dấu câu kiểu Latin trong nội dung.

    Hai nhóm, cùng một mã lỗi vì với người dùng chúng là cùng một việc phải làm:

    * sáu dấu `, . ? ! : ;` của checklist mục 6;
    * các dấu **có cặp** `( ) < > "` mà bảng dấu mục 3 bắt đổi sang `（） 《》 “”`
      và `srtgen fix` thật sự đổi (:data:`ASCII_PAIRED`).

    Chỉ chạy trên dòng 3 và dòng 4.  Dấu phẩy của mốc thời gian (`00:01:19,580`)
    nằm ở dòng 2 nên không bao giờ đi qua đây — đó là ngoại lệ bắt buộc của README.
    Dấu nháy đơn trong chữ Latin (`don't`) cũng không tính, vì nó không nằm trong
    bảng dấu và đã được bộ tách gộp vào cụm chữ.  Thẻ định dạng SRT (`<i>`) cũng
    không tính: bộ tách xếp nó vào kind `markup`, xem :data:`_MARKUP_RE`.
    """
    out: list[Finding] = []
    name = _LINE_NAME[which]
    spaced_dots = {m.start() for m in _SPACED_DOTS_RE.finditer(line)}
    quote_seen = 0
    for it in items:
        if it.kind != "punct":
            continue
        head = it.text[0]
        if head in ASCII_PAIRED:
            if head == '"':
                # Đúng một cặp thì dấu đầu là dấu mở, dấu sau là dấu đóng — đó
                # cũng là cách `smart_quotes` của S6 quyết định khi chạy `fix`.
                want = "“" if quote_seen % 2 == 0 else "”"
                quote_seen += len(it.text)
            else:
                want = ASCII_PAIRED[head]
            out.append(
                _mk(
                    ASCII_PUNCT,
                    cue,
                    f'Dòng {cue}: {name} còn dấu "{it.text}" kiểu Latin. '
                    f'Thấy "{_excerpt(line, it.start, it.end)}", phải đổi thành "{want}".',
                    line,
                )
            )
            continue
        if head not in ASCII_FORBIDDEN:
            continue
        if head == ".":
            # ".." trở lên, hoặc ". ." — đó là dấu lửng viết sai, để ELLIPSIS_FORM lo.
            if len(it.text) >= 2 or any(it.start <= p < it.end for p in spaced_dots):
                continue
            if it.start > 0 and _SPACED_DOTS_RE.search(line, max(0, it.start - 3), it.end + 3):
                continue
        want = {",": "，", ".": "。", "?": "？", "!": "！", ":": "：", ";": "；"}[head]
        out.append(
            _mk(
                ASCII_PUNCT,
                cue,
                f'Dòng {cue}: {name} còn dấu "{it.text}" kiểu Latin. '
                f'Thấy "{_excerpt(line, it.start, it.end)}", phải đổi thành "{want}".',
                line,
            )
        )
    return out


def _check_ellipsis(cue: int | None, line: str, items: Sequence[_Item], which: str) -> list[Finding]:
    """Checklist mục 8 — dấu lửng phải đúng hai ký tự `……` (U+2026 ×2)."""
    out: list[Finding] = []
    name = _LINE_NAME[which]
    for it in items:
        if it.kind != "punct":
            continue
        if set(it.text) == {ELLIPSIS_CHAR} and len(it.text) != 2:
            out.append(
                _mk(
                    ELLIPSIS_FORM,
                    cue,
                    f"Dòng {cue}: {name} có dấu lửng viết {len(it.text)} ký tự "
                    f'"{it.text}". Thấy "{_excerpt(line, it.start, it.end)}", phải viết đúng "……".',
                    line,
                )
            )
        elif set(it.text) == {"."} and len(it.text) >= 2:
            out.append(
                _mk(
                    ELLIPSIS_FORM,
                    cue,
                    f'Dòng {cue}: {name} dùng dấu lửng kiểu Latin "{it.text}". '
                    f'Thấy "{_excerpt(line, it.start, it.end)}", phải đổi thành "……".',
                    line,
                )
            )
    for m in _SPACED_DOTS_RE.finditer(line):
        out.append(
            _mk(
                ELLIPSIS_FORM,
                cue,
                f"Dòng {cue}: {name} có dấu chấm rời nhau kiểu \". . .\". "
                f'Thấy "{_excerpt(line, m.start(), m.end())}", phải đổi thành "……".',
                line,
            )
        )
    return out


def _check_dash(cue: int | None, line: str, items: Sequence[_Item], which: str) -> list[Finding]:
    """Checklist mục 3 — dấu ngắt lời phải đúng hai ký tự `——` (U+2014 ×2).

    `-` đã được bộ tách nhận là marker thì không đi qua đây, đúng như README:
    marker đổi người nói được giữ dạng ASCII cho tương thích SRT.
    """
    out: list[Finding] = []
    name = _LINE_NAME[which]
    for it in items:
        if it.kind != "punct":
            continue
        chars = set(it.text)
        if chars == {DASH_CHAR} and len(it.text) != 2:
            out.append(
                _mk(
                    DASH_FORM,
                    cue,
                    f"Dòng {cue}: {name} có dấu ngắt lời viết {len(it.text)} ký tự "
                    f'"{it.text}". Thấy "{_excerpt(line, it.start, it.end)}", phải viết đúng "——".',
                    line,
                )
            )
        elif chars and chars <= set(DASH_WRONG):
            out.append(
                _mk(
                    DASH_FORM,
                    cue,
                    f'Dòng {cue}: {name} dùng "{it.text}" làm dấu ngắt lời. '
                    f'Thấy "{_excerpt(line, it.start, it.end)}", phải đổi thành "——" và viết dính vào chữ đứng trước.',
                    line,
                )
            )
    return out


def _markup_run_start(items: Sequence[_Item], pos: int) -> int:
    """Đầu chuỗi thẻ SRT đứng LIỀN ngay trước `pos`; không có thẻ nào thì trả `pos`.

    Renderer gắn thẻ mở vào token đứng SAU nó và đặt khoảng trắng TRƯỚC thẻ
    (hợp đồng C, `render_tokens`): `好！ <i>- 好。`. Luật khoảng trắng phải nhìn
    xuyên qua thẻ như vậy, không thì file tool vừa ghi không qua được bộ kiểm.
    """
    ends = {it.end: it.start for it in items if it.kind == "markup"}
    while pos in ends:
        pos = ends[pos]
    return pos


def _markup_run_end(items: Sequence[_Item], pos: int) -> int:
    """Cuối chuỗi thẻ SRT bắt đầu LIỀN tại `pos`; không có thẻ nào thì trả `pos`."""
    starts = {it.start: it.end for it in items if it.kind == "markup"}
    while pos in starts:
        pos = starts[pos]
    return pos


def _check_space_around_punct(
    cue: int | None, line: str, items: Sequence[_Item], which: str
) -> list[Finding]:
    """Checklist mục 7 — dấu câu tiếng Trung phải dính vào nội dung.

    Ngoại lệ **duy nhất** theo README: đúng 1 khoảng trắng giữa dấu câu và marker
    đổi người nói (`起来。 - 我`).  Dấu mở được phép có khoảng trắng phía trước và
    dấu đóng được phép có khoảng trắng phía sau, vì đó là ranh giới cụm bình thường.
    """
    out: list[Finding] = []
    name = _LINE_NAME[which]
    marker_starts = {it.start for it in items if it.kind == "marker"}
    for it in items:
        if it.kind != "punct":
            continue
        head = it.text[0]
        if head in ASCII_FORBIDDEN or head in ASCII_OTHER:
            continue  # đã có ASCII_PUNCT/DASH_FORM lo, khỏi báo chồng
        is_open = head in PUNCT_OPEN
        is_close = head in PUNCT_CLOSE
        if not is_open and it.start > 0 and _is_space(line[it.start - 1]):
            out.append(
                _mk(
                    SPACE_AROUND_PUNCT,
                    cue,
                    f'Dòng {cue}: {name} có khoảng trắng ngay trước dấu "{it.text}". '
                    f'Thấy "{_excerpt(line, it.start, it.end)}", dấu câu phải viết dính vào chữ đứng trước.',
                    line,
                )
            )
        if not is_close and it.end < len(line) and _is_space(line[it.end]):
            # Ngoại lệ: khoảng trắng này đang tách dấu câu với marker đổi người nói.
            nxt = it.end
            while nxt < len(line) and _is_space(line[nxt]):
                nxt += 1
            # Thẻ mở dính vào marker (`好！ <i>- 好。`) không đổi ngoại lệ này: bỏ thẻ
            # đi thì dòng là `好！ - 好。`, đúng như README.
            if nxt == it.end + 1 and _markup_run_end(items, nxt) in marker_starts:
                continue
            out.append(
                _mk(
                    SPACE_AROUND_PUNCT,
                    cue,
                    f'Dòng {cue}: {name} có khoảng trắng ngay sau dấu "{it.text}". '
                    f'Thấy "{_excerpt(line, it.start, it.end)}", nội dung tiếp theo phải viết liền.',
                    line,
                )
            )
    return out


def _check_marker_spacing(
    cue: int | None, line: str, items: Sequence[_Item], which: str
) -> list[Finding]:
    """Checklist mục 10 — marker `-` phải có đúng 1 khoảng trắng mỗi bên.

    Đầu dòng (hoặc ngay sau dấu mở như `‘`) là chỗ mở lượt thoại nên không cần
    khoảng trắng bên trái; mọi vị trí khác đều cần, kể cả khi bên trái là dấu câu —
    đây chính là ngoại lệ cấu trúc README nói tới.

    "Đầu dòng" tính SAU các thẻ SRT đứng đầu dòng (:func:`_after_leading_markup`):
    `<i>- 你 是 谁？</i>` là marker mở dòng, đúng như `srtgen fix` viết ra. Coi `>`
    của thẻ là "chữ đứng trước" thì file tool vừa ghi không qua được chính bộ kiểm
    của tool, và lời khuyên sửa lại đòi đúng dạng sai `<i> - `.
    """
    out: list[Finding] = []
    name = _LINE_NAME[which]
    head = _after_leading_markup(line)
    for it in items:
        if it.kind != "marker":
            continue
        at_start = it.start == head
        # Giữa dòng, thẻ SRT cũng trong suốt: `好！ <i>- 好。` có khoảng trắng đứng
        # trước thẻ mở, đúng dạng renderer ghi ra. Xét ký tự bên ngoài chuỗi thẻ.
        left = _markup_run_start(items, it.start)
        right = _markup_run_end(items, it.end)
        # `‘- 他们 ...` — marker dính ngay sau dấu mở vẫn là chỗ mở lượt thoại,
        # chèn khoảng trắng vào đó sẽ phạm luật "cấm khoảng trắng sau dấu mở".
        opens_quote = not at_start and line[left - 1] in PUNCT_OPEN
        if not at_start and not opens_quote and not _is_space(line[left - 1]):
            out.append(
                _mk(
                    MARKER_SPACING,
                    cue,
                    f"Dòng {cue}: {name} thiếu khoảng trắng trước marker đổi người nói. "
                    f'Thấy "{_excerpt(line, it.start, it.end)}", cần '
                    f'"{_fix_left(line, it, left)}".',
                    line,
                )
            )
        if right >= len(line):
            out.append(
                _mk(
                    MARKER_SPACING,
                    cue,
                    f"Dòng {cue}: {name} có marker \"-\" ở cuối dòng, không có lời thoại theo sau. "
                    f'Thấy "{_excerpt(line, it.start, it.end)}".',
                    line,
                )
            )
        elif not _is_space(line[right]):
            out.append(
                _mk(
                    MARKER_SPACING,
                    cue,
                    f"Dòng {cue}: {name} thiếu khoảng trắng sau marker đổi người nói. "
                    f'Thấy "{_excerpt(line, it.start, it.end)}", cần '
                    f'"{_fix_right(line, it, right)}".',
                    line,
                )
            )
    return out


def _fix_left(line: str, it: _Item, at: int | None = None) -> str:
    """Dựng đoạn "phải viết thế này" cho thông báo — người sửa tay nhìn là làm được ngay.

    `at` là chỗ chèn khoảng trắng: trước chuỗi thẻ SRT dính vào marker (nếu có),
    để lời khuyên ra `好！ <i>- 好` chứ không phải dạng sai `好！<i> - 好`.
    """
    pos = it.start if at is None else at
    lo = max(0, pos - 8)
    hi = min(len(line), it.end + 8)
    return line[lo:pos] + " " + line[pos:hi]


def _fix_right(line: str, it: _Item, at: int | None = None) -> str:
    pos = it.end if at is None else at
    lo = max(0, it.start - 8)
    hi = min(len(line), pos + 8)
    return line[lo:pos] + " " + line[pos:hi]


# --------------------------------------------------------------------------- #
# luật so KHỚP HAI DÒNG
# --------------------------------------------------------------------------- #


def _punct_signature(items: Sequence[_Item]) -> list[tuple[int, str]]:
    """(số cụm chữ đứng trước, nội dung dấu) — vị trí dấu tính theo cụm, không theo ký tự."""
    sig: list[tuple[int, str]] = []
    n_words = 0
    for it in items:
        if it.kind == "word":
            n_words += 1
        elif it.kind == "punct":
            sig.append((n_words, it.text))
    return sig


def _marker_positions(items: Sequence[_Item]) -> list[int]:
    pos: list[int] = []
    n_words = 0
    for it in items:
        if it.kind == "word":
            n_words += 1
        elif it.kind == "marker":
            pos.append(n_words)
    return pos


def _check_cluster_count(
    cue: int | None, zh_line: str, py_line: str, zh_items: Sequence[_Item], py_items: Sequence[_Item]
) -> list[Finding]:
    """Checklist mục 2 — số cụm hai dòng phải bằng nhau sau khi bỏ dấu câu và marker."""
    zw = _words(zh_items)
    pw = _words(py_items)
    if len(zw) == len(pw):
        return []
    return [
        _mk(
            CUM_MISMATCH,
            cue,
            f"Dòng {cue}: dòng Hán có {len(zw)} cụm nhưng dòng pinyin có {len(pw)} cụm "
            f"(đã bỏ dấu câu và marker). "
            f'Hán: "{" | ".join(t.text for t in zw)}" — '
            f'Pinyin: "{" | ".join(t.text for t in pw)}".',
            zh_line,
        )
    ]


def _check_sync(
    cue: int | None, zh_line: str, py_line: str, zh_items: Sequence[_Item], py_items: Sequence[_Item]
) -> list[Finding]:
    """Checklist mục 9 và 10 — loại/vị trí dấu câu và marker phải đồng bộ hai dòng.

    Khi số cụm đã lệch thì vị trí tính theo cụm không còn nghĩa, nên phần so vị
    trí bị bỏ qua; phần so *dãy dấu* vẫn chạy vì nó độc lập với số cụm.
    """
    out: list[Finding] = []
    zsig = _punct_signature(zh_items)
    psig = _punct_signature(py_items)
    ztexts = [t for _, t in zsig]
    ptexts = [t for _, t in psig]
    aligned = len(_words(zh_items)) == len(_words(py_items))

    if ztexts != ptexts:
        out.append(
            _mk(
                PUNCT_SYNC,
                cue,
                f"Dòng {cue}: dấu câu hai dòng không giống nhau. "
                f'Dòng Hán dùng "{" ".join(ztexts) or "(không có)"}", '
                f'dòng pinyin dùng "{" ".join(ptexts) or "(không có)"}". '
                f'Hán: "{zh_line}" — Pinyin: "{py_line}".',
                zh_line,
            )
        )
    elif aligned and zsig != psig:
        bad = next((i for i, (a, b) in enumerate(zip(zsig, psig)) if a != b), 0)
        out.append(
            _mk(
                PUNCT_SYNC,
                cue,
                f'Dòng {cue}: dấu "{zsig[bad][1]}" đặt sau cụm thứ {zsig[bad][0]} ở dòng Hán '
                f"nhưng sau cụm thứ {psig[bad][0]} ở dòng pinyin. "
                f'Hán: "{zh_line}" — Pinyin: "{py_line}".',
                zh_line,
            )
        )

    zmark = _marker_positions(zh_items)
    pmark = _marker_positions(py_items)
    if len(zmark) != len(pmark):
        out.append(
            _mk(
                MARKER_SYNC,
                cue,
                f"Dòng {cue}: dòng Hán có {len(zmark)} marker đổi người nói còn dòng pinyin có "
                f"{len(pmark)}. Hai dòng phải có cùng số marker ở cùng vị trí. "
                f'Hán: "{zh_line}" — Pinyin: "{py_line}".',
                zh_line,
            )
        )
    elif aligned and zmark != pmark:
        bad = next((i for i, (a, b) in enumerate(zip(zmark, pmark)) if a != b), 0)
        out.append(
            _mk(
                MARKER_SYNC,
                cue,
                f"Dòng {cue}: marker thứ {bad + 1} đứng sau cụm thứ {zmark[bad]} ở dòng Hán "
                f"nhưng sau cụm thứ {pmark[bad]} ở dòng pinyin. "
                f'Hán: "{zh_line}" — Pinyin: "{py_line}".',
                zh_line,
            )
        )
    return out


def _check_erhua(
    cue: int | None, zh_line: str, py_line: str, zh_items: Sequence[_Item], py_items: Sequence[_Item]
) -> list[Finding]:
    """Checklist mục 1 (phần 儿化音) — `儿` là hậu tố, pinyin phải rút gọn thành `r`.

    Hai dạng sai: `儿` đứng thành một cụm riêng (`哪 儿`), và cụm đúng nhưng pinyin
    viết đầy đủ (`nǎér` thay vì `nǎr`).  README chốt dạng rút gọn, nên cả hai đều
    là lỗi kể cả khi số cụm hai dòng vẫn bằng nhau.
    """
    out: list[Finding] = []
    zw = _words(zh_items)
    pw = _words(py_items)

    for it in zw:
        if it.text == "儿":
            out.append(
                _mk(
                    ERHUA_SPLIT,
                    cue,
                    f'Dòng {cue}: chữ "儿" đứng thành một cụm riêng. '
                    f'Thấy "{_excerpt(zh_line, it.start, it.end)}", phải gộp vào cụm đứng trước '
                    f'(ví dụ "哪儿" đi với "nǎr").',
                    zh_line,
                )
            )

    # Hai vị từ này ở erhua.py chứ không tự chế lại tại đây: bản tự chế cũ so trên
    # chuỗi ĐÃ BỎ DẤU THANH nên "zhèr" (đúng) và "zhèér" (sai) đều thành "zheer",
    # khiến validator báo nhầm file hợp lệ.  Và nó không phân biệt 儿化音 (哪儿 = nǎr)
    # với chữ 儿 là âm tiết đầy đủ (女儿 = nǚér, tuyệt đối không được rút gọn).
    from srtgen.core.erhua import is_erhua_word as _is_long_erhua
    from srtgen.core.erhua import is_full_er_form as _is_full_er

    if len(zw) == len(pw):
        for zt, pt in zip(zw, pw):
            if _is_long_erhua(zt.text) and _is_full_er(pt.text):
                out.append(
                    _mk(
                        ERHUA_SPLIT,
                        cue,
                        f'Dòng {cue}: cụm "{zt.text}" ghi pinyin đầy đủ "{pt.text}". '
                        f'Với 儿化音 phải viết dạng rút gọn "{_contract_er(pt.text)}" '
                        f'(ví dụ "哪儿" là "nǎr", không phải "nǎér").',
                        py_line,
                    )
                )
    else:
        # Số cụm đã lệch nên không ghép theo vị trí được; vẫn phát hiện được dạng
        # sai khi cả hai dấu hiệu cùng xuất hiện trong một cue.
        zbad = [t for t in zw if _is_long_erhua(t.text)]
        pbad = [t for t in pw if _is_full_er(t.text)]
        for zt, pt in zip(zbad, pbad):
            out.append(
                _mk(
                    ERHUA_SPLIT,
                    cue,
                    f'Dòng {cue}: cụm "{zt.text}" ghi pinyin đầy đủ "{pt.text}". '
                    f'Với 儿化音 phải viết dạng rút gọn "{_contract_er(pt.text)}".',
                    py_line,
                )
            )
    return out


def _contract_er(pinyin: str) -> str:
    """Gợi ý dạng rút gọn cho thông báo: `nǎér` -> `nǎr`, `yīhuìer` -> `yīhuìr`."""
    low = pinyin
    for tail in ("ér", "ěr", "èr", "ēr", "er"):
        if low.endswith(tail):
            return low[: -len(tail)] + "r"
    return low + "r"


def _check_pair(
    cue: int | None, zh_line: str, py_line: str, zh_items: Sequence[_Item], py_items: Sequence[_Item]
) -> list[Finding]:
    """Checklist mục 3 — từng cặp cùng vị trí phải khớp phát âm, không chỉ khớp số lượng.

    Đây là luật bắt được lỗi mà việc đếm số cụm bỏ sót: `好|唱得|真好` ghép với
    `Hǎochàng|dé|zhēnhǎo` vẫn đủ 3 cụm nhưng `chàng` đã bị kéo sang cụm trước.

    Vì sao chấp nhận **mọi cách đọc** của đa âm tự chứ không chỉ cách đọc mặc định
    của `pypinyin`: đo trên corpus đã audit, cách đọc mặc định báo sai 38 chỗ mà
    chỗ nào cũng đúng ngữ cảnh (`谁` đọc `shéi`, `得` đọc `děi`, `地` đọc `de`,
    `着` đọc `zhuó`).  Một cảnh báo sai nhiều như vậy sẽ dạy người dùng bỏ qua
    cảnh báo — tệ hơn là không có cảnh báo.  Nên chỉ báo khi **không có tổ hợp
    cách đọc nào** trong từ điển dựng lại được chuỗi pinyin đang ghi.

    `pypinyin` import lazy và thiếu thì bỏ qua: đây là kiểm mức cảnh báo, không
    đáng để làm `srtgen check` chết trên máy chưa cài đủ thư viện.
    """
    zw = _words(zh_items)
    pw = _words(py_items)
    if len(zw) != len(pw):
        return []
    try:
        from pypinyin import Style, lazy_pinyin, pinyin
    except ImportError:
        return []

    out: list[Finding] = []
    for zt, pt in zip(zw, pw):
        if not _has_han(zt.text):
            continue
        actual = _pinyin_key(pt.text)
        if not actual:
            continue
        expected = _pinyin_key("".join(lazy_pinyin(zt.text, style=Style.NORMAL)))
        if not expected or expected == actual:
            continue
        # 儿化音 viết đúng (`nǎr`) sẽ lệch với `pypinyin` (`na` + `er`) — đó là dạng
        # README yêu cầu, không phải lỗi.  Dạng sai đã có ERHUA_SPLIT lo.
        is_erhua = zt.text.endswith("儿") and len(zt.text) >= 2
        if is_erhua and expected.endswith("er") and actual == expected[:-2] + "r":
            continue
        groups = [
            [_pinyin_key(r) for r in group]
            for group in pinyin(zt.text, style=Style.NORMAL, heteronym=True)
        ]
        if is_erhua and groups:
            groups[-1] = groups[-1] + ["r"]  # cho phép hậu tố rút gọn ở âm tiết cuối
        if _readings_cover(groups, actual):
            continue
        shown = " ".join("/".join(sorted(set(g))) for g in groups if g)
        out.append(
            _mk(
                PAIR_MISMATCH,
                cue,
                f'Dòng {cue}: cụm "{zt.text}" đi với pinyin "{pt.text}", nhưng theo từ điển chữ này '
                f'chỉ đọc được là "{shown}". Kiểm lại xem pinyin có bị kéo sang cụm bên cạnh không, '
                f"hoặc đây là tên riêng/đọc chệch cần bỏ qua. "
                f'Hán: "{zh_line}" — Pinyin: "{py_line}".',
                py_line,
            )
        )
    return out


def _readings_cover(groups: Sequence[Sequence[str]], actual: str) -> bool:
    """Có tổ hợp cách đọc nào ghép lại đúng bằng `actual` không?

    Quét theo vị trí thay vì sinh mọi tổ hợp: một cụm 4 chữ mỗi chữ 3 cách đọc là
    81 tổ hợp, còn cách này chỉ duyệt chuỗi một lần cho mỗi chữ.
    """
    if not groups:
        return False
    reachable = {0}
    for group in groups:
        nxt: set[int] = set()
        for pos in reachable:
            for reading in group:
                if reading and actual.startswith(reading, pos):
                    nxt.add(pos + len(reading))
        if not nxt:
            return False
        reachable = nxt
    return len(actual) in reachable


# --------------------------------------------------------------------------- #
# luật mức TOÀN FILE
# --------------------------------------------------------------------------- #


def _check_name_consistency(cue_words: list[tuple[int, list[str]]]) -> list[Finding]:
    """Checklist mục 12 — một tên riêng đã chốt cách tách thì phải giữ nguyên cả file.

    Cách phát hiện: gom mọi cụm Hán xuất hiện dạng liền (1 cụm), rồi quét lại toàn
    bộ file tìm chỗ cùng chuỗi đó bị viết thành nhiều cụm liên tiếp.  Chỉ báo
    `warn` vì tiếng Trung có những chuỗi trùng nhau một cách hợp lệ
    (`我 的` và `我的` không phải lúc nào cũng là một).

    Vì sao thông báo **không** được nói "tên riêng"
    ----------------------------------------------
    Phép đo trên `corpus/completed.srt` sau khi fix: 8 cảnh báo, và không cái nào
    là tên riêng — `一百万`, `就让`, `好好`, `干什么`, `多年`, `别闹`, `别过来`,
    `龙拳`.  Luật này soi **mọi chuỗi Hán lặp lại**, đó là đánh đổi cố ý (thu hẹp
    lại thành "chỉ chuỗi có trong names.json" sẽ bỏ sót đúng những tên mà bảng
    tên riêng chưa biết).  Nhưng nếu câu chữ khẳng định "cùng một tên riêng phải
    giữ một cách tách" thì người soát sẽ đi gộp một cụm nói bình thường lại —
    trong khi README mục 7 bảo phải tách theo nhịp pinyin.  Cảnh báo dẫn người
    dùng làm sai còn tệ hơn không cảnh báo, nên thông báo phải nói đúng thứ máy
    thật sự thấy và để người quyết.
    """
    joined_once: dict[str, int] = {}
    for idx, words in cue_words:
        for w in words:
            if len(w) >= 2 and all(_is_han(ch) for ch in w):
                joined_once.setdefault(w, idx)

    if not joined_once:
        return []

    split_at: dict[str, tuple[int, str]] = {}
    for idx, words in cue_words:
        n = len(words)
        for i in range(n):
            acc = words[i]
            if not all(_is_han(ch) for ch in acc):
                continue
            for j in range(i + 1, min(n, i + 4)):
                if not all(_is_han(ch) for ch in words[j]):
                    break
                acc += words[j]
                if len(acc) > 6:
                    break
                if acc in joined_once and acc not in split_at:
                    split_at[acc] = (idx, " ".join(words[i : j + 1]))

    out: list[Finding] = []
    for text, (idx, shown) in sorted(split_at.items(), key=lambda kv: kv[1][0]):
        first = joined_once[text]
        out.append(
            _mk(
                NAME_INCONSISTENT,
                idx,
                f'Cue {idx}: cụm "{text}" ở đây viết tách thành "{shown}", nhưng cue {first} '
                f'lại viết liền "{text}". Nếu đây là tên riêng thì phải chọn một cách '
                f"tách và giữ nguyên trong cả file; nếu chỉ là lời nói thường thì bỏ qua "
                f"cảnh báo này, vì lời nói thường được tách theo nhịp pinyin.",
                shown,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# gộp luật cho một cue
# --------------------------------------------------------------------------- #


def _check_cue_lines(cue_index: int | None, zh_line: str, py_line: str) -> list[Finding]:
    """Chạy toàn bộ luật nội dung cho một cặp dòng Hán/pinyin.

    Đây là **một đường duy nhất** dùng chung cho cả `validate_text` (dòng thô đọc
    từ file) và `validate_document` (dòng do renderer sinh ra), để hai lối vào
    không bao giờ cho kết quả khác nhau trên cùng nội dung.
    """
    findings: list[Finding] = []
    zh_items = _lex_line(zh_line)
    py_items = _lex_line(py_line)

    if not _words(zh_items) and not _words(py_items):
        findings.append(
            _mk(
                EMPTY_CUE,
                cue_index,
                f"Cue {cue_index}: không có chữ nào, chỉ có dấu câu hoặc để trống. "
                f'Hán: "{zh_line}" — Pinyin: "{py_line}".',
                zh_line,
            )
        )

    for line, items, which in ((zh_line, zh_items, "zh"), (py_line, py_items, "py")):
        findings += _check_whitespace(cue_index, line, which)
        findings += _check_ascii_punct(cue_index, line, items, which)
        findings += _check_ellipsis(cue_index, line, items, which)
        findings += _check_dash(cue_index, line, items, which)
        findings += _check_space_around_punct(cue_index, line, items, which)
        findings += _check_marker_spacing(cue_index, line, items, which)

    findings += _check_cluster_count(cue_index, zh_line, py_line, zh_items, py_items)
    findings += _check_sync(cue_index, zh_line, py_line, zh_items, py_items)
    findings += _check_erhua(cue_index, zh_line, py_line, zh_items, py_items)
    findings += _check_pair(cue_index, zh_line, py_line, zh_items, py_items)
    return findings


# --------------------------------------------------------------------------- #
# đường vào 1: văn bản .srt thô
# --------------------------------------------------------------------------- #


@dataclass
class _RawBlock:
    ordinal: int  # thứ tự xuất hiện, 1-based — dùng khi số thứ tự trong file hỏng
    lines: list[str] = field(default_factory=list)


def _split_blocks(text: str) -> list[_RawBlock]:
    """Cắt file thành block theo dòng trống, giữ nguyên mọi dòng để soi hình dạng."""
    blocks: list[_RawBlock] = []
    current: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw_line.strip() == "":
            if current:
                blocks.append(_RawBlock(len(blocks) + 1, current))
                current = []
            continue
        current.append(raw_line)
    if current:
        blocks.append(_RawBlock(len(blocks) + 1, current))
    return blocks


def _parse_timestamp_line(line: str) -> tuple[float, float] | None:
    m = TIMESTAMP_RE.match(line)
    if not m:
        return None
    h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(x) for x in m.groups())
    start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000.0
    end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000.0
    return start, end


def _fmt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def validate_text(srt_text: str) -> list[Finding]:
    """Kiểm một file `.srt` ở dạng văn bản thô, trả về danh sách vi phạm.

    Dùng cho lệnh `srtgen check` và trang "Kiểm tra file" của web UI — tức là chạy
    trên file do người khác gửi, chưa qua tay tool.  Vì thế nó phải soi được cả
    những lỗi thuộc về *hình dạng file* (block thiếu dòng, số thứ tự nhảy cóc,
    mốc thời gian sai) — những thứ mà một `Document` đã dựng xong không còn giữ.

    Dấu phẩy trong dòng mốc thời gian (`00:01:19,580`) không bao giờ bị tính là
    `ASCII_PUNCT`: luật dấu câu chỉ chạy trên dòng 3 và dòng 4.
    """
    # Một file `_vi.srt` lọt vào đây sẽ ăn trọn bộ luật dấu câu tiếng Trung và
    # đẻ ra hàng trăm lỗi ma (xem phần "đường vào 3" ở cuối file). Lái sang đúng
    # bộ luật ngay tại cửa, để người gọi không phải nhớ mình đang cầm file nào.
    if looks_like_vi_document(srt_text):
        return validate_vi_document(srt_text)

    text = srt_text.lstrip("﻿")
    findings: list[Finding] = []
    blocks = _split_blocks(text)

    prev_end: float | None = None
    prev_index: int | None = None
    cue_words: list[tuple[int, list[str]]] = []

    for block in blocks:
        lines = block.lines
        index: int | None = None
        head = lines[0].strip() if lines else ""
        if head.isdigit():
            index = int(head)

        if len(lines) != 4:
            where = f"Cue {index}" if index is not None else f"Block thứ {block.ordinal}"
            findings.append(
                _mk(
                    BLOCK_SHAPE,
                    index,
                    f"{where}: block có {len(lines)} dòng, phải có đúng 4 dòng "
                    f"(số thứ tự, mốc thời gian, dòng Hán, dòng pinyin). "
                    f'Nội dung đang thấy: "{" ⏎ ".join(lines)[:160]}".',
                    lines[0] if lines else "",
                )
            )

        if index is None:
            findings.append(
                _mk(
                    INDEX_SEQ,
                    None,
                    f'Block thứ {block.ordinal}: dòng đầu tiên phải là số thứ tự, nhưng đang là "{head}".',
                    lines[0] if lines else "",
                )
            )
        else:
            # Neo lại theo số thứ tự vừa đọc chứ không theo thứ tự block: một cue bị
            # xoá giữa file chỉ đáng một dòng báo lỗi, không phải hàng nghìn dòng
            # "lệch 1" kéo tới cuối file — người dùng cuối sẽ không đọc nổi.
            expected = 1 if prev_index is None else prev_index + 1
            if index != expected:
                if prev_index is None:
                    msg = (
                        f"Số thứ tự phải bắt đầu từ 1, nhưng cue đầu tiên đang đánh số {index}."
                    )
                else:
                    msg = (
                        f"Số thứ tự không liên tục: sau cue {prev_index} phải là {expected} "
                        f"nhưng đang là {index}."
                    )
                findings.append(_mk(INDEX_SEQ, index, msg + " Cần đánh số lại từ 1 liên tục.", lines[0]))
            prev_index = index

        if len(lines) < 2:
            continue

        span = _parse_timestamp_line(lines[1].strip())
        if span is None:
            findings.append(
                _mk(
                    TIMESTAMP_FORMAT,
                    index,
                    f'Cue {index}: mốc thời gian sai định dạng. Thấy "{lines[1]}", '
                    f'phải viết đúng dạng "00:01:19,580 --> 00:01:22,100".',
                    lines[1],
                )
            )
            # Dòng 2 không đọc được thì cũng không biết dòng nào mới là nội dung.
            # Block hỏng kiểu "thiếu một dòng" đẩy chính mốc thời gian xuống vị
            # trí dòng Hán, và luật nội dung sẽ đem dấu phẩy của `00:00:01,000`
            # ra soi rồi khuyên đổi thành `，` — làm theo là hỏng file, đúng thứ
            # ghi chú cuối `docs/format-contract.md` cấm. Người dùng chỉ cần sửa
            # đúng một chỗ là dòng mốc thời gian; sửa xong, lần kiểm sau mới soi
            # nội dung. Nói một lỗi thật còn hơn nói sáu lỗi trong đó năm cái sai.
            continue

        start, end = span
        if start >= end:
            findings.append(
                _mk(
                    TIMESTAMP_ORDER,
                    index,
                    f"Cue {index}: thời điểm bắt đầu không nhỏ hơn thời điểm kết thúc "
                    f'("{_fmt_time(start)}" --> "{_fmt_time(end)}").',
                    lines[1],
                )
            )
        if prev_end is not None and start < prev_end:
            findings.append(
                _mk(
                    TIMESTAMP_ORDER,
                    index,
                    f"Cue {index}: bắt đầu lúc {_fmt_time(start)} trong khi cue trước kéo dài "
                    f"tới {_fmt_time(prev_end)}. Hai cue chồng lấn thời gian, Aegisub sẽ báo lỗi.",
                    lines[1],
                )
            )
        prev_end = max(prev_end, end) if prev_end is not None else end

        if len(lines) < 4:
            continue

        zh_line, py_line = lines[2], lines[3]
        findings += _check_cue_lines(index, zh_line, py_line)
        if index is not None:
            cue_words.append((index, [it.text for it in _words(_lex_line(zh_line))]))

    findings += _check_name_consistency(cue_words)
    return _dedupe(findings)


# --------------------------------------------------------------------------- #
# đường vào 2: Document đã dựng
# --------------------------------------------------------------------------- #


def validate_document(doc: "Document") -> list[Finding]:
    """Kiểm một `Document` trước khi ghi ra file (chặng S8).

    Ở đây phần lớn luật nội dung chỉ còn là **assert phòng thủ**: hai dòng render
    từ cùng một token list nên lệch số cụm hay thừa khoảng trắng là bug renderer
    chứ không phải dữ liệu bẩn (build-spec mục 0 và mục 1).  Ngoài ra còn kiểm
    thêm bất biến chỉ nhìn thấy được ở mức token: số token `word` phải bằng số cụm
    đếm được trên cả hai dòng đã render.
    """
    # Cùng lý do như ở `validate_text`: chặng dịch S8 dựng ra một `Document`
    # tiếng Việt có hình dạng y hệt, và soi nó bằng luật Hán–pinyin là vô nghĩa.
    if looks_like_vi_document(doc):
        return validate_vi_document(doc)

    from srtgen.core.token import KIND_MARKER, KIND_PUNCT, KIND_WORD, render_py, render_zh

    findings: list[Finding] = []
    prev_end: float | None = None
    cue_words: list[tuple[int, list[str]]] = []

    for ordinal, cue in enumerate(doc.cues, start=1):
        index = cue.index
        if index != ordinal:
            findings.append(
                _mk(
                    INDEX_SEQ,
                    index,
                    f"Số thứ tự phải chạy liên tục từ 1: cue thứ {ordinal} lại đánh số {index}.",
                )
            )

        start, end = float(cue.start), float(cue.end)
        if start < 0 or end < 0 or end >= 360000:
            findings.append(
                _mk(
                    TIMESTAMP_FORMAT,
                    index,
                    f"Cue {index}: mốc thời gian nằm ngoài khoảng ghi được ra file SRT "
                    f"({start:.3f}s --> {end:.3f}s).",
                )
            )
        if start >= end:
            findings.append(
                _mk(
                    TIMESTAMP_ORDER,
                    index,
                    f"Cue {index}: thời điểm bắt đầu không nhỏ hơn thời điểm kết thúc "
                    f'("{_fmt_time(start)}" --> "{_fmt_time(end)}").',
                )
            )
        if prev_end is not None and start < prev_end:
            findings.append(
                _mk(
                    TIMESTAMP_ORDER,
                    index,
                    f"Cue {index}: bắt đầu lúc {_fmt_time(start)} trong khi cue trước kéo dài tới "
                    f"{_fmt_time(prev_end)}. Hai cue chồng lấn thời gian, Aegisub sẽ báo lỗi.",
                )
            )
        prev_end = max(prev_end, end) if prev_end is not None else end

        zh_line = render_zh(cue.tokens)
        py_line = render_py(cue.tokens)

        # EMPTY_CUE và 儿 đứng lẻ đã được `_check_cue_lines` bắt trên hai dòng render
        # ra; ở đây chỉ kiểm những bất biến **chỉ nhìn thấy được ở mức token**.
        word_tokens = [t for t in cue.tokens if t.kind == KIND_WORD]
        if word_tokens:
            n_zh = len(_words(_lex_line(zh_line)))
            n_py = len(_words(_lex_line(py_line)))
            if n_zh != len(word_tokens) or n_py != len(word_tokens):
                findings.append(
                    _mk(
                        CUM_MISMATCH,
                        index,
                        f"Cue {index}: có {len(word_tokens)} cụm trong dữ liệu nhưng render ra "
                        f"{n_zh} cụm ở dòng Hán và {n_py} cụm ở dòng pinyin. Đây là lỗi của bộ "
                        f'render, không phải lỗi nội dung. Hán: "{zh_line}" — Pinyin: "{py_line}".',
                        zh_line,
                    )
                )
            missing = [t for t in word_tokens if not (t.pinyin or "").strip()]
            if missing:
                shown = "、".join(t.zh for t in missing[:5])
                findings.append(
                    _mk(
                        CUM_MISMATCH,
                        index,
                        f"Cue {index}: {len(missing)} cụm chưa có pinyin ({shown}). "
                        f"Phải sinh lại pinyin cho các cụm này trước khi xuất file.",
                        zh_line,
                    )
                )

        for tok in cue.tokens:
            if tok.kind in (KIND_PUNCT, KIND_MARKER) and (tok.pinyin or "").strip():
                findings.append(
                    _mk(
                        PUNCT_SYNC,
                        index,
                        f'Cue {index}: token dấu câu "{tok.zh}" lại mang pinyin "{tok.pinyin}". '
                        f"Dấu câu và marker phải có pinyin rỗng.",
                        zh_line,
                    )
                )

        findings += _check_cue_lines(index, zh_line, py_line)
        cue_words.append((index, [t.zh for t in word_tokens]))

    findings += _check_name_consistency(cue_words)
    return _dedupe(findings)


def _dedupe(findings: Iterable[Finding]) -> list[Finding]:
    """Bỏ finding trùng hệt nhau, giữ nguyên thứ tự xuất hiện.

    Một lỗi có thể bị hai luật cùng chạm tới (ví dụ marker vừa thiếu space trái
    vừa thiếu space phải trên cùng một đoạn trích); người soát chỉ cần thấy một lần.
    """
    seen: set[tuple[str, int | None, str]] = set()
    out: list[Finding] = []
    for f in findings:
        key = (f.code, f.cue_index, f.message)
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


# --------------------------------------------------------------------------- #
# tổng hợp và báo cáo
# --------------------------------------------------------------------------- #


def summarize(findings: Iterable[Finding]) -> dict:
    """Đếm theo mức độ và theo mã lỗi, cho UI và report HTML dùng chung."""
    result: dict = {SEVERITY_ERROR: 0, SEVERITY_WARN: 0, SEVERITY_INFO: 0, "by_code": {}}
    for f in findings:
        if f.severity in result:
            result[f.severity] += 1
        else:  # severity lạ vẫn phải đếm được, đừng nuốt mất
            result[f.severity] = result.get(f.severity, 0) + 1
        result["by_code"][f.code] = result["by_code"].get(f.code, 0) + 1
    return result


def format_report_lines(findings: Iterable[Finding]) -> list[str]:
    """Dựng các dòng tiếng Việt để CLI in thẳng ra màn hình.

    Sắp theo mức độ rồi theo số cue, vì người soát bao giờ cũng sửa lỗi chặn trước.
    Không `print()` ở đây — thư viện chỉ trả chuỗi, người gọi mới quyết định in.
    """
    items = list(findings)
    if not items:
        return ["Không phát hiện lỗi định dạng nào. File đạt toàn bộ 12 mục của quy chuẩn."]

    stats = summarize(items)
    lines: list[str] = [
        f"Kết quả kiểm: {stats[SEVERITY_ERROR]} lỗi, "
        f"{stats[SEVERITY_WARN]} cảnh báo, {stats[SEVERITY_INFO]} ghi chú.",
        "",
    ]

    by_code = sorted(stats["by_code"].items(), key=lambda kv: (-kv[1], kv[0]))
    lines.append("Theo loại lỗi:")
    for code, count in by_code:
        label = ALL_RULES.get(code, (SEVERITY_ERROR, code))[1]
        lines.append(f"  - {label} ({code}): {count}")
    lines.append("")

    ordered = sorted(
        items,
        key=lambda f: (
            _SEVERITY_ORDER.get(f.severity, 9),
            f.cue_index if f.cue_index is not None else 10**9,
            f.code,
        ),
    )
    lines.append("Chi tiết:")
    for f in ordered:
        lines.append(f"  [{f.label()}] {f.message}")
    return lines


# --------------------------------------------------------------------------- #
# đường vào 3: file phụ đề TIẾNG VIỆT — build-spec-v2 mục 2
# --------------------------------------------------------------------------- #
#
# Vì sao phải có một đường đi riêng thay vì dùng lại bộ luật ở trên: toàn bộ luật
# phía trên được viết cho **dòng Hán và dòng pinyin**. Chúng đòi dấu câu phải là
# dấu toàn hình (，。？！), đòi `...` phải viết thành `……`, đòi số cụm hai dòng
# bằng nhau, đòi 儿 không được đứng lẻ. Một dòng tiếng Việt bình thường —
# "Tập 1: Hàng xóm mới." — vi phạm gần như toàn bộ những luật đó mà **không có
# lỗi nào là lỗi thật**. Cho luật tiếng Trung chạy trên file `_vi.srt` sẽ đẻ ra
# hàng trăm lỗi ma; người dùng sẽ ngừng đọc báo cáo, và như thế mất luôn cả
# những lỗi thật. Nên ranh giới ở đây là ranh giới cứng, không phải tuỳ chọn.
#
# Cả ba hàm công khai đều nhận **`Document` hoặc văn bản `.srt` thô**. Lý do
# không phải là tiện tay: khoảng trắng thừa cuối dòng — thứ build-spec-v2 nêu
# đích danh và file thật của người dùng đang mắc — **không tồn tại** trong một
# `Document`, vì tokenizer coi khoảng trắng là ranh giới cụm rồi renderer ghép
# lại bằng đúng một dấu cách. Muốn bắt được lỗi đó thì buộc phải nhìn vào dòng
# chữ nguyên văn nằm trên đĩa.


def _has_letters(text: str) -> bool:
    """Có ít nhất một chữ cái — dùng để loại ra những block chỉ có dấu và số."""
    return any(ch.isalpha() for ch in text)


#: Chữ cái **chỉ tiếng Việt mới có**, không bao giờ xuất hiện trong pinyin.
#:
#: Pinyin có dấu thanh dùng `ā á ǎ à ē é ě è ī í ǐ ì ō ó ǒ ò ū ú ǔ ù ü ǖ ǘ ǚ ǜ
#: ń ň ǹ` — tức là có phần chồng lấn với tiếng Việt ở `á à é è í ì ó ò ú ù`.
#: Danh sách dưới đây cố ý chỉ giữ phần **không** chồng lấn: các nguyên âm có
#: dấu mũ/dấu móc/dấu trăng, chữ `đ`, dấu hỏi và dấu ngã, cộng nguyên khối
#: U+1EA0..U+1EF9 (`ạ ả ấ ầ ẩ ẫ ậ ắ ằ …`) mà pinyin không dùng chữ nào.
_VIETNAMESE_ONLY = set("ăâđêôơưĂÂĐÊÔƠƯãẽĩõũỹÃẼĨÕŨỸảẻỉỏủỷẢẺỈỎỦỶ")


def _has_vietnamese_letters(text: str) -> bool:
    """Dòng chữ này có chữ cái đặc trưng tiếng Việt không?

    Đây là **dấu hiệu dương** cho biết một file là phụ đề tiếng Việt, dùng thay
    cho phép đếm số dòng ở những file mà lời dịch bị ngắt xuống hai dòng.
    """
    for ch in text:
        if ch in _VIETNAMESE_ONLY:
            return True
        if "Ạ" <= ch <= "ỹ":
            return True
    return False


@dataclass(frozen=True)
class _SideCue:
    """Một block của *một phía* (file tiếng Trung hoặc file tiếng Việt).

    Đây là mẫu số chung giữa hai kiểu đầu vào: văn bản `.srt` thô và `Document`
    đã dựng. Nhờ nó mà luật chỉ phải viết một lần, còn việc "lấy dữ liệu ở đâu"
    gói gọn trong hai hàm dựng ở dưới.

    `lines` giữ **nguyên văn** các dòng nội dung, kể cả khoảng trắng thừa — đó
    chính là thứ `VI_TRAILING_SPACE` đi tìm.
    """

    ordinal: int  # thứ tự xuất hiện, 1-based; dùng khi số thứ tự trong file hỏng
    index: int | None
    start: float | None
    end: float | None
    stamp: str  # mốc thời gian đã chuẩn hoá, để so được giữa hai kiểu đầu vào
    lines: tuple[str, ...]
    known_shape: bool  # có biết block gốc có mấy dòng không?

    @property
    def head(self) -> str:
        """Dòng nội dung đầu tiên — với file tiếng Trung đây là dòng Hán."""
        return self.lines[0] if self.lines else ""

    @property
    def text(self) -> str:
        """Toàn bộ nội dung trên một dòng.

        Ghép bằng một dấu cách để một câu tiếng Việt bị xuống dòng (lỗi hình
        dạng, `BLOCK_SHAPE` báo riêng) vẫn đọc được thành một câu khi đi so
        marker với file tiếng Trung.
        """
        return " ".join(self.lines)

    def where(self) -> int | None:
        """Số cue để hiển thị cho người dùng: ưu tiên số ghi trong file."""
        return self.index if self.index is not None else self.ordinal


def _cue_text(cue: object) -> str:
    """Lấy dòng chữ của một `Cue`, giữ nguyên khoảng trắng nếu còn giữ được.

    Ba đường, xếp theo mức độ trung thành giảm dần:

    1. một thuộc tính/khoá `vi` do chặng dịch gắn vào — trung thành tuyệt đối;
    2. cue chỉ có **đúng một** token: trả thẳng `token.zh`, tức nguyên văn cả
       dòng kể cả khoảng trắng hai đầu. Đây là cách chặng dịch nên dựng cue
       tiếng Việt, vì tiếng Việt không có khái niệm "cụm" như dòng Hán;
    3. còn lại thì render như dòng Hán — đúng nội dung nhưng khoảng trắng đã bị
       renderer chuẩn hoá, nên `VI_TRAILING_SPACE` sẽ không còn gì để thấy.

    Dùng `getattr` chứ không đụng vào `srtgen/core/token.py`: module đó là hợp
    đồng chung, thêm trường cho riêng validator là việc của chủ file đó.
    """
    for attr in ("vi", "vi_text", "text"):
        value = getattr(cue, attr, None)
        if isinstance(value, str) and value:
            return value
    meta = getattr(cue, "meta", None)
    if isinstance(meta, dict):
        for key in ("vi", "vi_text", "text"):
            value = meta.get(key)
            if isinstance(value, str) and value:
                return value

    tokens = list(getattr(cue, "tokens", None) or [])
    if len(tokens) == 1:
        return str(getattr(tokens[0], "zh", "") or "")
    if not tokens:
        return ""

    from srtgen.core.token import render_zh

    return render_zh(tokens)


def _side_from_text(srt_text: str) -> list[_SideCue]:
    """Dựng danh sách block từ văn bản `.srt` thô, không sửa một ký tự nào."""
    text = srt_text.lstrip("﻿")
    out: list[_SideCue] = []
    for block in _split_blocks(text):
        lines = block.lines
        head = lines[0].strip() if lines else ""
        index = int(head) if head.isdigit() else None
        stamp_raw = lines[1].strip() if len(lines) > 1 else ""
        span = _parse_timestamp_line(stamp_raw)
        if span is None:
            start = end = None
            stamp = stamp_raw
        else:
            start, end = span
            stamp = f"{_fmt_time(start)} --> {_fmt_time(end)}"
        out.append(
            _SideCue(
                ordinal=block.ordinal,
                index=index,
                start=start,
                end=end,
                stamp=stamp,
                lines=tuple(lines[2:]),
                known_shape=True,
            )
        )
    return out


def _side_from_document(doc: "Document") -> list[_SideCue]:
    """Dựng danh sách block từ một `Document` đã có sẵn trong bộ nhớ.

    `known_shape=False` vì một `Document` không còn nhớ file gốc có mấy dòng:
    hình dạng block chỉ tồn tại ở dạng văn bản. Bỏ qua còn hơn báo bừa.
    """
    out: list[_SideCue] = []
    for ordinal, cue in enumerate(list(getattr(doc, "cues", None) or []), start=1):
        try:
            start = float(getattr(cue, "start", 0.0))
            end = float(getattr(cue, "end", 0.0))
        except (TypeError, ValueError):
            start = end = 0.0
        raw_index = getattr(cue, "index", ordinal)
        index = int(raw_index) if isinstance(raw_index, (int, float)) else None
        out.append(
            _SideCue(
                ordinal=ordinal,
                index=index,
                start=start,
                end=end,
                stamp=f"{_fmt_time(start)} --> {_fmt_time(end)}",
                lines=(_cue_text(cue),),
                known_shape=False,
            )
        )
    return out


def _as_side(source: "Document | str") -> list[_SideCue]:
    """Một cửa duy nhất cho cả hai kiểu đầu vào."""
    if isinstance(source, str):
        return _side_from_text(source)
    return _side_from_document(source)


def looks_like_vi_document(doc: "Document | str") -> bool:
    """File này là phụ đề tiếng Việt hay phụ đề Hán–pinyin?

    Dấu hiệu quyết định là **chữ Hán**, không phải số dòng. Số dòng là dấu hiệu
    build-spec nêu (file vi 3 dòng, file zh 4 dòng) và nó đúng — nhưng nó chỉ
    đọc được từ văn bản thô, còn `corpus/raw.srt` cũng là file 3 dòng mà lại
    toàn chữ Hán. Chữ Hán thì phân biệt được ở cả hai kiểu đầu vào: một file phụ
    đề tiếng Trung dài hàng trăm cue không thể không có lấy một chữ Hán, còn một
    file tiếng Việt thì không bao giờ có.

    Điều kiện xác nhận thứ hai tồn tại để một tài liệu **chỉ có pinyin** (chữ
    Latin, không Hán) không bị nhận nhầm là tiếng Việt, và nó nhận hai dấu hiệu,
    chỉ cần một là đủ:

    * **chữ cái riêng của tiếng Việt** (`ă â đ ê ô ơ ư`, dấu hỏi/ngã, khối
      U+1EA0..U+1EF9) — pinyin không bao giờ có chúng, xem
      :func:`_has_vietnamese_letters`;
    * hình dạng block: file vi 3 dòng, file zh 4 dòng (chỉ đọc được từ văn bản
      thô), hoặc "phần lớn cụm chưa có pinyin" với một `Document`.

    Vì sao phải có dấu hiệu thứ nhất chứ không chỉ đếm dòng: một file `_vi.srt`
    mà phần lớn lời dịch bị ngắt xuống 2 dòng có ít hơn một nửa số block 3 dòng,
    và bản cũ đẩy nó sang bộ luật Hán–pinyin. Kết quả là hàng loạt lời khuyên vô
    nghĩa kiểu *'Thấy "Tập 1:", phải đổi thành "："'* và *'dòng Hán có 2 cụm
    nhưng dòng pinyin có 3 cụm'* — nghĩa là chính lỗi ngắt dòng cần báo lại bị
    chôn dưới một đống lỗi ma. Ngắt dòng vẫn bị bắt, nhưng bằng `BLOCK_SHAPE`
    của bộ luật tiếng Việt, đúng mã và đúng câu chữ.

    Trả về `False` cho tài liệu rỗng: không có gì để đọc thì không kết luận gì.
    """
    if isinstance(doc, str):
        cues = _side_from_text(doc)
        if not cues:
            return False
        texts = [c.text for c in cues]
        if any(_has_han(t) for t in texts):
            return False
        if not any(_has_letters(t) for t in texts):
            return False
        if any(_has_vietnamese_letters(t) for t in texts):
            return True
        three_line = sum(1 for c in cues if len(c.lines) == 1)
        return three_line * 2 > len(cues)

    cue_list = list(getattr(doc, "cues", None) or [])
    if not cue_list:
        return False

    from srtgen.core.token import KIND_WORD

    all_tokens = [t for cue in cue_list for t in getattr(cue, "tokens", None) or []]
    words = [t for t in all_tokens if t.kind == KIND_WORD]
    if not words:
        return False
    if any(_has_han(str(t.zh or "")) for t in all_tokens):
        return False
    if not any(_has_letters(str(t.zh or "")) for t in words):
        return False
    if any(_has_vietnamese_letters(str(t.zh or "")) for t in words):
        return True
    with_pinyin = sum(1 for t in words if str(t.pinyin or "").strip())
    return with_pinyin * 2 < len(words)


def _marker_shape(line: str) -> tuple[int, bool]:
    """(số marker đổi người nói, có mở đầu dòng bằng marker không).

    Dùng lại đúng bộ tách của dòng Hán nên `-` dính giữa hai chữ (`Cô-ca`,
    `2019-2020`) không bị tính là marker — chỉ `-` đứng đầu dòng hoặc có khoảng
    trắng kèm bên cạnh mới được tính, giống hệt luật ở README.

    So theo **số lượng và chỗ mở lượt** chứ không theo chỉ số cụm: một dòng
    tiếng Việt và một dòng Hán không bao giờ có cùng số cụm, nên "marker đứng
    sau cụm thứ 3" là phép so vô nghĩa giữa hai ngôn ngữ.
    """
    positions = _marker_positions(_lex_line(line))
    return len(positions), bool(positions) and positions[0] == 0


def _validate_vi_cues(cues: Sequence[_SideCue]) -> list[Finding]:
    """Thân của `validate_vi_document`, tách ra để `validate_pair` dùng lại.

    Không có luật dấu câu nào ở đây, và đó là chủ ý: dòng tiếng Việt dùng dấu
    ASCII bình thường (build-spec-v2 mục 2, ràng buộc 6).
    """
    findings: list[Finding] = []
    prev_index: int | None = None
    prev_end: float | None = None

    for cue in cues:
        where = cue.where()

        if cue.index is None:
            findings.append(
                _mk(
                    INDEX_SEQ,
                    None,
                    f"Block thứ {cue.ordinal} của file tiếng Việt: dòng đầu tiên phải là "
                    f"số thứ tự.",
                )
            )
        else:
            expected = 1 if prev_index is None else prev_index + 1
            if cue.index != expected:
                if prev_index is None:
                    msg = (
                        f"File tiếng Việt: số thứ tự phải bắt đầu từ 1, nhưng cue đầu tiên "
                        f"đang đánh số {cue.index}."
                    )
                else:
                    msg = (
                        f"File tiếng Việt: số thứ tự không liên tục, sau cue {prev_index} "
                        f"phải là {expected} nhưng đang là {cue.index}."
                    )
                findings.append(_mk(INDEX_SEQ, cue.index, msg + " Cần đánh số lại từ 1 liên tục."))
            prev_index = cue.index

        if cue.start is None or cue.end is None:
            findings.append(
                _mk(
                    TIMESTAMP_FORMAT,
                    where,
                    f"Cue {where} của file tiếng Việt: mốc thời gian sai định dạng. "
                    f'Thấy "{cue.stamp}", phải viết đúng dạng '
                    f'"00:01:19,580 --> 00:01:22,100".',
                    cue.stamp,
                )
            )
        else:
            if cue.start >= cue.end:
                findings.append(
                    _mk(
                        TIMESTAMP_ORDER,
                        where,
                        f"Cue {where} của file tiếng Việt: thời điểm bắt đầu không nhỏ hơn "
                        f'thời điểm kết thúc ("{_fmt_time(cue.start)}" --> '
                        f'"{_fmt_time(cue.end)}").',
                        cue.stamp,
                    )
                )
            if prev_end is not None and cue.start < prev_end:
                findings.append(
                    _mk(
                        TIMESTAMP_ORDER,
                        where,
                        f"Cue {where} của file tiếng Việt: bắt đầu lúc "
                        f"{_fmt_time(cue.start)} trong khi cue trước kéo dài tới "
                        f"{_fmt_time(prev_end)}. Hai cue chồng lấn thời gian, Aegisub sẽ "
                        f"báo lỗi.",
                        cue.stamp,
                    )
                )
            prev_end = max(prev_end, cue.end) if prev_end is not None else cue.end

        if not any(line.strip() for line in cue.lines):
            findings.append(
                _mk(
                    VI_EMPTY_LINE,
                    where,
                    f"Cue {where}: chưa có dòng tiếng Việt nào. Không được xoá cue để bù — "
                    f"hai file phải luôn khớp chỉ số, nên hãy điền lời dịch, hoặc dấu ♪ cho "
                    f"đoạn chỉ có nhạc nền.",
                )
            )
        elif cue.known_shape and len(cue.lines) > 1:
            joined = " ⏎ ".join(cue.lines)
            findings.append(
                _mk(
                    BLOCK_SHAPE,
                    where,
                    f"Cue {where}: block của file tiếng Việt có {len(cue.lines) + 2} dòng, "
                    f"phải có đúng 3 dòng (số thứ tự, mốc thời gian, một dòng tiếng Việt). "
                    f'Lời dịch đang bị ngắt làm {len(cue.lines)} dòng: "{joined[:160]}".',
                    cue.lines[0],
                )
            )

        for line in cue.lines:
            if line and _is_space(line[0]):
                findings.append(
                    _mk(
                        VI_TRAILING_SPACE,
                        where,
                        f"Cue {where}: dòng tiếng Việt bắt đầu bằng khoảng trắng. "
                        f'Thấy "{_excerpt(line, 0, 6)}", phải bỏ khoảng trắng đầu dòng.',
                        line,
                    )
                )
            if line and _is_space(line[-1]):
                findings.append(
                    _mk(
                        VI_TRAILING_SPACE,
                        where,
                        f"Cue {where}: dòng tiếng Việt kết thúc bằng khoảng trắng. "
                        f'Thấy "{_excerpt(line, max(0, len(line) - 6), len(line))}", '
                        f"phải bỏ khoảng trắng cuối dòng.",
                        line,
                    )
                )
            for m in _DOUBLE_SPACE_RE.finditer(line):
                findings.append(
                    _mk(
                        DOUBLE_SPACE,
                        where,
                        f"Cue {where}: dòng tiếng Việt có {m.end() - m.start()} khoảng "
                        f'trắng liền nhau. Thấy "{_excerpt(line, m.start(), m.end())}", '
                        f"chỉ được dùng đúng 1 khoảng trắng.",
                        line,
                    )
                )

    return findings


def validate_vi_document(doc: "Document | str") -> list[Finding]:
    """Kiểm **riêng** một file phụ đề tiếng Việt, không cần tới file tiếng Trung.

    Nhận `Document` hoặc văn bản `.srt` thô. Đưa văn bản thô vào thì kiểm được
    thêm hai thứ chỉ tồn tại trên đĩa: hình dạng block (phải đúng 3 dòng) và
    khoảng trắng thừa đầu/cuối dòng.

    Những ràng buộc cần **cả hai** file — số block bằng nhau, mốc thời gian
    trùng khít, marker đổi người nói cùng chỗ — nằm ở `validate_pair`.
    """
    return _dedupe(_validate_vi_cues(_as_side(doc)))


def validate_pair(zh_doc: "Document | str", vi_doc: "Document | str") -> list[Finding]:
    """Kiểm cặp `<title>.srt` + `<title>_vi.srt` theo build-spec-v2 mục 2.

    Kết quả gồm toàn bộ luật riêng của file tiếng Việt (`validate_vi_document`)
    cộng ba ràng buộc chỉ nhìn thấy được khi đặt hai file cạnh nhau:
    `VI_BLOCK_COUNT`, `VI_TIMESTAMP_DRIFT`, `VI_MARKER_SYNC`.

    Hàm này **cố ý không** chạy luật định dạng Hán–pinyin lên `zh_doc`. Người
    soát cần trả lời được câu "cặp file này có khớp nhau không" mà không bị vùi
    dưới hàng trăm lỗi dấu câu của file tiếng Trung — đó là việc của
    `validate_text` / `srtgen fix`, gọi riêng và báo cáo riêng.

    Khi số block đã lệch thì phần so từng cue chỉ chạy trên đoạn đầu chung nhau:
    so tiếp phần đuôi sẽ cho hàng nghìn dòng "lệch mốc thời gian" mà nguyên nhân
    thật chỉ có một, và người dùng cuối sẽ không tìm ra nó giữa đống ấy.
    """
    zh_cues = _as_side(zh_doc)
    vi_cues = _as_side(vi_doc)

    findings: list[Finding] = list(_validate_vi_cues(vi_cues))

    if len(zh_cues) != len(vi_cues):
        findings.append(
            _mk(
                VI_BLOCK_COUNT,
                None,
                f"File tiếng Trung có {len(zh_cues)} block nhưng file tiếng Việt có "
                f"{len(vi_cues)} block. Hai file phải cùng số block, cue thứ i của file "
                f"này ứng với cue thứ i của file kia; lệch số block nghĩa là toàn bộ phần "
                f"sau đó đã lệch dòng. Không được xoá hay gộp cue để bù.",
            )
        )

    for zh_cue, vi_cue in zip(zh_cues, vi_cues):
        where = vi_cue.where()

        if zh_cue.stamp != vi_cue.stamp:
            findings.append(
                _mk(
                    VI_TIMESTAMP_DRIFT,
                    where,
                    f"Cue {where}: mốc thời gian hai file không giống nhau. File tiếng "
                    f'Trung ghi "{zh_cue.stamp}", file tiếng Việt ghi "{vi_cue.stamp}". '
                    f"Dòng tiếng Việt phải dùng đúng mốc thời gian của dòng tiếng Trung, "
                    f"từng ký tự một.",
                    vi_cue.stamp,
                )
            )

        zh_count, zh_leads = _marker_shape(zh_cue.head)
        vi_count, vi_leads = _marker_shape(vi_cue.text)
        if (zh_count, zh_leads) != (vi_count, vi_leads):
            findings.append(
                _mk(
                    VI_MARKER_SYNC,
                    where,
                    f'Cue {where}: marker đổi người nói "-" lệch giữa hai file. Dòng tiếng '
                    f"Trung có {zh_count} marker, dòng tiếng Việt có {vi_count}. "
                    f'Hán: "{zh_cue.head}" — Việt: "{vi_cue.text}". Cue có hai lượt thoại '
                    f"thì dòng tiếng Việt phải giữ đúng số marker và đúng chỗ mở lượt.",
                    vi_cue.text,
                )
            )

    return _dedupe(findings)
