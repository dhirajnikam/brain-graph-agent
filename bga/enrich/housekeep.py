from __future__ import annotations

import time


def now_ms() -> int:
    return int(time.time() * 1000)


def decay_factor(age_days: float) -> float:
    # simple exponential-ish decay
    if age_days <= 7:
        return 0.95
    if age_days <= 30:
        return 0.80
    if age_days <= 90:
        return 0.50
    return 0.30
