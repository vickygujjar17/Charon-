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
    "PREFIX_ENV",
    "SOURCE_BROKER",
    "SOURCE_CHART",
    "SUFFIX_ENV",
    "Entry",
    "canonical",
    "catalogue",
    "matches",
    "resolve",
]

