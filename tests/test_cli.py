"""Sanity tests for CLI argument parsing (Click)."""

from typing import Any, cast

import click
import pytest
from click.testing import CliRunner

from association.cli import _parse_season_types, _parse_seasons, ai, cli, data_check, data_load, data_pull, query


def test_parse_seasons_single() -> None:
    assert _parse_seasons("2024") == [2024]


def test_parse_seasons_range() -> None:
    assert _parse_seasons("2022-2024") == [2022, 2023, 2024]


def test_parse_seasons_comma_list() -> None:
    assert _parse_seasons("2022,2023,2024") == [2022, 2023, 2024]


def test_parse_seasons_dedups_and_sorts_mixed_input() -> None:
    assert _parse_seasons("2024,2022-2023,2023") == [2022, 2023, 2024]


def test_parse_season_types() -> None:
    assert _parse_season_types("2,3") == [2, 3]


def test_parse_season_types_dedups_and_sorts() -> None:
    assert _parse_season_types("3,1,2,2") == [1, 2, 3]


def test_cli_lists_expected_commands() -> None:
    assert set(cli.commands.keys()) == {"data", "query", "ai"}
    data_group = cast(click.Group, cli.commands["data"])
    assert set(data_group.commands.keys()) == {"pull", "load", "check"}


def test_data_pull_requires_seasons() -> None:
    runner = CliRunner()
    result = runner.invoke(data_pull, [])
    assert result.exit_code != 0
    assert "seasons" in result.output.lower()


def test_data_pull_parses_seasons(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakePipeline:
        def __init__(self, client: object, data_dir: object, **kwargs: Any) -> None:
            captured["kwargs"] = kwargs

        def run(self, seasons: list[int], season_types: list[int]) -> None:
            captured["seasons"] = seasons
            captured["season_types"] = season_types

    monkeypatch.setattr("association.fetch.pipeline.Pipeline", FakePipeline)
    monkeypatch.setattr("association.fetch.client.ESPNClient", lambda **k: object())
    monkeypatch.setattr("association.fetch.warehouse.build", lambda *a, **k: None)

    runner = CliRunner()
    result = runner.invoke(data_pull, ["--seasons", "2024", "--fetch-only"])
    assert result.exit_code == 0, result.output
    assert captured["seasons"] == [2024]
    assert captured["kwargs"]["force"] is False


def test_data_pull_fetch_only_skips_warehouse_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """--fetch-only used to also silently drop --advanced-stats with no
    warning, back when that was a separate flag - now that advanced stats are
    unconditional whenever the warehouse IS built, the only thing left to
    verify is that --fetch-only really does skip the build step entirely."""
    build_calls: list[Any] = []

    class FakePipeline:
        def __init__(self, client: object, data_dir: object, **kwargs: Any) -> None:
            pass

        def run(self, seasons: list[int], season_types: list[int]) -> None:
            pass

    monkeypatch.setattr("association.fetch.pipeline.Pipeline", FakePipeline)
    monkeypatch.setattr("association.fetch.client.ESPNClient", lambda **k: object())
    monkeypatch.setattr("association.fetch.warehouse.build", lambda *a, **k: build_calls.append((a, k)))

    runner = CliRunner()
    result = runner.invoke(data_pull, ["--seasons", "2024", "--fetch-only"])
    assert result.exit_code == 0, result.output
    assert build_calls == []


def test_data_load_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "association.fetch.warehouse.build",
        lambda data_dir, db_path, tables=None: captured.update(tables=tables),
    )
    runner = CliRunner()
    result = runner.invoke(data_load, [])
    assert result.exit_code == 0, result.output
    assert captured["tables"] is None


def test_data_load_parses_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "association.fetch.warehouse.build",
        lambda data_dir, db_path, tables=None: captured.update(tables=tables),
    )
    runner = CliRunner()
    result = runner.invoke(data_load, ["--tables", "games,player_box_stats"])
    assert result.exit_code == 0, result.output
    assert captured["tables"] == ["games", "player_box_stats"]


def test_data_check_defaults_to_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """live now defaults to False (local-only, fast) - --live opts into the
    ESPN cross-check, replacing the old --offline opt-out."""
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "association.check.report.run_check",
        lambda data_dir, seasons, season_types, rate_limit, live, force: captured.update(live=live),
    )
    runner = CliRunner()
    result = runner.invoke(data_check, [])
    assert result.exit_code == 0, result.output
    assert captured["live"] is False


def test_data_check_live_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "association.check.report.run_check",
        lambda data_dir, seasons, season_types, rate_limit, live, force: captured.update(live=live),
    )
    runner = CliRunner()
    result = runner.invoke(data_check, ["--live"])
    assert result.exit_code == 0, result.output
    assert captured["live"] is True


def test_query_dispatches_with_question(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeAgent:
        def __init__(self, model: str, db_path: str, out_dir: object, verbose: bool, think: bool, fast_path: bool, router_model: str) -> None:
            captured["model"] = model

        def ask(self, question: str) -> str:
            captured["question"] = question
            return "the answer"

    monkeypatch.setattr("association.query.agent.Agent", FakeAgent)
    runner = CliRunner()
    result = runner.invoke(query, ["who led the league in assists"])
    assert result.exit_code == 0, result.output
    assert captured["question"] == "who led the league in assists"
    assert "the answer" in result.output


def test_ai_dispatches_with_think_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeAgent:
        def __init__(self, model: str, db_path: str, out_dir: object, verbose: bool, think: bool, fast_path: bool, router_model: str) -> None:
            captured["model"] = model
            captured["think"] = think
            captured["fast_path"] = fast_path
            captured["router_model"] = router_model

    monkeypatch.setattr("association.query.agent.Agent", FakeAgent)
    monkeypatch.setattr("association.query.repl.run_repl", lambda agent: None)
    runner = CliRunner()
    result = runner.invoke(ai, ["--think", "--model", "qwen3:8b"])
    assert result.exit_code == 0, result.output
    assert captured["think"] is True
    assert captured["model"] == "qwen3:8b"
    assert captured["fast_path"] is True
    # Routing and SQL generation run on different models by design.
    assert captured["router_model"] == "qwen2.5:3b"
    assert captured["model"] != captured["router_model"]


def test_version_flag_reports_the_packaged_version() -> None:
    """--version must agree with installed metadata, not a hand-copied literal.

    The whole point of deriving the number from importlib.metadata is that a
    bump touches pyproject.toml alone; this fails if someone reintroduces a
    literal that drifts.
    """
    from importlib.metadata import version

    for flag in ("--version", "-V"):
        result = CliRunner().invoke(cli, [flag])
        assert result.exit_code == 0, result.output
        assert version("association") in result.output


def test_dunder_version_matches_installed_metadata() -> None:
    """``association.__version__`` is the single source the CLI and docs read."""
    from importlib.metadata import version

    import association

    assert association.__version__ == version("association")


# ---------------- which tables a pull rebuilds ----------------


class _WrittenPipeline:
    """A Pipeline stand-in that reports having written a fixed set of tables."""

    written: set[str] = set()

    def __init__(self, client: object, data_dir: object, **kwargs: Any) -> None:
        pass

    def run(self, seasons: list[int], season_types: list[int]) -> None:
        pass


def _pull_with(monkeypatch: pytest.MonkeyPatch, written: set[str], db_exists: bool) -> list[Any]:
    """Run `data pull` with a faked pipeline and warehouse; return build calls."""
    build_calls: list[Any] = []

    pipeline_class = type("Pipeline", (_WrittenPipeline,), {"written": written})
    monkeypatch.setattr("association.fetch.pipeline.Pipeline", pipeline_class)
    monkeypatch.setattr("association.fetch.client.ESPNClient", lambda **k: object())
    monkeypatch.setattr("association.fetch.warehouse.build", lambda *a, **k: build_calls.append((a, k)))
    monkeypatch.setattr("pathlib.Path.exists", lambda self: db_exists)

    result = CliRunner().invoke(data_pull, ["--seasons", "2024"])
    assert result.exit_code == 0, result.output
    return build_calls


def test_a_pull_that_fetched_nothing_does_not_rebuild_the_warehouse(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reason `data pull` on a finished season took over a minute: the
    rebuild rescans the whole Parquet tree - 126,000 files here - and it ran
    unconditionally, including after a run that fetched nothing at all."""
    assert _pull_with(monkeypatch, written=set(), db_exists=True) == []


def test_a_pull_rebuilds_only_the_tables_it_wrote(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _pull_with(monkeypatch, written={"games", "player_box_stats"}, db_exists=True)
    assert len(calls) == 1
    assert calls[0][1]["tables"] == ["games", "player_box_stats"]


def test_a_missing_warehouse_is_built_in_full_however_little_was_fetched(monkeypatch: pytest.MonkeyPatch) -> None:
    """There is nothing for a subset to be a subset of - and the Parquet on
    disk may be from an earlier or interrupted run."""
    calls = _pull_with(monkeypatch, written={"games"}, db_exists=False)
    assert len(calls) == 1
    assert calls[0][1].get("tables") is None
