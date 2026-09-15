"""The contract every AI provider in srtgen must satisfy.

Rationale (plan section S7, build-spec section 7): the AI layer is a
*proposer*, never a writer.  It hands back structured JSON, the rule engine
re-checks every proposal, and a human approves what is left.  Keeping that
promise requires the surface between srtgen and any model vendor to be tiny -
one method that takes a prompt plus a JSON schema and returns a ``dict``.
Nothing here may write a file, mutate a Document or raise a vendor-specific
exception type into the pipeline.

Two consequences are deliberate:

* ``complete_json`` returns ``dict``, not text.  Prose parsing is banned; a
  provider that cannot enforce a schema must fail loudly instead of guessing.
* every failure surfaces as :class:`ProviderError`, which carries a Vietnamese
  ``user_message``.  The end user is not a developer, so "HTTP 429" must never
  reach the screen - the pipeline prints ``err.user_message`` and keeps going
  with whatever the offline path produced.
"""

from __future__ import annotations

from typing import Any, Final, Protocol, runtime_checkable

__all__ = [
    "JsonSchema",
    "Provider",
    "ProviderError",
    "ERR_MISSING_DEP",
    "ERR_AUTH",
    "ERR_RATE_LIMIT",
    "ERR_QUOTA",
    "ERR_TIMEOUT",
    "ERR_NETWORK",
    "ERR_BAD_RESPONSE",
    "ERR_BLOCKED",
    "ERR_CONFIG",
    "ERR_UNKNOWN",
    "ERROR_MESSAGES",
    "mask_key",
]


#: A JSON-schema fragment as the caller writes it (plain lowercase JSON Schema).
#: Each provider translates it into whatever its vendor expects; callers never
#: write vendor dialect.
JsonSchema = dict[str, Any]


# --------------------------------------------------------------------------- #
# error taxonomy
# --------------------------------------------------------------------------- #
# The categories exist so the UI can pick an *action button* ("Nhập lại API
# key", "Thử lại", "Kiểm tra mạng") rather than only showing a sentence.  They
# are strings, not an Enum, so they survive a JSON round trip into S7_ai.json
# without a custom encoder.

ERR_MISSING_DEP: Final[str] = "missing_dependency"
ERR_AUTH: Final[str] = "auth"
ERR_RATE_LIMIT: Final[str] = "rate_limit"
ERR_QUOTA: Final[str] = "quota"
ERR_TIMEOUT: Final[str] = "timeout"
ERR_NETWORK: Final[str] = "network"
ERR_BAD_RESPONSE: Final[str] = "bad_response"
ERR_BLOCKED: Final[str] = "blocked"
ERR_CONFIG: Final[str] = "config"
ERR_UNKNOWN: Final[str] = "unknown"

#: Default Vietnamese wording per category.  Written for someone who has never
#: heard of an API: say what happened, then say what to do about it.
ERROR_MESSAGES: Final[dict[str, str]] = {
    ERR_MISSING_DEP: (
        "Máy chưa cài thư viện cần thiết để gọi AI. "
        "Bước AI được bỏ qua, các bước còn lại vẫn chạy bình thường."
    ),
    ERR_AUTH: (
        "Mã API (API key) không đúng hoặc đã bị thu hồi. "
        "Hãy mở phần Cài đặt và dán lại mã API."
    ),
    ERR_RATE_LIMIT: (
        "Máy chủ AI đang bận vì có quá nhiều yêu cầu trong thời gian ngắn. "
        "Tool đã tự chờ và thử lại nhưng vẫn chưa được, hãy thử lại sau ít phút."
    ),
    ERR_QUOTA: (
        "Tài khoản AI đã dùng hết hạn mức của hôm nay. "
        "Hãy đợi sang ngày mới hoặc dùng một mã API khác."
    ),
    ERR_TIMEOUT: (
        "Máy chủ AI trả lời quá lâu nên tool đã dừng chờ. "
        "Thường là do mạng chậm, hãy thử lại."
    ),
    ERR_NETWORK: (
        "Không kết nối được tới máy chủ AI. "
        "Hãy kiểm tra kết nối mạng rồi thử lại."
    ),
    ERR_BAD_RESPONSE: (
        "AI trả về nội dung không đúng khuôn dạng nên tool đã bỏ qua để tránh làm hỏng file."
    ),
    ERR_BLOCKED: (
        "AI từ chối xử lý đoạn nội dung này. Tool bỏ qua đoạn đó và giữ nguyên kết quả cũ."
    ),
    ERR_CONFIG: (
        "Phần cài đặt AI chưa đúng nên tool không gọi được. Hãy kiểm tra lại trong Cài đặt."
    ),
    ERR_UNKNOWN: (
        "Có lỗi khi gọi AI. Tool bỏ qua bước AI và vẫn xuất file như bình thường."
    ),
}


class ProviderError(RuntimeError):
    """Every provider failure, wrapped so the pipeline sees exactly one type.

    ``str(err)`` stays technical (English, with status codes) because it goes
    into the log and into ``S7_ai.json`` where a developer reads it.
    ``err.user_message`` is the Vietnamese sentence the UI shows.  Splitting
    the two is what lets the log stay precise without the screen becoming
    frightening.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: str = ERR_UNKNOWN,
        user_message: str | None = None,
        status: int | None = None,
        retryable: bool = False,
        provider: str = "",
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.retryable = retryable
        self.provider = provider
        self.user_message = user_message or ERROR_MESSAGES.get(
            kind, ERROR_MESSAGES[ERR_UNKNOWN]
        )

    def to_dict(self) -> dict[str, Any]:
        """Shape stored in ``S7_ai.json`` so a report can explain a silent skip."""
        return {
            "kind": self.kind,
            "provider": self.provider,
            "status": self.status,
            "retryable": self.retryable,
            "message": str(self),
            "user_message": self.user_message,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ProviderError({self.kind!r}, status={self.status!r}, {str(self)!r})"


# --------------------------------------------------------------------------- #
# the protocol
# --------------------------------------------------------------------------- #

@runtime_checkable
class Provider(Protocol):
    """What S7 is allowed to ask of a model vendor.

    Structural typing (``Protocol``) rather than a base class: providers are
    small, independent, and often written against a vendor SDK that already has
    its own class hierarchy.  Requiring them to inherit from us would buy
    nothing and would make a test double harder to write than the real thing.

    Implementations MUST:

    * be safe to call repeatedly with the same arguments (S7 retries on resume);
    * never print, never write files, never mutate their arguments;
    * raise :class:`ProviderError` - and nothing else - for any failure that the
      caller could plausibly recover from.
    """

    #: Short stable id used in the cache key and in the report ("null", "gemini").
    name: str

    def complete_json(self, prompt: str, schema: JsonSchema, *, model: str) -> dict:
        """Return the model's answer already parsed into a ``dict``.

        ``schema`` is enforced by the vendor where possible (Gemini's
        ``responseSchema``).  Returning ``{}`` is a legitimate answer meaning
        "no proposal"; S7 treats it as such and applies nothing.
        """
        ...


# --------------------------------------------------------------------------- #
# helpers shared by providers
# --------------------------------------------------------------------------- #

def mask_key(key: str) -> str:
    """Render an API key safe to put in a log, a report or a bug screenshot.

    Reports are the artefact users forward to whoever is helping them, so a
    full key must never be able to reach one.  Four leading and four trailing
    characters are enough to answer "is this the key I pasted?".
    """
    cleaned = (key or "").strip()
    if not cleaned:
        return "(trống)"
    if len(cleaned) <= 10:
        return cleaned[:2] + "…"
    return f"{cleaned[:4]}…{cleaned[-4:]}"
