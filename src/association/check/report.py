"""`association data check`: report local data coverage, optionally cross-checked
against ESPN's live schedule to show exactly what games are missing.

Local-only (no network) by default - pass --live to also cross-check against
ESPN's schedule and report expected game counts, not just what's on disk.

A season+season_type that `pull` has already verified complete (postponed/
cancelled games included) gets its local `_complete` marker trusted here too -
no live schedule call needed, since a finished season's schedule can't change.
Pass --force to re-verify against ESPN anyway.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from ..fetch.client import ESPNClient
from ..fetch.pipeline import Pipeline

SEASON_TYPE_NAME = {1: "preseason", 2: "regular", 3: "postseason"}

# NetPoints (espnanalytics.com) has its own season_type labels, not this
# project's 1/2/3 - only regular season and playoffs have a clean equivalent
# to check coverage against here (it also has PlayIn/IST Championship rows,
# which aren't represented by any of this project's season_type codes).
NET_POINTS_TYPE_LABEL = {2: "Regular Season", 3: "Playoffs"}


def discover_seasons(data_dir: Path) -> list[int]:
    games_dir = data_dir / "games"
    if not games_dir.exists():
        return []
    seasons = set()
    for p in games_dir.glob("season=*"):
        try:
            seasons.add(int(p.name.split("=", 1)[1]))
        except ValueError:
            pass
    return sorted(seasons)


def discover_season_types(data_dir: Path) -> list[int]:
    """Same reasoning as discover_seasons - a hardcoded "1,2,3" default used to
    disagree with `data pull`'s own default of "2,3" (preseason skipped),
    making a plain `data check` report preseason as entirely missing for data
    nobody ever asked to fetch. Discovering from what's actually on disk
    (across every season, not per-season) fixes that structurally instead of
    just picking a new hardcoded string that could drift out of sync again."""
    games_dir = data_dir / "games"
    if not games_dir.exists():
        return []
    types = set()
    for p in games_dir.glob("season=*/season_type=*"):
        try:
            types.add(int(p.name.split("=", 1)[1]))
        except ValueError:
            pass
    return sorted(types)


def _local_count(con: duckdb.DuckDBPyConnection, table_dir: Path, season: int, season_type: int | None = None) -> int:
    if not table_dir.exists() or not any(table_dir.rglob("*.parquet")):
        return 0
    where = f"season = {season}"
    if season_type is not None:
        where += f" AND season_type = {season_type}"
    glob = str(table_dir / "**" / "*.parquet")
    row = con.execute(f"SELECT count(*) FROM read_parquet(?, union_by_name=true) WHERE {where}", [glob]).fetchone()
    assert row is not None  # COUNT(*) always returns exactly one row
    return row[0]


def _net_points_player_count(con: duckdb.DuckDBPyConnection, data_dir: Path, season: int, season_type: int) -> int:
    label = NET_POINTS_TYPE_LABEL.get(season_type)
    table_dir = data_dir / "net_points_player"
    if label is None or not table_dir.exists() or not any(table_dir.rglob("*.parquet")):
        return 0
    glob = str(table_dir / "**" / "*.parquet")
    row = con.execute(
        "SELECT count(*) FROM read_parquet(?, union_by_name=true) WHERE season = ? AND net_points_season_type = ?",
        [glob, season, label],
    ).fetchone()
    assert row is not None  # COUNT(*) always returns exactly one row
    return row[0]


def _resolved_count(data_dir: Path, season: int, season_type: int) -> int:
    # Games ESPN settled as postponed/cancelled - never played, but not "missing"
    # either. Directory listing only, same cheap check pipeline.py's own
    # completeness logic uses.
    d = data_dir / "_resolved" / f"season={season}" / f"season_type={season_type}"
    return sum(1 for _ in d.glob("*.marker")) if d.exists() else 0


def run_check(
    data_dir: Path,
    seasons: list[int] | None,
    season_types: list[int] | None,
    rate_limit: float = 5.0,
    live: bool = False,
    force: bool = False,
) -> None:
    data_dir = Path(data_dir)
    if not seasons:
        seasons = discover_seasons(data_dir)
    if not seasons:
        print(f"No local data found under {data_dir}. Run `association data pull` first.")
        return
    if not season_types:
        season_types = discover_season_types(data_dir)
    if not season_types:
        print(f"No local season-type data found under {data_dir}. Run `association data pull` first.")
        return

    con = duckdb.connect(":memory:")
    client = ESPNClient(rate_limit=rate_limit) if live else None
    pipeline = Pipeline(client, data_dir)
    team_ids = pipeline.team_ids() if live else []

    cols = ["season", "type", "complete", "games (have/expected)", "standings", "team_stats", "bpi", "players", "shots", "net_pts", "np_gm"]
    widths = [6, 10, 8, 23, 9, 10, 4, 7, 8, 7, 6]
    print(" ".join(c.rjust(w) for c, w in zip(cols, widths, strict=True)))
    print("-" * (sum(widths) + len(widths) - 1))

    any_cached = False
    any_net_points = False
    any_net_points_daily = False
    for season in seasons:
        std_count = _local_count(con, data_dir / "standings", season)
        bpi_count = _local_count(con, data_dir / "team_power_index", season)
        for season_type in season_types:
            marker = data_dir / "_complete" / f"season={season}" / f"season_type={season_type}.marker"
            is_cached_complete = marker.exists()
            complete = "yes" if is_cached_complete else "no"

            have_games = _local_count(con, data_dir / "games", season, season_type)
            resolved = _resolved_count(data_dir, season, season_type)
            have = have_games + resolved  # played + confirmed-never-played both count as "accounted for"

            if is_cached_complete and not force:
                # A completed season's schedule can't change - trust the marker
                # pull already verified, skip the live schedule call entirely.
                games_str = f"{have}/{have}*"
                any_cached = True
            elif live:
                expected = len(pipeline.event_ids_for(season, season_type, team_ids))
                games_str = f"{have}/{expected}"
            else:
                games_str = f"{have}/?"

            tss_count = _local_count(con, data_dir / "team_season_stats", season, season_type)
            players = len(pipeline.athlete_ids_for(season, season_type))
            shots = _local_count(con, data_dir / "shot_chart", season, season_type)
            net_pts = _net_points_player_count(con, data_dir, season, season_type)
            if season_type in NET_POINTS_TYPE_LABEL:
                any_net_points = True
            net_pts_daily = _local_count(con, data_dir / "net_points_player_game", season, season_type)
            if net_pts_daily:
                any_net_points_daily = True

            row = [
                str(season),
                SEASON_TYPE_NAME.get(season_type, str(season_type)),
                complete,
                games_str,
                str(std_count),
                str(tss_count),
                str(bpi_count),
                str(players),
                str(shots),
                str(net_pts),
                str(net_pts_daily),
            ]
            print(" ".join(c.rjust(w) for c, w in zip(row, widths, strict=True)))

    con.close()
    notes = []
    if any_cached:
        notes.append("* = trusted from local completion marker, not re-verified live this run (pass --force to re-verify)")
    if not live:
        notes.append("offline check - pass --live to cross-check game counts against ESPN's schedule")
    if any_net_points:
        notes.append(
            "net_pts = NetPoints (espnanalytics.com) player rows for regular/postseason only - "
            "it has no preseason equivalent, and net_points_team (not shown here) only ever covers "
            "the single current season, not full history."
        )
    if any_net_points_daily:
        notes.append(
            "np_gm = per-game NetPoints player rows (net_points_player_game), fetched only with "
            "--include-net-points-daily - a player row is dropped (not zero, just absent) if their "
            "display name couldn't be matched unambiguously to a local player."
        )
    if notes:
        print("\n" + "\n".join(notes))
