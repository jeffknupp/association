"""Regression tests for `association data check`.

Covers two real bugs from the same incident: the reported "missing" game
count didn't account for locally-resolved (postponed/cancelled) games, making
even a fully-accounted-for season look incomplete; and the live schedule
cross-check re-hit ESPN's API every run even for seasons already verified
complete by `pull`.
"""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from association.check import report


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


def test_resolved_count_counts_markers(tmp_path: Path) -> None:
    d = tmp_path / "_resolved" / "season=2024" / "season_type=2"
    d.mkdir(parents=True)
    (d / "event_1.marker").touch()
    (d / "event_2.marker").touch()
    assert report._resolved_count(tmp_path, 2024, 2) == 2


def test_resolved_count_zero_when_dir_missing(tmp_path: Path) -> None:
    assert report._resolved_count(tmp_path, 2024, 2) == 0


def test_run_check_no_local_data_prints_guidance(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    report.run_check(tmp_path, seasons=None, season_types=[2], live=False)
    out = capsys.readouterr().out
    assert "association data pull" in out


def test_run_check_offline_accounts_for_resolved_games_in_have_count(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Regression: a season with 2 real games + 1 postponed game used to
    report a misleading "2/?" (or "2/4" live) instead of counting the
    postponed game as accounted-for, not missing."""
    _write(tmp_path / "games" / "season=2024" / "season_type=2" / "event_1.parquet", [{"season": 2024, "season_type": 2, "event_id": "1"}])
    _write(tmp_path / "games" / "season=2024" / "season_type=2" / "event_2.parquet", [{"season": 2024, "season_type": 2, "event_id": "2"}])
    resolved_dir = tmp_path / "_resolved" / "season=2024" / "season_type=2"
    resolved_dir.mkdir(parents=True)
    (resolved_dir / "event_3.marker").touch()

    report.run_check(tmp_path, seasons=[2024], season_types=[2], live=False)
    out = capsys.readouterr().out
    assert "3/?" in out  # 2 played + 1 resolved = 3 accounted for, not just 2


def test_run_check_cached_complete_skips_live_schedule_call(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The core fix: a season/type already marked complete by `pull` must not
    trigger a fresh live schedule request - the marker is trusted."""
    _write(tmp_path / "games" / "season=2024" / "season_type=2" / "event_1.parquet", [{"season": 2024, "season_type": 2, "event_id": "1"}])
    (tmp_path / "_complete" / "season=2024").mkdir(parents=True)
    (tmp_path / "_complete" / "season=2024" / "season_type=2.marker").touch()

    def _boom(*a: object, **k: object) -> None:
        raise AssertionError("event_ids_for should not be called for an already-complete season")

    monkeypatch.setattr("association.check.report.Pipeline.event_ids_for", _boom)
    monkeypatch.setattr("association.check.report.Pipeline.team_ids", lambda self: [])
    monkeypatch.setattr("association.check.report.ESPNClient.__init__", lambda self, **k: None)

    report.run_check(tmp_path, seasons=[2024], season_types=[2], live=True)
    out = capsys.readouterr().out
    assert "1/1*" in out
    assert "trusted from local completion marker" in out


def test_run_check_force_bypasses_cache_and_hits_live(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path / "games" / "season=2024" / "season_type=2" / "event_1.parquet", [{"season": 2024, "season_type": 2, "event_id": "1"}])
    (tmp_path / "_complete" / "season=2024").mkdir(parents=True)
    (tmp_path / "_complete" / "season=2024" / "season_type=2.marker").touch()

    calls = {"n": 0}

    def _fake_event_ids_for(self: object, season: int, season_type: int, team_ids: list[str]) -> list[str]:
        calls["n"] += 1
        return ["1", "2"]

    monkeypatch.setattr("association.check.report.Pipeline.event_ids_for", _fake_event_ids_for)
    monkeypatch.setattr("association.check.report.Pipeline.team_ids", lambda self: [])
    monkeypatch.setattr("association.check.report.ESPNClient.__init__", lambda self, **k: None)

    report.run_check(tmp_path, seasons=[2024], season_types=[2], live=True, force=True)
    out = capsys.readouterr().out
    assert calls["n"] == 1
    assert "1/2" in out
    assert "*" not in out.split("\n")[2]  # the data row itself, not force-cached


def test_discover_seasons_reads_season_directories(tmp_path: Path) -> None:
    (tmp_path / "games" / "season=2023").mkdir(parents=True)
    (tmp_path / "games" / "season=2024").mkdir(parents=True)
    assert report.discover_seasons(tmp_path) == [2023, 2024]


def test_discover_seasons_empty_when_no_games_dir(tmp_path: Path) -> None:
    assert report.discover_seasons(tmp_path) == []


def test_run_check_reports_net_points_player_count(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(
        tmp_path / "net_points_player" / "season=2024" / "regular_season.parquet",
        [
            {"athlete_id": 1, "season": 2024, "net_points_season_type": "Regular Season", "overall": 1.0},
            {"athlete_id": 2, "season": 2024, "net_points_season_type": "Regular Season", "overall": 2.0},
        ],
    )
    report.run_check(tmp_path, seasons=[2024], season_types=[2], live=False)
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip().startswith("2024")]
    assert len(lines) == 1
    assert lines[0].split()[-2] == "2"  # net_pts column (np_gm, the daily table, is last)
    assert "NetPoints" in out


def test_run_check_net_points_zero_for_preseason(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """NetPoints has no preseason equivalent - season_type=1 should always
    report 0, not error out on a missing label mapping."""
    report.run_check(tmp_path, seasons=[2024], season_types=[1], live=False)
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip().startswith("2024")]
    assert lines[0].split()[-2] == "0"


def test_run_check_reports_net_points_daily_count(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(
        tmp_path / "net_points_player_game" / "season=2024" / "date=2024-01-01.parquet",
        [
            {"event_id": "1", "season": 2024, "season_type": 2, "team_id": "1", "athlete_id": "1", "t_net_pts": 1.0},
            {"event_id": "1", "season": 2024, "season_type": 2, "team_id": "2", "athlete_id": "2", "t_net_pts": 2.0},
        ],
    )
    report.run_check(tmp_path, seasons=[2024], season_types=[2], live=False)
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip().startswith("2024")]
    assert lines[0].split()[-1] == "2"
    assert "np_gm" in out
    assert "net_points_player_game" in out
