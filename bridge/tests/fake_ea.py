"""A stand-in for the Charon EA, for integration tests.

Why this exists
---------------
The bridge is one half of a two-process conversation, and its most
interesting behaviour only appears when both halves are present: a command
has to be drained, validated, retired into ``processed/`` with a verdict
prefix, and then reflected in the next snapshot. In-process tests can fake the
file system, but they cannot show that the *sequence* works.

There is no MetaTrader terminal in CI, so this module impersonates the parts of
``CCharonBridge`` that the bridge actually talks to. Every behaviour below is a
deliberate copy of the MQL5 code, so a future change on the EA side has an
obvious place to land here too:

* ``NextPending()``     -> oldest name by a case-insensitive lexicographic sort.
* ``ReadCommandFile()`` -> verb whitelist, symbol check, schema check, then the
                           +/-60s anti-replay window.
* ``Poll()``            -> read, **retire before applying**, one per call,
                           invalid files retired too (no head-of-line block).
* ``Retire()``          -> rename into ``processed/`` prefixed ``ok_`` or
                           ``rejected_``.

What it is not
--------------
It does not model MT5, orders, or risk. It cannot, and it must not pretend to:
the whole point of Charon is that only the EA touches the broker. This harness
proves the *plumbing*, and the CHANGELOG says so plainly.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

# --- mirrors CHARON_BRIDGE_SCHEMA / CHARON_BRIDGE_CMD_MAX_AGE in CharonBridge.mqh
SCHEMA = 1
CMD_MAX_AGE = 60

# --- mirrors CCharonBridge::VerbAllowed(). Adding one is a security decision.
ALLOWED_VERBS = ("arm", "disarm", "trail_mode", "ping")

TRAIL_MODE_NAMES = {
    0: "OFF",
    1: "POINTS",
    2: "ATR",
    3: "HWM_PERCENT",
    4: "RR_LOCK",
}


class FakeEA:
    """Drains the command inbox the way the EA does, and publishes state."""

    def __init__(self, common_dir: Path, symbol: str = "GBPUSD") -> None:
        self.common_dir = Path(common_dir)
        self.symbol = symbol

        # --- mirrored from CHARON_BRIDGE_ROOT / _CMD_DIR / _DONE_DIR
        self.root = self.common_dir / "Charon"
        self.inbox = self.root / "commands"
        self.processed = self.inbox / "processed"
        self.state_file = self.root / f"state_{symbol}.json"

        self.processed.mkdir(parents=True, exist_ok=True)

        # --- the EA's own view of the world, changed only by applied commands
        self.trail_mode = 2          # ATR
        self.armed = False
        self.arm_secs = -1
        self.ready = True
        self.seq = 0
        self.applied: list[str] = []

    #+----------------------------------------------------------------+
    #| Inbox                                                         |
    #+----------------------------------------------------------------+
    def next_pending(self) -> str | None:
        """Oldest ``*.json`` in the inbox, by the EA's own sort.

        ``StringCompare(name, best, true)`` is case-insensitive, so this sorts
        case-insensitively too rather than using a plain ``min()`` that would
        disagree on mixed-case names.
        """
        if not self.inbox.is_dir():
            return None
        names = sorted(
            (entry.name for entry in self.inbox.glob("*.json")),
            key=str.casefold,
        )
        return names[0] if names else None

    def _read_command(self, name: str) -> tuple[dict[str, Any], bool, str]:
        """Validate one command file.

        Returns ``(document, valid, reject_reason)``. The order of the checks
        matches ``ReadCommandFile``: id, verb, symbol, schema, age, then the
        per-verb argument.
        """
        path = self.inbox / name
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}, False, "unreadable json"
        if not isinstance(document, dict):
            return {}, False, "not an object"

        if not isinstance(document.get("id"), str):
            return document, False, "missing id"

        verb = document.get("verb")
        if not isinstance(verb, str):
            return document, False, "missing verb"
        if verb not in ALLOWED_VERBS:
            return document, False, f"verb '{verb}' is not permitted"

        symbol = document.get("symbol")
        if isinstance(symbol, str) and symbol not in ("", self.symbol):
            return document, False, f"symbol '{symbol}' is not '{self.symbol}'"

        if document.get("schema") != SCHEMA:
            return document, False, f"schema {document.get('schema')!r} is not {SCHEMA}"

        issued = document.get("issued")
        if not isinstance(issued, (int, float)):
            return document, False, "missing issued timestamp"
        age = int(time.time()) - int(issued)
        if age > CMD_MAX_AGE:
            return document, False, f"stale command ({age}s old)"
        if age < -CMD_MAX_AGE:
            return document, False, f"future-dated command ({-age}s ahead)"

        if verb == "trail_mode":
            mode = document.get("mode")
            if not isinstance(mode, (int, float)):
                return document, False, "trail_mode without a numeric mode"
            if int(mode) not in TRAIL_MODE_NAMES:
                return document, False, f"trail mode {mode} is out of range"

        return document, True, ""


    def _retire(self, name: str, valid: bool) -> str:
        """Rename into ``processed/`` before the command is applied."""
        tag = "ok" if valid else "rejected"
        destination = self.processed / f"{tag}_{name}"
        os.replace(self.inbox / name, destination)
        return destination.name

    def poll(self) -> dict[str, Any] | None:
        """Consume exactly one command, the way one timer tick would.

        The file is retired *before* it is applied, so a crash between the two
        cannot replay it -- the same ordering guarantee the EA makes.
        """
        name = self.next_pending()
        if name is None:
            return None

        document, valid, reject = self._read_command(name)
        archived = self._retire(name, valid)

        verdict = {
            "file": archived,
            "verb": document.get("verb"),
            "accepted": valid,
            "reason": reject,
        }

        if not valid:
            return verdict

        verb = document["verb"]
        if verb == "arm":
            self.armed = True
            self.arm_secs = 30
        elif verb == "disarm":
            self.armed = False
            self.arm_secs = -1
        elif verb == "trail_mode":
            self.trail_mode = int(document["mode"])

        self.applied.append(verb)
        return verdict

    def drain(self, limit: int = 20) -> list[dict[str, Any]]:
        """Consume up to ``limit`` commands, oldest first."""
        seen: list[dict[str, Any]] = []
        for _ in range(limit):
            verdict = self.poll()
            if verdict is None:
                break
            seen.append(verdict)
        return seen

    #+----------------------------------------------------------------+
    #| Snapshot                                                      |
    #+----------------------------------------------------------------+
    def publish(self) -> Path:
        """Write ``state_<SYMBOL>.json`` in the EA's wire format."""
        self.seq += 1
        payload = self.snapshot()
        self.root.mkdir(parents=True, exist_ok=True)
        temp = self.state_file.with_name(self.state_file.name + ".tmp")
        temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temp, self.state_file)
        return self.state_file

    def snapshot(self) -> dict[str, Any]:
        """The full snapshot document, matching ``CCharonBridge::Publish()``."""
        return {
            "schema": SCHEMA,
            "seq": self.seq,
            "ea_version": "1.0.4",
            "symbol": self.symbol,
            "server_time": int(time.time()),
            "ready": self.ready,
            "dashboard": True,
            "persist": True,
            "digits": 5,
            "currency": "USD",
            "balance": 10_000.0,
            "equity": 10_120.5,
            "basket": {
                "valid": True,
                "mixed": False,
                "dir": 0,
                "legs": 1,
                "lot": 0.30,
                "avg_entry": 1.26000,
                "open_pl": 120.50,
                "risk_money": 45.00,
                "risk_pct": 0.4447,
                "best": 1.26900,
                # --- verbatim text, exactly as RrRaderText() renders it
                "ladder": "1.50R 1.26450 | 2.00R 1.26600 | 3.00R 1.26900",
            },
            "model": {
                "sl": "300 points",
                "tp": "2.00R",
                "sizing": "0.50% of equity",
                "max_risk_pct": 1.0,
            },
            "trail": {
                "mode": self.trail_mode,
                "mode_name": TRAIL_MODE_NAMES[self.trail_mode],
                "mutable": True,
            },
            "pyramid": {
                "enabled": True,
                "max_legs": 3,
                "max_risk_pct": 1.5,
                "trigger": "MANUAL",
                "arm_key": 65,
                "armed": self.armed,
                "secs_left": self.arm_secs,
                "next_tier": 2,
                "progress_rr": 1.42,
            },
            "legs": [
                {
                    "ticket": 111,
                    "type": 0,
                    "side": "BUY",
                    "tier": 1,
                    "entry": 1.26000,
                    "lot": 0.30,
                    "sl": 1.25700,
                    "risk_price": 0.00300,
                    "hwm": 1.26900,
                    "progress_rr": 3.0,
                    "trail_active": True,
                    "be_done": True,
                    "half_risk_done": True,
                    "ladder": "1.50R 1.26450 | 2.00R 1.26600 | 3.00R 1.26900",
                }
            ],
        }


__all__ = ["ALLOWED_VERBS", "CMD_MAX_AGE", "SCHEMA", "TRAIL_MODE_NAMES", "FakeEA"]

