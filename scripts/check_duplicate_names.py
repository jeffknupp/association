#!/usr/bin/env python3
"""Find one concept defined in more than one module.

    python scripts/check_duplicate_names.py
    python scripts/check_duplicate_names.py --list   # every collision, allowed or not

Two module-level constants with the same name are either one fact written
twice - which drifts - or two different facts wearing one name, which is worse.
``MAX_LIMIT`` is the second kind: 100 in ``query/leaderboard.py`` and 50 in
``query/templates.py``, so a reader who knows one is wrong about the other.

This is the constants half of a shape `AGENTS.md` already records for
functions ("Branches built in parallel collide on private helper names,
silently"). Ruff's F811 and mypy's ``no-redef`` catch a collision **inside** one
module; nothing catches the same name in two. Five separate definitions of the
NBA's five-hour Eastern offset passed every gate this project had.

Not a copy-paste detector. Duplicated blocks are pylint's ``--duplicate-code``
and jscpd's job, and both would be a new dependency answering a different
question. This asks only: does one NAME mean one thing here?

Values are deliberately NOT compared. A scan that reports every constant equal
to 5 pairs ``DEFAULT_LIMIT`` with ``EASTERN_OFFSET_HOURS`` and buries the real
finding in coincidence.

**The allowlist is the point.** The collisions already on record are listed in
:data:`ALLOWED` with the issue that tracks them, so this fails only on a NEW
one. Removing an entry is how a fix gets verified; adding one needs an
`ISSUES.md` entry, not a shrug.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import sys
from collections import defaultdict

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "association"

#: Collisions that are known, filed and deliberately not fixed yet.
#:
#: Each maps the colliding name to the issue that tracks it. They are real
#: couplings, not false positives - nothing answers wrong because of them
#: today, which is why they are P4 rather than fixed in place.
ALLOWED: dict[str, str] = {
    # 100 in leaderboard.py, 50 in templates.py - one name, two limits.
    "MAX_LIMIT": "#81",
    # "total", twice. No entry of its own: it is one literal in two modules
    # that never import each other, and #83 covers the pattern.
    "FINGERPRINT_SUMMARY_CATEGORY": "",
    # ESPN's season-type code. nba/coverage.py holds it for the query layer;
    # fetch/pipeline.py holds its own so `fetch` depends on nothing above it -
    # a deliberate duplication of a wire constant, not a drifting fact.
    "POSTSEASON": "",
    # Each load-time module names the views it builds; same name, different
    # views, and no reader ever imports both.
    "VIEWS": "",
    # Each repair module's own required-column set. Same reasoning as VIEWS.
    "_REQUIRED_COLUMNS": "",
}


def collisions() -> dict[str, list[tuple[str, str]]]:
    """Every module-level constant defined in more than one module.

    Returns:
        name -> [(module, value repr)], for names appearing in 2+ modules.
    """
    by_name: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                # Upper-case only: a module-level lower-case binding is usually
                # a logger or a lazily built table, not a declared constant.
                if not isinstance(target, ast.Name) or not target.id.isupper():
                    continue
                try:
                    value = repr(ast.literal_eval(node.value)) if node.value else "?"
                except (ValueError, TypeError, SyntaxError):
                    value = "<computed>"
                by_name[target.id].append((str(path.relative_to(SRC)), value[:70]))
    return {name: places for name, places in by_name.items() if len({module for module, _ in places}) > 1}


def main() -> int:
    """Report collisions that are not on the allowlist."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="Show every collision, including allowed ones")
    args = parser.parse_args()

    found = collisions()
    unexpected = {name: places for name, places in found.items() if name not in ALLOWED}

    if args.list:
        for name, places in sorted(found.items()):
            tracked = ALLOWED.get(name)
            note = "" if name not in ALLOWED else f"  (allowed{f', {tracked}' if tracked else ''})"
            print(f"{name}{note}")
            for module, value in places:
                print(f"    {module:<42} = {value}")
        print()

    for name, places in sorted(unexpected.items()):
        print(f"FAIL  {name} is defined in {len(places)} modules:")
        for module, value in places:
            print(f"        {module:<42} = {value}")
        print("      Give it one home and import it, or rename one - and if it must stay, add it to ALLOWED with an ISSUES.md entry.")

    stale = sorted(name for name in ALLOWED if name not in found)
    for name in stale:
        print(f"FAIL  {name} is on the allowlist but no longer collides - remove it from ALLOWED.")

    problems = len(unexpected) + len(stale)
    print(f"{len(found) - len(unexpected)}/{len(found)} cross-module constant collisions are known and tracked")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
