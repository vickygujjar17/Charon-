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

import math
from dataclasses import dataclass, field
from typing import Any

from .version import SCHEMA_VERSION, SUPPORTED_SNAPSHOT_SCHEMAS


class SchemaError(ValueError):
    """The snapshot is not something we are willing to render."""


# --- verbs the bridge will ever emit. This tuple is the single source of
# --- truth on the Python side; the EA independently whitelists the same
# --- set in CCharonBridge::VerbAllowed(). Neither side can widen the set
# --- alone, which is the point.
# ---
# --- v1.1.0 adds attended manual execution. The naming is load-bearing: the
# --- raw verbs ``open``/``close``/``modify`` stay forbidden, because what the
# --- bridge now emits is not "open a position at this price" -- it is "submit
# --- this block-bounded ticket to the EA's own risk adjudication". The EA
# --- still derives the stop from the block, solves the volume through
# --- ``CCharonRisk::LotForStop`` and renders every R rung. Python carries the
# --- operator's intent and nothing else; it never decides a price, a volume
# --- or an R multiple.
ALLOWED_VERBS: tuple[str, ...] = (
    "arm", "disarm", "trail_mode", "ping",
    "order_open", "order_modify", "order_cancel",
    # --- v1.5.0. A watch is not an order: it asks the EA to hold a validated
    # --- ticket and submit it itself when the market reaches a price. The
    # --- bridge still decides no price and no volume, so these belong in the
    # --- same allowlist as the order verbs rather than beside ``open``.
    "watch_add", "watch_cancel",
)

# --- verbs that are deliberately absent, documented so the gap is a
# --- decision rather than an oversight. These remain forbidden after v1.1.0
# --- precisely because each one would hand the caller a decision the EA owns:
# --- ``open``/``size``/``buy``/``sell`` imply caller-chosen volume or price,
# --- and ``close``/``modify``/``cancel`` bypass the block-geometry contract.
# --- ``watch_fire`` is absent for the mirror reason: a fired watch is the
# --- EA's own conclusion from its own tick, never something the bridge
# --- reaches in and declares.
FORBIDDEN_VERBS: tuple[str, ...] = (
    "open", "close", "modify", "size", "buy", "sell", "cancel", "pyramid",
    "watch", "watch_fire",
)

# --- the attended-execution subset. Every one of these requires an explicit
# --- order specification; none of them is reachable with a bare verb.
ORDER_VERBS: tuple[str, ...] = ("order_open", "order_modify", "order_cancel")

# --- the watch subset. ``watch_add`` carries the same full ticket an
# --- ``order_open`` does; ``watch_cancel`` needs only the watch id it was
# --- given, because there is no broker-side object to name.
WATCH_VERBS: tuple[str, ...] = ("watch_add", "watch_cancel")

SIDES: tuple[str, ...] = ("BUY", "SELL")
ORDER_TYPES: tuple[str, ...] = ("MARKET", "LIMIT")

# --- the ceiling the EA re-checks against its own money model. This is a
# --- sanity bound on the *input*, not the risk gate: ``MaxAllowedRiskMoney``
# --- and ``RiskBudgetMoney`` remain the authority at execution time.
MAX_RISK_PCT: float = 100.0

# --- RR rungs the EA is willing to index. The ladder publishes three
# --- levels (InpRrLadderA/B/C), so 0 means "no rung" and 1..3 index them.
# --- Must match CHARON_BRIDGE_MAX_RUNG in CharonBridge.mqh.
MAX_RUNG: int = 3

# --- pyramid stages a ticket may carry. Tier 1 is the primary leg, so three
# --- further stages reach CHARON_MAX_TIERS (4) exactly.
# --- Must match CHARON_BRIDGE_MAX_STAGES in CharonBridge.mqh.
MAX_STAGES: int = 3

# --- armed watches the EA will hold at once. A queue longer than this is an
# --- operator who has forgotten it exists, so the EA refuses rather than
# --- growing the snapshot and the queue file without bound.
# --- Must match CHARON_BRIDGE_MAX_WATCHES in CharonBridge.mqh.
MAX_WATCHES: int = 8

# --- how a pyramid stage decides it may fire.
# --- ``rr``    -- the basket must have reached ``trigger`` R multiples;
# --- ``price`` -- the market must have traded through ``price``.
TRIGGER_KINDS: tuple[str, ...] = ("rr", "price")

# --- the journal is human text, so it is bounded rather than rejected: the
# --- EA stores a short ASCII tag beside the ticket and the bridge keeps the
# --- full note. Byte-for-byte equality is not promised across the wire --
# --- see ``journal_tag``.
MAX_JOURNAL_LEN: int = 500
MAX_JOURNAL_TAG_LEN: int = 24

# --- hard bound on a ticket's basket-wide risk ceiling. 0 means "do not
# --- attach a ceiling"; anything above this is refused as a typo rather than
# --- quietly clamped, because a ceiling is a safety input.
MAX_BASKET_CEILING_PCT: float = 100.0

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
class Order:
    """The EA's verdict on the last order ticket, rendered as published.

    ``status`` is authoritative: ``idle`` (nothing pending), ``accepted``,
    ``rejected``, ``pending`` or -- from v1.5.0 -- ``watching``, which means a
    validated ticket is armed and nothing has been submitted. The numeric
    fields are the EA's own solved values -- volume from ``LotForStop``, stop
    from the block geometry -- so a rejected ticket still shows *why* with the
    numbers it refused.

    ``None`` means "the EA did not report this", never zero. A zero lot on a
    rejected ticket would read as a real size.
    """

    status: str
    reason: str | None
    side: str | None
    lots: float | None
    entry: float | None
    stop: float | None
    risk_money: float | None
    risk_pct: float | None
    rung: int | None
    ticket: int | None
    journal_tag: str | None = None

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Order":
        if not isinstance(raw, dict):
            return cls(status="idle", reason=None, side=None, lots=None, entry=None,
                       stop=None, risk_money=None, risk_pct=None, rung=None,
                       ticket=None, journal_tag=None)
        status = _text(raw, "status")
        side = _text(raw, "side")
        rung = raw.get("rung")
        ticket = raw.get("ticket")
        return cls(
            status=status if status in ("idle", "accepted", "rejected", "pending",
                                       "watching") else "idle",
            reason=_text(raw, "reason"),
            side=side if side in ("BUY", "SELL") else None,
            lots=_number(raw, "lots"),
            entry=_number(raw, "entry"),
            stop=_number(raw, "stop"),
            risk_money=_number(raw, "risk_money"),
            risk_pct=_number(raw, "risk_pct"),
            rung=rung if isinstance(rung, int) else None,
            ticket=ticket if isinstance(ticket, int) and ticket > 0 else None,
            journal_tag=_text(raw, "journal_tag"),
        )


@dataclass(frozen=True)
class Pending:
    """One working pending order as the EA publishes it.

    Pending orders are invisible to the position list -- they are orders, not
    positions -- so without this a LIMIT ticket could only be cancelled by
    remembering its number. ``order`` reports the last verdict; this reports
    what is still working.

    ``None`` means "the EA did not report it", never zero, for the same reason
    it does everywhere else in this module.
    """

    ticket: int
    side: str
    order_type: str | None
    price: float | None
    stop: float | None
    lot: float | None
    placed_at: int | None

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Pending":
        ticket = raw.get("ticket")
        if not isinstance(ticket, int) or ticket <= 0:
            raise SchemaError(f"pending order has a non-positive ticket: {ticket!r}")
        side = _text(raw, "side")
        if side not in SIDES:
            raise SchemaError(f"pending order {ticket} has no usable side")
        placed = raw.get("placed_at")
        return cls(
            ticket=ticket,
            side=side,
            order_type=_text(raw, "order_type"),
            price=_number(raw, "price"),
            stop=_number(raw, "sl"),
            lot=_number(raw, "lot"),
            placed_at=placed if isinstance(placed, int) else None,
        )


def _pendings(raw: Any) -> tuple[Pending, ...]:
    """Parse a ``pending`` array, skipping anything unusable.

    One malformed entry must not hide the rest: a pending order the operator
    cannot see is one they cannot cancel, so partial knowledge still beats
    none. An entry whose *ticket* is unusable is dropped, because a row with
    no ticket cannot be acted on at all.
    """
    if not isinstance(raw, list):
        return ()
    parsed: list[Pending] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            parsed.append(Pending.from_json(item))
        except SchemaError:
            continue
    return tuple(parsed)


@dataclass(frozen=True)
class Watch:
    """One entry the EA is holding, waiting for the market to reach a price.

    A watch is *not* a pending order. Nothing exists at the broker until the
    EA decides the price was touched, so it has no broker ticket, cannot be
    cancelled through ``order_cancel``, and does not appear in
    :class:`Pending`. It carries the id the EA minted so the operator has
    something to name when cancelling.

    A watch is also the one row on this dashboard that can be lost without a
    trace -- a chart reload drops in-memory state -- which is why the EA
    persists the queue and why the snapshot reports it at all rather than
    leaving the UI to infer intent from a missing position.
    """

    watch_id: int
    side: str
    price: float | None
    """The price the operator asked for. ``None`` if the EA did not publish
    one -- never a substitute, because a watch with an invented target would
    look armed against a price nobody chose."""

    order_type: str | None
    created_at: int | None
    status: str | None
    """The EA's own word for what the watch is doing (``armed``, ``fired``,
    ``expired``). Passed through rather than re-derived, for the same reason
    the RR ladder is: two calculations of one state can disagree."""

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Watch":
        # --- accept either key: the EA writes ``id`` but a hand-edited or
        # --- replayed snapshot may say ``watch_id``. Same value either way.
        watch_id = raw.get("watch_id", raw.get("id"))
        if isinstance(watch_id, bool) or not isinstance(watch_id, int) or watch_id <= 0:
            raise SchemaError(f"watch has a non-positive id: {watch_id!r}")
        side = _text(raw, "side")
        if side not in SIDES:
            raise SchemaError(f"watch {watch_id} has no usable side")
        created = raw.get("created_at")
        # --- ``price`` is what the EA writes; ``target`` is accepted because a
        # --- watcher's price is naturally called a target on the way in and
        # --- the two must not disagree. ``price`` wins when both are present.
        price = _number(raw, "price")
        if price is None:
            price = _number(raw, "target")
        return cls(
            watch_id=watch_id,
            side=side,
            price=price,
            order_type=_text(raw, "order_type"),
            created_at=created if isinstance(created, int) else None,
            status=_text(raw, "status"),
        )


def _watches(raw: Any) -> tuple[Watch, ...]:
    """Parse a ``watch`` array, skipping anything unusable.

    Same rule as :func:`_pendings`: a row without a usable id cannot be
    cancelled, so it is dropped; every other kind of damage is survivable and
    must not hide the rows beside it.
    """
    if not isinstance(raw, list):
        return ()
    parsed: list[Watch] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            parsed.append(Watch.from_json(item))
        except SchemaError:
            continue
    return tuple(parsed)


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
    order: Order = field(default_factory=lambda: Order.from_json(None))
    pending: tuple[Pending, ...] = field(default_factory=tuple)
    watch: tuple[Watch, ...] = field(default_factory=tuple)
    bid: float | None = None
    ask: float | None = None

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Snapshot":
        if not isinstance(raw, dict):
            raise SchemaError("snapshot is not a JSON object")

        schema = raw.get("schema")
        # --- tolerant on the way in, strict on the way out. A v1.4.0 bridge
        # --- must still render a schema-2 EA (every new field is additive),
        # --- but it must never *command* one -- ``build_command`` stamps
        # --- SCHEMA_VERSION and the EA refuses a mismatch, which is the
        # --- guard that stops a half-upgraded install from trading on a
        # --- ticket whose constraints the old EA would silently drop.
        if schema not in SUPPORTED_SNAPSHOT_SCHEMAS:
            raise SchemaError(
                f"snapshot schema {schema!r} is not one this bridge renders "
                f"(it understands {SUPPORTED_SNAPSHOT_SCHEMAS})"
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
            # --- the revision actually read, not ours. The pre-flight in
            # --- main.py compares this against SCHEMA_VERSION to refuse a
            # --- ticket the EA cannot fully understand; recording our own
            # --- number here would make that check vacuous.
            schema=schema,
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
            order=Order.from_json(raw.get("order")),
            pending=_pendings(raw.get("pending")),
            watch=_watches(raw.get("watch")),
            bid=_number(raw, "bid"),
            ask=_number(raw, "ask"),
        )


@dataclass(frozen=True)
class OrderIntent:
    """What the operator asked for, validated and nothing more.

    Every field here is an *input* to the EA's risk adjudication. The
    snapshot that comes back carries the EA's verdict -- the solved volume,
    the derived stop, the rendered rungs -- and the dashboard renders that
    instead of computing a parallel answer.

    ``block_top``/``block_bottom`` are the structural boundaries the stop is
    derived from. They are prices the operator read off the chart, which is
    why they are accepted here: they are observations, not calculations.
    """

    side: str
    order_type: str
    entry: float | None
    block_top: float
    block_bottom: float
    risk_pct: float | None
    trail_trigger: int
    rung: int
    # --- v1.4.0 additions. Every one has a meaningful "absent" form, so a
    # --- ticket written by an older bridge still validates and still means
    # --- exactly what it used to mean.
    risk_money: float | None = None
    basket_risk_ceiling_pct: float | None = None
    trail_start_rr: float | None = None
    trail_start_points: float | None = None
    # --- v1.5.0: trail activation may be stated as the absolute price the
    # --- market must reach, which is what an operator actually has when they
    # --- are reading a level off the chart. Mutually exclusive with the other
    # --- two: three ways to say "start trailing" would leave the EA
    # --- arbitrating between contradictory instructions.
    trail_start_price: float | None = None
    stages: tuple[Stage, ...] = ()
    journal: str | None = None

    @property
    def risk_mode(self) -> str:
        """Which risk figure the ticket actually stated: ``pct`` or ``money``."""
        return "money" if self.risk_money is not None else "pct"

    @property
    def is_watch(self) -> bool:
        """Whether this ticket should be armed, not submitted.

        ``MARKET`` with no ``entry`` means "fill me now" and goes straight to
        ``order_open``. ``MARKET`` *with* an ``entry`` is the operator naming a
        price they want without claiming it is a resting order -- before
        v1.5.0 that combination was silently filled at market, which is the
        trap this property exists to close.
        """
        return self.order_type == "MARKET" and self.entry is not None

    def to_doc(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "side": self.side,
            "order_type": self.order_type,
            "block_top": self.block_top,
            "block_bottom": self.block_bottom,
            "trail_trigger": self.trail_trigger,
            "rung": self.rung,
        }
        if self.entry is not None:
            doc["entry"] = self.entry
        # --- exactly one of these is ever present; ``build_order_intent``
        # --- enforces the exclusivity, so the EA never has to arbitrate
        # --- between two contradictory budgets.
        if self.risk_pct is not None:
            doc["risk_pct"] = self.risk_pct
        if self.risk_money is not None:
            doc["risk_money"] = self.risk_money
        if self.basket_risk_ceiling_pct is not None:
            doc["basket_risk_ceiling_pct"] = self.basket_risk_ceiling_pct
        if self.trail_start_rr is not None:
            doc["trail_start_rr"] = self.trail_start_rr
        if self.trail_start_points is not None:
            doc["trail_start_points"] = self.trail_start_points
        if self.trail_start_price is not None:
            doc["trail_start_price"] = self.trail_start_price
        if self.stages:
            doc["stages"] = [stage.to_doc() for stage in self.stages]
        if self.journal:
            doc["journal"] = self.journal
            # --- the short form the EA puts in the broker comment. Sent as
            # --- its own key so the EA never has to transliterate anything:
            # --- Python guarantees printable ASCII here, and the full note
            # --- stays on this side of the wire.
            doc["journal_tag"] = journal_tag(self.journal)
        return doc


@dataclass(frozen=True)
class Stage:
    """One pyramid stage, as the operator stated it.

    ``trigger`` is interpreted according to ``kind``:

    * ``rr``    -- a multiple of the primary leg's 1R;
    * ``price`` -- an absolute price the market must trade through.

    ``lots_mult`` multiplies the *primary leg's solved volume*. It is not a
    volume: the EA still owns the arithmetic, and a multiplier cannot express
    an absolute size. A stage is therefore never a way to smuggle a lot value
    past the risk model.
    """

    kind: str
    trigger: float
    lots_mult: float

    def to_doc(self) -> dict[str, Any]:
        return {"kind": self.kind, "trigger": self.trigger, "lots_mult": self.lots_mult}


def journal_tag(text: str | None) -> str:
    """The short ASCII tag that rides in the broker comment.

    The full note lives in the journal sidecar, keyed by ticket, because a
    broker comment is size-limited and CPython's JSON writer is not the only
    party that has to read it: ``CCharonBridge::ReadJsonString`` decodes
    ``\\uXXXX`` escapes only partially (pre-1.4.0 it produced ``u00e9`` for
    ``\\u00e9``). Rather than depend on that being fixed, the wire form stays
    inside printable ASCII.

    What survives is deliberately lossy: alphanumerics and a few separators,
    upper-cased, truncated. That is all a comment can honestly carry -- the
    note itself is never reconstructed from it.
    """
    if not text:
        return ""
    cleaned = "".join(
        char if (char.isascii() and (char.isalnum() or char in "-_.+")) else "-"
        for char in text.strip()
    )
    # --- collapse the runs the substitution above can create
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")[:MAX_JOURNAL_TAG_LEN].upper()



def _finite(raw: Any, what: str) -> float:
    """A positive, finite price -- anything else is a schema error.

    ``NaN`` and ``inf`` are rejected explicitly: they survive JSON round
    trips through many producers, compare false against every bound, and
    would sail through a naive ``> 0`` test on the EA side.
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise SchemaError(f"{what} must be a number, got {raw!r}")
    value = float(raw)
    if not math.isfinite(value):
        raise SchemaError(f"{what} must be finite, got {raw!r}")
    if value <= 0.0:
        raise SchemaError(f"{what} must be positive, got {value!r}")
    return value



def _positive(raw: Any, what: str) -> float:
    """A positive, finite number -- ``NaN``/``inf``/bools refused."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise SchemaError(f"{what} must be a number, got {raw!r}")
    value = float(raw)
    if not math.isfinite(value) or value <= 0.0:
        raise SchemaError(f"{what} must be a positive finite number, got {raw!r}")
    return value


def _stages(raw: Any, side: str, entry: float | None) -> tuple[Stage, ...]:
    """Validate the pyramid ladder a ticket carries.

    The ordering rule is directional, not cosmetic: the EA only ever adds
    exposure once the basket is *winning* (``Evaluate`` refuses a tier below
    its R trigger), so a stage placed on the losing side of the entry could
    never fire and would silently turn a three-stage plan into a one-stage
    one. Refusing it here says so out loud.
    """
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise SchemaError(f"stages must be a list, got {type(raw).__name__}")
    if len(raw) > MAX_STAGES:
        raise SchemaError(f"a ticket may carry at most {MAX_STAGES} pyramid stages")

    built: list[Stage] = []
    for position, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise SchemaError(f"stage {position} is not an object")
        kind = item.get("kind", "rr")
        if kind not in TRIGGER_KINDS:
            raise SchemaError(
                f"stage {position} kind must be one of {TRIGGER_KINDS}, got {kind!r}"
            )
        trigger = _positive(item.get("trigger"), f"stage {position} trigger")
        lots_mult = _positive(item.get("lots_mult", 1.0), f"stage {position} lots_mult")
        built.append(Stage(kind=kind, trigger=trigger, lots_mult=lots_mult))

    # --- monotonic within each kind. Mixing kinds is allowed; comparing an R
    # --- multiple against a price would be meaningless, so only like pairs
    # --- are ordered against each other.
    for kind in TRIGGER_KINDS:
        ordered = [stage.trigger for stage in built if stage.kind == kind]
        ascending = side == "BUY"
        for earlier, later in zip(ordered, ordered[1:]):
            ahead = later > earlier if ascending else later < earlier
            if not ahead:
                raise SchemaError(
                    f"stages of kind {kind!r} must advance in the {side} direction "
                    f"(stage {earlier!r} then {later!r})"
                )

    # --- and on the profit side of a known entry, for the price kind only:
    # --- an R trigger is already measured from the entry.
    if entry is not None:
        for position, stage in enumerate(built, start=1):
            if stage.kind != "price":
                continue
            ahead = stage.trigger > entry if side == "BUY" else stage.trigger < entry
            if not ahead:
                raise SchemaError(
                    f"stage {position} triggers at {stage.trigger!r}, which is not "
                    f"beyond the entry {entry!r} for a {side}"
                )
    return tuple(built)


def build_order_intent(
    *,
    side: Any,
    order_type: Any,
    entry: Any,
    block_top: Any,
    block_bottom: Any,
    risk_pct: Any = None,
    trail_trigger: Any = 0,
    rung: Any = 0,
    risk_money: Any = None,
    basket_risk_ceiling_pct: Any = None,
    trail_start_rr: Any = None,
    trail_start_points: Any = None,
    trail_start_price: Any = None,
    stages: Any = None,
    journal: Any = None,
) -> OrderIntent:
    """Validate an order ticket before it is allowed near the inbox.

    This is a shape check, not a risk check. The EA is still the only party
    that decides whether a trade happens; refusing malformed intent here
    just keeps obvious garbage out of the command folder.

    Risk is stated **once**, as either a percentage or an amount of money.
    Both would be two answers to one question, and the EA would have to pick
    a winner -- so the ticket is refused instead of silently preferring one.
    """
    if side not in SIDES:
        raise SchemaError(f"side must be one of {SIDES}, got {side!r}")
    if order_type not in ORDER_TYPES:
        raise SchemaError(f"order_type must be one of {ORDER_TYPES}, got {order_type!r}")

    top = _finite(block_top, "block_top")
    bottom = _finite(block_bottom, "block_bottom")
    if top == bottom:
        raise SchemaError("block_top and block_bottom must differ; the block has no height")

    # --- the block defines which way the structure runs, and the direction
    # --- has to agree with it: a BUY against an inverted block would put the
    # --- stop on the wrong side of the structure it is supposed to respect.
    if side == "BUY" and top <= bottom:
        raise SchemaError("a BUY block needs block_top above block_bottom")
    if side == "SELL" and bottom <= top:
        raise SchemaError("a SELL block needs block_bottom above block_top")

    entry_price: float | None = None
    if entry is not None:
        entry_price = _finite(entry, "entry")
        low, high = (bottom, top) if top > bottom else (top, bottom)
        if order_type == "LIMIT" and not (low < entry_price < high):
            raise SchemaError("a LIMIT entry must sit strictly inside the block")

    # --- exactly one risk statement. ``risk_pct`` keeps its v1.1.0 bound; a
    # --- money figure can only be sanity-checked here, because the account
    # --- is the EA's to know -- it clamps against MaxAllowedRiskMoney().
    if risk_pct is None and risk_money is None:
        raise SchemaError("a ticket needs either risk_pct or risk_money")
    if risk_pct is not None and risk_money is not None:
        raise SchemaError("state risk as risk_pct or risk_money, not both")

    risk: float | None = None
    money: float | None = None
    if risk_pct is not None:
        if isinstance(risk_pct, bool) or not isinstance(risk_pct, (int, float)):
            raise SchemaError(f"risk_pct must be a number, got {risk_pct!r}")
        risk = float(risk_pct)
        if not math.isfinite(risk) or not (0.0 < risk <= MAX_RISK_PCT):
            raise SchemaError(f"risk_pct must be in (0, {MAX_RISK_PCT}], got {risk_pct!r}")
    else:
        money = _positive(risk_money, "risk_money")

    # --- the basket ceiling is a whole-basket gate, enforced by the EA's
    # --- ChPyramidRiskAccepts before any stage fires. It is not a second
    # --- sizing input, so a ceiling below the primary leg's own risk is a
    # --- contradiction and is refused rather than clamped.
    ceiling: float | None = None
    if basket_risk_ceiling_pct is not None:
        ceiling = _positive(basket_risk_ceiling_pct, "basket_risk_ceiling_pct")
        if ceiling > MAX_BASKET_CEILING_PCT:
            raise SchemaError(
                f"basket_risk_ceiling_pct must be at most {MAX_BASKET_CEILING_PCT}, "
                f"got {basket_risk_ceiling_pct!r}"
            )
        if risk is not None and risk > ceiling:
            raise SchemaError(
                f"risk_pct {risk!r} already exceeds the basket ceiling {ceiling!r}"
            )

    start_rr: float | None = None
    if trail_start_rr is not None:
        start_rr = _positive(trail_start_rr, "trail_start_rr")

    start_points: float | None = None
    if trail_start_points is not None:
        start_points = _positive(trail_start_points, "trail_start_points")

    # --- v1.5.0: at most one activation trigger. RR and points are both
    # --- *distances* from the entry, so the EA could in principle take the
    # --- tighter of the two -- but an absolute price is a different kind of
    # --- statement, and a ticket carrying two of these is a ticket whose
    # --- author had not decided. Refused rather than resolved.
    start_price: float | None = None
    if trail_start_price is not None:
        start_price = _positive(trail_start_price, "trail_start_price")
    stated_triggers = sum(
        value is not None for value in (start_rr, start_points, start_price)
    )
    if stated_triggers > 1:
        raise SchemaError(
            "state trail activation once: trail_start_rr, trail_start_points "
            "or trail_start_price -- not more than one"
        )
    # --- an absolute activation price has to sit on the profitable side of the
    # --- entry, or trailing would begin in the wrong direction from the first
    # --- tick. Checkable here only when an entry was given; otherwise the EA
    # --- checks it against the fill, which is the only price it trusts.
    if start_price is not None and entry_price is not None:
        if side == "BUY" and start_price <= entry_price:
            raise SchemaError("a BUY trail_start_price must sit above the entry")
        if side == "SELL" and start_price >= entry_price:
            raise SchemaError("a SELL trail_start_price must sit below the entry")

    if isinstance(trail_trigger, bool) or trail_trigger not in TRAIL_MODE_NAMES:
        raise SchemaError(
            f"trail_trigger must be one of {sorted(TRAIL_MODE_NAMES)}, got {trail_trigger!r}"
        )

    if isinstance(rung, bool) or not isinstance(rung, int):
        raise SchemaError(f"rung must be an integer, got {rung!r}")
    if not (0 <= rung <= MAX_RUNG):
        raise SchemaError(f"rung must be in [0, {MAX_RUNG}], got {rung!r}")

    note: str | None = None
    if journal is not None:
        if not isinstance(journal, str):
            raise SchemaError(f"journal must be text, got {type(journal).__name__}")
        # --- whitespace-only is "no note", not an error: an empty textarea
        # --- is how the operator says nothing.
        note = journal.strip() or None
        if note is not None and len(note) > MAX_JOURNAL_LEN:
            raise SchemaError(
                f"journal is {len(note)} characters; the limit is {MAX_JOURNAL_LEN}"
            )

    return OrderIntent(
        side=side,
        order_type=order_type,
        entry=entry_price,
        block_top=top,
        block_bottom=bottom,
        risk_pct=risk,
        trail_trigger=int(trail_trigger),
        rung=rung,
        risk_money=money,
        basket_risk_ceiling_pct=ceiling,
        trail_start_rr=start_rr,
        trail_start_points=start_points,
        trail_start_price=start_price,
        stages=_stages(stages, side, entry_price),
        journal=note,
    )




def build_command(verb: str, issued: int, *, symbol: str, command_id: str,
                  mode: int | None = None,
                  order: "OrderIntent | None" = None,
                  ticket: int | None = None,
                  watch_id: int | None = None) -> dict[str, Any]:
    """Construct a command document for the EA inbox.

    Refuses any verb outside :data:`ALLOWED_VERBS`, so a bug elsewhere in
    the process cannot turn into a file the EA might act on. Order verbs
    additionally refuse to be built without a validated
    :class:`OrderIntent` (or, for a cancel, an explicit ticket).

    ``watch_add`` carries the *same* validated intent ``order_open`` does --
    the EA re-checks it identically, because a watch is only a deferral of
    when the ticket is submitted, never a relaxation of what it must satisfy.
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

    if verb in ("order_open", "order_modify", "watch_add"):
        if order is None:
            raise SchemaError(f"{verb} needs a validated order intent")
        doc.update(order.to_doc())
        # --- ``watch_add`` must name the price it is waiting for. Without one
        # --- the EA would have nothing to compare against the tick and would
        # --- have to either fire immediately or wait forever; both are wrong
        # --- answers to a malformed ticket, so it is refused here instead.
        if verb == "watch_add":
            if order.order_type != "MARKET":
                raise SchemaError("watch_add only defers a MARKET entry")
            if order.entry is None:
                raise SchemaError("watch_add needs an entry price to watch for")

    if verb in ("order_modify", "order_cancel"):
        if ticket is None or ticket <= 0:
            raise SchemaError(f"{verb} needs a positive ticket, got {ticket!r}")
        doc["ticket"] = int(ticket)

    if verb == "watch_cancel":
        if watch_id is None or watch_id <= 0:
            raise SchemaError(f"watch_cancel needs a positive watch_id, got {watch_id!r}")
        doc["watch_id"] = int(watch_id)

    return doc


__all__ = [
    "ALLOWED_VERBS",
    "FORBIDDEN_VERBS",
    "MAX_BASKET_CEILING_PCT",
    "MAX_JOURNAL_LEN",
    "MAX_JOURNAL_TAG_LEN",
    "MAX_RISK_PCT",
    "MAX_RUNG",
    "MAX_STAGES",
    "MAX_WATCHES",
    "ORDER_TYPES",
    "ORDER_VERBS",
    "SIDES",
    "TRAIL_MODE_NAMES",
    "TRIGGER_KINDS",
    "WATCH_VERBS",
    "Basket",
    "Leg",
    "Model",
    "Order",
    "OrderIntent",
    "Pending",
    "Pyramid",
    "SchemaError",
    "Snapshot",
    "Stage",
    "Trail",
    "Watch",
    "build_command",
    "build_order_intent",
    "journal_tag",
]

