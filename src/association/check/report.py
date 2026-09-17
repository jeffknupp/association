"""`association data check`: report local data coverage, optionally cross-checked
against ESPN's live schedule to show exactly what games are missing.

Local-only (no network) by default - pass --live to also cross-check against
ESPN's schedule and report expected game counts, not just what's on disk.

A season+season_type that `pull` has already verified complete (postponed/
canceled games included) gets its local `_complete` marker trusted here too -
no live schedule call needed, since a finished season's schedule can't change.
Pass --force to re-verify against ESPN anyway.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any, NamedTuple

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
    """Seasons with data on disk, so a plain ``data check`` reports on what is
    actually there rather than on a hardcoded range."""
    games_dir = data_dir / "games"
    if not games_dir.exists():
        return []
    seasons = set()
    for p in games_dir.glob("season=*"):
        with contextlib.suppress(ValueError):
            seasons.add(int(p.name.split("=", 1)[1]))
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
        with contextlib.suppress(ValueError):
            types.add(int(p.name.split("=", 1)[1]))
    return sorted(types)


def _counts_by(con: duckdb.DuckDBPyConnection, table_dir: Path, *keys: str) -> dict[tuple[object, ...], int]:
    """Row counts for a whole table at once, keyed by ``keys``.

    One scan per table, not one per cell of the report. Each of these trees is
    read in full whichever way it is counted - the files are laid out under
    ``season=X/season_type=Y`` directories but are read raw, not hive
    partitioned (see the note in ``fetch.warehouse``), so a ``WHERE season =
    ...`` prunes no files and saves no I/O. Counting each season/season_type
    with its own filtered query therefore re-read all 40,558 ``games`` files
    once per row of the report: 74 scans at 3.2s each, where the single grouped
    scan they collapse into takes 3.0s.

    Missing keys are absent rather than zero; callers use ``.get(key, 0)``, so
    a season with no rows reports 0 exactly as it did when it was its own
    query returning no matches.
    """
    if not table_dir.exists() or not any(table_dir.rglob("*.parquet")):
        return {}
    glob = str(table_dir / "**" / "*.parquet")
    # Interpolated, not bound: these are column names, and every one of them is
    # a literal in the caller below, never anything read off disk or the CLI.
    cols = ", ".join(keys)
    rows = con.execute(f"SELECT {cols}, count(*) FROM read_parquet(?, union_by_name=true) GROUP BY {cols}", [glob]).fetchall()
    return {tuple(r[:-1]): r[-1] for r in rows}


def _resolved_count(data_dir: Path, season: int, season_type: int) -> int:
    # Games ESPN settled as postponed/canceled - never played, but not "missing"
    # either. Directory listing only, same cheap check pipeline.py's own
    # completeness logic uses.
    d = data_dir / "_resolved" / f"season={season}" / f"season_type={season_type}"
    return sum(1 for _ in d.glob("*.marker")) if d.exists() else 0


def _run_check_scope(data_dir: Path, seasons: list[int] | None, season_types: list[int] | None) -> tuple[list[int], list[int]] | None:
    """Resolves ``seasons``/``season_types`` from disk when not given, printing
    guidance and signaling a bail-out (``None``) when there is nothing local to
    report on. Order matters: seasons is checked (and its own guidance printed)
    before season_types is even discovered, exactly as the inline version did."""
    if not seasons:
        seasons = discover_seasons(data_dir)
    if not seasons:
        print(f"No local data found under {data_dir}. Run `association data pull` first.")
        return None
    if not season_types:
        season_types = discover_season_types(data_dir)
    if not season_types:
        print(f"No local season-type data found under {data_dir}. Run `association data pull` first.")
        return None
    return seasons, season_types


class _RunCheckCounts(NamedTuple):
    """The seven whole-table row counts ``run_check`` scans once up front and
    then indexes into per report row - see ``_counts_by`` for why one scan
    covers every row instead of one query per cell."""

    standings: dict[tuple[Any, ...], int]
    bpi: dict[tuple[Any, ...], int]
    games: dict[tuple[Any, ...], int]
    tss: dict[tuple[Any, ...], int]
    shots: dict[tuple[Any, ...], int]
    net_points_daily: dict[tuple[Any, ...], int]
    net_points: dict[tuple[Any, ...], int]


def _run_check_gather_counts(con: duckdb.DuckDBPyConnection, data_dir: Path) -> _RunCheckCounts:
    # Counted up front, one scan per table, and indexed into per row below.
    # standings and team_power_index are per-season only - they carry no
    # season_type column at all, so grouping by one would not bind.
    # net_points_player keys off its own string label rather than the numeric
    # 2/3 the rest of these use (see NET_POINTS_TYPE_LABEL).
    return _RunCheckCounts(
        standings=_counts_by(con, data_dir / "standings", "season"),
        bpi=_counts_by(con, data_dir / "team_power_index", "season"),
        games=_counts_by(con, data_dir / "games", "season", "season_type"),
        tss=_counts_by(con, data_dir / "team_season_stats", "season", "season_type"),
        shots=_counts_by(con, data_dir / "shot_chart", "season", "season_type"),
        net_points_daily=_counts_by(con, data_dir / "net_points_player_game", "season", "season_type"),
        net_points=_counts_by(con, data_dir / "net_points_player", "season", "net_points_season_type"),
    )


def _run_check_games_str(pipeline: Pipeline, team_ids: list[str], season: int, season_type: int, have: int, is_cached_complete: bool, live: bool, force: bool) -> tuple[str, bool]:
    """The "games (have/expected)" cell, and whether it was served from the
    cached completion marker rather than a live schedule call (for the
    caller's ``any_cached`` footnote)."""
    if is_cached_complete and not force:
        # A completed season's schedule can't change - trust the marker
        # pull already verified, skip the live schedule call entirely.
        return f"{have}/{have}*", True
    if live:
        expected = len(pipeline.event_ids_for(season, season_type, team_ids))
        return f"{have}/{expected}", False
    return f"{have}/?", False


def _run_check_net_points_cell(net_points: dict[tuple[Any, ...], int], season: int, season_type: int) -> tuple[int, bool]:
    """The net_pts cell, and whether this season_type has a NetPoints label at
    all (for the caller's ``any_net_points`` footnote)."""
    label = NET_POINTS_TYPE_LABEL.get(season_type)
    net_pts = net_points.get((season, label), 0) if label is not None else 0
    return net_pts, season_type in NET_POINTS_TYPE_LABEL


def _run_check_row(
    data_dir: Path,
    pipeline: Pipeline,
    team_ids: list[str],
    season: int,
    season_type: int,
    live: bool,
    force: bool,
    counts: _RunCheckCounts,
    widths: list[int],
) -> tuple[bool, bool, bool]:
    """Prints one (season, season_type) report row and returns which
    footnotes it triggers: (used the cached marker, has a NetPoints count,
    has a daily NetPoints count)."""
    marker = data_dir / "_complete" / f"season={season}" / f"season_type={season_type}.marker"
    is_cached_complete = marker.exists()
    complete = "yes" if is_cached_complete else "no"

    have_games = counts.games.get((season, season_type), 0)
    resolved = _resolved_count(data_dir, season, season_type)
    have = have_games + resolved  # played + confirmed-never-played both count as "accounted for"

    games_str, used_cached_marker = _run_check_games_str(pipeline, team_ids, season, season_type, have, is_cached_complete, live, force)

    tss_count = counts.tss.get((season, season_type), 0)
    players = len(pipeline.athlete_ids_for(season, season_type))
    shots = counts.shots.get((season, season_type), 0)
    net_pts, has_net_points = _run_check_net_points_cell(counts.net_points, season, season_type)
    net_pts_daily = counts.net_points_daily.get((season, season_type), 0)

    row = [
        str(season),
        SEASON_TYPE_NAME.get(season_type, str(season_type)),
        complete,
        games_str,
        str(counts.standings.get((season,), 0)),
        str(tss_count),
        str(counts.bpi.get((season,), 0)),
        str(players),
        str(shots),
        str(net_pts),
        str(net_pts_daily),
    ]
    print(" ".join(c.rjust(w) for c, w in zip(row, widths, strict=True)))
    return used_cached_marker, has_net_points, bool(net_pts_daily)


def _run_check_notes(any_cached: bool, live: bool, any_net_points: bool, any_net_points_daily: bool) -> list[str]:
    """Footnote lines describing which columns carry a caveat this run, if
    any."""
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
    return notes


def run_check(
    data_dir: Path,
    seasons: list[int] | None,
    season_types: list[int] | None,
    rate_limit: float = 5.0,
    live: bool = False,
    force: bool = False,
) -> None:
    """Report coverage per season and season type.

    Offline by default: compares what is on disk against local checkpoints.
    With ``live``, cross-checks against ESPN's own schedule, which is the real
    test of whether a pull is complete. Postponed, canceled and forfeited
    games are accounted for rather than counted as gaps.
    """
    data_dir = Path(data_dir)
    scope = _run_check_scope(data_dir, seasons, season_types)
    if scope is None:
        return
    seasons, season_types = scope

    con = duckdb.connect(":memory:")
    client = ESPNClient(rate_limit=rate_limit) if live else None
    pipeline = Pipeline(client, data_dir)
    team_ids = pipeline.team_ids() if live else []

    counts = _run_check_gather_counts(con, data_dir)

    cols = ["season", "type", "complete", "games (have/expected)", "standings", "team_stats", "bpi", "players", "shots", "net_pts", "np_gm"]
    widths = [6, 10, 8, 23, 9, 10, 4, 7, 8, 7, 6]
    print(" ".join(c.rjust(w) for c, w in zip(cols, widths, strict=True)))
    print("-" * (sum(widths) + len(widths) - 1))

    any_cached = False
    any_net_points = False
    any_net_points_daily = False
    for season in seasons:
        for season_type in season_types:
            used_cached_marker, has_net_points, has_net_points_daily = _run_check_row(data_dir, pipeline, team_ids, season, season_type, live, force, counts, widths)
            if used_cached_marker:
                any_cached = True
            if has_net_points:
                any_net_points = True
            if has_net_points_daily:
                any_net_points_daily = True

    con.close()
    notes = _run_check_notes(any_cached, live, any_net_points, any_net_points_daily)
    if notes:
        print("\n" + "\n".join(notes))
