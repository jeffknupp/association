"""Regression + sanity tests for the fetch pipeline's resumability and the O(1)
completion-marker optimization, using a fake client (no network)."""

import threading
import time
from pathlib import Path
from typing import Any

import pyarrow.dataset as ds
import pytest
from curl_cffi import requests as cf_requests

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
    return {"sports": [{"leagues": [{"teams": [{"team": {"id": str(i), "abbreviation": f"T{i}"}} for i in range(1, n + 1)]}]}]}


def _schedule_response(event_ids: list[str]) -> dict:
    return {"events": [{"id": eid} for eid in event_ids]}


def _game_summary_with_player(event_id: str, athlete_id: str, completed: bool = True, state: str = "post", home: str = "1", away: str = "2") -> dict:
    summary = _game_summary(event_id, completed=completed, state=state, home=home, away=away)
    summary["boxscore"]["players"] = [
        {
            "team": {"id": home},
            "statistics": [{"keys": ["points"], "athletes": [{"athlete": {"id": athlete_id}, "stats": ["20"]}]}],
        }
    ]
    return summary


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
    responses[SUMMARY_URL] = lambda params: _game_summary("100", completed=True) if params["event"] == "100" else _game_summary("101", completed=False, state="post")
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


TEAM_SEASON_STATS_URL_T1 = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/seasons/2024/types/2/teams/1/statistics"
PLAYER_CAREER_STATS_URL_10 = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/10/stats"
_PLAYER_CAREER_STATS_RESPONSE = {"categories": [{"names": ["avgPoints"], "statistics": [{"season": {"year": 2024}, "teamId": "1", "stats": ["20.0"]}]}]}


def test_run_season_type_refreshes_team_and_player_stats_while_season_in_progress(tmp_path: Path) -> None:
    """Regression: team/player season-stats aggregates shift every game of an
    in-progress season, but player season stats used to never be fetched at
    all until the WHOLE season was fully resolved (gated behind the same
    check that decides when to mark the season complete) - so a season in
    progress never got any player season stats locally, not even stale ones.
    A still-pending game must not block either from being fetched."""
    responses: dict[str, Any] = {TEAMS_URL: _teams_response(1)}
    responses[_schedule_url("1")] = _schedule_response(["500", "501"])
    responses[SUMMARY_URL] = lambda params: _game_summary_with_player("500", "10", completed=True) if params["event"] == "500" else _game_summary("501", completed=False, state="pre")
    responses[TEAM_SEASON_STATS_URL_T1] = {"splits": {"categories": [{"stats": [{"name": "blocks", "value": 5.0}]}]}}
    responses[PLAYER_CAREER_STATS_URL_10] = _PLAYER_CAREER_STATS_RESPONSE
    client = FakeClient(responses)
    pipeline = Pipeline(client, tmp_path)
    pipeline.fetch_teams()
    pipeline._run_season_type(2024, 2, pipeline.team_ids())

    assert not storage.is_complete(pipeline._complete_marker(2024, 2))  # still in progress - correct, unchanged
    assert (tmp_path / "team_season_stats" / "season=2024" / "season_type=2" / "team_1.parquet").exists()
    assert (tmp_path / "player_season_stats" / "athlete_10_type_2.parquet").exists()


def test_run_season_type_re_fetches_team_and_player_stats_on_second_pass_while_in_progress(tmp_path: Path) -> None:
    """Regression: once written, team/player season stats used to be frozen by
    their own resumability check (file exists -> skip) for the rest of the
    season, even without --force. While the season type is still in progress,
    a second pull must re-fetch both rather than silently keeping stale data."""
    responses: dict[str, Any] = {TEAMS_URL: _teams_response(1)}
    responses[_schedule_url("1")] = _schedule_response(["500", "501"])
    responses[SUMMARY_URL] = lambda params: _game_summary_with_player("500", "10", completed=True) if params["event"] == "500" else _game_summary("501", completed=False, state="pre")
    responses[TEAM_SEASON_STATS_URL_T1] = {"splits": {"categories": [{"stats": [{"name": "blocks", "value": 5.0}]}]}}
    responses[PLAYER_CAREER_STATS_URL_10] = _PLAYER_CAREER_STATS_RESPONSE
    client = FakeClient(responses)
    pipeline = Pipeline(client, tmp_path)
    pipeline.fetch_teams()
    team_ids = pipeline.team_ids()
    pipeline._run_season_type(2024, 2, team_ids)

    team_calls_before = sum(1 for url, _ in client.calls if url == TEAM_SEASON_STATS_URL_T1)
    player_calls_before = sum(1 for url, _ in client.calls if url == PLAYER_CAREER_STATS_URL_10)
    pipeline._run_season_type(2024, 2, team_ids)
    team_calls_after = sum(1 for url, _ in client.calls if url == TEAM_SEASON_STATS_URL_T1)
    player_calls_after = sum(1 for url, _ in client.calls if url == PLAYER_CAREER_STATS_URL_10)

    assert team_calls_after > team_calls_before
    assert player_calls_after > player_calls_before


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
        "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/seasons/2024/types/2/teams/1/statistics": {"splits": {"categories": [{"stats": [{"name": "blocks", "value": 5.0}]}]}}
    }
    client = FakeClient(responses)
    pipeline = Pipeline(client, tmp_path)
    pipeline.fetch_team_season_stats(2024, 2, "1")
    assert len(client.calls) == 1
    assert (tmp_path / "team_season_stats" / "season=2024" / "season_type=2" / "team_1.parquet").exists()


def test_fetch_team_season_stats_force_refresh_bypasses_existing_file(tmp_path: Path) -> None:
    responses = {TEAM_SEASON_STATS_URL_T1: {"splits": {"categories": [{"stats": [{"name": "blocks", "value": 5.0}]}]}}}
    client = FakeClient(responses)
    pipeline = Pipeline(client, tmp_path)
    pipeline.fetch_team_season_stats(2024, 2, "1")
    calls_before = len(client.calls)

    pipeline.fetch_team_season_stats(2024, 2, "1")  # no force_refresh - skips as before
    assert len(client.calls) == calls_before

    pipeline.fetch_team_season_stats(2024, 2, "1", force_refresh=True)
    assert len(client.calls) > calls_before


def test_fetch_player_season_stats_force_refresh_bypasses_existing_file(tmp_path: Path) -> None:
    responses = {PLAYER_CAREER_STATS_URL_10: _PLAYER_CAREER_STATS_RESPONSE}
    client = FakeClient(responses)
    pipeline = Pipeline(client, tmp_path)
    pipeline.fetch_player_season_stats("10", 2)
    calls_before = len(client.calls)

    pipeline.fetch_player_season_stats("10", 2)  # no force_refresh - skips as before
    assert len(client.calls) == calls_before

    pipeline.fetch_player_season_stats("10", 2, force_refresh=True)
    assert len(client.calls) > calls_before


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
    pipeline.fetch_net_points([2024])

    assert (tmp_path / "net_points_player" / "season=2024" / "regular_season.parquet").exists()
    assert (tmp_path / "net_points_player" / "season=2024" / "playoffs.parquet").exists()
    assert (tmp_path / "net_points_team" / "season=2024" / "net_points_team.parquet").exists()


def test_fetch_net_points_merges_per_100_possession_rate_file(tmp_path: Path) -> None:
    """nba_net_pts100_data.json is a second, separate request against the same
    public bucket - confirmed live it's fetched only when espnanalytics.com's
    own "Net Points / 100 Poss" toggle is used, not computed client-side."""
    responses: dict[str, Any] = {
        TEAMS_URL: _teams_response(1),
        NET_POINTS_PLAYER_URL: [{"dot_com_id": 10, "tm": "T1", "min_season": 2023, "seasonType": "Regular Season", "net_pts_games": 50, "overall": 1.0}],
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
    pipeline.fetch_net_points([2024])

    table = ds.dataset(str(tmp_path / "net_points_player" / "season=2024" / "regular_season.parquet")).to_table()
    row = table.to_pylist()[0]
    assert row["overall_per_100_poss"] == 3.3
    assert row["offense_per_100_poss"] == 2.2
    assert row["defense_per_100_poss"] == 1.1
    assert row["total_minutes"] == 1500


def test_fetch_net_points_skips_writing_files_already_on_disk(tmp_path: Path) -> None:
    responses: dict[str, Any] = {
        TEAMS_URL: _teams_response(1),
        NET_POINTS_PLAYER_URL: [{"dot_com_id": 10, "tm": "T1", "min_season": 2023, "seasonType": "Regular Season", "net_pts_games": 50, "overall": 1.0}],
        NET_POINTS_TEAM_URL: {"team4f": '{"teamId": {"0": "T1"}, "Side": {"0": "Total"}, "season": {"0": 2023}}'},
    }
    client = FakeClient(responses)
    pipeline = Pipeline(client, tmp_path)
    pipeline.fetch_teams()
    pipeline.fetch_net_points([2024])

    player_file = tmp_path / "net_points_player" / "season=2024" / "regular_season.parquet"
    written_at = player_file.stat().st_mtime_ns
    pipeline.fetch_net_points([2024])
    assert player_file.stat().st_mtime_ns == written_at  # not rewritten - still resumable on the write side


def test_fetch_net_points_force_overwrites(tmp_path: Path) -> None:
    responses: dict[str, Any] = {
        TEAMS_URL: _teams_response(1),
        NET_POINTS_PLAYER_URL: [{"dot_com_id": 10, "tm": "T1", "min_season": 2023, "seasonType": "Regular Season", "net_pts_games": 50, "overall": 1.0}],
        NET_POINTS_TEAM_URL: {"team4f": '{"teamId": {"0": "T1"}, "Side": {"0": "Total"}, "season": {"0": 2023}}'},
    }
    client = FakeClient(responses)
    pipeline = Pipeline(client, tmp_path, force=True)
    pipeline.fetch_teams()
    pipeline.fetch_net_points([2024])
    pipeline.fetch_net_points([2024])  # must not raise even with --force
    assert (tmp_path / "net_points_player" / "season=2024" / "regular_season.parquet").exists()


def test_fetch_net_points_refetches_current_season_but_not_a_past_one(tmp_path: Path, monkeypatch: Any) -> None:
    """Regression: NetPoints values shift daily while a season is in progress,
    but the season file, once written, used to be frozen for the rest of that
    season without --force - same reasoning as fetch_standings/
    fetch_power_index below."""
    monkeypatch.setattr("association.fetch.pipeline.current_season", lambda: 2024)
    responses: dict[str, Any] = {
        TEAMS_URL: _teams_response(1),
        NET_POINTS_PLAYER_URL: [
            {"dot_com_id": 10, "tm": "T1", "min_season": 2022, "seasonType": "Regular Season", "net_pts_games": 50, "overall": 1.0},
            {"dot_com_id": 10, "tm": "T1", "min_season": 2023, "seasonType": "Regular Season", "net_pts_games": 50, "overall": 2.0},
        ],
        NET_POINTS_TEAM_URL: {"team4f": '{"teamId": {"0": "T1"}, "Side": {"0": "Total"}, "season": {"0": 2023}}'},
    }
    client = FakeClient(responses)
    pipeline = Pipeline(client, tmp_path)
    pipeline.fetch_teams()
    pipeline.fetch_net_points([2023, 2024])

    past_file = tmp_path / "net_points_player" / "season=2023" / "regular_season.parquet"  # min_season 2022 -> season 2023, in the past
    current_file = tmp_path / "net_points_player" / "season=2024" / "regular_season.parquet"  # min_season 2023 -> season 2024, "current"
    past_written_at = past_file.stat().st_mtime_ns
    current_written_at = current_file.stat().st_mtime_ns

    time.sleep(0.01)  # guarantee a distinguishable mtime if the file IS rewritten
    pipeline.fetch_net_points([2023, 2024])
    assert past_file.stat().st_mtime_ns == past_written_at  # past season - untouched
    assert current_file.stat().st_mtime_ns > current_written_at  # current season - refreshed


# ---------------- NetPoints fingerprint ----------------


def _fingerprint_url(start_year: int) -> str:
    return f"https://nfl-player-metrics.s3.amazonaws.com/net-pts/fingerprint-files/nbafingerprint_{start_year}.json"


def _raise_403(params: Any) -> Any:
    from types import SimpleNamespace

    raise cf_requests.exceptions.HTTPError("403 Forbidden", response=SimpleNamespace(status_code=403))


def test_fetch_net_points_fingerprint_writes_matched_players(tmp_path: Path) -> None:
    _write_players_fixture(tmp_path, [{"athlete_id": "1966", "display_name": "LeBron James"}])
    responses: dict[str, Any] = {
        TEAMS_URL: _teams_response(1),
        _fingerprint_url(2025): {"2544": {"season": 2025, "displayName": "LeBron James", "2pt_oNetPts": 140.9}},
    }
    client = FakeClient(responses)
    pipeline = Pipeline(client, tmp_path)
    pipeline.fetch_teams()
    pipeline.fetch_net_points_fingerprint(2026)

    path = tmp_path / "net_points_player_fingerprint" / "season=2026" / "fingerprint.parquet"
    assert path.exists()
    table = ds.dataset(str(path), format="parquet").to_table()
    assert table.to_pylist()[0]["athlete_id"] == "1966"
    assert table.to_pylist()[0]["two_pt_o_net_pts"] == 140.9


def test_fetch_net_points_fingerprint_treats_403_as_no_data(tmp_path: Path) -> None:
    """A season with no fingerprint file yet (hasn't started, or older than
    NetPoints' floor) returns 403 from this bucket - confirmed live, unlike
    the 404/400 every espn.com endpoint uses for missing data. Must not raise."""
    responses: dict[str, Any] = {TEAMS_URL: _teams_response(1), _fingerprint_url(2026): _raise_403}
    client = FakeClient(responses)
    pipeline = Pipeline(client, tmp_path)
    pipeline.fetch_teams()
    pipeline.fetch_net_points_fingerprint(2027)  # must not raise
    assert not (tmp_path / "net_points_player_fingerprint" / "season=2027").exists()


def test_fetch_net_points_fingerprint_refetches_current_season_but_not_a_past_one(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("association.fetch.pipeline.current_season", lambda: 2024)
    _write_players_fixture(tmp_path, [{"athlete_id": "1966", "display_name": "LeBron James"}])
    responses: dict[str, Any] = {
        TEAMS_URL: _teams_response(1),
        _fingerprint_url(2022): {"2544": {"season": 2022, "displayName": "LeBron James", "2pt_oNetPts": 1.0}},
        _fingerprint_url(2023): {"2544": {"season": 2023, "displayName": "LeBron James", "2pt_oNetPts": 2.0}},
    }
    client = FakeClient(responses)
    pipeline = Pipeline(client, tmp_path)
    pipeline.fetch_teams()
    pipeline.fetch_net_points_fingerprint(2023)  # past season
    pipeline.fetch_net_points_fingerprint(2024)  # "current" season

    past_file = tmp_path / "net_points_player_fingerprint" / "season=2023" / "fingerprint.parquet"
    current_file = tmp_path / "net_points_player_fingerprint" / "season=2024" / "fingerprint.parquet"
    past_written_at = past_file.stat().st_mtime_ns
    current_written_at = current_file.stat().st_mtime_ns

    time.sleep(0.01)
    pipeline.fetch_net_points_fingerprint(2023)
    pipeline.fetch_net_points_fingerprint(2024)
    assert past_file.stat().st_mtime_ns == past_written_at  # past season - untouched
    assert current_file.stat().st_mtime_ns > current_written_at  # current season - refreshed


# ---------------- NetPoints daily (per-game) ----------------


class FakeDailyClient:
    """Stand-in for NetPointsDailyClient - no Cognito/S3, canned responses by date."""

    def __init__(self, responses: dict[str, Any], skills: dict[str, Any] | None = None) -> None:
        self.responses = responses
        self.skills = skills or {}
        self.calls: list[str] = []
        self.skill_calls: list[str] = []

    def get_daily(self, date: str, season_folder: int) -> Any:
        self.calls.append(date)
        return self.responses.get(date)

    def get_daily_players(self, date: str, season_folder: int) -> Any:
        self.skill_calls.append(date)
        return self.skills.get(date)


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
    client.responses[TEAMS_URL] = {"sports": [{"leagues": [{"teams": [{"team": {"id": "18", "abbreviation": "NY"}}]}]}]}
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
    assert fake_daily.skill_calls == ["2026-04-11", "2026-04-12"]

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


# ---------------- standings / power index: in-season refresh ----------------

STANDINGS_URL = "https://site.api.espn.com/apis/v2/sports/basketball/nba/standings"
POWER_INDEX_URL_2023 = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/seasons/2023/powerindex"
POWER_INDEX_URL_2024 = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/seasons/2024/powerindex"


def _standings_response() -> dict:
    return {"children": [{"standings": {"entries": [{"team": {"id": "1"}, "stats": [{"name": "wins", "value": 10.0}]}]}}]}


def _power_index_response(season: int) -> dict:
    return {"items": [{"season": season, "seasonType": 2, "team": {"$ref": "http://x/teams/1?x"}, "stats": [{"name": "bpi", "value": 5.0}]}]}


def test_fetch_standings_refetches_current_season_but_not_a_past_one(tmp_path: Path, monkeypatch: Any) -> None:
    """Regression: standings shift every game of an in-progress season, but
    used to be frozen after the first pull for the rest of that season
    without --force."""
    monkeypatch.setattr("association.fetch.pipeline.current_season", lambda: 2024)
    responses: dict[str, Any] = {STANDINGS_URL: _standings_response()}
    client = FakeClient(responses)
    pipeline = Pipeline(client, tmp_path)
    pipeline.fetch_standings(2023)  # past season
    pipeline.fetch_standings(2024)  # "current" season
    calls_before = len(client.calls)

    pipeline.fetch_standings(2023)
    pipeline.fetch_standings(2024)
    assert len(client.calls) == calls_before + 1  # only the current season re-fetched


def test_fetch_power_index_refetches_current_season_but_not_a_past_one(tmp_path: Path, monkeypatch: Any) -> None:
    """Same reasoning as fetch_standings above - BPI/projected wins shift every
    game of an in-progress season."""
    monkeypatch.setattr("association.fetch.pipeline.current_season", lambda: 2024)
    responses: dict[str, Any] = {
        POWER_INDEX_URL_2023: _power_index_response(2023),
        POWER_INDEX_URL_2024: _power_index_response(2024),
    }
    client = FakeClient(responses)
    pipeline = Pipeline(client, tmp_path)
    pipeline.fetch_power_index(2023)  # past season
    pipeline.fetch_power_index(2024)  # "current" season
    calls_before = len(client.calls)

    pipeline.fetch_power_index(2023)
    pipeline.fetch_power_index(2024)
    assert len(client.calls) == calls_before + 1  # only the current season re-fetched


# ---------------- what a run costs when there is nothing to do ----------------


def _net_points_pipeline(tmp_path: Path, **kwargs: Any) -> tuple[Pipeline, FakeClient]:
    responses: dict[str, Any] = {
        TEAMS_URL: _teams_response(1),
        NET_POINTS_PLAYER_URL: [{"dot_com_id": 10, "tm": "T1", "min_season": 2023, "seasonType": "Regular Season", "net_pts_games": 50, "overall": 1.0}],
        NET_POINTS_TEAM_URL: {"team4f": '{"teamId": {"0": "T1"}, "Side": {"0": "Total"}, "season": {"0": 2023}}'},
    }
    client = FakeClient(responses)
    pipeline = Pipeline(client, tmp_path, **kwargs)
    pipeline.fetch_teams()
    return pipeline, client


def test_net_points_is_not_downloaded_again_for_a_season_already_on_disk(tmp_path: Path, monkeypatch: Any) -> None:
    """The flat files are one league-wide download, so the old code fetched and
    parsed all of them on every run - a pull of one finished season paid for it
    every time, and the rewrite of the CURRENT season's file that followed made
    the warehouse rebuild non-empty, which is where the minute went."""
    monkeypatch.setattr("association.fetch.pipeline.current_season", lambda: 2026)
    pipeline, client = _net_points_pipeline(tmp_path)
    pipeline.fetch_net_points([2024])
    assert (tmp_path / "net_points_player" / "season=2024" / "regular_season.parquet").exists()

    before = len(client.calls)
    pipeline.fetch_net_points([2024])
    assert client.calls[before:] == []  # not one request, not just no writes


def test_net_points_is_still_downloaded_for_a_season_with_no_file_yet(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("association.fetch.pipeline.current_season", lambda: 2026)
    pipeline, client = _net_points_pipeline(tmp_path)
    pipeline.fetch_net_points([2024])
    before = len(client.calls)
    pipeline.fetch_net_points([2024, 2025])  # 2025 has nothing on disk
    assert client.calls[before:] != []


def test_net_points_is_still_downloaded_for_the_current_season(tmp_path: Path, monkeypatch: Any) -> None:
    """Values shift daily while a season is in progress; only a finished one
    is safe to consider done."""
    monkeypatch.setattr("association.fetch.pipeline.current_season", lambda: 2024)
    pipeline, client = _net_points_pipeline(tmp_path)
    pipeline.fetch_net_points([2024])
    before = len(client.calls)
    pipeline.fetch_net_points([2024])
    assert client.calls[before:] != []


def test_net_points_is_skipped_entirely_for_seasons_it_cannot_cover(tmp_path: Path) -> None:
    """NetPoints starts at 2018-19. A pull of 2005 must not download the files
    to discover that, every run, forever."""
    pipeline, client = _net_points_pipeline(tmp_path)
    before = len(client.calls)
    pipeline.fetch_net_points([2005])
    assert client.calls[before:] == []
    assert not (tmp_path / "net_points_player").exists()


def test_net_points_writes_only_the_seasons_asked_for(tmp_path: Path, monkeypatch: Any) -> None:
    """A pull of 2024 rewriting 2026's file is what made an "everything is
    already complete" run rebuild the warehouse."""
    monkeypatch.setattr("association.fetch.pipeline.current_season", lambda: 2026)
    responses: dict[str, Any] = {
        TEAMS_URL: _teams_response(1),
        NET_POINTS_PLAYER_URL: [
            {"dot_com_id": 10, "tm": "T1", "min_season": 2023, "seasonType": "Regular Season", "net_pts_games": 50, "overall": 1.0},
            {"dot_com_id": 11, "tm": "T1", "min_season": 2025, "seasonType": "Regular Season", "net_pts_games": 50, "overall": 2.0},
        ],
        NET_POINTS_TEAM_URL: {"team4f": '{"teamId": {"0": "T1"}, "Side": {"0": "Total"}, "season": {"0": 2023}}'},
    }
    pipeline = Pipeline(FakeClient(responses), tmp_path)
    pipeline.fetch_teams()
    pipeline.fetch_net_points([2024])
    assert (tmp_path / "net_points_player" / "season=2024").exists()
    assert not (tmp_path / "net_points_player" / "season=2026").exists()


def test_a_run_records_which_tables_it_wrote(tmp_path: Path, monkeypatch: Any) -> None:
    """The warehouse is rebuilt per table from the whole Parquet tree, so a run
    has to be able to say what changed - and a run that changed nothing has to
    be able to say that too."""
    monkeypatch.setattr("association.fetch.pipeline.current_season", lambda: 2026)
    pipeline, _ = _net_points_pipeline(tmp_path)
    assert pipeline.written == {"teams"}

    pipeline.fetch_net_points([2024])
    assert pipeline.written == {"teams", "net_points_player", "net_points_team"}


def test_a_second_run_over_finished_seasons_records_nothing_written(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("association.fetch.pipeline.current_season", lambda: 2026)
    pipeline, client = _net_points_pipeline(tmp_path)
    pipeline.fetch_net_points([2024])

    second = Pipeline(client, tmp_path)
    second.fetch_teams()
    second.fetch_net_points([2024])
    assert second.written == set()


# ---------------- the stat glossary ----------------


def _glossary_keys(tmp_path: Path) -> set[str]:
    table = ds.dataset(str(tmp_path / "stat_glossary" / "stat_glossary.parquet"), format="parquet").to_table()
    return {row["stat_key"] for row in table.to_pylist()}


def test_write_glossary_merges_with_what_is_already_on_disk(tmp_path: Path) -> None:
    """A run only collects glossary entries from the endpoints it actually
    fetched. Overwriting meant a pull that skipped everything checkpointed
    replaced the full glossary with the few keys it happened to see - confirmed
    live, 140 keys down to 94, losing exactly the box-score ones that nothing
    was going to re-derive."""
    first = Pipeline(None, tmp_path)
    first.glossary = {"points": {"stat_key": "points", "description": "points"}, "assists": {"stat_key": "assists", "description": "assists"}}
    first.write_glossary()

    second = Pipeline(None, tmp_path)
    second.glossary = {"wins": {"stat_key": "wins", "description": "wins"}}
    second.write_glossary()

    assert _glossary_keys(tmp_path) == {"points", "assists", "wins"}


def test_write_glossary_prefers_the_freshly_fetched_description(tmp_path: Path) -> None:
    first = Pipeline(None, tmp_path)
    first.glossary = {"points": {"stat_key": "points", "description": "old"}}
    first.write_glossary()

    second = Pipeline(None, tmp_path)
    second.glossary = {"points": {"stat_key": "points", "description": "new"}}
    second.write_glossary()

    table = ds.dataset(str(tmp_path / "stat_glossary" / "stat_glossary.parquet"), format="parquet").to_table()
    assert table.to_pylist() == [{"stat_key": "points", "description": "new"}]


def test_write_glossary_does_not_rewrite_an_unchanged_file(tmp_path: Path) -> None:
    """Otherwise every pull marks stat_glossary as written, and a run with
    nothing new to say rebuilds a warehouse table for no reason."""
    first = Pipeline(None, tmp_path)
    first.glossary = {"points": {"stat_key": "points", "description": "points"}}
    first.write_glossary()

    second = Pipeline(None, tmp_path)
    second.glossary = {"points": {"stat_key": "points", "description": "points"}}
    second.write_glossary()
    assert second.written == set()


def test_write_glossary_with_nothing_collected_leaves_the_file_alone(tmp_path: Path) -> None:
    first = Pipeline(None, tmp_path)
    first.glossary = {"points": {"stat_key": "points", "description": "points"}}
    first.write_glossary()

    second = Pipeline(None, tmp_path)
    second.write_glossary()
    assert _glossary_keys(tmp_path) == {"points"}
    assert second.written == set()


# ---------------- concurrency ----------------


def test_map_runs_every_item_with_workers(tmp_path: Path) -> None:
    pipeline = Pipeline(None, tmp_path, workers=4)
    done: list[int] = []
    lock = threading.Lock()

    def work(item: int) -> None:
        with lock:
            done.append(item)

    pipeline._map(work, list(range(50)), desc="test")
    assert sorted(done) == list(range(50))


def test_map_actually_overlaps_the_work(tmp_path: Path) -> None:
    """The whole point: ESPN answers a cold game summary in ~250-400ms and the
    old loop waited for each one before starting the next, so a pull ran at
    2.4 requests/second against a --rate-limit of 10 that never had to sleep."""
    pipeline = Pipeline(None, tmp_path, workers=4)
    in_flight = 0
    peak = 0
    lock = threading.Lock()
    started = threading.Barrier(4, timeout=5)

    def work(_: int) -> None:
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        started.wait()  # deadlocks unless four really are in flight at once
        with lock:
            in_flight -= 1

    pipeline._map(work, list(range(8)), desc="test")
    assert peak == 4


def test_map_is_serial_and_pool_free_with_one_worker(tmp_path: Path) -> None:
    """The default path stays exactly the loop it replaced."""
    pipeline = Pipeline(None, tmp_path, workers=1)
    threads: set[int] = set()
    pipeline._map(lambda _: threads.add(threading.get_ident()), list(range(5)), desc="test")
    assert threads == {threading.get_ident()}


def test_map_propagates_the_first_failure(tmp_path: Path) -> None:
    """A failing fetch aborted the pull when this was a plain loop; it still
    must, rather than being swallowed by a future nobody reads."""
    pipeline = Pipeline(None, tmp_path, workers=4)

    def work(item: int) -> None:
        if item == 3:
            raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        pipeline._map(work, list(range(20)), desc="test")


def test_written_and_glossary_survive_concurrent_writers(tmp_path: Path) -> None:
    """Both are mutated by every worker. The glossary's read-modify-write is
    not atomic, and a lost update there is a silently smaller glossary."""
    pipeline = Pipeline(None, tmp_path, workers=8)

    def work(index: int) -> None:
        pipeline._add_glossary([{"stat_key": f"stat{index}", "description": "d"}])
        pipeline._write_rows(tmp_path / "standings" / f"season={index}" / "standings.parquet", [{"a": index}])

    pipeline._map(work, list(range(60)), desc="test")
    assert len(pipeline.glossary) == 60
    assert pipeline.written == {"standings"}


def test_workers_below_one_is_treated_as_serial(tmp_path: Path) -> None:
    assert Pipeline(None, tmp_path, workers=0).workers == 1
