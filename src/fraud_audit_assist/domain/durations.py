"""Human-friendly durations (`10m`, `4h`, `1d5h`) used in rules, scenarios and personas."""

from __future__ import annotations

import re
from datetime import timedelta

_PART = re.compile(r"(\d+)([dhms])")
_UNITS = {"d": "days", "h": "hours", "m": "minutes", "s": "seconds"}


def parse_duration(text: str) -> timedelta:
    """Parses `1d5h30m`-style text. Units must be in d, h, m, s order and each may appear once."""
    cleaned = text.strip().replace(" ", "")
    parts = _PART.findall(cleaned)
    if not cleaned or "".join(n + u for n, u in parts) != cleaned:
        raise ValueError(f"Invalid duration '{text}': use forms like 10m, 4h or 1d5h")
    units = [u for _, u in parts]
    if units != sorted(set(units), key="dhms".index):
        raise ValueError(f"Invalid duration '{text}': units must be in d, h, m, s order, each once")
    return timedelta(**{_UNITS[u]: int(n) for n, u in parts})


def format_duration(value: timedelta) -> str:
    """Renders an elapsed time in the unit an analyst reads a Window in ("4 min", "26h 30 min", "10d 6h").

    One smaller unit is kept, so an elapsed time can be read directly against its Window; finer detail is dropped.
    """
    total_minutes = int(value.total_seconds() // 60)
    if value < timedelta(hours=1):
        return f"{total_minutes} min"
    if value < timedelta(days=7):
        hours, minutes = divmod(total_minutes, 60)
        return f"{hours}h" if minutes == 0 else f"{hours}h {minutes} min"
    days, rest = divmod(total_minutes, 24 * 60)
    hours = rest // 60
    return f"{days}d" if hours == 0 else f"{days}d {hours}h"
