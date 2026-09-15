"""S4 — chia dòng phụ đề (cue) từ mốc thời gian từng chữ.

Đây là chặng quyết định file có "giống bản chuẩn" hay không. Whisper trả về đoạn dài
và cắt tuỳ tiện theo hơi thở của người nói; bản chuẩn thì cắt theo nhịp thoại, trung
vị chỉ 1.32 giây và 5 Hán tự một dòng. Không có chặng này thì mọi chặng sau vẫn đúng
về định dạng nhưng file đọc lên vẫn "không giống người làm tay".

Thứ tự ưu tiên điểm cắt (plan.md mục S4, build-spec mục 7):

1. sau dấu kết câu ``。？！`` (và ``……``)
2. sau dấu ngắt ``，、`` — nhưng chỉ khi dòng đang gom đã đủ dài, xem dưới
3. tại khoảng lặng dài hơn ``cue.silence_split``
4. ép cắt khi vượt ``cue.max_chars`` hoặc ``cue.max_duration``

Vì sao dấu phẩy có thêm điều kiện độ dài
-----------------------------------------
Đo trên ``corpus/completed.srt``: 1124/1351 cue kết thúc bằng ``。？！``, nhưng chỉ
75 cue kết thúc bằng ``，、`` trong khi 148 cue mang dấu phẩy ở GIỮA dòng. Nói cách
khác người biên tập cắt ở khoảng một phần ba số dấu phẩy, phần còn lại thì không.
Cắt ở mọi dấu phẩy làm dòng vụn ra (1502 dòng, trung vị 4 Hán tự); không cắt ở dấu
phẩy nào thì dòng dài quá. Điều kiện "dòng đang gom đã có ít nhất
``comma_min_chars`` Hán tự" là cách rẻ nhất tái tạo được lựa chọn đó.

Số đo dựng lại luồng từ của chính corpus rồi chia lại (7579 từ, 1350 ranh giới thật):

===================  =======  =======  =======  ======
ngưỡng               chính xác  đầy đủ   F1       số dòng
===================  =======  =======  =======  ======
0 (mọi dấu phẩy)     0.8493   0.9563   0.8997   1521
4                    0.9207   0.9378   0.9292   1376
**5 (đang dùng)**    0.9327   0.9341   **0.9334**   **1353**
6                    0.9373   0.9296   0.9334   1340
8                    0.9397   0.9237   0.9316   1328
không cắt ở phẩy     0.9359   0.9081   0.9218   1311
===================  =======  =======  =======  ======

Ngưỡng 5 cho số dòng gần bản chuẩn nhất (1353 so với 1351 thật) và F1 cao nhất, nên
đó là giá trị mặc định. Số này KHÔNG có trong ``default.yaml`` (bản cấu hình viết
trước phép đo này), nên nó đọc ``cue.comma_min_chars`` nếu có và rơi về hằng số
:data:`COMMA_MIN_CHARS` nếu không — thêm khoá đó vào YAML là đủ để chỉnh, không phải
sửa code.

Cue quá ngắn thì KHÔNG gộp
---------------------------
``min_duration`` của bản chuẩn là **0.25 giây**, không phải 0.8 như plan.md viết:
đo thực tế 161/1351 cue ngắn hơn 0.8 giây và cue ngắn nhất là 0.24 giây. Gộp cue
ngắn theo kiểu thông thường sẽ xoá mất đúng cái nhịp làm nên bản chuẩn. Vì vậy
:func:`enforce_timing` chỉ NỚI ĐUÔI một cue quá ngắn cho đủ ``min_duration`` khi
phía trước cue kế tiếp còn chỗ trống, và không bao giờ gộp hai cue lại.
Tương tự ``min_gap`` mặc định là **0.0** vì 261 cue của bản chuẩn có khoảng cách
đúng bằng 0 — ép giãn ra là tự làm lệch khỏi bản chuẩn.

Marker đổi người nói: KHÔNG có ở phiên bản 1
---------------------------------------------
Bản chuẩn dùng dấu ``-`` để tách hai người nói trong cùng một dòng (15/1351 cue).
Whisper không cho biết ai đang nói (muốn biết phải thêm diarization, nặng và cần
token HuggingFace), nên phiên bản này không tự sinh dấu đó. Điều này được ghi vào
``S4_cues.json`` để bản báo cáo nói thẳng với người dùng thay vì để họ tự phát hiện.

Đầu vào ``S3_clean.json`` -> đầu ra ``S4_cues.json``::

    {"meta": {..., "segmented": false},
     "cues": [{"index": 1, "start": 1.2, "end": 3.4, "text": "你好。",
               "words": [{"word": "你", "start": .., "end": ..}], "reason": "end"}],
     "stats": {...}, "target": {...}, "comparison": {...}, "params": {...}}

``cues[i]["text"]`` là văn bản thô của ASR, CHƯA tách cụm — việc tách cụm là của S5.
Vì vậy ``meta["segmented"]`` để ``false``.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping, Sequence

from srtgen.core.token import is_han
from srtgen.stages.s3_cleanup import StageCancelled, Word

if TYPE_CHECKING:  # chỉ để gợi ý kiểu, không tạo phụ thuộc cứng lúc chạy
    from srtgen.core.context import Context

__all__ = [
    "STAGE_NO",
    "STAGE_NAME",
    "STAGE_NUMBER",
    "STAGE",
    "PREV_STAGE",
    "SENTENCE_ENDERS",
    "PAUSE_PUNCT",
    "ASCII_ENDERS",
    "ASCII_PAUSES",
    "ELLIPSIS_CHAR",
    "COMMA_MIN_CHARS",
    "MIN_CUE_TICK",
    "DEFAULT_CUE",
    "TARGET_STATS",
    "SPEAKER_MARKER_NOTE",
    "StageCancelled",
    "Word",
    "CueDraft",
    "cue_params",
    "iter_words",
    "split_words",
    "enforce_timing",
    "percentile",
    "cue_stats",
    "compare_to_target",
    "run",
]

STAGE_NO = 4
STAGE_NAME = "cues"
PREV_STAGE = (3, "clean")

#: Bí danh: mỗi chặng do một người viết nên tên hằng số này đang có ba biến thể
#: (``STAGE_NO`` / ``STAGE_NUMBER`` / ``STAGE``). Khai báo đủ cả ba để người viết
#: CLI gọi kiểu nào cũng chạy, thay vì phải nhớ chặng nào dùng tên nào.
STAGE_NUMBER = STAGE_NO
STAGE = STAGE_NO

#: Dấu kết câu và dấu ngắt, bản tiếng Trung. Giá trị thật lấy từ ``cfg["cue"]``;
#: mấy hằng này chỉ là đường lui khi người gọi không đưa cấu hình.
SENTENCE_ENDERS = "。？！"
PAUSE_PUNCT = "，、"

#: Bản ASCII. Bắt buộc phải nhìn cả hai bộ vì S3 CỐ Ý không chuẩn hoá dấu câu
#: (việc đó của S6, làm trên token). Whisper hay trả ``你好.`` lẫn ``你好。`` trong
#: cùng một video; nếu chỉ nhìn bộ tiếng Trung thì nửa số điểm cắt biến mất.
ASCII_ENDERS = ".?!"
ASCII_PAUSES = ",;"

ELLIPSIS_CHAR = "…"

#: Số Hán tự tối thiểu đã gom được thì mới cho phép cắt ở dấu ``，、``.
#: Đo trên corpus, xem docstring đầu file. Ghi đè bằng ``cfg["cue"]["comma_min_chars"]``.
COMMA_MIN_CHARS = 5

#: Độ dài tối thiểu tuyệt đối của một cue, tính bằng giây. Không phải tham số chất
#: lượng mà là ràng buộc định dạng: ``start`` phải NHỎ HƠN ``end`` sau khi làm tròn
#: về mili-giây, nếu không validator báo ``TIMESTAMP_ORDER`` và Aegisub kêu.
MIN_CUE_TICK = 0.01

#: Đường lui khi thiếu cấu hình. Các con số này đo trên corpus, xem build-spec mục 7.
DEFAULT_CUE: dict[str, Any] = {
    "max_chars": 18,
    "max_duration": 6.0,
    "min_duration": 0.25,
    "min_gap": 0.0,
    "silence_split": 0.30,
    "break_after_end": SENTENCE_ENDERS,
    "break_after_pause": PAUSE_PUNCT,
    "comma_min_chars": COMMA_MIN_CHARS,
}

#: Phân bố của bản chuẩn, để bản báo cáo đặt cạnh phân bố vừa sinh ra.
#: Đo trên toàn bộ 1351 cue của ``corpus/completed.srt``.
TARGET_STATS: dict[str, Any] = {
    "source": "corpus/completed.srt (1351 cue đã có người soát)",
    "count": 1351,
    "duration": {"median": 1.32, "p95": 2.92, "min": 0.24, "max": 11.16},
    "han": {"median": 5, "p95": 12, "max": 17},
    "clusters": {"median": 3, "p95": 8, "max": 12},
}

SPEAKER_MARKER_NOTE = (
    "Phiên bản này không tự đánh dấu “-” khi hai người nói trong cùng một dòng. "
    "Máy chỉ nghe được lời, không biết ai đang nói, nên việc đó dành cho người soát "
    "(bản mẫu có 15 dòng như vậy trên tổng số 1351 dòng)."
)


# --------------------------------------------------------------------------- #
# Mô hình dữ liệu
# --------------------------------------------------------------------------- #

@dataclass
class CueDraft:
    """Một dòng phụ đề khi chưa tách cụm.

    Chưa phải :class:`srtgen.core.token.Cue` vì ở đây chưa có token nào: việc tách
    cụm và sinh pinyin là của S5, và trộn hai việc đó vào một chặng chính là lỗi
    kiến trúc mà bản viết lại này tồn tại để tránh (plan.md mục 1).
    """

    index: int
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)
    reason: str = ""  # vì sao cắt ở đây: end | pause | silence | limit | eof

    def duration(self) -> float:
        return self.end - self.start

    def han_count(self) -> int:
        return sum(1 for ch in self.text if is_han(ch))

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "text": self.text,
            "han": self.han_count(),
            "reason": self.reason,
            "words": [w.to_dict() for w in self.words],
        }


# --------------------------------------------------------------------------- #
# Cấu hình
# --------------------------------------------------------------------------- #

def cue_params(cfg: Mapping[str, Any] | None) -> dict[str, Any]:
    """Rút các tham số chia cue ra khỏi cấu hình đầy đủ.

    Nhận cả ``cfg["cue"]["max_chars"]`` lẫn ``cfg["max_chars"]`` để hàm thuần tuý
    :func:`split_words` thử được mà không phải dựng cả cây cấu hình.
    """
    source: Mapping[str, Any] = {}
    if isinstance(cfg, Mapping):
        nested = cfg.get("cue")
        source = nested if isinstance(nested, Mapping) else cfg
    return {
        "max_chars": max(1, _as_int(source.get("max_chars"), DEFAULT_CUE["max_chars"])),
        "max_duration": max(
            0.1, _as_float(source.get("max_duration"), DEFAULT_CUE["max_duration"])
        ),
        "min_duration": max(
            0.0, _as_float(source.get("min_duration"), DEFAULT_CUE["min_duration"])
        ),
        "min_gap": max(0.0, _as_float(source.get("min_gap"), DEFAULT_CUE["min_gap"])),
        "silence_split": max(
            0.0, _as_float(source.get("silence_split"), DEFAULT_CUE["silence_split"])
        ),
        "break_after_end": _as_str(
            source.get("break_after_end"), DEFAULT_CUE["break_after_end"]
        ),
        "break_after_pause": _as_str(
            source.get("break_after_pause"), DEFAULT_CUE["break_after_pause"]
        ),
        "comma_min_chars": max(
            0, _as_int(source.get("comma_min_chars"), DEFAULT_CUE["comma_min_chars"])
        ),
    }


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_str(value: Any, default: str) -> str:
    return value if isinstance(value, str) and value else default


# --------------------------------------------------------------------------- #
# Luồng từ
# --------------------------------------------------------------------------- #

def iter_words(segments: Iterable[Any]) -> list[Word]:
    """Trải mọi segment của S3 thành một luồng từ liên tục.

    Ranh giới segment của Whisper bị BỎ HẲN chứ không dùng làm điểm cắt: chúng phản
    ánh chỗ Whisper hết bộ đệm, không phải chỗ người nói ngắt câu. Giữ lại chúng
    chính là lý do bản thô chia dòng không giống bản chuẩn.

    Segment nào không có mốc thời gian từng chữ thì được dựng từ ``text`` với thời
    gian chia đều — kém chính xác nhưng vẫn cho ra file dùng được, thay vì hỏng cả
    lần chạy chỉ vì một tuỳ chọn của chặng trước bị tắt.
    """
    out: list[Word] = []
    for seg in segments:
        start = _seg_float(seg, ("start", "begin", "t0"), 0.0)
        end = _seg_float(seg, ("end", "stop", "t1"), start)
        raw_words = _seg_get(seg, "words", None) or []
        words: list[Word] = []
        for item in raw_words:
            if isinstance(item, Word):
                word = item
            else:
                word = Word.from_any(item, fallback_start=start, fallback_end=end)
            if word is not None and word.text.strip():
                words.append(word)
        text = str(_seg_get(seg, "text", "") or "")
        if not words:
            words = _synth_words(text, start, end)
        else:
            words = _align_to_text(words, text)
        out.extend(words)
    return _repair_timeline(out)


def _seg_get(seg: Any, key: str, default: Any) -> Any:
    if isinstance(seg, Mapping):
        return seg.get(key, default)
    return getattr(seg, key, default)


def _seg_float(seg: Any, keys: Sequence[str], default: float) -> float:
    for key in keys:
        value = _seg_get(seg, key, None)
        if value is None or isinstance(value, bool):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return default


#: Khoảng ký tự bị bỏ qua dài hơn ngần này thì coi như hai chuỗi không cùng gốc.
_ALIGN_MAX_SKIP = 32


def _align_to_text(words: list[Word], text: str) -> list[Word]:
    """Trả lại cho danh sách từ những ký tự chỉ có trong ``text`` của segment.

    Vì sao cần: một vài bản Whisper trả về ``words`` đã lược mất dấu câu, trong khi
    ``text`` của cùng segment thì vẫn có. Mà dấu câu chính là tín hiệu cắt số một
    của chặng này (1124/1351 dòng của bản chuẩn kết thúc bằng ``。？！``) — mất nó
    thì cue chỉ còn được cắt bằng khoảng lặng và bằng giới hạn độ dài, tức là hỏng
    hẳn chất lượng chia dòng mà không có dấu hiệu gì báo ra ngoài.

    Cách làm: coi ``words`` là dãy con của ``text``, quét một lượt, ký tự nào bị bỏ
    qua thì dính vào từ đứng trước (đúng chỗ nó thuộc về trong tiếng Trung). Không
    khớp được thì trả nguyên danh sách cũ — thà thiếu dấu câu còn hơn ghép bừa chữ
    của câu khác vào.
    """
    if not words or not text.strip():
        return words
    if _squeeze("".join(w.text for w in words)) == _squeeze(text):
        return words

    out: list[Word] = []
    cursor = 0
    for word in words:
        chars = [ch for ch in word.text if not ch.isspace()]
        if not chars:
            continue
        first = text.find(chars[0], cursor)
        if first < 0 or first - cursor > _ALIGN_MAX_SKIP:
            return words
        lead = text[cursor:first]
        cursor = first
        piece = ""
        for ch in chars:
            found = text.find(ch, cursor)
            if found < 0 or found - cursor > _ALIGN_MAX_SKIP:
                return words
            piece += text[cursor:found + 1]
            cursor = found + 1
        if lead and out:
            out[-1].text += lead
        elif lead:
            piece = lead + piece
        out.append(
            Word(
                text=piece,
                start=word.start,
                end=word.end,
                probability=word.probability,
            )
        )
    if not out:
        return words
    if cursor < len(text):
        out[-1].text += text[cursor:]
    return out


def _squeeze(text: str) -> str:
    return "".join(text.split())


def _synth_words(text: str, start: float, end: float) -> list[Word]:
    """Dựng từ giả từ một câu không có mốc thời gian, chia đều theo số ký tự.

    Mỗi ký tự (kèm dấu câu dính ngay sau nó) thành một "từ". Cách chia này giữ được
    thứ tự và tổng thời lượng, đủ để :func:`split_words` làm việc; sai số nằm trong
    lòng một segment và được ghi rõ trong cảnh báo của S3.
    """
    chunks: list[str] = []
    for i, ch in enumerate(text):
        if ch.isspace():
            continue
        # "3.5" và "1,200" là một số, không phải chữ rồi dấu chấm câu.
        decimal = ch in ".," and _between_digits(text, i)
        joins_number = ch.isdigit() and chunks and _ends_in_number(chunks[-1])
        if chunks and (decimal or joins_number or _is_trailing_punct(ch)):
            chunks[-1] += ch
        else:
            chunks.append(ch)
    if not chunks:
        return []
    span = max(end - start, 0.0)
    total = sum(len(c) for c in chunks)
    out: list[Word] = []
    cursor = start
    for i, chunk in enumerate(chunks):
        width = span * len(chunk) / total if total else 0.0
        stop = end if i == len(chunks) - 1 else cursor + width
        out.append(Word(text=chunk, start=cursor, end=max(stop, cursor)))
        cursor = stop
    return out


def _is_trailing_punct(ch: str) -> bool:
    """Dấu câu dính vào chữ đứng TRƯỚC nó (không phải dấu mở ngoặc)."""
    return ch in "。，、？！：；”』」’…—.,?!:;\"')]}" or ch == ELLIPSIS_CHAR


def _ends_in_number(chunk: str) -> bool:
    """Cụm đang kết thúc bằng một con số (kể cả khi vừa nuốt dấu thập phân)."""
    if not chunk:
        return False
    if chunk[-1].isdigit():
        return True
    return chunk[-1] in ".," and len(chunk) >= 2 and chunk[-2].isdigit()


def _is_punct_tail(text: str) -> bool:
    """Từ này chỉ gồm dấu câu dính sau, ví dụ ``。`` hay vế thứ hai của ``……``.

    Cắt ngay TRƯỚC một từ như vậy sẽ đẩy dấu câu xuống đầu dòng kế tiếp — vừa xấu,
    vừa làm dòng đó vi phạm luật "dấu câu phải dính vào nội dung" của README.
    Whisper hay trả ``……`` thành hai token rời, nên ca này có thật chứ không phải
    phòng xa.
    """
    stripped = text.strip()
    return bool(stripped) and all(_is_trailing_punct(ch) for ch in stripped)


def _repair_timeline(words: list[Word]) -> list[Word]:
    """Ép luồng từ tăng dần theo thời gian.

    Whisper thỉnh thoảng trả về từ có ``end`` nhỏ hơn ``start`` của chính nó, hoặc
    hai từ chồng lấn ở ranh giới segment. Sửa ngay tại đây, một lần, để phần chia
    cue phía sau không phải phòng thủ ở mọi phép trừ.
    """
    cursor = None
    for word in words:
        if cursor is not None and word.start < cursor:
            word.start = cursor
        if word.end < word.start:
            word.end = word.start
        cursor = word.end
    return words


# --------------------------------------------------------------------------- #
# Chia cue
# --------------------------------------------------------------------------- #

def _break_kind(
    text: str,
    enders: frozenset[str],
    pauses: frozenset[str],
    following: str = "",
) -> str:
    """Xem trong một từ có dấu cho phép cắt không: ``"end"`` / ``"pause"`` / ``""``.

    Dấu ``.`` và ``,`` của ASCII bị bỏ qua khi nằm giữa hai chữ số, để "3.5" hay
    "1,200" không bị cắt làm đôi. ``following`` là từ ngay sau, cần thiết vì Whisper
    có thể trả "3." và "5" thành hai token — lúc đó chữ số bên phải nằm ở từ khác.
    """
    kind = ""
    for i, ch in enumerate(text):
        if ch in ".," and _between_digits(text, i, following):
            continue
        if ch in enders:
            return "end"
        if ch in pauses:
            kind = "pause"
    return kind


def _between_digits(text: str, i: int, following: str = "") -> bool:
    if i == 0 or not text[i - 1].isdigit():
        return False
    after = text[i + 1:] or following.lstrip()
    return bool(after) and after[0].isdigit()


def _han_count(words: Sequence[Word]) -> int:
    return sum(1 for w in words for ch in w.text if is_han(ch))


def _dense_len(words: Sequence[Word]) -> int:
    """Số ký tự không phải khoảng trắng — hàng rào cho nội dung không phải chữ Hán.

    ``max_chars`` đếm Hán tự (đúng như bản chuẩn), nên một dòng toàn số hoặc toàn
    chữ Latin sẽ không bao giờ chạm giới hạn đó. Hàng rào này giữ cho dòng như vậy
    không dài vô hạn.
    """
    return sum(1 for w in words for ch in w.text if not ch.isspace())


def _join_text(words: Sequence[Word]) -> str:
    """Ghép các từ lại thành câu, gộp khoảng trắng thừa.

    Whisper thêm space đứng trước từ tiếng Anh và không thêm với chữ Hán, nên phép
    nối thẳng đã ra đúng; chỗ này chỉ dọn khoảng trắng kép cho chắc. KHÔNG chèn
    thêm space nào: khoảng trắng trong file ``.srt`` cuối cùng là ranh giới cụm do
    S5 đặt, không phải thứ chặng này được quyền tạo ra.
    """
    return " ".join("".join(w.text for w in words).split())


def split_words(
    words: Sequence[Word], cfg: Mapping[str, Any] | None = None
) -> list[CueDraft]:
    """Gom luồng từ thành cue theo bốn mức ưu tiên của plan.md.

    Quyết định cắt được lấy SAU mỗi từ, có nhìn trước một từ để biết có sắp vượt
    giới hạn không. Cách này cho ra cùng kết quả với cách "gom quá rồi lùi lại tìm
    điểm cắt" trong mọi ca đo được trên corpus, mà không phải giữ bộ đệm lùi —
    quan trọng với video 40 phút và bộ nhớ của một máy 2017.
    """
    params = cue_params(cfg)
    enders = frozenset(params["break_after_end"] + ASCII_ENDERS + ELLIPSIS_CHAR)
    pauses = frozenset(params["break_after_pause"] + ASCII_PAUSES)
    comma_min = params["comma_min_chars"]
    max_chars = params["max_chars"]
    max_dense = max_chars * 2
    max_duration = params["max_duration"]
    silence = params["silence_split"]

    cues: list[CueDraft] = []
    buffer: list[Word] = []
    total = len(words)
    for i, word in enumerate(words):
        buffer.append(word)
        if i + 1 >= total:
            cues.append(_make_cue(len(cues) + 1, buffer, "eof"))
            buffer = []
            break

        nxt = words[i + 1]
        if _is_punct_tail(nxt.text):
            # Từ kế tiếp chỉ có dấu câu: để nó dính vào dòng này rồi mới xét cắt,
            # nếu không dòng sau sẽ bắt đầu bằng "。" hoặc nửa sau của "……".
            continue

        reason = ""
        kind = _break_kind(word.text, enders, pauses, nxt.text)
        chars = _han_count(buffer)
        if kind == "end":
            reason = "end"
        elif kind == "pause" and chars >= comma_min:
            reason = "pause"
        elif nxt.start - word.end > silence:
            reason = "silence"
        else:
            over_chars = chars + _han_count([nxt]) > max_chars
            over_dense = _dense_len(buffer) + _dense_len([nxt]) > max_dense
            over_time = nxt.end - buffer[0].start > max_duration
            if over_chars or over_dense or over_time:
                reason = "limit"

        if reason:
            cues.append(_make_cue(len(cues) + 1, buffer, reason))
            buffer = []

    if buffer:
        cues.append(_make_cue(len(cues) + 1, buffer, "eof"))
    return [c for c in cues if c.text]


def _make_cue(index: int, words: Sequence[Word], reason: str) -> CueDraft:
    return CueDraft(
        index=index,
        start=min(w.start for w in words),
        end=max(w.end for w in words),
        text=_join_text(words),
        words=list(words),
        reason=reason,
    )


def enforce_timing(
    cues: list[CueDraft], cfg: Mapping[str, Any] | None = None
) -> list[CueDraft]:
    """Ép mốc thời gian tăng dần, không chồng lấn, và đủ dài để hiện được.

    Aegisub cảnh báo khi hai dòng chồng lấn và validator chặn bằng
    ``TIMESTAMP_ORDER``, nên đây là chỗ duy nhất chịu trách nhiệm về bất biến đó.

    Ba việc, theo đúng thứ tự này:

    1. đẩy ``start`` ra sau đuôi cue trước cộng ``min_gap`` (mặc định 0.0 — bản
       chuẩn có 261 cue nối đuôi nhau đúng bằng 0, không được tự ý giãn ra);
    2. nới ``end`` cho đủ ``min_duration``, NHƯNG chỉ trong phần trống trước cue kế
       tiếp — không lấn, và tuyệt đối không gộp cue;
    3. làm tròn về mili-giây (đơn vị của SRT) rồi bảo đảm ``start < end``, vì phép
       làm tròn có thể kéo hai mốc cách nhau nửa mili-giây về cùng một con số.

    Sửa tại chỗ và trả lại chính danh sách đó cho tiện xâu chuỗi.
    """
    params = cue_params(cfg)
    min_gap = params["min_gap"]
    min_duration = params["min_duration"]

    prev_end: float | None = None
    for i, cue in enumerate(cues):
        if prev_end is not None and cue.start < prev_end + min_gap:
            cue.start = prev_end + min_gap
        if cue.end < cue.start:
            cue.end = cue.start

        want_end = max(cue.end, cue.start + min_duration)
        if i + 1 < len(cues):
            # Chỗ trống tới cue kế tiếp; nếu cue kế tiếp bắt đầu quá sớm thì giữ
            # nguyên đuôi hiện tại và để vòng lặp sau đẩy nó ra.
            room = max(cue.end, cues[i + 1].start - min_gap)
            want_end = min(want_end, room)
        cue.end = max(want_end, cue.start + MIN_CUE_TICK)

        cue.start = round(cue.start, 3)
        cue.end = round(cue.end, 3)
        if cue.end <= cue.start:
            cue.end = round(cue.start + MIN_CUE_TICK, 3)
        prev_end = cue.end

    for i, cue in enumerate(cues, start=1):
        cue.index = i
    return cues


# --------------------------------------------------------------------------- #
# Thống kê
# --------------------------------------------------------------------------- #

def percentile(values: Sequence[float], p: float) -> float:
    """Phân vị theo thứ hạng gần nhất.

    Dùng cách xếp hạng đơn giản (không nội suy) để con số in ra khớp với cách các
    số mục tiêu trong build-spec được đo, và để ``p95`` của một danh sách số nguyên
    vẫn là một số nguyên.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * p))
    return ordered[min(max(idx, 0), len(ordered) - 1)]


def _summary(values: Sequence[float]) -> dict[str, float]:
    if not values:
        return {"min": 0.0, "median": 0.0, "p95": 0.0, "max": 0.0, "mean": 0.0}
    return {
        "min": round(min(values), 3),
        "median": round(statistics.median(values), 3),
        "p95": round(percentile(values, 0.95), 3),
        "max": round(max(values), 3),
        "mean": round(statistics.fmean(values), 3),
    }


def _cue_view(item: Any) -> tuple[float, float, int, int, str]:
    """Đọc một cue ở bất kỳ dạng nào: :class:`CueDraft`, :class:`~srtgen.core.token.Cue`,
    hay dict đã ghi ra JSON.

    Nhờ vậy cùng một hàm :func:`cue_stats` chạy được ở S4 (cue thô) lẫn ở S8 (cue đã
    có token), và bản báo cáo so được hai bên với nhau bằng đúng một thước đo.
    """
    start = _seg_float(item, ("start",), 0.0)
    end = _seg_float(item, ("end",), start)

    tokens = _seg_get(item, "tokens", None)
    text = _seg_get(item, "text", None)
    if not isinstance(text, str) or not text:
        zh_text = getattr(item, "zh_text", None)
        text = zh_text() if callable(zh_text) else ""

    if isinstance(tokens, (list, tuple)) and tokens:
        clusters = sum(1 for t in tokens if _token_kind(t) == "word")
        source = "tokens"
    else:
        words = _seg_get(item, "words", None)
        if isinstance(words, (list, tuple)) and words:
            clusters = len(words)
            source = "asr_words"
        else:
            clusters = len([p for p in text.split() if p])
            source = "spaces"

    han = _seg_get(item, "han", None)
    if not isinstance(han, int):
        han = sum(1 for ch in text if is_han(ch))
    return start, end, han, clusters, source


def _token_kind(token: Any) -> str:
    if isinstance(token, Mapping):
        raw = token.get("kind", "word")
    else:
        raw = getattr(token, "kind", "word")
    text = str(raw).strip().lower()
    return "word" if text in {"word", ""} else text


def cue_stats(cues: Sequence[Any]) -> dict[str, Any]:
    """Phân bố thời lượng / Hán tự / số cụm của một danh sách cue.

    Ba con số này là thước đo duy nhất cho biết chặng S4 có bám bản chuẩn không, nên
    chúng được tính ở đây một lần và ghi thẳng vào ``S4_cues.json`` — người hiệu
    chỉnh tham số sau này chỉ cần mở file ra so, không phải viết lại script đo.

    "Số cụm" ở S4 đếm theo số TỪ của ASR (chưa có cụm thật), còn từ S5 trở đi nó đếm
    token ``word``. Trường ``clusters_source`` nói rõ đang đếm kiểu nào để không ai
    so nhầm hai con số khác bản chất với nhau.
    """
    if not cues:
        return {
            "count": 0,
            "duration": _summary([]),
            "han": _summary([]),
            "clusters": _summary([]),
            "gap": _summary([]),
            "clusters_source": "none",
            "total_duration": 0.0,
            "total_han": 0,
            "overlaps": 0,
            "non_positive": 0,
        }

    views = [_cue_view(c) for c in cues]
    durations = [round(max(v[1] - v[0], 0.0), 3) for v in views]
    hans = [float(v[2]) for v in views]
    clusters = [float(v[3]) for v in views]
    gaps = [
        round(views[i + 1][0] - views[i][1], 3) for i in range(len(views) - 1)
    ]
    sources = {v[4] for v in views}
    return {
        "count": len(views),
        "duration": _summary(durations),
        "han": _summary(hans),
        "clusters": _summary(clusters),
        "gap": _summary(gaps),
        "clusters_source": sources.pop() if len(sources) == 1 else "mixed",
        "total_duration": round(sum(durations), 3),
        "total_han": int(sum(hans)),
        "zero_gap": sum(1 for g in gaps if abs(g) < 1e-9),
        "overlaps": sum(1 for g in gaps if g < -1e-9),
        "non_positive": sum(1 for v in views if v[1] <= v[0]),
        "under_0_8s": sum(1 for d in durations if d < 0.8),
        "over_6s": sum(1 for d in durations if d > 6.0),
        "over_18_han": sum(1 for h in hans if h > 18),
    }


def compare_to_target(stats: Mapping[str, Any]) -> dict[str, Any]:
    """Đặt phân bố vừa đo cạnh phân bố bản chuẩn, kèm chênh lệch đã tính sẵn.

    Bản báo cáo là thứ người dùng đọc, và họ không phải dân kỹ thuật; đưa sẵn hiệu
    số ở đây để phần hiển thị chỉ việc in ra, không phải tự tính rồi tính sai.
    """
    out: dict[str, Any] = {}
    for field_name in ("duration", "han", "clusters"):
        measured = stats.get(field_name) or {}
        target = TARGET_STATS.get(field_name) or {}
        entry: dict[str, Any] = {}
        for key in ("median", "p95"):
            got = measured.get(key)
            want = target.get(key)
            if isinstance(got, (int, float)) and isinstance(want, (int, float)):
                entry[key] = {
                    "measured": round(float(got), 3),
                    "target": want,
                    "delta": round(float(got) - float(want), 3),
                }
        if entry:
            out[field_name] = entry
    count = stats.get("count")
    if isinstance(count, int):
        out["count"] = {
            "measured": count,
            "target": TARGET_STATS["count"],
            "note": (
                "Số dòng phụ thuộc độ dài video, chỉ so được khi chạy đúng video "
                "của bản mẫu."
            ),
        }
    return out


# --------------------------------------------------------------------------- #
# Điểm vào của chặng
# --------------------------------------------------------------------------- #

def _report(
    on_progress: Callable[[str, float], None] | None, message: str, fraction: float
) -> None:
    """Báo tiến trình mà không bao giờ làm chết chặng vì callback của UI lỗi."""
    if on_progress is None:
        return
    try:
        on_progress(message, max(0.0, min(1.0, float(fraction))))
    except Exception:  # pragma: no cover - callback do UI cung cấp, không tin được
        pass


def _check_cancel(ctx: "Context") -> None:
    if ctx.is_cancelled():
        raise StageCancelled("Đã dừng theo yêu cầu của bạn.")


def _build_warnings(stats: Mapping[str, Any], params: Mapping[str, Any]) -> list[str]:
    warnings: list[str] = []
    if stats["overlaps"]:
        warnings.append(
            f"Có {stats['overlaps']} chỗ hai dòng chồng thời gian lên nhau. "
            "Đây là lỗi của tool, hãy báo lại kèm file kết quả."
        )
    if stats["non_positive"]:
        warnings.append(
            f"Có {stats['non_positive']} dòng có thời điểm kết thúc không lớn hơn "
            "thời điểm bắt đầu."
        )
    over_time = stats.get("over_6s") or 0
    if over_time:
        limit = params.get("max_duration", DEFAULT_CUE["max_duration"])
        warnings.append(
            f"Có {over_time} dòng dài hơn 6 giây (giới hạn đang đặt là {limit} giây). "
            "Bản mẫu chỉ có 8 dòng như vậy trên 1351 dòng; nếu nhiều hơn hẳn thì "
            "nhiều khả năng bản gỡ băng thiếu dấu câu."
        )
    return warnings


def run(
    ctx: "Context", on_progress: Callable[[str, float], None] | None = None
) -> dict[str, Any]:
    """Chia cue và ghi ``work/<video_id>/S4_cues.json``.

    Trả về chính nội dung đã ghi để S5 dùng ngay mà không phải đọc lại đĩa.
    """
    _report(on_progress, "Đang chia lời thoại thành từng dòng phụ đề…", 0.0)

    if ctx.has_stage(STAGE_NO, STAGE_NAME):
        cached = ctx.load_stage(STAGE_NO, STAGE_NAME)
        if isinstance(cached, dict) and cached.get("cues"):
            _report(on_progress, "Dùng lại kết quả chia dòng của lần chạy trước.", 1.0)
            return cached

    previous = ctx.load_stage(*PREV_STAGE)
    if not isinstance(previous, dict) or not previous.get("segments"):
        raise RuntimeError(
            "Chưa có bản gỡ băng đã dọn để chia dòng. Hãy chạy lại từ bước dọn kết "
            "quả gỡ băng."
        )

    words = iter_words(previous["segments"])
    if not words:
        raise RuntimeError(
            "Bản gỡ băng không có chữ nào để chia dòng. Hãy nghe thử file âm thanh "
            "rồi chạy lại từ bước nghe và gỡ băng."
        )
    _check_cancel(ctx)
    _report(on_progress, f"Đã gom {len(words)} chữ có mốc thời gian.", 0.3)

    params = cue_params(ctx.cfg)
    cues = enforce_timing(split_words(words, ctx.cfg), ctx.cfg)
    if not cues:
        raise RuntimeError(
            "Không tạo được dòng phụ đề nào từ bản gỡ băng. Hãy chạy lại từ bước dọn "
            "kết quả gỡ băng."
        )
    _check_cancel(ctx)
    _report(on_progress, f"Đã chia thành {len(cues)} dòng.", 0.8)

    stats = cue_stats(cues)
    meta = dict(previous.get("meta") or {})
    meta.update(ctx.meta)
    meta.setdefault("video_id", ctx.video_id)
    # S5 đọc khoá này để biết văn bản đã được tách cụm hay chưa. Ở đây thì CHƯA:
    # khoảng trắng trong `text` là của Whisper, không phải ranh giới cụm.
    meta["segmented"] = False

    result: dict[str, Any] = {
        "stage": "S4",
        "name": STAGE_NAME,
        "meta": meta,
        "cues": [cue.to_dict() for cue in cues],
        "stats": stats,
        "target": TARGET_STATS,
        "comparison": compare_to_target(stats),
        "params": params,
        "warnings": _build_warnings(stats, params),
        "notes": [SPEAKER_MARKER_NOTE],
        "speaker_marker": False,
    }
    ctx.save_stage(STAGE_NO, STAGE_NAME, result)
    _report(
        on_progress,
        f"Chia xong {len(cues)} dòng, trung bình "
        f"{stats['han']['median']:.0f} chữ Hán mỗi dòng.",
        1.0,
    )
    return result
