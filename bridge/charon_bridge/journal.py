"""The trade journal: what the operator meant, kept beside the ticket.

Why this is not a snapshot field
--------------------------------
The EA is the authority on *what happened* -- the volume it solved, the stop
it derived, the rungs it rendered. It is not, and should not become, a store
for free text. A broker comment is a fixed-size byte field, and the EA's own
JSON reader is hand-written and deliberately simple; pushing prose through
that channel would make a note a source of trading failure.

So the journal lives here instead, in a sidecar keyed by ticket:

    ``<Common>\\Files\\Charon\\journal_<SYMBOL>.json``

The EA still gets a short ASCII tag for the broker comment (see
:func:`charon_bridge.schema.journal_tag`), and publishes it back in the
snapshot so the dashboard can show which note is attached to which position.
That tag is lossy and is never expanded back into the note.

Two invariants:

* **A note is never invented.** An unknown ticket has no entry, and the
  dashboard shows nothing rather than an empty string that looks like a note.
* **A missing or corrupt file is empty, never fatal.** A journal is
  commentary; losing it must not stop a dashboard from rendering live risk.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .paths import journal_path

# --- how many notes to keep. The file is rewritten whole on each write, so
# --- this is a real bound on its size rather than a display limit.
MAX_ENTRIES = 500

# --- notes waiting for a ticket. An order is queued before a position exists,
# --- so a note can briefly have no key; this bounds that queue.
MAX_UNFILED = 50


def _load_all(symbol: str) -> dict[str, Any]:
    """The whole sidecar, normalised. Never raises."""
    path = journal_path(symbol)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    entries = raw.get("entries")
    unfiled = raw.get("unfiled")
    return {
        "entries": entries if isinstance(entries, dict) else {},
        "unfiled": unfiled if isinstance(unfiled, list) else [],
    }


def _load(symbol: str) -> dict[str, Any]:
    """Every stored entry for ``symbol``, tolerating a damaged file."""
    return _load_all(symbol)["entries"]


def _save(symbol: str, document: dict[str, Any]) -> None:
    """Replace the sidecar atomically, keeping the newest entries only."""
    # --- dicts preserve insertion order, and an existing key keeps its
    # --- original position, so the tail really is the newest set.
    entries = dict(list(document["entries"].items())[-MAX_ENTRIES:])
    unfiled = document["unfiled"][-MAX_UNFILED:]
    path = journal_path(symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    payload = json.dumps(
        {"symbol": symbol, "entries": entries, "unfiled": unfiled}, indent=2
    )
    try:
        temp.write_text(payload, encoding="utf-8")
        os.replace(temp, path)
    except OSError:
        temp.unlink(missing_ok=True)


def note(symbol: str, ticket: int, text: str | None, *, tag: str = "") -> None:
    """Attach ``text`` to ``ticket``, or to the unfiled queue when it is 0.

    A ticket of 0 is meaningful, not a mistake: an ``order_open`` is queued
    before any ticket exists, so the note has nowhere to live yet. It goes on
    a FIFO and :func:`rekey` claims it the moment the EA reports a real
    ticket. Refusing 0 would mean the journal could only ever record notes
    for trades that already existed.
    """
    if not text:
        return
    document = _load_all(symbol)
    if ticket <= 0:
        document["unfiled"].append({"note": text, "tag": tag})
    else:
        document["entries"][str(int(ticket))] = {"note": text, "tag": tag}
    _save(symbol, document)


def entries(symbol: str) -> dict[int, dict[str, Any]]:
    """Every note for ``symbol``, keyed by integer ticket."""
    out: dict[int, dict[str, Any]] = {}
    for key, value in _load(symbol).items():
        try:
            ticket = int(key)
        except (TypeError, ValueError):
            continue
        if ticket > 0 and isinstance(value, dict):
            out[ticket] = value
    return out


def for_symbol(symbol: str) -> list[dict[str, Any]]:
    """The journal as the API renders it: newest first, tickets as integers."""
    rows = [
        {
            "ticket": ticket,
            "note": value.get("note"),
            "tag": value.get("tag", ""),
        }
        for ticket, value in entries(symbol).items()
    ]
    rows.sort(key=lambda row: row["ticket"], reverse=True)
    return rows


def rekey(symbol: str, from_ticket: int, to_ticket: int) -> None:
    """Give a queued note the ticket the EA finally reported.

    ``order_open`` is submitted before any ticket exists, so its note starts on
    the unfiled FIFO (``from_ticket`` 0) or against a ticket the client guessed.
    This is the step that gives it a final home so the position row can show it.

    A no-op when there is nothing to move: re-keying is housekeeping, never a
    reason to fail a request.
    """
    if to_ticket <= 0 or from_ticket == to_ticket:
        return
    document = _load_all(symbol)
    source = None
    if from_ticket <= 0:
        if document["unfiled"]:
            source = document["unfiled"].pop(0)
    else:
        source = document["entries"].pop(str(int(from_ticket)), None)
    if source is None:
        return
    document["entries"][str(int(to_ticket))] = source
    _save(symbol, document)


__all__ = ["MAX_ENTRIES", "MAX_UNFILED", "entries", "for_symbol", "note", "rekey"]
