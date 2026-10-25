"""Symbol naming: what the broker calls it, and what a human calls it.

A broker rarely names a pair the way a trader does. The same EURUSD may be
``EURUSD``, ``EURUSD.a``, ``EURUSD.pro``, ``EURUSDm``, ``mEURUSD`` or
``#EURUSD`` depending on the account. The dashboard has to display one
short, stable label -- and it must never *send* that label.

The rule this module exists to enforce:

    **Canonical form is for humans. Every command carries the exact
    broker name.**

Resolving ``"EURUSD"`` to the broker's ``"EURUSD.a"`` is useful for a
picker and a search box. Sending ``"EURUSD"`` to ``/api/order`` would
address a symbol the terminal may not have -- the EA compares the command's
``symbol`` against its own ``_Symbol`` and refuses a mismatch, so the
failure is at least loud. But the failure that matters is the quieter one:
a wrong-but-plausible name that resolves to a *different* live chart.

So :func:`resolve` only ever returns a name taken from ``known``, which is
built from what the EA is actually publishing plus what the terminal
actually lists. There is no code path here that invents a symbol.

Configuration
-------------
Broker affixes are deployment-specific, so they are configured rather than
guessed:

* ``CHARON_SYMBOL_PREFIX`` -- e.g. ``m`` for ``mEURUSD``
* ``CHARON_SYMBOL_SUFFIX`` -- e.g. ``.a`` or ``.pro`` for ``EURUSD.a``

Both are optional. With neither set, canonical form is just upper case.
Guessing them would be worse than useless: a ``.`` suffix rule would turn
``US30.cash`` into ``US30`` and then fail to match the real chart, and a
heuristic on ``#`` would silently mangle symbols that legitimately contain
it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, Sequence

PREFIX_ENV = "CHARON_SYMBOL_PREFIX"
SUFFIX_ENV = "CHARON_SYMBOL_SUFFIX"

# --- where a candidate came from, so the UI can prefer a live chart over a
# --- merely-listed instrument without inventing a second lookup path.
SOURCE_CHART = "chart"
SOURCE_BROKER = "broker"


def _affixes() -> tuple[str, str]:
    """``(prefix, suffix)`` from the environment, read fresh each call.

    Read per call rather than cached at import: a cached value would make
    the setting untestable without reloading modules.
    """
    return (
        os.environ.get(PREFIX_ENV, "").strip(),
        os.environ.get(SUFFIX_ENV, "").strip(),
    )


def canonical(symbol: str) -> str:
    """The short, human-facing form of ``symbol``.

    Display only -- see the module docstring. Stripping is bounded so a
    name can never be reduced to nothing: a symbol that *is* the suffix
    (an instrument literally called ``.a``) keeps its own text.
    """
    text = symbol.strip()
    if not text:
        return ""

    prefix, suffix = _affixes()
    if prefix and len(text) > len(prefix) and text.casefold().startswith(prefix.casefold()):
        text = text[len(prefix):]
    if suffix and len(text) > len(suffix) and text.casefold().endswith(suffix.casefold()):
        text = text[: len(text) - len(suffix)]
    return text.upper()


# --- how a picker groups instruments. Advisory only: this is a display label,
# --- never a filter. A symbol whose class cannot be determined is ASSET_OTHER
# --- and is still offered -- a broker calls things the textbook does not.
ASSET_FOREX = "forex"
ASSET_METALS = "metals"
ASSET_INDEX = "indices"
ASSET_ENERGY = "energy"
ASSET_CRYPTO = "crypto"
ASSET_STOCK = "stocks"
ASSET_OTHER = "other"

ASSET_CLASSES: tuple[str, ...] = (
    ASSET_FOREX,
    ASSET_METALS,
    ASSET_INDEX,
    ASSET_ENERGY,
    ASSET_CRYPTO,
    ASSET_STOCK,
    ASSET_OTHER,
)

# --- token lists, matched against the canonical (suffix-stripped) name.
# --- Deliberately short and unambiguous: the goal is a useful heading on a
# --- picker, and a wrong guess here must never cost an operator a symbol.
_METAL_TOKENS = frozenset({
    "XAU", "XAG", "XPT", "XPD", "GOLD", "SILVER", "PLATINUM", "PALLADIUM",
})
_INDEX_TOKENS = frozenset({
    "US30", "US500", "US100", "NAS100", "SPX500", "GER30", "GER40", "UK100",
    "JP225", "AUS200", "FRA40", "HK50", "DE30", "DE40", "EU50", "US2000",
    "DJ30", "NDX", "SPX", "DAX", "FTSE", "NIKKEI",
})
_ENERGY_TOKENS = frozenset({
    "WTI", "BRENT", "USOIL", "UKOIL", "NGAS", "NATGAS", "GAS", "OIL",
})
_CRYPTO_TOKENS = frozenset({
    "BTC", "ETH", "XRP", "LTC", "BCH", "ADA", "SOL", "DOGE", "DOT", "AVAX",
    "LINK", "XLM", "TRX", "BNB", "MATIC",
})
# --- currencies that appear in a six-letter pair. Kept as a set of the ones a
# --- retail broker actually lists, plus the majors' crosses.
_CURRENCIES = frozenset({
    "USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "SEK", "NOK",
    "DKK", "SGD", "HKD", "ZAR", "MXN", "TRY", "PLN", "CZK", "HUF", "CNH",
    "RMB", "INR", "RUB", "BRL", "THB", "KRW",
})


def asset_class(name: str) -> str:
    """A best-effort instrument class for ``name``, for grouping only.

    The rule that must not be relaxed: **this never rejects or rewrites a
    symbol.** Everything it cannot classify comes back as
    :data:`ASSET_OTHER`, which the picker still shows under its own heading.
    A broker suffix (``.a``, ``m``, ``#``) is already stripped by
    :func:`canonical`, so ``XAUUSD.a`` classifies like ``XAUUSD``.

    Detection order matters. Metals, indices, energy and crypto are matched by
    token because those names are distinctive; forex is matched structurally
    (two known currencies in a row) and only afterwards, so ``USDJPY`` is forex
    while ``USOIL`` is not.
    """
    text = canonical(name)
    if not text:
        return ASSET_OTHER

    # --- split on the usual separators so BTCUSD, BTC/USD and BTC-USD all
    # --- reduce to the same tokens.
    tokens = [token for token in text.replace("-", "/").replace("_", "/").replace(".", "/").split("/") if token]
    if len(tokens) > 1:
        for token in tokens:
            found = _classify_token(token)
            if found != ASSET_OTHER:
                return found

    found = _classify_token(text)
    return found


def _classify_token(token: str) -> str:
    """Classify one already-uppercased token.

    Prefix matching is used where the instrument's name *starts* with the
    marker (``BTCUSD``, ``XAUUSD``, ``WTIUSD``); exact matching is used for
    index names, where a prefix test would collide with unrelated symbols.
    Structural forex comes last so ``USOIL`` is never read as a currency pair.
    """
    if not token:
        return ASSET_OTHER
    for prefix in _METAL_TOKENS:
        if token.startswith(prefix):
            return ASSET_METALS
    for prefix in _ENERGY_TOKENS:
        if token.startswith(prefix):
            return ASSET_ENERGY
    for prefix in _CRYPTO_TOKENS:
        if token.startswith(prefix):
            return ASSET_CRYPTO
    if token in _INDEX_TOKENS:
        return ASSET_INDEX
    if len(token) == 6 and token.isalpha():
        if token[:3] in _CURRENCIES and token[3:] in _CURRENCIES:
            return ASSET_FOREX
    return ASSET_OTHER


@dataclass(frozen=True)
class Entry:
    """One instrument, as offered to the picker."""

    name: str
    """The exact name to put in a command. Never canonicalised."""

    canonical: str
    """The label to show. Never sent."""

    sources: tuple[str, ...]
    """``SOURCE_CHART`` and/or ``SOURCE_BROKER``, chart first."""

    def as_dict(self) -> dict[str, object]:
        return {
            "symbol": self.name,
            "label": self.canonical,
            "sources": list(self.sources),
            # --- a heading for the picker, never a filter. See asset_class().
            "asset_class": asset_class(self.name),
        }


def _unique(names: Iterable[str]) -> list[str]:
    """Drop blanks and case-insensitive duplicates, keeping first spelling.

    The broker's list is authoritative, so two spellings that differ only by
    case are the same instrument; listing both would make every picker entry
    ambiguous for :func:`resolve`.
    """
    seen: set[str] = set()
    found: list[str] = []
    for name in names:
        text = name.strip() if isinstance(name, str) else ""
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        found.append(text)
    return found


def catalogue(
    chart: Iterable[str] = (),
    broker: Iterable[str] = (),
) -> list[Entry]:
    """Merge the two sources into one list, chart names first.

    Order is deliberate: charts the EA is already publishing are the ones a
    trader can act on *right now*, so they lead. A broker-listed symbol with
    no chart behind it is still offered -- that is how you discover what to
    attach Charon to next -- but it sorts after the live ones.

    Merging is by exact name and ``sources`` accumulates, so a symbol that is
    both live and listed reports both rather than appearing twice.
    """
    order: list[str] = []
    sources: dict[str, list[str]] = {}
    for source, names in ((SOURCE_CHART, chart), (SOURCE_BROKER, broker)):
        for name in _unique(names):
            if name not in sources:
                sources[name] = []
                order.append(name)
            if source not in sources[name]:
                sources[name].append(source)

    return [
        Entry(name=name, canonical=canonical(name), sources=tuple(sources[name]))
        for name in order
    ]


def matches(hint: str, known: Sequence[str]) -> list[str]:
    """Every known name ``hint`` could mean, most specific first.

    Three passes, stopping at the first that finds anything:

    1. exact match -- the operator pasted the broker's own name;
    2. case-insensitive exact -- ``eurusd.a`` for ``EURUSD.a``;
    3. canonical match -- ``EURUSD`` for ``EURUSD.a``.

    Each pass is scored against ``known`` only, so an unknown hint yields an
    empty list and an ambiguous one yields several names. That is precisely
    the information the caller needs in order to ask a better question
    instead of picking one at random.
    """
    wanted = hint.strip() if isinstance(hint, str) else ""
    if not wanted:
        return []

    names = _unique(known)
    exact = [name for name in names if name == wanted]
    if exact:
        return exact

    folded = [name for name in names if name.casefold() == wanted.casefold()]
    if folded:
        return folded

    target = canonical(wanted)
    if not target:
        return []
    return [name for name in names if canonical(name) == target]


def resolve(hint: str, known: Sequence[str]) -> str | None:
    """The one known name ``hint`` unambiguously refers to, or ``None``.

    ``None`` covers both "nothing matched" and "more than one thing
    matched", which are genuinely different answers. Callers that must tell
    them apart should use :func:`matches` and inspect its length; this
    convenience form exists for the common case where either outcome means
    the same thing -- do not proceed.
    """
    found = matches(hint, known)
    return found[0] if len(found) == 1 else None


__all__ = [
    "ASSET_CLASSES",
    "ASSET_CRYPTO",
    "ASSET_ENERGY",
    "ASSET_FOREX",
    "ASSET_INDEX",
    "ASSET_METALS",
    "ASSET_OTHER",
    "ASSET_STOCK",
    "PREFIX_ENV",
    "SOURCE_BROKER",
    "SOURCE_CHART",
    "SUFFIX_ENV",
    "Entry",
    "asset_class",
    "canonical",
    "catalogue",
    "matches",
    "resolve",
]

