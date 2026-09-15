"""S3 — dọn kết quả ASR trước khi chia cue.

Vì sao chặng này tồn tại (plan.md mục S3): Whisper tiếng Trung có bốn tật cố định,
và cả bốn đều làm hỏng chặng sau chứ không chỉ làm xấu bản dịch.

1. **Phồn thể lẫn giản thể.** Cùng một video, Whisper có thể trả ``說`` ở câu này và
   ``说`` ở câu kia. Để lọt sang S5 thì ``jieba`` tách sai, ``pypinyin`` cho âm khác,
   và ``NAME_INCONSISTENT`` của validator sẽ nổ vì cùng một tên riêng có hai mặt chữ.
   Nên ép giản thể ngay tại đây, trước khi có bất kỳ ai đọc mặt chữ.
2. **Lặp ảo giác.** Ở đoạn nhạc nền hoặc đoạn không có tiếng nói, Whisper hay đọc
   lại một câu 5-10 lần liên tiếp. Nếu để nguyên, S4 sẽ chia ra hàng chục cue rác
   và người soát phải xoá tay từng cái.
3. **Segment rỗng hoặc chỉ có dấu câu.** Sinh ra cue không có chữ nào — validator
   gọi là ``EMPTY_CUE``, và trong file ``.srt`` nó là một block chỉ có dấu chấm.
4. **Dấu câu ASCII lẫn lộn.** CỐ Ý KHÔNG xử lý ở đây: S6 chuẩn hoá dấu câu trên
   token, còn ở tầng này chuỗi chưa được tokenize nên sửa bằng chuỗi là vi phạm
   nguyên tắc "mọi thao tác dấu câu làm trên token" (build-spec mục 0.3).
   Quan trọng hơn: S4 dùng chính dấu câu ASCII (``.``, ``,``, ``?``, ``!``) làm điểm
   cắt, nên đổi sớm cũng không giúp gì mà lại làm mất thông tin.

Chặng này KHÔNG sửa chữ, không đoán, không thêm dấu câu. Nó chỉ bỏ đi thứ chắc chắn
là rác và đổi mặt chữ phồn thể sang giản thể. Mọi thứ bị bỏ đều được ghi lại trong
``S3_clean.json`` để bản báo cáo nói được cho người dùng biết đã bỏ những gì.

Tỉ lệ ảo giác cao là dấu hiệu audio bị nhạc nền lấn — khi đó ``S3_clean.json`` kèm
sẵn gợi ý bật ``demucs`` ở S1, để bản báo cáo hiển thị thành một câu tiếng Việt có
hành động cụ thể chứ không phải một con số khô khan.

Đầu vào ``S2_asr.json``::

    {"segments": [{"start": 1.2, "end": 3.4, "text": "...",
                   "words": [{"start": .., "end": .., "word": "..", "probability": ..}]}],
     "meta": {...}}

Đầu ra ``S3_clean.json``: cùng hình dạng ``segments`` (đã dọn, đánh số lại từ 1),
cộng ``stats`` / ``warnings`` / ``suggestions`` / ``dropped`` cho bản báo cáo.
"""

from __future__ import annotations

import difflib
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Iterable, Sequence

if TYPE_CHECKING:  # chỉ để gợi ý kiểu; tránh phụ thuộc cứng khi doctor/web UI khởi động
    from srtgen.core.context import Context

__all__ = [
    "STAGE_NO",
    "STAGE_NAME",
    "STAGE_NUMBER",
    "STAGE",
    "PREV_STAGE",
    "CJK_PUNCT",
    "ASCII_PUNCT",
    "PUNCT_CHARS",
    "REPEAT_MIN_CHARS",
    "DEMUCS_HINT_RATIO",
    "DEMUCS_HINT_MIN_DROPPED",
    "MAX_DROPPED_LOGGED",
    "StageCancelled",
    "Word",
    "Segment",
    "parse_segments",
    "open_converter",
    "to_simplified",
    "normalise_for_compare",
    "similarity",
    "is_blank",
    "is_punct_only",
    "drop_hallucinations",
    "clean_segments",
    "run",
]

STAGE_NO = 3
STAGE_NAME = "clean"
PREV_STAGE = (2, "asr")

#: Bí danh: mỗi chặng do một người viết nên tên hằng số này đang có ba biến thể
#: (``STAGE_NO`` / ``STAGE_NUMBER`` / ``STAGE``). Khai báo đủ cả ba để người viết
#: CLI gọi kiểu nào cũng chạy, thay vì phải nhớ chặng nào dùng tên nào.
STAGE_NUMBER = STAGE_NO
STAGE = STAGE_NO

#: Dấu câu tiếng Trung và dấu câu ASCII. Whisper trộn cả hai bộ trong cùng một video,
#: nên chỗ nào cần biết "đây có phải chữ không" đều phải nhìn cả hai. Đây KHÔNG phải
#: bảng chuẩn hoá (việc đó của S6 và làm trên token) — chỉ là bộ lọc để nhận ra
#: segment không có chữ nào và để so hai câu với nhau mà bỏ qua dấu.
CJK_PUNCT = "。，、？！：；《》（）〈〉「」『』“”‘’…—－·"
ASCII_PUNCT = ",.?!:;'\"()[]{}<>-–~`@#$%^&*_+=|/\\"
PUNCT_CHARS = CJK_PUNCT + ASCII_PUNCT

#: Segment ngắn hơn ngần này (sau khi bỏ dấu câu) KHÔNG bị coi là lặp ảo giác.
#: Thoại phim có "不 不 不" hay "好 好 好" thật, còn ảo giác của Whisper luôn là một
#: câu dài lặp lại nguyên vẹn. Đặt sàn ở đây rẻ hơn nhiều so với việc người dùng
#: phát hiện tool nuốt mất thoại.
REPEAT_MIN_CHARS = 2

#: Bỏ quá ngần này phần segment vì lặp ảo giác thì gợi ý bật demucs ở S1.
DEMUCS_HINT_RATIO = 0.05
DEMUCS_HINT_MIN_DROPPED = 3

#: Số bản ghi "đã bỏ" giữ lại trong JSON. Một video hỏng nặng có thể sinh hàng nghìn
#: bản ghi; báo cáo chỉ cần vài chục ví dụ, còn tổng số đã nằm trong ``stats``.
MAX_DROPPED_LOGGED = 200


class StageCancelled(RuntimeError):
    """Người dùng bấm Dừng trên giao diện.

    Là lớp riêng (không phải ``RuntimeError`` trần) để UI phân biệt được "người dùng
    tự dừng" với "tool hỏng" — hai thứ này phải hiện hai màn hình khác nhau.
    Các chặng khác import lại từ đây thay vì tự định nghĩa bản sao.
    """


# --------------------------------------------------------------------------- #
# Mô hình dữ liệu
# --------------------------------------------------------------------------- #

@dataclass
class Word:
    """Một từ kèm mốc thời gian do Whisper trả về (``word_timestamps=True``).

    S4 chia cue hoàn toàn dựa vào danh sách này, nên nó phải sống sót qua S3 nguyên
    vẹn về mặt thời gian: chặng này chỉ được đổi mặt chữ, tuyệt đối không đụng
    ``start``/``end``.
    """

    text: str
    start: float
    end: float
    probability: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "word": self.text,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "probability": round(self.probability, 4),
        }

    @classmethod
    def from_any(
        cls,
        raw: Any,
        *,
        fallback_start: float = 0.0,
        fallback_end: float = 0.0,
    ) -> "Word | None":
        """Nhận cả dict lẫn đối tượng ``faster_whisper.transcribe.Word``.

        Chấp nhận nhiều tên khoá vì ``faster-whisper`` và ``openai-whisper`` đặt tên
        khác nhau (``word`` / ``text``), và vì S2 có thể ghi thẳng đối tượng của thư
        viện xuống JSON qua ``Context.save_stage``.
        """
        text = _first_str(raw, ("word", "text", "w"))
        if text is None:
            return None
        start = _first_float(raw, ("start", "begin", "t0"), fallback_start)
        end = _first_float(raw, ("end", "stop", "t1"), fallback_end)
        prob = _first_float(raw, ("probability", "prob", "confidence", "score"), 1.0)
        if end < start:  # dữ liệu hỏng: thà có một từ dài 0 giây còn hơn ngược thời gian
            end = start
        return cls(text=text, start=start, end=end, probability=prob)


@dataclass
class Segment:
    """Một đoạn Whisper trả về, cùng danh sách từ bên trong nó."""

    index: int
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)
    no_speech_prob: float = 0.0
    avg_logprob: float = 0.0

    def duration(self) -> float:
        return self.end - self.start

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.index,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "text": self.text,
            "words": [w.to_dict() for w in self.words],
            "no_speech_prob": round(self.no_speech_prob, 4),
            "avg_logprob": round(self.avg_logprob, 4),
        }

    @classmethod
    def from_any(cls, raw: Any, index: int) -> "Segment | None":
        text = _first_str(raw, ("text", "sentence", "content"))
        if text is None:
            return None
        start = _first_float(raw, ("start", "begin", "t0"), 0.0)
        end = _first_float(raw, ("end", "stop", "t1"), start)
        if end < start:
            end = start
        raw_words = _get(raw, "words", None)
        if raw_words is None:
            raw_words = _get(raw, "word_timestamps", None)
        words: list[Word] = []
        if isinstance(raw_words, Iterable) and not isinstance(raw_words, (str, bytes)):
            for item in raw_words:
                word = Word.from_any(item, fallback_start=start, fallback_end=end)
                if word is not None and word.text.strip():
                    words.append(word)
        return cls(
            index=int(_first_float(raw, ("id", "index"), float(index))),
            start=start,
            end=end,
            text=text,
            words=words,
            no_speech_prob=_first_float(raw, ("no_speech_prob", "no_speech"), 0.0),
            avg_logprob=_first_float(raw, ("avg_logprob", "logprob"), 0.0),
        )


def _get(raw: Any, key: str, default: Any) -> Any:
    """Đọc một trường từ dict hoặc từ thuộc tính của đối tượng."""
    if isinstance(raw, dict):
        return raw.get(key, default)
    return getattr(raw, key, default)


def _first_str(raw: Any, keys: Sequence[str]) -> str | None:
    for key in keys:
        value = _get(raw, key, None)
        if isinstance(value, str):
            return value
    return None


def _first_float(raw: Any, keys: Sequence[str], default: float) -> float:
    for key in keys:
        value = _get(raw, key, None)
        if value is None or isinstance(value, bool):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return default


def parse_segments(raw: Any) -> list[Segment]:
    """Rút danh sách segment ra khỏi bất kỳ hình dạng nào S2 ghi xuống.

    Chấp nhận ``{"segments": [...]}``, danh sách trần, hoặc ``{"result": {...}}``.
    Đọc lỏng ở ranh giới giữa hai chặng là có chủ ý: S2 còn có thể đổi cách ghi, mà
    mất cả file S2 chỉ vì đổi tên một khoá thì người dùng phải nghe lại 40 phút.
    """
    if raw is None:
        return []
    container: Any = raw
    if isinstance(raw, dict):
        container = []
        for key in ("segments", "result", "asr", "data"):
            value = raw.get(key)
            if isinstance(value, list):
                container = value
                break
            if isinstance(value, dict) and isinstance(value.get("segments"), list):
                container = value["segments"]
                break
    if not isinstance(container, list):
        return []
    out: list[Segment] = []
    for i, item in enumerate(container):
        seg = Segment.from_any(item, i)
        if seg is not None:
            out.append(seg)
    return out


# --------------------------------------------------------------------------- #
# Bước 1 — phồn thể sang giản thể
# --------------------------------------------------------------------------- #

def open_converter(enabled: bool = True) -> Any | None:
    """Mở bộ đổi ``opencc`` nếu máy có cài; trả ``None`` chứ không ném lỗi khi thiếu.

    ``opencc`` import LƯỜI ở đây vì hai lý do: nó là thư viện nặng, và nó là thứ duy
    nhất trong chặng này có thể thiếu trên máy người dùng. Thiếu nó thì bước này bị
    bỏ qua kèm một câu cảnh báo tiếng Việt — mất chút chất lượng còn hơn dừng cả
    pipeline sau khi đã nghe băng xong 40 phút.

    Hai gói cùng tên ``opencc`` tồn tại trên PyPI với cách đặt tên bộ luật hơi khác
    nhau, nên thử lần lượt vài cách khởi tạo thay vì chọn cứng một cách.
    """
    if not enabled:
        return None
    try:
        import opencc  # type: ignore[import-not-found]
    except Exception:
        return None
    factory = getattr(opencc, "OpenCC", None)
    if factory is None:
        return None
    for name in ("t2s", "t2s.json"):
        try:
            converter = factory(name)
        except Exception:
            continue
        if callable(getattr(converter, "convert", None)):
            return converter
    return None


def to_simplified(text: str, converter: Any | None) -> str:
    """Đổi một chuỗi sang giản thể; trả nguyên văn khi không có bộ đổi."""
    if converter is None or not text:
        return text
    try:
        return converter.convert(text)
    except Exception:  # pragma: no cover - lỗi nội bộ của opencc
        return text


def _convert_segment(seg: Segment, converter: Any | None) -> bool:
    """Đổi cả ``text`` lẫn từng ``word``, trả True nếu có gì đó thay đổi.

    Từng từ được đổi RIÊNG chứ không đổi cả câu rồi cắt lại theo ký tự: phép cắt lại
    sẽ phá mốc thời gian khi bản đổi làm thay đổi độ dài chuỗi, mà mốc thời gian
    chính là toàn bộ giá trị của chặng S2. Đánh đổi: vài cụm phồn thể phụ thuộc ngữ
    cảnh có thể ra kết quả khác so với đổi cả câu — chấp nhận được, vì ``t2s`` gần
    như luôn là ánh xạ từng ký tự.
    """
    if converter is None:
        return False
    changed = False
    new_text = to_simplified(seg.text, converter)
    if new_text != seg.text:
        seg.text = new_text
        changed = True
    for word in seg.words:
        new_word = to_simplified(word.text, converter)
        if new_word != word.text:
            word.text = new_word
            changed = True
    return changed


# --------------------------------------------------------------------------- #
# Bước 2 — lặp ảo giác
# --------------------------------------------------------------------------- #

def normalise_for_compare(text: str) -> str:
    """Rút chuỗi về phần "chữ" để so hai segment với nhau.

    Bỏ dấu câu và khoảng trắng vì ảo giác của Whisper lặp lại cùng một câu nhưng dấu
    câu cuối câu thì lung tung (``好。`` / ``好，`` / ``好``). So nguyên văn sẽ trượt
    đúng những ca cần bắt nhất.
    """
    normalised = unicodedata.normalize("NFC", text)
    return "".join(
        ch for ch in normalised if not ch.isspace() and ch not in PUNCT_CHARS
    ).lower()


def similarity(a: str, b: str) -> float:
    """Tỉ lệ giống nhau giữa hai chuỗi đã chuẩn hoá, trong khoảng 0..1.

    ``autojunk=False`` là bắt buộc: heuristic tự động của ``difflib`` coi ký tự xuất
    hiện nhiều là "rác" khi chuỗi dài trên 200 ký tự, mà một segment ảo giác dài
    chính là chuỗi lặp nhiều ký tự giống nhau — đúng thứ cần bắt.
    """
    if not a or not b:
        return 1.0 if a == b else 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def drop_hallucinations(
    segments: Sequence[Segment],
    *,
    threshold: float = 0.90,
    max_repeat: int = 2,
    min_chars: int = REPEAT_MIN_CHARS,
) -> tuple[list[Segment], list[dict[str, Any]]]:
    """Cắt phần đuôi của một chuỗi segment lặp lại, giữ ``max_repeat`` bản đầu.

    So với segment LIỀN KỀ (đúng chữ trong plan.md) chứ không so với bản đầu chuỗi:
    ảo giác của Whisper lặp gần như nguyên văn nên hai cách cho cùng kết quả ở ca
    thường, còn ở ca trôi dần thì so liền kề dừng đúng lúc câu bắt đầu đổi nội dung.

    Giữ lại ``max_repeat`` bản đầu thay vì gộp về một bản, vì thoại thật cũng có câu
    nói hai lần liên tiếp; chỉ từ lần thứ ba trở đi mới gần như chắc chắn là ảo giác.
    """
    kept: list[Segment] = []
    dropped: list[dict[str, Any]] = []
    prev_norm = ""
    run = 0
    for seg in segments:
        norm = normalise_for_compare(seg.text)
        long_enough = len(norm) >= min_chars and len(prev_norm) >= min_chars
        if long_enough and similarity(prev_norm, norm) >= threshold:
            run += 1
        else:
            run = 1
        prev_norm = norm
        if run > max_repeat:
            dropped.append(
                {
                    "reason": "repeat",
                    "start": round(seg.start, 3),
                    "end": round(seg.end, 3),
                    "text": seg.text.strip(),
                    "repeat_no": run,
                }
            )
        else:
            kept.append(seg)
    return kept, dropped


# --------------------------------------------------------------------------- #
# Bước 3 — segment rỗng / chỉ có dấu câu
# --------------------------------------------------------------------------- #

def is_blank(text: str) -> bool:
    return not text.strip()


def is_punct_only(text: str) -> bool:
    """Chuỗi chỉ gồm dấu câu và khoảng trắng.

    Chữ số KHÔNG bị coi là dấu câu: "3" là một segment hợp lệ (đếm ngược, số nhà,
    số điện thoại), bỏ nó đi là làm mất thoại.
    """
    stripped = text.strip()
    if not stripped:
        return False
    return all(ch.isspace() or ch in PUNCT_CHARS for ch in stripped)


# --------------------------------------------------------------------------- #
# Ghép ba bước
# --------------------------------------------------------------------------- #

def clean_segments(
    segments: Sequence[Segment],
    cfg: dict[str, Any] | None = None,
    *,
    converter: Any | None = None,
) -> tuple[list[Segment], dict[str, Any], list[dict[str, Any]]]:
    """Chạy ba bước dọn theo đúng thứ tự của plan.md, trả ``(giữ lại, thống kê, đã bỏ)``.

    Thứ tự có ý nghĩa: đổi giản thể TRƯỚC khi so lặp, nếu không thì hai bản ảo giác
    của cùng một câu — một bản phồn thể một bản giản thể — sẽ không nhận ra nhau.

    Hàm sửa ``segments`` tại chỗ (đổi mặt chữ, đánh số lại) để không nhân đôi bộ nhớ
    với một video 40 phút; người gọi nào cần bản gốc thì tự sao chép trước.
    """
    options = _cleanup_cfg(cfg)
    stats: dict[str, Any] = {
        "segments_in": len(segments),
        "words_in": sum(len(s.words) for s in segments),
        "chars_in": sum(len(s.text.strip()) for s in segments),
        "converted_t2s": 0,
        "dropped_repeat": 0,
        "dropped_empty": 0,
        "dropped_punct_only": 0,
    }
    dropped: list[dict[str, Any]] = []

    # -- bước 1: phồn thể -> giản thể --------------------------------------- #
    if converter is not None:
        for seg in segments:
            if _convert_segment(seg, converter):
                stats["converted_t2s"] += 1

    # -- bước 2: lặp ảo giác ------------------------------------------------ #
    work = list(segments)
    if options["repeat_max"] >= 1:
        work, repeat_dropped = drop_hallucinations(
            work,
            threshold=options["repeat_similarity"],
            max_repeat=options["repeat_max"],
        )
        stats["dropped_repeat"] = len(repeat_dropped)
        dropped.extend(repeat_dropped)

    # -- bước 3: rỗng / chỉ có dấu câu -------------------------------------- #
    kept: list[Segment] = []
    for seg in work:
        if options["drop_empty"] and is_blank(seg.text):
            stats["dropped_empty"] += 1
            dropped.append(
                {
                    "reason": "empty",
                    "start": round(seg.start, 3),
                    "end": round(seg.end, 3),
                    "text": "",
                }
            )
            continue
        if options["drop_punct_only"] and is_punct_only(seg.text):
            stats["dropped_punct_only"] += 1
            dropped.append(
                {
                    "reason": "punct_only",
                    "start": round(seg.start, 3),
                    "end": round(seg.end, 3),
                    "text": seg.text.strip(),
                }
            )
            continue
        seg.text = seg.text.strip()
        kept.append(seg)

    for i, seg in enumerate(kept, start=1):
        seg.index = i

    stats["segments_out"] = len(kept)
    stats["words_out"] = sum(len(s.words) for s in kept)
    stats["chars_out"] = sum(len(s.text) for s in kept)
    stats["dropped_total"] = (
        stats["dropped_repeat"] + stats["dropped_empty"] + stats["dropped_punct_only"]
    )
    stats["hallucination_ratio"] = (
        round(stats["dropped_repeat"] / stats["segments_in"], 4)
        if stats["segments_in"]
        else 0.0
    )
    stats["with_word_timestamps"] = sum(1 for s in kept if s.words)
    stats["duration"] = round(max((s.end for s in kept), default=0.0), 3)
    return kept, stats, dropped


def _cleanup_cfg(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Đọc mục ``cleanup`` của cấu hình, chịu được cả dạng phẳng lẫn dạng lồng.

    Nhận cả ``cfg["cleanup"]["repeat_max"]`` (cấu hình đầy đủ của pipeline) lẫn
    ``cfg["repeat_max"]`` (khi ai đó gọi thẳng hàm này trong test), để một hàm
    thuần tuý vẫn thử được mà không phải dựng cả cây cấu hình.
    """
    source: dict[str, Any] = {}
    if isinstance(cfg, dict):
        nested = cfg.get("cleanup")
        source = dict(nested) if isinstance(nested, dict) else dict(cfg)
    return {
        "to_simplified": _as_bool(source.get("to_simplified"), True),
        "drop_empty": _as_bool(source.get("drop_empty"), True),
        "drop_punct_only": _as_bool(source.get("drop_punct_only"), True),
        "repeat_similarity": _as_float(source.get("repeat_similarity"), 0.90),
        "repeat_max": _as_int(source.get("repeat_max"), 2),
        "demucs_hint_ratio": _as_float(
            source.get("demucs_hint_ratio"), DEMUCS_HINT_RATIO
        ),
    }


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "on", "1"}
    return bool(value)


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Cảnh báo và gợi ý cho bản báo cáo
# --------------------------------------------------------------------------- #

def _build_warnings(
    stats: dict[str, Any], converter: Any | None, want_convert: bool
) -> list[str]:
    warnings: list[str] = []
    if want_convert and converter is None:
        warnings.append(
            "Máy chưa cài opencc nên bỏ qua bước đổi chữ phồn thể sang giản thể. "
            "Nếu bản gỡ băng có lẫn chữ phồn thể, cách tách từ và pinyin có thể "
            "không đồng nhất. Cài bằng lệnh: pip install opencc-python-reimplemented"
        )
    if stats["segments_out"] == 0:
        warnings.append(
            "Sau khi dọn thì không còn câu nào. Nhiều khả năng đoạn âm thanh không "
            "có tiếng nói, hoặc đã chọn sai ngôn ngữ khi gỡ băng."
        )
    if stats["segments_out"] and not stats["with_word_timestamps"]:
        warnings.append(
            "Bản gỡ băng không kèm mốc thời gian từng chữ. Bước chia dòng vẫn chạy "
            "được nhưng mốc thời gian trong mỗi dòng chỉ là ước lượng."
        )
    return warnings


def _build_suggestions(
    stats: dict[str, Any],
    options: dict[str, Any],
    converter: Any | None,
    want_convert: bool,
) -> list[dict[str, str]]:
    """Gợi ý có hành động cụ thể, viết sẵn tiếng Việt cho báo cáo hiển thị thẳng."""
    suggestions: list[dict[str, str]] = []
    ratio = float(stats["hallucination_ratio"])
    if (
        stats["dropped_repeat"] >= DEMUCS_HINT_MIN_DROPPED
        and ratio >= options["demucs_hint_ratio"]
    ):
        suggestions.append(
            {
                "code": "ENABLE_DEMUCS",
                "message": (
                    f"Đã bỏ {stats['dropped_repeat']} câu bị đọc lặp lại "
                    f"({ratio * 100:.1f}% tổng số câu). Đây thường là do nhạc nền át "
                    "tiếng nói. Lần chạy sau nên bật mục “Tách giọng khỏi nhạc nền” "
                    "trong Cài đặt nâng cao — chậm hơn nhưng sạch hơn nhiều."
                ),
            }
        )
    if want_convert and converter is None:
        suggestions.append(
            {
                "code": "INSTALL_OPENCC",
                "message": (
                    "Cài thêm opencc để tool tự đổi chữ phồn thể sang giản thể: "
                    "pip install opencc-python-reimplemented"
                ),
            }
        )
    if stats["segments_out"] and not stats["with_word_timestamps"]:
        suggestions.append(
            {
                "code": "NO_WORD_TIMESTAMPS",
                "message": (
                    "Bản gỡ băng thiếu mốc thời gian từng chữ. Hãy chạy lại bước nghe "
                    "băng với tuỳ chọn mốc thời gian theo chữ để dòng phụ đề bám sát "
                    "lời thoại hơn."
                ),
            }
        )
    return suggestions


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


def _carry_meta(ctx: "Context", raw: Any) -> dict[str, Any]:
    """Chuyển tiếp phần metadata cần cho việc đặt tên file và cho bản báo cáo."""
    meta = dict(ctx.meta)
    sources: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        if isinstance(raw.get("meta"), dict):
            sources.append(raw["meta"])
        sources.append(raw)
    for key in (
        "video_id",
        "title",
        "source",
        "source_url",
        "language",
        "model",
        "duration",
    ):
        if key in meta:
            continue
        for source in sources:
            if key in source and source[key] is not None:
                meta[key] = source[key]
                break
    meta.setdefault("video_id", ctx.video_id)
    return meta


def run(
    ctx: "Context", on_progress: Callable[[str, float], None] | None = None
) -> dict[str, Any]:
    """Dọn kết quả gỡ băng và ghi ``work/<video_id>/S3_clean.json``.

    Trả về chính nội dung đã ghi (dict thuần) để chặng S4 dùng ngay mà không phải
    đọc lại đĩa, đồng thời vẫn có file để chạy tiếp giữa chừng.
    """
    _report(on_progress, "Đang dọn kết quả gỡ băng…", 0.0)

    if ctx.has_stage(STAGE_NO, STAGE_NAME):
        cached = ctx.load_stage(STAGE_NO, STAGE_NAME)
        if isinstance(cached, dict) and cached.get("segments") is not None:
            _report(on_progress, "Dùng lại kết quả đã dọn của lần chạy trước.", 1.0)
            return cached

    raw = ctx.load_stage(*PREV_STAGE)
    if raw is None:
        raise RuntimeError(
            "Chưa có kết quả gỡ băng để dọn. Hãy chạy lại từ bước nghe và gỡ băng."
        )

    segments = parse_segments(raw)
    if not segments:
        raise RuntimeError(
            "Kết quả gỡ băng không có câu nào. Nhiều khả năng đoạn âm thanh không có "
            "tiếng nói, hoặc file âm thanh bị hỏng. Hãy nghe thử file âm thanh trước "
            "khi chạy lại."
        )
    _check_cancel(ctx)
    _report(on_progress, f"Đã đọc {len(segments)} câu từ bản gỡ băng.", 0.15)

    options = _cleanup_cfg(ctx.cfg)
    converter = open_converter(options["to_simplified"])
    if options["to_simplified"]:
        _report(
            on_progress,
            "Đang đổi chữ phồn thể sang giản thể…"
            if converter is not None
            else "Bỏ qua bước đổi phồn thể sang giản thể (máy chưa cài opencc).",
            0.35,
        )
    _check_cancel(ctx)

    kept, stats, dropped = clean_segments(segments, ctx.cfg, converter=converter)
    _report(
        on_progress,
        f"Đã bỏ {stats['dropped_repeat']} câu đọc lặp và "
        f"{stats['dropped_empty'] + stats['dropped_punct_only']} câu trống.",
        0.85,
    )

    result: dict[str, Any] = {
        "stage": "S3",
        "name": STAGE_NAME,
        "meta": _carry_meta(ctx, raw),
        "segments": [seg.to_dict() for seg in kept],
        "stats": stats,
        "warnings": _build_warnings(stats, converter, options["to_simplified"]),
        "suggestions": _build_suggestions(
            stats, options, converter, options["to_simplified"]
        ),
        "dropped": dropped[:MAX_DROPPED_LOGGED],
        "dropped_truncated": max(0, len(dropped) - MAX_DROPPED_LOGGED),
    }
    ctx.save_stage(STAGE_NO, STAGE_NAME, result)
    _report(on_progress, f"Dọn xong, còn {len(kept)} câu.", 1.0)
    return result
