"""The count reader: how many games a named player cleared a line in, or
who in the league cleared it most often - ``threshold_count``'s own point,
read into a :class:`~association.query.result.Result`. A named player's
count is a :class:`~association.query.result.Scalar` (``how="count"``); the
league's is a :class:`~association.query.result.Grouped` ranking by
``player``. Phase 2's slice (ii) (``ROADMAP.md``, "Phase 2, the expected
steps", step 2): the compiler already read these rows, and the presenter
``compose.present._present_threshold_count`` said them in the retired
template's words (``templates.players._phrase_threshold_count`` and
``_threshold_count_notes``) until 2026-10-04; the words are the sayer's now
(:mod:`association.query.compose.say`), and what they added beyond the
compiled rows - the span's own seasons, the floor, the empty box scores,
the withheld stat, the leader's rebuilt games - are values and notes here.

The statement is the planned point, compiled and executed
(:func:`~association.query.compose.core.compile_query`,
:func:`~association.query.compose.core.rows_of`): each row's own rebuilt
count is its ``rebuilt_shown`` column, which is all the shape needs of
:func:`~association.query.compose.core.run`'s extras, so ``run`` - and the
box-score notes, total and own seasons it reads beside the rows, which the
presenter discarded - is not called. Measured first
(``~/association-research/stages/slice2a_measure.py``): the compiled rows
are the presenter's on 31 of the 31 recorded answers it gave.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from typing import Any

import duckdb

from association.nba.coverage import COVERAGE
from association.query.entities import Entity
from association.query.lines import measure_filters, threshold_count_line
from association.query.notes import Note
from association.query.player_games import REBUILT_STATS
from association.query.reading import Unsupported
from association.query.result import Grouped, Part, Result, Scalar, Span
from association.query.season_line import seasons_on_record
from association.query.templates.common import STAT_LABELS, TemplateResult, player_relation_season_type, unhonored_scoping
from association.query.templates.players import empty_box_scores, rebuilt_in_scope

from .core import Query, compile_query, rows_of


def _threshold_count_is_own_point(q: Query, column: str) -> bool:
    """Whether ``q`` counts exactly the games ``threshold_count`` counts: the
    router's own stat at its own threshold (or a below/above line carrying
    that threshold instead, which the count reads as the line), for one
    player or grouped by player over the league - nothing the question's
    words added."""
    threshold = q.scope.threshold
    counted = [(column, ">=", threshold)] if threshold is not None else []
    if q.predicates not in (counted, []) or q.position:
        return False
    if q.subject == "player":
        return q.skeleton == "scalar" and q.aggregate == "count"
    return q.skeleton == "grouped" and q.aggregate == "count" and q.group == "player"


def box_score_span(con: duckdb.DuckDBPyConnection, season: int | None, season_type: int, player: Entity | None) -> tuple[Span, list[Note]]:
    """The span a count or a single-game high read from box scores covers,
    as values: one season, or a career - a named player's own first and
    last season on record (:func:`~association.query.season_line.seasons_on_record`,
    which reaches back before any box score), or the league's from the box
    scores' floor - and the floor note where the answer has to say it
    FIRST: Michael Jordan's career began in 1984-85, and his "career high"
    from box scores that begin in 1993-94 is 55, not 69 - so a career that
    began before them is answered for the part they hold, and says so
    before the number (``templates.players._game_span`` was the read and
    the words both, until the count's and the high's sayers took the
    words).

    .. versionadded:: 5.0.0
    """
    floor = COVERAGE["player_box_stats"].first_season
    if season is not None or player is None:
        return Span(season=season, season_type=season_type, career=season is None), []
    began, ended = seasons_on_record(con, player.id, season_type)
    span = Span(season=None, season_type=season_type, career=True, first=began if isinstance(began, int) else None, last=ended if isinstance(ended, int) else None)
    if isinstance(began, int) and began < floor:
        return span, [Note("floor", {"table": "box_scores", "first": floor, "earliest": began, "whose": player.name, "season_type": season_type, "what": "career_began_earlier"})]
    return span, []


def _threshold_count_rows(q: Query, player: Entity | None, rows: list[dict[str, Any]]) -> list[tuple[Any, int, int]]:
    """The compiled count as ``(name, qualifying games, rebuilt games among
    them)``, most first: one row for a named player with any, none for one
    with none, one per player for the league."""
    if q.subject == "player":
        games = int(rows[0].get("games") or 0) if rows else 0
        return [(player.name, games, int(rows[0].get("rebuilt_shown") or 0))] if games and player is not None else []
    return [(r["group"], int(r["games"]), int(r.get("rebuilt_shown") or 0)) for r in rows]


def read_threshold_count(con: duckdb.DuckDBPyConnection, q: Query, *, stated: frozenset[str]) -> Result | TemplateResult | None:
    """``threshold_count``'s own point - a named player's games clearing a
    line, counted, or the league's count by player - read into a Result
    over the compiled statement. ``None`` where the point is not that (a
    line the question's words added, another skeleton, a stat no line is
    kept on, a below/above phrase naming no column) or carries a narrowing
    the count's words do not state (``stated``:
    ``compose.present.STATED_SCOPING``'s set), and the compiler's own
    sentence answers.

    .. versionadded:: 5.0.0
    """
    scope = q.scope
    if unhonored_scoping("threshold_count", scope, stated):
        return None
    try:
        # The count's column and threshold, read the one way its default
        # point reads them: a below/above phrase may be the whole line, with
        # no threshold at all ("Sga games with under 14 fta").
        column, threshold = threshold_count_line(scope)
        lines = measure_filters(scope.below, scope.above)
    except Unsupported:
        return None
    if not _threshold_count_is_own_point(q, column):
        return None
    compiled = compile_query(con, q)
    player = compiled.player
    rows = _threshold_count_rows(q, player, rows_of(con, compiled))
    season, season_type = compiled.span.season, player_relation_season_type(scope)
    span, notes = box_score_span(con, season, season_type, player)
    empty = empty_box_scores(con, season, season_type, player.id if player is not None else None, covered_by_rebuild=compiled.rebuilt)
    stat = scope.stat
    label = STAT_LABELS.get(stat or "", stat or "")
    notes += _threshold_count_notes(con, (season, season_type), player, rows, column, label, empty)
    facts: dict[str, Any] = {
        "stat": stat,
        # A phrase carrying the count's own number IS the count, misread: the
        # phrase wins, since it holds the direction and the column the model
        # lost; a phrase with another number is a second line beside it.
        "counted": None if any(line.value == threshold for line in lines) else threshold,
        "lines": [line.label for line in lines],
        "ordinal": compiled.span.ordinal if season is not None else None,
        "box_scores_from": COVERAGE["player_box_stats"].first_season,
        "empty_box_scores": empty[0],
    }
    if player is not None:
        body: Scalar | Grouped = Scalar(games=rows[0][1] if rows else 0, sums={"rebuilt": rows[0][2] if rows else 0}, how="count")
    else:
        body = Grouped(by="player", ranked_by="games", rows=tuple({"key": name, "games": games, "rebuilt": rebuilt} for name, games, rebuilt in rows))
    return Result(
        subject=player.name if player is not None else "every player",
        relation="player" if player is not None else "everyone",
        span=span,
        parts=(Part(body=body),),
        notes=tuple(notes),
        facts=facts,
    )


def _threshold_count_notes(
    con: duckdb.DuckDBPyConnection, seasons: tuple[int | None, int], player: Entity | None, rows: list[tuple[Any, int, int]], column: str, label: str, empty: tuple[int, int | None, int | None]
) -> list[Note]:
    """The count's notes past its sentence, in its order: a league-wide
    career is not all-time, a stat withheld from rebuilt lines (else the
    empty box scores), and the leader's rebuilt games."""
    season, season_type = seasons
    notes: list[Note] = []
    if season is None and player is None:
        notes.append(Note("floor", {"table": "box_scores", "first": COVERAGE["player_box_stats"].first_season, "what": "league_counts"}))
    # Only when nothing was counted AND the stat was deliberately withheld: a
    # count of none that names a decision beats one that implies missing data.
    withheld = 0 if (rows and rows[0][1]) or column in REBUILT_STATS else rebuilt_in_scope(con, season, season_type, player.id if player is not None else None)
    if withheld:
        notes.append(Note("stat_withheld", {"games": withheld, "stat": column, "label": label}))
    elif empty[0] and empty[1] is not None and empty[2] is not None:
        notes.append(Note("games_unseen", {"why": "empty_box_score", "games": empty[0], "first": empty[1], "last": empty[2], "whose": player.name if player is not None else None}))
    # Said whenever the COUNT rests on rebuilt games, not whenever one was
    # read: a rebuilt game that cleared no threshold changes nothing about
    # the number the reader was given.
    if rows and rows[0][2]:
        notes.append(Note("lines_rebuilt", {"games": rows[0][2], "total": rows[0][1], "whose": None if player is not None else rows[0][0], "what": "counted"}))
    return notes
