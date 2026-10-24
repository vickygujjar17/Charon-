"""Charon bridge -- a read-mostly window onto a running Charon EA.

The EA is and remains the only thing that talks to the broker. This
package reads the snapshot the EA publishes, exposes it to a browser,
and writes back a *very* small set of state-changing commands that the
trader could equally press on the chart.

See ``charon_bridge.schema`` for the channel contract and
``main.py`` for the HTTP surface.
"""

from .version import BRIDGE_VERSION, SCHEMA_VERSION

__all__ = ["BRIDGE_VERSION", "SCHEMA_VERSION"]
