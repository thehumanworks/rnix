"""Pure helpers for durations, names, and image keys."""

import hashlib
import re

from .errors import RnixError

# Bump when the image layout changes, so old images are not reused.
SCHEMA = "1"

MAX_LIFETIME = 24 * 3600
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(value: int | str | None) -> int | None:
    if value is None or isinstance(value, int):
        return value
    match = re.fullmatch(r"(\d+)([smhd]?)", value.strip())
    if not match:
        raise RnixError(f"invalid duration: {value!r}")
    return int(match[1]) * _UNITS[match[2] or "s"]


def lifetime(value: int | str) -> int:
    seconds = parse_duration(value)
    if seconds > MAX_LIFETIME:
        raise RnixError(f"timeout {value!r} is over Modal's 24h limit")
    return seconds


def sanitize(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    return cleaned or "repo"


def validate_session_name(name: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,62}", name) or ".." in name or name.endswith(".lock"):
        raise RnixError(f"invalid session name: {name!r}")
    return name


def _digest(*parts: str) -> str:
    return hashlib.sha256("\n".join((SCHEMA, *parts)).encode()).hexdigest()[:16]


def base_hash(base: str, toplevel_drv: str, bootstrap: str) -> str:
    return _digest(base, toplevel_drv, bootstrap)


def project_key(shell_drv: str, base: str) -> str:
    return _digest(shell_drv, base)
