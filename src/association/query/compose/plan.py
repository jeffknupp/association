"""The planner: a :class:`~association.query.reading.Reading` as the
compiler's point on a relation - :class:`~association.query.compose.core.Query`
over the player-games relation (one player, or everyone), or
:class:`~association.query.compose.team.TeamQuery` over the team-games
relation. A copy, not a decision: nothing here reads the question, and a
field the Reading did not settle is not settled here either.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from association.query.answer import Reply
from association.query.conditions import condition_needs_player_refusal
from association.query.coverage import coverage_refusal
from association.query.measures import stat_measure
from association.query.player_relation import RELATION_SCOPING_EXCLUDED, relation_scoping
from association.query.point import TEAM_SEASON_POINTS
from association.query.reading import CHART_INTENTS, Cause, Reading, Scope, _career_scope, unhonored_scoping
from association.query.result import Refusal
from association.query.team_relation import team_relation_scoping

from .core import Query, Refused, Unsupported, _check_relation_scoping
from .netpoints import NetPointsQuery
from .rankings import leaderboard_reads
from .say import say, say_refusal
from .seasons import player_compare_reads, player_history_reads, player_line_reads
from .shots import ShotQuery
from .team import TeamQuery
from .team_stats import TeamSeasonQuery, team_season_declines

WITH_WITHOUT_STATED: frozenset[str] = frozenset({"span", "without", "opponent", "conditions"})
"""The scoping ``with_without``'s words state - a career, the teammates
divided by, one opponent (both rows narrow together, #163) and a
companion's role - the retired template's own declaration; any other
narrowing is declined by name (:func:`_shape_declines`), and the
reader's words state the same (:data:`STATED_SCOPING`).

In the planner, which declines by it, since the last adapter
(``compose.adapt``, where it lived) was deleted.

.. versionadded:: 5.0.0
"""


def _stated_by_words() -> dict[str, frozenset[str]]:
    """The retired words -> the scoping they state, by the name of the
    template whose sentence they are: :data:`STATED_SCOPING` keyed by the
    words, which :data:`SHAPE_WORDS` maps each shape to. A compiled
    intent's sayer answers in its retired template's sentence, which names
    the narrowings that template honored and no other: asked a point
    narrowed beyond them (an opponent on a single-game high, a condition on
    a history), its reader steps aside (``None``) and the compiler's own
    sentence, which states every narrowing the relation applied, answers.
    A narrowing the relation cannot honor at all is the planner's refusal
    (:func:`plan`), before any reader runs; until 5.0.0 these lists lived
    in ``HONORED_SCOPING`` under the retired templates' names."""
    return {
        # game_log and player_stat retired stating the relation's whole set, and
        # `season_type_unstated`: read over both season types merged by date for
        # a log (compose.logs, which takes this set since Phase 2's first
        # slice), and from box scores as one combined read for an average
        # (`_player_stat_reads_box_scores`, `player_relation_season_type`).
        "game_log": relation_scoping("game_log", "season_type_unstated"),
        "player_stat": relation_scoping("player_stat", "season_type_unstated"),
        # player_splits' words: the relation's set less a date and a window
        # (RELATION_SCOPING_EXCLUDED: one game has nothing to split).
        "player_splits": relation_scoping("player_splits"),
        # The retired templates' words, as they stated their narrowings when they
        # retired (ROADMAP plan item 6, step (d), part 4).
        "record_when": relation_scoping("record_when"),
        "player_history": frozenset({"span"}),
        # leaderboard's words: a career pool, and a season total or a unit it
        # refuses by name (the template's own HONORED_SCOPING when it retired).
        "leaderboard": frozenset({"span", "rate"}),
        # period_split's words: the relation's set less a career and a since/until
        # range (RELATION_SCOPING_EXCLUDED: the accuracy caveat is per season),
        # which its point refuses outright (compose.adapt._adapt_period_split).
        # A period CONDITION (a quarter conditioning which games count, beside
        # the quarter measured - "first quarter points in games he made a
        # fourth-quarter three") is not among those words either
        # (RELATION_SCOPING_EXCLUDED): the presenter steps aside and the
        # compiler's sentence, which names both, answers.
        "period_split": relation_scoping("period_split"),
        # player_compare's words state no narrowing at all; its point refuses
        # one outright (query/point.py._compare_point), as the retired scope check did.
        "player_compare": frozenset(),
        # streak's words: the relation's set less one date, a window and a
        # quarter (RELATION_SCOPING_EXCLUDED: a run is a run of whole games over
        # every game in the span), which the planner refuses outright
        # (compose.plan._shape_declines), and the cells only a named player's
        # games settle on a team's or the league's run, by name, there too.
        "streak": relation_scoping("streak"),
        # player_matchup's words: the relation's set less a third team, a window,
        # an ordinal season and a quarter (RELATION_SCOPING_EXCLUDED), which the
        # planner refuses outright (compose.plan._shape_declines).
        "player_matchup": relation_scoping("player_matchup"),
        # with_without's words: a career, the teammates, one opponent and a
        # companion's role, the template's own declaration when it retired.
        "with_without": WITH_WITHOUT_STATED,
        "single_game_high": frozenset({"span"}),
        # A count is already a line on a column; `below` is the same line the
        # other way ("games with under 14 fta"), and a phrase carrying the count's
        # own number IS the count, misread - compose.counts reads it so.
        # `season_type_unstated` is stated the way `scoped_player` reads it -
        # one combined `season_type IN (2, 3)` read (player_relation_season_type).
        "threshold_count": frozenset({"span", "below", "above", "season_n", "season_type_unstated"}),
        # The team-season readers' words (compose.team_stats), as the retired
        # templates honored them: one team's line and its power index state no
        # narrowing at all; a ranking states the team relation's cells less the
        # ones TEAM_RELATION_SCOPING_EXCLUDED["team_leaderboard"] gives a reason
        # for - and refuses, by name, a venue or a span its metric has no
        # reading over (compose.team_stats).
        "team_stat": frozenset(),
        "team_outlook": frozenset(),
        "team_leaderboard": team_relation_scoping("team_leaderboard"),
        # The shapes Phase 2's slice (iv) ported from templates (PORTED_SHAPES):
        # each retired template's HONORED_SCOPING row, moved with it. Every
        # meeting in a season, on a date, at a venue, or over a since-bounded or
        # whole-career span; `order` and `game_n` pick out a subset of the tally
        # (TEAM_RELATION_SCOPING_EXCLUDED says why).
        "head_to_head": team_relation_scoping("head_to_head"),
        # A team's quarter or half: the team relation's whole set (its games
        # from `team_games`, its team and span from `scoped_team`), the quarter
        # or half the relation's own narrowing.
        "team_quarter_points": team_relation_scoping("team_quarter_points"),
        # The league's ranking by a quarter or half reads one season's pool,
        # narrowed by an opponent or a venue; a range of seasons and one date are
        # refused (the accuracy is measured per season, and one game ranks
        # nothing per game).
        "period_leaderboard": frozenset({"period", "half", "opponent", "venue"}),
        # A team's record: the team relation's cells less a date and a window
        # (TEAM_RELATION_SCOPING_EXCLUDED says why), a split by month, and both
        # season types together ("including the playoffs").
        "team_record": team_relation_scoping("team_record", "split", "season_type_unstated"),
        # The NetPoints relation's two (Phase 2, slice (v)), as the retired
        # templates honored them: a first or last game, one game's NetPoints or
        # its fingerprint drawn from the per-game tables; and a calendar date on
        # a fingerprint, honored by refusing it in the reader's own words - the
        # loader picks a player's first or last game, which is a different
        # question from a date (``compose.netpoints``).
        "player_netpoints": frozenset({"order"}),
        "fingerprint": frozenset({"order", "date"}),
        # The shot relation's readers (compose.shots, Phase 2, step 5): every
        # cell of the player relation they take their games from, less a quarter
        # and a half (RELATION_SCOPING_EXCLUDED: a shot read draws every shot of
        # each game) - the retired templates' HONORED_SCOPING rows, moved.
        "shot_chart": relation_scoping("shot_chart"),
        "shot_distance": relation_scoping("shot_distance"),
    }


@dataclass(frozen=True)
class PointShape:
    """What a planned point is read and said as (``ROADMAP-TYPES.md``,
    "Where today's 25 intents land": seven relations and six table shapes):
    the ``relation`` it reads (``player_games``, ``player_periods`` - a
    quarter or half of his games -, ``player_seasons``, ``team_games``,
    ``team_periods``, ``team_seasons``, ``team_snapshots``, ``shots``,
    ``netpoints``), its ``shape`` (``scalar``, ``rows``, ``ranking``,
    ``comparison``, ``split``, ``runs``, ``chart``) and what one row is
    ``by`` (or, for a scalar, how it is reduced). ``compiled`` is the
    shape of a point no reader takes, which the compiler's own sentence
    answers. The answer side chooses a reader by this and by nothing else
    (``compose.answer``); the planner settles it once
    (:func:`shape_of`).

    .. versionadded:: 5.0.0
    """

    relation: str
    shape: str
    by: str = ""


#: The team shapes whose reader is the point's only answer (slice (iv)'s
#: ported shapes, less the league's quarter ranking, a player relation's).
_TEAM_WHOLE_SHAPES: dict[str, PointShape] = {
    "head_to_head": PointShape("team_games", "comparison", "opponent"),
    "team_quarter_points": PointShape("team_periods", "scalar", "total"),
    "team_record": PointShape("team_games", "scalar", "record"),
}


def _player_shape(intent: str | None, q: Query) -> PointShape:
    """A point on a player's games or the season line: the shape its
    reader reads, or the compiler's own where none does."""
    if q.source == "seasons":
        seasons = {
            "leaderboard": PointShape("player_seasons", "ranking", "player"),
            "player_stat": PointShape("player_seasons", "scalar", "line"),
            "player_history": PointShape("player_seasons", "split", "season"),
            "player_compare": PointShape("player_seasons", "comparison", "subject"),
        }
        return seasons.get(intent or "", PointShape("player_seasons", "compiled", f"{q.skeleton} by {q.group}"))
    if intent == "period_split":
        return PointShape("player_periods", "rows", "date") if q.skeleton == "rows" else PointShape("player_periods", "split", "period")
    if intent == "period_leaderboard":
        return PointShape("player_periods", "ranking", "player")
    if intent in ("head_to_head", "team_quarter_points", "team_record"):
        # A team shape's reader takes the point whichever relation planned it.
        return _TEAM_WHOLE_SHAPES[intent or ""]
    games = {
        ("game_log", "rows"): PointShape("player_games", "rows", "date"),
        ("player_stat", "rows"): PointShape("player_games", "rows", "date"),
        ("player_stat", "scalar"): PointShape("player_games", "scalar", "line"),
        ("record_when", "scalar"): PointShape("player_games", "split", "line"),
        ("player_splits", "grouped"): PointShape("player_games", "split", "splits"),
        ("streak", "run"): PointShape("player_games", "runs", "line"),
        ("player_matchup", "pair"): PointShape("player_games", "comparison", "met"),
    }
    if intent == "threshold_count":
        return PointShape("player_games", {"scalar": "scalar", "grouped": "ranking"}.get(q.skeleton, "rows"), "count")
    if intent == "single_game_high":
        return PointShape("player_games", "rows", "measure")
    return games.get((intent or "", q.skeleton), PointShape("player_games", "compiled", f"{q.skeleton} by {q.group}"))


def _team_shape(intent: str | None, q: TeamQuery) -> PointShape:
    """A point on a team's games: the shape its reader reads, or the team
    compiler's own sum where none does."""
    if intent == "record_when":
        return PointShape("team_games", "split", "line")
    if intent in _TEAM_WHOLE_SHAPES:
        return _TEAM_WHOLE_SHAPES[intent or ""]
    shapes = {
        ("game_log", "rows"): PointShape("team_games", "rows", "date"),
        ("player_splits", "grouped"): PointShape("team_games", "split", "splits"),
        ("streak", "run"): PointShape("team_games", "runs", "won"),
    }
    if intent == "with_without" and q.shape == "grouped" and q.group == "presence":
        return PointShape("team_games", "split", "presence")
    return shapes.get((intent or "", q.shape), PointShape("team_games", "compiled", f"{q.shape} by {q.group}"))


#: A team's own season, by its relation and shape (``point.TEAM_SEASON_POINTS``'s pairs).
_TEAM_SEASON_SHAPES: dict[tuple[str, str], PointShape] = {
    ("team_seasons", "scalar"): PointShape("team_seasons", "scalar", "line"),
    ("team_seasons", "grouped"): PointShape("team_seasons", "ranking", "team"),
    ("team_snapshots", "scalar"): PointShape("team_snapshots", "scalar", "projection"),
}


def shape_of(intent: str | None, query: Query | TeamQuery | TeamSeasonQuery | NetPointsQuery | ShotQuery) -> PointShape:
    """The shape ``query`` is read and said as: the planner's translation of
    the reading's intent into the target types' relation, shape and ``by``
    - settled once, here, so that nothing after the plan reads the intent
    (the answer side dispatches on this; ``ROADMAP.md``, the Phase 2
    review's cleanup (b)6). The query alone settles it where its relation,
    shape and group differ; the intent only where two retired templates'
    words planned the same query and are still said apart (a log against a
    quarter's log, the league's ranking against a period's, a count's
    shapes, a team's own total read from its season line first).

    # Phase 3: needs the Reading's own shape and ``by`` - this table goes
    # when the reader names them and no longer carries an intent.

    .. versionadded:: 5.0.0
    """
    if isinstance(query, NetPointsQuery):
        return PointShape("netpoints", "chart", "fingerprint") if query.shape == "chart" else PointShape("netpoints", "scalar", "ratings")
    if isinstance(query, ShotQuery):
        return PointShape("shots", "chart", "shots") if query.shape == "chart" else PointShape("shots", "scalar", "distance")
    if isinstance(query, TeamSeasonQuery):
        return _TEAM_SEASON_SHAPES[(query.relation, query.shape)]
    if intent in TEAM_SEASON_POINTS:
        # A team's own total or ranking read from another relation's point:
        # the team-season reader first, as the retired template was tried
        # before the compiler, and the team compiler's sum where it declines.
        return _TEAM_SEASON_SHAPES[TEAM_SEASON_POINTS[intent or ""]]
    if isinstance(query, TeamQuery):
        return _team_shape(intent, query)
    return _player_shape(intent, query)


#: Each reader's shape -> the retired words it says the answer in: their
#: scoping (:func:`words_stated`) and the name a decline gives the shape.
SHAPE_WORDS: dict[PointShape, str] = {
    PointShape("player_games", "rows", "date"): "game_log",
    PointShape("player_games", "scalar", "line"): "player_stat",
    PointShape("player_games", "split", "line"): "record_when",
    PointShape("player_games", "split", "splits"): "player_splits",
    PointShape("player_games", "scalar", "count"): "threshold_count",
    PointShape("player_games", "ranking", "count"): "threshold_count",
    PointShape("player_games", "rows", "count"): "threshold_count",
    PointShape("player_games", "rows", "measure"): "single_game_high",
    PointShape("player_games", "runs", "line"): "streak",
    PointShape("player_games", "comparison", "met"): "player_matchup",
    PointShape("player_periods", "rows", "date"): "period_split",
    PointShape("player_periods", "split", "period"): "period_split",
    PointShape("player_periods", "ranking", "player"): "period_leaderboard",
    PointShape("player_seasons", "ranking", "player"): "leaderboard",
    PointShape("player_seasons", "scalar", "line"): "player_stat",
    PointShape("player_seasons", "split", "season"): "player_history",
    PointShape("player_seasons", "comparison", "subject"): "player_compare",
    PointShape("team_games", "rows", "date"): "game_log",
    PointShape("team_games", "split", "splits"): "player_splits",
    PointShape("team_games", "runs", "won"): "streak",
    PointShape("team_games", "split", "presence"): "with_without",
    PointShape("team_games", "split", "line"): "record_when",
    PointShape("team_games", "comparison", "opponent"): "head_to_head",
    PointShape("team_periods", "scalar", "total"): "team_quarter_points",
    PointShape("team_games", "scalar", "record"): "team_record",
    PointShape("team_seasons", "scalar", "line"): "team_stat",
    PointShape("team_seasons", "ranking", "team"): "team_leaderboard",
    PointShape("team_snapshots", "scalar", "projection"): "team_outlook",
    PointShape("netpoints", "scalar", "ratings"): "player_netpoints",
    PointShape("netpoints", "chart", "fingerprint"): "fingerprint",
    PointShape("shots", "chart", "shots"): "shot_chart",
    PointShape("shots", "scalar", "distance"): "shot_distance",
}
"""Which retired template's words each reader's shape is said in, the one
place a shape is named for a template: its scoping (:data:`STATED_SCOPING`)
and the name a decline of the shape gives (" has no reading of this
point").

.. versionadded:: 5.0.0
"""

STATED_SCOPING: dict[PointShape, frozenset[str]] = {shape: _stated_by_words()[words] for shape, words in SHAPE_WORDS.items()}
"""A reader's shape -> the scoping its retired WORDS state (keyed by the
shape it declares for; the sets themselves are :func:`_stated_by_words`'):
asked a point narrowed beyond them, the reader steps aside and the
compiler's own sentence answers. It stays until Phase 3's cells; the
answer side reads it by the planned point's :class:`PointShape`, never by
an intent.

.. versionadded:: 5.0.0

.. versionchanged:: 5.0.0
   In the planner, its one reader beside :data:`WITH_WITHOUT_STATED`, since
   ``compose/present.py`` was deleted (Phase 2, slice (iv)).

.. versionchanged:: 5.0.0
   Keyed by :class:`PointShape` (2026-10-05); by intent until then -
   :func:`words_stated` is that lookup, for the planner, which still reads
   the reading's intent.
"""


def words_stated(words: str) -> frozenset[str]:
    """The scoping a retired template's words state, by its name - the
    planner's lookup, while it still reads the reading's intent: the
    shape :data:`SHAPE_WORDS` names for those words, in
    :data:`STATED_SCOPING`.

    # Phase 3: needs the Reading's typed cells - the planner checks those,
    # and nothing looks a set up by a template's name.

    .. versionadded:: 5.0.0
    """
    return STATED_SCOPING[next(shape for shape, named in SHAPE_WORDS.items() if named == words)]


PORTED_SHAPES: frozenset[str] = frozenset({"head_to_head", "team_quarter_points", "period_leaderboard", "team_record"})
"""The shapes Phase 2's slice (iv) ported from templates the reader gave
no point: each is declined beyond the scoping its retired template's words
state (:data:`STATED_SCOPING`, where that template's ``HONORED_SCOPING``
row moved) by name, in the sentence the retired scope check refused it with
(:func:`_shape_declines`), before the relation's own cells are checked.

.. versionadded:: 5.0.0
"""


#: The player-relation cells a team's log, splits and run refuse by name
#: with a sentence of their own (``templates.games._team_game_log_refusals``,
#: ``templates.splits.team_splits``, ``point._default_streak``): let
#: through here so that sentence, which says where the question belongs, is
#: the refusal. Anything else a team's games do not carry is refused here.
_TEAM_READER_REFUSES: frozenset[str] = frozenset({"without", "below", "above", "season_n", "conditions"})


def _team_shape_cells(reading: Reading) -> frozenset[str]:
    """What a team point's reader takes beyond the team relation's own
    cells: the with/without split reads the teammates it divides by, a
    splits table its category, a sum the unit it is asked in (which its
    mover refuses or reads), and the log, the splits and the run refuse a
    handful of player cells with their own sentence."""
    if reading.intent in PORTED_SHAPES:
        # Declined beyond these first, in the shape's own words.
        return words_stated(reading.intent)
    if reading.group == "presence":
        return frozenset({"without", "conditions"})
    if reading.shape == "grouped":
        return _TEAM_READER_REFUSES | {"split"}
    if reading.shape in ("rows", "run") or reading.aggregate == "record":
        # The log, the run and a record over a line (record_when's team
        # reader) each refuse these by name.
        return _TEAM_READER_REFUSES
    return frozenset({"rate"})


def _streak_league_cells(scope: Scope) -> None:
    """Raise if a league-wide streak (nobody named at all) set ``opponent`` or
    ``venue`` - cells only a named team's or player's games can be narrowed by.
    A league-wide streak has no single subject for either to narrow against,
    unlike a team's run (on the team relation) or a player's (the relation
    reads both). ``templates.splits._streak_league_needs_named_subject`` was
    this, the retired template's refusal."""
    claimed = sorted(cell for cell in ("opponent", "venue") if getattr(scope, cell))
    if claimed:
        raise Unsupported(f"streak cannot honor {claimed} without a named team or player - the league-wide streak has no single subject to narrow")


def _shape_declines(point: Reading) -> str | None:
    """A cell the point's own reader cannot honor beyond the relation's
    cells - why the planner declines the point, or None. The comparison
    over the season line honors no narrowing at all ("compare curry and
    lebron vs the celtics" answered for the whole season would be the
    substitution the scoping cells exist to stop); the with/without split
    only what its words state; a quarter's split, a run and two players'
    meetings each refuse the cells their retired template excluded, with
    that template's reason. Until 5.0.0's last change the point reader
    raised these itself while reading, so what was read depended on what
    would answer (``ROADMAP.md``, Phase 1, the ``read_point`` move, step 3).
    """

    intent, scope = point.intent, point.scope
    if intent == "player_compare":
        ignored = unhonored_scoping(intent, scope, frozenset())
        return f"player_compare cannot honor {ignored} - it would answer for a different span than was asked" if ignored else None
    if intent == "with_without":
        ignored = unhonored_scoping(intent, scope, WITH_WITHOUT_STATED)
        return f"with_without cannot honor {ignored} - it would answer for a different span than was asked" if ignored else None
    if intent in PORTED_SHAPES or intent in CHART_INTENTS:
        ignored = unhonored_scoping(intent, scope, words_stated(intent))
        return f"{intent} cannot honor {ignored} - it would answer for a different span than was asked" if ignored else None
    if intent == "streak" and point.relation != "player":
        # A team's or the league's run: the cells only a named player's
        # games can be narrowed by, and game_n (one numbered game of each
        # series is not a run of CONSECUTIVE games); a league-wide run has
        # no single subject for an opponent or a venue to narrow against.
        # The retired template's two refusals, in the planner since
        # 2026-10-03 (the Phase 1 review found them still in the adapter).
        try:
            condition_needs_player_refusal(intent, scope, "game_n")
            if not (scope.team and scope.team.strip()):
                # The league's run (a win streak reads the team relation
                # with no team named; a stat's run reads everyone).
                _streak_league_cells(scope)
        except Unsupported as exc:
            return str(exc)
    if intent in ("period_split", "streak", "player_matchup"):
        excluded = RELATION_SCOPING_EXCLUDED[intent]
        # A period condition is excluded from period_split's presenter's
        # WORDS only: the point keeps it, and the compiler's own sentence
        # names both quarters.
        refused = [slot for slot in excluded if (intent != "period_split" or slot != "period_condition") and getattr(scope, slot) not in (None, "", (), False)]
        return f"{intent} cannot honor {refused} - {excluded[refused[0]]}" if refused else None
    return None


def plan(reading: Reading) -> Query | TeamQuery | TeamSeasonQuery | NetPointsQuery | ShotQuery:
    """The point ``reading`` names, on the relation it names - see
    :func:`_plan`. A team-season intent's point on another relation (a
    team's own total, "how many 3-pointers have the Magic made") that the
    relation declines is that intent's own team-season point instead, or
    the team-season reader's refusal of the narrowing
    (:func:`~association.query.compose.team_stats.team_season_declines`): the retired
    template was tried before the compiler, so the compiler declining
    never decided such a question (Phase 2, step 4).

    .. versionadded:: 5.0.0

    .. versionchanged:: 5.0.0
       Plans a team-season point (:data:`~association.query.point.TEAM_SEASON_POINTS`)
       as a :class:`~association.query.compose.team_stats.TeamSeasonQuery`.
    """
    try:
        return _plan(reading)
    except Unsupported as exc:
        if reading.intent not in TEAM_SEASON_POINTS:
            raise
        declined = team_season_declines(reading.intent, reading.scope, words_stated(reading.intent))
        if declined is not None:
            raise Unsupported(declined) from exc
        relation, shape = TEAM_SEASON_POINTS[reading.intent]
        return TeamSeasonQuery(scope=reading.scope, relation=relation, shape=shape)


def _plan(reading: Reading) -> Query | TeamQuery | TeamSeasonQuery | NetPointsQuery | ShotQuery:
    """The point ``reading`` names, on the relation it names - or
    :class:`~association.query.compose.core.Unsupported` where that relation
    cannot honor a narrowing the scope carries (``round``, ``rate``, a
    ``situation`` naming no calendar): the planner's own refusal, the rule
    the retired templates' scope check applied, applied for the relation
    (:func:`~association.query.compose.core._check_relation_scoping`).
    The answering loop plans a question's point once, through
    :func:`plan_point`.

    .. versionadded:: 5.0.0

    .. versionchanged:: 5.0.0
       Refuses a narrowing the relation cannot honor (ROADMAP plan item 6,
       step (f)); the compiler's own compile step had, one call later.
    """
    declined = _shape_declines(reading)
    if declined is not None:
        raise Unsupported(declined)
    if reading.relation == "netpoints":
        # A declared relation's point (Phase 2, slice (v)): its reader reads
        # the scope as the retired template read its slots.
        return NetPointsQuery(scope=reading.scope, shape="chart" if reading.shape == "chart" else "scalar")
    if reading.relation == "shots":
        # A declared chart relation's point: its reader reads the scope
        # whole, held above to what its words state.
        return ShotQuery(scope=reading.scope, shape="chart" if reading.shape == "chart" else "scalar")
    if reading.relation in ("team_seasons", "team_snapshots"):
        # A team's own season: its reader holds the question to the
        # narrowings its words state (team_season_declines), in the retired
        # template's order - before the coverage floor.
        return TeamSeasonQuery(scope=reading.scope, relation=reading.relation, shape="grouped" if reading.shape == "grouped" else "scalar")
    if reading.relation == "team":
        # Every team shape, the sums included, against the TEAM relation's
        # own cells and what this shape's reader takes beside them.
        _check_relation_scoping(reading.scope, "team", _team_shape_cells(reading))
        return TeamQuery(scope=reading.scope, measure=reading.measures[0], aggregate=reading.aggregate, shape=reading.shape, group=reading.group)
    subject = "everyone" if reading.relation == "everyone" else "player"
    # The season line's ranking (leaderboard's retired reader) honors `rate`
    # - a season total, or a unit refused by name - which no game-level read
    # does; a point its reader declines is refused with it (_game_level).
    _check_relation_scoping(reading.scope, subject, frozenset({"rate"}) if reading.source == "seasons" and subject == "everyone" else frozenset())
    planned = Query(
        scope=reading.scope,
        skeleton=reading.shape,
        measures=list(reading.measures),
        aggregate=reading.aggregate,
        group=reading.group,
        predicates=list(reading.predicates),
        order=reading.order,
        direction=reading.direction,
        limit=reading.limit,
        offset=reading.offset,
        minimum_games=reading.minimum_games,
        available=reading.available,
        span=reading.span,
        season=reading.season,
        source=reading.source,
        subject=subject,
        position=reading.position,
    )
    if planned.source == "seasons" and not _season_line_reads(reading.intent, planned):
        return _game_level(reading.intent, planned)
    return planned


#: The season line's readers' own checks, by intent: whether the reader
#: reads a point on the line (``compose.rankings``, ``compose.seasons``),
#: from the point alone.
_SEASON_LINE_READS = {
    "leaderboard": leaderboard_reads,
    "player_stat": player_line_reads,
    "player_history": player_history_reads,
    "player_compare": player_compare_reads,
}


def _season_line_reads(intent: str | None, q: Query) -> bool:
    """Whether the season line's reader for ``intent`` reads ``q`` - the
    relation saying for itself whether it reads a point, with the scoping
    its words state (:data:`STATED_SCOPING`)."""
    reads = _SEASON_LINE_READS.get(intent or "")
    return reads is not None and reads(q, words_stated(intent or ""))


def _game_level(intent: str | None, q: Query) -> Query:
    """A season-line point (``source="seasons"``) its reader does not read,
    as the game-level relation reads it: a per-season history becomes his
    career's games grouped by season (the reading the compiler gave every
    history before the season line was a source); a ranking is the
    game-level ranking, exactly as it answered behind the retired
    template's refusal - except for what it cannot say: a stat this
    relation has no measure for is refused by name rather than ranked as
    points, and a ``rate`` only the season line reads is not dropped. An
    unnarrowed ``player_stat`` has no game-level reading that answers the
    same question, so it is declined. The season's coverage floor is
    checked first, as the answering loop checked the season-line point
    before it re-planned it (until Phase 2, step 3, ``games_reading``, in
    ``compose.answer``).
    """
    refusal = coverage_refusal(intent or "", q.scope)
    if refusal is not None:
        raise Refused(refusal)
    if q.group == "season":
        return replace(q, scope=_career_scope(q.scope), source="games")
    if q.group == "player" and q.subject == "everyone":
        stat = q.scope.stat
        if stat is not None and stat.strip() and stat_measure(stat) is None:
            raise Refused(Refusal(kind="no_ranking_measure", facts={"stat": stat}, shown={"stat": stat}))
        if q.scope.rate:
            raise Unsupported("the relation cannot honor ['rate'] - it would answer for a different span than was asked")
        if q.scope.fields:
            raise Unsupported("the game-level ranking shows no columns beside its measure")
        return replace(q, source="games")
    raise Unsupported("an unnarrowed player line the season line's reader did not say")


@dataclass(frozen=True)
class Planned:
    """What planning one question's Reading came to: the ``query`` on its
    relation, or why there is none - ``declined`` (no reading of the point,
    or a narrowing the relation cannot honor: the caller refuses, naming
    the reason) or ``refusal`` (an answer of its own to give: a stat nothing
    ranks, a floor no ranking applies).

    .. versionadded:: 5.0.0
    """

    query: Query | TeamQuery | TeamSeasonQuery | NetPointsQuery | ShotQuery | None = None
    declined: str | None = None
    refusal: Reply | None = None
    #: What the query is read and said as (:func:`shape_of`): the answer
    #: side's one key.
    shape: PointShape | None = None
    #: The coverage floor the point's season is checked against before a
    #: reader runs - ``coverage.SOURCES``' entry, still named for the
    #: retired words. # Phase 3: needs the relation's declared tables.
    floor: str | None = None


#: The reading's causes whose page names the shape refused (``refused``)
#: beside the intent, as the answering loop's own refusals did until Phase 3,
#: step 0: the championship and what the words name that nothing reads
#: (:attr:`Reading.unsupported <association.query.reading.Reading.unsupported>`).
_REFUSED_BY_NAME: frozenset[str] = frozenset(
    {"championship", "playoff_round", "non_calendar_situation", "period_stat", "period_as_condition", "team_period_stat", "bench_points", "team_boolean_count"}
)


def refusal_of(cause: Cause) -> Refusal:
    """A point reading's :class:`~association.query.reading.Cause` as the
    :class:`~association.query.result.Refusal` it is said by: its kind and
    facts, and what the page shows beside the sentence - the facts
    themselves, but for the rankings' refusals, whose page held the
    sentence as its headline too, or the floor asked for.

    .. versionadded:: 5.0.0
    """
    if cause.kind in ("shot_distance_ranking", "ranking_unit"):
        return Refusal(kind=cause.kind, facts=cause.facts, under=("message", "headline"))
    if cause.kind == "ranking_floor_unit":
        return Refusal(kind=cause.kind, facts=cause.facts, shown={"floor": {"unit": cause.facts["unit"], "count": cause.facts["count"]}})
    if cause.kind in _REFUSED_BY_NAME:
        # What the page showed beside these sentences when the answering
        # loop said them itself: the shape refused, by name, and the intent.
        return Refusal(kind=cause.kind, facts=cause.facts, shown={"refused": cause.kind, "intent": cause.facts["intent"]})
    if cause.kind == "no_player_reading":
        return Refusal(kind=cause.kind, facts=cause.facts, shown={"named_player": cause.facts["player"]})
    return Refusal(kind=cause.kind, facts=cause.facts, shown=dict(cause.facts))


def refusal_result(cause: Cause) -> Reply:
    """The refusal a point reading's :class:`~association.query.reading.Cause`
    is said with: the sentence and the data the answering loop hands on,
    one per kind in :data:`~association.query.reading.CAUSES`, said by the
    sayer's one phrase table (:func:`~association.query.compose.say.refusal_phrase`).
    The reader carries the cause and never the sentence (``ROADMAP.md``,
    Phase 1, the ``read_point`` move, step 4).

    .. versionadded:: 5.0.0
    """
    return say_refusal(refusal_of(cause))


def plan_point(reading: Reading) -> Planned:
    """The PLAN stage for one question (``ROADMAP.md``, Phase 1): the point
    the parser read (:attr:`Reading.point <association.query.reading.Reading.point>`)
    planned onto its relation, once, by the answering loop - never by the
    parser, which only reads. The parser's own verdict stands where it read
    no point (:attr:`~association.query.reading.Reading.point_declined`,
    :attr:`~association.query.reading.Reading.point_refusal`); the planner's
    refusal of a narrowing the relation cannot honor is a decline, with
    its reason.

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       A refusal the words come to before any point
       (:attr:`~association.query.reading.Reading.refused`: a championship,
       a player named on a question whose shape has no reading for one) is
       the planning's refusal, ahead of the point's own.
    """
    if reading.refused is not None:
        # The words' own refusal, before any point: a championship, a player
        # named on a question whose shape has no reading for one.
        return Planned(refusal=refusal_result(reading.refused))
    if reading.point_refusal is not None:
        return Planned(refusal=refusal_result(reading.point_refusal))
    if reading.point is None:
        return Planned(declined=reading.point_declined or "the compiler has no reading of this point")
    try:
        query = plan(reading.point)
    except Unsupported as exc:
        return Planned(declined=str(exc))
    except Refused as exc:
        return Planned(refusal=say(exc.result))
    return Planned(query=query, shape=shape_of(reading.intent, query), floor=reading.intent)
