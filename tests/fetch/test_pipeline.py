"""Regression + sanity tests for the fetch pipeline's resumability and the O(1)
completion-marker optimization, using a fake client (no network)."""

from pathlib import Path
from typing import Any

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
