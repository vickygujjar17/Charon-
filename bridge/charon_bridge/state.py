"""Read the snapshot the EA publishes.

Everything here is read-only. The one thing this module is opinionated
about is **staleness**: a dashboard that renders a five-minute-old
snapshot as if it were live is actively dangerous, so every read carries
an explicit age and the UI is expected to shout when it is old.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import bridge_root, common_files_dir, state_path
from .schema import SchemaError, Snapshot

# --- The EA republishes once per second on its timer. Anything older than
# --- this many seconds means the terminal, the EA or the timer has stopped,
# --- and the numbers on screen are no longer being maintained.
STALE_AFTER_SECONDS = 5.0

# --- Beyond this the snapshot is treated as history, not as "current".
OFFLINE_AFTER_SECONDS = 30.0


class StateUnavailable(RuntimeError):
    """No snapshot exists yet -- the EA has not published, or the bridge is off."""


@dataclass(frozen=True)
class Reading:
    """A snapshot plus the facts needed to display it honestly."""

    snapshot: Snapshot
    path: Path
    age_seconds: float

    @property
    def stale(self) -> bool:
        return self.age_seconds > STALE_AFTER_SECONDS

    @property
    def offline(self) -> bool:
        return self.age_seconds > OFFLINE_AFTER_SECONDS

    @property
    def status(self) -> str:
        """One word the UI can colour without re-deriving thresholds."""
        if self.offline:
            return "offline"
        if self.stale:
            return "stale"
        if not self.snapshot.ready:
            return "starting"
        return "live"


def _read_json(path: Path) -> dict[str, Any]:
    """Parse a JSON file, tolerating a concurrent writer.

    The EA renames a temp file into place, so a reader should never see a
    partial document. If it somehow does, a single retry resolves it; a
    second failure is reported rather than papered over.
    """
    last_error: Exception | None = None
    for _attempt in range(2):
        try:
            with path.open("r", encoding="utf-8", errors="strict") as handle:
                loaded = json.load(handle)
            if not isinstance(loaded, dict):
                raise SchemaError("snapshot root is not an object")
            return loaded
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, SchemaError) as exc:
            last_error = exc
            time.sleep(0.05)
    raise StateUnavailable(f"could not read {path.name}: {last_error}")


def read_snapshot(symbol: str) -> Reading:
    """Load and validate the snapshot for ``symbol``."""
    path = state_path(symbol)
    if not path.is_file():
        raise StateUnavailable(
            f"no snapshot for {symbol} at {path}. "
            "Is InpBridgeEnable true on the chart?"
        )

    raw = _read_json(path)
    try:
        snapshot = Snapshot.from_json(raw)
    except SchemaError as exc:
        # --- a schema we cannot render is a setup problem, not a crash:
        # --- report it the same way as a missing file so the dashboard can
        # --- show one clear message instead of a stack trace.
        raise StateUnavailable(f"{path.name} is not renderable: {exc}") from exc

    # --- age is measured from the file, not from the EA's clock, so a
    # --- skewed terminal clock cannot disguise a dead EA as live.
    try:
        mtime = path.stat().st_mtime
    except OSError as exc:
        raise StateUnavailable(f"cannot stat {path.name}: {exc}") from exc
    age = max(0.0, time.time() - mtime)

    return Reading(snapshot=snapshot, path=path, age_seconds=age)


def discover_symbols() -> list[str]:
    """Every symbol that currently has a snapshot on disk.

    Lets the dashboard offer a chooser when the trader runs Charon on
    several charts at once, instead of guessing one.
    """
    root = bridge_root()
    if not root.is_dir():
        return []
    found: list[str] = []
    for entry in sorted(root.glob("state_*.json")):
        name = entry.stem
        if name.startswith("state_"):
            symbol = name[len("state_"):]
            if symbol:
                found.append(symbol)
    return found


def locate() -> dict[str, str]:
    """Where the bridge thinks it is looking, for the startup banner.

    A wrong directory is the most common setup mistake, so it is surfaced
    rather than buried in a traceback.
    """
    common = common_files_dir()
    root = bridge_root()
    return {
        "common_files": str(common),
        "bridge_root": str(root),
        "common_exists": "yes" if common.is_dir() else "NO",
        "root_exists": "yes" if root.is_dir() else "NO",
    }


__all__ = [
    "OFFLINE_AFTER_SECONDS",
    "STALE_AFTER_SECONDS",
    "Reading",
    "StateUnavailable",
    "discover_symbols",
    "locate",
    "read_snapshot",
]
