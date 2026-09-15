"""Gemini provider - the only place in srtgen that talks to a model vendor.

Rationale (plan section S7, build-spec-v2 section 5.4):

* ``temperature = 0`` and ``responseSchema``.  The pipeline must never parse
  prose.  A model that answers in sentences is a model whose answer we throw
  away, so we make the vendor enforce the shape instead of writing a parser
  that "usually works".
* **Back off, do not hop keys.**  A 429 means *this account is going too fast*,
  not *this key is broken*.  Rotating keys on the first 429 turns one throttled
  account into several throttled accounts and gets them all flagged.  So the
  same key is retried with exponentially growing waits, and only a key that
  keeps failing after its retries are spent is put on cooldown and skipped.
* **Explicit timeouts everywhere.**  The target machine is a 2017 iMac on a home
  connection; a request with no read timeout is a job that hangs forever with a
  progress bar that never moves, which the user can only fix by force-quitting.
* The model name is never hardcoded - vendors rename models every few months
  and the config file is where that churn belongs.

``httpx`` is imported lazily inside the request path so that ``srtgen doctor``
and the web UI still start on a machine where the optional ``ai`` extra was
never installed.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Final, Iterable, Sequence

from srtgen.providers.base import (
    ERR_AUTH,
    ERR_BAD_RESPONSE,
    ERR_BLOCKED,
    ERR_CONFIG,
    ERR_MISSING_DEP,
    ERR_NETWORK,
    ERR_QUOTA,
    ERR_RATE_LIMIT,
    ERR_TIMEOUT,
    JsonSchema,
    ProviderError,
    mask_key,
)

__all__ = [
    "GeminiProvider",
    "KeyPool",
    "KeyState",
    "DEFAULT_BASE_URL",
    "to_gemini_schema",
]

DEFAULT_BASE_URL: Final[str] = "https://generativelanguage.googleapis.com/v1beta"

#: HTTP statuses worth waiting on.  429 is throttling; 5xx is the vendor having
#: a bad minute.  Everything else is our fault and retrying it just wastes the
#: user's time.
_RETRY_STATUS: Final[frozenset[int]] = frozenset({408, 429, 500, 502, 503, 504})

#: Longest single wait between two attempts.  Past this the user is better
#: served by an honest "hãy thử lại sau ít phút" than by a frozen progress bar.
_MAX_BACKOFF: Final[float] = 60.0

#: How long a key that exhausted its retries is skipped for.
_KEY_COOLDOWN: Final[float] = 120.0

#: Lỗi mạng/hết giờ chờ không phải lỗi của khoá, nên khoá chỉ nghỉ vài giây.
_NETWORK_COOLDOWN: Final[float] = 5.0
_NETWORK_KINDS: Final[frozenset[str]] = frozenset({ERR_NETWORK, ERR_TIMEOUT})

#: JSON-Schema keys Gemini's ``responseSchema`` understands.  Anything else is
#: dropped rather than sent: an unknown key makes the whole request 400, and a
#: silently weaker schema is still verified by the rule engine afterwards.
_SCHEMA_KEYS: Final[frozenset[str]] = frozenset(
    {
        "type",
        "format",
        "description",
        "nullable",
        "enum",
        "items",
        "properties",
        "required",
        "propertyOrdering",
        "minItems",
        "maxItems",
    }
)


# --------------------------------------------------------------------------- #
# key pool
# --------------------------------------------------------------------------- #

@dataclass
class KeyState:
    """Live state of one API key.

    Kept per key rather than globally because the failure that matters -
    "this account is out of quota for today" - is a property of one key while
    the others may still be fine.
    """

    key: str
    requests: int = 0
    failures: int = 0
    cooldown_until: float = 0.0
    last_error: str = ""

    def is_ready(self, now: float) -> bool:
        return now >= self.cooldown_until

    def masked(self) -> str:
        return mask_key(self.key)

    def to_dict(self) -> dict[str, Any]:
        """Never contains the key itself - this ends up in reports users share."""
        return {
            "key": self.masked(),
            "requests": self.requests,
            "failures": self.failures,
            "cooling_down": self.cooldown_until > time.monotonic(),
            "last_error": self.last_error,
        }


class KeyPool:
    """Several keys with independent state, tried in order of least use.

    The plan is explicit that one free account is expected to be enough
    (8-12 requests per 40-minute video).  The pool exists for the user who
    already has two or three keys, and its rule is the conservative one: a key
    is only set aside after its own back-off budget is spent, never on the
    first 429.
    """

    def __init__(self, keys: Iterable[str]) -> None:
        seen: set[str] = set()
        self.states: list[KeyState] = []
        for raw in keys:
            key = (raw or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            self.states.append(KeyState(key=key))

    def __len__(self) -> int:
        return len(self.states)

    def __bool__(self) -> bool:
        return bool(self.states)

    def ready(self, now: float | None = None) -> list[KeyState]:
        moment = time.monotonic() if now is None else now
        return [s for s in self.states if s.is_ready(moment)]

    def acquire(self, now: float | None = None) -> KeyState | None:
        """Least-used ready key, or ``None`` when every key is cooling down."""
        candidates = self.ready(now)
        if not candidates:
            return None
        return min(candidates, key=lambda s: (s.requests, s.failures))

    def succeed(self, state: KeyState) -> None:
        """A success clears the failure streak: throttling is transient."""
        state.failures = 0
        state.cooldown_until = 0.0
        state.last_error = ""

    def penalize(self, state: KeyState, *, seconds: float = _KEY_COOLDOWN, error: str = "") -> None:
        state.failures += 1
        state.cooldown_until = time.monotonic() + max(0.0, seconds)
        if error:
            state.last_error = error

    def wait_hint(self, now: float | None = None) -> float:
        """Seconds until the earliest key is usable again (0 when one is ready)."""
        moment = time.monotonic() if now is None else now
        if not self.states:
            return 0.0
        if self.ready(moment):
            return 0.0
        return max(0.0, min(s.cooldown_until for s in self.states) - moment)

    def to_dict(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self.states]


# --------------------------------------------------------------------------- #
# schema translation
# --------------------------------------------------------------------------- #

def to_gemini_schema(schema: JsonSchema | None) -> dict[str, Any] | None:
    """Translate plain JSON Schema into the subset Gemini accepts.

    Callers write ordinary lowercase JSON Schema; the vendor dialect lives here
    so that swapping providers later does not mean rewriting four prompts.
    Unknown keywords are dropped instead of forwarded because an unrecognised
    keyword fails the whole request with a 400, and every proposal is re-checked
    by the rule engine anyway - a slightly weaker schema costs nothing.
    """
    if not isinstance(schema, dict) or not schema:
        return None
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _SCHEMA_KEYS:
            continue
        if key == "type" and isinstance(value, str):
            out["type"] = value.upper()
        elif key == "properties" and isinstance(value, dict):
            props = {k: to_gemini_schema(v) for k, v in value.items()}
            out["properties"] = {k: v for k, v in props.items() if v}
        elif key == "items":
            child = to_gemini_schema(value)
            if child:
                out["items"] = child
        elif key == "required" and isinstance(value, (list, tuple)):
            out["required"] = [str(v) for v in value]
        elif key == "enum" and isinstance(value, (list, tuple)):
            out["enum"] = [str(v) for v in value]
        else:
            out[key] = value
    return out or None


# --------------------------------------------------------------------------- #
# provider
# --------------------------------------------------------------------------- #

@dataclass
class _Attempt:
    """One row of the request log kept for ``S7_ai.json``."""

    model: str
    key: str
    status: int | None
    ok: bool
    waited: float = 0.0
    error: str = ""


class GeminiProvider:
    """Google Gemini through the ``generativelanguage`` REST endpoint.

    Only ``complete_json`` is public.  Everything else is deliberately private:
    the point of the provider boundary is that S7 cannot accidentally depend on
    a vendor detail such as ``candidates[0].content.parts``.
    """

    name = "gemini"
    needs_key = True

    def __init__(
        self,
        api_key: str | Sequence[str],
        *,
        model: str = "",
        timeout: float = 120.0,
        retries: int = 3,
        base_url: str = DEFAULT_BASE_URL,
        temperature: float = 0.0,
        max_output_tokens: int = 8192,
        thinking_budget: int | None = None,
        connect_timeout: float = 15.0,
        backoff_start: float = 2.0,
        backoff_factor: float = 2.0,
        jitter: float = 0.25,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        keys = [api_key] if isinstance(api_key, str) else list(api_key or [])
        self.pool = KeyPool(keys)
        if not self.pool:
            raise ProviderError(
                "GeminiProvider requires at least one API key",
                kind=ERR_CONFIG,
                provider=self.name,
                user_message=(
                    "Chưa có mã API (API key) của Gemini. "
                    "Hãy mở Cài đặt và dán mã API vào, hoặc để trống để tool chạy không cần AI."
                ),
            )
        #: Fallback model, used only when a caller omits ``model``.  The real
        #: value always comes from ``cfg["ai"]["model_t1"] / ["model_other"]``.
        self.default_model = model
        self.timeout = float(timeout)
        self.connect_timeout = float(connect_timeout)
        self.retries = max(0, int(retries))
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.temperature = float(temperature)
        self.max_output_tokens = int(max_output_tokens)
        # Gemini 2.5 tiêu token vào "suy nghĩ" TRƯỚC câu trả lời, và phần đó tính vào
        # maxOutputTokens. Phép thử khoá cho phép 32 token đã bị cắt cụt vì thế.
        # None = để model tự quyết; 0 = tắt suy nghĩ (Flash), Pro tối thiểu 128.
        self.thinking_budget = None if thinking_budget is None else int(thinking_budget)
        self.backoff_start = float(backoff_start)
        self.backoff_factor = float(backoff_factor)
        self.jitter = float(jitter)
        self._sleep = sleep
        #: Request bookkeeping surfaced in the stage file.
        self.calls = 0
        self.attempts: list[_Attempt] = []

    # -- public API -------------------------------------------------------- #

    def complete_json(self, prompt: str, schema: JsonSchema, *, model: str) -> dict:
        """Ask the model once and return its parsed JSON object.

        Raises :class:`ProviderError` on anything the caller might want to
        report; S7 catches it, records ``user_message`` in the stage file, and
        carries on with whatever the offline path already produced.
        """
        target = (model or self.default_model or "").strip()
        if not target:
            raise ProviderError(
                "no model name supplied",
                kind=ERR_CONFIG,
                provider=self.name,
                user_message="Chưa chọn tên model AI trong phần Cài đặt.",
            )
        body = self._build_body(prompt, schema)
        raw = self._send_with_retries(target, body)
        return self._extract_json(raw)

    def describe(self) -> dict[str, Any]:
        """Status for the report - masked keys only, never the secret itself."""
        return {
            "name": self.name,
            "calls": self.calls,
            "keys": self.pool.to_dict(),
            "base_url": self.base_url,
        }

    # -- request building -------------------------------------------------- #

    def _build_body(self, prompt: str, schema: JsonSchema) -> dict[str, Any]:
        """Assemble the request.

        ``responseMimeType`` plus ``responseSchema`` is what turns "please
        answer in JSON" from a request into a guarantee.  ``temperature`` is
        pinned at 0 (from config) because two runs of the same subtitle file
        must not disagree with each other.
        """
        generation: dict[str, Any] = {
            "temperature": self.temperature,
            "candidateCount": 1,
            "maxOutputTokens": self.max_output_tokens,
            "responseMimeType": "application/json",
        }
        converted = to_gemini_schema(schema)
        if converted:
            generation["responseSchema"] = converted
        if self.thinking_budget is not None:
            generation["thinkingConfig"] = {"thinkingBudget": self.thinking_budget}
        return {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": generation,
        }

    # -- transport --------------------------------------------------------- #

    def _client_factory(self):
        """Import ``httpx`` here, not at module import time.

        ``srtgen doctor`` exists precisely to tell a user that an optional
        dependency is missing; it cannot do that if importing the provider
        package is what crashes.
        """
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - depends on install extras
            raise ProviderError(
                f"httpx is required for the Gemini provider: {exc}",
                kind=ERR_MISSING_DEP,
                provider=self.name,
                user_message=(
                    "Máy chưa cài phần mở rộng để gọi AI (httpx). "
                    "Chạy lại bộ cài đặt, hoặc bỏ qua - tool vẫn xuất file được mà không cần AI."
                ),
            ) from exc
        timeout = httpx.Timeout(
            self.timeout, connect=self.connect_timeout, read=self.timeout, write=self.timeout
        )
        return httpx, httpx.Client(timeout=timeout, follow_redirects=False)

    def _send_with_retries(self, model: str, body: dict[str, Any]) -> dict[str, Any]:
        """Try each ready key; inside a key, back off exponentially.

        Order matters and is the whole point of this method: waiting on the
        *same* key is the polite response to a 429, and moving to another key is
        the last resort after that key's budget is gone.
        """
        httpx, client = self._client_factory()
        last: ProviderError | None = None
        # Mỗi khoá chỉ được thử ĐÚNG MỘT LƯỢT trong một lần gửi. Không có chốt này
        # thì vòng ngoài chạy mãi khi mạng treo: một lượt của khoá A (thử lại kèm
        # chờ luỹ tiến) mất lâu hơn 120 giây nguội của khoá B, nên lúc A hỏng xong
        # thì B lại "sẵn sàng", và ngược lại. Đo được với hai khoá trở lên: tool
        # đứng im vô hạn, nút Dừng cũng không thoát vì luồng đang nằm trong httpx.
        seen: list[Any] = []
        try:
            while True:
                state = self.pool.acquire()
                if state is None or any(s is state for s in seen):
                    break
                seen.append(state)
                error = self._try_one_key(httpx, client, model, body, state)
                if isinstance(error, dict):
                    return error
                last = error
                # Nghỉ 2 phút là cách cư xử đúng với lỗi 429 (khoá đang bị giới
                # hạn), nhưng SAI với lỗi mạng: lúc đó khoá không có tội gì, chỉ
                # là đường truyền chập. Cho nghỉ dài vì một cú chập mạng nghĩa là
                # lô dịch NGAY SAU đó thấy "không còn khoá nào" và cả phim mất
                # bản dịch. Lỗi mạng chỉ nghỉ vài giây.
                kind = str(getattr(error, "kind", ""))
                seconds = _NETWORK_COOLDOWN if kind in _NETWORK_KINDS else _KEY_COOLDOWN
                self.pool.penalize(state, seconds=seconds, error=str(error))
        finally:
            client.close()

        if last is not None:
            raise last
        if self.pool.states:
            # Có khoá, nhưng khoá nào cũng đang trong thời gian nghỉ. Đây là
            # chuyện tạm thời, KHÔNG phải "cài đặt sai" — báo nhầm kind khiến
            # chặng dịch coi là lỗi chí mạng và bỏ luôn phần còn lại của phim.
            wait = self.pool.wait_hint()
            raise ProviderError(
                f"every key is cooling down for another {wait:.0f}s",
                kind=ERR_RATE_LIMIT,
                provider=self.name,
                user_message=(
                    "Các mã API đang phải tạm nghỉ sau một loạt lỗi liên tiếp. "
                    f"Thử lại sau khoảng {max(1, int(wait // 60) + 1)} phút; "
                    "những câu chưa dịch vẫn giữ nguyên văn tiếng Trung."
                ),
            )
        raise ProviderError(
            "no usable API key",
            kind=ERR_CONFIG,
            provider=self.name,
        )

    def _try_one_key(
        self,
        httpx: Any,
        client: Any,
        model: str,
        body: dict[str, Any],
        state: KeyState,
    ) -> dict[str, Any] | ProviderError:
        """Return the parsed payload, or the error that ended this key's turn."""
        url = f"{self.base_url}/models/{model}:generateContent"
        wait = self.backoff_start
        last: ProviderError = ProviderError(
            "no attempt made", kind=ERR_NETWORK, provider=self.name
        )
        for attempt in range(self.retries + 1):
            state.requests += 1
            self.calls += 1
            try:
                response = client.post(
                    url,
                    params={"key": state.key},
                    json=body,
                    headers={"Content-Type": "application/json"},
                )
            except httpx.TimeoutException as exc:
                last = ProviderError(
                    f"timeout after {self.timeout}s: {exc}",
                    kind=ERR_TIMEOUT,
                    provider=self.name,
                    retryable=True,
                )
            except httpx.HTTPError as exc:
                last = ProviderError(
                    f"network error: {exc}",
                    kind=ERR_NETWORK,
                    provider=self.name,
                    retryable=True,
                )
            else:
                if response.status_code == 200:
                    self.pool.succeed(state)
                    self.attempts.append(
                        _Attempt(model=model, key=state.masked(), status=200, ok=True)
                    )
                    return self._parse_body(response)
                last = self._status_error(response)
                self.attempts.append(
                    _Attempt(
                        model=model,
                        key=state.masked(),
                        status=response.status_code,
                        ok=False,
                        error=str(last),
                    )
                )
                if not last.retryable:
                    return last
                retry_after = _retry_after_seconds(response)
                if retry_after is not None:
                    wait = max(wait, retry_after)

            if attempt >= self.retries:
                break
            delay = min(wait, _MAX_BACKOFF)
            # Jitter keeps two SrtGen windows on the same home connection from
            # retrying in lockstep and re-triggering the same throttle.
            delay += random.uniform(0.0, self.jitter * delay)
            self._sleep(delay)
            wait *= self.backoff_factor
        return last

    def _status_error(self, response: Any) -> ProviderError:
        """Map an HTTP status onto the taxonomy in ``base.py``."""
        status = response.status_code
        detail = _error_detail(response)
        # Google trả 400 (không phải 401) cho khoá sai: "API key not valid".
        # Xếp nhầm vào "bad response" thì người dùng thấy câu "AI trả về nội
        # dung không đúng khuôn dạng" và không biết phải dán lại khoá.
        bad_key = status == 400 and (
            "api key not valid" in detail.lower() or "api_key_invalid" in detail.lower()
        )
        if status in (401, 403) or bad_key:
            return ProviderError(
                f"HTTP {status}: {detail}",
                kind=ERR_AUTH,
                provider=self.name,
                status=status,
            )
        if status == 429:
            # A quota exhaustion and a rate limit share a status code; the body
            # is the only way to tell "wait a few seconds" from "wait a day".
            kind = ERR_QUOTA if "quota" in detail.lower() else ERR_RATE_LIMIT
            return ProviderError(
                f"HTTP 429: {detail}",
                kind=kind,
                provider=self.name,
                status=status,
                retryable=True,
            )
        if status == 404:
            return ProviderError(
                f"HTTP 404: {detail}",
                kind=ERR_CONFIG,
                provider=self.name,
                status=status,
                user_message=(
                    "Không tìm thấy model AI đã chọn. Tên model có thể đã đổi, "
                    "hãy chọn model khác trong Cài đặt."
                ),
            )
        if status in _RETRY_STATUS:
            return ProviderError(
                f"HTTP {status}: {detail}",
                kind=ERR_NETWORK,
                provider=self.name,
                status=status,
                retryable=True,
            )
        return ProviderError(
            f"HTTP {status}: {detail}",
            kind=ERR_BAD_RESPONSE,
            provider=self.name,
            status=status,
        )

    def _parse_body(self, response: Any) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError(
                f"response was not JSON: {exc}",
                kind=ERR_BAD_RESPONSE,
                provider=self.name,
                status=200,
            ) from exc
        if not isinstance(payload, dict):
            raise ProviderError(
                f"expected a JSON object, got {type(payload).__name__}",
                kind=ERR_BAD_RESPONSE,
                provider=self.name,
            )
        return payload

    # -- response unwrapping ----------------------------------------------- #

    def _extract_json(self, payload: dict[str, Any]) -> dict:
        """Dig the JSON object out of Gemini's envelope.

        A truncated answer (``MAX_TOKENS``) is treated as a failure rather than
        parsed leniently: half a batch of proposals silently dropped is exactly
        the kind of quiet damage this architecture exists to prevent.
        """
        feedback = payload.get("promptFeedback") or {}
        if isinstance(feedback, dict) and feedback.get("blockReason"):
            raise ProviderError(
                f"blocked by safety filter: {feedback.get('blockReason')}",
                kind=ERR_BLOCKED,
                provider=self.name,
            )
        candidates = payload.get("candidates") or []
        if not candidates:
            raise ProviderError(
                "response contained no candidates",
                kind=ERR_BAD_RESPONSE,
                provider=self.name,
            )
        first = candidates[0] if isinstance(candidates[0], dict) else {}
        finish = str(first.get("finishReason") or "").upper()
        text = _join_parts(first)
        if not text:
            if finish in ("SAFETY", "RECITATION", "PROHIBITED_CONTENT"):
                raise ProviderError(
                    f"empty answer, finishReason={finish}",
                    kind=ERR_BLOCKED,
                    provider=self.name,
                )
            raise ProviderError(
                f"empty answer, finishReason={finish or 'unknown'}",
                kind=ERR_BAD_RESPONSE,
                provider=self.name,
            )
        if finish == "MAX_TOKENS":
            raise ProviderError(
                "answer was cut off at maxOutputTokens",
                kind=ERR_BAD_RESPONSE,
                provider=self.name,
                user_message=(
                    "Câu trả lời của AI bị cắt giữa chừng nên tool bỏ qua lô này "
                    "để không làm hỏng file. Hãy thử lại."
                ),
            )
        try:
            parsed = json.loads(text)
        except ValueError as exc:
            raise ProviderError(
                f"model did not return valid JSON: {exc}",
                kind=ERR_BAD_RESPONSE,
                provider=self.name,
            ) from exc
        if isinstance(parsed, list):
            # Tolerated on purpose: a bare array is the one shape a schema-bound
            # model still slips into, and wrapping it costs nothing.
            return {"items": parsed}
        if not isinstance(parsed, dict):
            raise ProviderError(
                f"expected a JSON object, got {type(parsed).__name__}",
                kind=ERR_BAD_RESPONSE,
                provider=self.name,
            )
        return parsed


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #

def _join_parts(candidate: dict[str, Any]) -> str:
    content = candidate.get("content") or {}
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        return ""
    chunks = [str(p.get("text", "")) for p in parts if isinstance(p, dict)]
    return "".join(chunks).strip()


def _error_detail(response: Any) -> str:
    """Best-effort human-readable reason, kept short enough for a log line."""
    try:
        payload = response.json()
    except Exception:  # pragma: no cover - body may be HTML on a proxy error
        return (getattr(response, "text", "") or "")[:200]
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("status") or "")[:300]
    return str(payload)[:200]


def _retry_after_seconds(response: Any) -> float | None:
    """Honour ``Retry-After`` when the vendor sends one - it beats our guess."""
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(str(raw).strip()))
    except ValueError:
        return None
