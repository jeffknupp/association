"""The ``association`` commands: ``data pull``, ``data load``, ``data check``, ``query`` and ``web``.

.. versionadded:: 3.0.0
   Split out of the former ``association.cli`` module, which is now a package.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

from association import __version__
from association.cli.paths import default_data_dir, default_db_path, default_sim_path

# query.models holds no heavy imports, so this does not pull ollama or duckdb
# into CLI startup - unlike the Agent import, which stays lazy below.
from association.query.models import DEFAULT_ROUTER_MODEL

log: logging.Logger = logging.getLogger("association.cli")

# Resolved once at import, against the process's cwd - a worktree with no
# warehouse of its own gets the main checkout's, per association.cli.paths.
DEFAULT_DATA_DIR = default_data_dir()
DEFAULT_DB_PATH = default_db_path()
DEFAULT_SIM_PATH = default_sim_path()
DEFAULT_OUT_DIR = "./query_output"

LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]


def _configure_logging(log_level: str) -> None:
    """Send this package's log lines to stderr at ``log_level``.

    Deliberately not ``logging.basicConfig``, which configures the *root*
    logger: called from inside a subcommand it reconfigures logging for the
    whole process, including for whatever imported us. A CLI owns its
    interpreter and can get away with that, but these are ordinary functions
    that a library, a test, or the web server can reach, and none of those
    asked to have their handlers set.

    Every logger in the package is named ``association.*``, so attaching one
    handler here catches all of them, and ``propagate = False`` keeps the lines
    from also going wherever the host sends root's. Idempotent: two commands in
    one process must not print everything twice.
    """
    logger = logging.getLogger("association")
    logger.setLevel(log_level)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
    logger.propagate = False


CLI_EPILOG = f"""
Examples:

\b
    association data pull --seasons 2024
    association data pull --seasons 2022-2024 --season-types 2,3 --include-pbp
    association data load --tables games,player_box_stats
    association data check --seasons 2020-2024
    association query "Who are the top 5 3-point shooters by shot volume?"
    association query --verbose "Who led the league in blocks?"

Setup for query (one-time):

\b
    brew install ollama
    ollama serve &                # or `brew services start ollama`; must be running before use
    ollama pull {DEFAULT_ROUTER_MODEL}        # --router-model default, ~1.9GB - the only model

Pulling any other model: `ollama pull <name>:<tag>` (browse at https://ollama.com/library).

A question the parser can read is answered by a deterministic SQL template or the
compiler - one model call, which only copies the names out of the question and
picks a stat, ~1s warm on an 8-core CPU box. A question nothing here reads is
refused in the same second, and the refusal names why: nothing writes SQL from a
question any more (a tool-calling agent used to, answering 1 question in 23 over
minutes).

One small model by design: the normalizer copies names and picks one stat key under
a JSON schema, and measured over 277 real questions the 3B copies 299 of 302 names
verbatim at 0.8s a question while a 7B adds nothing on names at 1.8s.

Do NOT point --router-model at a thinking model: on the router's classification the
normalizer replaced, qwen3:4b took ~20s per question against qwen2.5:3b's 1.1s,
reasoning at length before emitting the same tiny JSON.

Shell completion (one-time):

\b
    bash: eval "$(_ASSOCIATION_COMPLETE=bash_source association)"   >> ~/.bashrc
    zsh:  eval "$(_ASSOCIATION_COMPLETE=zsh_source association)"    >> ~/.zshrc
    fish: _ASSOCIATION_COMPLETE=fish_source association | source    >> ~/.config/fish/config.fish

See completions/ in the repo for ready-made static scripts instead of the eval form.
"""


def _parse_seasons(spec: str) -> list[int]:
    seasons: set[int] = set()
    for raw_part in spec.split(","):
        part = raw_part.strip()
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


def _query_engine_options[F: Callable[..., Any]](f: F) -> F:
    f = click.option(
        "--router-model",
        default=DEFAULT_ROUTER_MODEL,
        show_default=True,
        help="Ollama model for the normalizer, which copies the names out of the question and picks a stat. Smaller on purpose - see the setup notes below.",
    )(f)
    f = click.option("--db-path", default=DEFAULT_DB_PATH, show_default=True, help="DuckDB warehouse file.")(f)
    f = click.option("--out-dir", default=DEFAULT_OUT_DIR, show_default=True, help="Directory for rendered shot charts.")(f)
    return click.option("--verbose", is_flag=True, help="Print the trace - the normalizer's reply, the parser's reading, the template or compiled point - as it happens.")(f)


@click.group(epilog=CLI_EPILOG, context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, "-V", "--version", prog_name="association")
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
@click.option("--include-pbp", is_flag=True, help="Also parse play-by-play, shot_chart, win_probability (free - same API call, more disk/parse time)")
@click.option(
    "--include-net-points-daily",
    is_flag=True,
    help="Also fetch per-game NetPoints (net_points_player_game/net_points_team_game) and the per-game "
    "play-type split (net_points_player_game_fingerprint) - two extra, signed S3 requests per date already "
    "covered locally (one per file), not per game or per player. Opt-in: needs "
    "boto3's Cognito credential exchange (see fetch/netpoints_client.py) and matches players by exact "
    "display-name (ambiguous/unmatched names are left unresolved, not guessed).",
)
@click.option("--rate-limit", type=float, default=5.0, show_default=True, help="Max requests/second against ESPN, across all workers")
@click.option(
    "--workers",
    type=int,
    default=4,
    show_default=True,
    help="How many requests to keep in flight. ESPN answers a cold game summary in ~250-400ms, and requests were "
    "issued one at a time, so a pull ran at ~2.4/s no matter what --rate-limit said. --rate-limit still bounds the "
    "request RATE across all workers; this only stops the pull falling short of it. Use 1 for the old serial behavior.",
)
@click.option("--force", is_flag=True, help="Re-fetch even if already checkpointed as complete")
@click.option("--fetch-only", is_flag=True, help="Fetch Parquet files only, skip building the DuckDB warehouse")
@click.option("--log-level", type=click.Choice(LOG_LEVELS), default="INFO", show_default=True)
def data_pull(
    seasons: str,
    season_types: str,
    data_dir: str,
    db_path: str,
    include_pbp: bool,
    include_net_points_daily: bool,
    rate_limit: float,
    workers: int,
    force: bool,
    fetch_only: bool,
    log_level: str,
) -> None:
    """Resumable fetch from ESPN into Parquet + the DuckDB warehouse."""
    # Imported inside each command, not at module level, so `association
    # --help` and tab-completion do not pay to import duckdb, pyarrow and
    # ollama. Leave them here.
    from association.fetch import warehouse
    from association.fetch.client import ESPNClient
    from association.fetch.pipeline import Pipeline

    _configure_logging(log_level)
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
        workers=workers,
    )
    pipeline.run(parsed_seasons, parsed_season_types)

    if not fetch_only:
        _rebuild_changed(warehouse, data_dir_path, db_path_path, pipeline.written)


def _rebuild_changed(warehouse: Any, data_dir: Path, db_path: Path, written: set[str]) -> None:
    """Reload only the tables this pull actually wrote.

    A full rebuild rescans the whole Parquet tree - 126,000 files and minutes
    of work on a mature warehouse - so doing it after a pull that fetched
    nothing is the single reason `data pull` on a finished season was slow.
    A run that wrote nothing needs no rebuild at all.

    A warehouse that does not exist yet is built in full regardless: the
    Parquet on disk may be from earlier runs (or an interrupted one), and there
    would be nothing for a subset to be a subset OF.
    """
    if not db_path.exists():
        log.info("no warehouse at %s yet - building every table", db_path)
        warehouse.build(data_dir, db_path)
        return
    if not written:
        log.info("nothing fetched - warehouse left as it is (use `association data load` to rebuild it anyway)")
        return
    log.info("rebuilding changed tables: %s", ", ".join(sorted(written)))
    warehouse.build(data_dir, db_path, tables=sorted(written))


@data.command("load")
@click.option("--data-dir", default=DEFAULT_DATA_DIR, show_default=True, help="Parquet flat-file root")
@click.option("--db-path", default=DEFAULT_DB_PATH, show_default=True, help="DuckDB warehouse file")
@click.option(
    "--tables",
    default=None,
    help="Comma-separated subset of tables to (re)load, e.g. 'games,player_box_stats' (unknown names error out). Default: every table with Parquet files on disk.",
)
@click.option("--log-level", type=click.Choice(LOG_LEVELS), default="INFO", show_default=True)
def data_load(data_dir: str, db_path: str, tables: str | None, log_level: str) -> None:
    """(Re)build the DuckDB warehouse from Parquet files already on disk, without fetching."""
    from association.fetch import warehouse

    _configure_logging(log_level)
    parsed_tables = [t.strip() for t in tables.split(",") if t.strip()] if tables else None
    warehouse.build(Path(data_dir), Path(db_path), tables=parsed_tables)


@data.command("check")
@click.option("--seasons", default=None, help="e.g. '2020-2024'. Defaults to every season with local data.")
@click.option("--season-types", default=None, help="e.g. '2,3'. Defaults to every season_type with local data (not a fixed 1,2,3).")
@click.option("--data-dir", default=DEFAULT_DATA_DIR, show_default=True, help="Parquet flat-file root - read directly, no --db-path needed.")
@click.option("--rate-limit", type=float, default=5.0, show_default=True)
@click.option(
    "--live",
    is_flag=True,
    help="Cross-check against ESPN's live schedule to report expected game counts, not just what's on disk "
    "(slower - a real network call per season/type not yet checkpointed complete). Off by default: a plain "
    "run only reports local coverage.",
)
@click.option("--force", is_flag=True, help="With --live, re-verify against ESPN even for seasons already marked complete locally.")
def data_check(seasons: str | None, season_types: str | None, data_dir: str, rate_limit: float, live: bool, force: bool) -> None:
    """Report data coverage vs. what ESPN's API actually has, per season/season_type."""
    from association.check.report import run_check

    parsed_seasons = _parse_seasons(seasons) if seasons else None
    parsed_season_types = _parse_season_types(season_types) if season_types else None
    run_check(Path(data_dir), parsed_seasons, parsed_season_types, rate_limit=rate_limit, live=live, force=force)


@cli.group()
def sim() -> None:
    """Build and run the possession-level game simulator (experimental; SIMULATION_PLAN.md)."""


@sim.command("build")
@click.option("--seasons", default=None, help="e.g. '2019-2026'. Defaults to every season from the floor (2007).")
@click.option("--db-path", default=DEFAULT_DB_PATH, show_default=True, help="DuckDB warehouse file, attached read-only.")
@click.option("--sim-path", default=DEFAULT_SIM_PATH, show_default=True, help="The simulator's own DuckDB file; created if missing, its tables replaced.")
@click.option("--log-level", type=click.Choice(LOG_LEVELS), default="INFO", show_default=True)
def sim_build(seasons: str | None, db_path: str, sim_path: str, log_level: str) -> None:
    """(Re)create the simulator's derived tables from the warehouse.

    Phase 1 today: ``sim_plays``, every play of every real game classified,
    ordered and timed, with a per-season report of what was kept and how it
    checks against the final score. Later phases add their tables here.
    """
    _configure_logging(log_level)
    from association.sim import database, plays, segments

    wanted = _parse_seasons(seasons) if seasons else None
    con = database.connect(Path(sim_path), Path(db_path))
    try:
        reports = plays.build_plays(con, wanted)
        segment_reports = segments.build_segments(con, wanted)
        database.write_meta(con, seasons=[r.season for r in reports], warehouse_path=Path(db_path))
    finally:
        con.close()
    click.echo("sim_plays")
    click.echo(f"{'season':>6} {'games':>6} {'plays':>8} {'unmapped':>8} {'reach final':>11} {'points sum':>10}")
    for r in reports:
        click.echo(f"{r.season:>6} {r.games:>6} {r.plays:>8} {r.unmapped:>8} {r.games_reaching_final:>11} {r.points_match_final:>10}")
    click.echo("sim_segments (minutes and plus-minus over fully resolved games)")
    click.echo(
        f"{'season':>6} {'games':>6} {'resolved s':>10} {'starters':>9} {'player-games':>12} {'min within 1':>12} {'+/- exact':>9} {'phantoms':>8} {'one-id':>6} {'inconsist':>9} {'unrostered':>10}"
    )
    for s in segment_reports:
        shares = f"{100 * s.resolved_share:>9.2f}% {100 * s.starters_share:>8.2f}% {s.player_games:>12} {100 * s.minutes_share:>11.2f}% {100 * s.plus_minus_share:>8.2f}%"
        click.echo(f"{s.season:>6} {s.games:>6} {shares} {s.phantom_touches:>8} {s.one_id_subs:>6} {s.inconsistent_subs:>9} {s.unrostered_touches:>10}")
    click.echo(f"wrote {sim_path}")


def _sim_output(prepared: Any, games: list[Any], summary: Any, *, pbp: bool, as_json: bool, meta: dict[str, Any]) -> None:
    """Print a run: the summary (and, with ``--pbp``, the first game's
    play-by-play and box score), as text or as JSON."""
    import json

    from association.sim import game as sim_game_module
    from association.sim import render

    if as_json:
        payload: dict[str, Any] = {"summary": sim_game_module.summary_json(summary, prepared.names)}
        if pbp:
            payload["game"] = render.game_json(games[0], prepared.names, meta)
        click.echo(json.dumps(payload, indent=2))
        return
    if pbp:
        click.echo(render.play_by_play(games[0], prepared.names))
        click.echo("")
        click.echo(render.box_score(games[0], prepared.names, prepared.near_prior))
        click.echo("")
    click.echo(sim_game_module.summary_text(summary, prepared.names))


def _sim_run_options[F: Callable[..., Any]](f: F) -> F:
    f = click.option("--n", type=int, default=1000, show_default=True, help="How many games to simulate.")(f)
    f = click.option("--seed", type=int, default=7, show_default=True, help="Game i of n is played from seed + i.")(f)
    f = click.option("--pbp", is_flag=True, help="Also print the first game's play-by-play and box score.")(f)
    f = click.option("--json", "as_json", is_flag=True, help="Emit JSON (schema sim.1) instead of text.")(f)
    f = click.option("--db-path", default=DEFAULT_DB_PATH, show_default=True, help="DuckDB warehouse file, attached read-only.")(f)
    return click.option("--sim-path", default=DEFAULT_SIM_PATH, show_default=True, help="The simulator's own DuckDB file (`association sim build`).")(f)


@sim.command("game")
@click.argument("home")
@click.argument("away")
@click.option("--season", type=int, default=None, help="The season the matchup is set in (the year it ends). Defaults to the latest on record.")
@click.option("--as-of", "as_of", type=click.DateTime(formats=["%Y-%m-%d"]), default=None, help="Rates from games before this date. Defaults to the day after the season's last regular-season game.")
@click.option("--home-roster", default=None, help="Comma-separated athlete ids to play for the home side instead of its regulars.")
@click.option("--away-roster", default=None, help="Comma-separated athlete ids to play for the away side instead of its regulars.")
@_sim_run_options
def sim_game(home: str, away: str, season: int | None, as_of: Any, home_roster: str | None, away_roster: str | None, n: int, seed: int, pbp: bool, as_json: bool, db_path: str, sim_path: str) -> None:
    """Simulate HOME against AWAY (abbreviations, nicknames or names) N times
    from the last three seasons' rates and print the summary.

    Experimental (SIMULATION_PLAN.md). Every output states the window the
    rates came from, the seed, the rosters used and the players simulated at
    nearly the league prior.
    """
    from association.sim import database
    from association.sim import game as sim_game_module

    con = database.connect(Path(sim_path), Path(db_path), read_only=True)
    try:
        home_id, away_id = sim_game_module.resolve_team(con, home), sim_game_module.resolve_team(con, away)
        chosen = season or sim_game_module.latest_season(con)
        window = sim_game_module.window_for(con, chosen, as_of.date() if as_of else None)
        rosters = {t: r.split(",") for t, r in ((home_id, home_roster), (away_id, away_roster)) if r}
        prepared = sim_game_module.prepare(con, home_id, away_id, window, rosters=rosters or None)
    except sim_game_module.SimulationRefused as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        con.close()
    games = sim_game_module.run(prepared, n, seed)
    summary = sim_game_module.summarize(games, prepared, seed)
    _sim_output(prepared, games, summary, pbp=pbp, as_json=as_json, meta={"window": window.describe(), "season": chosen})


@sim.command("replay")
@click.argument("event_id")
@_sim_run_options
def sim_replay(event_id: str, n: int, seed: int, pbp: bool, as_json: bool, db_path: str, sim_path: str) -> None:
    """Replay a real game N times: the players who appeared, rates from the
    games before that day, and the real result beside the simulated one.

    Experimental (SIMULATION_PLAN.md).
    """
    from association.sim import database
    from association.sim import game as sim_game_module

    con = database.connect(Path(sim_path), Path(db_path), read_only=True)
    try:
        real = sim_game_module.real_game(con, event_id)
        prepared = sim_game_module.prepare_replay(con, real)
    except sim_game_module.SimulationRefused as exc:
        raise click.ClickException(str(exc)) from exc
    finally:
        con.close()
    games = sim_game_module.run(prepared, n, seed)
    summary = sim_game_module.summarize(games, prepared, seed)
    names = prepared.names
    if not as_json:
        click.echo(f"real result, {real.date.isoformat()}: {names.abbreviation(real.away)} {real.away_score}, {names.abbreviation(real.home)} {real.home_score}")
    _sim_output(
        prepared, games, summary, pbp=pbp, as_json=as_json, meta={"window": prepared.window.describe(), "event_id": event_id, "real": {"home_score": real.home_score, "away_score": real.away_score}}
    )


@cli.command("query")
@click.argument("question")
@_query_engine_options
def query(question: str, router_model: str, db_path: str, out_dir: str, verbose: bool) -> None:
    """Ask one natural-language question about the local data."""
    import shlex
    import sys

    from association.query.agent import Agent

    agent = Agent(db_path, Path(out_dir), verbose=verbose, router_model=router_model)
    # Agent.ask no longer reads sys.argv - a caller says what the request was,
    # and for this caller that really is the command line.
    click.echo(agent.ask(question, label=shlex.join(sys.argv)).text)


@cli.command("web")
@click.option("--port", type=int, default=0, help="Port to serve on. The default binds a free one and prints the URL.")
@click.option("--host", default="127.0.0.1", show_default=True, help="Address to bind. Localhost on purpose: this has no authentication.")
@click.option("--router-model", default=DEFAULT_ROUTER_MODEL, show_default=True, help="Ollama model for the normalizer, which copies the names out of the question and picks a stat.")
@click.option("--db-path", default=DEFAULT_DB_PATH, show_default=True, help="DuckDB warehouse file.")
@click.option("--out-dir", default=DEFAULT_OUT_DIR, show_default=True, help="Directory for rendered charts.")
def web(port: int, host: str, router_model: str, db_path: str, out_dir: str) -> None:
    """Serve a local web interface for asking questions, until interrupted.

    Needs the `web` extra: pip install 'association-py[web]'
    """
    from association.web.serve import serve

    serve(host, port, db_path, Path(out_dir), router_model=router_model)


def main() -> None:
    """Console-script entry point for the ``association`` command."""
    cli(prog_name="association")


if __name__ == "__main__":
    main()
