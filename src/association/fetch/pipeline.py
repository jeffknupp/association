"""Orchestrates the fetch: seasons -> teams -> games -> players, with
existence-check resumability at every unit of work, plus a coarser O(1)
completion marker per (season, season_type) so a fully-fetched, finished
scope never has to re-derive its own completeness on a later run.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date
from datetime import timedelta as _timedelta
from pathlib import Path
from typing import Any, Protocol, TypeVar

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
from curl_cffi import requests as cf_requests
from tqdm import tqdm

from association.season import current_season

from . import endpoints, parse, storage
from .netpoints_client import NetPointsDailyClient

log: logging.Logger = logging.getLogger("association.fetch.pipeline")

T = TypeVar("T")

NET_POINTS_FIRST_SEASON = 2019
"""Earliest season espnanalytics.com's NetPoints covers (the 2018-19 season).

Anything older is not missing data to be fetched later - it does not exist, and
the bucket answers 403 for it. Used to decide whether a pull needs the NetPoints
files at all.

.. versionadded:: 1.5.0
"""


class JsonFetcher(Protocol):
    """What the pipeline needs from an HTTP client: one method.

    Declared structurally rather than as :class:`~association.fetch.client.ESPNClient`
    because that is the honest dependency - the pipeline never touches
    throttling, retries or TLS impersonation, only ``get_json`` - and because a
    test double should not have to inherit a network client to stand in for one.
    """

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        """Fetch and decode one JSON document."""
        ...


class DailyNetPointsFetcher(Protocol):
    """What the pipeline needs from the per-game NetPoints client."""

    def get_daily(self, date: str, season_folder: int) -> dict[str, Any] | None:
        """One date's NetPoints, or None when that date has no file."""
        ...

    def get_daily_players(self, date: str, season_folder: int) -> list[dict[str, Any]] | None:
        """One date's per-player, per-action-type NetPoints, or None."""
        ...


class Pipeline:
    """Drives a full fetch: teams, schedules, games, players, aggregates.

    Every step is checkpointed through :mod:`association.fetch.storage`, so a run
    resumes where the last one stopped and interrupting is safe. ``force``
    ignores those checkpoints.
    """

    def __init__(
        self,
        client: JsonFetcher | None,
        data_dir: Path,
        include_pbp: bool = False,
        include_net_points_daily: bool = False,
        force: bool = False,
        workers: int = 1,
    ):
        # client may be None for local-only use (e.g. `data check --offline`,
        # which never calls a network-touching method like event_ids_for).
        self.client = client
        self.root: Path = Path(data_dir)
        self.include_pbp = include_pbp
        self.include_net_points_daily = include_net_points_daily
        self.force = force
        self.workers: int = max(1, workers)
        self.glossary: dict[str, dict[str, Any]] = {}
        self._net_points_daily_client: DailyNetPointsFetcher | None = None
        # Guards `written` and `glossary`, both of which every worker mutates.
        # set.add is atomic under the GIL; the glossary's read-modify-write is
        # not, and one lock for both is simpler than reasoning about which is.
        self._state_lock = threading.Lock()
        self.written: set[str] = set()
        """Warehouse tables this run actually wrote a Parquet file for.

        The warehouse is rebuilt per table from the whole Parquet tree, which
        for this dataset means rescanning 126,000 files and several minutes -
        so a run that fetched nothing must be able to say so and skip it. Every
        write goes through :meth:`_write_rows`, which records the table here,
        rather than calling ``storage`` directly.
        """

    @property
    def _live_client(self) -> JsonFetcher:
        """Every network-touching method goes through this instead of self.client
        directly - a clear error beats an AttributeError if a Pipeline built for
        local-only use (client=None) ever has a network method called on it."""
        if self.client is None:
            raise RuntimeError("This Pipeline has no HTTP client (constructed for local-only use) - can't make network requests.")
        return self.client

    def _p(self, *parts: str | int) -> Path:
        return self.root.joinpath(*[str(p) for p in parts])

    def _exists(self, path: Path) -> bool:
        return storage.exists(path) and not self.force

    def _write_rows(self, path: Path, rows: list[dict[str, Any]]) -> None:  # noqa: D401
        """Write rows and record which table they belong to.

        The table name is the first path component under the root, which is how
        the Parquet tree is laid out and how warehouse.build globs it - deriving
        it here rather than passing it in means a new fetch method cannot forget
        to declare what it wrote, and a stale warehouse is invisible until
        someone queries it and gets an old answer.
        """
        if not rows:
            # storage.write_rows writes nothing for an empty list, so recording
            # the table would schedule a rebuild for a file that never appeared.
            return
        storage.write_rows(path, rows)
        with self._state_lock:
            self.written.add(path.relative_to(self.root).parts[0])

    def _write_row(self, path: Path, row: dict[str, Any]) -> None:
        """One-record counterpart to :meth:`_write_rows`."""
        self._write_rows(path, [row])

    def _add_glossary(self, rows: list[dict]) -> None:
        with self._state_lock:
            for r in rows:
                key = r.get("stat_key")
                if key and key not in self.glossary:
                    self.glossary[key] = r

    def _map(self, work: Callable[[T], None], items: list[T], desc: str, leave: bool = True) -> None:
        """Run `work` over `items`, with `self.workers` of them in flight.

        Serial when workers == 1, down to the same loop this replaced - the
        common path stays free of a pool, and so does every test.

        Requests here are latency-bound, not bandwidth- or CPU-bound: profiling
        a live pull put 96% of the main thread inside one curl call, at 2.4
        requests/second against a `--rate-limit` of 10 that never once had to
        sleep. The work is I/O under the GIL, so threads are the right tool and
        the rate limiter (shared, in ESPNClient) still bounds what ESPN sees.

        An exception from any item propagates, as it did serially, after the
        pool is torn down.
        """
        if self.workers == 1 or len(items) < 2:
            for item in tqdm(items, desc=desc, leave=leave):
                work(item)
            return
        with ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="fetch") as pool:
            # map, not submit+as_completed: it re-raises the first exception in
            # submission order, which keeps "the first failure aborts the pull"
            # true regardless of how the work interleaves.
            for _ in tqdm(pool.map(work, items), total=len(items), desc=desc, leave=leave):
                pass

    # ---------------- teams ----------------
    def fetch_teams(self) -> None:
        """Fetch the team list - the root of everything else, since schedules and
        ids are keyed off it."""
        path = self._p("teams", "teams.parquet")
        if self._exists(path):
            return
        data = self._live_client.get_json(endpoints.teams_url(), params={"limit": 50})
        rows = parse.parse_teams(data)
        self._write_rows(path, rows)

    def team_ids(self) -> list[str]:
        """Every team id, read back from the teams file on disk."""
        path = self._p("teams", "teams.parquet")
        if not storage.exists(path):
            self.fetch_teams()
        if not storage.exists(path):
            return []
        table = pq_read(path, columns=["team_id"])
        return [str(v) for v in table.column("team_id").to_pylist()]

    def team_abbr_to_id(self) -> dict[str, str]:
        """Abbreviation to team id, for sources that identify teams by abbreviation."""
        path = self._p("teams", "teams.parquet")
        if not storage.exists(path):
            self.fetch_teams()
        if not storage.exists(path):
            return {}
        table = pq_read(path, columns=["team_id", "abbreviation"])
        return {str(abbr): str(team_id) for team_id, abbr in zip(table.column("team_id").to_pylist(), table.column("abbreviation").to_pylist(), strict=True)}

    # ---------------- schedule -> event ids ----------------
    def event_ids_for(self, season: int, season_type: int, team_ids: list[str]) -> list[str]:
        """Every game id in a season, gathered from each team's schedule and
        deduplicated - each game appears on two schedules."""
        ids: set[str] = set()
        for team_id in tqdm(team_ids, desc=f"{season} type={season_type} schedules", leave=False):
            data = self._live_client.get_json(
                endpoints.team_schedule_url(team_id),
                params={"season": season, "seasontype": season_type},
            )
            ids.update(parse.parse_schedule_event_ids(data))
        return sorted(ids, key=lambda x: int(x))

    # ---------------- games / box scores ----------------
    def fetch_game(self, event_id: str, season: int, season_type: int) -> None:
        """Fetch one game's summary and write every table it yields: box scores,
        and play-by-play derivatives when ``include_pbp`` is set."""
        game_path = self._p("games", f"season={season}", f"season_type={season_type}", f"event_{event_id}.parquet")
        resolved_marker = self._resolved_marker(season, season_type, event_id)
        if (self._exists(game_path) or storage.is_complete(resolved_marker)) and not self.force:
            return

        data = self._live_client.get_json(endpoints.summary_url(), params={"event": event_id})
        parsed = parse.parse_game_summary(data, season, season_type)
        game_row = parsed["game"]
        if game_row is None:
            # Missing/malformed response - don't checkpoint, retry next run.
            return
        if not game_row.get("status_completed"):
            if game_row.get("status_state") == "post":
                # Terminally resolved but never played (postponed/cancelled/forfeited) -
                # there's no box score to store, but it's settled, not pending. Mark it
                # so we stop re-fetching this event every run and so the season-level
                # completion check (which compares against ESPN's full schedule count)
                # can still close out.
                storage.mark_complete(resolved_marker)
            # else: still pending (future/in-progress) - don't checkpoint, retry next run.
            return

        self._write_row(game_path, game_row)
        self._write_rows(
            self._p(
                "player_box_stats",
                f"season={season}",
                f"season_type={season_type}",
                f"event_{event_id}.parquet",
            ),
            parsed["player_box"],
        )
        self._write_rows(
            self._p("team_box_stats", f"season={season}", f"season_type={season_type}", f"event_{event_id}.parquet"),
            parsed["team_box"],
        )
        if self.include_pbp:
            self._write_rows(
                self._p("plays", f"season={season}", f"season_type={season_type}", f"event_{event_id}.parquet"),
                parsed["plays"],
            )
            self._write_rows(
                self._p("shot_chart", f"season={season}", f"season_type={season_type}", f"event_{event_id}.parquet"),
                parsed["shot_chart"],
            )
            self._write_rows(
                self._p(
                    "win_probability",
                    f"season={season}",
                    f"season_type={season_type}",
                    f"event_{event_id}.parquet",
                ),
                parsed["win_probability"],
            )
        self._add_glossary(parsed["glossary"])
        for athlete_id, bio in parsed["players_seen"].items():
            self._cache_player_bio(athlete_id, bio)

    def _cache_player_bio(self, athlete_id: str, bio: dict) -> None:
        path = self._p("players", f"athlete_{athlete_id}.parquet")
        if storage.exists(path):
            return
        self._write_row(path, bio)

    def _resolved_marker(self, season: int, season_type: int, event_id: str) -> Path:
        return self._p("_resolved", f"season={season}", f"season_type={season_type}", f"event_{event_id}.marker")

    def _resolved_event_count(self, season: int, season_type: int) -> int:
        # Directory listings only - no file content reads. Cheap even for a
        # season with 1000+ small per-game files. Counts both games that were
        # actually played (games/*.parquet) and ones ESPN settled as never
        # happening (postponed/cancelled - _resolved/*.marker), since both are
        # "nothing left to do here", just with different outcomes.
        games_dir = self._p("games", f"season={season}", f"season_type={season_type}")
        resolved_dir = self._p("_resolved", f"season={season}", f"season_type={season_type}")
        count = sum(1 for _ in games_dir.glob("*.parquet")) if games_dir.exists() else 0
        count += sum(1 for _ in resolved_dir.glob("*.marker")) if resolved_dir.exists() else 0
        return count

    # ---------------- player season stats ----------------
    def athlete_ids_for(self, season: int, season_type: int) -> list[str]:
        """Every player who appeared in a season, taken from the box scores already
        fetched rather than from a roster endpoint."""
        path = self._p("player_box_stats", f"season={season}", f"season_type={season_type}")
        if not path.exists():
            return []
        dataset = ds.dataset(str(path), format="parquet")
        table = dataset.to_table(columns=["athlete_id"])
        ids = {str(v) for v in table.column("athlete_id").to_pylist() if v is not None}
        return sorted(ids, key=lambda x: int(x))

    def fetch_player_season_stats(self, athlete_id: str, season_type: int, force_refresh: bool = False) -> None:
        """One file covers this player's WHOLE career for a season_type (ESPN's
        endpoint isn't scoped to a single season), so re-fetching it during an
        in-progress season (force_refresh=True, from _run_season_type below)
        naturally picks up that season's latest numbers along with everything
        else - no separate per-season key needed."""
        path = self._p("player_season_stats", f"athlete_{athlete_id}_type_{season_type}.parquet")
        if storage.exists(path) and not self.force and not force_refresh:
            return
        data = self._live_client.get_json(endpoints.player_career_stats_url(athlete_id), params={"seasontype": season_type})
        rows, glossary = parse.parse_player_career_stats(data, athlete_id, season_type)
        self._add_glossary(glossary)
        self._write_rows(path, rows)

    # ---------------- team season stats ----------------
    def fetch_team_season_stats(self, season: int, season_type: int, team_id: str, force_refresh: bool = False) -> None:
        """One team's season aggregate. ``force_refresh`` re-fetches even when the
        file exists, which is how an in-progress season stays current."""
        if season_type == 1:
            # ESPN has no team season stats for preseason - confirmed live: the
            # endpoint returns no data for every team/season checked. There's
            # never a resulting file to skip against on resumability grounds
            # alone, so without this guard every run re-hits the network for
            # nothing. Skip before the request, not after.
            return
        path = self._p("team_season_stats", f"season={season}", f"season_type={season_type}", f"team_{team_id}.parquet")
        if storage.exists(path) and not self.force and not force_refresh:
            return
        data = self._live_client.get_json(endpoints.team_season_stats_url(season, season_type, team_id))
        row, glossary = parse.parse_team_season_stats(data, season, season_type, team_id)
        self._add_glossary(glossary)
        if row:
            self._write_row(path, row)

    # ---------------- standings ----------------
    def fetch_standings(self, season: int) -> None:
        """Standings change daily while a season is in progress - re-fetch (one
        cheap request, all 30 teams in one call) every run for the current
        season rather than freezing it after the first pull. A season that's
        already over keeps its resumability skip as before. This can keep
        re-fetching a season that's technically finished but hasn't rolled
        over to the next one yet (this project's season-ends convention holds
        a season "current" through the following off-season, until October) -
        harmless, since it's a single request either way."""
        path = self._p("standings", f"season={season}", "standings.parquet")
        if storage.exists(path) and not self.force and season < current_season():
            return
        data = self._live_client.get_json(endpoints.standings_url(), params={"season": season})
        rows, glossary = parse.parse_standings(data, season)
        self._add_glossary(glossary)
        self._write_rows(path, rows)

    # ---------------- power index (BPI) ----------------
    def fetch_power_index(self, season: int) -> None:
        """Same reasoning as fetch_standings above - BPI/projected wins shift
        every game of an in-progress season."""
        path = self._p("team_power_index", f"season={season}", "power_index.parquet")
        if storage.exists(path) and not self.force and season < current_season():
            return
        data = self._live_client.get_json(endpoints.power_index_url(season))
        rows, glossary = parse.parse_power_index(data)
        self._add_glossary(glossary)
        self._write_rows(path, rows)

    # ---------------- NetPoints (espnanalytics.com) ----------------
    def _net_points_wanted(self, seasons: list[int]) -> set[int]:
        """The requested seasons NetPoints could have data for at all."""
        return {season for season in seasons if season >= NET_POINTS_FIRST_SEASON}

    def _net_points_needed(self, seasons: list[int]) -> bool:
        """Whether the NetPoints flat files are worth downloading this run.

        They are single league-wide downloads covering every season at once, so
        there is no per-season request to skip - the choice is to fetch all of
        it or none. Previously it was always fetched, which meant a pull of one
        finished season still spent a download, a parse of every season's rows,
        and a rewrite of the CURRENT season's files - and that rewrite then
        made the warehouse rebuild non-empty, turning an "everything is already
        complete" run into a multi-minute one.

        Fetch when any requested season is the current one (values shift daily
        while it is in progress) or has no player file yet. A finished season
        already on disk needs nothing.
        """
        wanted = self._net_points_wanted(seasons)
        if not wanted:
            return False
        if self.force:
            return True
        # Only the player file is checked. The team feed carries the current
        # season alone, so requiring a file per season there would make this
        # true forever for every historical season.
        return any(season >= current_season() or not any(self._p("net_points_player", f"season={season}").glob("*.parquet")) for season in wanted)

    def fetch_net_points(self, seasons: list[int]) -> None:
        """Season-level NetPoints for the requested seasons.

        Both files are single flat downloads (not parameterized by season or
        team), so there's no per-unit network call to skip the way there is for
        games/player-stats. What IS skippable is the whole download - see
        :meth:`_net_points_needed` - and everything outside the seasons the run
        was asked for: a pull of 2024 has no business rewriting 2026's file.

        Within the requested seasons, a file is only overwritten if it is
        missing or still the current season (values shift daily while a season
        is in progress - same reasoning as fetch_standings/fetch_power_index
        above; respects --force same as everywhere else).

        .. versionchanged:: 1.5.0
           Takes the seasons being pulled, and writes only those. Previously it
           fetched and wrote every season the source carried, on every run.
        """
        wanted = self._net_points_wanted(seasons)
        if not self._net_points_needed(seasons):
            log.info("NetPoints already on disk for %s - skipping", ", ".join(str(s) for s in sorted(wanted)) or "the requested seasons")
            return
        team_abbr_to_id = self.team_abbr_to_id()

        player_data = self._live_client.get_json(endpoints.net_points_player_url())
        rate_data = self._live_client.get_json(endpoints.net_points_player_100_url())
        player_rows = parse.parse_net_points_player(player_data, team_abbr_to_id, rate_data)
        by_season_type: dict[tuple[int, str], list[dict]] = {}
        for row in player_rows:
            season, season_type = row["season"], row["net_points_season_type"]
            if season not in wanted:
                continue
            by_season_type.setdefault((season, season_type), []).append(row)
        for (season, season_type), rows in by_season_type.items():
            slug = season_type.lower().replace(" ", "_")
            path = self._p("net_points_player", f"season={season}", f"{slug}.parquet")
            if storage.exists(path) and not self.force and season < current_season():
                continue
            self._write_rows(path, rows)

        team_data = self._live_client.get_json(endpoints.net_points_team_url())
        team_rows = parse.parse_net_points_team(team_data, team_abbr_to_id)
        by_season: dict[int, list[dict]] = {}
        for row in team_rows:
            if row["season"] not in wanted:
                continue
            by_season.setdefault(row["season"], []).append(row)
        for season, rows in by_season.items():
            path = self._p("net_points_team", f"season={season}", "net_points_team.parquet")
            if storage.exists(path) and not self.force and season < current_season():
                continue
            self._write_rows(path, rows)

    def fetch_net_points_fingerprint(self, season: int) -> None:
        """espnanalytics.com's "Net Pts Fingerprint" page - a per-player
        breakdown by shot/play type (2pt, 3pt, driving, fastbreak, rebound,
        turnover, ...), each split into offense/defense/total NetPoints. One
        file per season, same public bucket as fetch_net_points above but
        parameterized by season this time. Same in-season-refresh reasoning
        as fetch_standings/fetch_power_index. A season with no file yet
        (hasn't started, or older than NetPoints' 2018-19 floor) returns 403
        from this bucket - confirmed live, unlike every espn.com endpoint's
        404/400 - so that's caught here and treated as no data rather than
        left to raise."""
        path = self._p("net_points_player_fingerprint", f"season={season}", "fingerprint.parquet")
        if storage.exists(path) and not self.force and season < current_season():
            return
        try:
            data = self._live_client.get_json(endpoints.net_points_fingerprint_url(season - 1))
        except cf_requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 403:
                return
            raise
        if not data:
            return
        rows = parse.parse_net_points_fingerprint(data, self.team_abbr_to_id(), self._name_to_athlete_id())
        if rows:
            self._write_rows(path, rows)

    def _net_points_game_index(self) -> parse.NetPointsGameIndex:
        """The index NetPoints' per-date rows resolve their event_id through,
        built from games already on disk - NetPoints has no ESPN ids anywhere
        in its own data, so the game comes from (team, date) against ours.
        :class:`~association.fetch.parse.NetPointsGameIndex` owns the rule for
        turning ESPN's UTC tip into the date NetPoints named its file for.

        .. versionchanged:: 2.1.0
           Returns an index rather than a ``(team_id, date) -> game`` dict.
           The dict was keyed on the UTC date, which is not the date NetPoints
           labels with and which two of a team's games can share.
        """
        path = self._p("games")
        if not path.exists():
            return parse.NetPointsGameIndex([])
        table = ds.dataset(str(path), format="parquet").to_table(columns=["event_id", "season", "season_type", "date", "home_team_id", "away_team_id"])
        return parse.NetPointsGameIndex(
            zip(
                table.column("event_id").to_pylist(),
                table.column("season").to_pylist(),
                table.column("season_type").to_pylist(),
                table.column("date").to_pylist(),
                table.column("home_team_id").to_pylist(),
                table.column("away_team_id").to_pylist(),
                strict=True,
            )
        )

    def _name_to_athlete_id(self) -> dict[str, str]:
        """display_name -> athlete_id, built from players already on disk.
        A name shared by more than one locally-known player is dropped
        entirely rather than guessed - NetPoints' per-game files have no
        athlete_id of their own, only a display name to match against."""
        path = self._p("players")
        if not path.exists():
            return {}
        table = ds.dataset(str(path), format="parquet").to_table(columns=["athlete_id", "display_name"])
        by_name: dict[str, set[str]] = {}
        for athlete_id, name in zip(table.column("athlete_id").to_pylist(), table.column("display_name").to_pylist(), strict=True):
            if name:
                by_name.setdefault(name, set()).add(str(athlete_id))
        return {name: next(iter(ids)) for name, ids in by_name.items() if len(ids) == 1}

    def _net_points_dates_and_seasons(self) -> dict[str, int]:
        """NetPoints label -> project season, for every date with at least one
        local ESPN game, PLUS each such date minus one day. Daily data is
        fetched only for dates already covered, not over the source's history.

        The "minus one day" half is required, not an optimization: a game's
        NetPoints label is usually its ESPN date minus one (the UTC-vs-local
        offset - see NetPointsGameIndex), and a label date with no OTHER
        local game of its own would otherwise never be fetched. That missed a
        Lakers game entirely, rather than merely mislabelling it. The label's
        season is reused from its parent date unless independently known (a
        season boundary); reusing it costs at worst one empty S3 key."""
        path = self._p("games")
        if not path.exists():
            return {}
        table = ds.dataset(str(path), format="parquet").to_table(columns=["date", "season"])
        result: dict[str, int] = {}
        for date, season in zip(table.column("date").to_pylist(), table.column("season").to_pylist(), strict=True):
            if date:
                result[str(date)[:10]] = int(season)
        for date, season in list(result.items()):
            prior_day = (_date.fromisoformat(date) - _timedelta(days=1)).isoformat()
            result.setdefault(prior_day, season)
        # NetPoints starts at 2018-10-16 and the bucket answers 403 for
        # everything before it, so a date below the floor is a request that
        # cannot succeed. Measured against this warehouse: 7,835 dates have a
        # local game and 1,769 are in range, so without this three quarters of
        # the requests a fresh pull makes are spent being refused.
        return {date: season for date, season in result.items() if season >= NET_POINTS_FIRST_SEASON}

    def fetch_net_points_daily(self) -> None:
        """Opt-in (--include-net-points-daily): S3 requests per date this
        project has local ESPN games for (not per player, and not per game -
        NetPoints publishes per date, covering every game played that day).
        Needs a different, signed-request client than the rest of this
        pipeline - see netpoints_client.py for why.

        TWO objects per date, checkpointed separately. They are published
        together, so one marker would be simpler - but the play-type file was
        added to this pipeline long after the first, and a shared marker would
        have made every date already on disk look complete and skip it forever.
        Separate markers mean a pull that has the box-score half backfills only
        the half it is missing, at one request per date instead of two.

        Runs ``--workers`` dates at once, like the rest of the pipeline: these
        are S3 round trips, so the loop was latency-bound and spent its time
        waiting (measured, on the tail of a re-derivation: 12 dates a second
        against 0.3 serially). The three lookups the workers share are built
        before the pool and never written to afterwards; the writes go through
        :meth:`_write_rows`, which takes ``_state_lock``, and ``storage``,
        which gives every temp file a unique name.

        .. versionchanged:: 2.1.0
           Also fetches the per-game play-type split into
           ``net_points_player_game_fingerprint``, and fetches dates
           concurrently rather than one at a time.
        """
        if self._net_points_daily_client is None:
            self._net_points_daily_client = NetPointsDailyClient()

        team_abbr_to_id = self.team_abbr_to_id()
        game_index = self._net_points_game_index()
        name_to_athlete_id = self._name_to_athlete_id()
        dates_and_seasons = self._net_points_dates_and_seasons()

        client = self._net_points_daily_client

        def one_date(date: str) -> None:
            """One date's two objects, each behind its own marker - the unit
            of work a worker takes, and the unit a re-run skips."""
            season = dates_and_seasons[date]
            # A date whose rows all fail to resolve writes no parquet at all
            # (write_rows([]) is a no-op), so a MARKER rather than file
            # existence is what stops it being re-fetched forever - the same
            # fix already applied to preseason team-stats and postponed games.
            box_marker = self._p("_net_points_daily_done", f"date={date}.marker")
            if self.force or not storage.is_complete(box_marker):
                data = client.get_daily(date, season_folder=season - 1)
                player_rows, team_rows = parse.parse_net_points_daily(data, date, team_abbr_to_id, game_index, name_to_athlete_id)
                self._write_rows(self._p("net_points_player_game", f"season={season}", f"date={date}.parquet"), player_rows)
                self._write_rows(self._p("net_points_team_game", f"season={season}", f"date={date}.parquet"), team_rows)
                storage.mark_complete(box_marker)

            skill_marker = self._p("_net_points_daily_skills_done", f"date={date}.marker")
            if self.force or not storage.is_complete(skill_marker):
                skill_rows = parse.parse_net_points_daily_players(
                    client.get_daily_players(date, season_folder=season - 1),
                    date,
                    team_abbr_to_id,
                    game_index,
                    name_to_athlete_id,
                )
                self._write_rows(self._p("net_points_player_game_fingerprint", f"season={season}", f"date={date}.parquet"), skill_rows)
                storage.mark_complete(skill_marker)

        self._map(one_date, sorted(dates_and_seasons), desc="NetPoints daily")

    # ---------------- glossary ----------------
    def write_glossary(self) -> None:
        """Write the stat glossary, so a column name can be explained without
        guessing at what it means.

        Merged with what is already on disk rather than replacing it. A run only
        collects glossary entries from the endpoints it actually fetched, so a
        pull that skipped everything already checkpointed would otherwise
        overwrite a full glossary with the handful of keys that run happened to
        see - confirmed live, a current-season pull cut it from 140 keys to 94,
        and the keys it dropped were the box-score ones nothing was going to
        re-derive.

        Unchanged content is not rewritten, so a pull with nothing new to say
        leaves the file (and therefore the warehouse) alone.

        .. versionchanged:: 1.5.0
           Merges with the glossary on disk instead of replacing it.
        """
        path = self._p("stat_glossary", "stat_glossary.parquet")
        existing: dict[str, dict[str, Any]] = {}
        if storage.exists(path):
            for row in pq.read_table(path).to_pylist():
                key = row.get("stat_key")
                if key:
                    existing[str(key)] = row
        merged = {**existing, **self.glossary}
        if not merged or merged == existing:
            return
        self._write_rows(path, list(merged.values()))

    # ---------------- completion marker ----------------
    def _complete_marker(self, season: int, season_type: int) -> Path:
        return self._p("_complete", f"season={season}", f"season_type={season_type}.marker")

    # ---------------- one season+type ----------------
    def _run_season_type(self, season: int, season_type: int, team_ids: list[str]) -> None:
        marker = self._complete_marker(season, season_type)
        if not self.force and storage.is_complete(marker):
            # O(1): a single stat() call skips the schedule pull, the whole
            # game loop, the team-stats loop, and the athlete scan below.
            log.info("season %s type %s already complete - skipping", season, season_type)
            return

        event_ids = self.event_ids_for(season, season_type, team_ids)
        self._map(lambda event_id: self.fetch_game(event_id, season, season_type), event_ids, desc=f"{season} type={season_type} games")

        season_complete = bool(event_ids) and self._resolved_event_count(season, season_type) >= len(event_ids)

        # Team/player season stats are league-computed aggregates that shift
        # every game of an in-progress season - force_refresh=not season_complete
        # keeps re-fetching them (overwriting the prior pull) each run until the
        # season type truly is done, instead of freezing them after the first
        # pull the way a per-game fetch correctly does. Running the player loop
        # here too (previously gated behind season_complete, so it never even
        # ran during an in-progress season) is what actually keeps player season
        # stats current while games are still being played.
        self._map(
            lambda team_id: self.fetch_team_season_stats(season, season_type, team_id, force_refresh=not season_complete),
            team_ids,
            desc=f"{season} type={season_type} team season stats",
            leave=False,
        )

        athlete_ids = self.athlete_ids_for(season, season_type)
        self._map(
            lambda athlete_id: self.fetch_player_season_stats(athlete_id, season_type, force_refresh=not season_complete),
            athlete_ids,
            desc=f"{season} type={season_type} player season stats",
            leave=False,
        )

        if not season_complete:
            # Some discovered games haven't been played yet (in-progress season) -
            # leave unmarked so next run re-checks the schedule for new results
            # and keeps refreshing the aggregates above until it is.
            return

        storage.mark_complete(marker)

    # ---------------- top-level run ----------------
    def run(self, seasons: list[int], season_types: list[int]) -> None:
        """Fetch everything for the given seasons and season types.

        Resumable and idempotent: completed scopes are skipped unless the pipeline
        was constructed with ``force``.
        """
        self.fetch_teams()
        team_ids = self.team_ids()
        if not team_ids:
            log.error("No teams returned from ESPN - aborting.")
            return

        self.fetch_net_points(seasons)

        for season in seasons:
            log.info("== season %s ==", season)
            self.fetch_standings(season)
            self.fetch_power_index(season)

            for season_type in season_types:
                self._run_season_type(season, season_type, team_ids)

            # Resolves player names against the local `players` table, so it
            # has to run after this season's games above have populated it -
            # same reasoning as fetch_net_points_daily below.
            self.fetch_net_points_fingerprint(season)

        if self.include_net_points_daily:
            # Must run after the season/type loop above - it resolves against
            # whatever's now in the local games table, so games have to be
            # fetched first for this run's seasons to be there to resolve against.
            self.fetch_net_points_daily()

        self.write_glossary()


def pq_read(path: Path, columns: list[str]) -> pa.Table:
    """Read selected columns from a Parquet file, projecting at read time so a
    wide file costs only the columns actually needed."""
    return pq.read_table(path, columns=columns)
