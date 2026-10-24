"""In-process event log for the dashboard's live tail.

An MQL5 EA cannot push to a browser, and tailing the terminal's own log
files is brittle (paths differ per install, lines are reordered, and the
file is locked while the terminal writes). So the bridge keeps its own
ring buffer of the things it can observe -- snapshot reads, command
writes, EA verdicts -- and streams that.

It is explicitly *not* a mirror of the EA's Experts log. The EA is the
source of truth for trading decisions; this is a bridge-side transcript,
and the README says so.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any

# --- plenty for a session tail, small enough to stay invisible in RAM
MAX_ENTRIES = 500


@dataclass(frozen=True)
class Entry:
    seq: int
    at: float
    level: str      # info | warn | error | command
    message: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class EventLog:
    """Thread-safe ring buffer with a monotonic sequence.

    FastAPI runs handlers on several worker threads, so every mutation
    and every read takes the lock. The buffer is a ``deque`` with a fixed
    ``maxlen``, which makes trimming O(1) and unbounded growth impossible.
    """

    def __init__(self, maxlen: int = MAX_ENTRIES) -> None:
        self._lock = threading.Lock()
        self._entries: deque[Entry] = deque(maxlen=maxlen)
        self._seq = 0

    def add(self, level: str, message: str) -> Entry:
        with self._lock:
            self._seq += 1
            entry = Entry(seq=self._seq, at=time.time(), level=level, message=message)
            self._entries.append(entry)
            return entry

    def info(self, message: str) -> Entry:
        return self.add("info", message)

    def warn(self, message: str) -> Entry:
        return self.add("warn", message)

    def error(self, message: str) -> Entry:
        return self.add("error", message)

    def command(self, message: str) -> Entry:
        return self.add("command", message)

    def since(self, seq: int) -> list[Entry]:
        """Every entry newer than ``seq``, oldest first."""
        with self._lock:
            return [entry for entry in self._entries if entry.seq > seq]

    def tail(self, limit: int = 100) -> list[Entry]:
        with self._lock:
            items = list(self._entries)
        return items[-limit:]

    @property
    def latest_seq(self) -> int:
        with self._lock:
            return self._seq


# --- one process-wide log; the app wires it into the routes
LOG = EventLog()

__all__ = ["MAX_ENTRIES", "LOG", "Entry", "EventLog"]
