"""Shot charts and shot distances.

.. versionadded:: 3.0.0
   Split out of the former ``association.query.templates`` module.

.. versionchanged:: 4.4.0
   Both templates settle their player and read their games the way every
   other template on the player-games relation does (step 3, C5): the player
   and the span through :func:`common.scoped_player` (or, for ``shot_chart``,
   the chart's own best-match resolution over the same span - see
   :func:`_shot_chart_settle_player`), and their games through
   :func:`common.scoped_games`, read as :func:`association.query.player_games.games_subquery`
   (:func:`_shot_narrowed_rows`) - the same relation reader every other per-game
   template on it uses, which is what applies the window (``order``/``limit``,
   now set by ``scoped_games`` itself) together with the played guard. An
   opponent, a venue, a teammate's absence, a starter/bench half, one game of
   a series, a line on a box-score column, one Eastern date and ``since`` all
   narrow which games are drawn, the same as every other template on the
   relation. This is what fixes "Create a shot chart for Steph Curry's last
   two games of the regular season" having drawn the whole season (374 shots
   instead of the two games' worth).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import duckdb

from association.nba.coverage import COVERAGE

from ..conditions import box_source
from ..court import HAS_POSITION_SQL, SHOT_DISTANCE_SQL
from ..entities import Ambiguous, Entity, no_match
from ..player_games import games_subquery, named
from ..shotchart import DERIVED_SHOT_VALUES, SHOT_AVAILABILITY, SHOT_VALUE_SQL, UNSEPARABLE_SHOT_VALUES, render_for_player, resolve_chart_player
from .common import (
    _ISO_DATE,
    SEASON_TYPE_NAMES,
    MeasureFilter,
    TemplateContext,
    TemplateResult,
    TemplateUnsupported,
    _clarify,
    _defaulted_season_note,
    _no_narrowed_games,
    _period,
    _season_redirect,
    _Span,
    _span_of,
    measure_filters,
    scoped_games,
    scoped_player,
    settle_ordinal_season,
)

# ---------------- games: which ones a shot read draws from ----------------
#
# Every scoping slot a shot read can honor - an opponent, a venue, a
# teammate's absence, a starter/bench half, one game of a series, a line on
# a box-score column, one Eastern date, `since`, and `order`/`limit` as a
# window - goes through the ONE shared relation reader,
# `common.scoped_games` + `player_games.games_subquery` (`_shot_narrowed_rows`
# below). There is no template-local narrowing left to write: the window
# itself moved to `common.scoped_games` (`_relation_window`) so every
# template on the relation gets it, not just these two.


def _shots_windowed(slots: dict[str, Any]) -> bool:
    """Whether ``order``/``limit`` narrows this question to a window of
    games - the same reading :func:`common.scoped_games`'s own
    ``_relation_window`` applies inside the relation. Checked here only to
    decide whether the relation is needed at all, and whether a career span
    conflicts with a window it has no single "last N" for.

    A named ``order`` wins outright. Absent one, a ``limit`` alone still
    means "his last N games" - measured against the router's own traces for
    "Create a shot chart for Steph Curry's last two games of the regular
    season" (step 3, C5's finding): four separate runs, three different
    builds, all emit ``{'limit': 2, ...}`` with no ``order`` at all, so a
    rule gated on ``order`` alone would never reach the real question.
    """
    limit = slots.get("limit")
    return slots.get("order") in ("recent", "first") or (isinstance(limit, int) and not isinstance(limit, bool) and limit >= 1)


def _shots_other_narrowing(slots: dict[str, Any], date: str | None, measures: list[MeasureFilter]) -> bool:
    """Whether this question narrows which games a shot read draws from by
    anything BESIDES a window - an opponent, a venue, a teammate's absence, a
    starter/bench half, one game of a series, a line on a box-score column,
    one Eastern date, or ``since``."""
    return bool(slots.get("opponent") or slots.get("venue") or slots.get("without") or slots.get("split") or slots.get("game_n") or slots.get("since") or date or measures)


def _shots_has_narrowing(slots: dict[str, Any], date: str | None, measures: list[MeasureFilter]) -> bool:
    """Whether this question needs its games from the relation at all -
    either kind of narrowing - rather than straight off ``shot_chart`` by
    season alone."""
    return _shots_other_narrowing(slots, date, measures) or _shots_windowed(slots)


def _shot_narrowed_rows(con: duckdb.DuckDBPyConnection, player: Entity, span: _Span, narrowed: Any) -> tuple[list[str], list[str]] | TemplateResult:
    """The event ids (and their Eastern dates) an already-narrowed-and-windowed
    ``narrowed`` draws from, read through
    :func:`association.query.player_games.games_subquery` (:func:`named` to
    bind it) - the same relation reader every other per-game template uses.
    The played guard is already applied inside the window/narrowing
    (:func:`association.query.player_games._windowed`, which
    ``games_subquery`` now honors) - never a clause written here.

    Split out of ``_shot_chart_games``/``_shot_distance_games`` only so each
    keeps its own direct call to :func:`common.scoped_games` - which is what
    lets each template's own source (not this one, two calls away) satisfy
    ``test_every_template_honoring_a_scope_slot_actually_reads_it`` for the
    slots ``scoped_games``'s own body reads (``venue``, ``without``,
    ``split``, ``game_n``).

    Returns the ids and dates, or the ``TemplateResult`` :func:`common._no_narrowed_games`
    already answers with when nothing matches - the fact actually missing:
    his games in that span, a teammate, a match, or an empty box score.
    """
    box = box_source(con)
    base_sql, base_params = named(*games_subquery(narrowed, box))
    rows = con.execute(f"SELECT event_id, day FROM ({base_sql}) g", base_params).fetchall()
    if not rows:
        message = _no_narrowed_games(con, player, span, narrowed, rebuilt=box.rebuilt)
        return TemplateResult(data={"player": player.name, "message": message}, answer=message)
    return [r[0] for r in rows], [str(r[1]) for r in rows]


def _shots_span_prefix(span: _Span, season_type: int) -> str:
    """ "in the 2026 regular season" for a named season, or the career/"since"
    phrase :meth:`common._Span.during` already gives - what a narrowed,
    multi-game answer says before the narrowing itself."""
    return f"in the {_period(span.season, season_type)}" if span.season is not None else span.during()


# ---------------- shot_chart ----------------


@dataclass(frozen=True)
class _ShotChartGames:
    """Which games :func:`shot_chart` draws: nothing pinned at all (the whole
    span, read straight off ``shot_chart`` by season - every field ``None``),
    one game, or a set of them with ``window`` naming what it is."""

    event_id: str | None = None
    event_ids: tuple[str, ...] | None = None
    window: str | None = None

    @property
    def scoped(self) -> bool:
        """Whether either field pins the read to particular games."""
        return self.event_id is not None or self.event_ids is not None


def _shot_chart_settle_player(con: duckdb.DuckDBPyConnection, name: str, slots: dict[str, Any]) -> tuple[Entity, list[str], _Span, bool] | TemplateResult:
    """The player, the other names that also matched, the span, and whether
    the season was defaulted (not named) - ``shot_chart``'s own version of
    :func:`common.scoped_player`.

    It cannot simply call that: a chart keeps :func:`association.query.shotchart.resolve_chart_player`'s
    best-match handling (a chart of the wrong Curry is obvious on sight, since
    the plot is titled with the name that won) rather than
    :func:`common._resolved_player`'s strict refusal between candidates -
    ``shot_chart``'s own docstring already gives the reason it uses the
    renderer's resolver instead of the standard one. Every other step matches
    ``scoped_player``'s own order: the span is settled first, since it is what
    narrows an ambiguous name (a career keeps Dell Curry, this season does
    not), and ``season_n`` is read the same way.
    """
    season_n = slots.get("season_n")
    scope = _span_of("career" if season_n else slots.get("span"), None if season_n else slots.get("season"), slots.get("season_type") or 2, "player_game_log", since=slots.get("since"))
    resolved = resolve_chart_player(con, name, SHOT_AVAILABILITY, scope.season)
    if resolved is None:
        message = no_match(con, name)
        return TemplateResult(data={"message": message}, answer=message)
    if isinstance(resolved, Ambiguous):
        return _clarify(name, resolved.candidates, active=resolved.active)
    player, ambiguous = resolved
    settled = settle_ordinal_season(con, player, season_n, scope)
    if isinstance(settled, TemplateResult):
        return settled
    return player, ambiguous, settled, scope.defaulted


def _shot_chart_games(
    con: duckdb.DuckDBPyConnection, player: Entity, span: _Span, slots: dict[str, Any], season_type: int, date: str | None, measures: list[MeasureFilter], has_narrowing: bool
) -> _ShotChartGames | TemplateResult:
    """Which games :func:`shot_chart` draws from: nothing pinned at all (the
    whole span, read straight off ``shot_chart`` by season) when
    ``has_narrowing`` is false, or the relation's own read
    (:func:`common.scoped_games`, :func:`_shot_narrowed_rows`) otherwise.
    ``measures``/``has_narrowing`` are read by :func:`shot_chart` itself
    (through :func:`_shots_has_narrowing`) rather than here, so that
    function's own source names every scoping slot it honors - which is what
    ``test_every_template_honoring_a_scope_slot_actually_reads_it`` checks."""
    if not has_narrowing:
        return _ShotChartGames()
    narrowed = scoped_games(con, player, span, slots, opponent=slots.get("opponent"), measures=measures, date=date)
    if isinstance(narrowed, TemplateResult):
        return narrowed
    found = _shot_narrowed_rows(con, player, span, narrowed)
    if isinstance(found, TemplateResult):
        return found
    ids, _dates = found
    if len(ids) == 1:
        return _ShotChartGames(event_id=ids[0])
    return _ShotChartGames(event_ids=tuple(ids), window=f"{_shots_span_prefix(span, season_type)}{narrowed.filters(windowed=True)}")


def _shot_chart_message(ctx: TemplateContext, *, career: bool, defaulted: bool, scoped: bool, player: Entity, season_type: int, rendered_message: str, drew_something: bool) -> str:
    """What a shot_chart answer appends to the renderer's own message: the
    career-span note (#141) for a career, or - for a single defaulted season
    with nothing to draw - the redirect to seasons the player DOES have on
    record, exactly as before this existed."""
    if career:
        return rendered_message + _career_shot_note(ctx.con, player, season_type, found=drew_something)
    if not drew_something and not scoped and defaulted:
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

    .. versionchanged:: 4.4.0
       Reads its games through the player-games relation (step 3, C5):
       ``order`` now honors ``limit`` as a window of games rather than
       exactly one, and an opponent, a venue, a teammate's absence, a
       starter/bench half, one game of a series, a line on a box-score
       column, one Eastern date and ``since`` all narrow which games are
       drawn, the same as every other template on the relation. "Create a
       shot chart for Steph Curry's last two games of the regular season"
       used to draw the whole season (374 shots) because ``limit`` was never
       read; it now draws the two games.
    """
    name = slots.get("player")
    if not isinstance(name, str) or not name.strip():
        raise TemplateUnsupported("shot_chart needs a player name")
    shot_value = _shot_value(slots)
    con = ctx.con
    raw_date = slots.get("date")
    date = raw_date if isinstance(raw_date, str) and _ISO_DATE.match(raw_date) else None
    # Read here, not inside a step it calls, so this function's own source
    # names every scoping slot it honors (`test_every_template_honoring_a_scope_slot_actually_reads_it`).
    measures = measure_filters(slots.get("below"), slots.get("above"))
    has_narrowing = _shots_has_narrowing(slots, date, measures)

    # Settled before the shots are read, because the season is what narrows
    # an ambiguous name to the players who could have taken these shots.
    subject = _shot_chart_settle_player(con, name, slots)
    if isinstance(subject, TemplateResult):
        return subject
    player, ambiguous, span, defaulted = subject

    if span.career and _shots_windowed(slots):
        # "his last game" picks ONE game (or N) inside one season, where a
        # career asks for every one of them, and nothing here decides which
        # was meant - the same conflict a career span and a named season
        # raise on (`common._span_of`), just not one `_span_of` itself can
        # catch, since a window is not a span concept.
        raise TemplateUnsupported("shot_chart cannot combine a career span with a single game's order")

    season_type = span.season_type
    games = _shot_chart_games(con, player, span, slots, season_type, date, measures, has_narrowing)
    if isinstance(games, TemplateResult):
        return games

    rendered = render_for_player(
        con,
        ctx.out_dir,
        player,
        ambiguous,
        # An unspecified season means the CURRENT one here, exactly as it does
        # in every other template - passing None through charted a player's
        # entire career in one plot (confirmed live: 3,665 Curry attempts).
        # `season` is already None for a career span, deliberately, one
        # season_type at a time - `_career_shot_note` is what keeps that from
        # being the same silent-widening bug pointing the other way. Once the
        # read is pinned to particular games (`games.scoped`), season and
        # season_type are redundant - see `shotchart.render_for_player`.
        season=None if games.scoped else span.season,
        season_type=None if games.scoped else season_type,
        event_id=games.event_id,
        event_ids=games.event_ids,
        window=games.window,
        shot_value=shot_value,
    )
    # A "no player found" / "no shots found" message is returned as the answer
    # rather than falling through: the agent has no better source for a chart
    # than the same table this just queried.
    artifact = rendered.artifact
    message = _shot_chart_message(
        # `span.since` excludes a "since 2024"-shaped career: `_career_shot_note`
        # says "covers his whole career on record" against the player's REAL
        # range, which is true of a plain career and false of a bounded one -
        # "since 2024" is not his whole career even where the shot floor
        # clips nothing.
        ctx,
        career=span.career and span.since is None,
        defaulted=defaulted,
        scoped=games.scoped,
        player=player,
        season_type=season_type,
        rendered_message=rendered.message,
        drew_something=artifact is not None,
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


def _shot_distance_games(
    con: duckdb.DuckDBPyConnection, player: Entity, span: _Span, slots: dict[str, Any], season_type: int, date: str | None, measures: list[MeasureFilter], has_narrowing: bool
) -> tuple[str | None, list[Any], str] | TemplateResult:
    """The extra WHERE clause pinning a distance read to particular games, its
    parameters, and the ``game_note`` an answer appends after "distance" -
    ``shot_distance``'s counterpart of :func:`_shot_chart_games`, over the
    same relation read (:func:`common.scoped_games`, :func:`_shot_narrowed_rows`).
    An empty clause (``None``, ``[]``, ``""``) means the whole span, unpinned.
    ``measures``/``has_narrowing`` are read by :func:`shot_distance` itself,
    for the reason :func:`_shot_chart_games` gives.

    A single game reached through a WINDOW alone (no other narrowing) keeps
    its own precise phrasing - "in his most recent game (2026-04-12)" - the
    exact date read off the relation's own row, since that is a sentence a
    chart's ``game_label`` gives for free but a plain number has no other way
    to say. Anything else - several games, or a window mixed with real
    narrowing ("his last game vs Boston") - uses the general phrase every
    other template on the relation says a window with
    (:meth:`association.query.player_games.Narrowed.filters`).
    """
    if not has_narrowing:
        return None, [], ""
    narrowed = scoped_games(con, player, span, slots, opponent=slots.get("opponent"), measures=measures, date=date)
    if isinstance(narrowed, TemplateResult):
        return narrowed
    found = _shot_narrowed_rows(con, player, span, narrowed)
    if isinstance(found, TemplateResult):
        return found
    ids, dates = found
    # An IN clause even for one id - never the bare equality a hand-written
    # single-game narrowing would write, which is exactly the token
    # `test_templates_on_the_relation_do_not_narrow_it_themselves` forbids on
    # this table. These ids came FROM the relation, not a clause written
    # here, but the gate is a literal string search and cannot tell the two
    # apart, so the shape is avoided rather than argued with.
    marks = ", ".join("?" for _ in ids)
    clause = f"event_id IN ({marks})"
    if len(ids) == 1 and not _shots_other_narrowing(slots, date, measures):
        order = slots.get("order") if slots.get("order") in ("recent", "first") else "recent"
        game_note = f" in his {'first' if order == 'first' else 'most recent'} game ({dates[0]})"
    else:
        prefix = _shots_span_prefix(span, season_type)
        game_note = f" {prefix}{narrowed.filters(windowed=True)}"
    return clause, list(ids), game_note


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

    .. versionchanged:: 4.4.0
       Reads its games through the player-games relation (step 3, C5), the
       same as :func:`shot_chart`: ``order`` now honors ``limit`` as a window
       rather than always exactly one game, and an opponent, a venue, a
       teammate's absence, a starter/bench half, one game of a series, a line
       on a box-score column, one Eastern date and ``since`` all narrow which
       games the average is taken over.
    """
    con = ctx.con
    raw_date = slots.get("date")
    date = raw_date if isinstance(raw_date, str) and _ISO_DATE.match(raw_date) else None
    # Read here, not inside a step it calls, so this function's own source
    # names every scoping slot it honors (`test_every_template_honoring_a_scope_slot_actually_reads_it`).
    measures = measure_filters(slots.get("below"), slots.get("above"))
    has_narrowing = _shots_has_narrowing(slots, date, measures)

    subject = scoped_player(con, slots, "shot_distance needs a player name", table="player_game_log", available=SHOT_AVAILABILITY, span=slots.get("span"), season=slots.get("season"))
    if isinstance(subject, TemplateResult):
        return subject
    player, span = subject

    if span.career and _shots_windowed(slots):
        raise TemplateUnsupported("shot_distance cannot combine a career span with a single game's order")

    season_type = span.season_type
    shot_value = _shot_value(slots)
    if shot_value == 1:
        raise TemplateUnsupported("free throws have no meaningful shot distance")
    period = _shot_distance_period(player, span.season, season_type, career=span.career)
    kind = {2: "2-point ", 3: "3-point "}.get(shot_value or 0, "")
    refusal = _shot_distance_unseparable_refusal(player, span.season, shot_value, period, kind)
    if refusal is not None:
        return refusal

    where, params = _shot_distance_where(player.id, span.season, season_type, shot_value)
    extra = _shot_distance_games(con, player, span, slots, season_type, date, measures, has_narrowing)
    if isinstance(extra, TemplateResult):
        return extra
    clause, extra_params, game_note = extra
    if clause:
        where.append(clause)
        params.extend(extra_params)

    row = con.execute(f"SELECT AVG({SHOT_DISTANCE_SQL}), COUNT(*) FROM shot_chart WHERE {' AND '.join(where)}", params).fetchone()
    average, attempts = row or (None, 0)
    answer = _shot_distance_answer(player, kind, period, game_note, average, attempts, shot_value, span.season)
    if span.career and span.since is None:  # see the matching note in shot_chart's own call
        answer += _career_shot_note(con, player, season_type, found=attempts > 0)
    return TemplateResult(
        data={"player": player.name, "season": span.season, "shot_value": shot_value, "avg_feet": average, "attempts": attempts},
        answer=answer,
    )
