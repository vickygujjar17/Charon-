"""The journal sidecar: notes live on the bridge, keyed by ticket.

The property these tests exist to pin: a note is *commentary*, so it must
never be able to break a dashboard. A missing file, a corrupt file, an
unknown ticket and a re-key all have to behave predictably, because the
journal is written on the same request path that queues a real order.
"""

from __future__ import annotations

import json
from pathlib import Path

from charon_bridge import journal, paths


def test_a_note_is_stored_and_read_back(common_dir: Path) -> None:
    journal.note("GBPUSD", 111, "London break", tag="LONDON-BREAK")
    rows = journal.for_symbol("GBPUSD")
    assert len(rows) == 1
    assert rows[0]["ticket"] == 111
    assert rows[0]["note"] == "London break"
    assert rows[0]["tag"] == "LONDON-BREAK"


def test_the_sidecar_lives_beside_the_snapshot(common_dir: Path) -> None:
    """Per symbol, not global: two charts must not interleave their notes."""
    journal.note("GBPUSD", 111, "one")
    journal.note("EURUSD", 222, "two")
    assert paths.journal_path("GBPUSD").name == "journal_GBPUSD.json"
    assert journal.for_symbol("GBPUSD")[0]["note"] == "one"
    assert journal.for_symbol("EURUSD")[0]["note"] == "two"


def test_an_unknown_ticket_has_no_note(common_dir: Path) -> None:
    """Absent is not empty text: the UI shows nothing rather than a blank."""
    assert journal.for_symbol("GBPUSD") == []
    journal.note("GBPUSD", 111, "known")
    assert journal.entries("GBPUSD").get(999) is None


def test_a_missing_or_corrupt_file_reads_as_empty(common_dir: Path) -> None:
    """Commentary must never become an outage."""
    path = paths.journal_path("GBPUSD")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert journal.for_symbol("GBPUSD") == []
    # --- and writing afterwards repairs it rather than failing
    journal.note("GBPUSD", 42, "after the damage")
    assert journal.for_symbol("GBPUSD")[0]["note"] == "after the damage"


def test_a_blank_note_is_not_stored(common_dir: Path) -> None:
    journal.note("GBPUSD", 111, "")
    journal.note("GBPUSD", 112, None)
    assert journal.for_symbol("GBPUSD") == []


def test_a_note_for_a_queued_order_waits_on_the_unfiled_queue(common_dir: Path) -> None:
    """Ticket 0 is meaningful: the order is queued before a position exists."""
    journal.note("GBPUSD", 0, "queued before the fill")
    assert journal.for_symbol("GBPUSD") == []      # nothing to attribute yet
    journal.rekey("GBPUSD", 0, 555)
    assert journal.for_symbol("GBPUSD")[0]["note"] == "queued before the fill"


def test_rekeying_moves_a_note_onto_the_position_ticket(common_dir: Path) -> None:
    """A ticket is queued before a position exists, so the note starts unfiled."""
    journal.note("GBPUSD", 0, "unfiled")
    journal.rekey("GBPUSD", 0, 555)
    rows = journal.for_symbol("GBPUSD")
    assert [row["ticket"] for row in rows] == [555]
    assert rows[0]["note"] == "unfiled"


def test_rekeying_without_a_source_is_a_no_op(common_dir: Path) -> None:
    journal.rekey("GBPUSD", 0, 555)
    assert journal.for_symbol("GBPUSD") == []


def test_the_newest_entries_survive_the_trim(common_dir: Path) -> None:
    """The file is rewritten whole, so the bound has to be real."""
    for ticket in range(1, journal.MAX_ENTRIES + 6):
        journal.note("GBPUSD", ticket, f"note {ticket}")
    stored = journal.entries("GBPUSD")
    assert len(stored) == journal.MAX_ENTRIES
    # --- the tail is what was written last
    assert max(stored) == journal.MAX_ENTRIES + 5
    assert min(stored) == 6


def test_re_noting_a_ticket_updates_it_in_place(common_dir: Path) -> None:
    journal.note("GBPUSD", 111, "first")
    journal.note("GBPUSD", 111, "second")
    rows = journal.for_symbol("GBPUSD")
    assert len(rows) == 1
    assert rows[0]["note"] == "second"


def test_the_file_is_written_as_a_readable_document(common_dir: Path) -> None:
    journal.note("GBPUSD", 111, "London break", tag="LONDON-BREAK")
    raw = json.loads(paths.journal_path("GBPUSD").read_text(encoding="utf-8"))
    assert raw["symbol"] == "GBPUSD"
    assert raw["entries"]["111"]["note"] == "London break"
