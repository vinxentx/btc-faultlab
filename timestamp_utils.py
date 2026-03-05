#!/usr/bin/env python3
"""Utilities for parsing and writing experiment timestamps."""

from __future__ import annotations

import time
from datetime import datetime, timezone


def _normalize_unix_epoch(value: float) -> float:
    """Normalize unix epoch values in s/ms/us/ns to seconds."""
    absolute_value = abs(value)
    if absolute_value >= 1e18:  # nanoseconds
        return value / 1e9
    if absolute_value >= 1e15:  # microseconds
        return value / 1e6
    if absolute_value >= 1e12:  # milliseconds
        return value / 1e3
    return value


def parse_unix_or_iso8601(timestamp_text: str) -> datetime:
    """Parse unix epoch (s/ms/us/ns) or ISO8601 into UTC datetime."""
    value = timestamp_text.strip()
    if not value:
        raise ValueError("Empty timestamp")

    try:
        unix_value = float(value)
    except ValueError:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    normalized = _normalize_unix_epoch(unix_value)
    return datetime.fromtimestamp(normalized, tz=timezone.utc)


def now_unix_epoch(precision: int = 6) -> str:
    """Return current unix epoch timestamp string with fixed precision."""
    return f"{time.time():.{precision}f}"
