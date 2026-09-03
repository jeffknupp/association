"""Sanity tests for CLI argument parsing."""

from association.cli import _parse_season_types, _parse_seasons, build_parser


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


def test_build_parser_dispatches_to_data_pull() -> None:
    parser = build_parser()
    args = parser.parse_args(["data", "pull", "--seasons", "2024"])
    assert args.command == "data"
    assert args.data_command == "pull"
    assert args.seasons == "2024"


def test_build_parser_data_pull_advanced_stats_flag() -> None:
    parser = build_parser()
    args = parser.parse_args(["data", "pull", "--seasons", "2024", "--advanced-stats"])
    assert args.advanced_stats is True

    default_args = parser.parse_args(["data", "pull", "--seasons", "2024"])
    assert default_args.advanced_stats is False


def test_build_parser_dispatches_to_data_check() -> None:
    parser = build_parser()
    args = parser.parse_args(["data", "check", "--offline"])
    assert args.data_command == "check"
    assert args.offline is True


def test_build_parser_dispatches_to_query() -> None:
    parser = build_parser()
    args = parser.parse_args(["query", "who led the league in assists"])
    assert args.command == "query"
    assert args.question == "who led the league in assists"


def test_build_parser_dispatches_to_ai() -> None:
    parser = build_parser()
    args = parser.parse_args(["ai", "--think", "--model", "qwen3:8b"])
    assert args.command == "ai"
    assert args.think is True
    assert args.model == "qwen3:8b"
