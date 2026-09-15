"""Choosing a provider from the config - the single place that decision is made.

Rationale (build-spec section 7): S7 must never contain
``if cfg["ai"]["provider"] == "gemini"``.  It asks for a provider and gets one;
whether that provider talks to a network is not its business.  Concentrating
the decision here also means there is exactly one function to audit for the
rule that matters most: **the tool must always end up with a working provider,
never with an exception**, because an unset API key is a normal Tuesday for a
non-technical user, not an error condition.

So :func:`get_provider` never raises for a configuration problem.  It falls
back to :class:`~srtgen.providers.null.NullProvider` carrying a Vietnamese
``reason``, which S7 records in ``S7_ai.json`` and the report shows as
"chưa bật/chưa có mã API" instead of a traceback.

:func:`get_translator` (build-spec-v2 section 3) follows exactly the same rule
for the S8 translation stage, and shares the same API-key box: a key means
Gemini, no key means Google's free endpoint, and an explicit "off" means the
Vietnamese line keeps the original text.  It never raises either - a user who
has no key, no ``deep-translator`` and no network still gets a ``_vi.srt`` with
the right number of blocks, which is the promise the format contract makes.
"""

from __future__ import annotations

import os
import re
from typing import Any, Mapping

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
    ERR_UNKNOWN,
    ERROR_MESSAGES,
    JsonSchema,
    Provider,
    ProviderError,
    mask_key,
)
from srtgen.providers.null import NullProvider
from srtgen.providers.translate import (
    DEFAULT_TARGET,
    FREE_QUALITY_WARNING,
    GeminiTranslator,
    GoogleFreeTranslator,
    NullTranslator,
    Translator,
    available_translators,
)

__all__ = [
    "Provider",
    "ProviderError",
    "JsonSchema",
    "NullProvider",
    "get_provider",
    "resolve_api_keys",
    "ai_config",
    "available_providers",
    "mask_key",
    "Translator",
    "GeminiTranslator",
    "GoogleFreeTranslator",
    "NullTranslator",
    "get_translator",
    "translate_config",
    "available_translators",
    "DEFAULT_TARGET",
    "FREE_QUALITY_WARNING",
    "ERROR_MESSAGES",
    "ERR_AUTH",
    "ERR_BAD_RESPONSE",
    "ERR_BLOCKED",
    "ERR_CONFIG",
    "ERR_MISSING_DEP",
    "ERR_NETWORK",
    "ERR_QUOTA",
    "ERR_RATE_LIMIT",
    "ERR_TIMEOUT",
    "ERR_UNKNOWN",
]

#: Provider ids accepted in ``cfg["ai"]["provider"]``.  ``null`` is the default
#: and is spelled as a *string* in ``default.yaml`` ("null" is the class name,
#: not YAML's null value) - a trap worth naming here because reading it as
#: ``None`` would silently disable the explicit-off case.
_NULL_NAMES = {"", "null", "none", "off", "no", "false", "tắt"}

#: Separators a user might type between several keys in one settings box.
_KEY_SPLIT = re.compile(r"[\s,;]+")


def available_providers() -> list[str]:
    """Ids the UI can offer in a dropdown."""
    return ["null", "gemini"]


def ai_config(cfg: Mapping[str, Any] | None) -> dict[str, Any]:
    """Accept either the whole config or just its ``ai`` section.

    Callers include the pipeline (which holds the full config), the settings
    endpoint (which holds only the AI block) and tests (which pass a literal).
    Guessing here is cheaper than making three callers agree.
    """
    if not isinstance(cfg, Mapping):
        return {}
    section = cfg.get("ai")
    if isinstance(section, Mapping):
        return dict(section)
    return dict(cfg)


def resolve_api_keys(ai: Mapping[str, Any] | None) -> list[str]:
    """Collect API keys from the config value and from the environment.

    Order is deliberate: what the user typed in Settings wins over the
    environment, because the settings box is the thing they just edited and
    expect to take effect.  Several keys may be pasted into one box separated
    by comma, semicolon or newline - that is the pool the ``KeyPool`` uses.
    """
    ai = ai or {}
    keys: list[str] = []

    raw = ai.get("api_key")
    if isinstance(raw, (list, tuple)):
        keys.extend(str(k) for k in raw)
    elif raw:
        keys.extend(_KEY_SPLIT.split(str(raw).strip()))

    env_name = str(ai.get("api_key_env") or "").strip()
    if env_name:
        env_value = os.environ.get(env_name, "")
        if env_value:
            keys.extend(_KEY_SPLIT.split(env_value.strip()))

    seen: set[str] = set()
    unique: list[str] = []
    for key in keys:
        cleaned = key.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            unique.append(cleaned)
    return unique


def get_provider(cfg: Mapping[str, Any] | None) -> Provider:
    """Return the provider the config asks for, or a NullProvider explaining why not.

    Never raises.  Every ``return NullProvider(...)`` below carries the sentence
    the user will read, so that "AI did nothing" is always accompanied by a
    reason they can act on.
    """
    ai = ai_config(cfg)

    if not _truthy(ai.get("enabled", False)):
        return NullProvider(
            reason="Tầng AI đang tắt trong Cài đặt, tool chạy hoàn toàn ngoại tuyến."
        )

    name = str(ai.get("provider") or "").strip().lower()
    if name in _NULL_NAMES:
        return NullProvider(reason="Chưa chọn nhà cung cấp AI nên tool bỏ qua bước này.")

    if name == "gemini":
        keys = resolve_api_keys(ai)
        if not keys:
            env_name = str(ai.get("api_key_env") or "GEMINI_API_KEY")
            return NullProvider(
                reason=(
                    "Chưa có mã API (API key) của Gemini nên tool bỏ qua bước AI. "
                    f"Hãy dán mã vào ô trong Cài đặt, hoặc đặt biến môi trường {env_name}."
                )
            )
        from srtgen.providers.gemini import DEFAULT_BASE_URL, GeminiProvider

        try:
            return GeminiProvider(
                keys,
                model=str(ai.get("model_other") or ""),
                timeout=_number(ai.get("timeout"), 120.0),
                retries=int(_number(ai.get("retries"), 3)),
                temperature=_number(ai.get("temperature"), 0.0),
                base_url=str(ai.get("base_url") or "").strip() or DEFAULT_BASE_URL,
                max_output_tokens=int(_number(ai.get("max_output_tokens"), 8192)),
            )
        except ProviderError as err:
            return NullProvider(reason=err.user_message)

    return NullProvider(
        reason=(
            f"Không nhận ra nhà cung cấp AI “{name}” trong Cài đặt nên tool bỏ qua bước AI. "
            "Hãy chọn lại: null (không dùng AI) hoặc gemini."
        )
    )


# --------------------------------------------------------------------------- #
# translation back-end (S8)
# --------------------------------------------------------------------------- #

#: Ids accepted in ``cfg["translate"]["provider"]``.  ``auto`` is the default and
#: means "decide from whether an API key is present" - the decision a
#: non-technical user should never have to make by hand.
_AUTO_NAMES = {"", "auto", "tu-dong", "tự động", "tudong"}

#: Aliases for the free endpoint, because three spellings of the same thing will
#: end up in config files and in the settings dropdown over time.
_GOOGLE_NAMES = {"google", "google_free", "google-free", "googlefree", "free", "mien-phi"}

#: Defaults for the ``translate`` block.  They live here rather than only in
#: ``default.yaml`` so that a config written before this stage existed - or a
#: literal ``{}`` in a test - still produces a working translator.
TRANSLATE_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "provider": "auto",
    "target": DEFAULT_TARGET,
    "model": "",
    "batch_size": 25,
    "context_window": 5,   # số cue trước/sau gửi kèm làm ngữ cảnh
    "retries": 2,          # số lần gửi lại một lô khi model trả sai chỉ số
    "cache": True,
}

#: Tên cũ/tên khác của cùng một khoá, để một file cấu hình viết tay không lặng lẽ
#: bị bỏ qua.  Rẻ hơn nhiều so với việc người dùng ngồi đoán vì sao ngữ cảnh không
#: có tác dụng.
_TRANSLATE_ALIASES: dict[str, str] = {"context_cues": "context_window"}


def translate_config(cfg: Mapping[str, Any] | None) -> dict[str, Any]:
    """The ``translate`` block, with defaults filled in.

    Accepts the whole config or just the block itself, for the same reason
    :func:`ai_config` does: the pipeline holds the full config, the settings
    endpoint holds one section, and tests pass a literal.
    """
    merged = dict(TRANSLATE_DEFAULTS)
    if isinstance(cfg, Mapping):
        section = cfg.get("translate")
        block = section if isinstance(section, Mapping) else cfg
        for raw_key, value in block.items():
            key = _TRANSLATE_ALIASES.get(str(raw_key), str(raw_key))
            if key in TRANSLATE_DEFAULTS and value is not None:
                merged[key] = value
    return merged


def get_translator(cfg: Mapping[str, Any] | None) -> Translator:
    """Return the translator the config asks for.  Never raises.

    The decision order is the one in build-spec-v2 section 3, and the fallback
    at every step is :class:`~srtgen.providers.translate.NullTranslator` with a
    Vietnamese ``reason``: S8 records that sentence in ``S8_translate.json`` and
    the report prints it, so "the Vietnamese line is still Chinese" always comes
    with an explanation the user can act on.

    Note that the API key is shared with the AI proof-reading layer on purpose -
    it is one box in Settings - but ``ai.enabled`` is deliberately *not*
    consulted: a user who pasted a key has said what they want, and forcing them
    to also tick a second switch to get a good translation is exactly the kind
    of hidden coupling this tool must not have.
    """
    settings = translate_config(cfg)

    if not _truthy(settings.get("enabled", True)):
        return NullTranslator(
            reason=(
                "Phần dịch đang tắt, nên file tiếng Việt tạm giữ nguyên văn tiếng Trung. "
                "Muốn bật lại: vào Cài đặt, ô “Dịch tiếng Việt bằng”, chọn một mục "
                "khác “Không dịch”."
            )
        )

    name = str(settings.get("provider") or "").strip().lower()
    ai = ai_config(cfg)
    keys = resolve_api_keys(ai)

    if name in _NULL_NAMES and name not in _AUTO_NAMES:
        return NullTranslator(
            reason="Bạn đã chọn không dùng dịch vụ dịch nào nên dòng tiếng Việt giữ nguyên văn."
        )

    if name in _AUTO_NAMES:
        name = "gemini" if keys else "google_free"

    if name == "gemini":
        if not keys:
            env_name = str(ai.get("api_key_env") or "GEMINI_API_KEY")
            return NullTranslator(
                reason=(
                    "Chưa có mã API (API key) của Gemini nên chưa dịch được bằng AI. "
                    f"Hãy dán mã vào ô trong Cài đặt, hoặc đặt biến môi trường {env_name}."
                )
            )
        return _gemini_translator(ai, settings, keys)

    if name in _GOOGLE_NAMES:
        return GoogleFreeTranslator(retries=int(_number(settings.get("retries"), 2)) + 2)

    return NullTranslator(
        reason=(
            f"Không nhận ra dịch vụ dịch “{name}” trong Cài đặt nên dòng tiếng Việt giữ "
            "nguyên văn. Hãy chọn lại: gemini, google_free, hoặc tắt phần dịch."
        )
    )


def _gemini_translator(
    ai: Mapping[str, Any],
    settings: Mapping[str, Any],
    keys: list[str],
) -> Translator:
    """Build the Gemini translator on top of the provider S7 already uses.

    ``temperature=0`` is pinned here and not taken from the config: two runs of
    the same film must not produce two different Vietnamese files, and a user
    who raised the temperature for the proof-reading tasks did not ask for a
    different subtitle every time they press Run.
    """
    from srtgen.providers.gemini import DEFAULT_BASE_URL, GeminiProvider

    model = str(settings.get("model") or ai.get("model_other") or ai.get("model") or "")
    try:
        provider = GeminiProvider(
            keys,
            model=model,
            timeout=_number(ai.get("timeout"), 120.0),
            retries=int(_number(ai.get("retries"), 3)),
            temperature=0.0,
            base_url=str(ai.get("base_url") or "").strip() or DEFAULT_BASE_URL,
            max_output_tokens=int(_number(ai.get("max_output_tokens"), 8192)),
        )
        return GeminiTranslator(provider, model=model)
    except ProviderError as err:
        return NullTranslator(reason=err.user_message)


def _truthy(value: Any) -> bool:
    """YAML gives ``True``; a settings form gives the string ``"true"``."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "bật"}
    return bool(value)


def _number(value: Any, default: float) -> float:
    try:
        if value is None or isinstance(value, bool):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
