"""Rendering one player's shots to a static HTML court plot.

Extracted from Toolbox so the fast-path template and the agent's
render_shot_chart tool are one implementation, the same split leaderboard.py
got. Takes `con` and `out_dir` explicitly rather than reaching into a Toolbox,
so a template can call it with nothing but a warehouse and a directory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from .court import render_court_html
from .entities import Entity, find_players


def resolve_chart_player(con: duckdb.DuckDBPyConnection, player_name: str) -> tuple[Entity, list[str]] | None:
    """The player a chart is drawn for, plus any other names that matched.

    find_players, not resolve_player: a chart drawn for the wrong Curry is
    obvious on sight, so a best match is friendlier than refusing, and the plot
    is titled with the resolved name. Templates that report NUMBERS use
    resolve_player, where the same mistake would be invisible.

    Anything needing the athlete_id alongside a chart - scoping to one game, say
    - must resolve through this and pass the result to render_for_player, NOT
    resolve separately. Two independent resolutions of the same name can pick
    different players, which would scope the chart to a game the other one
    played.
    """
    candidates = find_players(con, player_name)
    if not candidates:
        return None
    return candidates[0], [c.name for c in candidates[1:]]


def render_shot_chart(
    con: duckdb.DuckDBPyConnection,
    out_dir: Path,
    player_name: str,
    season: int | None = None,
    season_type: int | None = None,
    event_id: str | None = None,
    period: int | None = None,
    shot_value: int | None = None,
    made_only: bool | None = None,
) -> str:
    """Resolve a player name and render their shots - the agent tool's entry
    point. A caller that has already resolved the player calls render_for_player.
    """
    resolved = resolve_chart_player(con, player_name)
    if resolved is None:
        return f"No player found matching {player_name!r}."
    player, ambiguous = resolved
    return render_for_player(
        con,
        out_dir,
        player,
        ambiguous,
        season=season,
        season_type=season_type,
        event_id=event_id,
        period=period,
        shot_value=shot_value,
        made_only=made_only,
    )


def render_for_player(
    con: duckdb.DuckDBPyConnection,
    out_dir: Path,
    player: Entity,
    ambiguous: list[str],
    season: int | None = None,
    season_type: int | None = None,
    event_id: str | None = None,
    period: int | None = None,
    shot_value: int | None = None,
    made_only: bool | None = None,
) -> str:
    """Render an already-resolved player's shots to a static HTML court plot.

    Free throws are excluded: they carry no court coordinates. Passing
    ``event_id`` scopes the chart to a single game and makes ``season`` and
    ``season_type`` redundant.

    Returns:
        A human-readable message naming the player, the made/attempted split,
        and the file written.
    """
    athlete_id, resolved_name = player.id, player.name

    # event_id already uniquely identifies one game - season/season_type would be
    # redundant at best and, if the model guesses either one wrong, silently zero
    # out real results. Ignore them whenever a specific game is requested.
    if event_id is not None:
        season = None
        season_type = None

    where = ["athlete_id = ?"]
    filter_params: list[Any] = [athlete_id]
    if season is not None:
        where.append("season = ?")
        filter_params.append(season)
    if season_type is not None:
        where.append("season_type = ?")
        filter_params.append(season_type)
    if event_id is not None:
        where.append("event_id = ?")
        filter_params.append(event_id)
    if period is not None:
        where.append("period = ?")
        filter_params.append(period)
    if shot_value is not None:
        where.append("points_attempted = ?")
        filter_params.append(shot_value)
    if made_only is not None:
        where.append("made = ?")
        filter_params.append(made_only)

    sql = f"SELECT coordinate_x, coordinate_y, made, shot_type, period, clock, event_id FROM shot_chart WHERE {' AND '.join(where)} AND coordinate_x IS NOT NULL"
    shots = con.execute(sql, filter_params).fetchall()
    if not shots:
        return f"No shots found for {resolved_name} with the given filters."

    made = sum(1 for s in shots if s[2])
    total = len(shots)
    title = resolved_name
    subtitle_parts = []
    if season is not None:
        subtitle_parts.append(f"season {season}")
    if season_type is not None:
        subtitle_parts.append({1: "preseason", 2: "regular season", 3: "postseason"}.get(season_type, str(season_type)))
    if event_id is not None:
        subtitle_parts.append(f"game {event_id}")
    if period is not None:
        subtitle_parts.append({1: "Q1", 2: "Q2", 3: "Q3", 4: "Q4"}.get(period, f"OT{period - 4}"))
    if shot_value is not None:
        subtitle_parts.append({1: "free throws", 2: "2PT attempts", 3: "3PT attempts"}.get(shot_value, f"{shot_value}pt attempts"))
    if made_only is not None:
        subtitle_parts.append("makes only" if made_only else "misses only")
    subtitle = ", ".join(subtitle_parts) or "all games"
    subtitle += f" - {made}/{total} ({made / total:.1%}) shown"

    html = render_court_html(title, subtitle, shots)
    safe_name = "".join(c if c.isalnum() else "_" for c in resolved_name.lower())
    scope = "_".join(
        filter(
            None,
            [
                str(season) if season else None,
                str(season_type) if season_type else None,
                event_id,
                f"p{period}" if period else None,
                f"{shot_value}pt" if shot_value else None,
                ("makes" if made_only else "misses") if made_only is not None else None,
            ],
        )
    )
    fname = f"shotchart_{safe_name}" + (f"_{scope}" if scope else "") + ".html"
    # Toolbox creates out_dir in its constructor, but this function is also
    # called straight from a template with whatever directory it was given -
    # it must not depend on someone else having made it first.
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / fname
    out_path.write_text(html)

    msg = f"Rendered shot chart for {resolved_name} ({made}/{total} made, {made / total:.1%}) to {out_path}"
    if ambiguous:
        msg += f". Note: other players also matched: {ambiguous}"
    return msg
