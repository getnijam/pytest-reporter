"""`[nijam]`-prefixed logging. Kept terse so it never spams CI output."""

from __future__ import annotations

import sys

_PREFIX = "[nijam]"
_silent = False


def set_silent(value: bool) -> None:
    """Suppress all output (set from the `nijam_silent` option)."""
    global _silent
    _silent = value


def warn(message: str) -> None:
    if _silent:
        return
    print(f"{_PREFIX} {message}", file=sys.stderr)


def info(message: str) -> None:
    """Info is only emitted when not silent; routed to stderr like warnings."""
    if _silent:
        return
    print(f"{_PREFIX} {message}", file=sys.stderr)
