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
from association.query.reading import Reading, Scope

from ..conditions import box_source
from ..entities import Ambiguous, Entity, no_match
from ..notes import note
from ..player_games import games_subquery, named
from ..shotchart import SHOT_AVAILABILITY, render_for_player, resolve_chart_player
from .common import (
    RELATION_SCOPING,
    SEASON_TYPE_NAMES,
    MeasureFilter,
    TemplateContext,
    TemplateResult,
    TemplateUnsupported,
    _clarify,
    _defaulted_season_note,
    _no_narrowed_games,
    _period,
    _Span,
    _span_of,
    measure_filters,
    scoped_games,
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
# itself moved to `common.scoped_games` (`relation_window`) so every
# template on the relation gets it, not just these two.


def _shots_windowed(scope: Scope) -> bool:
    """Whether ``order``/``limit`` narrows this question to a window of
    games - the same reading :func:`common.scoped_games`'s own
    ``relation_window`` applies inside the relation. Checked here only to
    decide whether the relation is needed at all, and whether a career span
    conflicts with a window it has no single "last N" for.

    A named ``order`` wins outright. Absent one, a ``limit`` alone still
    means "his last N games" - measured against the router's own traces for
    "Create a shot chart for Steph Curry's last two games of the regular
    season" (step 3, C5's finding): four separate runs, three different
    builds, all emit ``{'limit': 2, ...}`` with no ``order`` at all, so a
    rule gated on ``order`` alone would never reach the real question. A
    limit, when set, is 1 or more (the Scope's own range rule), so either
    field set is a window.
    """
    return scope.order is not None or scope.limit is not None


#: Every relation-scoping cell that means "these particular games, not the
#: whole span" for a shot read - :data:`~association.query.templates.common.RELATION_SCOPING`
#: (no cell is excluded for either template here - see ``HONORED_SCOPING``)
#: less the three already decided before :func:`_shots_other_narrowing` ever
#: runs: ``order`` is windowing, :func:`_shots_windowed`'s own question, not
#: "other" narrowing; ``span`` and ``season_n`` are settled into a concrete
#: season, or a bare career, by :func:`common.scoped_player`/
#: :func:`_shot_chart_settle_player` before this is called, so
#: ``render_for_player``/the distance query already read either directly, by
#: season, with no event-id narrowing needed. ``date`` and ``below``/``above``
#: are read here as the already-extracted ``date``/``measures`` parameters
#: rather than read off the Scope again, since each template builds them
#: itself before calling this.
#:
#: Derived rather than hand-listed so the next slot RELATION_SCOPING gains
#: reaches this function automatically: ``situation`` and ``conditions``
#: joined the relation after this list was first written and were never
#: added here, so a shot chart or distance narrowed only by a calendar
#: ``situation`` ("on tuesdays") or a companion's role/absence
#: (``conditions``, "when embiid starts") silently read the whole span
#: instead - the same failure shape as reading `date` too. `until` joins for
#: the same reason, though it never arrives without `since` beside it
#: (``common._validated_until``), so this is a no-op addition for it alone.
#:
#: .. versionadded:: 5.0.0
_SHOTS_GAME_NARROWING_SLOTS = RELATION_SCOPING - {"order", "span", "season_n", "date", "below", "above"}


def _shots_other_narrowing(scope: Scope, date: str | None, measures: list[MeasureFilter]) -> bool:
    """Whether this question narrows which games a shot read draws from by
    anything BESIDES a window - an opponent, a venue, a teammate's absence, a
    starter/bench half, one game of a series, a line on a box-score column,
    one Eastern date, ``since``, a calendar ``situation``, or a companion's
    role (``conditions``) - see :data:`_SHOTS_GAME_NARROWING_SLOTS`.

    .. versionchanged:: 5.0.0
       Derived from :data:`~association.query.templates.common.RELATION_SCOPING`
       rather than a hand-written list that predated ``situation`` and
       ``conditions`` joining it - both silently answered the whole span:
       "stephen curry shot chart on christmas" (a `situation`) and "stephen
       curry shot chart when draymond green starts" (a `conditions`) each
       drew every shot of the season rather than the games actually asked
       for.
    """
    return bool(any(getattr(scope, slot) for slot in _SHOTS_GAME_NARROWING_SLOTS) or date or measures)


def _shots_has_narrowing(scope: Scope, date: str | None, measures: list[MeasureFilter]) -> bool:
    """Whether this question needs its games from the relation at all -
    either kind of narrowing - rather than straight off ``shot_chart`` by
    season alone."""
    return _shots_other_narrowing(scope, date, measures) or _shots_windowed(scope)


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


def _shot_chart_settle_player(con: duckdb.DuckDBPyConnection, name: str, scope: Scope) -> tuple[Entity, list[str], _Span, bool] | TemplateResult:
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
    season_n = scope.season_n
    seasons = _span_of(
        "career" if season_n else scope.span,
        None if season_n else scope.season,
        scope.season_type or 2,
        "player_game_log",
        since=scope.since,
        until=scope.until,
    )
    resolved = resolve_chart_player(con, name, SHOT_AVAILABILITY, seasons.season)
    if resolved is None:
        message = no_match(con, name)
        return TemplateResult(data={"message": message}, answer=message)
    if isinstance(resolved, Ambiguous):
        return _clarify(name, resolved.candidates, active=resolved.active)
    player, ambiguous = resolved
    settled = settle_ordinal_season(con, player, season_n, seasons)
    if isinstance(settled, TemplateResult):
        return settled
    return player, ambiguous, settled, seasons.defaulted


def _shot_chart_games(
    con: duckdb.DuckDBPyConnection, player: Entity, span: _Span, scope: Scope, season_type: int, date: str | None, measures: list[MeasureFilter], has_narrowing: bool
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
    narrowed = scoped_games(con, player, span, scope, opponent=scope.opponent, measures=measures, date=date)
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
        from association.query.season_line import season_redirect  # the relation's read; at call time, since it imports templates

        redirect = season_redirect(ctx.con, player.id, season_type, "shot_chart")
        return rendered_message + _defaulted_season_note(redirect, SEASON_TYPE_NAMES.get(season_type, "regular season"), career_hint=False)
    return rendered_message


def shot_chart(ctx: TemplateContext, reading: Reading) -> TemplateResult:
    """Renders one player's shots to a static HTML court plot.

    Uses shotchart.render_shot_chart, the one renderer, so it inherits
    best-match player handling rather than resolve_player's
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
    scope = reading.scope
    name = scope.player
    if name is None or not name.strip():
        raise TemplateUnsupported("shot_chart needs a player name")
    shot_value = _shot_value(scope)
    con = ctx.con
    date = scope.date
    # Read here, not inside a step it calls, so this function's own source
    # names every scoping slot it honors (`test_every_template_honoring_a_scope_slot_actually_reads_it`).
    measures = measure_filters(scope.below, scope.above)
    has_narrowing = _shots_has_narrowing(scope, date, measures)

    # Settled before the shots are read, because the season is what narrows
    # an ambiguous name to the players who could have taken these shots.
    subject = _shot_chart_settle_player(con, name, scope)
    if isinstance(subject, TemplateResult):
        return subject
    player, ambiguous, span, defaulted = subject

    if span.career and _shots_windowed(scope):
        # "his last game" picks ONE game (or N) inside one season, where a
        # career asks for every one of them, and nothing here decides which
        # was meant - the same conflict a career span and a named season
        # raise on (`common._span_of`), just not one `_span_of` itself can
        # catch, since a window is not a span concept.
        raise TemplateUnsupported("shot_chart cannot combine a career span with a single game's order")

    season_type = span.season_type
    games = _shot_chart_games(con, player, span, scope, season_type, date, measures, has_narrowing)
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
    # rather than raised: nothing has a better source for a chart than the
    # same table this just queried, and a raise would refuse for the wrong cause.
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


def _shot_value(scope: Scope) -> int | None:
    """Which shots a question meant. "Curry's threes" arrives either as
    shot_value 3 or as the equivalent box-score stat depending on wording; both
    mean the same thing, so read both rather than fight the router over which.
    A shot value is 1, 2 or 3 by the time it is read here: the Reading's door
    (:meth:`~association.query.reading.Scope.from_slots`) refuses any other."""
    if scope.shot_value is not None:
        return scope.shot_value
    return {"threePointFieldGoalsMade": 3, "freeThrowsMade": 1}.get(scope.stat) if scope.stat is not None else None


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
    from association.query.season_line import seasons_played  # the relation's read; at call time, since it imports templates

    span = seasons_played(con, player.id, season_type)
    if span is None:
        return ""
    earliest, latest = span
    facts = {"table": "shots", "first": floor_season, "earliest": earliest, "last": latest, "season_type": season_type}
    if latest < floor_season:
        said = f" {player.name}'s {kind} career ({earliest}-{latest}) ends before shot data begins, in {floor_season}, so none of it can be shown."
        return note("floor", said, what="career_before_floor", **facts)
    if earliest < floor_season:
        return note("floor", f" Shot data begins with the {floor_season} season, so his {earliest}-{floor_season - 1} {kind}s are not shown.", what="career_clipped", **facts)
    return note("floor", f" Covers his whole {kind} career on record ({earliest}-{latest}).", what="career_whole", **facts) if found else ""
