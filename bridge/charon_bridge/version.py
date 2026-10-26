"""Version constants for the bridge.

``BRIDGE_VERSION`` is rewritten by ``_tools/bump_version.py`` alongside
``CHARON_VERSION``, so the two can never disagree in a release.
"""

from __future__ import annotations

# --- keep in lockstep with #define CHARON_VERSION in CharonDefines.mqh
BRIDGE_VERSION = "1.7.1"

# --- revision of the JSON channel between the EA and this package.
# --- Must equal CHARON_BRIDGE_SCHEMA in CharonBridge.mqh; the EA refuses
# --- any command carrying a different value.
# ---
# --- 2 = v1.1.0 attended execution: order verbs carry side / block geometry /
# --- risk_pct / trail_trigger / rung, and the snapshot gains an ``order``
# --- verdict block. A schema-1 EA would parse an ``order_open`` command as
# --- having no block at all, so the revision is what stops a half-upgraded
# --- install from trading on a partially understood ticket.
# ---
# --- 3 = v1.4.0 institutional ticket: risk may be stated in money instead of
# --- percent, a basket-wide risk ceiling may be attached, trail activation is
# --- per ticket, up to three pyramid stages carry their own trigger kind
# --- (RR or price) and multiplier, and a journal tag rides along.
# ---
# --- 4 = v1.5.0 watched entries: a MARKET ticket that names an ``entry`` no
# --- longer fills at market -- it arms a watch and waits for the market to
# --- trade through that price. Trail activation may additionally be expressed
# --- as an absolute price, and the snapshot publishes the EA's watch queue
# --- beside its pending orders.
# ---
# --- The bump is not cosmetic. ``ReadCommandFile`` ignores keys it does not
# --- know, so a schema-2 EA handed a schema-3 ticket would accept it, open a
# --- position, and silently drop every one of those constraints -- a trade
# --- that *looks* governed and is not. Refusing the version is the only
# --- honest answer, and ``main.py`` pre-flights it so the operator sees a
# --- readable message instead of a silently inert command.
# ---
# --- The same trap is why 4 exists. A schema-3 EA handed ``watch_add`` would
# --- not know the verb at all; handed a ``trail_start_price`` it would ignore
# --- the key and trail from its own default instead. Either way the operator
# --- would have been told a condition was armed when it was not.
SCHEMA_VERSION = 4

# --- Snapshot revisions this bridge can still render. v1.5.0 reads a schema-2
# --- or schema-3 EA's snapshot happily: every addition is additive
# --- (``watch`` defaults to empty, new leg fields default to None), so nothing
# --- is invented and nothing is lost. Commands stay strict -- see above.
SUPPORTED_SNAPSHOT_SCHEMAS: tuple[int, ...] = (2, 3, 4)

__all__ = ["BRIDGE_VERSION", "SCHEMA_VERSION", "SUPPORTED_SNAPSHOT_SCHEMAS"]
