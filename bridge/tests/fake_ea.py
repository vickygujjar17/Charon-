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
                           +/-60s anti-replay window **against the local
                           clock**, because ``Poll()`` compares ``issued`` to
                           ``(long)TimeLocal()``.
* ``Poll()``            -> read, **retire before applying**, one per call,
                           invalid files retired too (no head-of-line block).
* ``Retire()``          -> rename into ``processed/`` prefixed ``ok_`` or
                           ``rejected_``.
* ``Init()``            -> creates ``commands\\<SYMBOL>`` and drains only that
                           folder; ``legacy=True`` keeps the pre-v1.3.0 flat
                           inbox instead, for the routing tests.

What it is not
--------------
It does not model MT5, orders, or risk. It cannot, and it must not pretend to:
the whole point of Charon is that only the EA touches the broker. This harness
proves the *plumbing*, and the CHANGELOG says so plainly.
"""

from __future__ import annotations

import calendar
import json
import os
import time
from pathlib import Path
from typing import Any

# --- mirrors CHARON_BRIDGE_SCHEMA / CHARON_BRIDGE_CMD_MAX_AGE in CharonBridge.mqh
SCHEMA = 4
CMD_MAX_AGE = 60


def local_epoch(now: float | None = None) -> int:
    """``(long)TimeLocal()`` -- the clock ``ReadCommandFile`` compares against.

    Not ``time.time()``. ``TimeLocal`` is the terminal host's wall clock,
    while ``time.time()`` is UTC; on a UTC+5:30 terminal the two differ by
    19800 seconds, which is 330 times the 60-second tolerance. A harness
    that used the UTC epoch would accept every command the real EA rejects,
    and this file exists precisely so that gap cannot hide.
    """
    seconds = time.time() if now is None else now
    return calendar.timegm(time.localtime(seconds))


# --- mirrors CCharonBridge::VerbAllowed(). Adding one is a security decision.
ALLOWED_VERBS = ("arm", "disarm", "trail_mode", "ping",
                 "order_open", "order_modify", "order_cancel",
                 "watch_add", "watch_cancel")
ORDER_VERBS = ("order_open", "order_modify", "order_cancel")
# --- ``watch_add`` is validated exactly like ``order_open``: a watch defers
# --- *when* a ticket is submitted, never *what* it must satisfy.
TICKET_VERBS = ORDER_VERBS + ("watch_add",)
WATCH_VERBS = ("watch_add", "watch_cancel")
MAX_RUNG = 3
MAX_STAGES = 3
TRIGGER_KINDS = ("rr", "price")

TRAIL_MODE_NAMES = {
    0: "OFF",
    1: "POINTS",
    2: "ATR",
    3: "HWM_PERCENT",
    4: "RR_LOCK",
}


class FakeEA:
    """Drains the command inbox the way the EA does, and publishes state."""

    def __init__(self, common_dir: Path, symbol: str = "GBPUSD",
                 legacy: bool = False) -> None:
        self.common_dir = Path(common_dir)
        self.symbol = symbol
        self.legacy = legacy

        # --- mirrored from CHARON_BRIDGE_ROOT / _CMD_DIR / _DONE_DIR
        self.root = self.common_dir / "Charon"
        self.commands_root = self.root / "commands"
        # --- a v1.3.0 EA drains commands\<SYMBOL> and nothing else; the
        # --- pre-v1.3.0 one drained the flat folder. ``legacy`` selects the
        # --- old behaviour so the routing handshake can be tested.
        self.inbox = self.commands_root if legacy else self.commands_root / symbol
        self.processed = self.inbox / "processed"
        self.state_file = self.root / f"state_{symbol}.json"

        # --- mirror Init(): the EA creates its own inbox. The bridge treats
        # --- this folder's existence as "this EA speaks per-symbol".
        self.processed.mkdir(parents=True, exist_ok=True)

        # --- the EA's own view of the world, changed only by applied commands
        self.trail_mode = 2          # ATR
        self.armed = False
        self.arm_secs = -1
        self.ready = True
        self.seq = 0
        self.applied: list[str] = []
        # --- the EA's verdict on the last attended ticket
        self.order: dict[str, Any] = {
            "status": "idle", "reason": "", "side": "", "lots": 0.0,
            "entry": 0.0, "stop": 0.0, "risk_money": 0.0, "risk_pct": 0.0,
            "rung": 0, "ticket": 0, "journal_tag": "",
        }
        # --- v1.7.0: the pyramid ladder the last ticket carried, exactly as it
        # --- arrived. Kept separate from ``order`` because a plan outlives the
        # --- verdict that accepted it: the leg it drives opens later.
        self.stages: list[dict[str, Any]] = []
        # --- working pending orders, as CollectPending would report them.
        # --- Tests that need one append a row rather than relying on a
        # --- default, because a pending ticket is something the dashboard
        # --- offers to cancel.
        self.pending: list[dict[str, Any]] = []
        # --- the EA's watch queue, as CollectWatches would report it. Rows are
        # --- added by ``watch_add`` and removed by ``watch_cancel``; a test
        # --- that wants one armed can also append a row directly.
        self.watches: list[dict[str, Any]] = []
        self._next_watch_id = 1

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
        age = local_epoch() - int(issued)
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

        if verb in TICKET_VERBS:
            reject = self._validate_ticket(document, verb)
            if reject:
                return document, False, reject

        if verb == "watch_cancel":
            watch_id = document.get("watch_id")
            if isinstance(watch_id, bool) or not isinstance(watch_id, int) or watch_id <= 0:
                return document, False, "watch_cancel needs a positive watch_id"

        return document, True, ""

    def _validate_ticket(self, doc: dict[str, Any], verb: str) -> str:
        """Mirror the order-ticket checks in ``ReadCommandFile``.

        Empty string means valid. The checks are kept in the same order as the
        MQL5 parser so a ticket the real EA would refuse also fails here --
        that equivalence is the only thing that makes these tests meaningful.
        """
        side = doc.get("side")
        if side not in ("BUY", "SELL"):
            return "order ticket without a valid side"

        otype = doc.get("order_type", "MARKET")
        if otype not in ("MARKET", "LIMIT"):
            return f"order type '{otype}' is not supported"

        top = doc.get("block_top")
        bottom = doc.get("block_bottom")
        for name, value in (("block_top", top), ("block_bottom", bottom)):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return f"{name} is not a number"
            if not value > 0:
                return f"{name} is not positive"
        if top == bottom:
            return "block geometry is degenerate"
        if side == "BUY" and top <= bottom:
            return "a BUY block needs block_top above block_bottom"
        if side == "SELL" and bottom <= top:
            return "a SELL block needs block_bottom above block_top"

        entry = doc.get("entry")
        if entry is not None:
            if not isinstance(entry, (int, float)) or isinstance(entry, bool) or not entry > 0:
                return "entry price is not a positive number"
            if otype == "LIMIT":
                lo, hi = (bottom, top) if top > bottom else (top, bottom)
                if not (lo < entry < hi):
                    return "a LIMIT entry must sit inside the block"
        elif otype == "LIMIT":
            return "a LIMIT order needs an entry price"

        risk = doc.get("risk_pct")
        money = doc.get("risk_money")
        # --- exactly one risk statement, mirroring the MQL5 parser
        if risk is not None and money is not None:
            return "order ticket states risk_pct and risk_money; send one"
        if risk is None and money is None:
            return "order ticket without risk_pct or risk_money"
        if risk is not None:
            if not isinstance(risk, (int, float)) or isinstance(risk, bool):
                return "order ticket without risk_pct"
            if not 0 < risk <= 100:
                return f"risk {risk}% is outside the permitted range"
        if money is not None:
            if not isinstance(money, (int, float)) or isinstance(money, bool) or money <= 0:
                return f"risk money {money} is not positive"

        ceiling = doc.get("basket_risk_ceiling_pct")
        if ceiling is not None:
            if not isinstance(ceiling, (int, float)) or isinstance(ceiling, bool):
                return "basket ceiling is not a number"
            if not 0 <= ceiling <= 100:
                return f"basket ceiling {ceiling}% is outside [0, 100]"
            if risk is not None and ceiling > 0 and risk > ceiling:
                return "risk_pct already exceeds the basket ceiling"

        for key in ("trail_start_rr", "trail_start_points"):
            start = doc.get(key)
            if start is None:
                continue
            if not isinstance(start, (int, float)) or isinstance(start, bool) or start < 0:
                return f"{key} is not positive"

        # --- v1.5.0. An absolute activation price is validated here only for
        # --- sign and exclusivity: whether it sits on the profitable side of
        # --- the entry depends on the fill, which the EA learns at submit
        # --- time, not the parser.
        start_price = doc.get("trail_start_price")
        if start_price is not None:
            if not isinstance(start_price, (int, float)) or isinstance(start_price, bool):
                return "trail_start_price is not a number"
            if start_price <= 0:
                return "trail_start_price is not positive"
            stated = [key for key in ("trail_start_rr", "trail_start_points")
                      if doc.get(key) is not None]
            if stated:
                return "state trail activation once; " + ", ".join(stated) + " also given"

        if verb == "watch_add":
            if otype != "MARKET":
                return "watch_add only defers a MARKET entry"
            if entry is None:
                return "watch_add needs an entry price to watch for"

        # --- v1.7.0 pyramid stages. Mirrors ReadStages + StagesAdvance: a stage
        # --- is per-kind valid, the ladder must advance in the trade's
        # --- direction, and like kinds must be monotonic so no stage is
        # --- unreachable. An absent array is not an error.
        raw_stages = doc.get("stages")
        if raw_stages is not None:
            if not isinstance(raw_stages, list):
                return "stages is not an array"
            if len(raw_stages) > 3:
                return "a ticket may carry at most 3 pyramid stages"
            last_price = float("inf") if side == "SELL" else float("-inf")
            last_rr = float("inf") if side == "SELL" else float("-inf")
            for raw in raw_stages:
                if not isinstance(raw, dict):
                    return "a pyramid stage is not an object"
                kind = raw.get("kind")
                if kind not in ("rr", "price"):
                    return f"stage kind '{kind}' is not supported"
                trigger = raw.get("trigger")
                if (not isinstance(trigger, (int, float)) or isinstance(trigger, bool)
                        or not trigger > 0):
                    return "a pyramid stage has no positive trigger"
                mult = raw.get("lots_mult", 1.0)
                if (not isinstance(mult, (int, float)) or isinstance(mult, bool)
                        or not mult > 0):
                    return "a pyramid stage has a non-positive volume multiplier"
                direction = "pyramid stages do not advance in the trade's direction"
                if kind == "price":
                    # --- beyond the block, then strictly monotonic
                    if side == "BUY" and trigger <= top:
                        return direction
                    if side == "SELL" and trigger >= bottom:
                        return direction
                    if side == "BUY" and trigger <= last_price:
                        return direction
                    if side == "SELL" and trigger >= last_price:
                        return direction
                    last_price = float(trigger)
                else:
                    if side == "BUY" and trigger <= last_rr:
                        return direction
                    if side == "SELL" and trigger >= last_rr:
                        return direction
                    last_rr = float(trigger)

        tag = doc.get("journal_tag")
        if tag is not None:
            if not isinstance(tag, str):
                return "journal tag is not text"
            if len(tag) > 24:
                return "journal tag is too long"
            if not tag.isascii() or any(ord(c) < 32 or ord(c) > 126 for c in tag):
                return "journal tag carries characters a broker comment cannot hold"

        stages = doc.get("stages")
        if stages is not None:
            if not isinstance(stages, list):
                return "stages is not an array"
            if len(stages) > MAX_STAGES:
                return f"a ticket may carry at most {MAX_STAGES} pyramid stages"
            last_rr = None
            last_price = None
            for stage in stages:
                if not isinstance(stage, dict):
                    return "a pyramid stage is not an object"
                kind = stage.get("kind", "rr")
                if kind not in TRIGGER_KINDS:
                    return f"stage kind '{kind}' is not supported"
                trigger = stage.get("trigger")
                if not isinstance(trigger, (int, float)) or isinstance(trigger, bool) or trigger <= 0:
                    return "a pyramid stage has no positive trigger"
                mult = stage.get("lots_mult", 1.0)
                if not isinstance(mult, (int, float)) or isinstance(mult, bool) or mult <= 0:
                    return "a pyramid stage has a non-positive volume multiplier"
                if kind == "price":
                    if side == "BUY" and trigger <= top:
                        return "pyramid stages do not advance in the trade's direction"
                    if side == "SELL" and trigger >= bottom:
                        return "pyramid stages do not advance in the trade's direction"
                    if last_price is not None:
                        if side == "BUY" and trigger <= last_price:
                            return "pyramid stages do not advance in the trade's direction"
                        if side == "SELL" and trigger >= last_price:
                            return "pyramid stages do not advance in the trade's direction"
                    last_price = trigger
                else:
                    if last_rr is not None:
                        if side == "BUY" and trigger <= last_rr:
                            return "pyramid stages do not advance in the trade's direction"
                        if side == "SELL" and trigger >= last_rr:
                            return "pyramid stages do not advance in the trade's direction"
                    last_rr = trigger

        trigger = doc.get("trail_trigger", 0)
        if int(trigger) not in TRAIL_MODE_NAMES:
            return f"trail trigger {trigger} is out of range"

        rung = doc.get("rung", 0)
        if isinstance(rung, bool) or not isinstance(rung, int) or not 0 <= rung <= MAX_RUNG:
            return f"rung {rung} is out of range"

        if verb in ("order_modify", "order_cancel"):
            ticket = doc.get("ticket")
            if isinstance(ticket, bool) or not isinstance(ticket, int) or ticket <= 0:
                return "order ticket has no positive ticket number"

        return ""


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
        elif verb == "order_cancel":
            self.order = {
                "status": "accepted",
                "reason": f"pending #{document['ticket']} cancelled",
                "side": "", "lots": 0.0, "entry": 0.0, "stop": 0.0,
                "risk_money": 0.0, "risk_pct": 0.0, "rung": 0,
                "ticket": int(document["ticket"]),
            }
        elif verb == "watch_add":
            # --- the ticket is parsed and validated, then *parked*. Nothing
            # --- is submitted: the real EA only submits once its own tick
            # --- reaches ``entry``. ``lot``/``stop`` are deliberately left
            # --- unsolved here for the same reason -- the EA solves volume
            # --- against the fill, and a watch has no fill yet.
            watch = {
                "id": self._next_watch_id,
                "side": document["side"],
                "price": float(document["entry"]),
                "order_type": "MARKET",
                "created_at": int(time.time()),
                "status": "armed",
            }
            self._next_watch_id += 1
            self.watches.append(watch)
            self.order = {
                "status": "watching",
                "reason": f"watching {document['entry']}",
                "side": document["side"], "lots": 0.0,
                "entry": float(document["entry"]), "stop": 0.0,
                "risk_money": 0.0, "risk_pct": 0.0, "rung": 0,
                "ticket": 0,
                "journal_tag": document.get("journal_tag", ""),
            }
        elif verb == "watch_cancel":
            watch_id = int(document["watch_id"])
            before = len(self.watches)
            self.watches = [row for row in self.watches if row["id"] != watch_id]
            cancelled = len(self.watches) < before
            self.order = {
                "status": "accepted" if cancelled else "rejected",
                "reason": (f"watch #{watch_id} cancelled" if cancelled
                           else f"no watch #{watch_id}"),
                "side": "", "lots": 0.0, "entry": 0.0, "stop": 0.0,
                "risk_money": 0.0, "risk_pct": 0.0, "rung": 0,
                "ticket": 0,
            }
        else:
            # --- order_open / order_modify: simulate the EA's adjudication.
            # --- The volume is SOLVED here from the block height and the
            # --- ticket's budget, never taken from the command, which is
            # --- exactly the property the tests below assert.
            side = document["side"]
            # --- v1.7.0: the ladder the ticket carried is retained verbatim;
            # --- the real EA parses it onto the primary row, and the dashboard
            # --- reads it back from the snapshot. Retaining it here is what
            # --- lets a test prove nothing was dropped or reordered in flight.
            self.stages = [dict(row) for row in (document.get("stages") or [])]
            # --- risk is stated once; the fake honours whichever form arrived,
            # --- exactly as ApplyOrderCommand does. A money budget is used
            # --- as-is (the real EA clamps it first).
            if document.get("risk_money") is not None:
                budget = float(document["risk_money"])
                risk_pct = round(budget / 10_000.0 * 100.0, 4)
            else:
                risk_pct = float(document["risk_pct"])
                budget = 10_000.0 * risk_pct / 100.0
            height = abs(float(document["block_top"]) - float(document["block_bottom"]))
            lots = round(budget / (height * 100_000.0) / 0.01) * 0.01
            lots = max(lots, 0.01)
            entry = float(document.get("entry") or 1.26000)
            stop = (float(document["block_bottom"]) if side == "BUY"
                    else float(document["block_top"]))
            self.order = {
                "status": "pending" if document.get("order_type") == "LIMIT" else "accepted",
                "reason": "",
                "side": side,
                "lots": lots,
                "entry": entry,
                "stop": stop,
                "risk_money": round(height * 100_000.0 * lots, 2),
                "risk_pct": risk_pct,
                "rung": int(document.get("rung", 0)),
                "ticket": int(document.get("ticket") or 555),
                # --- the tag rides back so the dashboard can name the note
                "journal_tag": document.get("journal_tag", ""),
            }

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
            "order": dict(self.order),
            # --- working orders. Empty by default: the fake EA has no broker,
            # --- and a fabricated pending row would make a test assert on a
            # --- ticket that cannot be cancelled.
            "pending": [dict(row) for row in self.pending],
            # --- the EA's own watch queue, distinct from pending: these have
            # --- no broker ticket because nothing has been submitted.
            "watch": [dict(row) for row in self.watches],
            # --- last tick, as SymbolInfoDouble would report it. Published so
            # --- the dashboard can show a watch's distance-to-target instead
            # --- of making the operator subtract two numbers by hand.
            "bid": 1.25980,
            "ask": 1.26020,
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


__all__ = ["ALLOWED_VERBS", "CMD_MAX_AGE", "SCHEMA", "TICKET_VERBS",
           "TRAIL_MODE_NAMES", "WATCH_VERBS", "FakeEA"]

