"""Bộ dịch miễn phí không được bò hàng chục phút khi Google đang chặn máy.

Ca thật: 336 câu, 3/14 lô sau 20 phút, mỗi câu chờ đủ vòng backoff rồi vẫn trả về
rỗng. Luật mới: nghỉ 0,3 giây giữa hai câu để không vượt 5 yêu cầu/giây, và ba câu
liên tiếp bị chặn (đã hết lần thử) thì dừng ngay với câu tiếng Việt nói phải làm gì.
"""

from __future__ import annotations

import pytest

from srtgen.providers.base import ERR_RATE_LIMIT, ProviderError
from srtgen.providers.translate import GoogleFreeTranslator


class _Throttling:
    """Engine giả: luôn báo 'too many requests' như deep-translator thật."""

    def __init__(self) -> None:
        self.calls = 0

    def translate(self, text: str) -> str:
        self.calls += 1
        raise RuntimeError("Server Error: You made too many requests to the server. According to google...")


class _Fine:
    def translate(self, text: str) -> str:
        return "vi:" + text


def _translator(engine, **kw) -> GoogleFreeTranslator:
    slept: list[float] = []
    t = GoogleFreeTranslator(sleep=slept.append, retries=1, **kw)
    t._engine = lambda target: engine  # type: ignore[method-assign]
    t.slept = slept  # type: ignore[attr-defined]
    return t


def test_gives_up_fast_when_google_keeps_throttling() -> None:
    engine = _Throttling()
    t = _translator(engine)
    items = [{"id": i, "zh": f"句{i}"} for i in range(20)]
    with pytest.raises(ProviderError) as err:
        t.translate_batch(items, {}, target="vi")
    assert err.value.kind == ERR_RATE_LIMIT
    assert "Google đang tạm chặn" in err.value.user_message
    # 3 câu × (1 lần + 1 lần thử lại) — không phải 20 câu × 2
    assert engine.calls == 6


def test_paces_calls_between_lines() -> None:
    t = _translator(_Fine())
    out = t.translate_batch([{"id": 0, "zh": "你好"}, {"id": 1, "zh": "再见"}], {}, target="vi")
    assert out == {0: "vi:你好", 1: "vi:再见"}
    assert t.pause == pytest.approx(0.3)
    assert t.slept.count(0.3) == 2  # type: ignore[attr-defined]


def test_one_throttled_line_among_good_ones_does_not_abort() -> None:
    class _Flaky:
        def __init__(self) -> None:
            self.n = 0

        def translate(self, text: str) -> str:
            self.n += 1
            if text == "坏":
                raise RuntimeError("too many requests")
            return "vi:" + text

    t = _translator(_Flaky())
    out = t.translate_batch([{"id": 0, "zh": "好"}, {"id": 1, "zh": "坏"}, {"id": 2, "zh": "好2"}], {}, target="vi")
    assert out == {0: "vi:好", 2: "vi:好2"}, "câu hỏng lẻ thì bỏ qua, các câu khác vẫn dịch"
