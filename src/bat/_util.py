"""Small shared utilities with no internal BAT dependencies."""

from __future__ import annotations

from datetime import datetime, timezone

#: The UTC timestamp format used across BAT (run metadata, provenance,
#: error artifacts), e.g. ``"2026-06-23T15:30:15Z"``.
UTC_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def format_utc(dt: datetime) -> str:
    """Render ``dt`` as a UTC ``YYYY-MM-DDTHH:MM:SSZ`` string.

    A tz-naive datetime is assumed to already be UTC; a tz-aware one is
    converted to UTC first. Single source of truth for BAT's timestamp
    formatting, previously reimplemented in storage, errors, and
    provenance.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime(UTC_TIMESTAMP_FORMAT)
