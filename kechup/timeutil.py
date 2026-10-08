"""Time helpers: ISO 8601 UTC timestamps used throughout tasks.json."""

from __future__ import annotations

from datetime import datetime, timezone


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def parse_iso(text: str) -> datetime:
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
