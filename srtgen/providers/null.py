"""The provider srtgen uses by default: one that answers nothing, offline.

Rationale (plan section S7, build-spec section 13): the whole pipeline must run
end to end with no network, no API key and no model download, and every
deterministic test must produce byte-identical output on any machine.  That is
only true if "no AI" is a real provider rather than a pile of ``if ai_enabled:``
branches scattered through S7.

Returning ``{}`` is a *valid* answer meaning "I propose nothing".  S7 therefore
takes exactly the same code path with and without AI - the proposal list simply
comes out empty - which is what makes the offline run a genuine test of the
online one instead of a different program.
"""

from __future__ import annotations

from typing import Any

from srtgen.providers.base import JsonSchema

__all__ = ["NullProvider"]


class NullProvider:
    """Answers ``{}`` to everything and never touches the network.

    ``reason`` explains *in Vietnamese* why the AI layer is inactive - switched
    off in settings, no API key pasted, unknown provider name.  S7 copies it
    into ``S7_ai.json`` and the report so the user sees "chưa có mã API" instead
    of silently wondering why no proper nouns were detected.
    """

    name = "null"
    needs_key = False

    def __init__(self, reason: str = "") -> None:
        self.reason = reason or "Tầng AI đang tắt, tool chạy hoàn toàn ngoại tuyến."
        #: Number of calls received.  Tests assert this stays 0 on cache hits.
        self.calls = 0
        #: (model, prompt) of every call, for tests that check batching.
        self.seen: list[tuple[str, str]] = []

    def complete_json(self, prompt: str, schema: JsonSchema, *, model: str) -> dict:
        """Record the call and propose nothing.

        The arguments are kept rather than ignored so a test can assert that S7
        batched 25 items per request without ever reaching a network.
        """
        self.calls += 1
        self.seen.append((model, prompt))
        return {}

    def describe(self) -> dict[str, Any]:
        """One-line status for the report and for ``srtgen doctor``."""
        return {"name": self.name, "reason": self.reason, "calls": self.calls}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"NullProvider(calls={self.calls}, reason={self.reason!r})"
