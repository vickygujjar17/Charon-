"""The contract between the EA and this package.

Two invariants are enforced here rather than left to the UI:

**1. The ladder is opaque text.**
The EA renders the RR ladder with ``RrRaderText()`` and Python copies
the string through untouched. Python never multiplies an R multiple by a
stop distance, so a rung on the dashboard can never disagree with the
rung on the chart.

**2. A missing field is ``None``, not a zero.**
If the EA does not publish ``ladder`` we render "not reported". Inventing
a price from partial data would be worse than showing nothing, especially
in a module whose whole job is protecting open risk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .version import SCHEMA_VERSION


class SchemaError(ValueError):
    """The snapshot is not something we are willing to render."""


# --- verbs the bridge will ever emit. This tuple is the single source of
# --- truth on the Python side; the EA independently whitelists the same
# --- four in CCharonBridge::VerbAllowed(). Neither side can widen the set
# --- alone, which is the point.
ALLOWED_VERBS: tuple[str, ...] = ("arm", "disarm", "trail_mode", "ping")

# --- verbs that are deliberately absent, documented so the gap is a
# --- decision rather than an oversight.
FORBIDDEN_VERBS: tuple[str, ...] = (
    "open", "close", "modify", "size", "buy", "sell", "cancel", "pyramid",
)

TRAIL_MODE_NAMES: dict[int, str] = {
    0: "OFF",
    1: "POINTS",
    2: "ATR",
    3: "HWM_PERCENT",
    4: "RR_LOCK",
}


def _number(source: dict[str, Any], key: str) -> float | None:
    """Read a float, treating absent/null/garbage as ``None``."""
    raw = source.get(key)
    if raw is None:
        return None
    if isinstance(raw, bool):  # bool is an int subclass; never a price
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    return None


def _flag(source: dict[str, Any], key: str) -> bool:
    raw = source.get(key)
    return bool(raw) if isinstance(raw, bool) else False


def _text(source: dict[str, Any], key: str) -> str | None:
    raw = source.get(key)
    return raw if isinstance(raw, str) else None


@dataclass(frozen=True)
class Leg:
    """One position as published by the EA."""

    ticket: int
    side: str
    tier: int
    entry: float | None
    lot: float | None
    stop: float | None
    risk_price: float | None
    hwm: float | None
    progress_rr: float | None
    trail_active: bool
    be_done: bool
    half_risk_done: bool
    ladder: str | None = None

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Leg":
        side = _text(raw, "side")
        if side not in ("BUY", "SELL"):
            raise SchemaError(f"leg {raw.get('ticket')!r} has no usable side")
        ticket = raw.get("ticket")
        if not isinstance(ticket, int):
            raise SchemaError(f"leg has a non-integer ticket: {ticket!r}")
        tier = raw.get("tier")
        return cls(
            ticket=ticket,
            side=side,
            tier=tier if isinstance(tier, int) else 1,
            entry=_number(raw, "entry"),
            lot=_number(raw, "lot"),
            stop=_number(raw, "sl"),
            risk_price=_number(raw, "risk_price"),
            hwm=_number(raw, "hwm"),
            progress_rr=_number(raw, "progress_rr"),
            trail_active=_flag(raw, "trail_active"),
            be_done=_flag(raw, "be_done"),
            half_risk_done=_flag(raw, "half_risk_done"),
            ladder=_text(raw, "ladder"),
        )


@dataclass(frozen=True)
class Basket:
    valid: bool
    mixed: bool
    direction: str
    legs: int
    lot: float | None
    avg_entry: float | None
    open_pl: float | None
    risk_money: float | None
    risk_pct: float | None
    best: float | None
    ladder: str | None = None

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Basket":
        if not isinstance(raw, dict):
            raise SchemaError("snapshot has no basket object")
        direction_raw = raw.get("dir")
        # POSITION_TYPE_BUY is 0, so "no direction" and "buy" share a value;
        # the EA publishes `mixed` to disambiguate.
        if _flag(raw, "mixed"):
            direction = "MIXED"
        elif direction_raw == 0:
            direction = "BUY"
        elif direction_raw == 1:
            direction = "SELL"
        else:
            direction = "UNKNOWN"
        legs = raw.get("legs")
        return cls(
            valid=_flag(raw, "valid"),
            mixed=_flag(raw, "mixed"),
            direction=direction,
            legs=legs if isinstance(legs, int) else 0,
            lot=_number(raw, "lot"),
            avg_entry=_number(raw, "avg_entry"),
            open_pl=_number(raw, "open_pl"),
            risk_money=_number(raw, "risk_money"),
            risk_pct=_number(raw, "risk_pct"),
            best=_number(raw, "best"),
            ladder=_text(raw, "ladder"),
        )


@dataclass(frozen=True)
class Pyramid:
    enabled: bool
    max_legs: int
    max_risk_pct: float | None
    trigger: str
    arm_key: int
    armed: bool
    secs_left: int | None
    next_tier: int
    progress_rr: float | None

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Pyramid":
        if not isinstance(raw, dict):
            raise SchemaError("snapshot has no pyramid object")
        trigger = _text(raw, "trigger")
        secs = raw.get("secs_left")
        max_legs = raw.get("max_legs")
        arm_key = raw.get("arm_key")
        next_tier = raw.get("next_tier")
        return cls(
            enabled=_flag(raw, "enabled"),
            max_legs=max_legs if isinstance(max_legs, int) else 1,
            max_risk_pct=_number(raw, "max_risk_pct"),
            trigger=trigger if trigger in ("AUTO", "MANUAL") else "AUTO",
            arm_key=arm_key if isinstance(arm_key, int) else 0,
            armed=_flag(raw, "armed"),
            secs_left=secs if isinstance(secs, int) else None,
            next_tier=next_tier if isinstance(next_tier, int) else 2,
            progress_rr=_number(raw, "progress_rr"),
        )


@dataclass(frozen=True)
class Trail:
    mode: int
    mode_name: str
    mutable: bool

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Trail":
        if not isinstance(raw, dict):
            raise SchemaError("snapshot has no trail object")
        mode = raw.get("mode")
        if not isinstance(mode, int) or mode not in TRAIL_MODE_NAMES:
            raise SchemaError(f"unknown trail mode: {mode!r}")
        return cls(
            mode=mode,
            mode_name=TRAIL_MODE_NAMES[mode],
            mutable=_flag(raw, "mutable"),
        )


@dataclass(frozen=True)
class Model:
    sl: str | None
    tp: str | None
    sizing: str | None
    max_risk_pct: float | None

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Model":
        if not isinstance(raw, dict):
            return cls(sl=None, tp=None, sizing=None, max_risk_pct=None)
        return cls(
            sl=_text(raw, "sl"),
            tp=_text(raw, "tp"),
            sizing=_text(raw, "sizing"),
            max_risk_pct=_number(raw, "max_risk_pct"),
        )


@dataclass(frozen=True)
class Snapshot:
    """One validated EA snapshot."""

    schema: int
    seq: int
    ea_version: str | None
    symbol: str
    server_time: int
    ready: bool
    digits: int
    currency: str | None
    balance: float | None
    equity: float | None
    basket: Basket
    model: Model
    trail: Trail
    pyramid: Pyramid
    legs: tuple[Leg, ...] = field(default_factory=tuple)

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Snapshot":
        if not isinstance(raw, dict):
            raise SchemaError("snapshot is not a JSON object")

        schema = raw.get("schema")
        if schema != SCHEMA_VERSION:
            raise SchemaError(
                f"snapshot schema {schema!r} does not match bridge schema {SCHEMA_VERSION}"
            )

        symbol = _text(raw, "symbol")
        if not symbol:
            raise SchemaError("snapshot has no symbol")

        legs_raw = raw.get("legs")
        legs: tuple[Leg, ...] = ()
        if isinstance(legs_raw, list):
            legs = tuple(Leg.from_json(item) for item in legs_raw if isinstance(item, dict))

        seq = raw.get("seq")
        server_time = raw.get("server_time")
        digits = raw.get("digits")

        return cls(
            schema=SCHEMA_VERSION,
            seq=seq if isinstance(seq, int) else 0,
            ea_version=_text(raw, "ea_version"),
            symbol=symbol,
            server_time=server_time if isinstance(server_time, int) else 0,
            ready=_flag(raw, "ready"),
            digits=digits if isinstance(digits, int) else 5,
            currency=_text(raw, "currency"),
            balance=_number(raw, "balance"),
            equity=_number(raw, "equity"),
            basket=Basket.from_json(raw.get("basket")),
            model=Model.from_json(raw.get("model")),
            trail=Trail.from_json(raw.get("trail")),
            pyramid=Pyramid.from_json(raw.get("pyramid")),
            legs=legs,
        )


def build_command(verb: str, issued: int, *, symbol: str, command_id: str,
                  mode: int | None = None) -> dict[str, Any]:
    """Construct a command document for the EA inbox.

    Refuses any verb outside :data:`ALLOWED_VERBS`, so a bug elsewhere in
    the process cannot turn into a file the EA might act on.
    """
    if verb not in ALLOWED_VERBS:
        raise SchemaError(f"verb {verb!r} is not permitted by the bridge")

    doc: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "id": command_id,
        "verb": verb,
        "symbol": symbol,
        "issued": int(issued),
    }

    if verb == "trail_mode":
        if mode is None or mode not in TRAIL_MODE_NAMES:
            raise SchemaError(f"trail_mode needs a valid mode, got {mode!r}")
        doc["mode"] = int(mode)

    return doc


__all__ = [
    "ALLOWED_VERBS",
    "FORBIDDEN_VERBS",
    "TRAIL_MODE_NAMES",
    "Basket",
    "Leg",
    "Model",
    "Pyramid",
    "SchemaError",
    "Snapshot",
    "Trail",
    "build_command",
]

