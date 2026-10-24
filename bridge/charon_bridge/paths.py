"""Locate the MetaTrader 5 "Common" file sandbox.

The EA writes with ``FILE_COMMON``, which resolves to::

    %APPDATA%\\MetaQuotes\\Terminal\\Common\\Files\\

This module finds that directory without assuming a username, and
without ever writing outside it. Resolution order:

1. ``CHARON_COMMON_DIR`` -- an explicit override, used by the tests and
   by anyone running the bridge against a non-standard terminal.
2. ``%APPDATA%`` -- the normal Windows location.
3. ``~/.wine/drive_c/users/*/AppData/Roaming`` -- Wine fallback.

Nothing here touches the network or the registry.
"""

from __future__ import annotations

import os
from pathlib import Path

# --- must match CHARON_BRIDGE_ROOT / _CMD_DIR / _DONE_DIR in the EA
ROOT_NAME = "Charon"
COMMANDS_NAME = "commands"
PROCESSED_NAME = "processed"

ENV_OVERRIDE = "CHARON_COMMON_DIR"


def _appdata_candidates() -> list[Path]:
    """Every plausible ``...\\MetaQuotes\\Terminal\\Common\\Files`` path."""
    candidates: list[Path] = []

    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files")

    home = Path.home()
    wine = home / ".wine" / "drive_c" / "users"
    if wine.is_dir():
        for user in sorted(wine.iterdir()):
            roaming = user / "AppData" / "Roaming"
            if roaming.is_dir():
                candidates.append(roaming / "MetaQuotes" / "Terminal" / "Common" / "Files")

    return candidates


def common_files_dir() -> Path:
    """The MT5 Common ``Files`` directory.

    Returns the first candidate that exists. If none exists the primary
    ``%APPDATA%`` path is returned anyway -- it is the correct location
    to *report* as missing, rather than raising and hiding the reason.
    The caller decides whether a missing directory is fatal.
    """
    override = os.environ.get(ENV_OVERRIDE)
    if override:
        return Path(override)

    candidates = _appdata_candidates()
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    if candidates:
        return candidates[0]

    # --- last resort: a run with no APPDATA at all (bare container).
    return Path.home() / "MetaQuotes" / "Terminal" / "Common" / "Files"


def bridge_root() -> Path:
    """``<Common>\\Files\\Charon`` -- the bridge's own sandbox."""
    return common_files_dir() / ROOT_NAME


def commands_dir() -> Path:
    """Inbox the EA drains once per timer tick."""
    return bridge_root() / COMMANDS_NAME


def processed_dir() -> Path:
    """Audit trail of consumed commands; the EA never reads it back."""
    return commands_dir() / PROCESSED_NAME


def state_path(symbol: str) -> Path:
    """Snapshot for one symbol.

    The symbol is part of the name because a single terminal can run
    Charon on several charts at once, and they must not clobber each
    other's snapshot.
    """
    safe = sanitize_symbol(symbol)
    return bridge_root() / f"state_{safe}.json"


def sanitize_symbol(symbol: str) -> str:
    """Make a symbol safe to embed in a file name.

    Broker suffixes legitimately contain characters like ``.`` ``#``
    ``_`` and ``-``; anything that could escape the directory (``\\``,
    ``/``, ``..``) is replaced rather than trusted.
    """
    cleaned = []
    for ch in symbol.strip():
        if ch.isalnum() or ch in "._-":
            cleaned.append(ch)
        else:
            cleaned.append("_")
    safe = "".join(cleaned).strip("._-")
    return safe or "UNKNOWN"


def ensure_dirs() -> tuple[Path, Path]:
    """Create the bridge directories if they are missing.

    The EA also creates these, so this is only needed when the dashboard
    starts first. Returns ``(root, commands)`` for logging.
    """
    root = bridge_root()
    cmds = commands_dir()
    processed_dir().mkdir(parents=True, exist_ok=True)
    return root, cmds


__all__ = [
    "ENV_OVERRIDE",
    "ROOT_NAME",
    "COMMANDS_NAME",
    "PROCESSED_NAME",
    "bridge_root",
    "commands_dir",
    "common_files_dir",
    "ensure_dirs",
    "processed_dir",
    "sanitize_symbol",
    "state_path",
]
