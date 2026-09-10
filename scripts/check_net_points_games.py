#!/usr/bin/env python3
"""Warehouse and source check for the NetPoints per-game date rule.

Which ESPN game a NetPoints daily file's rows belong to is a claim about the
data, not about the code: NetPoints names its files for the US Eastern date
the games were played on and publishes no ESPN id, so `NetPointsGameIndex`
recovers the game from (team, Eastern date) against the `games` table. pytest
checks that rule against fixtures, offline. This checks it against the real
schedule and, optionally, against the source's own box score.

    python scripts/check_net_points_games.py [--db-path ./nba.duckdb] [--season 2026]
    python scripts/check_net_points_games.py --against-source --dates 20

Two checks, in that order:

1. **Offline, over the warehouse.** No player-game may appear twice. A
   duplicate `(event_id, athlete_id)` is the signature of two NetPoints dates
   resolving to one ESPN game - 611 of them in season 2026 before the rule was
   fixed, every pair disagreeing about how many possessions the player played.
2. **With --against-source, over the live daily files.** The daily file
   carries `pts` beside the NetPoints values and the parser deliberately drops
   it, which makes it a free, independent way to ask whether a row landed on
   the right game: resolve each row the way the pull does, then compare its
   points against `player_box_stats` for the event that came back. This is
   what caught the bug in the first place - NetPoints' 2025-10-25 file gives
   Ryan Kalkbrenner 14 points and its 2025-10-26 file gives him 4, and the old
   rule sent both rows to the game where he scored 4.

Needs a built warehouse; --against-source additionally needs network and
boto3's Cognito exchange (see fetch/netpoints_client.py).
"""

from __future__ import annotations

import argparse
import datetime as dt
import random
import sys

import duckdb

from association.fetch.netpoints_client import NetPointsDailyClient
from association.fetch.parse import NET_POINTS_ABBREV_TO_ESPN, NetPointsGameIndex

# Enough dates to cover a season's shapes (back-to-backs, afternoon tips, the
# turn of a month) without spending an hour on S3. Sampled deterministically.
DEFAULT_DATES = 12
SAMPLE_SEED = 0


def _duplicate_player_games(con: duckdb.DuckDBPyConnection, season: int) -> list[str]:
    """The rows a wrongly resolved date leaves behind, in both per-game tables."""
    problems: list[str] = []
    for table, keys in (("net_points_player_game", "event_id, athlete_id"), ("net_points_player_game_fingerprint", "event_id, athlete_id, category")):
        try:
            rows, worst = con.execute(
                f"SELECT count(*), max(n) FROM (SELECT count(*) AS n FROM {table} WHERE season = ? AND athlete_id IS NOT NULL GROUP BY {keys} HAVING n > 1)",
                [season],
            ).fetchone() or (0, None)
        except duckdb.CatalogException:
            problems.append(f"{table}: not in this warehouse - pull it with `data pull --include-net-points-daily`")
            continue
        if rows:
            problems.append(f"{table}: {rows} duplicated ({keys}) in season {season}, up to {worst} rows for one of them")
    return problems


def _game_index(con: duckdb.DuckDBPyConnection) -> NetPointsGameIndex:
    return NetPointsGameIndex(con.execute("SELECT event_id, season, season_type, date, home_team_id, away_team_id FROM games").fetchall())


def _played_nearby(con: duckdb.DuckDBPyConnection, athlete_id: str, event_id: str, date: str) -> str | None:
    """A game within a day of this one that the player IS in the box score of.

    The one thing that turns "absent from the box score" into evidence the row
    landed on the wrong game: another game, close enough to be the one the
    NetPoints date meant, where the player really appears.
    """
    row = con.execute(
        "SELECT g.event_id FROM player_box_stats b JOIN games g USING (event_id) WHERE b.athlete_id = ? AND g.event_id <> ? AND substr(g.date, 1, 10) BETWEEN ? AND ? LIMIT 1",
        [athlete_id, event_id, date, (dt.date.fromisoformat(date) + dt.timedelta(days=1)).isoformat()],
    ).fetchone()
    return None if row is None else str(row[0])


def _against_source(con: duckdb.DuckDBPyConnection, season: int, dates: int) -> list[str]:
    """Resolve a sample of live daily files and check the points agree."""
    index = _game_index(con)
    abbrevs = {str(a): str(t) for t, a in con.execute("SELECT team_id, abbreviation FROM teams").fetchall()}
    names: dict[str, set[str]] = {}
    for athlete_id, name in con.execute("SELECT athlete_id, display_name FROM players").fetchall():
        if name:
            names.setdefault(str(name), set()).add(str(athlete_id))
    name_to_athlete_id = {name: next(iter(ids)) for name, ids in names.items() if len(ids) == 1}

    played = [str(row[0]) for row in con.execute("SELECT DISTINCT substr(date, 1, 10) FROM games WHERE season = ? ORDER BY 1", [season]).fetchall()]
    sample = sorted(random.Random(SAMPLE_SEED).sample(played, min(dates, len(played))))

    client = NetPointsDailyClient()
    problems: list[str] = []
    checked = 0
    absent = 0
    for date in sample:
        data = client.get_daily(date, season_folder=season - 1)
        for raw in (data or {}).get("player_box") or []:
            abbrev = raw.get("tmName")
            team_id = abbrevs.get(NET_POINTS_ABBREV_TO_ESPN.get(abbrev, abbrev)) if abbrev else None
            athlete_id = name_to_athlete_id.get(raw.get("displayName"))
            points = raw.get("pts")
            game = index.resolve(team_id, date)
            if game is None or athlete_id is None or points is None:
                continue
            row = con.execute("SELECT points FROM player_box_stats WHERE event_id = ? AND athlete_id = ?", [game[0], athlete_id]).fetchone()
            if row is None:
                # ESPN's box score does not list this player at all. That is
                # only evidence about the DATE if some neighbouring game of
                # theirs does - otherwise the two sources simply disagree
                # about who dressed, which they do for end-of-bench players
                # (Lachlan Olbrich, 11.8 possessions and 2 points on
                # 2026-01-07, in a Chicago game ESPN gives 24 rows to and
                # leaves him out of - and Chicago played no other game that
                # date for the row to belong to instead).
                elsewhere = _played_nearby(con, athlete_id, game[0], date)
                if elsewhere is None:
                    absent += 1
                    continue
                checked += 1
                problems.append(f"{date}: {raw.get('displayName')} resolved to {game[0]}, which they are not in the box score of - they played {elsewhere} instead")
                continue
            checked += 1
            if int(row[0] or 0) != int(points):
                problems.append(f"{date}: {raw.get('displayName')} resolved to {game[0]}, where the box score says {row[0]} points and the source says {points}")
    unlisted = f"{absent} {'row' if absent == 1 else 'rows'} ESPN's box score does not list at all"
    print(f"{checked - len(problems)}/{checked} player-games from {len(sample)} live dates land on a game whose box score agrees ({unlisted})")
    return problems


def main() -> int:
    """Check the per-game NetPoints date rule and report what disagrees."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db-path", default="./nba.duckdb")
    parser.add_argument("--season", type=int, default=2026, help="Season-ending year to check (default: 2026).")
    parser.add_argument("--against-source", action="store_true", help="Also fetch live daily files and check the points agree. Needs network.")
    parser.add_argument("--dates", type=int, default=DEFAULT_DATES, help=f"How many dates to sample with --against-source (default: {DEFAULT_DATES}).")
    args = parser.parse_args()

    con = duckdb.connect(args.db_path, read_only=True)
    problems = _duplicate_player_games(con, args.season)
    if not problems:
        print(f"no player-game is written twice in season {args.season}")
    if args.against_source:
        problems += _against_source(con, args.season, args.dates)

    for problem in problems:
        print(f"FAIL  {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
