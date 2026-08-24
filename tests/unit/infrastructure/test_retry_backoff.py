"""P6: retry backoff calculation (wiki consumer exponential backoff)."""

from __future__ import annotations


def _backoff(retry_count: int) -> int:
    """Mirror of wiki_consumer backoff: 2^min(count,4)*60 capped at 900s."""
    return min(2 ** min(retry_count, 4) * 60, 900)


def test_backoff_sequence() -> None:
    assert _backoff(1) == 120
    assert _backoff(2) == 240
    assert _backoff(3) == 480
    assert _backoff(4) == 900  # 960 capped at 900
    assert _backoff(5) == 900
    assert _backoff(0) == 60
