"""Orchestrates the fetch: seasons -> teams -> games -> players, with
existence-check resumability at every unit of work, plus a coarser O(1)
completion marker per (season, season_type) so a fully-fetched, finished
scope never has to re-derive its own completeness on a later run.
"""

from __future__ import annotations

import logging
from datetime import date as _date
from datetime import timedelta as _timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
from tqdm import tqdm

from . import endpoints, parse, storage
from .client import ESPNClient
from .netpoints_client import NetPointsDailyClient

log = logging.getLogger("association.fetch.pipeline")


class Pipeline:
    def __init__(
        self,
        client: ESPNClient | None,
        data_dir: Path,
        include_pbp: bool = False,
        include_net_points_daily: bool = False,
        force: bool = False,
    ):
        # client may be None for local-only use (e.g. `data check --offline`,
        # which never calls a network-touching method like event_ids_for).
        self.client = client
        self.root = Path(data_dir)
        self.include_pbp = include_pbp
        self.include_net_points_daily = include_net_points_daily
        self.force = force
        self.glossary: dict[str, dict] = {}
        self._net_points_daily_client: NetPointsDailyClient | None = None

    @property
    def _live_client(self) -> ESPNClient:
        """Every network-touching method goes through this instead of self.client
        directly - a clear error beats an AttributeError if a Pipeline built for
        local-only use (client=None) ever has a network method called on it."""
        if self.client is None:
            raise RuntimeError("This Pipeline has no ESPNClient (constructed for local-only use) - can't make network requests.")
        return self.client

    def _p(self, *parts: str | int) -> Path:
        return self.root.joinpath(*[str(p) for p in parts])

    def _exists(self, path: Path) -> bool:
        return storage.exists(path) and not self.force

    def _add_glossary(self, rows: list[dict]) -> None:
        for r in rows:
            key = r.get("stat_key")
            if key and key not in self.glossary:
                self.glossary[key] = r

    # ---------------- teams ----------------
    def fetch_teams(self) -> None:
        path = self._p("teams", "teams.parquet")
        if self._exists(path):
            return
        data = self._live_client.get_json(endpoints.teams_url(), params={"limit": 50})
        rows = parse.parse_teams(data)
        storage.write_rows(path, rows)

    def team_ids(self) -> list[str]:
        path = self._p("teams", "teams.parquet")
        if not storage.exists(path):
            self.fetch_teams()
        if not storage.exists(path):
            return []
        table = pq_read(path, columns=["team_id"])
        return [str(v) for v in table.column("team_id").to_pylist()]

    def team_abbr_to_id(self) -> dict[str, str]:
        path = self._p("teams", "teams.parquet")
        if not storage.exists(path):
            self.fetch_teams()
        if not storage.exists(path):
            return {}
        table = pq_read(path, columns=["team_id", "abbreviation"])
        return {
            str(abbr): str(team_id)
            for team_id, abbr in zip(table.column("team_id").to_pylist(), table.column("abbreviation").to_pylist(), strict=True)
        }

    # ---------------- schedule -> event ids ----------------
    def event_ids_for(self, season: int, season_type: int, team_ids: list[str]) -> list[str]:
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
        game_path = self._p(
            "games", f"season={season}", f"season_type={season_type}", f"event_{event_id}.parquet"
        )
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

        storage.write_row(game_path, game_row)
        storage.write_rows(
            self._p(
                "player_box_stats",
                f"season={season}",
                f"season_type={season_type}",
                f"event_{event_id}.parquet",
            ),
            parsed["player_box"],
        )
        storage.write_rows(
            self._p(
                "team_box_stats", f"season={season}", f"season_type={season_type}", f"event_{event_id}.parquet"
            ),
            parsed["team_box"],
        )
        if self.include_pbp:
            storage.write_rows(
                self._p("plays", f"season={season}", f"season_type={season_type}", f"event_{event_id}.parquet"),
                parsed["plays"],
            )
            storage.write_rows(
                self._p(
                    "shot_chart", f"season={season}", f"season_type={season_type}", f"event_{event_id}.parquet"
                ),
                parsed["shot_chart"],
            )
            storage.write_rows(
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
        storage.write_row(path, bio)

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
        path = self._p("player_box_stats", f"season={season}", f"season_type={season_type}")
        if not path.exists():
            return []
        dataset = ds.dataset(str(path), format="parquet")
        table = dataset.to_table(columns=["athlete_id"])
        ids = {str(v) for v in table.column("athlete_id").to_pylist() if v is not None}
        return sorted(ids, key=lambda x: int(x))

    def fetch_player_season_stats(self, athlete_id: str, season_type: int) -> None:
        path = self._p("player_season_stats", f"athlete_{athlete_id}_type_{season_type}.parquet")
        if self._exists(path):
            return
        data = self._live_client.get_json(
            endpoints.player_career_stats_url(athlete_id), params={"seasontype": season_type}
        )
        rows, glossary = parse.parse_player_career_stats(data, athlete_id, season_type)
        self._add_glossary(glossary)
        storage.write_rows(path, rows)

    # ---------------- team season stats ----------------
    def fetch_team_season_stats(self, season: int, season_type: int, team_id: str) -> None:
        if season_type == 1:
            # ESPN has no team season stats for preseason - confirmed live: the
            # endpoint returns no data for every team/season checked. There's
            # never a resulting file to skip against on resumability grounds
            # alone, so without this guard every run re-hits the network for
            # nothing. Skip before the request, not after.
            return
        path = self._p(
            "team_season_stats", f"season={season}", f"season_type={season_type}", f"team_{team_id}.parquet"
        )
        if self._exists(path):
            return
        data = self._live_client.get_json(endpoints.team_season_stats_url(season, season_type, team_id))
        row, glossary = parse.parse_team_season_stats(data, season, season_type, team_id)
        self._add_glossary(glossary)
        if row:
            storage.write_row(path, row)

    # ---------------- standings ----------------
    def fetch_standings(self, season: int) -> None:
        path = self._p("standings", f"season={season}", "standings.parquet")
        if self._exists(path):
            return
        data = self._live_client.get_json(endpoints.standings_url(), params={"season": season})
        rows, glossary = parse.parse_standings(data, season)
        self._add_glossary(glossary)
        storage.write_rows(path, rows)

    # ---------------- power index (BPI) ----------------
    def fetch_power_index(self, season: int) -> None:
        path = self._p("team_power_index", f"season={season}", "power_index.parquet")
        if self._exists(path):
            return
        data = self._live_client.get_json(endpoints.power_index_url(season))
        rows, glossary = parse.parse_power_index(data)
        self._add_glossary(glossary)
        storage.write_rows(path, rows)

    # ---------------- NetPoints (espnanalytics.com) ----------------
    def fetch_net_points(self) -> None:
        """Both NetPoints files are single flat downloads (not parameterized by
        season/team), so there's no per-unit network call to skip the way there
        is for games/player-stats - always fetch, but only write out a
        season(+type) file that isn't already on disk (respects --force same as
        everywhere else)."""
        team_abbr_to_id = self.team_abbr_to_id()

        player_data = self._live_client.get_json(endpoints.net_points_player_url())
        player_rows = parse.parse_net_points_player(player_data, team_abbr_to_id)
        by_season_type: dict[tuple[int, str], list[dict]] = {}
        for row in player_rows:
            season, season_type = row["season"], row["net_points_season_type"]
            by_season_type.setdefault((season, season_type), []).append(row)
        for (season, season_type), rows in by_season_type.items():
            slug = season_type.lower().replace(" ", "_")
            path = self._p("net_points_player", f"season={season}", f"{slug}.parquet")
            if self._exists(path):
                continue
            storage.write_rows(path, rows)

        team_data = self._live_client.get_json(endpoints.net_points_team_url())
        team_rows = parse.parse_net_points_team(team_data, team_abbr_to_id)
        by_season: dict[int, list[dict]] = {}
        for row in team_rows:
            by_season.setdefault(row["season"], []).append(row)
        for season, rows in by_season.items():
            path = self._p("net_points_team", f"season={season}", "net_points_team.parquet")
            if self._exists(path):
                continue
            storage.write_rows(path, rows)

    def _team_date_to_game(self) -> dict[tuple[str, str], tuple[str, int, int]]:
        """(team_id, date) -> (event_id, season, season_type), built from
        games already on disk - this is how NetPoints' per-date rows resolve
        to an event_id, since NetPoints has no ESPN ids anywhere in its own
        data. A team plays at most one game per real-world date, so this is
        an exact, deterministic join - no name/id-matching heuristic needed
        for games or teams, only for players (see parse.py)."""
        path = self._p("games")
        if not path.exists():
            return {}
        table = ds.dataset(str(path), format="parquet").to_table(
            columns=["event_id", "season", "season_type", "date", "home_team_id", "away_team_id"]
        )
        result: dict[tuple[str, str], tuple[str, int, int]] = {}
        for event_id, season, season_type, date, home_id, away_id in zip(
            table.column("event_id").to_pylist(),
            table.column("season").to_pylist(),
            table.column("season_type").to_pylist(),
            table.column("date").to_pylist(),
            table.column("home_team_id").to_pylist(),
            table.column("away_team_id").to_pylist(),
            strict=True,
        ):
            if not date:
                continue
            date_part = str(date)[:10]
            game = (str(event_id), int(season), int(season_type))
            if home_id is not None:
                result[(str(home_id), date_part)] = game
            if away_id is not None:
                result[(str(away_id), date_part)] = game
        return result

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
        """NetPoints label -> project season, for every date this project
        already has at least one local ESPN game for, PLUS each such date
        minus one day - NetPoints daily data is only fetched for dates
        already covered, not blindly over the source's full history.

        The "minus one day" half is required, not an optimization: a local
        game's true NetPoints label is usually its ESPN date minus one (the
        UTC-vs-local-date offset - see _resolve_net_points_game), and if that
        label date has no OTHER local game of its own, it would otherwise
        never be fetched at all. Confirmed live: a Lakers game with no other
        local game the literal calendar day before it was silently missed
        entirely until this was added, not just given a wrong event_id like
        the back-to-back case. The label's season is reused from its parent
        date unless that label is itself an independently-known date (i.e.
        the rare case of a season boundary) - the only failure mode of
        reusing it is fetching one extra, harmlessly empty S3 key."""
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
        return result

    def fetch_net_points_daily(self) -> None:
        """Opt-in (--include-net-points-daily): one S3 request per date this
        project has local ESPN games for (not per player, and not per game -
        NetPoints publishes one file per date covering every game played that
        day). Needs a different, signed-request client than the rest of this
        pipeline - see netpoints_client.py for why."""
        if self._net_points_daily_client is None:
            self._net_points_daily_client = NetPointsDailyClient()

        team_abbr_to_id = self.team_abbr_to_id()
        team_date_to_game = self._team_date_to_game()
        name_to_athlete_id = self._name_to_athlete_id()
        dates_and_seasons = self._net_points_dates_and_seasons()

        for date in tqdm(sorted(dates_and_seasons), desc="NetPoints daily"):
            season = dates_and_seasons[date]
            # A date whose rows all fail to resolve (e.g. no teams matched)
            # writes no parquet file at all (storage.write_rows([]) is a
            # no-op) - a marker, not file-existence, is what makes that date
            # skip on the next run instead of being re-fetched forever, the
            # same class of fix already applied to preseason team-stats and
            # postponed games elsewhere in this pipeline.
            marker = self._p("_net_points_daily_done", f"date={date}.marker")
            if not self.force and storage.is_complete(marker):
                continue

            data = self._net_points_daily_client.get_daily(date, season_folder=season - 1)
            player_rows, team_rows = parse.parse_net_points_daily(
                data, date, team_abbr_to_id, team_date_to_game, name_to_athlete_id
            )
            storage.write_rows(self._p("net_points_player_game", f"season={season}", f"date={date}.parquet"), player_rows)
            storage.write_rows(self._p("net_points_team_game", f"season={season}", f"date={date}.parquet"), team_rows)
            storage.mark_complete(marker)

    # ---------------- glossary ----------------
    def write_glossary(self) -> None:
        if not self.glossary:
            return
        path = self._p("stat_glossary", "stat_glossary.parquet")
        storage.write_rows(path, list(self.glossary.values()))

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
        for event_id in tqdm(event_ids, desc=f"{season} type={season_type} games"):
            self.fetch_game(event_id, season, season_type)
        for team_id in tqdm(team_ids, desc=f"{season} type={season_type} team season stats", leave=False):
            self.fetch_team_season_stats(season, season_type, team_id)

        if not event_ids or self._resolved_event_count(season, season_type) < len(event_ids):
            # Some discovered games haven't been played yet (in-progress season) -
            # leave unmarked so next run re-checks the schedule for new results.
            return

        athlete_ids = self.athlete_ids_for(season, season_type)
        for athlete_id in tqdm(
            athlete_ids, desc=f"{season} type={season_type} player season stats", leave=False
        ):
            self.fetch_player_season_stats(athlete_id, season_type)

        storage.mark_complete(marker)

    # ---------------- top-level run ----------------
    def run(self, seasons: list[int], season_types: list[int]) -> None:
        self.fetch_teams()
        team_ids = self.team_ids()
        if not team_ids:
            log.error("No teams returned from ESPN - aborting.")
            return

        self.fetch_net_points()

        for season in seasons:
            log.info("== season %s ==", season)
            self.fetch_standings(season)
            self.fetch_power_index(season)

            for season_type in season_types:
                self._run_season_type(season, season_type, team_ids)

        if self.include_net_points_daily:
            # Must run after the season/type loop above - it resolves against
            # whatever's now in the local games table, so games have to be
            # fetched first for this run's seasons to be there to resolve against.
            self.fetch_net_points_daily()

        self.write_glossary()


def pq_read(path: Path, columns: list[str]) -> pa.Table:
    return pq.read_table(path, columns=columns)
