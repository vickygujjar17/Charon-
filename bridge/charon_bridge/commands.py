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


def recent(limit: int = 20) -> list[dict[str, Any]]:
    """The audit trail the EA left behind, newest first.

    The EA renames each consumed file into ``processed/`` and prefixes the
    verdict, so this is a record of what actually happened rather than
    what was requested.
    """
    folder = processed_dir()
    if not folder.is_dir():
        return []

    entries = sorted(folder.glob("*.json"), key=lambda p: p.name, reverse=True)[:limit]
    trail: list[dict[str, Any]] = []
    for entry in entries:
        verdict = "unknown"
        if entry.name.startswith("ok_"):
            verdict = "accepted"
        elif entry.name.startswith("rejected_"):
            verdict = "rejected"
        trail.append({
            "file": entry.name,
            "verdict": verdict,
            "at": entry.stat().st_mtime,
        })
    return trail


__all__ = ["CommandError", "pending", "recent", "submit"]
