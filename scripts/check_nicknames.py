#!/usr/bin/env python3
"""Warehouse check for entities.PLAYER_NICKNAMES.

The table is hand-curated and its correctness is a claim about the warehouse,
not about the code: every value has to name exactly one player, and no key may
be a name token that belongs only to somebody else. Neither can be asserted in
pytest, which runs offline against fixtures - so it lives here, next to
check_routing.py, and needs a built warehouse but no ollama.

    python scripts/check_nicknames.py [--db-path ./nba.duckdb]

Run it after editing the table. A failure is one of three things: a value that
no longer matches a player (an ESPN spelling change, or a player the current
warehouse does not reach back far enough to hold), a value that became
ambiguous, or a key that collides with a real player's name.
"""

from __future__ import annotations

import argparse
import re
import sys

import duckdb

from association.query.entities import PLAYER_NICKNAMES

# Keys that ARE another player's real name token, kept on purpose: one player
# dominates the shorthand badly enough that a question carrying only it cannot
# reliably have meant the other. Listed rather than waved through by a rule, so
# that adding a fifth is a decision somebody makes explicitly - the same reason
# PLAYER_NICKNAMES is curated instead of derived.
DELIBERATE_COLLISIONS = {
    "melo",  # Fab Melo, 19 games in the warehouse; "Melo" is Carmelo Anthony.
    "mj",  # MJ Walker, 3 games; "MJ" is Michael Jordan.
    "russ",  # Russ Smith, 66 games; "Russ" is Russell Westbrook.
    "shaq",  # Shaq Buchanan, 3 games; "Shaq" is Shaquille O'Neal.
}


def main() -> int:
    """Check every nickname against the warehouse. Returns a process exit code."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default="./nba.duckdb")
    args = parser.parse_args()

    con = duckdb.connect(args.db_path, read_only=True)
    names = [r[0] for r in con.execute("SELECT display_name FROM players").fetchall()]
    # Tokens as a person would type them: "Karl-Anthony" is two, "Ja'Kobe" two.
    owners: dict[str, set[str]] = {}
    for name in names:
        for token in re.split(r"[^A-Za-z0-9.]+", name):
            if token:
                owners.setdefault(token.casefold(), set()).add(name)

    failures = 0
    for nick, target in sorted(PLAYER_NICKNAMES.items()):
        hits = [n for n in names if n.casefold() == target.casefold()]
        if len(hits) != 1:
            print(f"FAIL  {nick!r} -> {target!r}: matches {len(hits)} players {hits[:3]}")
            failures += 1
            continue
        stolen = owners.get(nick, set()) - {target}
        if stolen and target not in owners.get(nick, set()) and nick not in DELIBERATE_COLLISIONS:
            # The key is somebody's actual name and NOT the target's - the
            # override would answer about the wrong person. A key the target
            # also owns is fine: that is "melo", "shaq", "luka".
            print(f"FAIL  {nick!r} -> {target!r}: {nick!r} is the name of {sorted(stolen)[:3]}")
            failures += 1

    print(f"\n{len(PLAYER_NICKNAMES) - failures}/{len(PLAYER_NICKNAMES)} nicknames check out against {args.db_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
