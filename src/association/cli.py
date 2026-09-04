#!/usr/bin/env python3
"""association - a single CLI for the ESPN NBA dataset: fetch it, audit it, query it."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import click

DEFAULT_DATA_DIR = "./data/parquet"
DEFAULT_DB_PATH = "./nba.duckdb"
DEFAULT_MODEL = "qwen2.5:7b"
DEFAULT_OUT_DIR = "./query_output"

LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]

F = TypeVar("F", bound=Callable[..., Any])

CLI_EPILOG = """
\b
Examples:
    association data pull --seasons 2024
    association data pull --seasons 2022-2024 --season-types 2,3 --include-pbp
    association data load --tables games,player_box_stats
    association data check --seasons 2020-2024
    association query "Who are the top 5 3-point shooters by shot volume?"
    association ai --model qwen3:8b --think --verbose

\b
Setup for query/ai (one-time):
    brew install ollama
    ollama serve &                # or `brew services start ollama`; must be running before use
    ollama pull qwen2.5:7b        # default model, ~4.7GB, no thinking support
    ollama pull qwen3:8b          # optional: visible reasoning traces (--think) + tool calling, ~5GB

Pulling any other model: `ollama pull <name>:<tag>` (browse at https://ollama.com/library).
Tool-calling support varies by model - check a model's page before swapping --model.

--think is much slower than the default (confirmed live on a 16GB M2: qwen3:8b's
thinking-token volume varies run to run, 3-6x+ the wall time of qwen2.5:7b for the
same question) - reach for it when investigating a wrong answer, not for routine
queries. Switching --model between calls also costs ~60-80s to swap the loaded
model on memory-constrained hardware - avoid alternating models call to call.

\b
Shell completion (one-time):
    bash: eval "$(_ASSOCIATION_COMPLETE=bash_source association)"   >> ~/.bashrc
    zsh:  eval "$(_ASSOCIATION_COMPLETE=zsh_source association)"    >> ~/.zshrc
    fish: _ASSOCIATION_COMPLETE=fish_source association | source    >> ~/.config/fish/config.fish
See completions/ in the repo for ready-made static scripts instead of the eval form.
"""


def _parse_seasons(spec: str) -> list[int]:
    seasons: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            seasons.update(range(int(a), int(b) + 1))
        else:
            seasons.add(int(part))
    return sorted(seasons)


def _parse_season_types(spec: str) -> list[int]:
    return sorted({int(x.strip()) for x in spec.split(",") if x.strip()})


def _query_engine_options(f: F) -> F:
    f = click.option("--model", default=DEFAULT_MODEL, show_default=True, help="Ollama model name.")(f)
    f = click.option("--db-path", default=DEFAULT_DB_PATH, show_default=True, help="DuckDB warehouse file.")(f)
    f = click.option("--out-dir", default=DEFAULT_OUT_DIR, show_default=True, help="Directory for rendered shot charts.")(f)
    f = click.option("--verbose", is_flag=True, help="Print tool calls as they happen.")(f)
    f = click.option(
        "--think",
        is_flag=True,
        help="Show the model's reasoning trace before each response (requires a thinking-capable "
        "model, e.g. qwen3:8b - qwen2.5 does not support this).",
    )(f)
    return f


@click.group(epilog=CLI_EPILOG, context_settings={"help_option_names": ["-h", "--help"]})
def cli() -> None:
    """Fetch, audit, and query ESPN's NBA stats via a local DuckDB warehouse."""


@cli.group()
def data() -> None:
    """Fetch and audit the ESPN NBA dataset."""


@data.command("pull")
@click.option("--seasons", required=True, help="e.g. '2024' or '2022-2024' or '2022,2023,2024' (ESPN's season year = the year the season ends)")
@click.option("--season-types", default="2,3", show_default=True, help="1=preseason 2=regular 3=postseason")
@click.option("--data-dir", default=DEFAULT_DATA_DIR, show_default=True, help="Parquet flat-file root")
@click.option("--db-path", default=DEFAULT_DB_PATH, show_default=True, help="DuckDB warehouse file")
@click.option(
    "--include-pbp", is_flag=True, help="Also parse play-by-play, shot_chart, win_probability (free - same API call, more disk/parse time)"
)
@click.option(
    "--include-net-points-daily",
    is_flag=True,
    help="Also fetch per-game NetPoints (net_points_player_game/net_points_team_game) - one extra, "
    "signed S3 request per date already covered locally, not per game or per player. Opt-in: needs "
    "boto3's Cognito credential exchange (see fetch/netpoints_client.py) and matches players by exact "
    "display-name (ambiguous/unmatched names are left unresolved, not guessed).",
)
@click.option("--rate-limit", type=float, default=5.0, show_default=True, help="Max requests/second against ESPN")
@click.option("--force", is_flag=True, help="Re-fetch even if already checkpointed as complete")
@click.option("--fetch-only", is_flag=True, help="Fetch Parquet files only, skip building the DuckDB warehouse")
@click.option(
    "--advanced-stats",
    is_flag=True,
    help="Also build computed player_advanced_stats/player_season_advanced_stats views "
    "(true shooting %, effective FG%, usage rate, game score - see fetch/advanced_stats.py)",
)
@click.option("--log-level", type=click.Choice(LOG_LEVELS), default="INFO", show_default=True)
def data_pull(
    seasons: str,
    season_types: str,
    data_dir: str,
    db_path: str,
    include_pbp: bool,
    include_net_points_daily: bool,
    rate_limit: float,
    force: bool,
    fetch_only: bool,
    advanced_stats: bool,
    log_level: str,
) -> None:
    """Resumable fetch from ESPN into Parquet + the DuckDB warehouse."""
    from .fetch import warehouse
    from .fetch.client import ESPNClient
    from .fetch.pipeline import Pipeline

    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parsed_seasons = _parse_seasons(seasons)
    parsed_season_types = _parse_season_types(season_types)
    data_dir_path = Path(data_dir)
    db_path_path = Path(db_path)

    client = ESPNClient(rate_limit=rate_limit)
    pipeline = Pipeline(
        client,
        data_dir_path,
        include_pbp=include_pbp,
        include_net_points_daily=include_net_points_daily,
        force=force,
    )
    pipeline.run(parsed_seasons, parsed_season_types)

    if not fetch_only:
        warehouse.build(data_dir_path, db_path_path, include_advanced_stats=advanced_stats)


@data.command("load")
@click.option("--data-dir", default=DEFAULT_DATA_DIR, show_default=True, help="Parquet flat-file root")
@click.option("--db-path", default=DEFAULT_DB_PATH, show_default=True, help="DuckDB warehouse file")
@click.option(
    "--tables",
    default=None,
    help="Comma-separated subset of tables to (re)load, e.g. 'games,player_box_stats' "
    "(unknown names error out). Default: every table with Parquet files on disk.",
)
@click.option("--advanced-stats", is_flag=True, help="Also (re)build the computed player_advanced_stats/player_season_advanced_stats views.")
@click.option("--log-level", type=click.Choice(LOG_LEVELS), default="INFO", show_default=True)
def data_load(data_dir: str, db_path: str, tables: str | None, advanced_stats: bool, log_level: str) -> None:
    """(Re)build the DuckDB warehouse from Parquet files already on disk, without fetching."""
    from .fetch import warehouse

    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parsed_tables = [t.strip() for t in tables.split(",") if t.strip()] if tables else None
    warehouse.build(Path(data_dir), Path(db_path), tables=parsed_tables, include_advanced_stats=advanced_stats)


@data.command("check")
@click.option("--seasons", default=None, help="e.g. '2020-2024'. Defaults to every season with local data.")
@click.option("--season-types", default="1,2,3", show_default=True)
@click.option("--data-dir", default=DEFAULT_DATA_DIR, show_default=True)
@click.option("--rate-limit", type=float, default=5.0, show_default=True)
@click.option("--offline", is_flag=True, help="Skip the live ESPN schedule cross-check (fast, but can't report expected game counts).")
@click.option("--force", is_flag=True, help="Re-verify live against ESPN even for seasons already marked complete locally.")
def data_check(seasons: str | None, season_types: str, data_dir: str, rate_limit: float, offline: bool, force: bool) -> None:
    """Report data coverage vs. what ESPN's API actually has, per season/season_type."""
    from .check.report import run_check

    parsed_seasons = _parse_seasons(seasons) if seasons else None
    parsed_season_types = _parse_season_types(season_types)
    run_check(Path(data_dir), parsed_seasons, parsed_season_types, rate_limit=rate_limit, live=not offline, force=force)


@cli.command("query")
@click.argument("question")
@_query_engine_options
def query(question: str, model: str, db_path: str, out_dir: str, verbose: bool, think: bool) -> None:
    """Ask one natural-language question about the local data."""
    from .query.agent import Agent

    agent = Agent(model, db_path, Path(out_dir), verbose=verbose, think=think)
    click.echo(agent.ask(question))


@cli.command("ai")
@_query_engine_options
def ai(model: str, db_path: str, out_dir: str, verbose: bool, think: bool) -> None:
    """Interactive REPL - keeps conversation history across questions."""
    from .query.agent import Agent
    from .query.repl import run_repl

    agent = Agent(model, db_path, Path(out_dir), verbose=verbose, think=think)
    run_repl(agent)


def main() -> None:
    cli(prog_name="association")


if __name__ == "__main__":
    main()
