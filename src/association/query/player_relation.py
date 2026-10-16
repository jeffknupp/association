"""The player-games relation's shared steps: settling who a question is about and
the seasons it covers, and narrowing his games - or the league's - by every
cell the relation carries.

Every reader on the relation narrows through these and through nothing else:
an opponent, a venue, a teammate's absence, a starter/bench half, one game of
a series, a line on a box-score column, a date, a calendar or conference
``situation``, a quarter or half, and a window of the newest or oldest N -
declared once, in :data:`RELATION_SCOPING`.
:mod:`association.query.player_games` holds the clause builder
(:class:`~association.query.player_games.Narrowed`) and the statements; this
module holds the steps that settle the names and spans and apply the cells,
beside it rather than in it because the two together would pass 2,500 lines.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

import duckdb

from association.nba.coverage import COVERAGE, REGULAR_SEASON
from association.nba.season import current_season, eastern_day_utc_range
from association.query.conditions import _game_scope, _Scope, box_source
from association.query.entities import BOX_SCORES, Ambiguous, Availability, Entity, clarify, find_players, players_of, resolve_player, resolve_team, resolved_player, resolved_team, teammate_names
from association.query.lines import MeasureFilter, measure_filters
from association.query.notes import Note
from association.query.player_games import (
    _PLAYER_GAMES,
    BOTH_SEASON_TYPES,
    CONDITION_PREDICATES,
    PERIOD_COLUMNS,
    PERIOD_PLAYS_COLUMNS,
    STARTER_SIDES,
    STAT_LABELS,
    THRESHOLD_STAT_COLUMNS,
    Condition,
    Narrowed,
    _tenure_clause,
    aggregate_sql,
    league,
    scope_without_guard,
    season_type_clause,
)
from association.query.reading import (
    Companion,
    Cuts,
    DeclineReason,
    Filtered,
    Measure,
    Period,
    PointShape,
    ShapeCells,
    ShapeDeclined,
    Situation,
    Span,
    Unsupported,
    Window,
    _clamp_limit,
    ordinal_word,
    period_narrowing,
)
from association.query.reading import Line as ReadLine
from association.query.result import Cell, GameOfSeries, Line, Refusal, Role, Unanswered
from association.query.season_text import SEASON_TYPE_NAMES, season_phrase
from association.query.team_games import TeamNarrowed

# What the player-games relation narrows by, declared ONCE. Every reader (and
# the compiler) that settles its player through `scoped_player` and his games
# through `scoped_games` honors all of these, because the narrowing is done
# there and not in the reader: an opponent, his own team (a tenure), a venue, a
# teammate's absence, a named half of the starter/bench split, one game of
# each playoff series, a line on a box-score column, one Eastern date, a
# calendar or alignment situation, a span of seasons, a first season, an
# ordinal season. The window (`reading.Window`: an end of the span with its
# count) is the newest or oldest N of the
# narrowed games, which the relation cuts after every row filter and before
# whatever the reader does with the rows, so "30-point games in his last 10"
# counts inside the ten (the one skeleton-specific rule).
# `scoped_games` sets it (`relation_window`, below): a NAMED end wins
# outright, and a bare count with no end still means the newest N - the
# grammar reads "Create a shot chart for Steph Curry's last two games of the
# regular season" as a count alone once the chart's one-game end is dropped,
# so a rule gated on the end alone would never reach that question.
#
# One declaration for the relation, never a list per reader: lists kept per
# reader drift apart, a cell taught to one reader at a time. A shape on the relation whose words cannot
# honor one of these says so in its row of RELATION_SCOPING_EXCLUDED, with the
# reason.
#
# The games' cuts (`reading.Cuts`) are seven of
# the eight by name: `round` is left out, since no game is labeled by its
# round and every reader refuses it (`unhonored_cells` lists it wherever it
# is set). The tenure is a cell of this relation alone: a team has none.
RELATION_SCOPING = frozenset({"split", *ReadLine.CELLS, *Companion.CELLS, *(Cuts.CELLS - {"round"}), *Span.CELLS, *Window.CELLS, *Period.CELLS})
"""The cells every reader on the player-games relation honors: the split,
the subject's lines (:attr:`~association.query.reading.Line.CELLS`:
``line``, a line the relation narrows the games by - kept under
a number, a floor of minutes, two or more "N+ stat" pairs on one game -
applied by :func:`narrow_measures` and said by
:meth:`~association.query.player_games.Narrowed.filters`; ``period_line``,
a line in a quarter or half, applied by :func:`_apply_period_condition`),
the companions (:attr:`~association.query.reading.Companion.CELLS`:
``companion``, a player beside the subject with his role, applied by
:func:`_narrow_player_games` through :func:`_condition_from_slot`), the games' cuts (:attr:`~association.query.reading.Cuts.CELLS`
less ``round``: ``opponent``, ``tenure``, ``venue``, ``date``,
``situation``, ``game_n``, ``season_n``, each applied by :func:`scoped_games`
and said by :meth:`~association.query.player_games.Narrowed.filters`), and
the span's three cells (:attr:`~association.query.reading.Span.CELLS`) -
``career`` (every season on record), ``range`` (a
career cut at one or both ends: ``since``, ``until``) and ``both`` (both
season types in one read), each applied by :func:`span_of` through
:func:`scoped_player`, said by :meth:`ResolvedSpan.during` and
:meth:`ResolvedSpan.years`, and refused where the span contradicts itself
(a career beside a named season, a range beside one) by :func:`span_of`.
A reader whose words do not state one of these steps aside for it, or
refuses it, by :data:`RELATION_SCOPING_EXCLUDED`.

.. versionadded:: 4.4.0

.. versionchanged:: 5.0.0
   ``period_condition`` - a quarter or half as a condition on which games
   count (ROADMAP step 2, #275), applied by :func:`_apply_period_condition`.

.. versionchanged:: 6.0.0
   The lines' two cells (``line``, ``period_line``) and the companions' one
   (``companion``) in place of the slots ``below``, ``above``,
   ``period_condition``, ``without`` and ``conditions`` (Phase 3, step 2).

.. versionchanged:: 6.0.0
   The span's cells by name (``career``, ``range``, ``both``) in place of
   the slots ``span``, ``since`` and ``until``; ``both`` was each reader's
   own extra (``season_type_unstated``) until then. The games' cuts by name,
   the ``tenure`` among them, which the compiler alone passed until then.
   The period's one cell (:attr:`~association.query.reading.Period.CELLS`:
   ``period``, a quarter or a half, what a read SEES of each game - applied
   by :func:`apply_period`, said by :meth:`~association.query.player_games.Narrowed.filters`)
   in place of the slots ``period`` and ``half``.
"""


# Each shape's row on the relation: the cells its reader's words do NOT
# state, each with why, and what the planner does about one
# (`reading.ShapeCells`). A reason has to be about the shape's answer, not its
# code: a cell that merely was not wired is not excluded, it is wired. Keyed
# by the planned point's shape (`reading.PointShape`, the answer side's one
# key): one row per reader on this relation or one settling its player and
# span through this relation's steps - the season line's, the NetPoints
# relation's and the shot relation's (`compose._ROUTES`, whose keys a test
# holds equal to the two tables'). The row is the one declaration of a
# shape's cells; the planner reads it and keeps no table of its own.
#
# The span's `both` cell (both season types in one read) is stated by a
# log, a line and a count - their words said "including the playoffs", or
# named no type on "last N games" - and by nothing else on the relation: a
# reader whose answer is one season type's steps aside, and the compiler's
# own sentence, which reads both and says so, answers. The reason is the
# answer's in each case, as the rule above asks (`BOTH_TYPES_ASIDE`). Each
# reason is a `reading.DeclineReason` member, which the sayer words
# (`compose.say.decline_reason_phrase`).
# The relation's cells beyond the span and the cuts - the line family, the
# companions, a split, a window, the ranked measure, a quarter - are the
# narrowings a shape that reads one row per player per season or per game,
# or one season's rating, has no game to narrow by; each is said here with
# its reason, as every cut and span cell such a shape steps aside for is.


def _excluded_rows(cells: frozenset[str], why: DeclineReason) -> dict[str, DeclineReason]:
    """``cells``, in name order, each with the one reason a shape gives for all of them."""
    return dict.fromkeys(sorted(cells), why)


RELATION_SCOPING_EXCLUDED: dict[PointShape, ShapeCells] = {
    # A log's and a line's sentences head whole games and never name
    # a quarter or half: their readers step aside for one, and the
    # compiler's own sentence, which names the period through
    # Narrowed.filters, answers. Each honors one named half of the
    # starter/bench split, never the bare category.
    PointShape("player_games", "rows", "date"): ShapeCells(unstated={"period": DeclineReason.LOG_WHOLE_GAMES}, sides=True),
    PointShape("player_games", "scalar", "line"): ShapeCells(unstated={"period": DeclineReason.LINE_WHOLE_GAMES}, sides=True),
    PointShape("player_seasons", "scalar", "line"): ShapeCells(unstated={"period": DeclineReason.LINE_WHOLE_GAMES}, sides=True),
    # A run, a split and a record are read over every game in the span: one date
    # is no run and has nothing to split, and the last N games are game_log's
    # question. A quarter or half narrows what a read SEES of each game, which a
    # sentence headed as whole games would not say.
    PointShape("player_games", "split", "splits"): ShapeCells(
        unstated={
            "date": DeclineReason.SPLITS_ONE_GAME,
            "window": DeclineReason.SPLITS_RECENT_GAMES,
            "period": DeclineReason.SPLITS_WHOLE_GAMES,
            "both": DeclineReason.BOTH_TYPES_ASIDE,
        }
    ),
    PointShape("player_games", "split", "line"): ShapeCells(
        unstated={
            "date": DeclineReason.RECORD_ONE_GAME,
            "window": DeclineReason.RECORD_RECENT_GAMES,
            "period": DeclineReason.RECORD_WHOLE_GAMES,
            "both": DeclineReason.BOTH_TYPES_ASIDE,
        }
    ),
    # A run's games are ONE player's, or a team's: a teammate's role, a starter
    # half, an ordinal season or a line is a fact about a named player's game,
    # and the games "game 4 of each series" picks out are not consecutive to
    # each other; the league's run has no single subject for an opponent or a
    # venue to narrow against.
    PointShape("player_games", "runs", "line"): ShapeCells(
        unstated={
            "date": DeclineReason.RUN_ONE_GAME,
            "window": DeclineReason.RUN_EVERY_GAME,
            "period": DeclineReason.RUN_WHOLE_GAMES,
            "both": DeclineReason.BOTH_TYPES_ASIDE,
        },
        refused=frozenset({"date", "window", "period"}),
        named_player=frozenset({"companion", "split", "season_n", "line", "game_n"}),
        named_subject=frozenset({"opponent", "venue"}),
    ),
    # A quarter's or half's accuracy caveat (PERIOD_RECONCILIATION) is measured
    # per SEASON against ESPN's own linescores, so a read across seasons - a
    # career, a range, both season types, the last N games across seasons
    # (decisions D13, D14) - is caveated season by season and labeled by the
    # seasons and types it holds (`compose.periods`); a season under
    # PERIOD_REFUSE_BELOW is left out of such a read and said. The period
    # CONDITION (a quarter conditioning which games count, beside the quarter
    # measured) is excluded from the reader's WORDS, not from the point: it steps
    # aside and the compiler's own sentence, which names both quarters, answers.
    **dict.fromkeys(
        (PointShape("player_periods", "rows", "date"), PointShape("player_periods", "split", "period")),
        ShapeCells(unstated={"period_line": DeclineReason.PERIOD_ONE_PERIOD}, sides=True),
    ),
    # Two players' meetings, refused outright where a narrowing would cut
    # the pair the matchup exists to compare.
    PointShape("player_games", "comparison", "met"): ShapeCells(
        unstated={
            "opponent": DeclineReason.MEETINGS_NO_THIRD_TEAM,
            "window": DeclineReason.MEETINGS_AVERAGES,
            "season_n": DeclineReason.MEETINGS_WHOSE_SEASON,
            "period": DeclineReason.MEETINGS_PERIOD,
            "period_line": DeclineReason.MEETINGS_PERIOD_LINE,
            "both": DeclineReason.BOTH_TYPES_ASIDE,
        },
        refused=frozenset({"opponent", "window", "season_n", "period", "period_line"}),
    ),
    # A high's words state a career; the compiler's own ranking of games,
    # which says the narrowing it applied, answers the rest.
    PointShape("player_games", "rows", "measure"): ShapeCells(
        unstated={
            "both": DeclineReason.BOTH_TYPES_ASIDE,
            "range": DeclineReason.HIGH_RANGE_ASIDE,
            **_excluded_rows(Cuts.CELLS - {"round"}, DeclineReason.HIGH_CUTS_ASIDE),
            **_excluded_rows((RELATION_SCOPING - Span.CELLS - Cuts.CELLS), DeclineReason.HIGH_NARROWINGS_ASIDE),
        }
    ),
    # A count is already a line on a column: the `line` cell is the same line
    # the other way ("games with under 14 fta"), and a phrase carrying the
    # count's own number IS the count, misread - compose.counts reads it so.
    # The span's `both` cell is stated the way `scoped_player` reads it - one
    # combined `season_type IN (2, 3)` read (player_relation_season_type); the
    # one cut its words state is an ordinal season.
    **dict.fromkeys(
        (PointShape("player_games", "scalar", "count"), PointShape("player_games", "ranking", "count"), PointShape("player_games", "rows", "count")),
        ShapeCells(
            unstated={
                "range": DeclineReason.COUNT_RANGE_ASIDE,
                **_excluded_rows(Cuts.CELLS - {"round", "season_n"}, DeclineReason.COUNT_CUTS_ASIDE),
                **_excluded_rows(
                    RELATION_SCOPING - Span.CELLS - Cuts.CELLS - {"line"},
                    DeclineReason.COUNT_NARROWINGS_ASIDE,
                ),
            }
        ),
    ),
    # The season line's readers settle their player and span through this
    # relation's steps (`scoped_player`): a ranking of season lines pools one
    # season or a career, never a range (the game-level ranking reads one), and
    # takes a unit beyond the relation (a season total, a NetPoints metric's
    # per-100 form; a unit the metric has no form of is the point's own
    # `ranking_unit` refusal); a history is every season or the last N.
    PointShape("player_seasons", "ranking", "player"): ShapeCells(
        unstated={
            "range": DeclineReason.SEASON_RANKING_RANGE,
            "both": DeclineReason.SEASON_RANKING_BOTH,
            **_excluded_rows(Cuts.CELLS - {"round"}, DeclineReason.SEASON_LINE_CUTS_ASIDE),
            **_excluded_rows((RELATION_SCOPING - Span.CELLS - Cuts.CELLS), DeclineReason.SEASON_LINE_NARROWINGS_ASIDE),
        },
        # A unit, and a line on the season's own figure ("averaged 30 ppg",
        # ISSUES.md #390), which the ranking keeps its players past.
        taken=Measure.CELLS | ReadLine.SEASON_CELLS,
    ),
    # Players ranked by how many of their seasons a season line holds
    # (ISSUES.md #391) pool every season on record: no span is stated, and a
    # season's figure is no game to cut.
    PointShape("player_seasons", "ranking", "seasons"): ShapeCells(
        unstated={
            "range": DeclineReason.SEASONS_COUNTED_RANGE,
            "both": DeclineReason.SEASON_COUNT_BOTH,
            **_excluded_rows(Cuts.CELLS - {"round"}, DeclineReason.SEASON_LINE_CUTS_ASIDE),
            **_excluded_rows((RELATION_SCOPING - Span.CELLS - Cuts.CELLS), DeclineReason.SEASON_LINE_NARROWINGS_ASIDE),
        },
        taken=ReadLine.SEASON_CELLS,
        declined="plan",
    ),
    # How many players a season line holds is that ranking counted (ISSUES.md
    # #390): the same cells, said the same way.
    PointShape("player_seasons", "scalar", "count"): ShapeCells(
        unstated={
            "range": DeclineReason.SEASON_COUNT_RANGE,
            "both": DeclineReason.SEASON_COUNT_BOTH,
            **_excluded_rows(Cuts.CELLS - {"round"}, DeclineReason.SEASON_LINE_CUTS_ASIDE),
            **_excluded_rows((RELATION_SCOPING - Span.CELLS - Cuts.CELLS), DeclineReason.SEASON_LINE_NARROWINGS_ASIDE),
        },
        taken=Measure.CELLS | ReadLine.SEASON_CELLS,
    ),
    PointShape("player_seasons", "split", "season"): ShapeCells(
        unstated={
            "range": DeclineReason.HISTORY_RANGE,
            "both": DeclineReason.HISTORY_BOTH,
            **_excluded_rows(Cuts.CELLS - {"round"}, DeclineReason.SEASON_LINE_CUTS_ASIDE),
            **_excluded_rows((RELATION_SCOPING - Span.CELLS - Cuts.CELLS), DeclineReason.SEASON_LINE_NARROWINGS_ASIDE),
        }
    ),
    # Two or more players' lines side by side state no narrowing at all:
    # "compare curry and lebron vs the celtics" answered for the whole season
    # would be the substitution the cells exist to stop.
    PointShape("player_seasons", "comparison", "subject"): ShapeCells(
        unstated={
            "career": DeclineReason.COMPARISON_SPAN,
            "range": DeclineReason.COMPARISON_SPAN,
            "both": DeclineReason.COMPARISON_SPAN,
            **_excluded_rows(Cuts.CELLS - {"round"}, DeclineReason.SEASON_LINE_CUTS_ASIDE),
            **_excluded_rows((RELATION_SCOPING - Span.CELLS - Cuts.CELLS), DeclineReason.COMPARISON_NARROWINGS),
        },
        declined="plan",
    ),
    # A ranking by a quarter is one season's pool: the rebuilt figures'
    # accuracy is measured per season and type.
    PointShape("player_periods", "ranking", "player"): ShapeCells(
        unstated={
            "career": DeclineReason.QUARTER_RANKING_SEASON,
            "range": DeclineReason.QUARTER_RANKING_SEASON,
            "both": DeclineReason.QUARTER_RANKING_TYPE,
            **_excluded_rows(Cuts.CELLS - {"round", "opponent", "venue"}, DeclineReason.QUARTER_RANKING_CUTS),
            **_excluded_rows(
                (RELATION_SCOPING - Span.CELLS - Cuts.CELLS) - {"period"},
                DeclineReason.QUARTER_RANKING_NARROWINGS,
            ),
        },
        declined="plan",
    ),
    # The NetPoints relation's two: one season's rating or fingerprint, or one
    # game's chosen as a first or last game (the window); a calendar date on a
    # fingerprint is honored by refusing it in the reader's own words - the
    # loader picks a player's first or last game, which is a different
    # question from a date (``compose.netpoints``).
    PointShape("netpoints", "scalar", "ratings"): ShapeCells(
        unstated={
            "career": DeclineReason.NETPOINTS_CAREER,
            "range": DeclineReason.NETPOINTS_RANGE,
            "both": DeclineReason.NETPOINTS_BOTH,
            **_excluded_rows(
                Cuts.CELLS - {"round"},
                DeclineReason.NETPOINTS_CUTS,
            ),
            **_excluded_rows((RELATION_SCOPING - Span.CELLS - Cuts.CELLS) - {"window"}, DeclineReason.NETPOINTS_NARROWINGS),
        },
        declined="plan",
    ),
    PointShape("netpoints", "chart", "fingerprint"): ShapeCells(
        unstated={
            "career": DeclineReason.FINGERPRINT_SPAN,
            "range": DeclineReason.FINGERPRINT_SPAN,
            "both": DeclineReason.FINGERPRINT_BOTH,
            **_excluded_rows(
                Cuts.CELLS - {"round", "date"},
                DeclineReason.FINGERPRINT_CUTS,
            ),
            **_excluded_rows((RELATION_SCOPING - Span.CELLS - Cuts.CELLS) - {"window"}, DeclineReason.FINGERPRINT_NARROWINGS),
        },
        declined="plan",
    ),
    # A shot read draws every shot of the games the relation narrows to; a
    # quarter narrows the LINE of each game, not which of its shots are drawn,
    # so the chart would be the whole game under a quarter's heading.
    PointShape("shots", "chart", "shots"): ShapeCells(
        unstated={"period": DeclineReason.SHOT_CHART_PERIOD, "both": DeclineReason.BOTH_TYPES_ASIDE},
        declined="plan",
        sides=True,
    ),
    PointShape("shots", "scalar", "distance"): ShapeCells(
        unstated={"period": DeclineReason.SHOT_DISTANCE_PERIOD, "both": DeclineReason.BOTH_TYPES_ASIDE},
        declined="plan",
        sides=True,
    ),
}
"""Per shape, what its reader's words do not state of the relation's cells
(:data:`RELATION_SCOPING`), why, and what the planner does about one
(:class:`~association.query.reading.ShapeCells`): the reader steps aside and
the compiler's own sentence answers; or the planner refuses it outright,
saying why (a run, a quarter's split, two players' meetings), or declines it
where the reader is the point's only answer (a comparison, a ranking by a
quarter, the NetPoints and shot relations' readers). Read by the planner's
one check (:func:`~association.query.compose.plan.cells_unhonored`).

.. versionadded:: 4.4.0

.. versionchanged:: 6.0.0
   The span's cells (``career``, ``range``, ``both``) per reader, the
   season line's and the NetPoints relation's readers included, since they
   settle their span through this relation's steps (Phase 3, step 2); the
   games' cuts per reader whose words state fewer than the relation's.

.. versionchanged:: 6.0.0
   Keyed by :class:`~association.query.reading.PointShape`, one
   :class:`~association.query.reading.ShapeCells` per shape a reader on
   this relation reads (Phase 3, step 2's closing slice): by the retired
   templates' names until then, with a reason per cell and nothing else -
   what a shape's words stated was ``compose.plan.STATED_SCOPING``, built
   from these rows by ``relation_scoping``, ``relation_span``,
   ``relation_cuts`` and ``relation_period``, all deleted.
"""


def narrow_measures(narrowed: Narrowed, filters: list[MeasureFilter]) -> None:
    """Apply :func:`measure_filters`' lines to a relation read, each with its
    label so the answer names what it kept."""
    for line in filters:
        narrowed.narrow_measure(line.column, line.op, line.value, line.label)


def career_end(season: int | None) -> int | None:
    """The ``through`` a span narrows a name by. None for one season, which
    narrows by ``season`` itself; the current season for a career, which keeps
    everybody with a row on record - Dell Curry's career is a real answer to
    "curry career points" - but names whoever plays now first, rather than
    cutting Stephen behind five retired Currys."""
    return current_season() if season is None else None


@dataclass(frozen=True)
class ResolvedSpan:
    """The seasons an answer covers: one (``season``), or a whole career
    (``season`` None) from ``first`` on, less any ``phantom`` season that is a
    copy of another.

    ``defaulted`` is True when ``season`` was never named - the question asked
    about "now", not about this particular year - and False when the question
    named it outright, career spans included (where the field is meaningless:
    a career has no single season to have been defaulted). It is what lets a
    refusal built from this span tell "the current season has nothing on
    record" from "the season you named has nothing on record": only the first
    is safe to redirect toward the player's other seasons (issue #18), because
    the second is a correct, specific answer and redirecting it would be the
    same guess-dressed-as-an-answer this project keeps refusing to make."""

    season: int | None
    season_type: int
    first: int = 0
    phantom: tuple[int, ...] = ()
    defaulted: bool = False
    #: The season a "since" question starts from - a career cut at the front,
    #: so the answer says "since 2022" and skips the box-score-floor note that
    #: a whole career carries (the question asked for no earlier season).
    since: int | None = None
    #: The inclusive LAST season of a ``since``-bounded range ("2019-20 to
    #: 2023-24", a decade) - None for an open-ended "since 2022", which still
    #: reaches the present. Only ever set alongside ``since`` (see
    #: :func:`span_of`); the answer says "2019-2024" rather than "since 2019"
    #: once it is.
    #:
    #: .. versionadded:: 4.4.0
    until: int | None = None
    #: Which season of his career this one is, when the question named it that
    #: way ("his 18th season") - so the answer says so beside the year.
    ordinal: int | None = None

    @property
    def career(self) -> bool:
        """Every season, rather than one."""
        return self.season is None

    @property
    def kind(self) -> str:
        """``"regular season"`` or ``"postseason"``."""
        return SEASON_TYPE_NAMES.get(self.season_type, "regular season")

    def clause(self, column: str) -> tuple[str, list[Any]]:
        """SQL restricting ``column`` to these seasons, and its parameters.

        .. versionchanged:: 4.4.0
           Bounds the upper end too when ``until`` is set.
        """
        if self.season is not None:
            return f"{column} = ?", [self.season]
        # The phantom is excluded by name, not left to the floor: 1993 is a full,
        # healthy-looking copy of 1994 (see coverage.Coverage.phantom), and a
        # career that counted it would list every 1993-94 game twice.
        excluded = f" AND {column} NOT IN ({', '.join('?' for _ in self.phantom)})" if self.phantom else ""
        if self.until is not None:
            return f"{column} BETWEEN ? AND ?{excluded}", [self.first, self.until, *self.phantom]
        return f"{column} >= ?{excluded}", [self.first, *self.phantom]

    def years(self, first: Any, last: Any) -> str:
        """The seasons a career answer's rows actually reach: ``"2024-2026
        regular seasons"``, or one season's name.

        .. versionchanged:: 4.4.0
           A :data:`~association.query.player_games.BOTH_SEASON_TYPES` span
           pluralizes each half ("2024-2026 regular seasons and postseasons")
           rather than tacking an "s" onto the end of "regular season and
           postseason", which reads as though only the second half repeated.
        """
        if first is None or last is None:
            return f"{self.kind}s"
        if first == last:
            return f"{first} {self.kind}"
        if self.season_type == BOTH_SEASON_TYPES:
            return f"{first}-{last} regular seasons and postseasons"
        return f"{first}-{last} {self.kind}s"

    def during(self, first: Any = None, last: Any = None, whose: str = "his career") -> str:
        """The span as it ends a sentence: ``"in the 2026 regular season"`` or
        ``"over his career (2019-2026 regular seasons)"``.

        .. versionchanged:: 4.4.0
           Says "from 2019 through 2024" rather than "since 2019" once
           ``until`` bounds the range - the "2011-2019" wording (step 3, K1).
        """
        if self.season is not None and self.ordinal is not None:
            return f"in his {ordinal_word(self.ordinal)} season ({season_phrase(self.season, self.season_type)})"
        if self.season is not None:
            return f"in the {season_phrase(self.season, self.season_type)}"
        if self.since is not None and self.until is not None:
            return f"from {self.since} through {self.until} ({self.years(first, last)})"
        if self.since is not None:
            return f"since {self.since} ({self.years(first, last)})"
        return f"over {whose} ({self.years(first, last)})"


def validated_until(until: int | None, since: int | None) -> int | None:
    """The validated ``until`` slot: an inclusive last season, named beside
    ``since`` only - the span tagger never reads one without the other (a
    decade, or a named range like "2019-20 to 2023-24"),
    so a caller checks this before ``since`` has necessarily reached
    :func:`span_of` itself (a reader that reads ``since`` through its own
    code path, the way ``TEAM_RANKING``'s did for a non-record metric, would otherwise drop
    ``until`` silently rather than refusing it - the failure shape AGENTS.md
    warns against).

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Public, for the team-season ranking's reader; ``validated_until`` until then.
    """
    if not until:
        return None
    if not since:
        raise Unsupported(f"until {until} with no since")
    if until < since:
        raise Unsupported(f"until {until} before since {since}")
    return until


def span_of(span: Span, table: str, *, season_type: int | None = None) -> ResolvedSpan:
    """The seasons a question covers - the relation's reading of the typed
    :class:`~association.query.reading.Span`. ``table`` sets how far back a
    career reaches - box scores from 1994, the season line from 1977 -
    since a career is only as long as the table it is summed from. A range
    (``since``) is a career that starts there instead: every season from it
    on, the phantom still excluded, and never earlier than the table
    reaches; ``until`` bounds the other end - the inclusive last season -
    and is validated against ``since`` here (see :func:`validated_until`).
    No season named is the current one (``defaulted``); a career beside a
    named season, or a range beside one, is refused rather than picked
    between. ``season_type`` is the type to read, where a caller reads
    both types as one (:func:`player_relation_season_type`); the span's own
    type, or the regular season, otherwise.

    .. versionchanged:: 4.3.0
       Honors ``since``.

    .. versionchanged:: 4.4.0
       Honors ``until`` (step 3, K1).

    .. versionchanged:: 6.0.0
       Takes the typed :class:`~association.query.reading.Span` (Phase 3,
       step 2) in place of the six slot values.
    """
    kind = season_type if season_type is not None else (span.season_type or REGULAR_SEASON)
    season, since = span.season, span.since
    until = validated_until(span.until, since)
    if since:
        if season:
            raise ShapeDeclined("span_and_season", {"season": season, "since": since}, f"since {since} and the {season} season at once")
        coverage = COVERAGE[table]
        return ResolvedSpan(None, kind, max(since, coverage.floor(kind).season), coverage.phantom, since=since, until=until)
    if not span.career:
        return ResolvedSpan(season or current_season(), kind, defaulted=not season)
    if season:
        # "Career" and a named year at once. Either reading answers a different
        # question from the other, so neither is picked.
        raise ShapeDeclined("span_and_season", {"season": season, "since": None}, f"a career span and the {season} season at once")
    coverage = COVERAGE[table]
    return ResolvedSpan(None, kind, coverage.floor(kind).season, coverage.phantom)


def settle_ordinal_season(con: duckdb.DuckDBPyConnection, player: Entity, season_n: Any, span: ResolvedSpan) -> ResolvedSpan | Unanswered:
    """The span a question naming a season by its place in ``player``'s career
    ("his 18th season") actually covers: that year, with the ordinal kept so
    the answer names both. Unchanged when no ordinal was named.

    A career's seasons are its regular seasons on the per-player season table
    (which reaches back to 1976-77, before any box score), counted from his
    first, so a postseason question about "his 18th season" is that year's
    postseason. A player with fewer seasons than the ordinal gets a refusal
    naming how many he has, rather than his last one or the current year.

    .. versionadded:: 4.3.0
    """
    if not season_n:
        return span
    seasons = [
        int(row[0]) for row in con.execute("SELECT DISTINCT season FROM player_season_stats_deduped WHERE athlete_id = ? AND season_type = ? ORDER BY season", [player.id, REGULAR_SEASON]).fetchall()
    ]
    n = int(season_n)
    if n < 1 or n > len(seasons):
        on_record = [seasons[0], seasons[-1]] if seasons else None
        return Refusal(kind="no_such_season_n", facts={"player": player.name, "seasons": len(seasons), "on_record": on_record, "season_n": n}, shown={"player": player.name})
    return ResolvedSpan(seasons[n - 1], span.season_type, ordinal=n)


def _narrow_player_games(
    con: duckdb.DuckDBPyConnection,
    player: Entity,
    span: ResolvedSpan,
    *,
    opponent: Any,
    venue: Any,
    without: Any,
    split: Any = None,
    game_n: Any = None,
    team: Any = None,
    conditions: Sequence[Companion] = (),
) -> Narrowed | Unanswered:
    """``player``'s games in ``span``, narrowed to an opponent, a venue, a
    teammate's absence and a starter/bench half where the question named them.
    A name that needs a clarifying question comes back as the :class:`~association.query.result.Clarify`
    asking it.

    Every narrowing here is a filter over the same set of player-games, which
    is why they compose: a new one becomes available to every caller at once
    rather than being taught to each reader separately. ``split`` was the
    fourth, and it reaches both `game_log` and `player_stat` through this one
    change. ``conditions`` are the scope's typed companions but the
    absences (:class:`~association.query.reading.Companion`), each read by
    :func:`_condition_from_slot`; ``without`` the absent ones' names.

    .. versionchanged:: 4.3.0
       Honors one half of the starter/bench split (``split``), and one game of
       each playoff series (``game_n``).

    .. versionchanged:: 4.4.0
       Takes ``team`` - the player's OWN team, as opposed to ``opponent`` -
       for the shape ``player_stat`` alone opts into
       (``reading.OWN_TEAM_RESTORABLE_ASKS``): "lebron stats as a
       starter for Miami" (yardstick-v2 F166) keeps only the games he played
       for that team, unlike ``game_log``'s own ``team``/``opponent`` dance
       (``games._team_slot_for_player``), which still drops a team the
       player actually played for rather than narrowing by it - a template
       has to ask for this explicitly, so nothing else on the relation is
       affected.
    """
    if game_n and span.season_type != 3:
        # A series has games 1-7; a regular season has nothing "game 4" names -
        # true of BOTH_SEASON_TYPES too (0 != 3), so "game 4 including the
        # playoffs" still refuses rather than guessing which type "game 4" was.
        raise Unsupported(f"game {game_n} names a game of a playoff series, and this is a {span.kind} question")
    season_clause, season_params = span.clause("pgl.season")
    type_clause, type_params = season_type_clause("pgl.season_type", span.season_type)
    narrowed = Narrowed(
        base=["pgl.athlete_id = ?", type_clause, season_clause, "NOT pgl.did_not_play"],
        base_params=[player.id, *type_params, *season_params],
    )
    if team:
        own_team = team if isinstance(team, Entity) else resolved_team(con, team, season=span.season)
        if isinstance(own_team, Unanswered):
            return own_team
        narrowed.team = own_team
        narrowed.extra.append("pgl.team_id = ?")
        narrowed.extra_params.append(own_team.id)
    if opponent:
        # A caller that has already resolved the team (it needs the name for
        # its answer before the games are read) passes the Entity; text is
        # resolved here, so a clarification about the team comes back as the
        # answer either way.
        team = opponent if isinstance(opponent, Entity) else resolved_team(con, opponent, season=span.season)
        if isinstance(team, Unanswered):
            return team
        narrowed.opponent = team
        narrowed.extra.append("pgl.opponent_team_id = ?")
        narrowed.extra_params.append(team.id)
    if venue:
        narrowed.venue = venue
        narrowed.extra.append("(g.home_team_id = pgl.team_id) = ?")
        narrowed.extra_params.append(narrowed.venue == "home")
    if split in STARTER_SIDES:
        # Only a NAMED half filters. `starter_bench` reaches here unchanged
        # when the question named both, and is stepped aside for or declined
        # by the planner for the readers that cannot show a split table
        # (``ShapeCells.sides``, ``compose.plan.cells_unhonored``).
        narrowed.started = STARTER_SIDES[split]
        narrowed.extra.append("pgl.starter = ?")
        narrowed.extra_params.append(narrowed.started)
    # Every name the question gave, required together: "without Tatum and
    # Brown" is the games NEITHER played. Reading only the first answered a
    # question about two players with the games one of them missed - fluently,
    # and with nothing in the answer saying the other had been dropped.
    for text in teammate_names(without):
        mate = _resolved_teammate(con, text, player, span)
        if isinstance(mate, Unanswered):
            return mate
        narrowed.add_condition(_absence_condition(con, mate, player, span, narrowed.opponent), box_source(con))
    # The general shape of the same thing: any
    # player, on either side, under any predicate - "when Embiid and Paul
    # George start", "vs LeBron without Durant", "in games Maxey had 20+".
    for entry in conditions:
        condition = _condition_from_slot(con, entry, player, span, narrowed.opponent)
        if isinstance(condition, Unanswered):
            return condition
        narrowed.add_condition(condition, box_source(con))
    if game_n:
        narrowed.narrow_series_game(int(game_n))
    return narrowed


def player_relation_season_type(scope: Filtered) -> int:
    """The ``season_type`` to read the player relation for: ``BOTH_SEASON_TYPES``
    when the question asked for both explicitly ("including the playoffs") or
    named none at all in a "last N games" question
    (:attr:`~association.query.reading.Span.both`, the span tagger's), else
    the season type the span tagger read from the question's
    own words.

    The game log's own "last N games" merge
    (``compose.logs._player_log_mixed``) does not call this - it interleaves
    two separate reads rather than reading one relation with
    ``season_type IN (2, 3)``, because it needs each type's own count for the
    header. Every other reader on the relation (``scoped_player`` here, and
    the count's league or one-player read, :mod:`association.query.compose.counts`)
    wants exactly the single combined read this
    gives, which is simpler than a merge: an aggregate has no rows to
    interleave.

    .. versionadded:: 4.4.0
    """
    if scope.span.both:
        return BOTH_SEASON_TYPES
    return scope.span.season_type or REGULAR_SEASON


def scoped_player(
    con: duckdb.DuckDBPyConnection,
    scope: Filtered,
    needs: str,
    *,
    table: str,
    available: Availability | tuple[Availability, ...],
    span: Span | None = None,
) -> tuple[Entity, ResolvedSpan] | Unanswered:
    """The player a question is about and the seasons it covers, settled in the
    one order that works - or the :class:`~association.query.result.Clarify` asking which player was meant.

    The span comes first because it is what narrows an ambiguous name: a career
    keeps Dell Curry and this season does not. An ordinal season ("his 18th
    season") cannot be a year until he is known, so the name is narrowed over
    his career and the ordinal settled after. Every reader of a player's
    games takes these steps from here, so a fix to one - the raw season,
    not a defaulted one, is what narrows the name - does not have to
    be found and repeated in each.

    ``span`` overrides the scope's own where a reader has a reason to: a
    date names its own game, so a log reads the career for it
    (:meth:`~association.query.reading.Span.over_career`). ``season_n`` and
    the season type are the question's and are read here.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Reads the typed :class:`~association.query.reading.Filtered`. A slot dict
       was taken until every caller passed ``reading``, the tests last,
       through their slot door (``tests/slots.py``, deleted in Phase 4,
       step 0).
    """
    season_n = scope.cuts.season_n
    asked = span if span is not None else scope.span
    seasons = span_of(asked.over_career() if season_n else asked, table, season_type=player_relation_season_type(scope))
    player = resolved_player(con, scope.who.player, needs, available=available, season=seasons.season, through=career_end(seasons.season))
    if isinstance(player, Unanswered):
        return player
    settled = settle_ordinal_season(con, player, season_n, seasons)
    if isinstance(settled, Unanswered):
        return settled
    return player, settled


def apply_situation[NarrowedT: (Narrowed, TeamNarrowed)](narrowed: NarrowedT, situation: Situation) -> None:
    """Apply the ``situation`` cell to ``narrowed``: the calendar narrowing
    the words named (a weekday, a month, a fixed day, "since <day>") or -
    the other half of the same cell - the conference/division narrowing
    they named ("vs the west", "against the southeast division"), as the
    reader parsed them (:class:`~association.query.reading.Situation`).
    Refused BY VALUE (never silently dropped) when they named neither: the
    relation carries the game's Eastern day and each opponent's
    season-alignment, and nothing about the player's age or a return from
    injury, so dropping the cell would answer a wider question under a
    heading that promised the narrower.

    The one place :func:`scoped_games`/:func:`league_games` (the player
    relation) and :func:`team_games` (the team relation) turn a ``situation``
    into a clause, so a reading either function adds here reaches every
    reader on both relations at once - the same discipline every other
    relation-scoping cell keeps (see ``RELATION_SCOPING``/``TEAM_RELATION_SCOPING``
    above).

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Public (``templates.common._apply_situation`` until then): both
       relations' shared steps call it.

    .. versionchanged:: 6.0.0
       Takes the typed :class:`~association.query.reading.Situation`, parsed
       by the reader; the words are read here no more (Phase 3, step 2).
    """
    if situation.calendar is not None:
        narrowed.narrow_calendar(situation.calendar)
        return
    if situation.alignment is not None:
        narrowed.narrow_alignment(situation.alignment)
        return
    raise Unsupported(
        f'no narrowing in situation {situation.text!r} - a weekday, a month, a holiday, "since <day>", a conference ("vs the west") or a division '
        '("vs the southeast division") is read; an age or anything else is not'
    )


def relation_window(scope: Filtered) -> tuple[str, int] | None:
    """The WINDOW :func:`scoped_games` cuts the narrowed games to - the
    newest or oldest N, after every other filter
    (:attr:`association.query.player_games.Narrowed.window`) - or ``None``
    where the typed window (:class:`~association.query.reading.Window`,
    on ``scope.window``) names neither an end nor a count.

    A named end (``Window.order``) wins outright. Absent one, a count alone
    still means "his last N games": "Create a shot chart for Steph Curry's
    last two games of the regular season" comes with a count and no end,
    as the grammar reads it once the chart's one-game
    end is dropped - so a rule gated on the end alone would never reach the
    real question. No reader on the relation has any other use for a bare
    count - it ranks nothing here, only `leaderboard` does that, over a
    different table - so there is no other reading for one to collide with.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Public (was ``_relation_window``): the period reader cuts its
       cross-season window by it (``compose.periods``).

    .. versionchanged:: 6.0.0
       Reads the typed ``scope.window`` (Phase 3, step 2).
    """
    order: str | None = scope.window.order
    if order is None:
        # A limit, when set, is 1 or more: the filters' own range rule.
        if scope.window.count is None:
            return None
        order = "recent"
    return order, _clamp_limit(scope.window.count, default=1)


def _apply_period_condition(con: duckdb.DuckDBPyConnection, narrowed: Narrowed, condition: ReadLine) -> Unanswered | None:
    """A quarter or half used as a condition on which games count - the
    ``period_line`` cell of :data:`RELATION_SCOPING`
    (a :class:`~association.query.reading.Line` in a period), applied here
    for a named player's games and a league-wide read alike
    (:meth:`~association.query.player_games.Narrowed.narrow_period_condition`).
    A column rebuilt from the plays, in a warehouse holding none, is refused
    rather than read as zero - every game would fail the condition.

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       Takes the typed line (a ``PeriodCondition`` until Phase 3, step 2).
    """
    asked = condition.period.narrowing() if condition.period is not None else None
    stat, threshold = condition.measure, int(condition.value)
    if asked is None or stat is None or stat not in PERIOD_COLUMNS or threshold < 1:
        raise Unsupported(f"no period condition reads {condition!r}")
    periods, label = asked
    plays = has_table(con, "plays")
    if not plays and stat in PERIOD_PLAYS_COLUMNS:
        return Refusal(kind="period_condition_needs_plays", facts={"threshold": threshold, "stat": stat, "period": label})
    noun = STAT_LABELS.get(stat, stat)
    # Said outright either way, so the reading is visible and the other is
    # one word away: "exactly 1 3-pointer" against "1+ 3-pointers".
    phrase = f"exactly {threshold} {noun}{'' if threshold == 1 else 's'} in the {label}" if condition.op == "=" else f"{threshold}+ {noun}s in the {label}"
    log_columns = frozenset(row[0] for row in con.execute("DESCRIBE player_game_log").fetchall())
    narrowed.narrow_period_condition(periods, stat, threshold, phrase, op=condition.op, plays=plays, log_columns=log_columns)
    return None


def period_lines(scope: Filtered) -> list[ReadLine]:
    """The lines ``scope`` holds in a quarter or half (the ``period_line``
    cell), in the question's order.

    .. versionadded:: 6.0.0
    """
    return [line for line in scope.lines if line.period is not None]


def apply_period(con: duckdb.DuckDBPyConnection, narrowed: Narrowed, scope: Filtered) -> None:
    """A quarter or half narrows every read of the relation to that part of
    each game (:meth:`~association.query.player_games.Narrowed.narrow_periods`)
    - the ``period`` cell of :data:`RELATION_SCOPING` (the typed
    :class:`~association.query.reading.Period`), applied here for a named
    player's games and a league-wide read alike."""
    asked = period_narrowing(scope)
    if asked is not None:
        log_columns = frozenset(row[0] for row in con.execute("DESCRIBE player_game_log").fetchall())
        narrowed.narrow_periods(*asked, plays=has_table(con, "plays"), log_columns=log_columns)


def has_table(con: duckdb.DuckDBPyConnection, name: str) -> bool:
    """Whether the warehouse holds ``name`` - a fixture or a partial load may not.

    .. versionadded:: 5.0.0
       Public, both relations' step (``templates.common._has_table`` until then).
    """
    row = con.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [name]).fetchone()
    return bool(row and row[0])


def scoped_games(
    con: duckdb.DuckDBPyConnection,
    player: Entity,
    span: ResolvedSpan,
    scope: Filtered,
    *,
    opponent: Any,
    measures: list[MeasureFilter],
    date: str | None = None,
) -> Narrowed | Unanswered:
    """``player``'s games in ``span`` under every row-level narrowing the
    question carries: opponent, his own team (a tenure), venue, an absent
    teammate, a starter/bench half, a game of each playoff series, lines on
    box-score columns, one date, a calendar or alignment situation, and a
    window (``order``/``limit``) cut after all of the above - the games'
    cuts read off the typed :class:`~association.query.reading.Cuts`
    (``scope.cuts``), one step for every reader on the relation.

    Each is a filter over the same rows, so each means the same thing whatever
    the reader then does with the rows - list them, average them, count them.
    That is why they are read from ``scope`` HERE and not by each reader: a
    cell this does not read is one no reader on the relation can honor, and a
    cell it does read reaches all of them at once. The planner has already
    refused any the relation cannot honor, and a reader steps aside for one
    its words do not state, so nothing arrives here that is not claimed.

    ``opponent`` is passed because ``PLAYER_LOG`` may have rewritten it (a
    ``team`` beside a named player is his opponent) and a reader that needs
    the team's name before the read passes it already resolved; ``measures``
    because each reader decides what a bare ``threshold`` means before any
    name is resolved. The tenure (``scope.cuts.tenure``, "lebron stats as a
    starter for Miami" - his games for that team, written by
    ``subject._apply_own_team`` for the one reader whose words state it) is
    read here like every other cut.

    .. versionadded:: 4.4.0

    .. versionchanged:: 4.4.0
       Sets :attr:`Narrowed.window` from ``order``/``limit`` (step 3, C5) -
       see :func:`relation_window`. A no-op for a caller that reads its rows
       through :func:`association.query.player_games.rows_sql` directly
       (``game_log``, ``player_stat``, ``period_split``): that reader takes
       its own ``order``/``limit`` arguments and never consults ``.window``,
       so setting it here changes nothing for them. Only a reader built on
       :func:`association.query.player_games.aggregate_sql`,
       :func:`~association.query.player_games.grouped_sql` or
       :func:`~association.query.player_games.games_subquery` - all three
       already honor it - is affected, and only when the question's own
       slots set a window.

    .. versionchanged:: 4.4.0
       Takes ``team``.

    .. versionchanged:: 5.0.0
       Reads the typed :class:`~association.query.reading.Filtered`. A slot dict
       was taken until every caller passed ``reading``, the tests last,
       through their slot door (``tests/slots.py``, deleted in Phase 4,
       step 0).

    .. versionchanged:: 6.0.0
       Reads the cuts off ``scope.cuts`` (Phase 3, step 2), the tenure
       among them; ``team`` is no longer taken.
    """
    narrowed = _narrow_player_games(
        con,
        player,
        span,
        opponent=opponent,
        venue=scope.cuts.venue,
        # A list, as the slot always was: teammate_names reads a list or one
        # bare name, and a tuple would be neither - every teammate dropped.
        without=[c.player for c in scope.companions if c.absent],
        split=scope.split,
        game_n=scope.cuts.game_n,
        team=scope.cuts.tenure,
        conditions=[c for c in scope.companions if not c.absent],
    )
    if isinstance(narrowed, Unanswered):
        return narrowed
    narrow_measures(narrowed, measures)
    if date:
        start, end = eastern_day_utc_range(date)
        narrowed.extra.append("g.date >= ? AND g.date < ?")
        narrowed.extra_params += [start, end]
        narrowed.date = date
    if scope.cuts.situation:
        # Honored where it names the calendar or a conference/division, and
        # refused BY VALUE where it names anything else (an age, "since
        # returning") - see apply_situation.
        apply_situation(narrowed, scope.cuts.situation)
    apply_period(con, narrowed, scope)
    for in_period in period_lines(scope):
        refused = _apply_period_condition(con, narrowed, in_period)
        if refused is not None:
            return refused
    narrowed.window = relation_window(scope)
    return narrowed


#: A ``position`` cell's letter as :func:`league_games` narrows the relation by
#: it - the generic code plus every specific one it covers. ``players.position_abbr``
#: holds both the generic letter and the specific one (measured against the
#: warehouse: G 862, F 724, C 502, SG 254, PF 252, SF 247, PG 237), so "forwards"
#: reaches every forward on record and "shooting guards" only those listed as SG.
POSITION_CODES: dict[str, list[str]] = {"G": ["G", "PG", "SG", "GF"], "F": ["F", "PF", "SF", "GF"], "C": ["C"], "PG": ["PG"], "SG": ["SG"], "PF": ["PF"], "SF": ["SF"]}


"""``players.position_abbr`` values a question's position word reaches.

.. versionadded:: 4.4.0
"""


def league_games(con: duckdb.DuckDBPyConnection, span: ResolvedSpan, scope: Filtered, *, position: str | None) -> Narrowed | Unanswered:
    """Every player's games in ``span`` - the league-wide read a question with
    no player subject narrows the same way one player's games are: an
    opponent, a venue, a team's roster, lines on box-score columns and the
    calendar ``situation`` narrowing - plus one dimension a single player's
    games have no use for, a position.

    Built on :func:`association.query.player_games.league`, the "everyone
    at once" read one optional ``athlete_id`` filter narrows to one man,
    narrowed by the same clauses
    :func:`scoped_games` applies to a named player's - so a narrowing that
    reaches a player's games reaches this read too, without being taught to
    it separately. ``without``, ``split``, ``game_n`` and ``date`` are
    :func:`scoped_games`' own cells that this read has no subject for (whose
    teammate would "without" name? whose start would "split" count?) and are
    not narrowings here; a caller that wants a game-of-series number or a
    fixed date over the league still resolves a player first.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Reads the typed :class:`~association.query.reading.Filtered`. A slot dict
       was taken until every caller passed ``reading``, the tests last,
       through their slot door (``tests/slots.py``, deleted in Phase 4,
       step 0).
    """
    season_clause, season_params = span.clause("pgl.season")
    narrowed = league(season_clause, season_params, span.season_type)
    # The log LEFT JOINs players; a box score for an athlete missing there
    # would otherwise be counted under a NULL name and reported as a
    # nameless leader.
    narrowed.narrow("pgl.player_name IS NOT NULL")
    if scope.cuts.opponent and scope.cuts.opponent.strip():
        team = resolved_team(con, scope.cuts.opponent, season=span.season)
        if isinstance(team, Unanswered):
            return team
        narrowed.opponent = team
        narrowed.narrow("pgl.opponent_team_id = ?", team.id)
    if scope.who.team is not None:
        team = resolved_team(con, scope.who.team, season=span.season)
        if isinstance(team, Unanswered):
            return team
        narrowed.team = team
        narrowed.narrow("pgl.team_id = ?", team.id)
    if scope.cuts.venue:
        narrowed.venue = scope.cuts.venue
        narrowed.narrow("(g.home_team_id = pgl.team_id) = ?", scope.cuts.venue == "home")
    narrow_measures(narrowed, measure_filters(scope))
    season_n = scope.cuts.season_n
    if season_n is not None and season_n > 0:
        # Each player's Nth regular season, counted the way settle_ordinal_season
        # counts one player's: distinct regular seasons on the per-player season
        # table, in order. "Most points in 15th season played" (yardstick-v2
        # F099) is every player's own 15th season, not the 15th season on
        # record.
        narrowed.narrow(
            "pgl.season = (SELECT s.season FROM (SELECT athlete_id, season, ROW_NUMBER() OVER (PARTITION BY athlete_id ORDER BY season) AS n "
            "FROM (SELECT DISTINCT athlete_id, season FROM player_season_stats_deduped WHERE season_type = 2)) s WHERE s.athlete_id = pgl.athlete_id AND s.n = ?)",
            season_n,
        )
        narrowed.ordinal = season_n
    if scope.cuts.situation:
        apply_situation(narrowed, scope.cuts.situation)
    if position:
        codes = POSITION_CODES.get(position, [position])
        narrowed.narrow(f"pgl.athlete_id IN (SELECT athlete_id FROM players WHERE position_abbr IN ({', '.join('?' for _ in codes)}))", *codes)
    apply_period(con, narrowed, scope)
    for in_period in period_lines(scope):
        refused = _apply_period_condition(con, narrowed, in_period)
        if refused is not None:
            return refused
    return narrowed


def condition_player(
    con: duckdb.DuckDBPyConnection,
    scope: Filtered,
    needs: str,
    condition_scope: _Scope,
    *,
    team: Entity | None = None,
    measures: list[MeasureFilter] | None = None,
    opponent: Entity | None = None,
) -> tuple[Entity, Narrowed] | Unanswered:
    """The player a condition read is about, and his games in
    ``condition_scope`` under the question's row-level narrowings, read off
    ``scope``: for the readers that group a player's games by a condition
    (splits, a record above a threshold, a streak, with/without) and read
    them as a subquery (:func:`association.query.player_games.games_subquery`).

    ``condition_scope`` is :class:`~association.query.conditions._Scope`,
    kept because it reads "career ... in 2015" as 2015 where ``span_of``
    refuses the pair - the one place the two readers of a player's games
    disagree (ISSUES.md). ``team`` narrows to the games he played for that team. ``measures``
    is the lines a caller has already read off ``below``/``above`` with
    :func:`measure_filters` - built by the caller, before any name is
    resolved, the same way :func:`player_stat` does it - and defaults to none
    so a caller that does not pass any keeps reading every game in scope.
    ``opponent`` is the team the games are against when the caller has
    already resolved it - ``PLAYER_SPLITS`` does, so that a clarification
    about the team comes before one about the player - and is handed to
    :func:`scoped_games` as it is, never resolved a second time; left None,
    the scope's own ``opponent`` is read.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Reads the typed :class:`~association.query.reading.Filtered`, and takes
       ``opponent``: the filters hold names, so a team already resolved goes
       beside it. The template's own ``_Scope`` is ``condition_scope``.
    """
    # The name is narrowed over the games the condition covers: the one
    # season it settled on, or the career (the range and the type the
    # scope's own).
    subject = scoped_player(con, scope, needs, table="player_game_log", available=BOX_SCORES, span=replace(scope.span, career=condition_scope.season is None, season=condition_scope.season))
    if isinstance(subject, Unanswered):
        return subject
    player, span = subject
    narrowed = scoped_games(con, player, span, scope, opponent=scope.cuts.opponent if opponent is None else opponent, measures=measures or [])
    if isinstance(narrowed, Unanswered):
        return narrowed
    if team is not None:
        narrowed.narrow("pgl.team_id = ?", team.id)
    return player, whole_span(narrowed)


def whole_span[NarrowedT: (Narrowed, TeamNarrowed)](narrowed: NarrowedT) -> NarrowedT:
    """``narrowed`` with no window: the condition skeletons - a split, a
    record, a run - are read over every game in the span, which is why each
    of them excludes the ``window`` cell in :data:`RELATION_SCOPING_EXCLUDED`
    (DeclineReason.SPLITS_RECENT_GAMES). A bare count
    is filler on those questions (``limit: 1`` beside "76ers
    record when Maxey scores 20+"), and :func:`relation_window` reads a bare
    count as the newest N for the readers that DO honor a window - so the
    skeleton that does not says so here, once, instead of the filler cutting a
    63-game record to one game.

    .. versionadded:: 4.4.0
    """
    narrowed.window = None
    return narrowed


def _teammates_among(con: duckdb.DuckDBPyConnection, candidates: list[Entity], player: Entity, span: ResolvedSpan) -> list[Entity]:
    """The candidates who were on one of ``player``'s teams in a season of
    ``span``. Elimination, never preference - the same move as
    entities.narrow_to_available: it drops the Currys who cannot be the one a
    Warriors question means, and still asks between two who both can."""
    if not candidates:
        return []
    clause, params = span.clause("season")
    # Both season types read as one ("last 10 games", decision D14) are the
    # two types' own rows: `season_type = 0` matched none, and every
    # teammate read as nobody's.
    type_clause, type_params = season_type_clause("season_type", span.season_type)
    placeholders = ", ".join("?" for _ in candidates)
    rows = con.execute(
        f"SELECT DISTINCT m.athlete_id FROM player_box_stats m "
        f"JOIN (SELECT DISTINCT season, team_id FROM player_box_stats WHERE athlete_id = ? AND {type_clause} AND {clause}) s ON s.season = m.season AND s.team_id = m.team_id "
        f"WHERE m.athlete_id IN ({placeholders})",
        [player.id, *type_params, *params, *(c.id for c in candidates)],
    ).fetchall()
    have = {str(row[0]) for row in rows}
    return [c for c in candidates if c.id in have]


def _condition_from_slot(con: duckdb.DuckDBPyConnection, entry: Companion, player: Entity, span: ResolvedSpan, opponent: Entity | None = None) -> Condition | Unanswered:
    """One companion - a :class:`~association.query.reading.Companion`:
    a player, his side (``"own"`` or ``"opponent"``), a predicate, and the
    line a ``reached`` one names - as a
    :class:`~association.query.player_games.Condition` with its player
    resolved: a teammate the way "without" resolves one (narrowed to who
    shared a team with the subject), an opponent-side player against the
    box scores in the span. A predicate or stat this does not read refuses
    rather than narrowing to nothing.

    An absence the question wrote on the subject's own side ("without X")
    whose player was never his teammate in the span is read on the OTHER
    side where the games are narrowed to an ``opponent`` he played for then
    - "vs lakers without lebron" (Jeff's call: the side is
    settled where the name is resolved, since the parser does not read the
    warehouse) - bounded to his time on that team and said as "without X
    on the other side"; else the teammate refusal stands, naming both.

    .. versionadded:: 5.0.0

    .. versionchanged:: 5.0.0
       Takes ``opponent``, for an absence read on the other side.
    """
    if not entry.player.strip():
        raise Unsupported(f"a condition needs a player, got {entry!r}")
    side, predicate = entry.side, entry.predicate
    if side not in ("own", "opponent") or predicate not in CONDITION_PREDICATES:
        raise Unsupported(f"no condition reads side {side!r} with predicate {predicate!r}")
    if side == "own":
        found = _resolved_teammate(con, entry.player, player, span)
        if predicate == "absent" and isinstance(found, Entity):
            return _absence_condition(con, found, player, span, opponent)
    else:
        found = resolved_player(con, entry.player, "player", available=BOX_SCORES, season=span.season, through=career_end(span.season))
    if isinstance(found, Unanswered):
        return found
    line: tuple[str, str, int, str] | None = None
    if predicate == "reached":
        stat = entry.line.measure if entry.line is not None else None
        threshold = int(entry.line.value) if entry.line is not None else None
        column = THRESHOLD_STAT_COLUMNS.get(stat) if stat is not None else None
        if column is None or stat is None or threshold is None or threshold < 1:
            raise Unsupported(f"a reached condition needs a known stat and a positive threshold, got {stat!r}/{threshold!r}")
        line = (column, ">=", threshold, f"{threshold}+ {STAT_LABELS.get(stat, stat)}s")
    # An own-side absence was returned above, so no tenure is carried here.
    return Condition(found, side, predicate, line)


def _absence_condition(con: duckdb.DuckDBPyConnection, mate: Entity, player: Entity, span: ResolvedSpan, opponent: Entity | None) -> Condition:
    """ "Without X" as the relation reads it: the games in which X had no
    row for the subject's team, or - where X was never his teammate in the
    span and the games are narrowed to an ``opponent`` X played for then -
    for the OTHER side ("vs lakers without lebron"), said as "without X on
    the other side". Over one season that is every such game, traded or
    injured alike (decision D15); over a career it is bounded to X's time on
    the team (:func:`_absence_tenure`), whose empty answer says he was never
    the subject's teammate. Settled here, where the name is resolved, since
    the parser reads no roster (Jeff's call).

    .. versionadded:: 5.0.0

    .. versionchanged:: 7.0.0
       No tenure bounds the absence over one season (D15).
    """
    if opponent is not None and not _teammates_among(con, [mate], player, span) and _on_team_in_span(con, mate, opponent, span):
        return Condition(mate, "opponent", "absent", None, _absence_tenure(con, mate, span, side="opponent"))
    together = span.season is None or bool(_teammates_among(con, [mate], player, replace(span, season=None, since=None, until=None)))
    return Condition(mate, "own", "absent", None, _absence_tenure(con, mate, span, side="own", ever_together=together))


def _absence_tenure(con: duckdb.DuckDBPyConnection, mate: Entity, span: ResolvedSpan, *, side: str, ever_together: bool = True) -> tuple[str, list[Any]] | None:
    """The tenure an absence is bounded to: none over one season, where
    "without Y" is every game of the subject's in which Y had no row for the
    team - the games after Y was traded away above all, which are the ones
    the question is about (decision D15: "jeremiah fears stats without
    dejounte murray" answered 63 games where 68 were asked); Y's time on the
    team over a career, where the decades before he arrived are not games
    played "without" him (D15 keeps the cut there, said in the answer). A
    teammate who never shared a team with the subject in any season keeps
    the cut over one season too, so the answer says he was never his
    teammate rather than calling every game "without" a stranger ("rui
    hachimura stats without reeves" read as Antonio Reeves)."""
    if span.season is not None and ever_together:
        return None
    return _tenure_clause(con, mate, span.season, side=side)


def _on_team_in_span(con: duckdb.DuckDBPyConnection, mate: Entity, team: Entity, span: ResolvedSpan) -> bool:
    """Whether ``mate`` has a box score for ``team`` in a season of ``span``."""
    clause, params = span.clause("season")
    type_clause, type_params = season_type_clause("season_type", span.season_type)
    row = con.execute(f"SELECT 1 FROM player_box_stats WHERE athlete_id = ? AND team_id = ? AND {type_clause} AND {clause} LIMIT 1", [mate.id, team.id, *type_params, *params]).fetchone()
    return row is not None


def _resolved_teammate(con: duckdb.DuckDBPyConnection, text: Any, player: Entity, span: ResolvedSpan) -> Entity | Unanswered:
    """The teammate a "without" names. "Without curry" is six players by name
    and at most two by roster, so an ambiguous name is narrowed to the ones who
    shared a team with ``player`` in the span before anything is asked.

    .. versionchanged:: 4.4.0
       A near spelling (:func:`~association.query.entities.suggest_players`)
       with exactly one candidate is taken rather than asked about, the same
       default :func:`~association.query.entities.resolve_player` already
       applies to a bare surname - visible in the answer
       (:func:`~association.query.entities.note_typo_reading`) and correctable
       (the note names the exact text that was typed). yardstick-v2 F157:
       "de'aaron fox vs magic last five games without wembyanama" used to
       refuse "did you mean Victor Wembanyama?" over a typo the question's own
       key note says resolves cleanly - the true reason the question falls
       short is a game count, not a name that failed to resolve.

    .. versionchanged:: 5.0.0
       The near spelling is read by :func:`~association.query.entities.resolve_player`
       itself (:func:`~association.query.entities.read_near_spelling`), as for
       every other name slot, and a surname back-off with one survivor ("Jemel
       Embiid") asks here as it does everywhere else rather than being taken.
    """
    if not isinstance(text, str) or not text.strip():
        raise Unsupported("'without' names nobody")
    resolved = resolve_player(con, text)
    if isinstance(resolved, Ambiguous):
        # Every match, not find_players' first page of ten: "without williams"
        # is 62 players by name, and a teammate who sorted past the tenth was
        # reported as nobody's teammate at all.
        candidates = find_players(players_of(con), text, limit=None)
        shared = _teammates_among(con, candidates, player, span)
        if len(shared) > 1:
            # Each of them shared his team in the span, so none is counted away.
            return clarify(text, [c.name for c in shared], active=len(shared))
        if not shared:
            return Refusal(
                kind="not_a_teammate", facts={"asked": text, "player": player.name, "during": span.during()}, shown={"unmatched": text, "candidates": [c.name for c in candidates]}, under=()
            )
        resolved = shared[0]
    if not isinstance(resolved, Entity):
        # Nothing by that name and no single near spelling (resolve_player
        # already reads one): a suggestion, or a refusal.
        found = resolved_player(con, text, available=BOX_SCORES)
        if isinstance(found, Unanswered):
            return found
        resolved = found
    if resolved.id == player.id:
        raise Unsupported(f"{player.name} cannot play without himself")
    return resolved


def no_narrowed_games(con: duckdb.DuckDBPyConnection, player: Entity, span: ResolvedSpan, narrowed: Narrowed, *, rebuilt: bool = False) -> Refusal:
    """Why a narrowed question found no games, naming the fact that is really
    missing - his games in that span, the teammate, the match, or an empty box
    score. They are different causes, and "X has no games" said of a player
    who simply never met that opponent - or whose games are every one of
    them there, with ESPN's box score served empty - sends the reader to look
    in the wrong place. Returned as the typed
    :class:`~association.query.result.Refusal` with the facts its sentence
    is made of; the sayer words it (``compose.say.refusal_phrase``), and a
    caller that answers with it says what the page shows beside it
    (``shown``).

    ``rebuilt`` has to match whatever the caller's own query decides a
    played game: with it, a game reconstructed from play-by-play already counts
    as recorded, so what is left over here is genuinely unrecorded, not merely
    unread. Passing the wrong value would either call a rebuilt game "empty" or
    call a truly empty one "recorded".

    .. versionchanged:: 5.0.0
       Returns the cause and its facts, not the sentence.
    """
    if narrowed.date:
        # One named day: the rest of his career is not the fact that is missing.
        return Refusal(kind="no_game_on_date", facts={"player": player.name, "kind": span.kind, "date": narrowed.date, "narrowing": narrowed.filters(dated=False)}, shown={})
    where, params = narrowed.clauses(narrowed=False, rebuilt=rebuilt)
    total, first, last = con.execute(f"SELECT COUNT(*), MIN(pgl.season), MAX(pgl.season) {_PLAYER_GAMES} WHERE {where}", params).fetchone() or (0, None, None)
    if not total:
        return _no_narrowed_games_in_span(con, player, span, narrowed, rebuilt=rebuilt)
    during = span.during(first, last)
    # One at a time: with two teammates named, the fact that is missing is
    # which of them never shared a team with him, and saying "one of them did
    # not" sends the reader to look in the wrong place.
    for mate, (tenure, tenure_params) in zip(narrowed.without, narrowed.tenure, strict=True):
        together = con.execute(f"SELECT COUNT(*) {_PLAYER_GAMES} WHERE {where} AND {tenure}", [*params, *tenure_params]).fetchone()
        if not together or not together[0]:
            return Refusal(kind="not_teammates_then", facts={"teammate": mate.name, "player": player.name, "games": total, "during": during}, shown={})
    return Refusal(kind="none_matched", facts={"player": player.name, "games": total, "during": during, "narrowing": narrowed.filters()}, shown={})


def _no_narrowed_games_in_span(con: duckdb.DuckDBPyConnection, player: Entity, span: ResolvedSpan, narrowed: Narrowed, *, rebuilt: bool) -> Refusal:
    """:func:`no_narrowed_games` where the span itself holds none of his
    games with a box score: every one of them served empty, no box score
    at all in a career, or none in the season - redirected to his own
    range where the season was never named."""
    # Before saying his games do not exist, check whether they do and ESPN
    # simply served no box score for them - the mirror-image bug AGENTS.md
    # records, in its own shape: a refusal that is confident and names the
    # wrong missing fact (the season, rather than the box scores). Every
    # Chicago and New Orleans game from 2013 to 2018 is one of these, and a
    # player whose games in the span are entirely such games has none that
    # pass the guard above - which alone reads as "he has no games at all".
    empty_where, empty_params = narrowed.clauses(narrowed=False, recorded=False, rebuilt=rebuilt)
    empty_total, empty_first, empty_last = con.execute(f"SELECT COUNT(*), MIN(pgl.season), MAX(pgl.season) {_PLAYER_GAMES} WHERE {empty_where}", empty_params).fetchone() or (0, None, None)
    if empty_total:
        return Refusal(kind="box_scores_empty", facts={"player": player.name, "games": empty_total, "during": span.during(empty_first, empty_last)}, shown={})
    if span.career:
        return Refusal(kind="no_box_scores", facts={"player": player.name, "kind": span.kind, "first": span.first}, shown={})
    redirect = None
    if span.defaulted:
        # The season was never named - the question asked about "now", and
        # a retired player's "now" is empty. Redirecting to his own range
        # beats a refusal that reads as though his career itself were the
        # gap (issue #18); a season the question named keeps this plain,
        # because that refusal is correct as given.
        # At call time: the relation imports this module.
        from association.query.season_line import season_redirect

        found = season_redirect(con, player.id, span.season_type, "player_game_log")
        redirect = list(found) if found is not None else None
    return Refusal(kind="no_games_in_span", facts={"player": player.name, "span": span.during()[len("in the ") :], "kind": span.kind, "redirect": redirect}, shown={})


def narrowing_emptied(con: duckdb.DuckDBPyConnection, narrowed: Narrowed) -> bool:
    """Whether the player has games under the base clauses alone - so it is
    the narrowing (an opponent, a condition, a venue) that left none, and
    the answer should name it rather than the span.

    .. versionadded:: 6.0.0
    """
    if not narrowed.extra:
        return False
    where, params = narrowed.clauses(narrowed=False, rebuilt=box_source(con).rebuilt)
    row = con.execute(f"SELECT COUNT(*) {_PLAYER_GAMES} WHERE {where}", params).fetchone()
    return bool(row and row[0])


def log_total(con: duckdb.DuckDBPyConnection, narrowed: Narrowed, *, rebuilt: bool) -> tuple[int, int | None, int | None]:
    """How many of the player's games match every narrowing the question
    carries, before the window (``order``/``limit``) cuts them to the rows
    listed - what a game log's heading says "of how many" with (F149) - and
    the first and last season among them, which the heading's parenthesis
    names - never the seasons of the rows SHOWN, which would read a career's
    "last 10 of 118 games" as "(2026 regular season)" (ISSUES.md #285/#277). Read
    through :func:`~association.query.player_games.aggregate_sql` over
    :func:`whole_span` (which takes the window off ``narrowed`` itself),
    never a hand-written ``COUNT(*)``.

    .. versionadded:: 6.0.0
    """
    sql, params = aggregate_sql(whole_span(narrowed), ["COUNT(*)", "MIN(pgl.season)", "MAX(pgl.season)"], rebuilt=rebuilt)
    row = con.execute(sql, params).fetchone()
    if not row or row[0] is None:
        return 0, None, None
    return int(row[0]), (int(row[1]) if row[1] is not None else None), (int(row[2]) if row[2] is not None else None)


def narrowed_cells(narrowed: Narrowed) -> tuple[Cell, ...]:
    """The cells a player's narrowed games carry beyond the opponent, the
    venue and the teammates absent, as the Result's typed values
    (:data:`~association.query.result.Cell`): his role, each line the games
    were kept past, and the game of a series - in the order the answer
    names them.

    .. versionadded:: 5.0.0
    """
    cells: list[Cell] = []
    if narrowed.started is not None:
        cells.append(Role(started=narrowed.started))
    cells += [Line(column=column, op=op, value=value, label=label) for column, op, value, label in narrowed.lines]
    if narrowed.series_game is not None:
        cells.append(GameOfSeries(n=narrowed.series_game))
    return tuple(cells)


def without_notes(con: duckdb.DuckDBPyConnection, player: Entity, span: ResolvedSpan, narrowed: Narrowed) -> list[Note]:
    """What "without" was taken to mean: over one season, where no tenure
    bounds the absence (decision D15), a game he did not play for the team,
    traded or injured alike - and, for a teammate with no game for the
    subject's team in the season at all, that every game is without him,
    since a table of every game headed "without LeBron James" hides that
    LeBron James was elsewhere; over a career, a game he did not play while
    on the same team.

    .. versionadded:: 7.0.0
    """
    absences = [c for c in narrowed.conditions if c.side == "own" and c.predicate == "absent"]
    if any(c.tenure is not None for c in absences):
        return [Note("definition", {"term": "without", "names": [c.player.name for c in absences]})]
    elsewhere = [c.player for c in absences if not _teammates_among(con, [c.player], player, span)]
    notes = [Note("definition", {"term": "without", "names": [c.player.name for c in absences], "what": "season"})]
    if elsewhere:
        notes.append(Note("definition", {"term": "without", "names": [mate.name for mate in elsewhere], "what": "elsewhere", "whose": player.name}))
    return notes


def box_score_notes_read(
    con: duckdb.DuckDBPyConnection, player: Entity, span: ResolvedSpan, narrowed: Narrowed, *, career_note: bool = True, rebuilt: bool = False, rebuilt_shown: int = 0
) -> list[Note]:
    """What a box-score answer has to say about itself, read as kinds and
    facts (:class:`~association.query.notes.Note`): what "without" was
    taken to mean, the figures that were rebuilt rather than fetched, the
    empty lines left out, and - unless ``career_note`` is off, as it is for
    one dated game - a career older than the box scores. The sayer phrases
    each (``compose.say.note_phrase``); the compiler's own sentence phrases
    them through it too (``compose.core._box_notes``).

    .. versionadded:: 5.0.0
    """
    notes: list[Note] = []
    if narrowed.without:
        notes += without_notes(con, player, span, narrowed)
    if rebuilt_shown:
        # Said outright, because these numbers did not come from ESPN. Per game
        # they are close (see REBUILT_STATS) but they are not the box score, and
        # a reader quoting one should know which kind of number they hold.
        notes.append(Note("lines_rebuilt", {"games": rebuilt_shown, "what": "shown"}))
    where, params = narrowed.clauses(recorded=False, rebuilt=rebuilt)
    row = con.execute(f"SELECT COUNT(*) {_PLAYER_GAMES} WHERE {where}", params).fetchone()
    empty = row[0] if row else 0
    if empty:
        notes.append(Note("games_unseen", {"games": empty, "why": "empty_box_score"}))
    if span.career and career_note and span.since is None:
        row = con.execute(
            "SELECT MIN(season) FROM player_season_stats_deduped WHERE athlete_id = ? AND season_type = ? AND gamesPlayed > 0",
            [player.id, span.season_type],
        ).fetchone()
        earliest = row[0] if row else None
        if earliest is not None and earliest < span.first:
            notes.append(Note("floor", {"table": "box_scores", "first": span.first, "earliest": earliest}))
    return notes


def condition_scope(span: Span, tables: tuple[str, ...]) -> _Scope:
    """The games a question covers, for a condition read - the typed
    :class:`~association.query.reading.Span` as the condition module's own
    scope. No season means the current one - except for a career, where it
    means every season on record, which is what the word asked for. A
    season the question named beats "career": the reader keeps a named
    year alongside it, and "career ... in 2015" is asking about 2015. A
    range is every season from its first on - and, like ``span_of``'s own
    pairing of the two, conflicts with a named season rather than silently
    picking one: a caller that let both through here would resolve "since
    2022 and 2020 at once" as though only "since 2022" had been asked,
    with nothing saying the named year was dropped. The range's ``until``
    is not read here: the condition reads are the streak's, the splits'
    and the pair's, which read ``since`` alone.

    .. versionchanged:: 4.3.0
       Honors ``since``.

    .. versionchanged:: 4.4.0
       Refuses ``since`` alongside a named ``season`` instead of silently
       preferring ``since``.

    .. versionchanged:: 6.0.0
       Takes the typed :class:`~association.query.reading.Span` (Phase 3, step 2).
    """
    kind = span.season_type or 2
    season, since = span.season, span.since
    if since:
        if season:
            raise ShapeDeclined("span_and_season", {"season": season, "since": since}, f"since {since} and the {season} season at once")
        scope = _game_scope(None, kind, tables)
        return _Scope(None, kind, max(since, scope.first), scope.phantoms)
    if season is not None:
        return _game_scope(season, kind, tables)
    return _game_scope(None if span.career else current_season(), kind, tables)


def where_in(scope: _Scope) -> str:
    """ "in the 2026 regular season", or "in any regular season on record" for a span with nothing in it."""
    return f"in the {scope.label()}" if scope.season is not None else f"in any {scope.kind} on record ({scope.first} onward)"


def no_games(con: duckdb.DuckDBPyConnection, player: Entity, scope: _Scope, team: Entity | None) -> Refusal:
    """Nothing to report for a player, naming which fact is missing.

    Not the season: check_coverage has already refused any season the tables
    do not reach. What is left is the player - either no box score lists him
    at all, or the ones that do are all games he sat out, and those are
    different causes (``no_player_games``, ``listed_not_played``).

    .. versionchanged:: 5.0.0
       Returns the cause and its facts, not the sentence.
    """
    params: dict[str, Any] = {**scope.params(), "player": player.id}
    where = f"pbs.athlete_id = $player AND {scope.where('pbs')}"
    if team is not None:
        where += " AND pbs.team_id = $team"
        params["team"] = team.id
    listed = con.execute(f"SELECT COUNT(*) FROM player_box_stats pbs WHERE {where}", params).fetchone()
    count = int(listed[0]) if listed else 0
    facts = {"player": player.name, "team": team.name if team else None, "where": where_in(scope), "games": count}
    shown = {"player": player.name, "team": team.name if team else None, "span": scope.label(), "games": 0}
    return Refusal(kind="listed_not_played" if count else "no_player_games", facts=facts, shown=shown, under=())


def rebuilt_in_scope(con: duckdb.DuckDBPyConnection, season: int | None, season_type: int, athlete_id: str | None) -> int:
    """How many games in scope carry a line rebuilt from play-by-play.

    It explains a refusal rather than answers: when the stat asked for is
    outside :data:`REBUILT_STATS`, "no games with a box score" is true of the
    fetched lines and hides that rebuilt ones exist and were withheld on
    purpose. Saying which is the difference between a gap and a decision.

    .. versionadded:: 2.2.0

    .. versionchanged:: 5.0.0
       Public (``_rebuilt_in_scope`` until then): the count's and the
       high's readers take it.
    """
    if not box_source(con).rebuilt:
        return 0
    scope, params = scope_without_guard("l", season, season_type)
    where = f"{scope} AND l.reconstructed"
    if athlete_id is not None:
        where += " AND l.athlete_id = ?"
        params.append(athlete_id)
    row = con.execute(f"SELECT COUNT(*) FROM player_game_log l WHERE {where}", params).fetchone()
    return int(row[0]) if row else 0


def empty_box_scores(con: duckdb.DuckDBPyConnection, season: int | None, season_type: int, athlete_id: str | None, *, covered_by_rebuild: bool = False) -> tuple[int, int | None, int | None]:
    """Games in scope whose box score is empty: (count, first season, last season).

    Every game from 2012-13 through 2017-18 has a box score, but 161-166 a
    season hold nothing - every player's minutes NULL and every stat 0. LeBron
    James's 76 games of 2012-13 are all present and sum to 1,835 points, against
    the season table's 2,036. A zero can hide a real maximum or a real count but
    never invent one, so the answer stands - and says how many games it could
    not see. For a player, only the empty games he actually played in count.

    ``covered_by_rebuild`` excludes the games the answer DID see through
    ``player_box_stats_filled``. Without it the same answer both reads a game
    and reports it as unseen - "his highest was 43, rebuilt from play-by-play"
    beside "68 of his games have an empty box score, so a bigger game may be
    missing", where those 68 are the very games the 43 came from.

    .. versionchanged:: 5.0.0
       Public (``_empty_box_scores`` until then): the count's and the
       high's readers take it.
    """
    if covered_by_rebuild and box_source(con).rebuilt:
        # What is still unseen: no minutes AND no rebuild to stand in for them.
        scope, params = scope_without_guard("l", season, season_type)
        if athlete_id is None:
            sql = (
                f"SELECT COUNT(*), MIN(season), MAX(season) FROM (SELECT l.season FROM player_game_log l WHERE {scope} "
                "GROUP BY l.event_id, l.season HAVING MAX(l.minutes) IS NULL AND NOT BOOL_OR(COALESCE(l.reconstructed, FALSE)))"
            )
        else:
            sql = (
                f"SELECT COUNT(*), MIN(l.season), MAX(l.season) FROM player_game_log l WHERE {scope} "
                "AND l.minutes IS NULL AND NOT COALESCE(l.reconstructed, FALSE) AND NOT COALESCE(l.did_not_play, FALSE) AND l.athlete_id = ?"
            )
            params.append(athlete_id)
        row = con.execute(sql, params).fetchone()
        return (int(row[0]), row[1], row[2]) if row else (0, None, None)

    scope, params = scope_without_guard("b", season, season_type)
    empty = f"SELECT b.event_id, b.season FROM player_box_stats b WHERE {scope} GROUP BY b.event_id, b.season HAVING MAX(b.minutes) IS NULL"
    if athlete_id is None:
        sql = f"SELECT COUNT(*), MIN(season), MAX(season) FROM ({empty})"
    else:
        sql = (
            f"SELECT COUNT(*), MIN(r.season), MAX(r.season) FROM player_box_stats r JOIN ({empty}) e ON e.event_id = r.event_id AND e.season = r.season "
            "WHERE r.athlete_id = ? AND NOT COALESCE(r.did_not_play, FALSE)"
        )
        params.append(athlete_id)
    row = con.execute(sql, params).fetchone()
    return (int(row[0]), row[1], row[2]) if row else (0, None, None)


def _team_slot_played_for(con: duckdb.DuckDBPyConnection, player: Entity, team: Entity) -> bool:
    """Whether ``player`` has ever suited up for ``team``, anywhere in
    ``player_game_log``.

    The only question this answers is "is this team the SUBJECT, not the
    opponent" - so it deliberately looks across his whole career rather than
    the season in scope: a team slot naming a season he was not on it is still
    not an opponent, and treating it as one would file a real former team as
    though the two had played each other.
    """
    row = con.execute("SELECT 1 FROM player_game_log WHERE athlete_id = ? AND team_id = ? LIMIT 1", [player.id, team.id]).fetchone()
    return row is not None


def team_slot_for_player(con: duckdb.DuckDBPyConnection, player: Entity, team_text: str, *, season: int | None, opponent: Any) -> Any:
    """What a ``team`` slot means once ``player`` is named - see #147.

    An ``opponent`` already named wins outright: a model's ``team`` beside it
    is measured noise beside an already-correct opponent, not a second fact
    to reconcile - measured on the filed corpus
    rows, it is Payton Pritchard's invented "Phoenix Suns" beside a correct
    "Philadelphia 76ers" opponent, and Kobe Bryant's own "Los Angeles Lakers"
    beside a correct "Houston Rockets" one. Comparing the two and refusing
    when they disagreed was tried first and was wrong for exactly this shape:
    "Phoenix Suns" is a real, resolvable team, so a naive conflict check
    refused Pritchard's question rather than answering it.

    With no ``opponent`` already named, his own team narrows nothing, so it is
    dropped; a different, real team is his opponent (the Curry shape); and a
    name nothing resolves to - a model inventing a team the way it
    sometimes invents a player, see AGENTS.md's "A model invents names, and
    an invented name resolves" -
    or an ambiguous one, is dropped rather than guessed at or asked about: the
    player, not the team, is what the question is about.

    .. versionadded:: 5.0.0
       Public, beside the relation's other settlers
       (``templates.games._team_slot_for_player`` until then).
    """
    if isinstance(opponent, str) and opponent.strip():
        return opponent
    match resolve_team(con, team_text, season):
        case Entity() as team:
            return opponent if _team_slot_played_for(con, player, team) else team_text
        case _:
            # NotFound or Ambiguous - dropped either way, since `opponent` is
            # not set here for either to fill.
            return opponent
