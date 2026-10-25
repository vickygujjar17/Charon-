"""Version constants for the bridge.

``BRIDGE_VERSION`` is rewritten by ``_tools/bump_version.py`` alongside
``CHARON_VERSION``, so the two can never disagree in a release.
"""

from __future__ import annotations

# --- keep in lockstep with #define CHARON_VERSION in CharonDefines.mqh
BRIDGE_VERSION = "1.1.2"

# --- revision of the JSON channel between the EA and this package.
# --- Must equal CHARON_BRIDGE_SCHEMA in CharonBridge.mqh; the EA refuses
# --- any command carrying a different value.
# ---
# --- 2 = v1.1.0 attended execution: order verbs carry side / block geometry /
# --- risk_pct / trail_trigger / rung, and the snapshot gains an ``order``
# --- verdict block. A schema-1 EA would parse an ``order_open`` command as
# --- having no block at all, so the revision is what stops a half-upgraded
# --- install from trading on a partially understood ticket.
SCHEMA_VERSION = 2

__all__ = ["BRIDGE_VERSION", "SCHEMA_VERSION"]
