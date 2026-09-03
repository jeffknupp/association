#!/usr/bin/env python3
"""association - a single CLI for the ESPN NBA dataset: fetch it, audit it, query it.

Subcommands:
    association data pull [...]     resumable fetch from ESPN -> Parquet -> DuckDB warehouse
    association data load [...]     (re)build the DuckDB warehouse from Parquet already on disk
    association data check [...]    report data coverage, optionally cross-checked live vs ESPN
    association query "..."         one-shot natural-language question (local LLM, no cloud calls)
    association ai [...]            interactive REPL (same engine as `query`, keeps context)

Examples:
    association data pull --seasons 2024
    association data pull --seasons 2022-2024 --season-types 2,3 --include-pbp
    association data load --tables games,player_box_stats
    association data check --seasons 2020-2024
    association query "Who are the top 5 3-point shooters by shot volume?"
    association ai --model qwen3:8b --think --verbose

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
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

DEFAULT_DATA_DIR = "./data/parquet"
DEFAULT_DB_PATH = "./nba.duckdb"
DEFAULT_MODEL = "qwen2.5:7b"
DEFAULT_OUT_DIR = "./query_output"


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


def _cmd_data_pull(args: argparse.Namespace) -> None:
    from .fetch import warehouse
    from .fetch.client import ESPNClient
    from .fetch.pipeline import Pipeline

    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    seasons = _parse_seasons(args.seasons)
    season_types = _parse_season_types(args.season_types)
    data_dir = Path(args.data_dir)
    db_path = Path(args.db_path)

    client = ESPNClient(rate_limit=args.rate_limit)
    pipeline = Pipeline(client, data_dir, include_pbp=args.include_pbp, force=args.force)
    pipeline.run(seasons, season_types)

    if not args.fetch_only:
        warehouse.build(data_dir, db_path, include_advanced_stats=args.advanced_stats)


def _cmd_data_load(args: argparse.Namespace) -> None:
    from .fetch import warehouse

    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    tables = [t.strip() for t in args.tables.split(",") if t.strip()] if args.tables else None
    warehouse.build(Path(args.data_dir), Path(args.db_path), tables=tables, include_advanced_stats=args.advanced_stats)


def _cmd_data_check(args: argparse.Namespace) -> None:
    from .check.report import run_check

    seasons = _parse_seasons(args.seasons) if args.seasons else None
    season_types = _parse_season_types(args.season_types)
    run_check(Path(args.data_dir), seasons, season_types, rate_limit=args.rate_limit, live=not args.offline, force=args.force)


def _cmd_query(args: argparse.Namespace) -> None:
    from .query.agent import Agent

    agent = Agent(args.model, args.db_path, Path(args.out_dir), verbose=args.verbose, think=args.think)
    print(agent.ask(args.question))


def _cmd_ai(args: argparse.Namespace) -> None:
    from .query.agent import Agent
    from .query.repl import run_repl

    agent = Agent(args.model, args.db_path, Path(args.out_dir), verbose=args.verbose, think=args.think)
    run_repl(agent)


def _add_query_engine_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--db-path", default=DEFAULT_DB_PATH)
    p.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    p.add_argument("--verbose", action="store_true", help="Print tool calls as they happen.")
    p.add_argument(
        "--think",
        action="store_true",
        help="Show the model's reasoning trace before each response (requires a thinking-capable "
        "model, e.g. qwen3:8b - qwen2.5 does not support this).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="association", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    data_p = sub.add_parser("data", help="Fetch and audit the ESPN NBA dataset.")
    data_sub = data_p.add_subparsers(dest="data_command", required=True)

    pull_p = data_sub.add_parser("pull", help="Resumable fetch from ESPN into Parquet + the DuckDB warehouse.")
    pull_p.add_argument(
        "--seasons",
        required=True,
        help="e.g. '2024' or '2022-2024' or '2022,2023,2024' "
        "(ESPN's season year = the year the season ends)",
    )
    pull_p.add_argument("--season-types", default="2,3", help="1=preseason 2=regular 3=postseason (default: 2,3)")
    pull_p.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help=f"Parquet flat-file root (default: {DEFAULT_DATA_DIR})")
    pull_p.add_argument("--db-path", default=DEFAULT_DB_PATH, help=f"DuckDB warehouse file (default: {DEFAULT_DB_PATH})")
    pull_p.add_argument(
        "--include-pbp",
        action="store_true",
        help="Also parse play-by-play, shot_chart, win_probability (free - same API call, more disk/parse time)",
    )
    pull_p.add_argument("--rate-limit", type=float, default=5.0, help="Max requests/second against ESPN (default: 5)")
    pull_p.add_argument("--force", action="store_true", help="Re-fetch even if already checkpointed as complete")
    pull_p.add_argument("--fetch-only", action="store_true", help="Fetch Parquet files only, skip building the DuckDB warehouse")
    pull_p.add_argument(
        "--advanced-stats",
        action="store_true",
        help="Also build computed player_advanced_stats/player_season_advanced_stats views "
        "(true shooting %%, effective FG%%, usage rate, game score - see fetch/advanced_stats.py)",
    )
    pull_p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    pull_p.set_defaults(func=_cmd_data_pull)

    load_p = data_sub.add_parser(
        "load",
        help="(Re)build the DuckDB warehouse from Parquet files already on disk, without fetching.",
    )
    load_p.add_argument("--data-dir", default=DEFAULT_DATA_DIR, help=f"Parquet flat-file root (default: {DEFAULT_DATA_DIR})")
    load_p.add_argument("--db-path", default=DEFAULT_DB_PATH, help=f"DuckDB warehouse file (default: {DEFAULT_DB_PATH})")
    load_p.add_argument(
        "--tables",
        default=None,
        help="Comma-separated subset of tables to (re)load, e.g. 'games,player_box_stats' "
        "(unknown names error out). Default: every table with Parquet files on disk.",
    )
    load_p.add_argument(
        "--advanced-stats",
        action="store_true",
        help="Also (re)build the computed player_advanced_stats/player_season_advanced_stats views.",
    )
    load_p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    load_p.set_defaults(func=_cmd_data_load)

    check_p = data_sub.add_parser(
        "check", help="Report data coverage vs. what ESPN's API actually has, per season/season_type."
    )
    check_p.add_argument("--seasons", default=None, help="e.g. '2020-2024'. Defaults to every season with local data.")
    check_p.add_argument("--season-types", default="1,2,3")
    check_p.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    check_p.add_argument("--rate-limit", type=float, default=5.0)
    check_p.add_argument(
        "--offline",
        action="store_true",
        help="Skip the live ESPN schedule cross-check (fast, but can't report expected game counts).",
    )
    check_p.add_argument(
        "--force",
        action="store_true",
        help="Re-verify live against ESPN even for seasons already marked complete locally.",
    )
    check_p.set_defaults(func=_cmd_data_check)

    query_p = sub.add_parser("query", help="Ask one natural-language question about the local data.")
    query_p.add_argument("question", help="Natural-language question about the NBA data.")
    _add_query_engine_args(query_p)
    query_p.set_defaults(func=_cmd_query)

    ai_p = sub.add_parser("ai", help="Interactive REPL - keeps conversation history across questions.")
    _add_query_engine_args(ai_p)
    ai_p.set_defaults(func=_cmd_ai)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
