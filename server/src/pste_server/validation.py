import os
from datetime import datetime, timedelta, timezone

from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

MAX_PASTE_BYTES = int(os.environ.get("MAX_PASTE_BYTES", 1048576))

ALLOWED_POST_FIELDS = {"pste", "lang", "auto_detect", "expires_at", "expires_in_n", "expires_in_unit", "single_view"}

_UNIT_SECONDS = {"H": 3600, "D": 86400, "W": 7 * 86400, "M": 60}


def validate_content(content: str) -> str:
    """Raises ValueError if content is invalid; returns content."""
    try:
        encoded = content.encode("utf-8")
    except UnicodeEncodeError as e:
        raise ValueError(f"Content must be valid UTF-8: {e}")
    if len(encoded) > MAX_PASTE_BYTES:
        raise ValueError(
            f"Paste exceeds maximum size of {MAX_PASTE_BYTES} bytes "
            f"(got {len(encoded)} bytes)"
        )
    return content


def validate_lang(lang: str) -> str:
    """Raises ValueError if lang is not a known Pygments lexer name."""
    if lang.lower() == "none":
        raise ValueError("'none' is reserved; omit lang to store no default")
    try:
        get_lexer_by_name(lang)
    except ClassNotFound:
        raise ValueError(f"Unknown language/lexer: {lang!r}")
    return lang


def validate_expires_in(n_str: str, unit: str) -> datetime:
    """Parse expires_in_n + expires_in_unit (web form); raises ValueError if invalid."""
    try:
        n = int(n_str)
        if n <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError(f"expiry amount must be a positive integer, got {n_str!r}")
    unit = unit.upper()
    if unit not in _UNIT_SECONDS:
        raise ValueError(f"expiry unit must be one of H, D, W, M; got {unit!r}")
    try:
        return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=n * _UNIT_SECONDS[unit])
    except OverflowError:
        raise ValueError(f"expiry duration is too large")


def validate_expires_at(value: str) -> datetime:
    """Parse ISO8601 UTC timestamp; raises ValueError if invalid or in the past."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"Invalid expires_at format (expected ISO8601): {value!r}")
    if dt.tzinfo is None:
        raise ValueError("expires_at must include timezone (use UTC, e.g. 2026-01-01T00:00:00Z)")
    dt_utc = dt.astimezone(timezone.utc).replace(tzinfo=None)
    if dt_utc <= datetime.now(timezone.utc).replace(tzinfo=None):
        raise ValueError("expires_at must be in the future")
    return dt_utc
