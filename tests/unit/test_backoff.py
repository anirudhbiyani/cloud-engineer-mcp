"""Tests for the restart-backoff helper."""

from __future__ import annotations

import random

from cloud_engineer_mcp.backends.process import _restart_backoff_seconds


class TestRestartBackoff:
    def test_zero_base_disables_backoff(self) -> None:
        for attempt in range(1, 10):
            assert _restart_backoff_seconds(attempt, base=0.0, cap=60.0) == 0.0

    def test_exponential_growth(self) -> None:
        # With base=1.0 and 0 jitter, attempt n produces base * 2^(n-1).
        random.seed(0)
        # The jitter band is ±25%, so we test in a bounded range.
        for attempt in range(1, 6):
            d = _restart_backoff_seconds(attempt, base=1.0, cap=60.0)
            expected = 2 ** (attempt - 1)
            lo, hi = expected * 0.75, expected * 1.25
            assert lo <= d <= hi, f"attempt={attempt}: {d} not in [{lo}, {hi}]"

    def test_capped_at_max(self) -> None:
        # 2^10 = 1024 which is much larger than cap=10.
        # With ±25% jitter, the result should be in [7.5, 12.5].
        random.seed(0)
        for _ in range(20):
            d = _restart_backoff_seconds(attempt=10, base=1.0, cap=10.0)
            assert 7.5 <= d <= 12.5

    def test_never_negative(self) -> None:
        random.seed(0)
        for _ in range(100):
            d = _restart_backoff_seconds(attempt=1, base=0.1, cap=60.0)
            assert d >= 0.0

    def test_jitter_produces_variation(self) -> None:
        # Across many attempts at the same n, values should not all be equal.
        random.seed(42)
        values = {_restart_backoff_seconds(3, base=1.0, cap=60.0) for _ in range(20)}
        assert len(values) > 1
