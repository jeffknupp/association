"""Rendering one player's shots to a static HTML court plot.

Extracted from Toolbox so the fast-path template and the agent's
render_shot_chart tool are one implementation, the same split leaderboard.py
got. Takes `con` and `out_dir` explicitly rather than reaching into a Toolbox,
so a template can call it with nothing but a warehouse and a directory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from .answer import Artifact, RenderResult
from .court import render_court_html
from .entities import Ambiguous, Availability, Entity, clarification, find_players, narrow_to_available, no_match

SHOT_AVAILABILITY = Availability("shot_chart")
"""Where a shot chart's rows live, for narrowing an ambiguous name to the
players who actually took shots in the season being charted.

.. versionadded:: 2.1.0
"""

ChartResolution = tuple[Entity, list[str]] | Ambiguous | None
"""What resolving a chart's player can come to: the player and any other names
that matched, a question about which of several was meant, or None for a name
nothing matched.

.. versionadded:: 2.1.0
"""


def resolve_chart_player(con: duckdb.DuckDBPyConnection, player_name: str, available: Availability, season: int | None = None) -> ChartResolution:
    """The player a chart is drawn for, a clarifying question, or None.

    Narrowed by data before it is decided, which is what lets a chart keep
    answering a bare surname without guessing. Of the candidates a name
    matches, only those with a row in ``available`` for ``season`` could have
    produced the chart being asked for; dropping the rest is a fact about the
    warehouse rather than a preference between people. Exactly one left is the
    answer. Two or more are a real question, and it gets asked.

    Anything needing the athlete_id alongside a chart - scoping to one game, say
    - must resolve through this and pass the result to render_for_player, NOT
    resolve separately. Two independent resolutions of the same name can pick
    different players, which would scope the chart to a game the other one
    played.

    .. versionadded:: 1.2.0

    .. versionchanged:: 2.1.0
       Takes ``available`` and ``season``, and may return
       :class:`association.query.entities.Ambiguous`. It previously took the
       best match unconditionally, on the reasoning that a chart of the wrong
       Curry is obvious on sight because the plot is titled with the resolved
       name - which holds only when a plot is drawn. "Maxey" resolved to Marlon
       Maxey, who last played in 1994, and the answer was a false claim that
       the warehouse had no fingerprint data for the season. Measured over the
       566 players with a 2026 fingerprint, 319 have a surname that matches
       somebody else and 221 surnames league-wide put a player with no
       fingerprint ahead of one who has it.
    """
    candidates = find_players(con, player_name)
    if not candidates:
        return None
    if len(candidates) > 1:
        narrowed = narrow_to_available(con, candidates, available, season)
        # Narrowing that eliminates EVERYBODY is not a reason to ask which one
        # was meant: no answer to that question draws a chart either, so the
        # renderer's own message - which names the player it tried and what the
        # season does hold - explains more than the question would. So it
        # narrows only where it discriminates, and otherwise leaves the old
        # best match in place to fail loudly.
        if narrowed:
            candidates = narrowed
            if len(candidates) > 1:
                return Ambiguous(query=player_name, candidates=[c.name for c in candidates])
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
) -> RenderResult:
    """Resolve a player name and render their shots - the agent tool's entry
    point. A caller that has already resolved the player calls
    :func:`render_for_player`.

    .. versionchanged:: 1.2.0
       Resolution split out into :func:`resolve_chart_player` and the rendering
       body into :func:`render_for_player`. This signature is unchanged.

    .. versionchanged:: 2.0.0
       Returns a :class:`association.query.answer.RenderResult` rather than a
       message string, so a caller can reach the file that was drawn.

    .. versionchanged:: 2.1.0
       May answer with a clarifying question - ``artifact`` None and the
       message naming the candidates - where an ambiguous name previously drew
       the best match. See :func:`resolve_chart_player`.
    """
    # `season` is passed through as given, None included: an unscoped chart
    # covers a whole career, so narrowing the name to one year would filter by
    # something the question never said.
    resolved = resolve_chart_player(con, player_name, SHOT_AVAILABILITY, season)
    if resolved is None:
        return RenderResult(no_match(con, player_name), None)
    if isinstance(resolved, Ambiguous):
        return RenderResult(clarification(player_name, resolved.candidates), None)
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
) -> RenderResult:
    """Render an already-resolved player's shots to a static HTML court plot.

    Free throws are excluded: they carry no court coordinates. Passing
    ``event_id`` scopes the chart to a single game and makes ``season`` and
    ``season_type`` redundant.

    Returns:
        A :class:`association.query.answer.RenderResult`: the message naming
        the player and the made/attempted split, and the file written -
        ``artifact`` is None when no shots matched, which is an answer rather
        than a failure.

    .. versionadded:: 1.2.0

    .. versionchanged:: 2.0.0
       Returns a :class:`association.query.answer.RenderResult` instead of a
       bare message with the path formatted into it, so a caller that wants to
       show the chart does not have to parse the path back out of a sentence.
       :func:`association.query.fingerprint.render_for_players` returns the
       same shape.
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
        return RenderResult(f"No shots found for {resolved_name} with the given filters.", None)

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
    return RenderResult(msg, Artifact("shot_chart", out_path))
