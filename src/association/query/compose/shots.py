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
The chart is a :class:`~association.query.result.Chart` - the marks, the
counts drawn, the caption and the file name - which :func:`draw_shot_chart`
writes to the output directory (``court.render_court_html``) before the
sayer names the file; the distance is a
:class:`~association.query.result.Scalar`. The sayer
(:mod:`association.query.compose.say`) words both.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

import duckdb

from association.nba.coverage import COVERAGE
from association.query.conditions import box_source
from association.query.court import HAS_POSITION_SQL, SHOT_DISTANCE_SQL, render_court_html
from association.query.entities import SHOT_AVAILABILITY, Ambiguous, Entity, no_match
from association.query.game_label import game_label
from association.query.notes import Note
from association.query.player_games import Narrowed, games_subquery
from association.query.reading import Scope
from association.query.result import Chart, Decided, Narrowing, Part, Result, Scalar, Span
from association.query.season_line import Statement, season_redirect, seasons_played
from association.query.shotchart import DERIVED_SHOT_VALUES, SHOT_VALUE_SQL, UNSEPARABLE_SHOT_VALUES, resolve_chart_player
from association.query.templates.common import (
    RELATION_SCOPING,
    SEASON_TYPE_NAMES,
    MeasureFilter,
    ResolvedSpan,
    TemplateResult,
    TemplateUnsupported,
    clarify,
    measure_filters,
    no_narrowed_games,
    scoped_games,
    scoped_player,
    season_phrase,
    settle_ordinal_season,
    span_of,
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
    result = _shot_distance_result(con, player, span, shot_value, period, narrowing, game, average, attempts or 0, games or 0)
    if not attempts and ids is None and span.defaulted and not span.career:
        # A defaulted season with no shots names the seasons he IS on record
        # for (#18), as the chart does: "no shots ... in the 2026 regular
        # season" of a player retired since 2003 is true of the wrong year.
        redirect = season_redirect(con, player.id, span.season_type, "shot_chart")
        if redirect is not None:
            facts = {"first": redirect[0], "last": redirect[1], "what": span.kind}
            result = replace(result, decisions=(Decided(kind="season_redirected", field="season", chose=None, why="the season read by default holds nothing for him", facts=facts),))
    return result


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


# ---------------- shot_chart ----------------


@dataclass(frozen=True)
class _ShotChartGames:
    """Which games a chart draws: nothing pinned (the whole span, read off
    ``shot_chart`` by season - every field ``None``), one game, or a set of
    them with ``window`` naming what it is."""

    event_id: str | None = None
    event_ids: tuple[str, ...] | None = None
    window: str | None = None

    @property
    def scoped(self) -> bool:
        """Whether either field pins the read to particular games."""
        return self.event_id is not None or self.event_ids is not None


def _shot_chart_settle_player(con: duckdb.DuckDBPyConnection, name: str, scope: Scope) -> tuple[Entity, list[str], ResolvedSpan, bool] | TemplateResult:
    """The player, the other names that also matched, the span, and whether
    the season was defaulted - ``scoped_player``'s order (the span first,
    since it narrows an ambiguous name; ``season_n`` the same way), with the
    chart's own resolution (:func:`~association.query.shotchart.resolve_chart_player`):
    the candidates narrowed to those with shots in the span, and the best
    match kept where nobody is left, since a chart is titled with the name
    that won."""
    season_n = scope.season_n
    seasons = span_of("career" if season_n else scope.span, None if season_n else scope.season, scope.season_type or 2, "player_game_log", since=scope.since, until=scope.until)
    resolved = resolve_chart_player(con, name, SHOT_AVAILABILITY, seasons.season)
    if resolved is None:
        message = no_match(con, name)
        return TemplateResult(data={"message": message}, answer=message)
    if isinstance(resolved, Ambiguous):
        return clarify(name, resolved.candidates, active=resolved.active)
    player, ambiguous = resolved
    settled = settle_ordinal_season(con, player, season_n, seasons)
    if isinstance(settled, TemplateResult):
        return settled
    return player, ambiguous, settled, seasons.defaulted


def _shot_chart_games(con: duckdb.DuckDBPyConnection, player: Entity, span: ResolvedSpan, scope: Scope, measures: list[MeasureFilter]) -> _ShotChartGames | TemplateResult:
    """The games a chart draws from: none pinned where nothing narrows the
    question, else the relation's own read (``scoped_games``,
    :func:`_shots_narrowed_rows`) - one game, or a set named by the span
    and the relation's phrase for the narrowing."""
    if not _shots_has_narrowing(scope, scope.date, measures):
        return _ShotChartGames()
    narrowed = scoped_games(con, player, span, scope, opponent=scope.opponent, measures=measures, date=scope.date)
    if isinstance(narrowed, TemplateResult):
        return narrowed
    found = _shots_narrowed_rows(con, player, span, narrowed)
    if isinstance(found, TemplateResult):
        return found
    ids, _dates = found
    if len(ids) == 1:
        return _ShotChartGames(event_id=ids[0])
    return _ShotChartGames(event_ids=tuple(ids), window=f"{_shots_span_prefix(span)}{narrowed.filters(windowed=True)}")


def _shot_chart_refusal(shot_value: int | None, season: int | None, name: str) -> str | None:
    """The sentence refusing to draw, where the shot value says so - a free
    throw (no court position worth drawing) or a season in
    :data:`~association.query.shotchart.UNSEPARABLE_SHOT_VALUES` - or ``None``."""
    if shot_value == 1:
        return "Free throws are all taken from the same line and carry no court position worth drawing, so there is no free-throw chart to render."
    kind = {2: "2PT attempts", 3: "3PT attempts"}.get(shot_value or 0, f"{shot_value}pt attempts")
    if shot_value is not None and season in UNSEPARABLE_SHOT_VALUES:
        return f"{UNSEPARABLE_SHOT_VALUES[season]}. A chart of {name}'s {kind} in {season} cannot be drawn."
    return None


def _shot_chart_statement(athlete_id: str, season: int | None, season_type: int | None, games: _ShotChartGames, shot_value: int | None) -> Statement:
    """The shots a chart draws: one player's located shots in a season (and
    its type), or in the games pinned. Free throws are left off - from 2002
    to 2018 they carry a fixed position under the rim. Filtered to a shot
    value, a shot NO value can be read for is kept, so it can be counted and
    said rather than dropped where nobody would know."""
    where = ["athlete_id = ?", HAS_POSITION_SQL]
    params: list[Any] = [athlete_id]
    if season is not None:
        where.append("season = ?")
        params.append(season)
    if season_type is not None:
        where.append("season_type = ?")
        params.append(season_type)
    # The games pinned, as an IN list even for one: these ids came FROM the
    # relation, and a bare equality on the game is the shape the relation
    # templates' source check forbids a reader to write.
    pinned = (games.event_id,) if games.event_id is not None else games.event_ids
    if pinned is not None:
        where.append(f"event_id IN ({', '.join('?' for _ in pinned)})")
        params.extend(pinned)
    if shot_value is not None:
        where.append(f"({SHOT_VALUE_SQL} = ? OR {SHOT_VALUE_SQL} IS NULL)")
        params.append(shot_value)
    else:
        where.append(f"{SHOT_VALUE_SQL} IS DISTINCT FROM 1")
    return Statement(f"SELECT coordinate_x, coordinate_y, made, shot_type, period, clock, event_id, season, {SHOT_VALUE_SQL} FROM shot_chart WHERE {' AND '.join(where)}", params)


def _shot_chart_notes(kept: list[tuple[Any, ...]], unknown: list[tuple[Any, ...]], shot_value: int | None) -> list[Note]:
    """What a shot-value filter left out or derived: shots no value can be
    read for (only across seasons - one unseparable season is refused), and
    each season whose split is derived rather than labeled."""
    notes = []
    if unknown:
        seasons = sorted({r[7] for r in unknown})
        why = ["unseparable" if s in UNSEPARABLE_SHOT_VALUES else "no_value_recorded" for s in seasons]
        notes.append(Note("shots_unlabeled", {"shots": len(unknown), "seasons": seasons, "why": why}))
    if shot_value is not None:
        notes.extend(Note("shot_values_derived", {"season": s}) for s in sorted({r[7] for r in kept} & DERIVED_SHOT_VALUES.keys()))
    return notes


def _shot_chart_caption(season: int | None, season_type: int | None, games: _ShotChartGames, game: str | None, shot_value: int | None, made: int, total: int) -> str:
    """The plot's caption: what scoped it - the season and its type, the one
    game by its label (its bare id where nothing describes it, #155) or the
    window by its phrase, the shot value - and the made/attempted split."""
    parts = []
    if season is not None:
        parts.append(f"season {season}")
    if season_type is not None:
        parts.append({1: "preseason", 2: "regular season", 3: "postseason"}.get(season_type, str(season_type)))
    if games.event_id is not None:
        parts.append(game or f"game {games.event_id}")
    elif games.event_ids is not None and games.window:
        parts.append(games.window)
    if shot_value is not None:
        parts.append({1: "free throws", 2: "2PT attempts", 3: "3PT attempts"}.get(shot_value, f"{shot_value}pt attempts"))
    return f"{', '.join(parts) or 'all games'} - {made}/{total} ({made / total:.1%}) shown"


def _shot_chart_file(name: str, season: int | None, season_type: int | None, games: _ShotChartGames, shot_value: int | None) -> str:
    """The page's file name: the player and every filter that scoped it - a
    window by its size and its two ends, not every id."""
    safe_name = "".join(c if c.isalnum() else "_" for c in name.lower())
    ids = games.event_ids
    scoped = "_".join(
        filter(
            None,
            [
                str(season) if season else None,
                str(season_type) if season_type else None,
                games.event_id,
                f"{len(ids)}g_{ids[0]}_{ids[-1]}" if ids else None,
                f"{shot_value}pt" if shot_value else None,
            ],
        )
    )
    return f"shotchart_{safe_name}" + (f"_{scoped}" if scoped else "") + ".html"


def _shot_chart_drawn(con: duckdb.DuckDBPyConnection, player: Entity, span: ResolvedSpan, games: _ShotChartGames, shot_value: int | None) -> tuple[Chart, list[Note], str | None, str | None]:
    """The chart's body, its notes, the narrowing's phrase (the one game's
    label or the window's) and the refusal sentence, if the shot value
    refuses the drawing: the shots read, kept and counted. An unspecified
    season means the current one (passing None through once charted a
    career, 3,665 Curry attempts); once particular games are pinned, the
    season and its type are redundant and left off."""
    season = None if games.scoped else span.season
    season_type = None if games.scoped else span.season_type
    refused = _shot_chart_refusal(shot_value, season, player.name)
    if refused is not None:
        return Chart(kind="shot_chart", title=player.name), [], None, refused
    rows = values_of(con, _shot_chart_statement(player.id, season, season_type, games, shot_value))
    kept = [r for r in rows if shot_value is None or r[8] == shot_value]
    unknown = [r for r in rows if shot_value is not None and r[8] is None]
    notes = _shot_chart_notes(kept, unknown, shot_value)
    marks = tuple(r[:7] for r in kept)
    if not marks:
        return Chart(kind="shot_chart", title=player.name), notes, None, None
    made, total = sum(1 for s in marks if s[2]), len(marks)
    # Only a single-game chart has one game to name.
    game = game_label(con, player.id, games.event_id) if games.event_id is not None else None
    caption = _shot_chart_caption(season, season_type, games, game, shot_value, made, total)
    chart = Chart(kind="shot_chart", made=made, attempted=total, marks=marks, title=player.name, caption=caption, file=_shot_chart_file(player.name, season, season_type, games, shot_value))
    return chart, notes, game or (games.window if games.event_ids else None), None


def read_shot_chart(con: duckdb.DuckDBPyConnection, q: ShotQuery, *, stated: frozenset[str]) -> Result | TemplateResult | None:
    """``shot_chart``'s point - one player's located shots over a season, a
    career or the games a narrowing or a window sends the read to, made and
    missed - read into a :class:`~association.query.result.Result` whose one
    part is a :class:`~association.query.result.Chart` (the marks, the
    counts, the caption and the file name; :func:`draw_shot_chart` writes
    it). ``None`` where the point is not that or carries a narrowing the
    retired template's words did not state (``stated``); a
    :class:`~association.query.templates.common.TemplateResult` back is the
    relation's refusal (no such player, which one, no games), and
    ``TemplateUnsupported`` what the template refused outright: no name, a
    career and a window at once. Its remarks: the shots a value filter left
    out or derived, the other names that matched (``also_matched``), and
    for a career the floor's note, or for a defaulted season that drew
    nothing the seasons the player IS on record for (``season_redirected``).

    .. versionadded:: 5.0.0
       ``templates.shots.shot_chart`` and ``shotchart.render_for_player``
       were this, the read moved whole.
    """
    scope = q.scope
    if q.shape != "chart" or unhonored_scoping("shot_chart", scope, stated):
        return None
    name = scope.player
    if name is None or not name.strip():
        raise TemplateUnsupported("shot_chart needs a player name")
    measures = measure_filters(scope.below, scope.above)
    subject = _shot_chart_settle_player(con, name, scope)
    if isinstance(subject, TemplateResult):
        return subject
    player, ambiguous, span, defaulted = subject
    if span.career and _shots_windowed(scope):
        # "his last game" picks games inside one season; a career asks for every one.
        raise TemplateUnsupported("shot_chart cannot combine a career span with a single game's order")
    games = _shot_chart_games(con, player, span, scope, measures)
    if isinstance(games, TemplateResult):
        return games
    chart, notes, drawn_from, refused = _shot_chart_drawn(con, player, span, games, shot_value_of(scope))
    decisions: list[Decided] = []
    if chart.marks and ambiguous:
        decisions.append(Decided(kind="also_matched", field="player", chose=player.name, instead_of=tuple(ambiguous)))
    if span.career and span.since is None:
        # "since 2024" is not his whole career, even where the floor clips nothing.
        floor = _shots_career_floor(con, player, span.season_type, found=bool(chart.marks))
        if floor is not None:
            notes.append(floor)
    elif not chart.marks and refused is None and not games.scoped and defaulted:
        # A defaulted season with nothing to draw names the seasons he IS on
        # record for (#18), rather than blaming filters nobody gave. Not after
        # a refusal: nothing was drawn because a free-throw chart is never
        # drawn, and "he last appears in 2026" beneath it was a non sequitur.
        redirect = season_redirect(con, player.id, span.season_type, "shot_chart")
        if redirect is not None:
            facts = {"first": redirect[0], "last": redirect[1], "what": SEASON_TYPE_NAMES.get(span.season_type, "regular season")}
            decisions.append(Decided(kind="season_redirected", field="season", chose=None, why="the season read by default holds nothing for him", facts=facts))
    return Result(
        subject=player.name,
        relation="player",
        span=Span(season=span.season, season_type=span.season_type, career=span.career, source="shots"),
        narrowing=Narrowing(phrase=drawn_from or ""),
        parts=(Part(body=chart),),
        notes=tuple(notes),
        decisions=tuple(decisions),
        empty=refused,
    )


def draw_shot_chart(result: Result, out_dir: Path) -> Result:
    """``result``'s chart drawn: the court page written to ``out_dir`` under
    the chart's file name (``court.render_court_html``), and the Result
    handed back naming where. A chart with nothing to draw is handed back
    as it is. The RUN stage's last act, between the read and the sayer: the
    reader reads and writes nothing, and the sayer, which names the file,
    takes the Result alone.

    .. versionadded:: 5.0.0
    """
    chart = result.chart
    if chart is None or not chart.marks:
        return result
    html = render_court_html(chart.title, chart.caption, list(chart.marks))
    # The answering loop creates out_dir when it starts; a reader called
    # straight from a test with whatever directory it was given must not
    # depend on someone else having made it first.
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / chart.file
    out_path.write_text(html)
    return replace(result, parts=(Part(body=replace(chart, path=str(out_path))), *result.parts[1:]))
