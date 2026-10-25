"""Version constants for the bridge.

``BRIDGE_VERSION`` is rewritten by ``_tools/bump_version.py`` alongside
``CHARON_VERSION``, so the two can never disagree in a release.
"""

from __future__ import annotations

# --- keep in lockstep with #define CHARON_VERSION in CharonDefines.mqh
BRIDGE_VERSION = "1.4.0"

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
# --- The bump is not cosmetic. ``ReadCommandFile`` ignores keys it does not
# --- know, so a schema-2 EA handed a schema-3 ticket would accept it, open a
# --- position, and silently drop every one of those constraints -- a trade
# --- that *looks* governed and is not. Refusing the version is the only
# --- honest answer, and ``main.py`` pre-flights it so the operator sees a
# --- readable message instead of a silently inert command.
SCHEMA_VERSION = 3

# --- Snapshot revisions this bridge can still render. v1.4.0 reads a schema-2
# --- EA's snapshot happily: every schema-3 addition is additive (``pending``
# --- defaults to empty, new leg fields default to None), so nothing is
# --- invented and nothing is lost. Commands stay strict -- see above.
SUPPORTED_SNAPSHOT_SCHEMAS: tuple[int, ...] = (2, 3)

__all__ = ["BRIDGE_VERSION", "SCHEMA_VERSION", "SUPPORTED_SNAPSHOT_SCHEMAS"]
