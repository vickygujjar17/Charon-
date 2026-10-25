"""Write commands into the EA's inbox.

The only writer of files the EA acts on, so this module is deliberately
small, synchronous and paranoid:

* the name is ``<epoch-ns>-<counter>-<rand>.json`` so the EA's plain
  lexicographic sort is a true FIFO -- an ``arm`` issued before a
  ``disarm`` can never be applied in the wrong order;
* the payload lands via a temp file and an atomic rename, so the EA can
  never open a half-written command;
* only verbs in :data:`charon_bridge.schema.ALLOWED_VERBS` are accepted.

The one clock rule that must not be relaxed: ``issued`` is a **local** epoch
second, because the EA compares it against ``TimeLocal()`` -- not against
``TimeGMT()``. See :func:`local_epoch`.

There is no code path here that can express an order. See
``docs`` in ``README.md`` for why that is a hard boundary.
"""

from __future__ import annotations

import calendar
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from .paths import (
    PROCESSED_NAME,
    ensure_dirs,
    inbox_for,
    inboxes,
    iter_commands,
)
from .schema import ALLOWED_VERBS, OrderIntent, SchemaError, build_command


class CommandError(RuntimeError):
    """A command could not be written."""


_counter = 0


def local_epoch(now_ns: int | None = None) -> int:
    """Epoch seconds on the **local clock**, which is the EA's reference.

    ``time.time_ns() // 1_000_000_000`` is a UTC epoch second. The EA does
    not compare against UTC -- ``CharonBridge.mqh`` reads
    ``(long)TimeLocal()`` -- so the two numbers differ by the host's UTC
    offset. On a UTC VPS beside an IST desktop that is 19800 seconds, and
    every command was refused as ``future-dated command (19800s ahead)``:
    the whole dashboard was silently inert, with the refusal visible only
    in the EA's Experts log and in the ``rejected_`` prefix of the archive.

    ``time.localtime()`` applies whichever offset (including DST) was in
    force at that instant, and ``calendar.timegm`` re-reads the resulting
    fields as if they were UTC. The result is exactly what ``TimeLocal()``
    returns: seconds since the epoch *of the local wall clock*.

    Deriving it this way rather than adding a fixed offset keeps a
    DST-crossing session correct, and it does not depend on ``tm_gmtoff``,
    which is not available on every platform.
    """
    seconds = (time.time_ns() if now_ns is None else now_ns) / 1_000_000_000
    return calendar.timegm(time.localtime(seconds))


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


def submit(verb: str, symbol: str, *, mode: int | None = None,
           order: OrderIntent | None = None,
           ticket: int | None = None) -> dict[str, Any]:
    """Queue one command for the EA and return what was sent.

    ``issued`` is the **local** epoch second (:func:`local_epoch`). The EA
    compares it against its own clock and refuses anything more than
    ``CHARON_BRIDGE_CMD_MAX_AGE`` away in either direction, so a file left
    on disk and replayed later is a no-op rather than a surprise.

    For ``order_open``/``order_modify`` the caller must pass a validated
    :class:`~charon_bridge.schema.OrderIntent`; for ``order_modify``/
    ``order_cancel`` a positive ticket. This module still writes the file
    and nothing else -- the EA decides whether the order happens.
    """
    if verb not in ALLOWED_VERBS:
        raise SchemaError(f"verb {verb!r} is not permitted")

    # --- resolve first, then make sure *that* inbox exists. Resolving
    # --- before creating is what lets a v1.2.0 EA keep working: its
    # --- subfolder does not exist, so the command goes to the flat inbox.
    folder = inbox_for(symbol)
    ensure_dirs(symbol)
    # --- two clocks on purpose: the file name stays in UTC nanoseconds
    # --- because only relative order matters there and monotonicity is
    # --- enough, while ``issued`` must match the EA's TimeLocal().
    now_ns = time.time_ns()
    issued = local_epoch(now_ns)
    command_id = uuid.uuid4().hex

    doc = build_command(verb, issued, symbol=symbol, command_id=command_id,
                        mode=mode, order=order, ticket=ticket)
    name = _next_name(now_ns)
    payload = json.dumps(doc, indent=2, sort_keys=True) + "\n"

    path = folder / name
    _atomic_write(path, payload)
    return {"id": command_id, "file": name, "verb": verb, "sent": doc}


def _claims(entry: Path, symbol: str) -> bool:
    """Whether the command in ``entry`` belongs to ``symbol``.

    A file in a per-symbol inbox is attributed by its folder. A file in the
    legacy flat inbox has no folder to go by, so it is attributed by the
    ``symbol`` field the EA itself checks -- the same field that makes the
    command acceptable to that chart and no other.

    An unreadable or symbol-less file is **not** attributed: it cannot be
    shown under the right chart without guessing, and showing it under every
    chart would make one symbol's queue look like another's. It still appears
    in the unscoped view, so nothing is hidden from the operator.

    Note that the EA treats an empty ``symbol`` as a broadcast -- any chart
    will apply it -- so it has no single owner by construction, however
    tempting it is to guess one.
    """
    try:
        with entry.open("r", encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    if not isinstance(document, dict):
        return False
    owner = document.get("symbol")
    return isinstance(owner, str) and owner == symbol


def pending(symbol: str | None = None) -> list[str]:
    """Command files still waiting for the EA, oldest first.

    ``symbol`` narrows the answer to one chart's queue; ``None`` is the
    whole queue, in the order the EA will drain it.
    """
    return [name for _symbol, name in _pending_entries(symbol)]


def _pending_entries(symbol: str | None) -> list[tuple[str, str]]:
    """``(symbol, filename)`` for every waiting command, oldest first."""
    return sorted(
        ((sym, entry.name) for sym, entry in iter_commands(symbol)
         if entry.name.endswith(".json")
         and (symbol is None or sym != "" or _claims(entry, symbol))),
        key=lambda item: (item[1], item[0]),
    )



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


def recent(limit: int = 20, symbol: str | None = None) -> list[dict[str, Any]]:
    """The audit trail the EA left behind, newest first.

    The EA renames each consumed file into ``processed/`` and prefixes the
    verdict, so this is a record of what actually happened rather than
    what was requested.

    Every archive is read -- the legacy inbox's plus one per symbol -- so
    the trail does not lose its history at the moment the EA is upgraded to
    per-symbol folders.

    Ordering is by the file's modification time -- i.e. *when the EA
    rendered the verdict* -- falling back to the FIFO name so identical
    timestamps still sort deterministically. Sorting on the raw filename
    would order the trail by the ``ok_``/``rejected_`` prefix rather than
    by time, which is a lie about what happened most recently.
    """
    if limit <= 0:
        return []

    scanned: list[tuple[float, str, str]] = []
    for folder_name, folder in inboxes(symbol):
        archive = folder / PROCESSED_NAME
        if not archive.is_dir():
            continue
        for entry in archive.glob("*.json"):
            # --- a file in a per-symbol archive is attributed by its folder;
            # --- a file in the legacy archive only by its own payload, and
            # --- only when the payload names this symbol.
            if symbol is not None and folder_name == "" and not _claims(entry, symbol):
                continue
            try:
                stamped = entry.stat().st_mtime
            except OSError:
                # --- the EA retires files concurrently, so one can vanish
                # --- between glob() and stat(). A vanished file is not an
                # --- audit entry; skipping it is correct, raising is a 500.
                continue
            scanned.append((stamped, _issued_name(entry.name), entry.name))

    scanned.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [
        {"file": name, "verdict": _verdict(name), "at": stamped}
        for stamped, _fifo, name in scanned[:limit]
    ]


__all__ = ["CommandError", "local_epoch", "pending", "recent", "submit"]

