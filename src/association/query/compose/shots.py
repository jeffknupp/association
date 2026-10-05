"""One player's located shots - drawn on a court (``shot_chart``) or
averaged by distance from the rim (``shot_distance``): a declared relation
with its own reader (``ROADMAP.md``, "Charts are declared shapes"), not
ported onto the player-games relation until after Phase 3.

The reader is the retired templates' read, moved whole
(``templates/shots.py``, Phase 2, step 5): the player and the span settled
through the shared steps (:func:`~association.query.templates.common.scoped_player`),
the games a narrowing or a window sends the read to taken from the
player-games relation's own reader
(:func:`~association.query.player_games.games_subquery`, through
:func:`~association.query.templates.common.scoped_games`), and the shots
read off ``shot_chart`` - every statement built here and executed through
the compiler's one door (:func:`~association.query.compose.core.values_of`).
The distance is a :class:`~association.query.result.Scalar`; the sayer
(:mod:`association.query.compose.say`) words it.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import duckdb

from association.nba.coverage import COVERAGE
from association.query.conditions import box_source
from association.query.court import HAS_POSITION_SQL, SHOT_DISTANCE_SQL
from association.query.entities import SHOT_AVAILABILITY, Entity
from association.query.notes import Note
from association.query.player_games import Narrowed, games_subquery
from association.query.reading import Scope
from association.query.result import Narrowing, Part, Result, Scalar, Span
from association.query.season_line import Statement, seasons_played
from association.query.shotchart import DERIVED_SHOT_VALUES, SHOT_VALUE_SQL, UNSEPARABLE_SHOT_VALUES
from association.query.templates.common import (
    RELATION_SCOPING,
    MeasureFilter,
    ResolvedSpan,
    TemplateResult,
    TemplateUnsupported,
    measure_filters,
    no_narrowed_games,
    scoped_games,
    scoped_player,
    season_phrase,
    unhonored_scoping,
)

from .core import values_of


@dataclass(frozen=True)
class ShotQuery:
    """A point on the shot relation: the question's scoping, read whole by
    the relation's reader, and the shape - the shots drawn (``chart``) or
    their average distance (``scalar``). ``subject`` is whose shots they
    are, always one named player's. The shot relation's counterpart of
    :class:`~association.query.compose.core.Query`.

    .. versionadded:: 5.0.0
    """

    #: The question's scoping, read whole by the relation's reader.
    scope: Scope
    shape: Literal["chart", "scalar"] = "chart"
    subject: Literal["player"] = "player"


SHOT_VALUE_FROM_STAT: dict[str, int] = {"threePointFieldGoalsMade": 3, "freeThrowsMade": 1}
"""A box-score stat that names a shot value: "Curry's threes" arrives as
``shot_value`` 3 or as the made-threes stat depending on wording, and both
mean the same shots.

.. versionadded:: 5.0.0
   ``templates.shots.SHOT_VALUE_FROM_STAT`` was this.
"""


def shot_value_of(scope: Scope) -> int | None:
    """Which shots a question meant: its ``shot_value`` (1, 2 or 3, the
    Reading's door refuses any other), or the one its stat names
    (:data:`SHOT_VALUE_FROM_STAT`), or every shot (``None``).

    .. versionadded:: 5.0.0
       ``templates.shots._shot_value`` was this.
    """
    if scope.shot_value is not None:
        return scope.shot_value
    return SHOT_VALUE_FROM_STAT.get(scope.stat) if scope.stat is not None else None


# ---------------- games: which ones a shot read draws from ----------------
#
# Every scoping slot a shot read can honor - an opponent, a venue, a
# teammate's absence, a starter/bench half, one game of a series, a line on
# a box-score column, one Eastern date, `since`, a calendar situation, a
# companion's role, and `order`/`limit` as a window - goes through the ONE
# shared relation reader, `scoped_games` + `games_subquery`
# (`_shots_narrowed_rows` below). There is no narrowing written here.


def _shots_windowed(scope: Scope) -> bool:
    """Whether ``order``/``limit`` narrows this question to a window of
    games - the reading ``scoped_games``'s own window applies inside the
    relation. A ``limit`` alone still means "his last N games": the reader
    emits ``{'limit': 2}`` with no ``order`` for "Create a shot chart for
    Steph Curry's last two games of the regular season"."""
    return scope.order is not None or scope.limit is not None


def _shots_other_narrowing(scope: Scope, date: str | None, measures: list[MeasureFilter]) -> bool:
    """Whether this question narrows which games a shot read draws from by
    anything BESIDES a window: any cell of the player relation
    (``RELATION_SCOPING``) but the window, the span and the ordinal season
    (settled into a season or a career before this runs), and the date and
    lines, read here as ``date`` and ``measures``. Derived from the
    relation's set rather than listed, so a cell the relation gains reaches
    a shot read at once - ``situation`` and ``conditions`` once silently
    drew the whole span because a hand list predated them."""
    cells = RELATION_SCOPING - {"order", "span", "season_n", "date", "below", "above"}
    return bool(any(getattr(scope, cell) for cell in cells) or date or measures)


def _shots_has_narrowing(scope: Scope, date: str | None, measures: list[MeasureFilter]) -> bool:
    """Whether this question needs its games from the relation at all, rather
    than straight off ``shot_chart`` by season alone."""
    return _shots_other_narrowing(scope, date, measures) or _shots_windowed(scope)


def _shots_narrowed_rows(con: duckdb.DuckDBPyConnection, player: Entity, span: ResolvedSpan, narrowed: Narrowed) -> tuple[list[str], list[str]] | TemplateResult:
    """The event ids (and their Eastern dates) an already-narrowed-and-windowed
    ``narrowed`` draws from, read through the relation's own
    :func:`~association.query.player_games.games_subquery` - or the
    refusal :func:`~association.query.templates.common.no_narrowed_games`
    names the missing fact with when nothing matches: his games in that
    span, a teammate, a match, or an empty box score."""
    box = box_source(con)
    base_sql, base_params = games_subquery(narrowed, box)
    rows = values_of(con, Statement(f"SELECT event_id, day FROM ({base_sql}) g", list(base_params)))
    if not rows:
        message = no_narrowed_games(con, player, span, narrowed, rebuilt=box.rebuilt)
        return TemplateResult(data={"player": player.name, "message": message}, answer=message)
    return [r[0] for r in rows], [str(r[1]) for r in rows]


def _shots_span_prefix(span: ResolvedSpan) -> str:
    """ "in the 2026 regular season" for a named season, or the career or
    "since" phrase the span gives - what a narrowed, multi-game answer says
    before the narrowing itself."""
    return f"in the {season_phrase(span.season, span.season_type)}" if span.season is not None else span.during()


def _shots_career_floor(con: duckdb.DuckDBPyConnection, player: Entity, season_type: int, *, found: bool) -> Note | None:
    """What a career answer says about what it covers (#141), as a
    ``floor`` note on the shot table - or ``None`` where there is nothing to
    say: the player has no season of the type on record, or a career wholly
    on or after the floor drew nothing. Three shapes, by where his career
    sits against the 2002 shot floor: entirely before it
    (``career_before_floor``), partly (``career_clipped``) or not at all
    (``career_whole``) - a whole-career answer that changed shape from one
    season to every season still has to say which it is."""
    floor_season = COVERAGE["shot_chart"].floor(season_type).season
    played = seasons_played(con, player.id, season_type)
    if played is None:
        return None
    earliest, latest = played
    facts = {"table": "shots", "first": floor_season, "earliest": earliest, "last": latest, "season_type": season_type}
    if latest < floor_season:
        return Note("floor", {"what": "career_before_floor", **facts})
    if earliest < floor_season:
        return Note("floor", {"what": "career_clipped", **facts})
    return Note("floor", {"what": "career_whole", **facts}) if found else None


# ---------------- shot_distance ----------------


def _shot_distance_statement(athlete_id: str, span: ResolvedSpan, shot_value: int | None, event_ids: list[str] | None) -> Statement:
    """The distance read: the average distance from the rim, the shots and
    the games they came from. No ``season = ?`` on a career span - the shot
    table holds no rows before 2002, so leaving the column unfiltered sums
    exactly the seasons on record. Free throws are excluded by value, not by
    a missing position: from 2002 to 2018 they carry a fixed one under the
    rim. ``event_ids`` pins the read to the games the relation narrowed to,
    as an IN list even for one."""
    where = ["athlete_id = ?"]
    params: list[Any] = [athlete_id]
    if span.season is not None:
        where.append("season = ?")
        params.append(span.season)
    where += ["season_type = ?", HAS_POSITION_SQL, f"{SHOT_VALUE_SQL} IS DISTINCT FROM 1"]
    params.append(span.season_type)
    if shot_value is not None:
        where.append(f"{SHOT_VALUE_SQL} = ?")
        params.append(shot_value)
    if event_ids is not None:
        where.append(f"event_id IN ({', '.join('?' for _ in event_ids)})")
        params.extend(event_ids)
    return Statement(f"SELECT AVG({SHOT_DISTANCE_SQL}), COUNT(*), COUNT(DISTINCT event_id) FROM shot_chart WHERE {' AND '.join(where)}", params)


def _shot_distance_unseparable(player: Entity, span: ResolvedSpan, shot_value: int | None, period: str, kind: str) -> TemplateResult | None:
    """The refusal in place of an answer, where :data:`~association.query.shotchart.UNSEPARABLE_SHOT_VALUES`
    makes one named season's twos and threes unreadable - or ``None``. A
    career's ``season`` is None, so a career sum leaves such shots out
    instead (ISSUES.md tracks that gap)."""
    season = span.season
    if shot_value is None or season not in UNSEPARABLE_SHOT_VALUES:
        return None
    message = f"{UNSEPARABLE_SHOT_VALUES[season]}. {player.name}'s average {kind}shot distance in the {period} cannot be given; his average over all shots can."
    return TemplateResult(data={"player": player.name, "season": season, "shot_value": shot_value, "message": message}, answer=message)


def _shot_distance_games(
    con: duckdb.DuckDBPyConnection, player: Entity, span: ResolvedSpan, scope: Scope, measures: list[MeasureFilter]
) -> tuple[list[str], Narrowing, dict[str, Any]] | TemplateResult:
    """The games a narrowed distance read is pinned to, with the narrowing
    as the answer names it: one game a window alone reached keeps its own
    words ("in his most recent game (2026-04-12)"), as facts; any other set
    is said as the span and the relation's own phrase for the narrowing
    (:meth:`~association.query.player_games.Narrowed.filters`)."""
    narrowed = scoped_games(con, player, span, scope, opponent=scope.opponent, measures=measures, date=scope.date)
    if isinstance(narrowed, TemplateResult):
        return narrowed
    found = _shots_narrowed_rows(con, player, span, narrowed)
    if isinstance(found, TemplateResult):
        return found
    ids, dates = found
    opponent = narrowed.opponent.name if narrowed.opponent else None
    if len(ids) == 1 and not _shots_other_narrowing(scope, scope.date, measures):
        return ids, Narrowing(opponent=opponent, venue=narrowed.venue), {"game_date": dates[0], "first_game": scope.order == "first"}
    return ids, Narrowing(phrase=f" {_shots_span_prefix(span)}{narrowed.filters(windowed=True)}", opponent=opponent, venue=narrowed.venue), {}


def read_shot_distance(con: duckdb.DuckDBPyConnection, q: ShotQuery, *, stated: frozenset[str]) -> Result | TemplateResult | None:
    """``shot_distance``'s point - one player's average shot distance from
    the rim, optionally of one shot value, over a season, a career or the
    games a narrowing or a window sends the read to - read into a
    :class:`~association.query.result.Result` whose one part is a
    :class:`~association.query.result.Scalar` (the average under
    ``avg_feet``, the shots under ``attempts``, ``games`` the games they
    came from). ``None`` where the point is not that or carries a narrowing
    the retired template's words did not state (``stated``); a
    :class:`~association.query.templates.common.TemplateResult` back is the
    relation's refusal (a name, a season, no games, an unseparable season),
    and ``TemplateUnsupported`` what the template refused outright: a
    career and a window at once, a free throw's distance.

    .. versionadded:: 5.0.0
       ``templates.shots.shot_distance`` was this, the read moved whole.
    """
    scope = q.scope
    if q.shape != "scalar" or unhonored_scoping("shot_distance", scope, stated):
        return None
    measures = measure_filters(scope.below, scope.above)
    subject = scoped_player(con, scope, "shot_distance needs a player name", table="player_game_log", available=SHOT_AVAILABILITY, span=scope.span, season=scope.season)
    if isinstance(subject, TemplateResult):
        return subject
    player, span = subject
    if span.career and _shots_windowed(scope):
        raise TemplateUnsupported("shot_distance cannot combine a career span with a single game's order")
    shot_value = shot_value_of(scope)
    if shot_value == 1:
        raise TemplateUnsupported("free throws have no meaningful shot distance")
    # A career has no single year to name: its season type alone.
    period = f"career {span.kind}" if span.season is None else season_phrase(span.season, span.season_type)
    kind = {2: "2-point ", 3: "3-point "}.get(shot_value or 0, "")
    refusal = _shot_distance_unseparable(player, span, shot_value, period, kind)
    if refusal is not None:
        return refusal
    ids: list[str] | None = None
    narrowing, game = Narrowing(), dict[str, Any]()
    if _shots_has_narrowing(scope, scope.date, measures):
        pinned = _shot_distance_games(con, player, span, scope, measures)
        if isinstance(pinned, TemplateResult):
            return pinned
        ids, narrowing, game = pinned
    ((average, attempts, games),) = values_of(con, _shot_distance_statement(player.id, span, shot_value, ids))
    return _shot_distance_result(con, player, span, shot_value, period, narrowing, game, average, attempts or 0, games or 0)


def _shot_distance_result(
    con: duckdb.DuckDBPyConnection, player: Entity, span: ResolvedSpan, shot_value: int | None, period: str, narrowing: Narrowing, game: dict[str, Any], average: Any, attempts: int, games: int
) -> Result:
    """The distance read's Result: the line, and its notes in the order the
    answer says them - a derived season's caveat, then the career's floor."""
    notes: list[Note] = []
    if attempts and average is not None and shot_value is not None and span.season in DERIVED_SHOT_VALUES:
        notes.append(Note("shot_values_derived", {"season": span.season}))
    if span.career and span.since is None:
        # "since 2024" is not his whole career, even where the floor clips nothing.
        floor = _shots_career_floor(con, player, span.season_type, found=attempts > 0)
        if floor is not None:
            notes.append(floor)
    return Result(
        subject=player.name,
        relation="player",
        span=Span(season=span.season, season_type=span.season_type, career=span.career, phrase=period, source="shots"),
        narrowing=narrowing,
        parts=(Part(body=Scalar(games=games, values={"avg_feet": average}, sums={"attempts": attempts})),),
        notes=tuple(notes),
        facts={"shot_value": shot_value, **game},
    )
