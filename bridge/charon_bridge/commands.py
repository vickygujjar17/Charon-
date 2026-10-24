"""Write commands into the EA's inbox.

The only writer of files the EA acts on, so this module is deliberately
small, synchronous and paranoid:

* the name is ``<epoch-ns>-<counter>-<rand>.json`` so the EA's plain
  lexicographic sort is a true FIFO -- an ``arm`` issued before a
  ``disarm`` can never be applied in the wrong order;
* the payload lands via a temp file and an atomic rename, so the EA can
  never open a half-written command;
* only verbs in :data:`charon_bridge.schema.ALLOWED_VERBS` are accepted.

There is no code path here that can express an order. See
``docs`` in ``README.md`` for why that is a hard boundary.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from .paths import commands_dir, ensure_dirs, processed_dir
from .schema import ALLOWED_VERBS, SchemaError, build_command


class CommandError(RuntimeError):
    """A command could not be written."""


_counter = 0


def _next_name(now_ns: int) -> str:
    """A name that sorts chronologically and stays unique.

    Monotonic within the process via ``_counter``; a random suffix covers
    two bridge processes pointed at the same directory.
    """
    global _counter
    _counter += 1
    return f"{now_ns:020d}-{_counter:06d}-{uuid.uuid4().hex[:8]}.json"


def _atomic_write(path: Path, payload: str) -> None:
    """Write ``payload`` so a reader sees all of it or none of it."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise CommandError(f"could not write command: {exc}") from exc


def submit(verb: str, symbol: str, *, mode: int | None = None) -> dict[str, Any]:
    """Queue one command for the EA and return what was sent.

    ``issued`` is the local epoch second. The EA compares it against its
    own clock and refuses anything more than ``CHARON_BRIDGE_CMD_MAX_AGE``
    away in either direction, so a file left on disk and replayed later is
    a no-op rather than a surprise.
    """
    if verb not in ALLOWED_VERBS:
        raise SchemaError(f"verb {verb!r} is not permitted")

    ensure_dirs()
    now_ns = time.time_ns()
    issued = int(now_ns // 1_000_000_000)
    command_id = uuid.uuid4().hex

    doc = build_command(verb, issued, symbol=symbol, command_id=command_id, mode=mode)
    name = _next_name(now_ns)
    payload = json.dumps(doc, indent=2, sort_keys=True) + "\n"

    path = commands_dir() / name
    _atomic_write(path, payload)
    return {"id": command_id, "file": name, "verb": verb, "sent": doc}


def pending() -> list[str]:
    """Command files still waiting for the EA, oldest first."""
    folder = commands_dir()
    if not folder.is_dir():
        return []
    return sorted(entry.name for entry in folder.glob("*.json"))


# --- The EA prefixes the verdict to the original FIFO name when it retires
# --- a command (``ok_<name>.json`` / ``rejected_<name>.json``). Both the
# --- verdict and the original name have to be recovered from the one string.
_VERDICT_PREFIXES: tuple[tuple[str, str], ...] = (
    ("ok_", "accepted"),
    ("rejected_", "rejected"),
)


def _verdict(filename: str) -> str:
    for prefix, label in _VERDICT_PREFIXES:
        if filename.startswith(prefix):
            return label
    return "unknown"


def _issued_name(filename: str) -> str:
    """The FIFO name the bridge wrote, with the EA's verdict prefix removed.

    Used only as a deterministic tiebreak: the prefix must never be the
    primary sort key, because it would then group the whole trail by
    verdict instead of ordering it by time.
    """
    for prefix, _label in _VERDICT_PREFIXES:
        if filename.startswith(prefix):
            return filename[len(prefix):]
    return filename


def recent(limit: int = 20) -> list[dict[str, Any]]:
    """The audit trail the EA left behind, newest first.

    The EA renames each consumed file into ``processed/`` and prefixes the
    verdict, so this is a record of what actually happened rather than
    what was requested.

    Ordering is by the file's modification time -- i.e. *when the EA
    rendered the verdict* -- falling back to the FIFO name so identical
    timestamps still sort deterministically. Sorting on the raw filename
    would order the trail by the ``ok_``/``rejected_`` prefix rather than
    by time, which is a lie about what happened most recently.
    """
    folder = processed_dir()
    if not folder.is_dir():
        return []

    scanned: list[tuple[float, str, str]] = []
    for entry in folder.glob("*.json"):
        try:
            stamped = entry.stat().st_mtime
        except OSError:
            # --- the EA retires files concurrently, so one can disappear
            # --- between glob() and stat(). A vanished file is not an
            # --- audit entry; skipping it is correct, raising is a 500.
            continue
        scanned.append((stamped, _issued_name(entry.name), entry.name))

    if limit <= 0:
        return []

    scanned.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [
        {"file": name, "verdict": _verdict(name), "at": stamped}
        for stamped, _fifo, name in scanned[:limit]
    ]


__all__ = ["CommandError", "pending", "recent", "submit"]
