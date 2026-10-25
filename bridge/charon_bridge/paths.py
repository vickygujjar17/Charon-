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

# --- Characters kept as-is in a file or folder name; everything else becomes
# --- "_". Kept in one place because the MQL5 mirror (SafeName) must match it
# --- character for character -- see sanitize_symbol().
_KEEP_CHARS = frozenset(
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz._-"
)



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


def commands_dir(symbol: str | None = None) -> Path:
    """Inbox the EA drains once per timer tick.

    ``symbol`` selects that chart's own inbox -- ``Charon\\commands\\EURUSD``
    -- which is what stops one EA from consuming a command meant for
    another. ``None`` is the legacy flat inbox at ``Charon\\commands``:
    every pre-v1.3.0 EA reads and writes exactly there, so it has to stay
    addressable, and the bridge has to keep reading it after the upgrade.
    """
    root = bridge_root() / COMMANDS_NAME
    if symbol is None:
        return root
    return root / sanitize_symbol(symbol)


def processed_dir(symbol: str | None = None) -> Path:
    """Audit trail of consumed commands; the EA never reads it back."""
    return commands_dir(symbol) / PROCESSED_NAME


def command_subdirs() -> list[Path]:
    """Every per-symbol inbox on disk, sorted. Never includes ``processed``.

    ``processed`` lives *inside* the inbox, so a naive directory walk finds
    it and then treats the archive as a queue.
    """
    root = bridge_root() / COMMANDS_NAME
    if not root.is_dir():
        return []
    found = [
        entry for entry in sorted(root.iterdir())
        if entry.is_dir() and entry.name != PROCESSED_NAME
    ]
    return found


def inboxes(symbol: str | None = None) -> list[tuple[str, Path]]:
    """``(folder_name, path)`` for every inbox that should be read.

    ``folder_name`` is ``""`` for the legacy flat inbox, because that one is
    not scoped to a symbol: its files carry a ``symbol`` field the EA checks
    itself, so the bridge must not pretend to know which chart owns them.

    This union is not a nicety. The MQL5 side moved to per-symbol folders in
    v1.3.0; a bridge that read only the new layout would silently stop
    listing every command queued by the *previous* release, and those files
    would sit in ``Charon\\commands\\*.json`` forever with nothing draining
    them from this view.
    """
    root = bridge_root() / COMMANDS_NAME
    found: list[tuple[str, Path]] = []
    if root.is_dir():
        found.append(("", root))
    if symbol is None:
        found.extend((entry.name, entry) for entry in command_subdirs())
    else:
        one = commands_dir(symbol)
        if one.is_dir():
            found.append((one.name, one))
    return found


def iter_commands(symbol: str | None = None):
    """Yield ``(folder_name, path)`` for every queued ``*.json`` command.

    Reads the legacy inbox and the per-symbol inboxes together, so the
    dashboard sees one queue regardless of which release wrote each file.
    """
    for folder_name, folder in inboxes(symbol):
        for entry in sorted(folder.glob("*.json")):
            if entry.is_file():
                yield folder_name, entry


def processed_dirs(symbol: str | None = None) -> list[Path]:
    """Every ``processed`` folder that exists, legacy inbox included."""
    found = []
    for _folder_name, folder in inboxes(symbol):
        archive = folder / PROCESSED_NAME
        if archive.is_dir():
            found.append(archive)
    return found


def inbox_for(symbol: str) -> Path:
    """The inbox the EA currently running ``symbol`` will actually drain.

    Routing is decided by the filesystem, not by a guess: a v1.3.0 EA
    creates ``Charon\\commands\\<SYMBOL>`` in ``Init()`` and reads nothing
    else, while a v1.2.0 EA reads only the flat inbox.

    * folder present -> the EA adopted per-symbol inboxes -> write there.
    * folder absent  -> the EA is still flat -> write to the legacy inbox.

    The order matters and so does the side effect: **the bridge must never
    create this folder itself.** Doing so would make the probe always
    succeed, and commands for a still-flat EA would be routed into a folder
    nothing drains -- the exact silent failure this check exists to avoid.
    Only the EA creates it; ``ensure_dirs`` deliberately does not.
    """
    scoped = commands_dir(symbol)
    if scoped.is_dir():
        return scoped
    return commands_dir()


def mark_inbox(symbol: str) -> Path:
    """Create the per-symbol inbox. **For tests and tooling only.**

    The EA creates its own inbox in ``Init()``; calling this from the
    bridge's write path would defeat :func:`inbox_for`.
    """
    folder = commands_dir(symbol)
    (folder / PROCESSED_NAME).mkdir(parents=True, exist_ok=True)
    return folder


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

    The kept set is **ASCII only**, deliberately. ``str.isalnum()`` is
    Unicode-aware and would keep ``é``, while the MQL5 mirror
    (``CCharonBridge::SafeName``) can only keep ASCII without writing a
    Unicode table. The two processes address the same folders, so a
    disagreement does not raise -- it splits the queue across two folder
    names, one of which nothing drains. ``test_symbol_names.py`` pins them
    together.
    """
    cleaned = []
    for ch in symbol.strip():
        if ch in _KEEP_CHARS:
            cleaned.append(ch)
        else:
            cleaned.append("_")
    safe = "".join(cleaned).strip("._-")
    return safe or "UNKNOWN"


def ensure_dirs(symbol: str | None = None) -> tuple[Path, Path]:
    """Create the bridge directories if they are missing.

    The EA also creates these, so this is only needed when the dashboard
    starts first. Returns ``(root, commands)`` for logging.

    ``symbol`` also creates that chart's inbox *only if the EA already
    created it*, because the writer must not depend on the EA having ticked
    first. It never creates the per-symbol folder itself -- see
    :func:`inbox_for` for why that would break routing.
    """
    root = bridge_root()
    cmds = inbox_for(symbol) if symbol is not None else commands_dir()
    processed_dir().mkdir(parents=True, exist_ok=True)
    if symbol is not None and cmds != commands_dir():
        processed_dir(symbol).mkdir(parents=True, exist_ok=True)
    return root, cmds


__all__ = [
    "ENV_OVERRIDE",
    "ROOT_NAME",
    "COMMANDS_NAME",
    "PROCESSED_NAME",
    "bridge_root",
    "command_subdirs",
    "commands_dir",
    "common_files_dir",
    "ensure_dirs",
    "inboxes",
    "inbox_for",
    "iter_commands",
    "mark_inbox",
    "processed_dir",
    "processed_dirs",
    "sanitize_symbol",
    "state_path",
]
