#!/usr/bin/env python3
"""Warehouse check for the playoff bracket ``real_games`` derives.

    python scripts/check_playoff_rounds.py [--db-path ./nba.duckdb]

``real_games.playoff_round`` is each series' place in the bracket and
``real_games.series_game`` each game's number in its series
(``fetch/repairs/real_games.py``). ESPN labels both where it labels anything:
the summary's note names the round from 2009 ("NBA FINALS - GAME 7", "West
1st Round - Game 1"), and its playoff series counts the game in every
postseason. This reads ``games``' copy of both (``series_note``,
``series_game``; a tree pulled before they were parsed has neither -
``scripts/backfill_series_games.py``) and fails on any game where the
bracket and ESPN disagree, or where a postseason game has no round. Needs a
warehouse, no network and no ollama; run it after a pull or a load that
touches ``games``.
"""

from __future__ import annotations

import argparse
import sys

import duckdb

from association.cli.paths import default_db_path

# A note's words for each round, checked in this order: "conference finals"
# before "finals", "semifinals" before both. The 2004-2008 notes ("PLAYOFFS",
# "FINALS") name only the last round; "FINALS" alone is it.
_NOTE_ROUNDS = (("SEMIFINALS", 2), ("1ST ROUND", 1), ("FIRST ROUND", 1), ("NBA FINALS", 4), ("CONFERENCE FINALS", 3), ("EAST FINALS", 3), ("WEST FINALS", 3))


def note_round(note: str | None) -> int | None:
    """The round an ESPN note names, or None for one naming no round."""
    if note is None:
        return None
    text = " ".join(note.upper().split())
    for words, number in _NOTE_ROUNDS:
        if words in text:
            return number
    return 4 if text == "FINALS" else None


def main() -> int:
    """Compare the bracket with ESPN's labels, game by game."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db-path", default=default_db_path(), help="DuckDB warehouse file")
    args = parser.parse_args()
    con = duckdb.connect(args.db_path, read_only=True)
    columns = {str(row[0]) for row in con.execute("DESCRIBE games").fetchall()}
    if not {"series_note", "series_game"} <= columns:
        print("games has no series_note/series_game: run scripts/backfill_series_games.py first", file=sys.stderr)
        return 1
    rows = con.execute(
        "SELECT r.event_id, r.season, r.playoff_round, r.series_game, g.series_note, g.series_game FROM real_games r JOIN games g USING (event_id, season, season_type) WHERE r.season_type = 3"
    ).fetchall()
    unplaced = [row for row in rows if row[2] is None]
    rounds = [row for row in rows if note_round(row[4]) is not None and note_round(row[4]) != row[2]]
    numbers = [row for row in rows if row[5] is not None and row[5] != row[3]]
    labeled = sum(note_round(row[4]) is not None for row in rows)
    counted = sum(row[5] is not None for row in rows)
    print(f"{len(rows)} postseason games; ESPN names the round of {labeled} and counts {counted}")
    for title, bad in (("no round", unplaced), ("round disagrees with ESPN's note", rounds), ("number disagrees with ESPN's count", numbers)):
        print(f"{title}: {len(bad)}")
        for row in bad[:20]:
            print(f"  {row}")
    return 1 if unplaced or rounds or numbers else 0


if __name__ == "__main__":
    sys.exit(main())
