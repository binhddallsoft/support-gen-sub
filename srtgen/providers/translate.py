"""Translation back-ends - the only place srtgen turns Chinese into Vietnamese.

Rationale (build-spec-v2 section 3):

* The pipeline must produce ``<title>_vi.srt`` with **exactly** the same number
  of blocks and the same timestamps as ``<title>.srt``.  That guarantee cannot
  survive a back-end that is free to merge, split or drop lines, so the surface
  offered here is deliberately narrow: *given these numbered items, return one
  string per number*.  A back-end that cannot honour the numbering simply
  returns fewer rows and the caller keeps the original text - it never gets the
  chance to shift a cue.
* ``GeminiTranslator`` does **not** re-implement key pooling, exponential
  back-off or ``responseSchema`` handling.  All of that already exists in
  :mod:`srtgen.providers.gemini` and is shared with the S7 proof-reading layer,
  so this class only builds the prompt and the schema and hands them to that
  provider.  One retry policy, one key pool, one place to audit.
* ``GoogleFreeTranslator`` exists because a user with no API key must still get
  a Vietnamese file.  It is measurably worse - it sees one line at a time and
  knows nothing about who is speaking - which is why :data:`FREE_QUALITY_WARNING`
  is part of the class rather than a sentence someone has to remember to show.
* ``NullTranslator`` returns the Chinese text unchanged.  It is the default in
  tests: the structural promise (same cue count, same timestamps) is exactly
  what must hold when the translation itself is a no-op, so the offline test is
  a genuine test of the online path.

Every failure surfaces as :class:`~srtgen.providers.base.ProviderError`, which
already carries a Vietnamese ``user_message``; nothing here raises a vendor
exception type into the pipeline.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
from typing import Any, Callable, Final, Mapping, Protocol, Sequence, runtime_checkable

from srtgen.providers.base import (
    ERR_BAD_RESPONSE,
    ERR_CONFIG,
    ERR_MISSING_DEP,
    JsonSchema,
    ProviderError,
)

__all__ = [
    "Translator",
    "GeminiTranslator",
    "GoogleFreeTranslator",
    "NullTranslator",
    "DEFAULT_TARGET",
    "TARGET_NAMES",
    "FREE_QUALITY_WARNING",
    "build_schema",
    "build_prompt",
    "glossary_hash",
    "google_language_code",
    "available_translators",
]

#: Ngôn ngữ đích mặc định.  Tool này sinh ra cho một người dịch phim Trung sang
#: tiếng Việt, nên "vi" là mặc định chứ không phải một tuỳ chọn phải khai báo.
DEFAULT_TARGET: Final[str] = "vi"

#: Tên tiếng Việt của ngôn ngữ đích, dùng trong prompt và trong thông báo.
TARGET_NAMES: Final[dict[str, str]] = {
    "vi": "tiếng Việt",
    "en": "tiếng Anh",
    "ja": "tiếng Nhật",
    "ko": "tiếng Hàn",
}

#: Mã ngôn ngữ của ``deep-translator`` khác mã ISO ở vài chỗ; bảng này chỉ liệt
#: kê những chỗ khác nhau đó.
_GOOGLE_CODES: Final[dict[str, str]] = {"zh": "zh-CN", "zh-cn": "zh-CN", "vi": "vi"}

#: Câu cảnh báo bắt buộc hiện lên khi dùng bản dịch miễn phí (build-spec-v2 mục 3).
#: Để ở đây chứ không ở web UI vì CLI cũng phải nói đúng câu này.
FREE_QUALITY_WARNING: Final[str] = (
    "Đang dịch bằng dịch vụ miễn phí của Google. Bản dịch này chỉ đọc từng câu rời "
    "nên không nắm được ngữ cảnh phim, xưng hô và tên nhân vật thường không nhất quán. "
    "Muốn chất lượng cao hơn, hãy dán mã API (API key) của Gemini vào phần Cài đặt."
)

#: Số lần thử lại tối đa của bản miễn phí khi bị chặn tốc độ, và mốc chờ đầu tiên.
_FREE_RETRIES: Final[int] = 4
_FREE_BACKOFF_START: Final[float] = 1.5
_FREE_BACKOFF_MAX: Final[float] = 30.0
#: Nghỉ giữa hai câu. Google cho phép 5 yêu cầu/giây; gọi dày hơn thì IP bị chặn
#: một lúc lâu (đã xảy ra: 336 câu bò 20 phút rồi vẫn hỏng). 0.3s ~ 3 câu/giây.
_FREE_PAUSE: Final[float] = 0.3
#: Số câu liên tiếp bị chặn (đã hết lần thử) thì bỏ cuộc và nói thẳng với người dùng.
_FREE_MAX_CONSECUTIVE_THROTTLED: Final[int] = 3

#: Dấu hiệu "máy chủ chặn tốc độ" trong thông báo lỗi của deep-translator.  Thư
#: viện đó ném nhiều lớp lỗi khác nhau tuỳ phiên bản nên dò theo chữ rẻ hơn và
#: bền hơn là bắt đúng lớp.
_RATE_LIMIT_MARKS: Final[tuple[str, ...]] = (
    "too many requests",
    "429",
    "rate limit",
    "quota",
    "timed out",
    "timeout",
    "connection",
    "temporarily unavailable",
    "503",
)


# --------------------------------------------------------------------------- #
# the contract
# --------------------------------------------------------------------------- #

@runtime_checkable
class Translator(Protocol):
    """What S8 is allowed to ask of a translation back-end.

    ``translate_batch`` receives ``items`` as ``[{"id": int, "zh": str}, ...]``
    and must return ``{id: vietnamese_text}``.  Two rules make the structural
    guarantee possible:

    * a returned id that was not sent is ignored by the caller;
    * a sent id that is missing from the answer means "no translation", and the
      caller keeps the original Chinese line for that cue.

    Under no circumstances may an implementation reorder, merge or split items -
    the mapping is by id, never by position.
    """

    #: Stable short id used in the cache key and in ``S8_translate.json``.
    name: str

    #: Whether this back-end needs an API key.  The UI uses it to decide whether
    #: to show "chưa có mã API" next to the provider name.
    needs_key: bool

    def translate_batch(
        self,
        items: list[dict[str, Any]],
        context: dict[str, Any],
        *,
        target: str,
    ) -> dict[int, str]:
        ...


# --------------------------------------------------------------------------- #
# prompt + schema (shared by every model-based back-end)
# --------------------------------------------------------------------------- #

_PROMPT: Final[str] = """You are a professional subtitle translator working from
Chinese into {target_name}. You are translating ONE film or episode; the lines
below are consecutive.

{film}Return a translation for EVERY id listed under ITEMS, and for no other id.
Exactly {count} objects, ids {id_list}.

STYLE - this is the part that matters:
  * Film subtitles, not prose. Spoken register, short sentences, the way a
    Vietnamese person actually talks.
  * Keep forms of address consistent with the relationship between the
    characters (anh/em, ông/tôi, con/mẹ...). Once you pick one for a pair of
    characters, keep it for the whole film.
  * Keep the tone of the original: a joke stays a joke, a snap stays a snap,
    a formal line stays formal.
  * NEVER add explanations, notes, translator's comments, or anything in
    brackets that is not in the original.
  * Keep the title of a work inside 《》 as a title; do not translate it into
    its literal meaning.
  * A line that is only music or a sound effect (♪, 【音乐】) stays as it is.
  * Use ordinary ASCII punctuation (, . ? ! ...) - this is Vietnamese text, NOT
    Chinese, so do not use ，。？！
  * If the Chinese line starts with "- " or contains " - " marking a change of
    speaker, keep that marker in the same place in the translation.
  * Never leave a translation empty. If a line carries no words, repeat it
    unchanged.
{glossary}{before}{after}ITEMS (translate these, one object per id):
{items}
"""

_GLOSSARY_BLOCK: Final[str] = """
GLOSSARY - these names have a fixed {target_name} spelling. Use it exactly,
every single time, and never invent a different one:
{rows}
"""

_BEFORE_BLOCK: Final[str] = """
CONTEXT BEFORE (already translated, read only - do NOT return these):
{rows}
"""

_AFTER_BLOCK: Final[str] = """
CONTEXT AFTER (comes next, read only - do NOT return these):
{rows}
"""


def build_schema(count: int) -> JsonSchema:
    """JSON schema pinning the answer to exactly ``count`` ``{id, vi}`` objects.

    ``minItems``/``maxItems`` are the whole point: the vendor refuses to emit a
    short array, which removes the most common way a batch translation quietly
    loses a line.  The caller still checks the ids afterwards - a schema
    constrains the shape, not the content.
    """
    return {
        "type": "object",
        "properties": {
            "lines": {
                "type": "array",
                "minItems": max(0, int(count)),
                "maxItems": max(0, int(count)),
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "vi": {"type": "string"},
                    },
                    "required": ["id", "vi"],
                    "propertyOrdering": ["id", "vi"],
                },
            }
        },
        "required": ["lines"],
    }


def build_prompt(
    items: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any],
    *,
    target: str,
) -> str:
    """Assemble the request text: style rules, glossary, neighbours, then items.

    The neighbouring cues are sent as *read-only* context and are visibly
    separated from the items to translate.  Sending them as ordinary items and
    throwing the answers away would double the cost and, worse, let the model
    renumber the batch.
    """
    target_name = TARGET_NAMES.get(str(target).lower(), str(target))
    ids = [int(it.get("id", n)) for n, it in enumerate(items)]

    glossary = _glossary_block(context.get("glossary"), target_name)
    before = _context_block(_BEFORE_BLOCK, context.get("before"))
    after = _context_block(_AFTER_BLOCK, context.get("after"))
    film = _film_block(context)

    payload = [
        {"id": int(it.get("id", n)), "zh": str(it.get("zh", ""))}
        for n, it in enumerate(items)
    ]
    return _PROMPT.format(
        target_name=target_name,
        film=film,
        count=len(payload),
        id_list=", ".join(str(i) for i in ids),
        glossary=glossary,
        before=before,
        after=after,
        items=json.dumps(payload, ensure_ascii=False, indent=1),
    )


def _film_block(context: Mapping[str, Any]) -> str:
    """One line naming the film, when we know it.

    A title is often the only clue that the dialogue is historical, or a
    cartoon, or a news broadcast - which changes the register of every line.
    """
    title = str(context.get("title") or "").strip()
    return f'The film is titled "{title}".\n\n' if title else ""


def _glossary_block(glossary: Any, target_name: str) -> str:
    """Render ``names.json`` as a fixed-spelling table.

    This is the single highest-value part of the prompt: it is what keeps one
    character's name spelled the same way from minute 1 to minute 40, which no
    per-line translator can do.
    """
    rows = _glossary_rows(glossary)
    if not rows:
        return ""
    body = "\n".join(f"  {han} = {vi}" for han, vi in rows)
    return _GLOSSARY_BLOCK.format(target_name=target_name, rows=body)


def _glossary_rows(glossary: Any) -> list[tuple[str, str]]:
    """``{Hán: bản dịch}`` sorted longest-first so 陈路周 wins over 陈."""
    if not isinstance(glossary, Mapping):
        return []
    rows: list[tuple[str, str]] = []
    for han, value in glossary.items():
        key = str(han).strip()
        text = str(value).strip()
        if key and text:
            rows.append((key, text))
    rows.sort(key=lambda kv: (-len(kv[0]), kv[0]))
    return rows[:120]


def _context_block(template: str, lines: Any) -> str:
    if not isinstance(lines, (list, tuple)) or not lines:
        return ""
    rows = "\n".join(f"  {str(line).strip()}" for line in lines if str(line).strip())
    return template.format(rows=rows) if rows else ""


def glossary_hash(glossary: Mapping[str, str] | None) -> str:
    """Short stable digest of the glossary, for the cache key.

    The glossary changes what a correct translation *is* (a name may become a
    different Vietnamese spelling), so an answer produced under a different
    glossary must not be reused.  Hashing it is cheaper than storing it.
    """
    rows = _glossary_rows(glossary)
    blob = json.dumps(rows, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def google_language_code(target: str) -> str:
    """ISO code as ``deep-translator`` spells it."""
    code = str(target or DEFAULT_TARGET).strip().lower()
    return _GOOGLE_CODES.get(code, code)


def available_translators() -> list[str]:
    """Ids the settings screen may offer in a dropdown."""
    return ["auto", "gemini", "google_free", "null"]


# --------------------------------------------------------------------------- #
# Gemini
# --------------------------------------------------------------------------- #

class GeminiTranslator:
    """Translate through the Gemini provider already configured for S7.

    Takes a ready-made provider object rather than API keys so that the key
    pool, the exponential back-off and the ``responseSchema`` plumbing are
    literally the same objects the proof-reading layer uses - there is no second
    retry policy in this codebase to keep in sync.
    """

    name = "gemini"
    needs_key = True

    def __init__(self, provider: Any, *, model: str = "") -> None:
        if provider is None or not hasattr(provider, "complete_json"):
            raise ProviderError(
                "GeminiTranslator needs a provider exposing complete_json()",
                kind=ERR_CONFIG,
                provider=self.name,
                user_message=(
                    "Chưa gọi được AI để dịch. Hãy kiểm tra lại mã API trong phần Cài đặt."
                ),
            )
        self.provider = provider
        self.model = str(model or getattr(provider, "default_model", "") or "")
        #: Số lô đã gửi đi, để báo cáo nói được "đã gọi AI bao nhiêu lần".
        self.calls = 0

    def translate_batch(
        self,
        items: list[dict[str, Any]],
        context: dict[str, Any],
        *,
        target: str = DEFAULT_TARGET,
    ) -> dict[int, str]:
        """Send one batch and return ``{id: text}`` for the ids that came back.

        Rows whose id was not requested are dropped rather than guessed at: a
        model that renumbered the batch has told us its answer is unreliable,
        and silently accepting it is how a whole film ends up one cue out of
        step.
        """
        if not items:
            return {}
        wanted = {int(it.get("id", n)) for n, it in enumerate(items)}
        prompt = build_prompt(items, context, target=target)
        schema = build_schema(len(items))

        self.calls += 1
        answer = self.provider.complete_json(prompt, schema, model=self.model)
        return _rows_to_map(answer, wanted, provider=self.name)

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "model": self.model, "calls": self.calls}


def _rows_to_map(answer: Any, wanted: set[int], *, provider: str) -> dict[int, str]:
    """Unwrap ``{"lines": [{"id", "vi"}]}`` into ``{id: text}``.

    ``items`` is accepted as an alias because ``GeminiProvider`` wraps a bare
    top-level array under that key; anything else is a malformed answer and the
    caller retries the batch.
    """
    if not isinstance(answer, Mapping):
        raise ProviderError(
            f"expected an object, got {type(answer).__name__}",
            kind=ERR_BAD_RESPONSE,
            provider=provider,
        )
    rows = answer.get("lines")
    if not isinstance(rows, list):
        rows = answer.get("items")
    if not isinstance(rows, list):
        raise ProviderError(
            "answer contained no 'lines' array",
            kind=ERR_BAD_RESPONSE,
            provider=provider,
        )

    out: dict[int, str] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        try:
            key = int(row.get("id"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if key not in wanted:
            continue
        text = clean_line(row.get("vi") or row.get("text") or "")
        if text:
            out[key] = text
    return out


# --------------------------------------------------------------------------- #
# Google, free endpoint
# --------------------------------------------------------------------------- #

class GoogleFreeTranslator:
    """``deep-translator`` against Google's free endpoint, one line at a time.

    The endpoint takes no context, so this class cannot do the thing that makes
    the paid path good (consistent names, consistent forms of address).  What it
    *can* do is never lose a line, and never hammer a service that just told us
    to slow down - hence the exponential back-off, learned from voice-pro.

    ``deep_translator`` is imported inside the call so that a machine without it
    still runs ``srtgen doctor`` and the whole offline pipeline.
    """

    name = "google_free"
    needs_key = False

    #: Câu cảnh báo UI phải hiện khi chọn nhà cung cấp này.
    warning = FREE_QUALITY_WARNING

    def __init__(
        self,
        *,
        source: str = "zh-CN",
        retries: int = _FREE_RETRIES,
        backoff_start: float = _FREE_BACKOFF_START,
        backoff_factor: float = 2.0,
        pause: float = _FREE_PAUSE,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.source = source
        self.retries = max(0, int(retries))
        self.backoff_start = float(backoff_start)
        self.backoff_factor = float(backoff_factor)
        #: Nghỉ ngắn giữa hai câu để không vượt 5 yêu cầu/giây của Google.
        self.pause = max(0.0, float(pause))
        self._sleep = sleep
        self.calls = 0
        self.last_throttled = False

    def translate_batch(
        self,
        items: list[dict[str, Any]],
        context: dict[str, Any],
        *,
        target: str = DEFAULT_TARGET,
    ) -> dict[int, str]:
        """Translate each item on its own; a failed line is simply left out.

        Leaving a line out is deliberate.  The caller keeps the original Chinese
        text for that cue, which preserves the block count - far better than
        aborting the whole run because one request out of nine hundred failed.
        """
        del context  # endpoint miễn phí không nhận ngữ cảnh — xem docstring lớp
        if not items:
            return {}
        engine = self._engine(target)

        out: dict[int, str] = {}
        throttled_in_a_row = 0
        for n, item in enumerate(items):
            try:
                key = int(item.get("id", n))
            except (TypeError, ValueError):
                continue
            source_text = str(item.get("zh", "")).strip()
            if not source_text:
                continue
            self.last_throttled = False
            text = self._translate_one(engine, source_text)
            if text:
                out[key] = text
                throttled_in_a_row = 0
            elif self.last_throttled:
                throttled_in_a_row += 1
                if throttled_in_a_row >= _FREE_MAX_CONSECUTIVE_THROTTLED:
                    # Google đang chặn hẳn máy này. Bò tiếp là mỗi câu chờ đủ vòng
                    # backoff rồi vẫn hỏng — 20 phút cho một file mà kết quả vẫn là
                    # chữ Hán. Dừng ngay, nói rõ, để người dùng chờ hoặc dùng mã API.
                    from srtgen.providers.base import ERR_RATE_LIMIT

                    raise ProviderError(
                        "google free endpoint keeps throttling this machine",
                        kind=ERR_RATE_LIMIT,
                        provider=self.name,
                        retryable=True,
                        user_message=(
                            "Google đang tạm chặn máy này vì gọi dịch quá nhiều. Hãy chờ khoảng "
                            "15 phút rồi bấm “Dịch lại toàn bộ tiếng Việt”, hoặc dán mã API của "
                            "Gemini vào Cài đặt để dịch ngay (nhanh hơn và đúng ngữ cảnh hơn)."
                        ),
                    )
            if self.pause:
                self._sleep(self.pause)
        return out

    # -- internals ---------------------------------------------------------- #

    def _engine(self, target: str) -> Any:
        try:
            from deep_translator import GoogleTranslator
        except ImportError as exc:  # pragma: no cover - phụ thuộc cách cài
            raise ProviderError(
                f"deep-translator is required for the free translator: {exc}",
                kind=ERR_MISSING_DEP,
                provider=self.name,
                user_message=(
                    "Máy chưa cài phần dịch miễn phí (deep-translator). "
                    "Hãy chạy lại bộ cài đặt, hoặc nhập mã API của Gemini để dịch bằng AI."
                ),
            ) from exc
        try:
            return GoogleTranslator(
                source=self.source, target=google_language_code(target)
            )
        except Exception as exc:
            raise ProviderError(
                f"could not start GoogleTranslator: {exc}",
                kind=ERR_CONFIG,
                provider=self.name,
                user_message=(
                    "Không dùng được dịch vụ dịch miễn phí của Google lúc này. "
                    "Vào Cài đặt, ở ô “Dịch tiếng Việt bằng”, chọn “Gemini” và dán "
                    "mã API vào ô ngay trên. Hoặc chọn “Không dịch” để chỉ lấy "
                    "file tiếng Trung."
                ),
            ) from exc

    def _translate_one(self, engine: Any, text: str) -> str:
        """One line, with exponential back-off on throttling.

        Returns ``""`` when every attempt failed; raising here would throw away
        the hundreds of lines already translated in this run.
        """
        wait = self.backoff_start
        for attempt in range(self.retries + 1):
            self.calls += 1
            try:
                answer = engine.translate(text)
            except Exception as exc:  # deep-translator ném nhiều lớp lỗi khác nhau
                if _looks_throttled(exc) and attempt >= self.retries:
                    self.last_throttled = True
                if attempt >= self.retries or not _looks_throttled(exc):
                    return ""
            else:
                return clean_line(answer)
            delay = min(wait, _FREE_BACKOFF_MAX)
            delay += random.uniform(0.0, 0.25 * delay)
            self._sleep(delay)
            wait *= self.backoff_factor
        return ""

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "calls": self.calls, "warning": self.warning}


def _looks_throttled(exc: BaseException) -> bool:
    """Is this the endpoint asking us to slow down, or a permanent failure?

    Matching on the message rather than on an exception class because
    ``deep-translator`` renames and reshuffles its exception hierarchy between
    minor versions, and a wrong ``except`` clause here would turn "wait two
    seconds" into "give up on this line".
    """
    text = f"{type(exc).__name__} {exc}".lower()
    return any(mark in text for mark in _RATE_LIMIT_MARKS)


# --------------------------------------------------------------------------- #
# Null
# --------------------------------------------------------------------------- #

class NullTranslator:
    """Hands the Chinese text straight back, offline and deterministic.

    This is not a stub: "no translation" has to be a *normal* path, because it
    is what a user without an API key and without ``deep-translator`` gets, and
    it is what the whole test suite runs on.  Returning the source text keeps
    the promise in build-spec-v2 section 2 point 8 - a cue never disappears from
    one of the two files just because nobody could translate it.
    """

    name = "null"
    needs_key = False

    def __init__(self, reason: str = "") -> None:
        self.reason = reason or (
            "Chưa bật phần dịch nên dòng tiếng Việt tạm giữ nguyên văn tiếng Trung."
        )
        #: Số lô đã nhận; test khẳng định con số này để chắc là không có mạng.
        self.calls = 0

    def translate_batch(
        self,
        items: list[dict[str, Any]],
        context: dict[str, Any],
        *,
        target: str = DEFAULT_TARGET,
    ) -> dict[int, str]:
        del context, target
        self.calls += 1
        out: dict[int, str] = {}
        for n, item in enumerate(items):
            try:
                key = int(item.get("id", n))
            except (TypeError, ValueError):
                continue
            text = clean_line(item.get("zh", ""))
            if text:
                out[key] = text
        return out

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "reason": self.reason, "calls": self.calls}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"NullTranslator(calls={self.calls}, reason={self.reason!r})"


# --------------------------------------------------------------------------- #
# shared cleaning
# --------------------------------------------------------------------------- #

#: Khoảng trắng Unicode mà model hay trả về (NBSP, ideographic space).  Chúng
#: nhìn giống dấu cách nhưng ``VI_TRAILING_SPACE`` vẫn tính là khoảng trắng thừa.
_ODD_SPACE = re.compile(r"[   -   　]")

#: Ký tự điều khiển và ký tự định hướng hai chiều — vô hình trên màn hình nhưng
#: làm lệch mọi phép so chuỗi về sau.
_INVISIBLE = re.compile(r"[​-‏  ‪-‮﻿]")


def clean_line(text: Any) -> str:
    """Normalise one translated line into something the emitter can write.

    The rules in build-spec-v2 section 2 forbid leading/trailing spaces and
    double spaces, and a model that returns ``"Chào  anh.\\n"`` is not wrong
    about the translation - it is wrong about the format.  Fixing it here means
    the emitter never has to, and ``VI_TRAILING_SPACE`` stays a real error
    rather than a routine one.
    """
    raw = str(text or "")
    raw = _INVISIBLE.sub("", raw)
    raw = _ODD_SPACE.sub(" ", raw)
    raw = raw.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return re.sub(r" {2,}", " ", raw).strip()
