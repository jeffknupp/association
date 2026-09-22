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
from .court import BEYOND_THE_ARC_SQL, HAS_POSITION_SQL, render_court_html
from .entities import MAX_CANDIDATES, Ambiguous, Availability, Entity, clarification, find_players, narrow_to_available, no_match
from .game_label import game_label

UNSEPARABLE_SHOT_VALUES: dict[int, str] = {
    2002: (
        "ESPN did not label 2002's shots as twos or threes, and unlike every later season its shot descriptions do not name every three - counted against the "
        "box score they miss about one in sixty - so threes cannot be reliably separated from twos"
    ),
}
"""Seasons where nothing reliably says whether a shot was a two or a three, by
season, with the reason. A question filtered to twos or threes in one of them
is refused rather than answered from whatever subset happens to be labeled.

.. versionadded:: 2.1.0
"""

DERIVED_SHOT_VALUES: dict[int, str] = {
    2003: ("ESPN did not label 2003's shots as twos or threes, so they are read from each shot's description, whose count of threes matches the box score's to within 0.01%"),
    2022: (
        "ESPN labeled only 4% of 2022's shots as twos or threes, so the rest are read from the description where it says and otherwise from where the shot "
        "was taken against the three-point line, which counts 0.2% more threes than the box score does"
    ),
}
"""Seasons whose shot values are mostly derived rather than labeled by ESPN,
with the caveat an answer filtered to twos or threes carries.

.. versionadded:: 2.1.0
"""

TEXT_NAMES_EVERY_THREE_UNTIL = 2012
"""The last season whose shot descriptions name every three.

Through 2012 "three point" is in the description of every shot ESPN labels a
three and of no shot it labels a two - 100.00% agreement in each of 2004-2012.
From 2013 step-backs and pull-ups stop saying so (99.66% in 2013, 95.45% by
2024), and only the shot's position is left to decide.

.. versionadded:: 2.1.0
"""

_UNSEPARABLE = ", ".join(str(s) for s in sorted(UNSEPARABLE_SHOT_VALUES))

SHOT_VALUE_SQL = f"""(CASE
    WHEN shot_type ILIKE '%free throw%' THEN 1
    WHEN points_attempted IN (2, 3) THEN points_attempted
    WHEN description ILIKE '%three point%' THEN 3
    WHEN description ILIKE '%two point%' THEN 2
    WHEN season IN ({_UNSEPARABLE}) THEN NULL
    WHEN season <= {TEXT_NAMES_EVERY_THREE_UNTIL} THEN 2
    WHEN NOT {HAS_POSITION_SQL} THEN NULL
    WHEN {BEYOND_THE_ARC_SQL} THEN 3
    ELSE 2
END)"""
"""A ``shot_chart`` row's value - 1, 2 or 3 - as SQL, or NULL where nothing
establishes it. The one definition every shot-value filter reads.

``points_attempted`` cannot be filtered on directly, because **0 there means
unlabeled, not zero points**: every shot of 2002 and 2003, 96% of 2022's, and
about a quarter of each season's from 2004 to 2012 - every one of those a miss,
so "his twos" answered from the labels alone came out at a 72% field goal
percentage. So the label is used where there is one, and otherwise, in order:

- ``shot_type`` for free throws, which are unlabeled in 2002, 2003 and 2022;
- the description, where it says "three point" or "two point" - a label ESPN
  wrote in prose, which disagrees with its numeric label on at most 3 shots a
  season;
- through :data:`TEXT_NAMES_EVERY_THREE_UNTIL`, a two, since those seasons'
  descriptions name every three;
- after it, the shot's position against the three-point line
  (:data:`association.query.court.BEYOND_THE_ARC_SQL`), which matches ESPN's
  own labels on 99.83-99.94% of shots in every season it labeled.

Counted against the box score's three-point attempts per player-game, the
result matches in 99.3-100% of games in every season but 2002 - which is
:data:`UNSEPARABLE_SHOT_VALUES`, and NULL here for any shot neither labeled nor
described.

.. versionadded:: 2.1.0
"""

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

       Every match is narrowed, not the first ``MAX_CANDIDATES``. Narrowing a
       page cut by name drew Anthony Davis for "Davis" while JD Davison, Nigel
       Hayes-Davis and Trayce Jackson-Davis also had 2026 shots, and 22 more
       names did the same that season.
    """
    # Every match, not find_players' first page. Narrowing a page cut
    # alphabetically chooses by name rather than eliminating: Anthony Davis was
    # the only "Davis" on the first page with 2026 shots, and the three more
    # who had them sorted past it.
    candidates = find_players(con, player_name, limit=None)
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
        if len(narrowed) > 1:
            # Every survivor played in the season charted, so none may be
            # counted away - unless no season was given, and nobody did.
            return Ambiguous(query=player_name, candidates=[c.name for c in narrowed], active=0 if season is None else len(narrowed))
        if narrowed:
            return narrowed[0], []
    # The runners-up a failure message mentions are the page find_players has
    # always returned, rather than every Williams in the warehouse.
    shown = candidates[:MAX_CANDIDATES]
    return shown[0], [c.name for c in shown[1:]]


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
       the best match. See :func:`resolve_chart_player`. ``shot_value`` now
       reaches unlabeled shots, and may be refused for a season that cannot
       separate them; see :func:`render_for_player`.
    """
    # `season` is passed through as given, None included: an unscoped chart
    # covers a whole career, so narrowing the name to one year would filter by
    # something the question never said.
    resolved = resolve_chart_player(con, player_name, SHOT_AVAILABILITY, season)
    if resolved is None:
        return RenderResult(no_match(con, player_name), None)
    if isinstance(resolved, Ambiguous):
        return RenderResult(clarification(player_name, resolved.candidates, active=resolved.active), None)
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


def _render_for_player_refusal(shot_value: int | None, season: int | None, resolved_name: str) -> str | None:
    """A message refusing to draw the chart, if ``shot_value``/``season`` say
    so - a free throw filter (no court position worth drawing) or a season in
    :data:`UNSEPARABLE_SHOT_VALUES` - or None to proceed.
    """
    if shot_value == 1:
        return "Free throws are all taken from the same line and carry no court position worth drawing, so there is no free-throw chart to render."
    kind = {2: "2PT attempts", 3: "3PT attempts"}.get(shot_value or 0, f"{shot_value}pt attempts")
    if shot_value is not None and season in UNSEPARABLE_SHOT_VALUES:
        return f"{UNSEPARABLE_SHOT_VALUES[season]}. A chart of {resolved_name}'s {kind} in {season} cannot be drawn."
    return None


def _render_for_player_where(
    athlete_id: str,
    season: int | None,
    season_type: int | None,
    event_id: str | None,
    event_ids: tuple[str, ...] | None,
    period: int | None,
    shot_value: int | None,
    made_only: bool | None,
) -> tuple[list[str], list[Any]]:
    """The WHERE clause and its params, in the order ``render_for_player``
    applies its filters.

    .. versionchanged:: 4.4.0
       Takes ``event_ids`` - a set of games (a player's narrowed or windowed
       games, from :mod:`association.query.templates.shots`) beside the
       single ``event_id`` this already took.
    """
    where = ["athlete_id = ?", HAS_POSITION_SQL]
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
    elif event_ids is not None:
        marks = ", ".join("?" for _ in event_ids)
        where.append(f"event_id IN ({marks})")
        filter_params.extend(event_ids)
    if period is not None:
        where.append("period = ?")
        filter_params.append(period)
    if shot_value is not None:
        # NULL is kept here so it can be counted and said, rather than dropped
        # by the filter where nobody would know.
        where.append(f"({SHOT_VALUE_SQL} = ? OR {SHOT_VALUE_SQL} IS NULL)")
        filter_params.append(shot_value)
    else:
        where.append(f"{SHOT_VALUE_SQL} IS DISTINCT FROM 1")
    if made_only is not None:
        where.append("made = ?")
        filter_params.append(made_only)
    return where, filter_params


def _render_for_player_notes(kept: list[tuple[Any, ...]], unknown: list[tuple[Any, ...]], shot_value: int | None) -> list[str]:
    """Caveats about what a ``shot_value`` filter left out or derived - shots
    that cannot be told apart as twos or threes, and seasons whose split is
    derived rather than labeled (:data:`DERIVED_SHOT_VALUES`)."""
    notes = []
    if unknown:
        # Only reachable across seasons (a career or one game): a single
        # unseparable season was refused above.
        why = "; ".join(UNSEPARABLE_SHOT_VALUES.get(s, f"nothing records their value in {s}") for s in sorted({r[7] for r in unknown}))
        notes.append(f"left out {len(unknown):,} {'shot' if len(unknown) == 1 else 'shots'} that cannot be told apart as twos or threes: {why}")
    if shot_value is not None:
        notes.extend(DERIVED_SHOT_VALUES[s] for s in sorted({r[7] for r in kept} & DERIVED_SHOT_VALUES.keys()))
    return notes


def _render_for_player_subtitle(
    season: int | None,
    season_type: int | None,
    event_id: str | None,
    game: str | None,
    event_ids: tuple[str, ...] | None,
    window: str | None,
    period: int | None,
    shot_value: int | None,
    made_only: bool | None,
    made: int,
    total: int,
) -> str:
    """The plot's subtitle: which filters scoped it, plus the made/attempted split.

    A single game is named by ``game`` - "2025-04-13 vs POR, W 118-104" - where
    :func:`association.query.game_label.game_label` could describe it, and by
    its bare event id otherwise. See `ISSUES.md` #155: the event id alone told
    a reader nothing about which game was drawn.

    .. versionchanged:: 4.4.0
       A multi-game window (``event_ids``, ``window``) is named by ``window`` -
       "last 2 games (2026-04-10 to 2026-04-13)" - the phrase
       :mod:`association.query.templates.shots` built from the narrowing.
    """
    subtitle_parts = []
    if season is not None:
        subtitle_parts.append(f"season {season}")
    if season_type is not None:
        subtitle_parts.append({1: "preseason", 2: "regular season", 3: "postseason"}.get(season_type, str(season_type)))
    if event_id is not None:
        subtitle_parts.append(game or f"game {event_id}")
    elif event_ids is not None and window:
        subtitle_parts.append(window)
    if period is not None:
        subtitle_parts.append({1: "Q1", 2: "Q2", 3: "Q3", 4: "Q4"}.get(period, f"OT{period - 4}"))
    if shot_value is not None:
        subtitle_parts.append({1: "free throws", 2: "2PT attempts", 3: "3PT attempts"}.get(shot_value, f"{shot_value}pt attempts"))
    if made_only is not None:
        subtitle_parts.append("makes only" if made_only else "misses only")
    subtitle = ", ".join(subtitle_parts) or "all games"
    subtitle += f" - {made}/{total} ({made / total:.1%}) shown"
    return subtitle


def _render_for_player_filename(
    resolved_name: str,
    season: int | None,
    season_type: int | None,
    event_id: str | None,
    event_ids: tuple[str, ...] | None,
    period: int | None,
    shot_value: int | None,
    made_only: bool | None,
) -> str:
    """The HTML file's name: the player, and every filter that scoped the chart.

    .. versionchanged:: 4.4.0
       A multi-game window (``event_ids``) names its size and its two ends -
       not every id, which could run to :data:`association.query.templates.common.MAX_LIMIT`.
    """
    safe_name = "".join(c if c.isalnum() else "_" for c in resolved_name.lower())
    scope = "_".join(
        filter(
            None,
            [
                str(season) if season else None,
                str(season_type) if season_type else None,
                event_id,
                f"{len(event_ids)}g_{event_ids[0]}_{event_ids[-1]}" if event_ids else None,
                f"p{period}" if period else None,
                f"{shot_value}pt" if shot_value else None,
                ("makes" if made_only else "misses") if made_only is not None else None,
            ],
        )
    )
    return f"shotchart_{safe_name}" + (f"_{scope}" if scope else "") + ".html"


def _render_for_player_message(
    resolved_name: str, game: str | None, event_ids: tuple[str, ...] | None, window: str | None, made: int, total: int, out_path: Path, ambiguous: list[str], notes: list[str]
) -> str:
    """The success message: which game (or window of games) was drawn (for a
    single-game chart, see `ISSUES.md` #155), the made/attempted split, then
    every note - the runners-up that also matched, and any shot_value caveat.

    .. versionchanged:: 4.4.0
       Names a multi-game window (``event_ids``, ``window``) the same way a
       single game is named.
    """
    context = game or (window if event_ids else None)
    who = f"{resolved_name} ({context})" if context else resolved_name
    msg = f"Rendered shot chart for {who} ({made}/{total} made, {made / total:.1%}) to {out_path}"
    if ambiguous:
        msg += f". Note: other players also matched: {ambiguous}"
    for note in notes:
        msg += f". Note: {note}"
    return msg


def render_for_player(
    con: duckdb.DuckDBPyConnection,
    out_dir: Path,
    player: Entity,
    ambiguous: list[str],
    season: int | None = None,
    season_type: int | None = None,
    event_id: str | None = None,
    event_ids: tuple[str, ...] | None = None,
    window: str | None = None,
    period: int | None = None,
    shot_value: int | None = None,
    made_only: bool | None = None,
) -> RenderResult:
    """Render an already-resolved player's shots to a static HTML court plot.

    Free throws are excluded, and so is any shot with no recorded position -
    see :data:`association.query.court.HAS_POSITION_SQL`. Passing ``event_id``
    scopes the chart to a single game and makes ``season`` and ``season_type``
    redundant; passing ``event_ids`` does the same for a SET of games - a
    player's narrowed or windowed games from
    :mod:`association.query.templates.shots` (step 3, C5) - with ``window``
    the phrase naming what they are ("last 2 games (2026-04-10 to
    2026-04-13)"), shown the way a single game's own label is. An empty
    ``event_ids`` answers "no shots found" without querying - the caller's
    narrowing matched no games at all, which is a fact about the games, not
    about the shots.

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

    .. versionchanged:: 2.1.0
       ``shot_value`` is read through :data:`SHOT_VALUE_SQL` rather than
       ``points_attempted``, whose 0 means unlabeled: Stephen Curry's 2022
       threes charted 38 of 751 attempts, every one a miss. A season in
       :data:`UNSEPARABLE_SHOT_VALUES` is refused, one in
       :data:`DERIVED_SHOT_VALUES` says so, ``shot_value=1`` is refused
       because a free throw has no position worth drawing, and free throws no
       longer reach an unfiltered chart - from 2002 to 2018 they carry a fixed
       position under the rim and were drawn there as shots.

    .. versionchanged:: 4.4.0
       A single-game chart (``event_id`` given) names the game by its date,
       opponent and result - "2025-04-13 vs POR, W 118-104" - in the subtitle
       and the message, where :func:`association.query.game_label.game_label`
       can describe it, rather than by its bare event id. `ISSUES.md` #155:
       neither the page nor the answer previously said which game was drawn.

    .. versionchanged:: 4.4.0
       Takes ``event_ids`` and ``window`` for a chart scoped to a SET of
       games rather than one - step 3, C5. An unnarrowed chart is unaffected.
    """
    athlete_id, resolved_name = player.id, player.name

    if event_ids is not None and not event_ids:
        # The caller's narrowing (an opponent, a window, a line on a
        # box-score column) matched no games at all - a fact about which
        # games he played, not about which shots he took, but the same
        # message either way: there is nothing here for the agent to find
        # either.
        return RenderResult(f"No shots found for {resolved_name} with the given filters.", None)

    # event_id/event_ids already uniquely identify the games - season/season_type
    # would be redundant at best and, if the model guesses either one wrong,
    # silently zero out real results. Ignore them whenever specific games are
    # requested.
    if event_id is not None or event_ids is not None:
        season = None
        season_type = None

    refusal = _render_for_player_refusal(shot_value, season, resolved_name)
    if refusal is not None:
        return RenderResult(refusal, None)

    where, filter_params = _render_for_player_where(athlete_id, season, season_type, event_id, event_ids, period, shot_value, made_only)

    sql = f"SELECT coordinate_x, coordinate_y, made, shot_type, period, clock, event_id, season, {SHOT_VALUE_SQL} FROM shot_chart WHERE {' AND '.join(where)}"
    rows = con.execute(sql, filter_params).fetchall()
    kept = [r for r in rows if shot_value is None or r[8] == shot_value]
    unknown = [r for r in rows if shot_value is not None and r[8] is None]
    notes = _render_for_player_notes(kept, unknown, shot_value)
    shots = [r[:7] for r in kept]
    if not shots:
        message = f"No shots found for {resolved_name} with the given filters."
        return RenderResult(" Note: ".join([message, *notes]) + ("." if notes else ""), None)

    made = sum(1 for s in shots if s[2])
    total = len(shots)
    title = resolved_name
    # Only a single-game chart has one game to name - a season or career chart
    # covers many, and naming one of them would misdescribe the rest.
    game = game_label(con, athlete_id, event_id) if event_id is not None else None
    subtitle = _render_for_player_subtitle(season, season_type, event_id, game, event_ids, window, period, shot_value, made_only, made, total)

    html = render_court_html(title, subtitle, shots)
    fname = _render_for_player_filename(resolved_name, season, season_type, event_id, event_ids, period, shot_value, made_only)
    # Toolbox creates out_dir in its constructor, but this function is also
    # called straight from a template with whatever directory it was given -
    # it must not depend on someone else having made it first.
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / fname
    out_path.write_text(html)

    msg = _render_for_player_message(resolved_name, game, event_ids, window, made, total, out_path, ambiguous, notes)
    return RenderResult(msg, Artifact("shot_chart", out_path))
