"""Human-friendly duration parsing utilities."""
from __future__ import annotations

import re
from typing import Optional

from humanfriendly import InvalidTimespan, parse_timespan


_DURATION_RE = re.compile(
    r"^\s*(\d+)\s*(s|sec|secs|seconds|m|min|mins|minutes|h|hr|hrs|hour|hours|"
    r"d|day|days|w|week|weeks)\s*$",
    re.IGNORECASE,
)

_UNIT_MAP = {
    "s": 1, "sec": 1, "secs": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "w": 604800, "week": 604800, "weeks": 604800,
}


def parse_duration(value: str) -> Optional[int]:
    """Parse a duration string to seconds.

    Accepts forms like "1h", "30m", "1h30m", "2 hours", "1d12h",
    or pure humanfriendly forms ("1 hour 30 minutes").
    Returns None if parsing fails.
    """
    if not value:
        return None
    value = value.strip().lower()

    # Try simple "1h30m" combined form
    combined_re = re.findall(r"(\d+)\s*([a-z]+)", value)
    if combined_re and "".join(f"{n}{u}" for n, u in combined_re).replace(" ", "") == value.replace(" ", ""):
        total = 0
        ok = True
        for num, unit in combined_re:
            mult = _UNIT_MAP.get(unit)
            if mult is None:
                ok = False
                break
            total += int(num) * mult
        if ok and total > 0:
            return total

    # Try a single-unit match
    m = _DURATION_RE.match(value)
    if m:
        n, unit = m.group(1), m.group(2)
        mult = _UNIT_MAP.get(unit.lower())
        if mult:
            return int(n) * mult

    # Fallback to humanfriendly
    try:
        return int(parse_timespan(value))
    except (InvalidTimespan, ValueError):
        return None


def format_duration(seconds: int) -> str:
    """Format a number of seconds into a French human-readable string."""
    if seconds <= 0:
        return "0 seconde"
    units = [
        ("semaine", 604800, "semaines"),
        ("jour", 86400, "jours"),
        ("heure", 3600, "heures"),
        ("minute", 60, "minutes"),
        ("seconde", 1, "secondes"),
    ]
    parts: list[str] = []
    remaining = seconds
    for singular, size, plural in units:
        value, remaining = divmod(remaining, size)
        if value:
            parts.append(f"{value} {plural if value > 1 else singular}")
        if len(parts) >= 2:
            break
    return ", ".join(parts) if parts else "0 seconde"
