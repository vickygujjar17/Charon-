"""Optional read-only view through the MetaTrader5 Python package.

The EA publishes everything the dashboard *needs*. This module exists
only to answer questions the file snapshot cannot: "is a terminal even
running on this machine, and which build?". That helps a confused user
far more than a generic "no snapshot" error.

**Read-only by construction.** Only ``initialize``, ``terminal_info``,
``account_info`` and ``symbol_info`` are ever called. There is no call to
``order_send``, ``order_check`` or any position-modifying function, and
the test-suite asserts that this module's source contains no such name.
Charon's single ``OrderSend`` site stays ``CharonTrade.mqh``.

If the package is not installed -- or not on a platform that has it --
everything degrades to ``available = False`` and the dashboard simply
does not show the extra panel. Nothing depends on this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# --- imported lazily: the package is Windows-only and optional, so a
# --- missing install must not stop the bridge from serving the snapshot.
try:  # pragma: no cover - exercised only where MT5 is installed
    import MetaTrader5 as _mt5
except Exception:  # noqa: BLE001 - any import failure means "unavailable"
    _mt5 = None


@dataclass(frozen=True)
class TerminalView:
    """A tiny, JSON-safe summary of the local terminal."""

    available: bool
    reason: str = ""
    path: str | None = None
    build: int | None = None
    connected: bool = False
    company: str | None = None
    account_login: int | None = None
    account_server: str | None = None
    account_currency: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "reason": self.reason,
            "path": self.path,
            "build": self.build,
            "connected": self.connected,
            "company": self.company,
            "account_login": self.account_login,
            "account_server": self.account_server,
            "account_currency": self.account_currency,
        }


def available() -> bool:
    return _mt5 is not None


def describe() -> TerminalView:
    """Best-effort terminal summary; never raises.

    A diagnostic helper that can throw would defeat its own purpose, so
    every failure is folded into a human-readable ``reason``.
    """
    if _mt5 is None:
        return TerminalView(
            available=False,
            reason="MetaTrader5 package not installed (optional; the bridge "
                   "works from the EA's snapshot alone)",
        )

    try:  # pragma: no cover - requires a live terminal
        if not _mt5.initialize():
            return TerminalView(
                available=False,
                reason=f"MetaTrader5 present but initialize() failed: {_mt5.last_error()}",
            )
        try:
            term = _mt5.terminal_info()
            acct = _mt5.account_info()
            return TerminalView(
                available=True,
                path=getattr(term, "path", None),
                build=getattr(term, "build", None),
                connected=bool(getattr(term, "connected", False)),
                company=getattr(term, "company", None),
                account_login=getattr(acct, "login", None) if acct else None,
                account_server=getattr(acct, "server", None) if acct else None,
                account_currency=getattr(acct, "currency", None) if acct else None,
            )
        finally:
            _mt5.shutdown()
    except Exception as exc:  # noqa: BLE001 - diagnostics must never raise
        return TerminalView(available=False, reason=f"MetaTrader5 probe failed: {exc}")


# --- Names that must never appear in this module. The test-suite reads
# --- this file and asserts the absence, so a future edit that reaches for
# --- an order function fails CI instead of quietly creating a second path.
FORBIDDEN_CALLS: tuple[str, ...] = (
    "order_send",
    "order_check",
    "positions_modify",
    "positions_close",
)

__all__ = ["FORBIDDEN_CALLS", "TerminalView", "available", "describe"]
