import os
import re
from datetime import timedelta

_DURATION_RE = re.compile(r"^(\d+)([HMWDhmwd])$")
_UNIT_SECONDS = {"H": 3600, "D": 86400, "W": 7 * 86400, "M": 60}


def _bool_env(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).lower() in ("1", "true", "yes")


def _duration_env(name: str, default: str = "") -> timedelta | None:
    val = os.environ.get(name, default).strip()
    if not val:
        return None
    m = _DURATION_RE.match(val)
    if not m:
        raise ValueError(
            f"Invalid {name} value {val!r} — expected e.g. 12H, 7D, 2W, 3M"
        )
    n, unit = int(m.group(1)), m.group(2).upper()
    return timedelta(seconds=n * _UNIT_SECONDS[unit])


def hard_delete_on_expire() -> bool:
    return _bool_env("DELETE_ON_EXPIRE", False)


def hard_delete_on_single_view() -> bool:
    return _bool_env("DELETE_ON_SINGLE_VIEW", False)


def delete_after_expire() -> timedelta | None:
    """Hard-delete soft-deleted expired pastes after this duration (independent of DELETE_ON_EXPIRE)."""
    return _duration_env("DELETE_AFTER_EXPIRE", "7D")


def delete_after_single_view() -> timedelta | None:
    """Hard-delete soft-deleted single-view pastes after this duration (independent of DELETE_ON_SINGLE_VIEW)."""
    return _duration_env("DELETE_AFTER_SINGLE_VIEW", "7D")
