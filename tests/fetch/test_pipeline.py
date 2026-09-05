"""Regression + sanity tests for the fetch pipeline's resumability and the O(1)
completion-marker optimization, using a fake client (no network)."""

from pathlib import Path
from typing import Any

import pyarrow.dataset as ds

from association.fetch import storage
from association.fetch.pipeline import Pipeline

TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams"
SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"


def _schedule_url(team_id: str) -> str:
    return f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{team_id}/schedule"


class FakeClient:
    """Minimal stand-in for ESPNClient - returns canned JSON by exact URL, no
    network. `responses` values may be a dict (returned as-is) or a callable
    taking `params` and returning a dict."""

    def __init__(self, responses: dict) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict | None]] = []

    def get_json(self, url: str, params: dict | None = None) -> Any:
        self.calls.append((url, params))
        resp = self.responses.get(url)
        if callable(resp):
            return resp(params)
        return resp


def _teams_response(n: int) -> dict:
    return {
        "sports": [{"leagues": [{"teams": [{"team": {"id": str(i), "abbreviation": f"T{i}"}} for i in range(1, n + 1)]}]}]
    }


def _schedule_response(event_ids: list[str]) -> dict:
    return {"events": [{"id": eid} for eid in event_ids]}


def _game_summary(event_id: str, completed: bool = True, state: str = "post", home: str = "1", away: str = "2") -> dict:
    return {
        "header": {
            "id": event_id,
            "competitions": [
                {
                    "date": "2024-01-01T00:00Z",
                    "neutralSite": False,
                    "conferenceCompetition": False,
                    "status": {
                        "type": {
                            "name": "STATUS_FINAL" if completed else "STATUS_POSTPONED",
                            "completed": completed,
                            "state": state,
                        }
                    },
                    "competitors": [
                        {"homeAway": "home", "winner": completed, "score": 100, "team": {"id": home}},
                        {"homeAway": "away", "winner": False, "score": 90, "team": {"id": away}},
                    ],
                }
            ],
        },
        "gameInfo": {},
        "boxscore": {"teams": [], "players": []},
        "plays": [],
        "winprobability": [],
    }


def test_fetch_teams_writes_then_skips_on_rerun(tmp_path: Path) -> None:
    client = FakeClient({TEAMS_URL: _teams_response(3)})
    pipeline = Pipeline(client, tmp_path)
    pipeline.fetch_teams()
    assert pipeline.team_ids() == ["1", "2", "3"]

    calls_before = len(client.calls)
    pipeline.fetch_teams()
    assert len(client.calls) == calls_before  # already on disk - no new request


def test_run_season_type_marks_complete_when_all_games_resolved(tmp_path: Path) -> None:
    """A season is only "complete" once every discovered game is either played
    (has a games/*.parquet row) or permanently settled as never-played
    (postponed/cancelled -> _resolved/*.marker)."""
    responses: dict[str, Any] = {TEAMS_URL: _teams_response(2)}
    for i in (1, 2):
        responses[_schedule_url(str(i))] = _schedule_response(["100", "101"])
    responses[SUMMARY_URL] = lambda params: (
        _game_summary("100", completed=True) if params["event"] == "100" else _game_summary("101", completed=False, state="post")
    )
    client = FakeClient(responses)
    pipeline = Pipeline(client, tmp_path)
    pipeline.fetch_teams()
    pipeline._run_season_type(2024, 2, pipeline.team_ids())

    assert storage.is_complete(pipeline._complete_marker(2024, 2))
    assert (tmp_path / "games" / "season=2024" / "season_type=2" / "event_100.parquet").exists()
    assert (tmp_path / "_resolved" / "season=2024" / "season_type=2" / "event_101.marker").exists()
    # the postponed game must NOT get a fake row in the games table
    assert not (tmp_path / "games" / "season=2024" / "season_type=2" / "event_101.parquet").exists()


def test_run_season_type_not_marked_complete_when_game_still_pending(tmp_path: Path) -> None:
    """Regression companion: an in-progress season (a real future/unplayed
    game, state='pre') must NOT be marked complete - new results still need
    picking up on the next run."""
    responses: dict[str, Any] = {TEAMS_URL: _teams_response(1)}
    responses[_schedule_url("1")] = _schedule_response(["200"])
    responses[SUMMARY_URL] = lambda params: _game_summary("200", completed=False, state="pre")
    client = FakeClient(responses)
    pipeline = Pipeline(client, tmp_path)
    pipeline.fetch_teams()
    pipeline._run_season_type(2024, 2, pipeline.team_ids())

    assert not storage.is_complete(pipeline._complete_marker(2024, 2))
    assert not (tmp_path / "_resolved" / "season=2024" / "season_type=2" / "event_200.marker").exists()


def test_postponed_game_not_refetched_every_run(tmp_path: Path) -> None:
    """Regression: before the resolved-marker fix, a postponed game had no
    checkpoint at all and got re-fetched (a wasted network call) on every
    single pull run forever."""
    responses: dict[str, Any] = {TEAMS_URL: _teams_response(1)}
    responses[_schedule_url("1")] = _schedule_response(["101"])
    responses[SUMMARY_URL] = lambda params: _game_summary("101", completed=False, state="post")
    client = FakeClient(responses)
    pipeline = Pipeline(client, tmp_path)
    pipeline.fetch_teams()

    pipeline.fetch_game("101", 2024, 2)
    summary_calls_after_first = sum(1 for url, _ in client.calls if url == SUMMARY_URL)
    pipeline.fetch_game("101", 2024, 2)
    summary_calls_after_second = sum(1 for url, _ in client.calls if url == SUMMARY_URL)

    assert summary_calls_after_first == 1
    assert summary_calls_after_second == 1  # no new call the second time


def test_run_season_type_skips_entirely_once_complete_marker_present(tmp_path: Path) -> None:
    """The core performance fix: a second run against an already-fully-fetched
    season/type must do a single O(1) marker check, not re-derive the
    schedule (30 network calls) or re-scan every game file."""
    responses: dict[str, Any] = {TEAMS_URL: _teams_response(1)}
    responses[_schedule_url("1")] = _schedule_response(["300"])
    responses[SUMMARY_URL] = lambda params: _game_summary("300", completed=True)
    client = FakeClient(responses)
    pipeline = Pipeline(client, tmp_path)
    pipeline.fetch_teams()
    team_ids = pipeline.team_ids()
    pipeline._run_season_type(2024, 2, team_ids)

    calls_before = len(client.calls)
    pipeline._run_season_type(2024, 2, team_ids)
    assert len(client.calls) == calls_before  # zero new network calls


def test_force_bypasses_completion_marker(tmp_path: Path) -> None:
    responses: dict[str, Any] = {TEAMS_URL: _teams_response(1)}
    responses[_schedule_url("1")] = _schedule_response(["400"])
    responses[SUMMARY_URL] = lambda params: _game_summary("400", completed=True)
    client = FakeClient(responses)
    pipeline = Pipeline(client, tmp_path, force=True)
    pipeline.fetch_teams()
    team_ids = pipeline.team_ids()
    pipeline._run_season_type(2024, 2, team_ids)

    calls_before = len(client.calls)
    pipeline._run_season_type(2024, 2, team_ids)
    assert len(client.calls) > calls_before  # force=True -> re-does the full check


def test_athlete_ids_for_scoped_to_season_and_type(tmp_path: Path) -> None:
    """Regression: athlete discovery used to scan the whole season directory
    across all season_types combined - scoping to one season_type is both
    more correct (no cross-type leakage) and cheaper to scan."""
    from association.fetch import storage as storage_mod

    storage_mod.write_rows(
        tmp_path / "player_box_stats" / "season=2024" / "season_type=2" / "event_1.parquet",
        [{"athlete_id": "10", "event_id": "1"}],
    )
    storage_mod.write_rows(
        tmp_path / "player_box_stats" / "season=2024" / "season_type=3" / "event_2.parquet",
        [{"athlete_id": "99", "event_id": "2"}],
    )
    client = FakeClient({})
    pipeline = Pipeline(client, tmp_path)
    assert pipeline.athlete_ids_for(2024, 2) == ["10"]
    assert pipeline.athlete_ids_for(2024, 3) == ["99"]


def test_fetch_team_season_stats_skips_network_for_preseason(tmp_path: Path) -> None:
    """Regression: ESPN has no team season stats for preseason (season_type=1)
    - confirmed live, the endpoint returns no data for every team/season. With
    no resulting file to ever skip against, every run used to re-hit the
    network for nothing. Must short-circuit before the request, not after."""
    client = FakeClient({})  # any URL lookup would return None and fail loudly if hit unexpectedly
    pipeline = Pipeline(client, tmp_path)
    pipeline.fetch_team_season_stats(2024, 1, "1")
    assert client.calls == []
    assert not (tmp_path / "team_season_stats" / "season=2024" / "season_type=1" / "team_1.parquet").exists()


def test_fetch_team_season_stats_still_fetches_for_regular_and_postseason(tmp_path: Path) -> None:
    responses = {
        "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/seasons/2024/types/2/teams/1/statistics": {
            "splits": {"categories": [{"stats": [{"name": "blocks", "value": 5.0}]}]}
        }
    }
    client = FakeClient(responses)
    pipeline = Pipeline(client, tmp_path)
    pipeline.fetch_team_season_stats(2024, 2, "1")
    assert len(client.calls) == 1
    assert (tmp_path / "team_season_stats" / "season=2024" / "season_type=2" / "team_1.parquet").exists()


NET_POINTS_PLAYER_URL = "https://nfl-player-metrics.s3.amazonaws.com/net-pts/nba_net_pts_data.json"
NET_POINTS_PLAYER_100_URL = "https://nfl-player-metrics.s3.amazonaws.com/net-pts/nba_net_pts100_data.json"
NET_POINTS_TEAM_URL = "https://nfl-player-metrics.s3.amazonaws.com/net-pts/team_nba.json"


def test_fetch_net_points_writes_one_file_per_season_and_type(tmp_path: Path) -> None:
    responses: dict[str, Any] = {
        TEAMS_URL: _teams_response(2),  # team "1" -> abbreviation "T1"
        NET_POINTS_PLAYER_URL: [
            {"dot_com_id": 10, "tm": "T1", "min_season": 2023, "seasonType": "Regular Season", "net_pts_games": 50, "overall": 1.0},
            {"dot_com_id": 11, "tm": "T2", "min_season": 2023, "seasonType": "Playoffs", "net_pts_games": 10, "overall": 2.0},
        ],
        NET_POINTS_TEAM_URL: {"team4f": '{"teamId": {"0": "T1"}, "Side": {"0": "Total"}, "season": {"0": 2023}}'},
    }
    client = FakeClient(responses)
    pipeline = Pipeline(client, tmp_path)
    pipeline.fetch_teams()
    pipeline.fetch_net_points()

    assert (tmp_path / "net_points_player" / "season=2024" / "regular_season.parquet").exists()
    assert (tmp_path / "net_points_player" / "season=2024" / "playoffs.parquet").exists()
    assert (tmp_path / "net_points_team" / "season=2024" / "net_points_team.parquet").exists()


def test_fetch_net_points_merges_per_100_possession_rate_file(tmp_path: Path) -> None:
    """nba_net_pts100_data.json is a second, separate request against the same
    public bucket - confirmed live it's fetched only when espnanalytics.com's
    own "Net Points / 100 Poss" toggle is used, not computed client-side."""
    responses: dict[str, Any] = {
        TEAMS_URL: _teams_response(1),
        NET_POINTS_PLAYER_URL: [
            {"dot_com_id": 10, "tm": "T1", "min_season": 2023, "seasonType": "Regular Season", "net_pts_games": 50, "overall": 1.0}
        ],
        NET_POINTS_PLAYER_100_URL: [
            {
                "dot_com_id": 10,
                "min_season": 2023,
                "seasonType": "Regular Season",
                "tNet100": 3.3,
                "oNet100": 2.2,
                "dNet100": 1.1,
                "totMin": 1500,
            }
        ],
        NET_POINTS_TEAM_URL: {"team4f": '{"teamId": {"0": "T1"}, "Side": {"0": "Total"}, "season": {"0": 2023}}'},
    }
    client = FakeClient(responses)
    pipeline = Pipeline(client, tmp_path)
    pipeline.fetch_teams()
    pipeline.fetch_net_points()

    table = ds.dataset(str(tmp_path / "net_points_player" / "season=2024" / "regular_season.parquet")).to_table()
    row = table.to_pylist()[0]
    assert row["overall_per_100_poss"] == 3.3
    assert row["offense_per_100_poss"] == 2.2
    assert row["defense_per_100_poss"] == 1.1
    assert row["total_minutes"] == 1500


def test_fetch_net_points_skips_writing_files_already_on_disk(tmp_path: Path) -> None:
    responses: dict[str, Any] = {
        TEAMS_URL: _teams_response(1),
        NET_POINTS_PLAYER_URL: [
            {"dot_com_id": 10, "tm": "T1", "min_season": 2023, "seasonType": "Regular Season", "net_pts_games": 50, "overall": 1.0}
        ],
        NET_POINTS_TEAM_URL: {"team4f": '{"teamId": {"0": "T1"}, "Side": {"0": "Total"}, "season": {"0": 2023}}'},
    }
    client = FakeClient(responses)
    pipeline = Pipeline(client, tmp_path)
    pipeline.fetch_teams()
    pipeline.fetch_net_points()

    player_file = tmp_path / "net_points_player" / "season=2024" / "regular_season.parquet"
    written_at = player_file.stat().st_mtime_ns
    pipeline.fetch_net_points()
    assert player_file.stat().st_mtime_ns == written_at  # not rewritten - still resumable on the write side


def test_fetch_net_points_force_overwrites(tmp_path: Path) -> None:
    responses: dict[str, Any] = {
        TEAMS_URL: _teams_response(1),
        NET_POINTS_PLAYER_URL: [
            {"dot_com_id": 10, "tm": "T1", "min_season": 2023, "seasonType": "Regular Season", "net_pts_games": 50, "overall": 1.0}
        ],
        NET_POINTS_TEAM_URL: {"team4f": '{"teamId": {"0": "T1"}, "Side": {"0": "Total"}, "season": {"0": 2023}}'},
    }
    client = FakeClient(responses)
    pipeline = Pipeline(client, tmp_path, force=True)
    pipeline.fetch_teams()
    pipeline.fetch_net_points()
    pipeline.fetch_net_points()  # must not raise even with --force
    assert (tmp_path / "net_points_player" / "season=2024" / "regular_season.parquet").exists()


# ---------------- NetPoints daily (per-game) ----------------


class FakeDailyClient:
    """Stand-in for NetPointsDailyClient - no Cognito/S3, canned responses by date."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get_daily(self, date: str, season_folder: int) -> Any:
        self.calls.append(date)
        return self.responses.get(date)


def _write_games_fixture(data_dir: Path, rows: list[dict]) -> None:
    storage.write_rows(data_dir / "games" / "f.parquet", rows)


def _write_players_fixture(data_dir: Path, rows: list[dict]) -> None:
    storage.write_rows(data_dir / "players" / "f.parquet", rows)


def test_team_date_to_game_covers_home_and_away(tmp_path: Path) -> None:
    _write_games_fixture(
        tmp_path,
        [{"event_id": "1", "season": 2026, "season_type": 2, "date": "2026-04-12T22:00Z", "home_team_id": "18", "away_team_id": "30"}],
    )
    pipeline = Pipeline(FakeClient({}), tmp_path)
    mapping = pipeline._team_date_to_game()
    assert mapping[("18", "2026-04-12")] == ("1", 2026, 2)
    assert mapping[("30", "2026-04-12")] == ("1", 2026, 2)


def test_name_to_athlete_id_drops_ambiguous_names(tmp_path: Path) -> None:
    _write_players_fixture(
        tmp_path,
        [
            {"athlete_id": "1", "display_name": "Unique Player"},
            {"athlete_id": "2", "display_name": "Duplicate Name"},
            {"athlete_id": "3", "display_name": "Duplicate Name"},
        ],
    )
    pipeline = Pipeline(FakeClient({}), tmp_path)
    mapping = pipeline._name_to_athlete_id()
    assert mapping == {"Unique Player": "1"}


def test_fetch_net_points_daily_writes_resolved_rows(tmp_path: Path) -> None:
    _write_games_fixture(
        tmp_path,
        [{"event_id": "1", "season": 2026, "season_type": 2, "date": "2026-04-12T22:00Z", "home_team_id": "18", "away_team_id": "30"}],
    )
    _write_players_fixture(tmp_path, [{"athlete_id": "9", "display_name": "Test Player"}])
    client = FakeClient({TEAMS_URL: _teams_response(1)})
    client.responses[TEAMS_URL] = {
        "sports": [{"leagues": [{"teams": [{"team": {"id": "18", "abbreviation": "NY"}}]}]}]
    }
    pipeline = Pipeline(client, tmp_path)
    pipeline.fetch_teams()
    pipeline._net_points_daily_client = FakeDailyClient(
        {
            "2026-04-12": {
                "player_box": [{"tmName": "NYK", "displayName": "Test Player", "oNetPts": 1.0, "dNetPts": 2.0, "tNetPts": 3.0}],
                "team_box": [{"tmName": "NYK", "netPts2s": 1.0}],
            }
        }
    )

    pipeline.fetch_net_points_daily()

    player_file = tmp_path / "net_points_player_game" / "season=2026" / "date=2026-04-12.parquet"
    team_file = tmp_path / "net_points_team_game" / "season=2026" / "date=2026-04-12.parquet"
    assert player_file.exists()
    assert team_file.exists()
    assert storage.exists(player_file)


def test_fetch_net_points_daily_marks_done_even_with_zero_resolved_rows(tmp_path: Path) -> None:
    """Regression-shaped: storage.write_rows([]) is a no-op, so a date whose
    rows all fail to resolve must not look "incomplete" forever and get
    re-fetched every run - the same class of bug already fixed for preseason
    team-stats and postponed games elsewhere in this pipeline."""
    _write_games_fixture(
        tmp_path,
        [{"event_id": "1", "season": 2026, "season_type": 2, "date": "2026-04-12T22:00Z", "home_team_id": "18", "away_team_id": "30"}],
    )
    _write_players_fixture(tmp_path, [])
    client = FakeClient({TEAMS_URL: {"sports": [{"leagues": [{"teams": [{"team": {"id": "18", "abbreviation": "NY"}}]}]}]}})
    pipeline = Pipeline(client, tmp_path)
    pipeline.fetch_teams()
    fake_daily = FakeDailyClient({"2026-04-11": None, "2026-04-12": None})  # no data published for either date
    pipeline._net_points_daily_client = fake_daily

    pipeline.fetch_net_points_daily()
    assert fake_daily.calls == ["2026-04-11", "2026-04-12"]

    pipeline.fetch_net_points_daily()
    assert fake_daily.calls == ["2026-04-11", "2026-04-12"]  # not called again - markers made both skip


def test_net_points_dates_and_seasons_includes_day_before_each_local_date(tmp_path: Path) -> None:
    """Regression, confirmed live: a Lakers game with no OTHER local game on
    the literal calendar day before it was silently never fetched at all -
    its true NetPoints label (ESPN date minus one) never appeared in the
    fetch set, unlike the back-to-back bug which fetched the wrong game
    rather than no game. Every local date's day-before must be included too."""
    _write_games_fixture(tmp_path, [{"event_id": "1", "season": 2026, "season_type": 2, "date": "2025-11-29T03:00Z", "home_team_id": "13", "away_team_id": "6"}])
    pipeline = Pipeline(FakeClient({}), tmp_path)
    dates = pipeline._net_points_dates_and_seasons()
    assert dates == {"2025-11-29": 2026, "2025-11-28": 2026}


def test_net_points_dates_and_seasons_prefers_real_season_over_derived_one(tmp_path: Path) -> None:
    """If the day before a local date is ALSO independently a real local date
    (the common case - most days have games), its own real season must win,
    not the derived value from the following day."""
    _write_games_fixture(
        tmp_path,
        [
            {"event_id": "1", "season": 2019, "season_type": 3, "date": "2019-06-14T00:00Z", "home_team_id": "13", "away_team_id": "6"},
            {"event_id": "2", "season": 2020, "season_type": 1, "date": "2019-06-15T00:00Z", "home_team_id": "13", "away_team_id": "6"},
        ],
    )
    pipeline = Pipeline(FakeClient({}), tmp_path)
    dates = pipeline._net_points_dates_and_seasons()
    assert dates["2019-06-14"] == 2019  # its own real season, not season 2020 derived from the 15th
