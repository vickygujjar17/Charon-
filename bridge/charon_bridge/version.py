"""Version constants for the bridge.

``BRIDGE_VERSION`` is rewritten by ``_tools/bump_version.py`` alongside
``CHARON_VERSION``, so the two can never disagree in a release.
"""

from __future__ import annotations

# --- keep in lockstep with #define CHARON_VERSION in CharonDefines.mqh
BRIDGE_VERSION = "1.0.4"

# --- revision of the JSON channel between the EA and this package.
# --- Must equal CHARON_BRIDGE_SCHEMA in CharonBridge.mqh; the EA refuses
# --- any command carrying a different value.
SCHEMA_VERSION = 1

__all__ = ["BRIDGE_VERSION", "SCHEMA_VERSION"]
