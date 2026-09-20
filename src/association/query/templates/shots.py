"""Shot charts and shot distances.

.. versionadded:: 3.0.0
   Split out of the former ``association.query.templates`` module.
"""

from __future__ import annotations

from typing import Any

import duckdb

from association.nba.coverage import COVERAGE
from association.nba.season import current_season
from association.nba.season import eastern_date as _eastern_date

from ..court import HAS_POSITION_SQL, SHOT_DISTANCE_SQL
from ..entities import Ambiguous, Entity, no_match
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


def _career_shot_scope(intent: str, span: Any, raw_season: Any, order: Any) -> tuple[bool, int | None]:
    """Whether this question is a ``span`` "career" one, and what ``season``
    should be from here on - None for a career, the named or defaulted one
    otherwise. Shared by :func:`shot_chart` and :func:`shot_distance` (#141).

    Raises on the two combinations nothing here can honor:

    - a career span WITH a named season - either reading answers a different
      question from the other, the same conflict ``common._span_of`` raises
      on elsewhere;
    - a career span WITH an ``order`` - "his last game" picks ONE game inside
      one season, where a career asks for every one of them, and nothing
      here decides which was meant.
    """
    career = span == "career"
    if career and isinstance(raw_season, int) and raw_season:
        raise TemplateUnsupported(f"a career span and the {raw_season} season at once")
    if career and order in ("recent", "first"):
        raise TemplateUnsupported(f"{intent} cannot combine a career span with a single game's order")
    season = None if career else (raw_season or current_season())
    return career, season


def _career_shot_span(con: duckdb.DuckDBPyConnection, athlete_id: str, season_type: int) -> tuple[int, int] | None:
    """The first and last season ``athlete_id`` actually played ``season_type``,
    read from his own season line rather than ``shot_chart`` - so a career
    shot question can say whether the 2002 floor (COVERAGE["shot_chart"],
    "shots are derived from play-by-play, which ESPN does not have before
    2002") clips a real part of it, rather than reading a career that started
    before shots exist as though it had none at all. None with nothing on
    record for that season type."""
    row = con.execute(
        "SELECT MIN(season), MAX(season) FROM player_season_stats_deduped WHERE athlete_id = ? AND season_type = ? AND gamesPlayed > 0",
        [athlete_id, season_type],
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0]), int(row[1])


def _career_shot_note(con: duckdb.DuckDBPyConnection, player: Entity, season_type: int, *, found: bool) -> str:
    """What a ``span`` "career" shot_chart/shot_distance answer says about what
    it covers (#141) - AGENTS.md's rule against narrowing a question silently
    applies just as much to widening one from "the latest season with data" to
    "every season" without saying so. Three shapes, depending on where the
    player's own career sits against the 2002 shot floor:

    - entirely before it: nothing can be shown, and the message says why
      instead of reading like a plain "no shots found";
    - partly before it: the seasons left out are named, the same way a
      defaulted single season redirects to what IS on record rather than
      claiming there is none;
    - entirely on or after it: nothing is clipped, and the note just says so,
      because a whole-career answer that changed shape from one season to
      every season still has to say which it is.
    """
    kind = SEASON_TYPE_NAMES.get(season_type, "regular season")
    floor_season = COVERAGE["shot_chart"].floor(season_type).season
    span = _career_shot_span(con, player.id, season_type)
    if span is None:
        return ""
    earliest, latest = span
    if latest < floor_season:
        return f" {player.name}'s {kind} career ({earliest}-{latest}) ends before shot data begins, in {floor_season}, so none of it can be shown."
    if earliest < floor_season:
        return f" Shot data begins with the {floor_season} season, so his {earliest}-{floor_season - 1} {kind}s are not shown."
    return f" Covers his whole {kind} career on record ({earliest}-{latest})." if found else ""


def _shot_chart_event_id(con: duckdb.DuckDBPyConnection, athlete_id: str, season: int, season_type: int, order: str, name: str) -> str:
    """The event_id an `order` slot narrows a chart to, or a refusal - the
    player has no better source for a single game than the log this reads."""
    found = _scoping_game(con, athlete_id, season, season_type, order)
    if found is None:
        raise TemplateUnsupported(f"no games found to chart for {name!r}")
    event_id: str = found[0]
    return event_id


def _shot_chart_message(ctx: TemplateContext, *, career: bool, defaulted: bool, event_id: str | None, player: Entity, season_type: int, rendered_message: str, drew_something: bool) -> str:
    """What a shot_chart answer appends to the renderer's own message: the
    career-span note (#141) for a career, or - for a single defaulted season
    with nothing to draw - the redirect to seasons the player DOES have on
    record, exactly as before this existed."""
    if career:
        return rendered_message + _career_shot_note(ctx.con, player, season_type, found=drew_something)
    if not drew_something and event_id is None and defaulted:
        # "No shots found ... with the given filters" blames the filters even
        # when there were none - the season was defaulted to "now", and a
        # retired player's "now" has nothing to draw. That reads as though the
        # warehouse holds no shots of his at all, which is false for anyone
        # with a season on record (issue #18); redirect to it instead. No
        # "or ask for his career" - a single defaulted season keeps this
        # refusal plain otherwise, because it is the correct answer.
        redirect = _season_redirect(ctx.con, player.id, season_type, "shot_chart")
        return rendered_message + _defaulted_season_note(redirect, SEASON_TYPE_NAMES.get(season_type, "regular season"), career_hint=False)
    return rendered_message


def shot_chart(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """Renders one player's shots to a static HTML court plot.

    Uses shotchart.render_shot_chart, the same function the agent tool calls,
    so it inherits best-match player handling rather than resolve_player's
    refusal: a chart of the wrong Curry is obvious on sight, and titled with the
    resolved name.

    .. versionchanged:: 4.1.0
       A defaulted (unnamed) season with no shots for the player now redirects
       to the seasons he does have on record, when there are any, rather than
       "No shots found ... with the given filters" - which blamed a filter
       that was never given. A season the question named outright is
       unaffected.

    .. versionchanged:: 4.4.0
       Honors ``span`` "career": "all playoff games" now draws every
       postseason on record instead of silently drawing the latest one and
       presenting it as all of them (#141). See :func:`_career_shot_note` for
       what the answer says it covers, and how it says so where the 2002 shot
       floor clips a career that started earlier.
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
    # Each slot read here rather than inside the helper, so the template's own
    # source names every scoping slot it claims to honor - which is what
    # test_every_template_honoring_a_scope_slot_actually_reads_it checks.
    career, season = _career_shot_scope("shot_chart", slots.get("span"), slots.get("season"), slots.get("order"))
    defaulted = not career and not (isinstance(slots.get("season"), int) and slots.get("season"))
    season_type = slots.get("season_type") or 2

    # Resolved ONCE, here, and the same player is then used both to find the
    # game to scope to and to draw the chart. Resolving separately for each
    # would let the two disagree and scope the chart to a game the other
    # candidate played. `season=None` for a career narrows to anybody who
    # ever took a shot, the same as an unscoped chart already did.
    resolved = resolve_chart_player(ctx.con, name, SHOT_AVAILABILITY, season)
    if resolved is None:
        message = no_match(ctx.con, name)
        return TemplateResult(data={"message": message}, answer=message)
    if isinstance(resolved, Ambiguous):
        return _clarify(name, resolved.candidates, active=resolved.active)
    player, ambiguous = resolved

    event_id = None
    if slots.get("order") in ("recent", "first"):
        # season is never None here: the career+order combination that would
        # leave it so is refused by _career_shot_scope.
        assert season is not None
        event_id = _shot_chart_event_id(ctx.con, player.id, season, season_type, slots["order"], name)

    rendered = render_for_player(
        ctx.con,
        ctx.out_dir,
        player,
        ambiguous,
        # An unspecified season means the CURRENT one here, exactly as it does
        # in every other template - passing None through charted a player's
        # entire career in one plot (confirmed live: 3,665 Curry attempts).
        # `season` is already None for a career span, deliberately, one
        # season_type at a time - _career_shot_note is what keeps that from
        # being the same silent-widening bug pointing the other way.
        season=None if event_id else season,
        season_type=None if event_id else season_type,
        event_id=event_id,
        shot_value=shot_value,
    )
    # A "no player found" / "no shots found" message is returned as the answer
    # rather than falling through: the agent has no better source for a chart
    # than the same table this just queried.
    artifact = rendered.artifact
    message = _shot_chart_message(
        ctx, career=career, defaulted=defaulted, event_id=event_id, player=player, season_type=season_type, rendered_message=rendered.message, drew_something=artifact is not None
    )
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


def _shot_distance_period(player: Entity, season: int | None, season_type: int, *, career: bool) -> str:
    """The label an answer's "in the ..." names: a year and a season type for
    one season, or - for a career - the season type alone, since a career has
    no single year to name."""
    if career:
        return f"career {SEASON_TYPE_NAMES.get(season_type, 'regular season')}"
    # season is never None here: only a career span leaves it so, and _shot_value
    # of 1 is refused before this is ever called.
    assert season is not None
    return _period(season, season_type)


def _shot_distance_unseparable_refusal(player: Entity, season: int | None, shot_value: int | None, period: str, kind: str) -> TemplateResult | None:
    """A refusal in place of an answer, where UNSEPARABLE_SHOT_VALUES makes the
    named season's twos and threes unreadable - or None to proceed. Only
    reachable for one named season: `season` is None on a career span, and no
    int key in this dict is None, so a career sum silently leaves these shots
    out instead (ISSUES.md tracks that gap rather than repeating this refusal
    across a multi-season sum, which would refuse seasons that ARE readable)."""
    if shot_value is None or season not in UNSEPARABLE_SHOT_VALUES:
        return None
    # Returned, not raised: the agent reads the same unlabeled rows.
    message = f"{UNSEPARABLE_SHOT_VALUES[season]}. {player.name}'s average {kind}shot distance in the {period} cannot be given; his average over all shots can."
    return TemplateResult(data={"player": player.name, "season": season, "shot_value": shot_value, "message": message}, answer=message)


def _shot_distance_where(athlete_id: str, season: int | None, season_type: int, shot_value: int | None) -> tuple[list[str], list[Any]]:
    """The WHERE clause and its params for a distance query. No `season = ?`
    line at all on a career span - shot_chart itself holds no rows before 2002
    (COVERAGE["shot_chart"]), so leaving the column unfiltered already sums
    exactly the seasons on record, no separate floor clause needed."""
    # Free throws are excluded by value, not by a missing position: from 2002
    # to 2018 they carry a fixed one under the rim, and averaged in as shots.
    where = ["athlete_id = ?"]
    params: list[Any] = [athlete_id]
    if season is not None:
        where.append("season = ?")
        params.append(season)
    where += ["season_type = ?", HAS_POSITION_SQL, f"{SHOT_VALUE_SQL} IS DISTINCT FROM 1"]
    params.append(season_type)
    if shot_value is not None:
        where.append(f"{SHOT_VALUE_SQL} = ?")
        params.append(shot_value)
    return where, params


def _shot_distance_order_scope(con: duckdb.DuckDBPyConnection, player: Entity, season: int, season_type: int, order: str) -> tuple[str, str, str]:
    """The extra WHERE clause an `order` slot adds (one game), its parameter,
    and the note naming which game it was."""
    found = _scoping_game(con, player.id, season, season_type, order)
    if found is None:
        raise TemplateUnsupported(f"no games found for {player.name}")
    game_note = f" in his {'first' if order == 'first' else 'most recent'} game ({_eastern_date(found[1])})"
    return "event_id = ?", found[0], game_note


def _shot_distance_answer(player: Entity, kind: str, period: str, game_note: str, average: float | None, attempts: int, shot_value: int | None, season: int | None) -> str:
    """The average and its made/attempted-style count, or an honest "none
    found" - and, for one named DERIVED_SHOT_VALUES season, the caveat that
    its twos and threes are read rather than labeled."""
    if not attempts or average is None:
        return f"No {kind}shots with recorded coordinates for {player.name}{game_note} in the {period}."
    answer = f"{player.name}'s average {kind}shot distance{game_note or f' in the {period}'} was {average:.1f} feet, over {attempts:,} attempts with recorded coordinates."
    if shot_value is not None and season in DERIVED_SHOT_VALUES:
        answer += f" Note: {DERIVED_SHOT_VALUES[season]}."
    return answer


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
    2022" averaged 38 of his 751 attempts, every one of them a miss.

    .. versionchanged:: 4.4.0
       Honors ``span`` "career": averages across every season of the season
       type on record instead of only the latest one, the same shape as
       :func:`shot_chart` (#141). Note: a career average still silently drops
       an UNSEPARABLE_SHOT_VALUES season's shots from a ``shot_value`` filter
       rather than refusing or noting it the way one named season does, and
       does not repeat DERIVED_SHOT_VALUES' per-season caveat across a
       multi-season sum - see ISSUES.md.
    """
    con = ctx.con
    career, season = _career_shot_scope("shot_distance", slots.get("span"), slots.get("season"), slots.get("order"))
    player = _resolved_player(con, slots.get("player"), "shot_distance needs a player name", available=SHOT_AVAILABILITY, season=season)
    if isinstance(player, TemplateResult):
        return player

    season_type = slots.get("season_type") or 2
    shot_value = _shot_value(slots)
    if shot_value == 1:
        raise TemplateUnsupported("free throws have no meaningful shot distance")
    period = _shot_distance_period(player, season, season_type, career=career)
    kind = {2: "2-point ", 3: "3-point "}.get(shot_value or 0, "")
    refusal = _shot_distance_unseparable_refusal(player, season, shot_value, period, kind)
    if refusal is not None:
        return refusal

    where, params = _shot_distance_where(player.id, season, season_type, shot_value)
    game_note = ""
    if slots.get("order") in ("recent", "first"):
        # season is never None here: the career+order combination that would
        # leave it so is refused by _career_shot_scope.
        assert season is not None
        clause, event_id, game_note = _shot_distance_order_scope(con, player, season, season_type, slots["order"])
        where.append(clause)
        params.append(event_id)
    row = con.execute(f"SELECT AVG({SHOT_DISTANCE_SQL}), COUNT(*) FROM shot_chart WHERE {' AND '.join(where)}", params).fetchone()

    average, attempts = row or (None, 0)
    answer = _shot_distance_answer(player, kind, period, game_note, average, attempts, shot_value, season)
    if career:
        answer += _career_shot_note(con, player, season_type, found=attempts > 0)
    return TemplateResult(
        data={"player": player.name, "season": season, "shot_value": shot_value, "avg_feet": average, "attempts": attempts},
        answer=answer,
    )
