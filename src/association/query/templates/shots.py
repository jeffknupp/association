"""Shot charts and shot distances.

.. versionadded:: 3.0.0
   Split out of the former ``association.query.templates`` module.
"""

from __future__ import annotations

from typing import Any

import duckdb

from association.nba.season import current_season
from association.nba.season import eastern_date as _eastern_date

from ..court import HAS_POSITION_SQL, SHOT_DISTANCE_SQL
from ..entities import Ambiguous, no_match
from ..shotchart import DERIVED_SHOT_VALUES, SHOT_AVAILABILITY, SHOT_VALUE_SQL, UNSEPARABLE_SHOT_VALUES, render_for_player, resolve_chart_player
from .common import SEASON_TYPE_NAMES, TemplateContext, TemplateResult, TemplateUnsupported, _clarify, _defaulted_season_note, _period, _resolved_player, _season_redirect


def _scoping_game(con: duckdb.DuckDBPyConnection, athlete_id: str, season: int, season_type: int, order: str) -> tuple[Any, ...] | None:
    """The single game an `order` slot narrows a question to: (event_id, date),
    or None if the player has no games that season. Both orderings are explicit -
    LIMIT 1 without an ORDER BY returns an arbitrary row, not the first or last."""
    return con.execute(
        f"SELECT event_id, game_date FROM player_game_log WHERE athlete_id = ? AND season = ? AND season_type = ? ORDER BY game_date {'ASC' if order == 'first' else 'DESC'} LIMIT 1",
        [athlete_id, season, season_type],
    ).fetchone()


def shot_chart(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """Renders one player's shots to a static HTML court plot.

    Uses shotchart.render_shot_chart, the same function the agent tool calls,
    so it inherits best-match player handling rather than resolve_player's
    refusal: a chart of the wrong Curry is obvious on sight, and titled with the
    resolved name.

    .. versionchanged:: 4.0.2
       A defaulted (unnamed) season with no shots for the player now redirects
       to the seasons he does have on record, when there are any, rather than
       "No shots found ... with the given filters" - which blamed a filter
       that was never given. A season the question named outright is
       unaffected.
    """
    name = slots.get("player")
    if not isinstance(name, str) or not name.strip():
        raise TemplateUnsupported("shot_chart needs a player name")
    shot_value = _shot_value(slots)

    # "a shot chart of Curry's LAST regular season game" charted the whole
    # season - 803 attempts instead of that game's 22 - because nothing scoped
    # the request to one game. `order` means the same here as in game_log, and
    # resolving it to an event_id is the only way the chart can scope.
    #
    # Settled before the name is resolved, because the season is what narrows
    # an ambiguous name to the players who could have taken these shots.
    raw_season = slots.get("season")
    defaulted = not (isinstance(raw_season, int) and raw_season)
    season = raw_season or current_season()
    season_type = slots.get("season_type") or 2

    # Resolved ONCE, here, and the same player is then used both to find the
    # game to scope to and to draw the chart. Resolving separately for each
    # would let the two disagree and scope the chart to a game the other
    # candidate played.
    resolved = resolve_chart_player(ctx.con, name, SHOT_AVAILABILITY, season)
    if resolved is None:
        message = no_match(ctx.con, name)
        return TemplateResult(data={"message": message}, answer=message)
    if isinstance(resolved, Ambiguous):
        return _clarify(name, resolved.candidates, active=resolved.active)
    player, ambiguous = resolved

    event_id = None
    if slots.get("order") in ("recent", "first"):
        found = _scoping_game(ctx.con, player.id, season, season_type, slots["order"])
        if found is None:
            raise TemplateUnsupported(f"no games found to chart for {name!r}")
        event_id = found[0]

    rendered = render_for_player(
        ctx.con,
        ctx.out_dir,
        player,
        ambiguous,
        # An unspecified season means the CURRENT one here, exactly as it does
        # in every other template - passing None through charted a player's
        # entire career in one plot (confirmed live: 3,665 Curry attempts).
        season=None if event_id else season,
        season_type=None if event_id else season_type,
        event_id=event_id,
        shot_value=shot_value,
    )
    # A "no player found" / "no shots found" message is returned as the answer
    # rather than falling through: the agent has no better source for a chart
    # than the same table this just queried.
    artifact = rendered.artifact
    message = rendered.message
    if artifact is None and event_id is None and defaulted:
        # "No shots found ... with the given filters" blames the filters even
        # when there were none - the season was defaulted to "now", and a
        # retired player's "now" has nothing to draw. That reads as though the
        # warehouse holds no shots of his at all, which is false for anyone
        # with a season on record (issue #18); redirect to it instead. No
        # "or ask for his career" - shot_chart draws one season, never a
        # career - and a season the question named outright keeps this
        # refusal plain, because it is the correct answer.
        redirect = _season_redirect(ctx.con, player.id, season_type, "shot_chart")
        message += _defaulted_season_note(redirect, SEASON_TYPE_NAMES.get(season_type, "regular season"), career_hint=False)
    return TemplateResult(
        data={"message": message, "player": player.name, "path": str(artifact.path) if artifact else None},
        answer=message,
        artifacts=[artifact] if artifact else [],
    )


SHOT_VALUE_FROM_STAT = {"threePointFieldGoalsMade": 3, "freeThrowsMade": 1}


def _shot_value(slots: dict[str, Any]) -> int | None:
    """Which shots a question meant. "Curry's threes" arrives either as
    shot_value 3 or as the equivalent box-score stat depending on wording; both
    mean the same thing, so read both rather than fight the router over which."""
    raw = slots.get("shot_value")
    if raw in (1, 2, 3):
        return int(raw)
    stat = slots.get("stat")
    return SHOT_VALUE_FROM_STAT.get(stat) if isinstance(stat, str) else None


def shot_distance(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """Average shot distance for one player, optionally by shot value.

    The agent wrote a distance formula, then dropped both the 3-point filter
    and the season filter, reporting an all-shots all-seasons 16.94 as a
    current-season three-point figure. A fixed formula over known columns is
    template work - but "fixed" is only as good as the frame: this template
    then measured from a hoop 5.25 feet from where the data puts it, and
    answered Stephen Curry's 2026 threes with 23.6 feet, inside the line. From
    the rim (see court.HOOP_Y) they average 27.6.

    Shot values come from shotchart.SHOT_VALUE_SQL rather than
    ``points_attempted``, which is 0 for an unlabeled shot: "Curry's threes in
    2022" averaged 38 of his 751 attempts, every one of them a miss."""
    con = ctx.con
    season = slots.get("season") or current_season()
    player = _resolved_player(con, slots.get("player"), "shot_distance needs a player name", available=SHOT_AVAILABILITY, season=season)
    if isinstance(player, TemplateResult):
        return player

    season_type = slots.get("season_type") or 2
    shot_value = _shot_value(slots)
    if shot_value == 1:
        raise TemplateUnsupported("free throws have no meaningful shot distance")
    period = _period(season, season_type)
    kind = {2: "2-point ", 3: "3-point "}.get(shot_value or 0, "")
    if shot_value is not None and season in UNSEPARABLE_SHOT_VALUES:
        # Returned, not raised: the agent reads the same unlabeled rows.
        message = f"{UNSEPARABLE_SHOT_VALUES[season]}. {player.name}'s average {kind}shot distance in the {period} cannot be given; his average over all shots can."
        return TemplateResult(data={"player": player.name, "season": season, "shot_value": shot_value, "message": message}, answer=message)

    # Free throws are excluded by value, not by a missing position: from 2002
    # to 2018 they carry a fixed one under the rim, and averaged in as shots.
    where = ["athlete_id = ?", "season = ?", "season_type = ?", HAS_POSITION_SQL, f"{SHOT_VALUE_SQL} IS DISTINCT FROM 1"]
    params: list[Any] = [player.id, season, season_type]
    if shot_value is not None:
        where.append(f"{SHOT_VALUE_SQL} = ?")
        params.append(shot_value)
    game_note = ""
    if slots.get("order") in ("recent", "first"):
        found = _scoping_game(con, player.id, season, season_type, slots["order"])
        if found is None:
            raise TemplateUnsupported(f"no games found for {player.name}")
        where.append("event_id = ?")
        params.append(found[0])
        game_note = f" in his {'first' if slots['order'] == 'first' else 'most recent'} game ({_eastern_date(found[1])})"
    row = con.execute(f"SELECT AVG({SHOT_DISTANCE_SQL}), COUNT(*) FROM shot_chart WHERE {' AND '.join(where)}", params).fetchone()

    average, attempts = row or (None, 0)
    if not attempts or average is None:
        answer = f"No {kind}shots with recorded coordinates for {player.name}{game_note} in the {period}."
    else:
        answer = f"{player.name}'s average {kind}shot distance{game_note or f' in the {period}'} was {average:.1f} feet, over {attempts:,} attempts with recorded coordinates."
        if shot_value is not None and season in DERIVED_SHOT_VALUES:
            answer += f" Note: {DERIVED_SHOT_VALUES[season]}."
    return TemplateResult(
        data={"player": player.name, "season": season, "shot_value": shot_value, "avg_feet": average, "attempts": attempts},
        answer=answer,
    )
