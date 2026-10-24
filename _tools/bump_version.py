#!/usr/bin/env python3
"""bump_version.py -- keep every Charon version string in lockstep.

Charon's release identifier lives in exactly three shapes:

  1. `#define CHARON_VERSION        "1.0.0"`  in CharonDefines.mqh
     -- the human-facing release string.
  2. `#property version   "1.00"`             in every .mq5/.mqh
     -- MetaEditor requires the numeric xxx.yyy form; it always mirrors
        CHARON_VERSION (1.0.0 -> 1.00, 1.2.3 -> 1.02).
  3. `BRIDGE_VERSION = "1.0.0"`               in bridge/charon_bridge/version.py
     -- the Python bridge ships with the EA, so the two must never
        disagree about which release is installed.

Usage
    python _tools/bump_version.py --check           # verify only (CI gate)
    python _tools/bump_version.py 1.0.1             # bump everywhere
    python _tools/bump_version.py 1.0.1 --dry-run   # show what would change

Exit codes: 0 = consistent / applied, 1 = mismatch or bad input.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VERSION_DEF = re.compile(r'(#define\s+CHARON_VERSION\s+")([^"]+)(")')
VERSION_PROP = re.compile(r'(#property\s+version\s+)"([^"]+)"')
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

# --- the Python bridge carries the same release number
BRIDGE_VERSION_PY = REPO_ROOT / "bridge" / "charon_bridge" / "version.py"
BRIDGE_VERSION_DEF = re.compile(r'(BRIDGE_VERSION\s*=\s*")([^"]+)(")')


def property_form(version: str) -> str:
    """MetaEditor needs xxx.yyy -- map 1.2.3 to 1.02."""
    major, minor, _patch = (int(p) for p in SEMVER.match(version).groups())
    return f"{major}.{minor:02d}"


def source_files() -> list[Path]:
    files = sorted(REPO_ROOT.glob("*.mqh")) + sorted(REPO_ROOT.glob("*.mq5"))
    return [f for f in files if f.is_file()]


def version_file() -> Path:
    return REPO_ROOT / "VERSION"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bump or verify Charon versions.")
    parser.add_argument("version", nargs="?", help="new version, e.g. 1.0.1")
    parser.add_argument("--check", action="store_true",
                        help="verify consistency only, never write")
    parser.add_argument("--dry-run", action="store_true",
                        help="report changes without writing")
    args = parser.parse_args(argv)

    defines = REPO_ROOT / "CharonDefines.mqh"
    if not defines.is_file():
        print(f"FATAL: {defines} not found")
        return 1

    match = VERSION_DEF.search(read(defines))
    if not match:
        print(f"FATAL: CHARON_VERSION not found in {defines.name}")
        return 1
    current = match.group(2)

    if args.version:
        if not SEMVER.match(args.version):
            print(f"FATAL: '{args.version}' is not X.Y.Z")
            return 1
        target = args.version
    else:
        target = current

    expected_prop = property_form(target)
    problems: list[str] = []
    changes: list[str] = []

    for path in source_files():
        text = read(path)
        original = text

        if path.name == "CharonDefines.mqh":
            text = VERSION_DEF.sub(lambda m: m.group(1) + target + m.group(3), text)

        text = VERSION_PROP.sub(lambda m: f'{m.group(1)}"{expected_prop}"', text)

        for hit in VERSION_PROP.finditer(original):
            if hit.group(2) != expected_prop:
                problems.append(
                    f"{path.name}: #property version \"{hit.group(2)}\" != \"{expected_prop}\"")
        if path.name == "CharonDefines.mqh":
            for hit in VERSION_DEF.finditer(original):
                if hit.group(2) != target:
                    problems.append(
                        f"{path.name}: CHARON_VERSION \"{hit.group(2)}\" != \"{target}\"")

        if text != original:
            changes.append(path.name)
            if not args.check and not args.dry_run:
                path.write_text(text, encoding="utf-8", newline="\n")

    # --- the VERSION file must agree too
    vfile = version_file()
    if vfile.is_file():
        stored = read(vfile).strip()
        if stored != target:
            problems.append(f"VERSION: \"{stored}\" != \"{target}\"")
            changes.append("VERSION")
            if not args.check and not args.dry_run:
                vfile.write_text(target + "\n", encoding="utf-8", newline="\n")

    # --- the Python bridge must ship the same release number
    bridge_files = 0
    if BRIDGE_VERSION_PY.is_file():
        bridge_files = 1
        text = read(BRIDGE_VERSION_PY)
        original = text
        for hit in BRIDGE_VERSION_DEF.finditer(original):
            if hit.group(2) != target:
                problems.append(
                    f"bridge/charon_bridge/version.py: BRIDGE_VERSION "
                    f"\"{hit.group(2)}\" != \"{target}\"")
        text = BRIDGE_VERSION_DEF.sub(lambda m: m.group(1) + target + m.group(3), text)
        if text != original:
            changes.append("bridge/charon_bridge/version.py")
            if not args.check and not args.dry_run:
                BRIDGE_VERSION_PY.write_text(text, encoding="utf-8", newline="\n")
    else:
        problems.append("bridge/charon_bridge/version.py: missing")

    print(f"current CHARON_VERSION : {current}")
    print(f"target  CHARON_VERSION : {target}")
    print(f"#property version form : {expected_prop}")
    print(f"files scanned          : {len(source_files())} MQL5 + {bridge_files} bridge")

    if args.check:
        if problems:
            print("\nINCONSISTENT:")
            for p in problems:
                print(f"  {p}")
            return 1
        print("\nOK: every version string agrees")
        return 0

    if changes:
        verb = "would update" if args.dry_run else "updated"
        print(f"\n{verb}: {', '.join(changes)}")
    else:
        print("\nOK: nothing to change")
    return 0


if __name__ == "__main__":
    sys.exit(main())
