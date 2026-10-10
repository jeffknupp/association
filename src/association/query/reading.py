"""The Reading: what a question asks, as one typed record, and the whole
record of the decision.

A question is read once - who it is about (the :class:`~association.query.subject.Subject`),
which relation answers it, what it measures, which rows count, over what
scope, in what window, in what shape - and everything after the reading
consumes this record and reads nothing from the question again. The planner
(:func:`association.query.compose.plan.plan`) turns it into the compiler's
point on a relation; the agent logs it as the trace line that says where
every value came from.

ROADMAP plan item 6. Step (a) made the record and planned the compiler's
point from it; step (b) made the parser that builds it; step (d) types it -
:class:`Scope` is the scoping as fields rather than a slot dict, every field
a closed type the checkers hold every construction to - and moves the
templates onto it: every template and the compiler read the Scope's fields.
:meth:`Scope.from_slots` is the one door a slot dict comes in by, refusing a
key or a value nothing here types, and :meth:`Scope.to_slots` the way back to
the slot shape a route is recorded and traced in.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields, replace
from datetime import date
from typing import TYPE_CHECKING, Any, ClassVar, Literal, get_args

from association.query import lexicon
from association.query.calendar import AlignmentNarrowing, CalendarNarrowing, parse_alignment, parse_situation

if TYPE_CHECKING:
    from association.query import subject as subject_reading
    from association.query.decisions import Decision
    from association.query.entities import Availability

Shape = Literal["scalar", "rows", "ranking", "comparison", "split", "runs", "chart"]
"""The form of the point's one body, in the target's vocabulary
(``ROADMAP-TYPES.md``, "The shapes"): one number over the whole narrowed set
(``scalar``), one row per game (``rows``), one row per value of what it is
``by`` - an entity over the league (``ranking``), the named subjects
(``comparison``), a dimension of the games (``split``) - the longest runs
of consecutive games one predicate holds along (``runs``), or a drawing
(``chart``). Named by the point reader, with :attr:`Reading.by` and
:attr:`Reading.on`; the compiler's skeleton (``rows``, ``scalar``,
``grouped``, ``run``, ``pair``, ``chart`` - :class:`~association.query.compose.core.Query`'s)
is derived from the three by the planner
(:func:`~association.query.compose.plan.skeleton_of`).

.. versionadded:: 5.0.0

.. versionchanged:: 5.0.0
   ``run`` and ``pair`` (ROADMAP plan item 6, step (g): ``streak``'s and
   ``player_matchup``'s retired templates); ``chart`` (Phase 2, step 5:
   ``fingerprint``; ``shot_chart``).

.. versionchanged:: 6.0.0
   The target's seven shapes, which the reader names (Phase 3, step 1);
   the compiler's skeleton until then, copied into ``Query.skeleton``.
"""

PointRelation = Literal["player_games", "player_periods", "player_seasons", "team_games", "team_periods", "team_seasons", "team_snapshots", "netpoints", "shots"]
"""The relation a point is read ON, in the target's vocabulary
(``ROADMAP-TYPES.md``, "Query": its ``relation``): a player's or the
league's games, a quarter or half of them (``player_periods``), the season
line (``player_seasons``), a team's games or a quarter of them, a team's own
season (``team_seasons``: its line and its place in the standings), ESPN's
power index for it (``team_snapshots``), NetPoints and the located shots.
Named by the point reader (:attr:`Reading.on`). Beside it :data:`Relation`
still says which compiler plans the point and whether its subject is
everyone, which Phase 3's step 2 retypes with the subject.

.. versionadded:: 6.0.0
"""

Aggregate = Literal["none", "per_game", "total", "count", "record"]
"""How the rows are reduced. A single game's high is no aggregate: it is the
``rows`` shape ordered by the measure; and a rate is the per-game one, a
ratio of the games' sums (``compose.core.RATES``).

.. versionadded:: 5.0.0
"""

Group = Literal["none", "venue", "starter", "season", "season_type", "month", "month_of_year", "opponent", "won", "player", "presence", "period", "line"]
"""``"none"`` or a key of :data:`~association.query.compose.core.GROUPS` (a
test holds the two to the same names) - or ``"presence"``, the team
relation's own group: a team's games divided by whether named teammates
played (``with_without``'s retired template, ``compose.team.compile_team_presence``)
- or ``"period"``, the player relation's own: each game divided into its
four quarters, one group per quarter over the same games
(``compose.core._compile_by_period``; "Jokic points by quarter").

.. versionadded:: 5.0.0

.. versionchanged:: 5.0.0
   ``presence`` (ROADMAP plan item 6, step (g)); ``period`` (step 2, the
   period relation's leftovers, #162).
"""

Relation = Literal["player", "everyone", "team", "team_seasons", "team_snapshots", "netpoints", "shots"]
"""Which relation answers: one named player's games, the league's, or a
team's - or, for a team's own season, its line and its place in the league
(``team_seasons``: ``team_season_stats`` and the standings), or ESPN's power
index for it (``team_snapshots``: ``team_power_index``), the two team-season
relations of ``ROADMAP-TYPES.md`` ("Query") - or ESPN Analytics' NetPoints
(``netpoints``: a player's season ratings, his play-type fingerprint and a
game's, the declared relation of the same draft).

.. versionadded:: 5.0.0

.. versionchanged:: 5.0.0
   ``team_seasons`` and ``team_snapshots`` (Phase 2, step 4: ``team_stat``,
   ``team_leaderboard`` and ``team_outlook``, whose templates read them);
   ``netpoints`` (Phase 2, step 5: ``player_netpoints`` and ``fingerprint``).
   ``shots`` (Phase 2, step 5): one player's located shots on ``shot_chart``,
   a declared relation with its own reader (``compose.shots``), not ported
   onto the player-games relation (``ROADMAP.md``, "Charts are declared
   shapes").
"""

SubjectKind = Literal["player", "pair", "team", "teams", "position", "everyone", "team_players"]
"""Who a question is about, as one word (:attr:`Subject.kind`): one named
player, two or more (``pair``), a team, two teams meeting, a position group,
the league (``everyone``), or a team's players as a group
(``team_players``: "a Hawks player"). The subject reading decides it
(:func:`~association.query.subject.read_subject`); the point reader reads
the relation a point is on off it and the names beside it.

.. versionadded:: 6.0.0
   ``subject.SUBJECT_KINDS`` was the set; the literal is the typed
   subject's (Phase 3, step 2).
"""

SeasonType = Literal[2, 3]
"""ESPN's season type: 2 the regular season, 3 the postseason.

.. versionadded:: 5.0.0
"""

Split = Literal["home_away", "starter_bench", "wins_losses", "month", "starter", "bench"]
"""A split the question asks for: the two-sided category, or the one side
("starter", "bench") a filtering template honors.

.. versionadded:: 5.0.0
"""

SPLIT_KINDS: tuple[str, ...] = ("home_away", "starter_bench", "wins_losses", "month")
"""The splits a player's games divide by, in the order a table shows them
when none is named; a team's games take all but ``starter_bench``.

.. versionadded:: 5.0.0
   On the reader's side (``templates.splits.SPLIT_KINDS`` was this).
"""


class ScopeError(ValueError):
    """A slot value the Scope cannot hold - a key nothing types, a value of
    the wrong type or outside its closed set, a window of fewer than one
    game. Raised where a slot dict comes in (:meth:`Scope.from_slots`), so
    the question is refused the way a template's refusal is rather than
    an answer quietly leaving the narrowing out.

    .. versionadded:: 5.0.0
    """


#: The slot name each span cell was declared and refused under until Phase
#: 3, step 2 - the name a decline still says ("cannot honor ['since']"),
#: until the decline-to-Cause commit rewords it.
_CELL_SLOT_NAMES: dict[str, tuple[str, ...]] = {"career": ("span",), "range": ("since", "until"), "both": ("season_type_unstated",)}


@dataclass(frozen=True, kw_only=True)
class Span:
    """The seasons a question covers and the season type it reads, as the
    words gave them - ``ROADMAP-TYPES.md``'s ``Span``, the first filter
    family typed (Phase 3, step 2). One value in place of six slots
    (``season``, ``season_type``, ``season_type_unstated``, ``span``,
    ``since``, ``until``), read by one tagger
    (:func:`~association.query.span.read_span`) and resolved by one step
    per relation (:func:`~association.query.player_relation.span_of`).
    Each field at its default is the part unstated, which the relation
    defaults - no season named is the current one, or every one on record
    for a career; no type named is the regular season.

    The parts are fields rather than one union over ``one | range | career``
    because the words name them separately and the readings hold them
    together: measured on the 2,710 readings of 2026-10-09, a career beside a
    named season (3, refused as "a career span and the 2001 season at
    once"), a career beside a range (8, the range wins), both season types
    beside a season (4), a range (3) or a career (8). A union would have to
    pick, and the relation is what picks, with its refusal where it cannot.

    .. versionadded:: 6.0.0
    """

    #: One season the words named ("2023-24", "last season", "in 2019"):
    #: the year it ends in. None where none was named - never a default.
    season: int | None = None
    #: The season type the words named - the postseason (3) where they
    #: said so, the regular season (2) otherwise - or None where no reader
    #: settled it (a Scope built from a slot dict naming none).
    season_type: SeasonType | None = None
    #: Both season types read together: named outright ("including the
    #: playoffs", "regular season and playoffs"), or none named on a "last
    #: N games" log, which reads the newest games whatever their type.
    #: ``season_type`` stays 2 beside it, never 3, so a reader of the type
    #: alone never narrows to the postseason.
    both: bool = False
    #: Every season on record ("career", "all time", "ever", "all his
    #: playoff games", "since he joined the league"), or implied: a count
    #: of a player's games with no season, a pair's record, a tenure.
    career: bool = False
    #: The first season of a range ("since 2015", "the 2010s", "the past
    #: two seasons", "from 2019-20 to 2023-24"), every season from it on.
    since: int | None = None
    #: The inclusive last season of a range (a decade, "2019-20 to
    #: 2023-24") - only ever beside ``since``.
    until: int | None = None

    #: The span's three cells, the ones a relation's cell table declares
    #: (``player_relation.RELATION_SCOPING``, ``team_relation.TEAM_RELATION_SCOPING``)
    #: and :meth:`cells` reports a value as setting: every season on record
    #: (``career``), a range of seasons bounded at one or both ends
    #: (``range``: ``since``, and ``until`` beside it), and both season
    #: types read together (``both``). A named season and a season type
    #: are not cells: every relation reads them, and nothing steps aside
    #: for or refuses one.
    CELLS: ClassVar[frozenset[str]] = frozenset({"career", "range", "both"})

    @property
    def named(self) -> bool:
        """Whether the words said which seasons: a season, a career or a range."""
        return self.season is not None or self.career or self.since is not None

    @property
    def stated(self) -> frozenset[str]:
        """Which parts the words gave - ``season``, ``career``, ``range``,
        ``postseason``, ``both`` - and so which the relation defaults. The
        regular season named outright is not told from the default: every
        reader reads it as the default, and nothing says it was asked."""
        parts = {name for name, held in (("season", self.season is not None), ("career", self.career), ("range", self.since is not None), ("both", self.both)) if held}
        if self.season_type == 3:
            parts.add("postseason")
        return frozenset(parts)

    def cells(self) -> frozenset[str]:
        """The span cells this value sets (:attr:`CELLS`): what a relation's
        table must honor, or step aside or refuse for."""
        return frozenset(cell for cell, held in (("career", self.career), ("range", self.since is not None or self.until is not None), ("both", self.both)) if held)

    def unhonored(self, honored: frozenset[str]) -> list[str]:
        """The slot names (``span``, ``since``, ``until``,
        ``season_type_unstated``) of the cells this value sets beyond
        ``honored`` - the names a decline says, in the order the slot list
        said them."""
        held = {"span": self.career, "since": self.since is not None, "until": self.until is not None, "season_type_unstated": self.both}
        return [slot for cell in self.cells() - honored for slot in _CELL_SLOT_NAMES[cell] if held[slot]]

    def over_career(self) -> Span:
        """This span read over every season: a name narrowed over a career
        (an ordinal season, a date that names its own game) - the career
        set and the one season dropped, the range and the type kept."""
        return replace(self, career=True, season=None)

    def without_range(self) -> Span:
        """This span with no range: what a read whose retired body took the
        season or the career alone covers (the league's longest run, the
        with/without split's teammates) - the planner has refused the range
        for it before it is read."""
        return replace(self, since=None, until=None)

    def as_career(self) -> Span:
        """This span with the career set and nothing else moved - what a
        count of a player's games, a tenure or "ever" implies where no
        season was named."""
        return replace(self, career=True)

    @classmethod
    def from_slots(cls, slots: Mapping[str, Any]) -> Span:
        """The Span six slot values name (:meth:`Scope.from_slots` reads
        them off a slot dict): ``season``, ``season_type``,
        ``season_type_unstated``, ``span``, ``since``, ``until``."""
        return cls(
            season=slots.get("season"),
            season_type=slots.get("season_type"),
            both=bool(slots.get("season_type_unstated")),
            career=slots.get("span") == "career",
            since=slots.get("since"),
            until=slots.get("until"),
        )

    def to_slots(self) -> dict[str, Any]:
        """The six slots, exactly as the stages wrote them until Phase 3,
        step 2 - the projection every recorded reading is compared through."""
        out: dict[str, Any] = {}
        if self.season is not None:
            out["season"] = self.season
        if self.season_type is not None:
            out["season_type"] = self.season_type
        if self.both:
            out["season_type_unstated"] = True
        if self.career:
            out["span"] = "career"
        if self.since is not None:
            out["since"] = self.since
        if self.until is not None:
            out["until"] = self.until
        return out


#: The slot name each window cell was declared and refused under until
#: Phase 3, step 2 - the name a decline still says ("cannot honor
#: ['order']"), until the decline-to-Cause commit rewords it.
_WINDOW_CELL_SLOT_NAMES: dict[str, tuple[str, ...]] = {"window": ("order",), "ranked_by": ("ranked_by",)}


@dataclass(frozen=True, kw_only=True)
class Window:
    """Which rows a read keeps and from which end, as the words gave them -
    ``ROADMAP-TYPES.md``'s ``Window``, the second filter family typed
    (Phase 3, step 2). One value in place of four slots (``order``,
    ``limit``, ``rank``, ``ranked_by``), read by one tagger
    (:func:`~association.query.window.read_window`) and cut by one step per
    relation (:func:`~association.query.player_relation.relation_window`,
    which both relations read). Each field at its default is the part
    unstated, which each reader defaults - no count is its own default
    (ten games for a log, five for a ranking), no end is the newest.

    The parts are fields rather than the draft's ``order: recent | first |
    top | bottom``, because the readings hold them apart: measured on the
    2,710 readings of 2026-10-09 (``~/association-research/stages/window_family.py``),
    a count stands with no end 102 times ("top 5", a history's seasons,
    "last 10 home games" on a reader with no window of its own) and an end
    never stands without a count (0); "top" and "bottom" name no end of the
    SPAN (a ranking's own rank word says which end of the ranking), so the
    grammar reads them as the count alone. ``rank`` is a team ranking's
    own parameter (86 readings, four values), ``by`` the one measure a
    ranking of games over a yes/no stat is ordered by (2, both "points").
    The point's ``offset`` was 0 on every reading and is gone.

    .. versionadded:: 6.0.0
    """

    #: Which end of the span the rows are taken from, where the words said:
    #: the newest ("last 10 games", "his last game") or the oldest ("first
    #: 5 games", "the season opener"). None for a bare count ("top 5") and
    #: for a count of seasons - never a default.
    order: Literal["recent", "first"] | None = None
    #: How many rows the words asked for: games, or seasons for a history
    #: (``of``). None where none was named; never below 1.
    count: int | None = None
    #: What ``count`` counts: ``"games"``, or ``"seasons"`` where a history
    #: reads "the past 5 years" as its count of seasons
    #: (:data:`~association.query.span.LIMIT_COUNTS_SEASONS`).
    of: Literal["games", "seasons"] = "games"
    #: Which end of a team ranking was asked for ("most", "fewest", "best",
    #: "worst"; :data:`~association.query.lexicon.RANK_WORDS`), which the
    #: team-season readers resolve against the metric.
    rank: Literal["most", "fewest", "best", "worst"] | None = None
    #: The measure a ranking of the games over a yes/no stat is ordered by
    #: ("the highest scoring triple doubles": ``"points"``), which the
    #: compiler's boolean-game ranking reads and the season-line ranking's
    #: reader steps aside for.
    by: str | None = None

    #: The window's two cells, the ones a relation's cell table declares
    #: (``player_relation.RELATION_SCOPING``, ``team_relation.TEAM_RELATION_SCOPING``)
    #: and :meth:`cells` reports a value as setting: a window of N at one
    #: end of the span (``window``: an ``order``, with its count), and the
    #: measure a boolean-game ranking is ordered by (``ranked_by``). A bare
    #: count is not a cell: each reader takes it as its own parameter (how
    #: many rows, runs, meetings or seasons to show; the games relations
    #: read it as the newest N), and nothing steps aside for or refuses one.
    CELLS: ClassVar[frozenset[str]] = frozenset({"window", "ranked_by"})

    def __post_init__(self) -> None:
        # The one range rule every reader already keeps: a count below 1 is
        # no window at all (the readers clamp it to their default), so a
        # producer writing one has a bug to say out loud.
        if self.count is not None and self.count < 1:
            raise ScopeError(f"scope limit {self.count} is below 1")

    @property
    def named(self) -> bool:
        """Whether the words asked for a window at all: an end or a count."""
        return self.order is not None or self.count is not None

    def cells(self) -> frozenset[str]:
        """The window cells this value sets (:attr:`CELLS`): what a relation's
        table must honor, or step aside or refuse for."""
        return frozenset(cell for cell, held in (("window", self.order is not None), ("ranked_by", self.by is not None)) if held)

    def unhonored(self, honored: frozenset[str]) -> list[str]:
        """The slot names (``order``, ``ranked_by``) of the cells this value
        sets beyond ``honored`` - the names a decline says."""
        return [slot for cell in self.cells() - honored for slot in _WINDOW_CELL_SLOT_NAMES[cell]]

    @classmethod
    def from_slots(cls, slots: Mapping[str, Any]) -> Window:
        """The Window four slot values name (:meth:`Scope.from_slots` reads
        them off a slot dict): ``order``, ``limit``, ``rank``, ``ranked_by``.
        A slot dict says nothing about what a count counts, so ``of`` is
        games; the tagger is the one writer of a count of seasons."""
        return cls(order=slots.get("order"), count=slots.get("limit"), rank=slots.get("rank"), by=slots.get("ranked_by"))

    def to_slots(self) -> dict[str, Any]:
        """The four slots, exactly as the stages wrote them until Phase 3,
        step 2 - the projection every recorded reading is compared through."""
        out: dict[str, Any] = {}
        if self.by is not None:
            out["ranked_by"] = self.by
        if self.rank is not None:
            out["rank"] = self.rank
        if self.order is not None:
            out["order"] = self.order
        if self.count is not None:
            out["limit"] = self.count
        return out


#: The slot name each cut was declared and refused under until Phase 3,
#: step 2 - the name a decline still says ("cannot honor ['situation']"),
#: until the decline-to-Cause commit rewords it. ``own_team`` is the
#: tenure's slot name; the rest are their own. The order is the old
#: ``Scope``'s field order, which :meth:`Scope.to_slots` keeps.
_CUT_SLOT_NAMES: dict[str, str] = {
    "opponent": "opponent",
    "tenure": "own_team",
    "date": "date",
    "situation": "situation",
    "game_n": "game_n",
    "season_n": "season_n",
    "round": "round",
    "venue": "venue",
}


@dataclass(frozen=True, kw_only=True)
class Situation:
    """A circumstance the words put the games under, as the reader read it
    (``ROADMAP-TYPES.md``'s ``Calendar``, ``DateRange``, the alignment half
    of its ``Opponent``, and its ``Unsupported`` for the rest): the words
    as typed (``text``: "on tuesdays", "in march", "vs the west", "since
    january 31st", "overtime", "18 year old"), and what they parse to -
    the calendar narrowing (``calendar``: a weekday, a month, a holiday,
    every game from a day of the season on, or from one date on across
    seasons; the draft's ``DateRange`` is those last two kinds), or the
    conference or division the opponent is in (``alignment``), or neither,
    which the relation refuses by the words (an age, overtime, a return
    from injury: nothing in the warehouse narrows by them). Parsed once,
    where the words are read (:func:`situation_of`); the relations read
    the parsed halves and never the words again.

    One value for the three rather than three cells, because the cell the
    relations declare is the ``situation`` (the one slot it was), and a
    decline still names it so (``_CUT_SLOT_NAMES``): measured on the 2,710
    readings of 2026-10-09 (``~/association-research/stages/cuts_family.py``),
    96 carry one - 42 a calendar narrowing, 21 an alignment, 33 neither.

    .. versionadded:: 6.0.0
    """

    text: str
    calendar: CalendarNarrowing | None = None
    alignment: AlignmentNarrowing | None = None

    @property
    def read(self) -> bool:
        """Whether the words name a narrowing a relation applies: a calendar
        narrowing or an alignment. False is the situation a relation
        refuses by value."""
        return self.calendar is not None or self.alignment is not None


@dataclass(frozen=True, kw_only=True)
class Cuts:
    """Which games of a span a read sees, as the words gave them -
    ``ROADMAP-TYPES.md``'s ``Opponent``, ``Tenure``, ``Venue``, ``OnDate``,
    ``DateRange``, ``Calendar``, ``Round``, ``GameOfSeries`` and
    ``SeasonOfCareer``, the third filter family typed (Phase 3, step 2).
    One value in place of eight slots (``opponent``, ``own_team``, ``venue``,
    ``date``, ``situation``, ``round``, ``game_n``, ``season_n``), read by
    one tagger (:func:`~association.query.cuts.read_cuts`) from the words -
    the opponent and the tenure taken from the subject reading, the one
    reader of who stands against or beside the subject - and applied by one
    step per relation (:func:`~association.query.player_relation.scoped_games`,
    :func:`~association.query.team_relation.team_games`). Each field at its
    default is the cut unstated: every game of the span.

    The parts are fields rather than the draft's nine types, because the
    readings hold them together and a relation applies each as one more
    clause over the same rows: measured on the 2,710 readings of 2026-10-09
    (``~/association-research/stages/cuts_family.py``), an opponent beside
    a venue 32 times, a round beside a situation 5, a game of a series
    beside a round 3 and beside an opponent 2, a date beside its own month 4.

    .. versionadded:: 6.0.0
    """

    #: The team the subject is set against, as the subject reading read it
    #: from the words ("vs the pistons"; :func:`~association.query.subject.read_subject`):
    #: the name, which the relation resolves against the span's season.
    opponent: str | None = None
    #: The team the player played FOR ("as a starter for Miami"), a tenure
    #: the subject reading writes beside him (``subject._apply_own_team``);
    #: the relation keeps the games he played for it.
    tenure: str | None = None
    #: Where the games were played, where the words said one side: at home
    #: or on the road. Both at once is a split, not a cut.
    venue: Literal["home", "away"] | None = None
    #: One calendar day, ``YYYY-MM-DD`` - a game named outright ("march
    #: 17", "november 11 2019"), the year fixed by the season the words name
    #: or the current one, never on a career question.
    date: str | None = None
    #: A circumstance the games are under: a calendar narrowing, an
    #: alignment of the opponent, or words nothing reads (:class:`Situation`).
    situation: Situation | None = None
    #: A playoff round, as worded ("finals", "first round"); no table
    #: carries one, so every relation refuses it by name.
    round: str | None = None
    #: One game of each playoff series ("game 4"), which the relation
    #: numbers by date within the series.
    game_n: int | None = None
    #: A season named by its place in the player's career ("his 18th
    #: season"), settled to a year once he is known.
    season_n: int | None = None

    #: The eight cuts, each under its own name: the ones a relation's cell
    #: table declares (``player_relation.RELATION_SCOPING``,
    #: ``team_relation.TEAM_RELATION_SCOPING``) and :meth:`cells` reports a
    #: value as setting. A ``round`` is in neither table (no game is labeled
    #: by its round, so every reader refuses it); a ``tenure`` and an
    #: ordinal ``season_n`` are one player's, not the team relation's.
    CELLS: ClassVar[frozenset[str]] = frozenset(_CUT_SLOT_NAMES)

    @property
    def named(self) -> bool:
        """Whether the words cut the span at all."""
        return bool(self.cells())

    def cells(self) -> frozenset[str]:
        """The cuts this value sets (:attr:`CELLS`): what a relation's table
        must honor, or step aside or refuse for."""
        return frozenset(name for name in _CUT_SLOT_NAMES if getattr(self, name) is not None)

    def unhonored(self, honored: frozenset[str]) -> list[str]:
        """The slot names (``own_team`` for the tenure, the rest their own)
        of the cuts this value sets beyond ``honored`` - the names a decline
        says."""
        return [_CUT_SLOT_NAMES[cell] for cell in _CUT_SLOT_NAMES if cell in self.cells() and cell not in honored]

    @classmethod
    def from_slots(cls, slots: Mapping[str, Any]) -> Cuts:
        """The Cuts eight slot values name (:meth:`Scope.from_slots` reads
        them off a slot dict): ``opponent``, ``own_team``, ``venue``,
        ``date``, ``situation`` (its words, parsed here), ``round``,
        ``game_n``, ``season_n``."""
        situation = slots.get("situation")
        return cls(
            opponent=slots.get("opponent"),
            tenure=slots.get("own_team"),
            venue=slots.get("venue"),
            date=slots.get("date"),
            situation=situation_of(situation) if isinstance(situation, str) else None,
            round=slots.get("round"),
            game_n=slots.get("game_n"),
            season_n=slots.get("season_n"),
        )

    def to_slots(self) -> dict[str, Any]:
        """The eight slots, exactly as the stages wrote them until Phase 3,
        step 2 (the situation as its words), in the old field order - the
        projection every recorded reading is compared through."""
        out: dict[str, Any] = {}
        for cell, slot in _CUT_SLOT_NAMES.items():
            value = getattr(self, cell)
            if value is not None:
                out[slot] = value.text if isinstance(value, Situation) else value
        return out


def situation_of(text: str) -> Situation:
    """The :class:`Situation` ``text`` names: its words, and the calendar
    narrowing or - read only where there is none, as the relations read it
    - the alignment they parse to. One reader for the tagger and the slot
    door alike.

    .. versionadded:: 6.0.0
    """
    calendar = parse_situation(text)
    return Situation(text=text, calendar=calendar, alignment=None if calendar is not None else parse_alignment(text))


@dataclass(frozen=True, kw_only=True)
class Period:
    """What a read SEES of each game, as the words gave it -
    ``ROADMAP-TYPES.md``'s ``Reading.period`` ("a quarter or half"), the
    fourth filter family typed (Phase 3, step 2). One value in place of two
    slots (``period``, a quarter 1-4 - or an overtime period by number, 5
    and up, which the relation labels and nothing reads from the words -
    and ``half``, 1-2), read by one tagger
    (:func:`~association.query.period.read_period`) and applied by one step
    per relation (:func:`~association.query.player_relation.apply_period`,
    ``team_relation._team_games_apply_period``), which narrows every read
    to the period's line of each game rather than to which games are read.
    None on the Scope is the whole game.

    One cell, ``period``, not two: measured on the 2,710 readings of
    2026-10-09 (``~/association-research/stages/period_family.py``), 120
    carry one - 79 quarters and 41 halves, never both - the two relations
    apply a quarter and a half through one step (``narrow_periods``), and
    each reader's exclusion rows paired the two slots under one reason in
    two wordings. The slot name a decline said (``period`` or ``half``) is
    kept by :meth:`unhonored` until the decline-to-Cause commit rewords it.

    .. versionadded:: 6.0.0
    """

    #: Which one, by number: the quarter (1-4; 5 and up an overtime period,
    #: as ``period_label`` names it) or, with ``half``, the half (1-2).
    number: int
    #: Whether ``number`` counts halves rather than quarters.
    half: bool = False

    #: The family's one cell, which both relations' tables declare
    #: (``player_relation.RELATION_SCOPING``, ``team_relation.TEAM_RELATION_SCOPING``).
    CELLS: ClassVar[frozenset[str]] = frozenset({"period"})

    def __post_init__(self) -> None:
        if isinstance(self.number, bool) or not isinstance(self.number, int):
            raise ScopeError(f"scope period={self.number!r} is not a whole number")
        if self.half and self.number not in (1, 2):
            raise ScopeError(f"scope half={self.number!r} is not one of (1, 2)")

    def narrowing(self) -> tuple[tuple[int, ...], str] | None:
        """The periods of each game this value narrows a read to, and how
        an answer names them - ``((3, 4), "2nd half")``, ``((5,),
        "overtime")`` - or None for a number no game has a period for
        (outside 1-10), which the period readers decline by name."""
        if self.half:
            return _HALF_PERIODS[self.number], f"{ordinal_word(self.number)} half"
        if 1 <= self.number <= 10:
            return (self.number,), period_label(self.number)
        return None

    def cells(self) -> frozenset[str]:
        """The one cell this value sets (:attr:`CELLS`)."""
        return self.CELLS

    def unhonored(self, honored: frozenset[str]) -> list[str]:
        """The slot name (``half`` for a half, ``period`` for a quarter) of
        the cell beyond ``honored`` - the name a decline says."""
        return [] if "period" in honored else ["half" if self.half else "period"]

    @classmethod
    def from_slots(cls, slots: Mapping[str, Any]) -> Period:
        """The Period the two slot values name (:meth:`Scope.from_slots`
        reads them off a slot dict): ``period`` or ``half``, never both -
        the words name one or the other, and a half beside a quarter was
        the model-era slot under the stages' half."""
        if "period" in slots and "half" in slots:
            raise ScopeError(f"scope period={slots['period']!r} and half={slots['half']!r} at once")
        if "half" in slots:
            return cls(number=slots["half"], half=True)
        return cls(number=slots["period"])

    def to_slots(self) -> dict[str, Any]:
        """The one slot, exactly as the stages wrote it until Phase 3, step
        2 - the projection every recorded reading is compared through."""
        return {"half": self.number} if self.half else {"period": self.number}


#: The slot names the line family's cells were declared and refused under
#: until Phase 3, step 2 - the names a decline still says ("cannot honor
#: ['above', 'without']"), until the decline-to-Cause commit rewords it: a
#: line the relation narrows by was ``below`` and ``above`` (by which way it
#: faces), a line in a quarter ``period_condition``, a companion ``without``
#: (an absence) and ``conditions`` (any other role).
_LINE_CELL_SLOT_NAMES: dict[str, tuple[str, ...]] = {"line": ("below", "above"), "period_line": ("period_condition",)}

LineOp = Literal[">=", ">", "<=", "<", "="]
"""How a line compares a stat to its number (``player_games.MEASURE_OPS``
holds the same five as SQL).

.. versionadded:: 6.0.0
"""


@dataclass(frozen=True, kw_only=True)
class Line:
    """A stat reached, missed or equaled in a game, as the words gave it -
    ``ROADMAP-TYPES.md``'s ``Line(measure, op, value, who, period)``, the
    fifth filter family typed (Phase 3, step 2): "30+ points", "under 14
    fta", "with 25 minutes", "scores 30", "fouled out" (fouls at six), "one
    three in the first quarter". One value in place of the five carriers a
    line had: the ``stat``/``threshold`` slot pair, the ``above`` and
    ``below`` phrases kept whole, the point's re-reading of the number's
    words into a predicate, a companion's ``reached`` entry and the
    ``period_condition``. Whose line it is lives where the value does: on
    the :class:`Scope` it is the subject's, on a :class:`Companion` the
    companion's (the draft's ``who``).

    Built from the words, never from the slot pair: measured on the 2,710
    readings of 2026-10-09 (``~/association-research/stages/line_family.py``),
    in 11 of them the slot pair contradicted the words ("20+ point 5+
    assist" read ``stat: assists, threshold: 20``; the relation narrowed by
    the words and the pair went unread).

    .. versionadded:: 6.0.0
    """

    #: The column the words name (a :data:`~association.query.lexicon.MEASURE_WORDS`
    #: value), a boolean measure, or None where the words name no stat a
    #: game can be kept under ("under 25 years old", "below 8000"): the
    #: relation refuses such a line by its words (``line_names_no_stat``).
    measure: str | None
    op: LineOp = ">="
    #: The number: a count of the stat (a boolean measure's line holds True).
    value: int | bool = 1
    #: The quarter or half the line is read in, where the words put it in
    #: one ("after making one three in the first quarter"); None for the
    #: whole game.
    period: Period | None = None
    #: The characters the line was read from, as typed and casefolded
    #: ("20+ points", "under 14 fta"): what a refusal quotes, and what the
    #: slot-era projection prints (:meth:`Scope.to_slots`). Empty for a
    #: line built by hand rather than read.
    as_typed: str = ""
    #: Whether the shape is KEYED on this line - a count of the games over
    #: it, a record above and below it, a run holding it, a high ranked by
    #: its stat (the draft's ``line(Line)`` dimension, ``Runs.line``): read
    #: into the point's predicate by the shape's reader. The ``threshold``
    #: slot until Phase 3, step 2; the shape's line moves to the point's
    #: ``by`` when the measure is typed.
    keyed: bool = False
    #: Whether the RELATION narrows the games by this line as a filter: a
    #: phrase kept under or over a number ("under 14 fta", "with 25
    #: minutes"), or one of two or more "N+ stat" pairs on one game ("20+
    #: point 5+ assist games"). The ``below`` and ``above`` phrases until
    #: Phase 3, step 2. A line that is neither keyed nor narrowing is read
    #: and applied by nothing but the league's multi-line listing - the
    #: second and later bare "N stat" lines of "20 pts, 10 reb, 5 ast",
    #: which the stages read the first of and dropped the rest (ISSUES.md,
    #: "A second bare line on a count is dropped").
    narrows: bool = False

    #: The family's two cells on the subject's own lines, the ones a
    #: relation's cell table declares (``player_relation.RELATION_SCOPING``)
    #: and :meth:`Scope.cells` reports a value as setting: a line the
    #: relation narrows the games by (``line``: a phrase kept under or over
    #: a number, or two or more "N+ stat" pairs on one game) and a line in
    #: a quarter or half (``period_line``). A single "N+ stat" line under a
    #: count, a record, a streak or a high is that shape's OWN line, read
    #: into the point's predicate rather than applied by the relation, and
    #: sets no cell - as the ``threshold`` slot never did.
    CELLS: ClassVar[frozenset[str]] = frozenset({"line", "period_line"})

    def __post_init__(self) -> None:
        if self.op not in (">=", ">", "<=", "<", "="):
            raise ScopeError(f"line op {self.op!r} is not one of ('>=', '>', '<=', '<', '=')")
        if isinstance(self.value, bool):
            return
        if not isinstance(self.value, int):
            raise ScopeError(f"line value {self.value!r} is not a whole number")

    @property
    def below(self) -> bool:
        """Whether the line keeps games under its number ("under 14 fta",
        "at most 5 turnovers")."""
        return self.op in ("<", "<=")

    @property
    def minutes_phrase(self) -> bool:
        """Whether the line is a floor of minutes read on every reader
        ("with 25 minutes", "30+ mins"; :data:`~association.query.lexicon.ABOVE`)
        rather than a line the threshold grammar reads."""
        return self.measure == "minutes" and self.op == ">=" and self.period is None

    @property
    def pair(self) -> bool:
        """Whether the words carried a plus ("20+ points", "36 plus
        rebounds", "30 or more points"; :data:`~association.query.lexicon.THRESHOLD_PAIR`) -
        two or more of them on one game are lines the relation narrows by,
        where one alone is the shape's own line."""
        return self.period is None and self.op == ">=" and not self.minutes_phrase and lexicon.THRESHOLD_PAIR.fullmatch(self.as_typed) is not None

    def as_predicate(self) -> tuple[str, str, Any]:
        """The ``(measure, op, value)`` triple the compiler reads
        (:attr:`Reading.predicates`); a line naming no stat has none."""
        if self.measure is None:
            raise ScopeError(f"a line on no stat ({self.as_typed!r}) is no predicate")
        return (self.measure, self.op, self.value)

    def to_slot(self) -> dict[str, Any]:
        """The ``period_condition`` slot value this line was recorded as until
        Phase 3, step 2 (a line in a quarter or half), the projection every
        recorded reading is compared through."""
        if self.period is None or self.measure is None:
            raise ScopeError(f"{self!r} is no period condition")
        where = {"half": self.period.number} if self.period.half else {"period": self.period.number}
        return {"stat": self.measure, "threshold": self.value, "op": self.op, **where}

    def to_period_record(self) -> dict[str, Any]:
        """The ``period_condition`` value as a recorded reading held it until
        Phase 3, step 2 - every field of the record it was, a quarter's
        ``half`` and a half's ``period`` as None."""
        if self.period is None or self.measure is None:
            raise ScopeError(f"{self!r} is no period condition")
        return {"stat": self.measure, "threshold": self.value, "op": self.op, "period": None if self.period.half else self.period.number, "half": self.period.number if self.period.half else None}

    @classmethod
    def from_period_slot(cls, entry: Any) -> Line:
        """One ``period_condition`` slot value, a dict, as a line in a quarter
        or half - raising on a shape the relation could not read."""
        if isinstance(entry, Line):
            return entry
        if not isinstance(entry, Mapping) or set(entry) - {"stat", "threshold", "op", "period", "half"} or "stat" not in entry or "threshold" not in entry:
            raise ScopeError(f"scope period_condition {entry!r} is not a stat, a threshold and a period or half")
        period, half = entry.get("period"), entry.get("half")
        if (period is None) == (half is None):
            raise ScopeError(f"scope period_condition {entry!r} needs exactly one of period and half")
        where = Period(number=_whole("period_condition period", period)) if period is not None else Period(number=_one_of(1, 2)("period_condition half", half), half=True)
        return cls(
            measure=_text("period_condition stat", entry["stat"]),
            op=_one_of(">=", "=")("period_condition op", entry.get("op", ">=")),
            value=_whole("period_condition threshold", entry["threshold"]),
            period=where,
        )


Predicate = Literal["played", "absent", "started", "bench", "reached"]
"""What a companion did in the games asked about (``player_games.CONDITION_PREDICATES``
holds the same five on the relation's side).

.. versionadded:: 6.0.0
"""


@dataclass(frozen=True, kw_only=True)
class Companion:
    """A player named beside the subject with the role the question gives him
    in the games asked about - ``ROADMAP-TYPES.md``'s ``Companion(player,
    side, predicate)``, the fifth filter family typed (Phase 3, step 2):
    "without KD" (``absent``), "when Embiid and Paul George play"
    (``played``), "when Embiid starts" (``started``), "with Tatum off the
    bench" (``bench``), "in games Maxey had 20+ points" (``reached``, with
    his :class:`Line`), "most points by curry vs lebron" (``played`` on the
    ``opponent``'s side). One value in place of three slots - ``with_player``
    (the with/without split's names), ``without`` (the teammates absent) and
    ``conditions`` (every other role, typed as ``ConditionSpec``) - read by
    ONE reader, the subject reading (``subject.read_subject``: the name by
    its position in the phrase, the role by the phrase's own words), written
    onto the Scope by ``subject.apply_subject``, and resolved by one step per
    relation (``player_relation._condition_from_slot``, the with/without
    split's ``presence`` read).

    .. versionadded:: 6.0.0
    """

    #: The name as the question spells it (a near spelling kept as typed:
    #: "without zzyzx" is refused by that name, never answered as though
    #: nobody had been named).
    player: str
    #: Whose games he was in: the subject's own (a teammate), or the
    #: opponent's (a player after "vs"/"against").
    side: Literal["own", "opponent"] = "own"
    predicate: Predicate = "played"
    #: A ``reached`` companion's line ("scores 20+ points"); None for any
    #: other role.
    line: Line | None = None

    #: The family's one cell on the companions, which both relations' tables
    #: name (``player_relation.RELATION_SCOPING``): a player beside the
    #: subject with a role. Declared under two slot names until Phase 3,
    #: step 2 (``without`` for an absence, ``conditions`` for the rest), and
    #: never honored or refused apart - no table named one without the
    #: other - so one cell, which a decline still says under the slot name
    #: set (:func:`companion_slot_names`).
    CELLS: ClassVar[frozenset[str]] = frozenset({"companion"})

    def __post_init__(self) -> None:
        if not isinstance(self.player, str) or not self.player.strip():
            raise ScopeError(f"a companion needs a player, got {self.player!r}")
        if self.side not in ("own", "opponent"):
            raise ScopeError(f"companion side {self.side!r} is not one of ('own', 'opponent')")
        if self.predicate not in ("played", "absent", "started", "bench", "reached"):
            raise ScopeError(f"companion predicate {self.predicate!r} is not one of ('played', 'absent', 'started', 'bench', 'reached')")
        if (self.line is not None) != (self.predicate == "reached"):
            raise ScopeError(f"a reached companion carries a line and no other role does, got {self!r}")

    @property
    def absent(self) -> bool:
        """Whether he sat the games out on the subject's own side - the
        ``without`` slot's reading, which the relation bounds to his tenure."""
        return self.predicate == "absent" and self.side == "own"

    @classmethod
    def from_slot(cls, entry: Any) -> Companion:
        """One ``conditions`` slot entry, a dict, as a typed companion -
        raising on a shape the relation could not read."""
        if isinstance(entry, Companion):
            return entry
        if not isinstance(entry, Mapping) or set(entry) - {"player", "side", "predicate", "stat", "threshold"}:
            raise ScopeError(f"scope condition {entry!r} is not a player, side, predicate and line")
        player = entry.get("player")
        if not isinstance(player, str) or not player.strip():
            raise ScopeError(f"scope condition {entry!r} names no player")
        predicate = _one_of("played", "absent", "started", "bench", "reached")("condition predicate", entry.get("predicate", "played"))
        stat, threshold = entry.get("stat"), entry.get("threshold")
        line = None
        if predicate == "reached":
            # A reached role's line, as the slot carried it: its stat may be a
            # word no column answers to (the relation refuses such a line by
            # name), but a line with no stat or no number is no line at all.
            if stat is None or threshold is None:
                raise ScopeError(f"scope condition {entry!r} reaches a line with no stat or no threshold")
            line = Line(measure=_text("condition stat", stat), value=_whole("condition threshold", threshold))
        return cls(player=player, side=_one_of("own", "opponent")("condition side", entry.get("side", "own")), predicate=predicate, line=line)

    def to_slot(self) -> dict[str, Any]:
        """The ``conditions`` entry this companion was recorded as until
        Phase 3, step 2: the player, the side, the predicate, and a reached
        role's stat and threshold."""
        line = {}
        if self.line is not None:
            line = {key: value for key, value in (("stat", self.line.measure), ("threshold", self.line.value)) if value is not None}
        return {"player": self.player, "side": self.side, "predicate": self.predicate, **line}

    def to_record(self) -> dict[str, Any]:
        """The ``conditions`` entry as a recorded reading held it until Phase
        3, step 2 - every field of the record it was, ``stat`` and
        ``threshold`` None for any role but ``reached``."""
        return {
            "player": self.player,
            "side": self.side,
            "predicate": self.predicate,
            "stat": self.line.measure if self.line is not None else None,
            "threshold": self.line.value if self.line is not None else None,
        }


MeasureHow = Literal["per_game", "total", "per_100", "per_90"]
"""How a measure is asked for: a figure per game, a season total, a rate per
100 possessions (the one adjusted form NetPoints has), or per 90 - a unit
nothing here holds, kept so the refusal names it.

.. versionadded:: 6.0.0
"""

MeasureWhose = Literal["own", "opponent"]
"""Whose figure a measure is: the subject's own, or what it gave up ("points
allowed" is the opponent's points).

.. versionadded:: 6.0.0
"""

MeasureSide = Literal["offense", "defense", "total"]
"""The side of the ball a NetPoints measure or a fingerprint is asked on.

.. versionadded:: 6.0.0
"""


@dataclass(frozen=True, kw_only=True)
class Measure:
    """What a question asks about - ``ROADMAP-TYPES.md``'s
    ``Measure(key, how, whose, category, side)``, the sixth filter family
    typed (Phase 3, step 2): one value in place of the seven slots that
    carried it (``stat``, ``rate``, ``per_game``, ``side``, ``shot_value``,
    ``fields``, ``kind``). The ``key`` is one of the catalog's
    (:data:`association.query.measure.CATALOG`), which says how the measure
    is read on each relation - as a game column or a derived measure, as the
    season line's column, as a ranking's metric, as a team's metric - and
    what it is called; the six vocabularies the slot was read against are
    lookups into it (:func:`association.query.measure.key_of`).

    Measured first on the 2,710 readings of 2026-10-09
    (``~/association-research/stages/measure_family.py``): a ``stat`` on
    1,004 readings in 69 spellings - 23 the model's, 37 the words', and the
    team metrics' alias texts for the rest ("fgm", "ppg", "defensive
    rating") - the model's key and the words' disagreeing on 58 (the words
    won every one, as the stages wrote them), a key no catalog held on 24
    (``shot_distance``, a sentinel the ranking refuses by; ``games_played``;
    the given-up keys), a rate on 13, a per-game log on 38, a side on 1, a
    shot value on 13 (every one 3, beside a stat naming the same shots on 5),
    columns beside a ranking on 7, a run's kind on 6.

    .. versionadded:: 6.0.0
    """

    #: The catalog's key (:data:`association.query.measure.CATALOG`), or
    #: None where the words or the model named a measure no catalog holds
    #: (``as_typed`` says what), or where the question says something about
    #: its measure (a unit, a side, which shots, a run's result, the columns
    #: beside a ranking) without naming which.
    key: str | None = None
    #: How the reader wrote the measure: the model's key, the grammar's,
    #: a stage's spelling or a team metric's alias text as the words had it
    #: - what a refusal prints ("no team metric for stat 'ppg'") and what
    #: the ``stat`` slot projects to (:meth:`Scope.to_slots`). None where
    #: no measure was named.
    as_typed: str | None = None
    #: How the figure is asked for (:data:`MeasureHow`); None where the
    #: words say nothing and the reader's default applies.
    how: MeasureHow | None = None
    #: The unit's words, as the ``rate`` slot carried them ("per 100", "per
    #: possession", "/ 90", "total"): the cell a relation honors or refuses
    #: by name (:attr:`CELLS`), and what the refusal quotes. None where the
    #: unit is the key's own (a NetPoints rate folded into its metric).
    unit: str | None = None
    whose: MeasureWhose = "own"
    #: NetPoints only: the side of the ball a fingerprint or a rating is
    #: asked on.
    side: MeasureSide | None = None
    #: The shots relation only: which shots a chart or a distance is about
    #: (1, 2 or 3). The measure's rather than a cell of the relation,
    #: because a stat naming the same shots arrives beside it ("threes" is
    #: 3-pointers made AND shot value 3) and the relation reads the value
    #: first, then the stat (``compose.shots.shot_value_of``).
    shot_value: Literal[1, 2, 3] | None = None
    #: NetPoints only: one of the play-type categories a fingerprint's
    #: metric is of (``nba.netpoints.FINGERPRINT_CATEGORIES``' own
    #: spellings: ``two_pt``, ``assist``, ...), where a metric of one was
    #: named outright (``assist_o_net_pts``); None otherwise.
    category: str | None = None
    #: A run's result (the streak shape): True for a run of wins, False for
    #: losses, read from the words ("losing streak", "skid") - the ``kind``
    #: slot until Phase 3, step 2, written on every run and read by the
    #: run's reader only where no stat line is.
    won: bool | None = None
    #: The columns a ranking asks to see beside its measure ("with their
    #: rebounds and assists", "the team they play for"): catalog keys, or
    #: ``team`` - the ``fields`` slot until Phase 3, step 2.
    beside: tuple[str, ...] = ()

    #: The family's one cell a relation's table declares and a decline
    #: names: ``rate``, a unit asked for - honored by the season-line
    #: ranking (a season total, a NetPoints metric's per-100 form; a unit the
    #: metric has no form of refused by name) and the team compiler's
    #: unnarrowed total, refused by every other reader as the slot was.
    CELLS: ClassVar[frozenset[str]] = frozenset({"rate"})

    def __post_init__(self) -> None:
        if self.as_typed is not None and (not isinstance(self.as_typed, str) or not self.as_typed.strip()):
            raise ScopeError(f"measure as_typed {self.as_typed!r} is not text")
        if self.how is not None and self.how not in ("per_game", "total", "per_100", "per_90"):
            raise ScopeError(f"measure how {self.how!r} is not one of ('per_game', 'total', 'per_100', 'per_90')")
        if self.whose not in ("own", "opponent"):
            raise ScopeError(f"measure whose {self.whose!r} is not one of ('own', 'opponent')")
        if self.side is not None and self.side not in ("offense", "defense", "total"):
            raise ScopeError(f"scope side={self.side!r} is not one of ('offense', 'defense', 'total')")
        if self.shot_value is not None and (isinstance(self.shot_value, bool) or self.shot_value not in (1, 2, 3)):
            raise ScopeError(f"scope shot_value={self.shot_value!r} is not one of (1, 2, 3)")

    @property
    def named(self) -> bool:
        """Whether the question named a measure at all (``as_typed``), known
        to the catalog or not."""
        return self.as_typed is not None

    def cells(self) -> frozenset[str]:
        """The cell this value sets (:attr:`CELLS`): ``rate`` where a unit
        was asked for that the key did not fold in."""
        return frozenset({"rate"}) if self.unit is not None else frozenset()

    def unhonored(self, honored: frozenset[str]) -> list[str]:
        """The cell set that ``honored`` does not hold, by its slot name."""
        return [cell for cell in sorted(self.cells()) if cell not in honored]

    @classmethod
    def from_slots(cls, slots: Mapping[str, Any]) -> Measure | None:
        """The measure the seven slot values name (:meth:`Scope.from_slots`
        reads them off a slot dict): the ``stat`` resolved through the
        catalog (:func:`association.query.measure.key_of`), ``rate`` as
        the unit and its reading, ``per_game`` as the per-game ``how``,
        ``side``, ``shot_value``, ``fields`` as ``beside``, ``kind`` as
        ``won``. None where every one is absent."""
        from association.query.measure import key_of  # the catalog; `measure` imports this module's types

        stat, rate, per_game = slots.get("stat"), slots.get("rate"), slots.get("per_game")
        side, shot_value, fields, kind = slots.get("side"), slots.get("shot_value"), slots.get("fields"), slots.get("kind")
        if not any((stat, rate, per_game, side, shot_value, fields, kind)):
            return None
        key, whose, keyed_side, keyed_how, category = key_of(stat) if stat else (None, "own", None, None, None)
        how: MeasureHow | None = keyed_how
        if rate:
            how = _how_of_unit(rate)
        elif per_game:
            how = "per_game"
        return cls(
            key=key,
            as_typed=stat or None,
            how=how,
            unit=rate or None,
            whose=whose,
            side=side or keyed_side,
            shot_value=shot_value or None,
            category=category,
            won=None if kind is None else kind != "loss",
            beside=tuple(fields or ()),
        )

    def to_slots(self) -> dict[str, Any]:
        """The seven slots this value was recorded as until Phase 3, step 2,
        in the field order they stood in - each only where set, as the
        slot dict held them."""
        out: dict[str, Any] = {}
        folded_side, folded_how = self._folded()
        if self.as_typed is not None:
            out["stat"] = self.as_typed
        if self.beside:
            out["fields"] = list(self.beside)
        if self.how == "per_game" and self.unit is None and folded_how is None:
            out["per_game"] = True
        if self.unit is not None:
            out["rate"] = self.unit
        if self.side is not None and folded_side is None:
            out["side"] = self.side
        if self.shot_value is not None:
            out["shot_value"] = self.shot_value
        if self.won is not None:
            out["kind"] = "win" if self.won else "loss"
        return out

    def _folded(self) -> tuple[MeasureSide | None, MeasureHow | None]:
        """The side and the rate the key's own spelling folds in
        (``netpoints_defense_per_100``, ``avg_game_score``), which the slots
        never carried apart from it - left out of the projection."""
        from association.query.measure import key_of  # the catalog; `measure` imports this module's types

        if self.as_typed is None:
            return None, None
        _key, _whose, side, how, _category = key_of(self.as_typed)
        return side, how

    def projected(self) -> dict[str, Any]:
        """Every one of the seven slots, at its default or not, in field
        order (:meth:`Scope.projected`)."""
        folded_side, folded_how = self._folded()
        return {
            "stat": self.as_typed,
            "fields": self.beside,
            "per_game": self.how == "per_game" and self.unit is None and folded_how is None,
            "rate": self.unit,
            "side": self.side if folded_side is None else None,
            "shot_value": self.shot_value,
            "kind": None if self.won is None else ("win" if self.won else "loss"),
        }


_UNIT_HOW: tuple[tuple[str, MeasureHow], ...] = (("total", "total"), ("90", "per_90"), ("100", "per_100"), ("possession", "per_100"), ("adjusted", "per_100"))


def _how_of_unit(unit: str) -> MeasureHow | None:
    """The reading of a unit's words (the ``rate`` slot's value): a season
    total, per 90, per 100 possessions in any of its spellings; None for a
    unit nothing here reads ("per_36"), which the cell still carries."""
    for word, how in _UNIT_HOW:
        if word in unit.casefold():
            return how
    return None


#: The seven slots the measure was carried as until Phase 3, step 2, in
#: the order they stood among the Scope's fields - where the projection
#: emits them (:meth:`Scope.projected`), with the window's ``ranked_by``
#: after ``fields`` and its ``rank`` after ``kind`` as before.
_MEASURE_SLOT_NAMES = frozenset({"stat", "fields", "per_game", "rate", "side", "shot_value", "kind"})


#: The four slot names the subject was carried as until Phase 3, step 2,
#: in the order they stood among the Scope's fields - the names the
#: subject's door still takes (:meth:`Subject.from_slots`).
_SUBJECT_SLOT_NAMES = ("player", "players", "team", "teams")


@dataclass(frozen=True)
class Subject:
    """Who a read is about - ``ROADMAP-TYPES.md``'s ``Subject(kind, names,
    position, of_team)``, the seventh family typed (Phase 3, step 2): one
    value in place of the four slots that carried the names (``player``,
    ``players``, ``team``, ``teams``) and the point's ``position``. Written
    by the subject reading alone (:func:`~association.query.subject.apply_subject`,
    over what the stages settled from it), and read by the point reader and
    the relations' readers - who the read narrows to; resolving a name
    against the warehouse stays where each is read.

    ``kind`` is the reading's (:data:`SubjectKind`). ``players`` are the
    players the read is about, as the reading settled them - the question's
    own spelling of a name the model completed, a span the model copied and
    nothing placed, the player a count's grammar names where the model
    dropped him, the companion whose line a team's record is keyed on - in
    the question's order: ONE is the player a reader reads (:attr:`player`;
    the ``player`` slot), two or more a comparison or a pair (the
    ``players`` slot). ``teams`` the same for teams: one is the team the
    read is about, or whose players it ranks (the draft's ``of_team``: "most
    points by a 76ers power forward"), or a team named beside a player that
    the reading placed nowhere (:attr:`team`; the ``team`` slot); two or
    more are teams a slot dict named as a list (the router-era ``teams``
    slot - the parser never writes one: 0 of the 2,710 readings), and two
    teams meeting are the first team and the games' opponent
    (:attr:`Cuts.opponent`), as the relations apply them. ``position`` is
    the position group the words name ("centers", "a shooting guard";
    :data:`~association.query.lexicon.POSITION_WORDS`' codes), which the
    league's own reads narrow to - on a point, only the moves that honor
    one carry it (``point._everyone_point``).

    The names are fields beside the kind rather than the draft's one
    ``names`` tuple, and the kind is the reading's rather than what the
    names say, because the readings hold them apart: measured on the 2,710
    readings of 2026-10-10 (``~/association-research/stages/subject_family.py``),
    players and a team together on 43 (22 team records keyed on a
    companion's line, 21 a team named beside a player that nothing placed),
    a player under a ``team`` kind on 23, a player under ``everyone`` on 11
    (the grammar's subject a count or a high settles where the reading named
    nobody), a team beside a position group on 9, a position group under a
    ``player`` kind on 1. One player is never carried in the plural slot,
    two never in the singular, and a list of teams never at all (players:
    1,439 one, 122 two, 1 three; teams: 382 one), so the count says which
    slot a name was carried in (:meth:`to_slots`). No cell: no relation table
    declares, honors or refuses a name - a reader narrows to whom it is
    about, and a name nothing resolves is the relation's refusal
    (:attr:`CELLS`).

    .. versionadded:: 6.0.0
    """

    kind: SubjectKind = "everyone"
    players: tuple[str, ...] = ()
    teams: tuple[str, ...] = ()
    position: str | None = None

    #: The family's cells: none. Who a read is about is what the relation
    #: reads, not a narrowing its cell table could honor or refuse; a
    #: position group narrows the league's own read (``league_games``'s
    #: ``position``) and is honored or not by the point reader, by shape.
    CELLS: ClassVar[frozenset[str]] = frozenset()

    def __post_init__(self) -> None:
        """Hold the kind to :data:`SubjectKind`, each name to non-blank text
        and the position to a group :data:`~association.query.lexicon.POSITION_GROUPS`
        holds."""
        if self.kind not in _SUBJECT_KINDS:
            raise ScopeError(f"subject kind {self.kind!r} is not one of {sorted(_SUBJECT_KINDS)}")
        for name, names in (("players", self.players), ("teams", self.teams)):
            if not isinstance(names, tuple) or not all(isinstance(each, str) and each.strip() for each in names):
                raise ScopeError(f"subject {name}={names!r} is not a tuple of names")
        if self.position is not None and self.position not in lexicon.POSITION_GROUPS:
            raise ScopeError(f"subject position {self.position!r} is not one of {sorted(lexicon.POSITION_GROUPS)}")

    @property
    def player(self) -> str | None:
        """The one player the read is about - None where it names none, or
        two or more (a comparison, a pair)."""
        return self.players[0] if len(self.players) == 1 else None

    @property
    def team(self) -> str | None:
        """The one team the read is about or narrows to - None where it
        names none, or two or more."""
        return self.teams[0] if len(self.teams) == 1 else None

    @property
    def named(self) -> bool:
        """Whether a player or a team is named at all."""
        return bool(self.players or self.teams)

    @classmethod
    def from_slots(cls, slots: Mapping[str, Any]) -> Subject:
        """The subject four slot values name (:meth:`Scope.from_slots` reads
        them off a slot dict): ``player`` then ``players`` as the players,
        ``team`` then ``teams`` as the teams, each name once, the kind
        what they hold - two or more players a ``pair``, one a ``player``,
        then two or more teams ``teams``, one a ``team``, else
        ``everyone``. A position is no slot: a reading writes it."""
        players = tuple(dict.fromkeys(_slot_names(slots.get("player")) + _slot_names(slots.get("players"))))
        teams = tuple(dict.fromkeys(_slot_names(slots.get("team")) + _slot_names(slots.get("teams"))))
        return cls(kind=_kind_named(players, teams), players=players, teams=teams)

    def to_slots(self) -> dict[str, Any]:
        """The four slots this value was recorded as until Phase 3, step 2,
        each only where set: one player as ``player`` and two or more as
        ``players``, one team as ``team`` and two or more as ``teams`` (the
        parser filled them so; a slot dict that listed ONE name in a
        plural slot, or split two teams between ``team`` and ``teams``,
        comes back as the count says)."""
        out: dict[str, Any] = {}
        if len(self.players) == 1:
            out["player"] = self.players[0]
        elif self.players:
            out["players"] = list(self.players)
        if len(self.teams) == 1:
            out["team"] = self.teams[0]
        elif self.teams:
            out["teams"] = list(self.teams)
        return out

    def slot_record(self) -> dict[str, Any]:
        """Every one of the four slots, at its default or not, in field
        order (:meth:`Scope.projected`)."""
        slots = self.to_slots()
        return {"player": slots.get("player"), "players": tuple(slots.get("players", ())), "team": slots.get("team"), "teams": tuple(slots.get("teams", ()))}

    def without_position(self) -> Subject:
        """This subject with no position group: what a point that honors
        none reads (``point._read_point``)."""
        return replace(self, position=None) if self.position is not None else self


_SUBJECT_KINDS: frozenset[str] = frozenset(get_args(SubjectKind))


def _slot_names(raw: Any) -> tuple[str, ...]:
    """The names one slot value holds - a name, or a list of names - each
    stripped of nothing, blanks left out (a blank is the slot absent)."""
    if raw is None:
        return ()
    names = [raw] if isinstance(raw, str) else list(raw)
    return tuple(name for name in names if isinstance(name, str) and name.strip())


def _kind_named(players: tuple[str, ...], teams: tuple[str, ...]) -> SubjectKind:
    """The kind a slot dict's names say, a player before a team (the
    reading's own precedence: a named subject is the narrower claim)."""
    if len(players) >= 2:
        return "pair"
    if players:
        return "player"
    if len(teams) >= 2:
        return "teams"
    if teams:
        return "team"
    return "everyone"


@dataclass(frozen=True)
class Claim:
    """The characters of the question one reader rule read - ``start`` and
    ``end`` as a slice of the question, and ``what`` it read them as
    (``season``, ``season_type``, ``career``, ``range``, ``intent``, ...).
    A tagger claims each span it read once, cut to the words its reading
    depends on (:func:`~association.query.span.needed`); a rule over the
    whole question claims the words its decision turned on
    (:func:`~association.query.span.read_by`). Two claims over one word fold
    or join into one (:func:`~association.query.span.claimed`), and the
    content words nothing claimed are the Reading's :attr:`Reading.unread`.

    .. versionadded:: 6.0.0
    """

    start: int
    end: int
    what: str


@dataclass(frozen=True, kw_only=True)
class Scope:
    """What narrows the answer, one typed field per scoping slot. A field at
    its default (None, empty, False) is the slot absent - the reading every
    reader already takes of a falsy slot (:meth:`cells` asks whether each
    typed value sets its cells, and the split whether it is truthy). The names are here too, as the question gave them;
    resolving them against the warehouse happens where each is read.

    .. versionadded:: 5.0.0
    """

    #: Who the read is about: one typed value (:class:`Subject` - the kind,
    #: the players, the teams, the position group; the four slots
    #: ``player``, ``players``, ``team`` and ``teams`` and the point's
    #: ``position`` until Phase 3, step 2), written by the subject reading
    #: alone.
    subject: Subject = field(default_factory=Subject)
    #: Which games of the span: one typed value (:class:`Cuts`; the eight
    #: slots ``opponent``, ``own_team``, ``venue``, ``date``, ``situation``,
    #: ``round``, ``game_n`` and ``season_n`` until Phase 3, step 2).
    cuts: Cuts = field(default_factory=Cuts)
    #: Who stands beside the subject, each with his role: one typed value
    #: per player (:class:`Companion`; the three slots ``with_player``,
    #: ``without`` and ``conditions`` until Phase 3, step 2), written by the
    #: subject reading alone.
    companions: tuple[Companion, ...] = ()
    #: The lines on a stat the subject's games are kept past (:class:`Line`;
    #: the ``threshold`` slot beside ``stat``, the ``above`` and ``below``
    #: phrases and the ``period_condition`` until Phase 3, step 2), in the
    #: question's order.
    lines: tuple[Line, ...] = ()
    #: What is measured: one typed value (:class:`Measure`; the seven slots
    #: ``stat``, ``fields``, ``per_game``, ``rate``, ``side``, ``shot_value``
    #: and ``kind`` until Phase 3, step 2), None where the question says
    #: nothing about its measure (a line's own measure is the :class:`Line`'s).
    measure: Measure | None = None
    #: When: the seasons and the season type, one typed value
    #: (:class:`Span`; six slots until Phase 3, step 2).
    span: Span = field(default_factory=Span)
    split: Split | None = None
    #: What a read sees of each game: one typed value (:class:`Period`, a
    #: quarter or a half; the two slots ``period`` and ``half`` until
    #: Phase 3, step 2), None for the whole game.
    period: Period | None = None
    #: Which rows are kept, and from which end: one typed value
    #: (:class:`Window`; the four slots ``order``, ``limit``, ``rank`` and
    #: ``ranked_by`` until Phase 3, step 2).
    window: Window = field(default_factory=Window)

    #: Every cell a scope can set, by name: each typed family's
    #: (:attr:`Span.CELLS`, :attr:`Window.CELLS`, :attr:`Cuts.CELLS`,
    #: :attr:`Period.CELLS`, :attr:`Line.CELLS`, :attr:`Companion.CELLS`,
    #: :attr:`Measure.CELLS`) and the split's one, ``split`` - the role
    #: family's, whose value (:data:`Split`, a closed set of names) is not
    #: typed yet; a subject is no cell (:attr:`Subject.CELLS`). What the two
    #: relation tables and each shape's row speak of, and nothing else.
    CELLS: ClassVar[frozenset[str]] = Span.CELLS | Window.CELLS | Cuts.CELLS | Period.CELLS | Line.CELLS | Companion.CELLS | Measure.CELLS | {"split"}

    @classmethod
    def from_slots(cls, slots: Mapping[str, Any]) -> Scope:
        """The scope a slot dict names. An empty value (None, "", an empty
        list, False) is the slot absent; a key nothing here types, or a value
        of the wrong type or outside its closed set, raises - a slot this
        could only drop is a narrowing the answer would silently leave out.

        .. versionadded:: 5.0.0
        """
        unknown = sorted(
            set(slots)
            - _SCOPE_FIELDS
            - frozenset(_SUBJECT_SLOT_NAMES)
            - _SPAN_SLOT_NAMES
            - _WINDOW_SLOT_NAMES
            - _CUT_SLOT_KEYS
            - _PERIOD_SLOT_NAMES
            - _LINE_SLOT_NAMES
            - _COMPANION_SLOT_NAMES
            - _MEASURE_SLOT_NAMES
        )
        if unknown:
            raise ScopeError(f"no scope field for slot(s) {unknown}")
        values: dict[str, Any] = {}
        typed_slots: dict[str, dict[str, Any]] = {"subject": {}, "span": {}, "window": {}, "cuts": {}, "period": {}, "lines": {}, "companions": {}, "measure": {}}
        for name, raw in slots.items():
            # A blank string is the slot absent too: the model files " " for
            # an opponent it has none of, and every template read it as
            # nobody (`_optional_team`), where the relation's resolver read
            # it as a team named nothing and refused.
            if raw is None or raw is False or (isinstance(raw, (str, list, tuple)) and not raw) or (isinstance(raw, str) and not raw.strip()):
                continue
            family, whole = _typed_family(name, raw)
            if whole:
                values[name] = raw
            elif family is None:
                values[name] = _CHECKS[name](name, raw)
            else:
                typed_slots[family][name] = _CHECKS[name](name, raw)
        for family, door in (
            ("subject", Subject.from_slots),
            ("measure", Measure.from_slots),
            ("span", Span.from_slots),
            ("window", Window.from_slots),
            ("cuts", Cuts.from_slots),
            ("period", Period.from_slots),
            ("lines", _lines_from_slots),
            ("companions", _companions_from_slots),
        ):
            if typed_slots[family]:
                if family in values:
                    raise ScopeError(f"a typed {family} and the slot(s) {sorted(typed_slots[family])} at once")
                # The lines' door reads the keyed line's stat off the measure.
                values[family] = door({**typed_slots[family], **({"stat": typed_slots["measure"].get("stat")} if family == "lines" else {})})
        return cls(**values)

    def to_slots(self, *, split_by_presence: bool = False) -> dict[str, Any]:
        """The Scope as a slot dict - every field away from its default,
        sequences as lists: the shape a route is recorded in and the trace
        prints (:meth:`Reading.describe`). The cuts' eight slots print where
        the fields stood until Phase 3, step 2 (:data:`_CUT_SLOT_POSITIONS`),
        the lines' four and the companions' three where theirs stood
        (:data:`_LINE_SLOT_POSITIONS`, :data:`_COMPANION_SLOT_POSITIONS`),
        so the trace line reads as it did. ``split_by_presence`` is whether
        the point divides the games by the companions' presence (the
        with/without split): there a teammate who PLAYED is a name the split
        is by (``with_player``), everywhere else a condition on the games
        (``conditions``) - the one place the slot a value printed under
        depended on the shape, which the holder says
        (:meth:`Reading.projected`, ``Route.projected``, ``Query.projected``).

        .. versionadded:: 5.0.0

        .. versionchanged:: 6.0.0
           Takes ``split_by_presence`` (Phase 3, step 2, the companions).
        """
        out: dict[str, Any] = {}
        cut_slots = self.cuts.to_slots()
        line_slots = self.line_slots()
        companion_slots = self.companion_slots(split_by_presence=split_by_presence)
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name in ("cuts", "lines", "companions"):
                continue
            if f.name == "subject":
                # The four slots where the fields stood, the cuts' and the
                # companions' attached after them.
                out.update(value.to_slots())
            elif not (value is None or value is False or value == ()):
                if f.name in ("span", "window", "period"):
                    out.update(value.to_slots())
                elif f.name == "measure":
                    # The seven slots, with the lines' three attached after
                    # the first of them, where they stood.
                    measure_slots = value.to_slots()
                    for slot in ("stat", "fields"):
                        if slot in measure_slots:
                            out[slot] = measure_slots[slot]
                        if slot == "stat":
                            out.update(_attached_line_slots(line_slots))
                    out.update({slot: measure_slots[slot] for slot in ("per_game", "rate", "side", "shot_value", "kind") if slot in measure_slots})
                    continue
                else:
                    out[f.name] = list(value) if isinstance(value, tuple) else value
            elif f.name == "measure":
                out.update(_attached_line_slots(line_slots))
                continue
            for family, slot in _ATTACHED_SLOT_POSITIONS.get(f.name, ()):
                held = {"cuts": cut_slots, "companions": companion_slots, "lines": line_slots}[family]
                if slot in held:
                    out[slot] = [c.to_slot() for c in held[slot]] if slot == "conditions" else held[slot]
        return out

    def line_slots(self) -> dict[str, Any]:
        """The four slots the subject's lines were recorded as until Phase 3,
        step 2, exactly as the stages wrote them: ``threshold``, the first
        line the threshold grammar read (a count's, a record's, a streak's or
        a high's own); ``above``, the floors of minutes and - where the words
        hold two or more - the "N+ stat" pairs, as typed; ``below``, the
        lines kept under a number, as typed; ``period_condition``, the one
        line in a quarter or half. The projection every recorded reading is
        compared through."""
        out: dict[str, Any] = {}
        whole = [line for line in self.lines if line.period is None]
        keyed = next((line for line in whole if line.keyed), None)
        if keyed is not None:
            out["threshold"] = keyed.value
        narrowing = [line for line in whole if line.narrows]
        above = [line.as_typed for line in narrowing if line.op == ">=" and not line.pair] + [line.as_typed for line in narrowing if line.pair]
        if above:
            out["above"] = above
        below = [line.as_typed for line in narrowing if line.below]
        if below:
            out["below"] = below
        in_period = next((line for line in self.lines if line.period is not None), None)
        if in_period is not None:
            out["period_condition"] = in_period.to_slot()
        return out

    def companion_slots(self, *, split_by_presence: bool = False) -> dict[str, Any]:
        """The three slots the companions were recorded as until Phase 3,
        step 2, exactly as the two writers wrote them: ``without``, the
        teammates absent; ``with_player``, the teammates who played, on the
        with/without split alone (``split_by_presence``) and only where none
        is absent (the split divides by the absent ones then, and the
        players who played went unwritten); ``conditions``, every companion
        on the other side first (the parser's own writing), then each own-side
        role but an absence - and a teammate who played, where the point is
        not the split."""
        out: dict[str, Any] = {}
        without = [c.player for c in self.companions if c.absent]
        if without:
            out["without"] = without
        played = [c.player for c in self.companions if c.side == "own" and c.predicate == "played"]
        if split_by_presence and played and not without:
            out["with_player"] = played
        other_side = [c for c in self.companions if c.side == "opponent"]
        own = [c for c in self.companions if c.side == "own" and c.predicate != "absent" and (c.predicate != "played" or not split_by_presence)]
        if other_side or own:
            out["conditions"] = [*other_side, *own]
        return out

    def projected(self, *, split_by_presence: bool = False) -> dict[str, Any]:
        """Every field, at its default or not, with the span as the six
        slots, the window as the four, the cuts as the eight, the period
        as the two (``period``, ``half``), the lines as the four
        (``threshold``, ``above``, ``below``, ``period_condition``) and the
        companions as the three (``with_player``, ``without``,
        ``conditions``) they were until Phase 3, step 2, in the field order
        they stood in - the shape a recorded Scope kept
        (:func:`~association.query.stages.plain`), so a reading recorded
        before a part was typed compares identical to one recorded after.
        The typed values are recorded beside the reading
        (``stages._reading_record``: ``span``, ``window``, ``cuts``,
        ``period``, ``lines``, ``companions``), never here.
        ``split_by_presence`` as :meth:`to_slots` takes it.

        .. versionadded:: 6.0.0
        """
        out: dict[str, Any] = {}
        cut_slots = self.cuts.to_slots()
        line_slots = self.line_slots()
        companion_slots = self.companion_slots(split_by_presence=split_by_presence)
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name in ("lines", "companions"):
                continue
            if f.name == "subject":
                out.update(value.slot_record())
            elif f.name == "span":
                slots = value.to_slots()
                out["season"] = slots.get("season")
                out["season_type"] = slots.get("season_type")
                out["season_type_unstated"] = bool(slots.get("season_type_unstated"))
                out["span"] = slots.get("span")
                out["since"] = slots.get("since")
                out["until"] = slots.get("until")
            elif f.name == "window":
                out["order"] = value.order
                out["limit"] = value.count
            elif f.name == "period":
                slots = value.to_slots() if value is not None else {}
                out["period"] = slots.get("period")
                out["half"] = slots.get("half")
            elif f.name == "measure":
                # The seven slots where the fields stood - the lines' three
                # after `stat`, the window's `ranked_by` after `fields` and
                # its `rank` after `kind`, as the record held them.
                measure_slots = value.projected() if value is not None else _NO_MEASURE_RECORD
                for slot in ("stat", "fields", "per_game", "rate", "side", "shot_value", "kind"):
                    out[slot] = measure_slots[slot]
                    if slot == "stat":
                        for attached in ("threshold", "above", "below"):
                            out[attached] = _projected_slot(self, "lines", attached, cut_slots, companion_slots, line_slots)
                    elif slot == "fields":
                        out["ranked_by"] = self.window.by
                    elif slot == "kind":
                        out["rank"] = self.window.rank
                continue
            elif f.name != "cuts":
                out[f.name] = value
            for family, slot in _ATTACHED_SLOT_POSITIONS.get(f.name, ()):
                out[slot] = _projected_slot(self, family, slot, cut_slots, companion_slots, line_slots)
        return out

    def cells(self) -> frozenset[str]:
        """Every cell this scope sets (:attr:`CELLS`): what the shape a point
        is planned as must state, or its relation's table carry, or the
        planner refuse or the reader step aside for
        (:func:`~association.query.compose.plan.cells_unhonored`).

        .. versionchanged:: 6.0.0
           Every family's cells and the split's (Phase 3, step 2's closing
           slice); the line family's alone until then, the rest read beside
           it by ``unhonored_cells`` and ``cell_set`` family by family.
        """
        held = set(self.span.cells()) | self.window.cells() | self.cuts.cells() | self._line_cells()
        if self.period is not None:
            held |= self.period.cells()
        if self.measure is not None:
            held |= self.measure.cells()
        if self.split:
            held.add("split")
        return frozenset(held)

    def _line_cells(self) -> frozenset[str]:
        """The line family's cells this scope sets (:attr:`Line.CELLS`,
        :attr:`Companion.CELLS`), read off the slots they were recorded as."""
        held = set()
        slots = self.line_slots()
        if "above" in slots or "below" in slots:
            held.add("line")
        if "period_condition" in slots:
            held.add("period_line")
        if self.companions:
            held.add("companion")
        return frozenset(held)


def companion_slot_names(scope: Scope) -> list[str]:
    """The slot names (``without``, ``conditions``) the companions on
    ``scope`` were declared and refused under - the names a decline says,
    in the order the slot list said them.

    .. versionadded:: 6.0.0
    """
    slots = scope.companion_slots()
    return [name for name in ("without", "conditions") if name in slots]  # a teammate who played is the split's name or a condition: a cell either way


def line_slot_names(scope: Scope, cell: str) -> list[str]:
    """The slot names (``below``, ``above``; ``period_condition``) the lines
    on ``scope`` set under ``cell`` were declared and refused under.

    .. versionadded:: 6.0.0
    """
    slots = scope.line_slots()
    return [name for name in _LINE_CELL_SLOT_NAMES[cell] if name in slots]


def cell_slots(scope: Scope, cell: str) -> list[str]:
    """The slot names ``cell`` is set under on ``scope`` - the names a
    decline says ("cannot honor ['since', 'until']"), each cell by the slots
    it was declared under until Phase 3, step 2 (:meth:`Span.unhonored`,
    :meth:`Window.unhonored`, :meth:`Cuts.unhonored`, :meth:`Period.unhonored`,
    :func:`line_slot_names`, :func:`companion_slot_names`; the unit and the
    split by their own names), in the order the slot list said them; none
    where the cell is not set. Until the decline-to-Cause commit rewords the
    declines, this is how a typed cell is said.

    .. versionadded:: 6.0.0
    """
    if cell not in scope.cells():
        return []
    if cell in Span.CELLS:
        return scope.span.unhonored(Span.CELLS - {cell})
    if cell in Window.CELLS:
        return scope.window.unhonored(Window.CELLS - {cell})
    if cell in Cuts.CELLS:
        return scope.cuts.unhonored(Cuts.CELLS - {cell})
    if cell in Period.CELLS and scope.period is not None:
        return scope.period.unhonored(frozenset())
    if cell in Line.CELLS:
        return line_slot_names(scope, cell)
    if cell in Companion.CELLS:
        return companion_slot_names(scope)
    return [cell]


def _lines_from_slots(slots: Mapping[str, Any]) -> tuple[Line, ...]:
    """The lines four slot values name (:meth:`Scope.from_slots` reads them
    off a slot dict): the ``above`` and ``below`` phrases as the lines their
    words read as (through the phrase reader every reader of them went
    through), a ``threshold`` beside the ``stat`` as the shape's own keyed
    line, and the ``period_condition``. A phrase whose words name no stat is
    a line on no measure, which the relation refuses by its words as it did;
    a phrase carrying the keyed line's own number beside it is read as the
    count's reader reads the pair (the phrase IS the count)."""
    from association.query.lines import phrase_line  # the reader of a phrase's words, beside the lexicon

    lines: list[Line] = [phrase_line(str(phrase), below=True) for phrase in _as_list(slots.get("below"))]
    lines += [phrase_line(str(phrase), below=False) for phrase in _as_list(slots.get("above"))]
    threshold = slots.get("threshold")
    if threshold is not None:
        from association.query.measure import column_of  # the catalog; `measure` imports this module's types

        stat = slots.get("stat")
        lines.insert(0, Line(measure=column_of(stat) if isinstance(stat, str) else None, value=threshold, as_typed=f"{threshold} {stat}" if isinstance(stat, str) else str(threshold), keyed=True))
    in_period = slots.get("period_condition")
    if in_period is not None:
        lines.append(Line.from_period_slot(in_period))
    return tuple(lines)


def _as_list(raw: Any) -> list[Any]:
    if raw is None:
        return []
    return [raw] if isinstance(raw, str) else list(raw)


def _companions_from_slots(slots: Mapping[str, Any]) -> tuple[Companion, ...]:
    """The companions three slot values name (:meth:`Scope.from_slots` reads
    them off a slot dict): the ``conditions`` entries as typed, the
    ``without`` names as absent teammates, the ``with_player`` names as
    teammates who played (beside a role the same name holds in
    ``conditions``, as the two slots held them: the split divides by the
    one and narrows by the other) - the other side's first, as the slots
    held them."""
    found: list[Companion] = [Companion.from_slot(entry) for entry in _as_list(slots.get("conditions"))]
    found += [Companion(player=name, predicate="absent") for name in _as_list(slots.get("without"))]
    found += [Companion(player=name, predicate="played") for name in _as_list(slots.get("with_player"))]
    other_side = [c for c in found if c.side == "opponent"]
    return (*other_side, *(c for c in found if c.side == "own"))


def _typed_family(name: str, raw: Any) -> tuple[str | None, bool]:
    """Which typed value the slot ``name`` belongs to at the door, and
    whether ``raw`` IS that value whole: ``("span", True)`` for a
    :class:`Span` under ``span``, ``("span", False)`` for the span's slot
    ``season`` (and ``span``, the slot, as a string), the window's and the
    cuts' and the period's the same, ``(None, False)`` for a field of the
    Scope's own."""
    whole = _WHOLE_VALUES.get(name)
    if whole is not None and isinstance(raw, whole):
        return name, True
    if name in ("lines", "companions") and isinstance(raw, (tuple, list)) and all(isinstance(each, Line if name == "lines" else Companion) for each in raw):
        return name, True
    for slot_names, family in _FAMILY_SLOT_NAMES:
        if name in slot_names:
            return family, False
    return None, False


#: Where each typed family's slot stood among the Scope's fields until Phase
#: 3, step 2 - ``(family, slot)`` emitted after the named field, in order.
#: The opponent and the tenure followed ``teams`` (the subject's last slot,
#: so they follow the typed subject), then the companions' three; the lines' ``threshold``, ``above`` and ``below`` followed
#: ``stat`` (the measure's first slot, emitted by the measure's own branch);
#: the date and the situation followed the span; a game of a
#: series, an ordinal season and a round followed the split; the line in a
#: quarter followed the period, and the venue it.
_ATTACHED_SLOT_POSITIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "subject": (("cuts", "opponent"), ("cuts", "own_team"), ("companions", "with_player"), ("companions", "without"), ("companions", "conditions")),
    "span": (("cuts", "date"), ("cuts", "situation")),
    "split": (("cuts", "game_n"), ("cuts", "season_n"), ("cuts", "round")),
    "period": (("lines", "period_condition"), ("cuts", "venue")),
}


#: Every measure slot at its default, for a Scope with no measure.
_NO_MEASURE_RECORD: dict[str, Any] = {"stat": None, "fields": (), "per_game": False, "rate": None, "side": None, "shot_value": None, "kind": None}


def _attached_line_slots(line_slots: dict[str, Any]) -> dict[str, Any]:
    """The lines' three slots that followed ``stat`` (``threshold``,
    ``above``, ``below``), each where set, for :meth:`Scope.to_slots`."""
    return {slot: line_slots[slot] for slot in ("threshold", "above", "below") if slot in line_slots}


def _projected_slot(scope: Scope, family: str, slot: str, cut_slots: dict[str, Any], companion_slots: dict[str, Any], line_slots: dict[str, Any]) -> Any:
    """One attached slot as the full-field projection recorded it: a cut as
    its value or None; a companion slot as the tuple it was, its
    ``conditions`` entries with every field; a line slot as its value, the
    phrase tuples empty by default, a ``period_condition`` with every field."""
    if family == "cuts":
        return cut_slots.get(slot)
    if family == "companions":
        if slot not in companion_slots:
            return ()
        return tuple(c.to_record() for c in companion_slots[slot]) if slot == "conditions" else tuple(companion_slots[slot])
    if slot == "period_condition":
        in_period = next((line for line in scope.lines if line.period is not None), None)
        return in_period.to_period_record() if in_period is not None else None
    if slot == "threshold":
        return line_slots.get("threshold")
    return tuple(line_slots[slot]) if slot in line_slots else ()


def cell_set(scope: Scope, cell: str) -> bool:
    """Whether ``scope`` sets ``cell`` (:meth:`Scope.cells`) - a cut by its
    cell name (:attr:`Cuts.CELLS`: ``tenure``, not ``own_team``), every other
    cell by its name: the one reading of a cell name against a Scope, for a
    reader that lists the relation's cells it reads apart (the shot
    readers' narrowing check) and the planner's one check
    (:func:`~association.query.compose.plan.cells_unhonored`).

    .. versionadded:: 6.0.0
    """
    return cell in scope.cells()


def _text(name: str, raw: Any) -> str:
    if not isinstance(raw, str):
        raise ScopeError(f"scope {name}={raw!r} is not text")
    return raw


def _texts(name: str, raw: Any) -> tuple[str, ...]:
    # A bare string is one item: the readers have always taken it so
    # (``entities.teammate_names``: slot values are advisory, and a
    # reader of one shape would be one stray route from answering nothing).
    if isinstance(raw, str):
        return (raw,)
    if not isinstance(raw, (list, tuple)) or not all(isinstance(item, str) for item in raw):
        raise ScopeError(f"scope {name}={raw!r} is not a list of text")
    return tuple(raw)


def _iso_day(name: str, raw: Any) -> str:
    # One calendar day as the cuts tagger writes it (cuts._day): an
    # ISO date and nothing else - "last night" would pass a text check and
    # then fail in whichever reader got it first.
    try:
        valid = isinstance(raw, str) and len(raw) == 10 and date.fromisoformat(raw).isoformat() == raw
    except ValueError:
        valid = False
    if not valid:
        raise ScopeError(f"scope {name}={raw!r} is not a calendar day (YYYY-MM-DD)")
    return raw


def _whole(name: str, raw: Any) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ScopeError(f"scope {name}={raw!r} is not a whole number")
    return raw


def _flag(name: str, raw: Any) -> bool:
    if not isinstance(raw, bool):
        raise ScopeError(f"scope {name}={raw!r} is not a flag")
    return raw


def _conditions(name: str, raw: Any) -> list[Any]:
    if not isinstance(raw, (list, tuple)):
        raise ScopeError(f"scope {name}={raw!r} is not a list of conditions")
    return list(raw)


def _one_of(*allowed: object) -> Callable[[str, Any], Any]:
    def check(name: str, raw: Any) -> Any:
        """``raw`` when it is one of ``allowed``, else the reason it is not."""
        if isinstance(raw, bool) or raw not in allowed:
            raise ScopeError(f"scope {name}={raw!r} is not one of {allowed}")
        return raw

    return check


_CHECKS: dict[str, Callable[[str, Any], Any]] = {
    **dict.fromkeys(("player", "team", "opponent", "own_team", "stat", "ranked_by", "rate", "kind", "situation", "round"), _text),
    "date": _iso_day,
    **dict.fromkeys(("players", "teams", "with_player", "without", "above", "below", "fields"), _texts),
    **dict.fromkeys(("threshold", "season", "since", "until", "game_n", "season_n", "period", "limit"), _whole),
    **dict.fromkeys(("per_game", "season_type_unstated"), _flag),
    "conditions": _conditions,
    "period_condition": lambda name, raw: raw,
    "side": _one_of("offense", "defense", "total"),
    "shot_value": _one_of(1, 2, 3),
    "rank": _one_of("most", "fewest", "best", "worst"),
    "season_type": _one_of(2, 3),
    "span": _one_of("career"),
    "split": _one_of("home_away", "starter_bench", "wins_losses", "month", "starter", "bench"),
    "half": _one_of(1, 2),
    "venue": _one_of("home", "away"),
    "order": _one_of("recent", "first"),
}
_SCOPE_FIELDS = frozenset(f.name for f in fields(Scope))
#: The six slot names the span's door still takes (:meth:`Span.from_slots`).
_SPAN_SLOT_NAMES = frozenset({"season", "season_type", "season_type_unstated", "span", "since", "until"})
#: The four slot names the window's door still takes (:meth:`Window.from_slots`).
_WINDOW_SLOT_NAMES = frozenset({"order", "limit", "rank", "ranked_by"})
#: The eight slot names the cuts' door still takes (:meth:`Cuts.from_slots`).
_CUT_SLOT_KEYS = frozenset(_CUT_SLOT_NAMES.values())
#: The two slot names the period's door still takes (:meth:`Period.from_slots`).
_PERIOD_SLOT_NAMES = frozenset({"period", "half"})
#: The four slot names the lines' door still takes (:func:`_lines_from_slots`).
_LINE_SLOT_NAMES = frozenset({"threshold", "above", "below", "period_condition"})
#: The three slot names the companions' door still takes (:func:`_companions_from_slots`).
_COMPANION_SLOT_NAMES = frozenset({"with_player", "without", "conditions"})

#: Each typed value's field, and the type a whole value under it has.
_WHOLE_VALUES: dict[str, type] = {"subject": Subject, "span": Span, "window": Window, "cuts": Cuts, "period": Period, "measure": Measure}
#: Each family's slot names, and the family they pass the door into.
_FAMILY_SLOT_NAMES: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset(_SUBJECT_SLOT_NAMES), "subject"),
    (_SPAN_SLOT_NAMES, "span"),
    (_WINDOW_SLOT_NAMES, "window"),
    (_CUT_SLOT_KEYS, "cuts"),
    (_PERIOD_SLOT_NAMES, "period"),
    (_LINE_SLOT_NAMES, "lines"),
    (_COMPANION_SLOT_NAMES, "companions"),
    (_MEASURE_SLOT_NAMES, "measure"),
)


CAUSES: frozenset[str] = frozenset(
    {
        "shot_distance_ranking",
        "no_ranking_measure",
        "ranking_floor_unit",
        "ranking_unit",
        "needs_stat",
        "unknown_stat",
        "needs_threshold",
        "threshold_needs_stat",
        "threshold_counts_every_game",
        "line_names_no_stat",
        "needs_line",
        "career_place_needs_player",
        "needs_subject",
        "team_streak_of_stat",
        "matchup_needs_two",
        "no_period_stat",
        "no_coach_table",
        "too_short",
        "championship",
        "no_player_reading",
        "playoff_round",
        "non_calendar_situation",
        "period_stat",
        "period_as_condition",
        "team_period_stat",
        "bench_points",
        "team_boolean_count",
        "cannot_honor",
        "no_reading_of_point",
    }
)
"""The closed set of causes a point reading refuses by (:class:`Cause.kind`),
each named for the fact that is missing (AGENTS.md, "A refusal names the
missing thing, never only the slot"):

- a league-wide ranking by shot distance, which nothing ranks
  (``shot_distance_ranking``); a ranking by a named stat the relation has
  no measure for (``no_ranking_measure``: ``stat``); a ranking floor in a
  unit no ranking applies (``ranking_floor_unit``: ``unit``, ``count``); a
  season-line ranking in a unit its metric has no form of
  (``ranking_unit``: ``metric``, ``rate``);
- a shape over a line on a stat - a single-game high, a record or a count
  of games over a line, a streak, a log kept past a number - with no stat
  read (``needs_stat``: ``intent``), a stat with no per-game column
  (``unknown_stat``: ``intent``, ``stat``), a stat with no number
  (``needs_threshold``: ``intent``, ``stat``), a number with no stat
  (``threshold_needs_stat``: ``intent``, ``threshold``), a number every
  game clears (``threshold_counts_every_game``: ``intent``,
  ``threshold``), or a below/above phrase naming no stat
  (``line_names_no_stat``: ``phrase``, ``side``);
- a league-wide count with no line read at all (``needs_line``); an
  ordinal season with no player to be a place in (``career_place_needs_player``:
  ``season_n``); a record over a line with neither a player nor a team
  (``needs_subject``: ``intent``); a team's run of a stat line
  (``team_streak_of_stat``: ``stat``); a matchup without exactly two
  players (``matchup_needs_two``: ``names``); a quarter's or half's figure
  the period's line does not rebuild (``no_period_stat``: ``stat``);
- a question about a coach, which no table here holds (``no_coach_table``:
  ``unanswerable``, the shape nothing reads);
- a question too short to be one, refused unread before the normalizer is
  asked (``too_short``: ``asked``, the words as typed - the parser's
  :func:`~association.query.parse.too_short`);
- a refusal the words come to before any reader runs
  (:attr:`Reading.refused`): a championship, which no table holds
  (``championship``: ``intent``), or a player named on a question whose
  shape has no reading for one (``no_player_reading``: ``player``,
  ``intent``);
- what the words name and nothing reads (:attr:`Reading.unsupported`,
  said only where the answer side declines): a playoff round
  (``playoff_round``: ``intent``, ``round``), a situation nothing reads
  (``non_calendar_situation``: ``intent``, ``situation``, ``reads_as`` -
  ``age``, ``alignment`` or ``other``), a stat a quarter's line does not
  rebuild on a period shape (``period_stat``: ``intent``, ``stat``,
  ``period``, ``half``), a quarter as a condition with no line
  (``period_as_condition``: ``intent``), a team's quarter of a stat
  nothing holds (``team_period_stat``: ``intent``, ``stat``), bench points
  (``bench_points``: ``intent``), a team's total of its players'
  triple-doubles (``team_boolean_count``: ``intent``, ``stat``). Each was
  ``refusals.unanswerable``'s check, asked by the answering loop with the
  question until Phase 3, step 0; their ``intent`` is what the page shows
  beside the sentence (``refused`` and ``intent``, as it did);
- a narrowing the point's shape cannot honor beyond its relation's cells
  (``cannot_honor``: ``intent``, the shape's retired ``name``, the
  ``slots`` it sets, ``without`` - ``player`` or ``team or player`` where
  the cell needs a named subject the point has none of - and ``why``, the
  reason its shape's row gives, as the row words it), and a point a
  shape's only reader reads nothing of (``no_reading_of_point``:
  ``intent``, ``name``). Declines the user saw as "Nothing here answers
  this question: ..." until Phase 3, step 4; said in that sentence, word
  for word, and only where the floor and the reading's own causes did
  not speak first, as the decline was (:class:`ShapeDeclined`).

The planner says each (``compose.plan.refusal_result``); a new cause is an
entry here and a sentence there.

.. versionadded:: 5.0.0
"""


@dataclass(frozen=True, kw_only=True)
class Cause:
    """Why a point reading refuses: a kind from :data:`CAUSES` and the plain
    facts its sentence needs - never the sentence, which is the answer
    side's to build (``ROADMAP-TYPES.md``: ``Refusal(cause, facts)``). Until
    5.0.0's last change the reader built the refusal's ``Reply``
    itself and the Reading carried it (``ROADMAP.md``, Phase 1, the
    ``read_point`` move, step 4).

    .. versionadded:: 5.0.0
    """

    kind: str
    facts: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Hold ``kind`` to :data:`CAUSES`."""
        if self.kind not in CAUSES:
            raise ValueError(f"{self.kind!r} is not a cause a point reading refuses by: {sorted(CAUSES)}")


@dataclass(frozen=True, kw_only=True)
class LeftOut:
    """A question setting two players against each other ("embiid vs jolic")
    that the reading holds fewer than two players of: the players it holds
    (``held``) and the ones the question's words also name that it does not
    (``names``) - none where only one of the compared names matches anybody.
    What the answer says beside a one-polygon fingerprint, as values: the
    sentence is the sayer's (``name_left_out``, a decision).

    .. versionadded:: 6.0.0
    """

    held: tuple[str, ...]
    names: tuple[str, ...] = ()


@dataclass(frozen=True)
class PointShape:
    """What a planned point is read and said as (``ROADMAP-TYPES.md``,
    "Where today's 25 intents land": the draft's ``Query.relation``,
    ``shape`` and ``by``): the ``relation`` it reads (:data:`PointRelation`),
    its ``shape`` (:data:`Shape`) and what one row is ``by`` (or, for a
    scalar, how it is reduced; ``""`` for a point no reader takes). The
    answer side chooses a reader, its stated scoping and the coverage
    floor by this and by nothing else (``compose.answer``, ``coverage``).
    Built by the planner from the point's own :attr:`Reading.on`,
    :attr:`Reading.shape` and :attr:`Reading.by`, the one place it differs
    being a season-line point re-planned at the game level
    (:func:`~association.query.compose.plan.point_shape`).

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       In ``reading``, built from the point's own fields; ``compose.plan``'s,
       translated from the intent by ``shape_of``, until Phase 3, step 1.
    """

    relation: PointRelation
    shape: Shape
    by: str = ""


# What a question's words ask for (Phase 3, step 4): the shape the grammar
# names - the parent grammar by the subject's kind
# (``parse.PARENT_GRAMMAR``), the child grammars (``subject._CHILD_GRAMMARS``)
# and the stages before the taggers (``router._settle_stages``) - as the
# point it means before any typed filter moves it, in the target's own
# vocabulary: the relation its reader reads first, the shape and what it is
# ``by``. One key per retired intent, the intent's own default point's (a
# player's, where a team's reads another relation: the point reader moves a
# team's log to ``team_games``, an unnarrowed line stays on the season line
# and a narrowed one moves to the games), so the page's label is one lookup
# (:data:`SHAPE_NAMES`). Measured first (``~/association-research/stages/
# intent_family.py``, the 2,710 readings): an intent maps to up to four
# planned keys, since ``on`` and sometimes the shape follow the typed
# filters, and two planned keys are reached by more than one intent - so
# the key is what the words ASK, and the planned key what the point IS.
# ``None`` is the grammar's "no shape": the words name nothing a reader
# reads (``other``).

PLAYER_LOG = PointShape("player_games", "rows", "date")
"""A player's games, newest first (``game_log``): a team's log moves to
``team_games``, the league's or a position group's stays here.

.. versionadded:: 6.0.0
"""
PLAYER_LINE = PointShape("player_seasons", "scalar", "line")
"""A player's line (``player_stat``): the season line unnarrowed, his games
narrowed or windowed.

.. versionadded:: 6.0.0
"""
PLAYER_SPLITS = PointShape("player_games", "split", "splits")
"""A player's (or a team's) four splits (``player_splits``).

.. versionadded:: 6.0.0
"""
LINE_RECORD = PointShape("player_games", "split", "line")
"""A record above and below a line (``record_when``).

.. versionadded:: 6.0.0
"""
PERIOD_LOG = PointShape("player_periods", "rows", "date")
"""A player's quarter or half (``period_split``).

.. versionadded:: 6.0.0
"""
GAMES_COUNTED = PointShape("player_games", "scalar", "count")
"""A count of games over a line (``threshold_count``).

.. versionadded:: 6.0.0
"""
GAME_HIGHS = PointShape("player_games", "rows", "measure")
"""Single games ranked by a measure (``single_game_high``).

.. versionadded:: 6.0.0
"""
LINE_RUNS = PointShape("player_games", "runs", "line")
"""The longest runs of games a predicate holds along (``streak``).

.. versionadded:: 6.0.0
"""
PLAYER_MEETINGS = PointShape("player_games", "comparison", "met")
"""Two players' meetings (``player_matchup``).

.. versionadded:: 6.0.0
"""
PRESENCE_SPLIT = PointShape("team_games", "split", "presence")
"""A team's record with and without named teammates (``with_without``).

.. versionadded:: 6.0.0
"""
TEAM_MEETINGS = PointShape("team_games", "comparison", "opponent")
"""Two teams' meetings (``head_to_head``).

.. versionadded:: 6.0.0
"""
TEAM_PERIOD_TOTAL = PointShape("team_periods", "scalar", "total")
"""A team's quarter or half (``team_quarter_points``).

.. versionadded:: 6.0.0
"""
PERIOD_RANKING = PointShape("player_periods", "ranking", "player")
"""Players ranked by a quarter or half (``period_leaderboard``).

.. versionadded:: 6.0.0
"""
TEAM_RECORD = PointShape("team_games", "scalar", "record")
"""A team's record (``team_record``).

.. versionadded:: 6.0.0
"""
NETPOINTS_RATINGS = PointShape("netpoints", "scalar", "ratings")
"""A player's NetPoints (``player_netpoints``).

.. versionadded:: 6.0.0
"""
NETPOINTS_FINGERPRINT = PointShape("netpoints", "chart", "fingerprint")
"""A player's fingerprint, drawn (``fingerprint``).

.. versionadded:: 6.0.0
"""
SHOT_CHART = PointShape("shots", "chart", "shots")
"""A player's shot chart, drawn (``shot_chart``).

.. versionadded:: 6.0.0
"""
SHOT_DISTANCE = PointShape("shots", "scalar", "distance")
"""A player's average shot distance (``shot_distance``).

.. versionadded:: 6.0.0
"""
PLAYER_RANKING = PointShape("player_seasons", "ranking", "player")
"""Players ranked by a measure (``leaderboard``): the season line, or the
games where the line does not read it.

.. versionadded:: 6.0.0
"""
PLAYER_COMPARISON = PointShape("player_seasons", "comparison", "subject")
"""Two or more players' lines side by side (``player_compare``).

.. versionadded:: 6.0.0
"""
SEASON_HISTORY = PointShape("player_seasons", "split", "season")
"""A player's stat season by season (``player_history``).

.. versionadded:: 6.0.0
"""
TEAM_LINE = PointShape("team_seasons", "scalar", "line")
"""A team's own season line (``team_stat``).

.. versionadded:: 6.0.0
"""
TEAM_RANKING = PointShape("team_seasons", "ranking", "team")
"""Teams ranked by a season metric or the standings (``team_leaderboard``).

.. versionadded:: 6.0.0
"""
TEAM_OUTLOOK = PointShape("team_snapshots", "scalar", "projection")
"""A team's place in ESPN's power index (``team_outlook``).

.. versionadded:: 6.0.0
"""
TEAM_COACH = PointShape("team_seasons", "scalar", "coach")
"""A team's coach (``coach``): a fact of a team's season the warehouse holds
no column for, so its point is refused by that cause (``no_coach_table``)
and no reader takes it.

.. versionadded:: 6.0.0
"""

SHAPE_NAMES: dict[PointShape, str] = {
    PLAYER_LOG: "game_log",
    PointShape("player_games", "scalar", "line"): "player_stat",
    LINE_RECORD: "record_when",
    PLAYER_SPLITS: "player_splits",
    GAMES_COUNTED: "threshold_count",
    PointShape("player_games", "ranking", "count"): "threshold_count",
    PointShape("player_games", "rows", "count"): "threshold_count",
    GAME_HIGHS: "single_game_high",
    LINE_RUNS: "streak",
    PLAYER_MEETINGS: "player_matchup",
    PERIOD_LOG: "period_split",
    PointShape("player_periods", "split", "period"): "period_split",
    PERIOD_RANKING: "period_leaderboard",
    PLAYER_RANKING: "leaderboard",
    PLAYER_LINE: "player_stat",
    SEASON_HISTORY: "player_history",
    PLAYER_COMPARISON: "player_compare",
    PointShape("team_games", "rows", "date"): "game_log",
    PointShape("team_games", "split", "splits"): "player_splits",
    PointShape("team_games", "runs", "won"): "streak",
    PRESENCE_SPLIT: "with_without",
    PointShape("team_games", "split", "line"): "record_when",
    TEAM_MEETINGS: "head_to_head",
    TEAM_PERIOD_TOTAL: "team_quarter_points",
    TEAM_RECORD: "team_record",
    TEAM_LINE: "team_stat",
    TEAM_RANKING: "team_leaderboard",
    TEAM_OUTLOOK: "team_outlook",
    NETPOINTS_RATINGS: "player_netpoints",
    NETPOINTS_FINGERPRINT: "fingerprint",
    SHOT_CHART: "shot_chart",
    SHOT_DISTANCE: "shot_distance",
    TEAM_COACH: "coach",
}
"""The retired intent's name for a shape, and nothing else: ONE table,
keyed by the shape (:class:`PointShape`), for the two things that still
say a retired template's name - the page's label (``Answer.intent``,
through :attr:`Reading.intent`, written from the key the words asked for:
:func:`asked_label`), and a decline's sentence ("game_log has no reading of
this point", "head_to_head cannot honor [...]", keyed by the PLANNED
shape). Every shape a reader takes, and the coach's, which none does.
Phase 4 deletes it with the page's renderers (decision D3: the page renders
by shape).

.. versionadded:: 6.0.0
   In ``reading``, keyed by the asked shape as well as the planned one;
   ``compose.plan``'s, the decline's name alone, until Phase 3, step 4.
"""


def asked_label(asked: PointShape | None) -> str:
    """The retired intent's name for what the words ask
    (:data:`SHAPE_NAMES`), ``"other"`` where they name no shape - the page's
    label (``Answer.intent``) and the trace's, read nowhere on the
    answering path.

    .. versionadded:: 6.0.0
    """
    return "other" if asked is None else SHAPE_NAMES[asked]


def labeled(asked: PointShape | None, label: str) -> str:
    """``label`` checked against the key it names (:func:`asked_label`), or
    filled from it where it is empty: a label is the table's, never a second
    choice beside the key. Empty stays empty only where nothing was asked -
    a record built by hand, never read from words.

    .. versionadded:: 6.0.0
    """
    if asked is None:
        if label not in ("", "other"):
            raise ValueError(f"the label {label!r} names a shape, and nothing was asked")
        return label
    expected = asked_label(asked)
    if label not in ("", expected):
        raise ValueError(f"the label {label!r} is not the one {asked} is named by ({expected!r})")
    return expected


@dataclass(frozen=True, kw_only=True)
class ShapeCells:
    """One shape's row of its relation's cell table
    (``player_relation.RELATION_SCOPING_EXCLUDED``,
    ``team_relation.TEAM_RELATION_SCOPING_EXCLUDED``, keyed by the
    :class:`PointShape` a planned point is read and said as): what its
    reader's words state of the relation's cells, and what the planner does
    with a cell they do not. The shape states its relation's table, and
    :attr:`taken` beside it, less :attr:`unstated`
    (:func:`~association.query.compose.plan.cells_stated`); the planner's
    one check reads a point's cells against that
    (:func:`~association.query.compose.plan.cells_unhonored`), in the order
    the slot-era checks it replaced read them.

    .. versionadded:: 6.0.0
       Phase 3, step 2's closing slice: one record per shape, where the
       planner's ``STATED_SCOPING``, ``_TEAM_READER_REFUSES``, the four
       branches of ``_shape_declines``, ``unhonored_scoping``'s split rule
       and ``conditions._CONDITION_PLAYER_ONLY_CELLS`` stood.
    """

    #: The cells this shape's words do not state, each with why - a reason
    #: about the answer, never about the code - in the order a refusal lists
    #: them. Where the relation's table carries the cell and the shape
    #: neither refuses nor declines it, the reader steps aside and the
    #: compiler's own sentence, which states every narrowing the relation
    #: applied, answers.
    unstated: Mapping[str, str] = field(default_factory=dict)
    #: Of :attr:`unstated`, the cells nothing answers: the planner refuses
    #: them outright, saying the first one's why ("streak cannot honor
    #: ['date'] - one game is not a run").
    refused: frozenset[str] = frozenset()
    #: Cells beyond the relation's table the planner lets through to this
    #: shape: ones its reader reads (a season-line ranking's unit, a team
    #: record's month), or refuses in its own words, which say where the
    #: question belongs (a teammate on a team's log).
    taken: frozenset[str] = frozenset()
    #: Where a cell the words do not state is declined in the sentence
    #: "<shape> cannot honor [...] - it would answer for a different span than
    #: was asked", when this shape's reader is the point's only answer: by
    #: the planner, before any reader runs (``"plan"``), or by the reader
    #: when it is asked (``"read"``: a team's own season, whose point the team
    #: compiler's sum may still answer). None where the reader steps aside.
    declined: Literal["plan", "read"] | None = None
    #: Whether the reader honors one named half of the starter/bench split
    #: and not the bare category, a table of both halves it does not produce.
    sides: bool = False
    #: Cells only a named player's games can be narrowed by: refused where the
    #: point's subject is no named player ("... without a named player -
    #: only his own games can be narrowed that way").
    named_player: frozenset[str] = frozenset()
    #: Cells a run's games can be narrowed by only for a named team or
    #: player: refused for the league's own run ("... without a named team
    #: or player - the league-wide streak has no single subject to narrow").
    named_subject: frozenset[str] = frozenset()


@dataclass(frozen=True, kw_only=True)
class Reading:
    """One question, read. The point fields (measures, aggregate, group,
    predicates, order, direction, limit) mirror
    :class:`~association.query.compose.core.Query` on purpose: the planner
    is a copy, not a second decision; its shape, ``by`` and ``on`` are the
    target's own vocabulary, from which the planner derives the compiler's
    skeleton and source. Every construction names its fields.

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       ``shape`` takes the target's vocabulary, and ``by`` and ``on`` are
       named beside it (Phase 3, step 1); ``source`` is gone - the season
       line is ``on="player_seasons"``.
    """

    #: The scoping the relation narrows by.
    scope: Scope = field(default_factory=Scope)
    #: The point's shape, what one row is ``by`` and the relation it is read
    #: ``on`` - the three the answer side keys a reader and its stated
    #: scoping by (:class:`PointShape`), named by the point reader and
    #: never derived from the intent after it (Phase 3, step 1). ``by`` is
    #: what one row is for a grouped shape (``player``, ``season``,
    #: ``subject``, ``presence``, ...), how a scalar is reduced or what rows
    #: are ordered by for a reader's shape (``line``, ``count``, ``date``,
    #: ``measure``), and ``""`` where no reader takes the point and the
    #: compiler's own sentence states what the query reads.
    shape: Shape = "rows"
    by: str = ""
    on: PointRelation = "player_games"
    #: The measures the answer reads (box-score columns or derived measures).
    measures: list[str] = field(default_factory=list)
    aggregate: Aggregate = "none"
    group: Group = "none"
    #: Row predicates: ``(measure, op, value)``.
    predicates: list[tuple[str, str, Any]] = field(default_factory=list)
    order: Literal["date", "measure"] = "date"
    direction: Literal["asc", "desc"] = "desc"
    #: Row count for a ``rows`` read, or the number of groups for a ``grouped`` one.
    limit: int | None = None
    #: The minimum games a group needs to be kept, for a ranking.
    minimum_games: int | None = None
    #: Binding parity with the template being mirrored (see
    #: :class:`~association.query.compose.core.Query`).
    available: Availability | None = None
    #: The span the subject is settled in and his games read over, where
    #: the point reader settles it apart from the scope's own: over the
    #: career for a date that names its own game (the name settled over
    #: every season, the date the scope), this season outright for a run
    #: (never a defaulted one, so an empty run is refused and not
    #: redirected). ``None`` is the scope's own span. Until Phase 3, step 2
    #: this was the slot pair ``span``/``season`` beside the scope's.
    #:
    #: .. versionadded:: 6.0.0
    subject_span: Span | None = None
    relation: Relation = "player"
    #: What the question's words ask for (:class:`PointShape`, one of the
    #: grammar's keys - :data:`PLAYER_LOG`, :data:`PLAYER_LINE`, ...), named
    #: by the parent grammar, the child grammars or the stages, and read by
    #: the point reader, the subject reading's sets and the answering loop;
    #: ``None`` where the words name no shape. Since Phase 3, step 4 - an
    #: intent string until then.
    #:
    #: .. versionadded:: 6.0.0
    asked: PointShape | None = None
    #: The page's label for :attr:`asked` - the retired intent's name, from
    #: the one table (:data:`SHAPE_NAMES`, :func:`asked_label`), filled from
    #: the key and held to it (:func:`labeled`); read only for
    #: ``Answer.intent``, the trace and the records, never to decide. Empty
    #: on a Reading built by hand with nothing asked.
    #:
    #: .. versionchanged:: 6.0.0
    #:    The label alone: the readers key on :attr:`asked`.
    intent: str = ""
    #: Who the question is about, as the subject reading read it.
    subject: subject_reading.Subject | None = None
    #: One line per finding, for the trace.
    evidence: tuple[str, ...] = ()
    #: What the parser decided on the way, as values - who the question is
    #: about, and each name, role, tenure or position group it wrote into
    #: the scope (:func:`~association.query.parse.reading_from_route`) - kept
    #: on the answer beside the trace line each one prints.
    decisions: tuple[Decision, ...] = ()
    #: The model's names the question never held that nothing in it could
    #: replace ("Jusuf Nurkic" on "compare sga and embiid"): the agent
    #: refuses by name rather than answer about somebody the question never
    #: mentioned (AGENTS.md: "when it cannot be repaired, say so").
    misread: tuple[str, ...] = ()
    #: The characters of the question each reader rule read
    #: (:class:`Claim`), in the question's order: each tagger's since Phase
    #: 3, step 2, cut to what its reading depends on and joined by the
    #: grammars', the stages' and the point reader's since step 3. What they
    #: leave unclaimed is :attr:`unread`.
    #:
    #: .. versionadded:: 6.0.0
    claims: tuple[Claim, ...] = ()
    #: The content words of the question no claim covers, in the question's
    #: order, as the claims ledger spells a word (lowercased, its edge
    #: punctuation dropped): the words nothing read (``ROADMAP.md``,
    #: contract 2). Counted by the ledger's own rule
    #: (:func:`~association.query.lexicon.content_words` - a function word
    #: is no content word, nor a word the model's reply accounts for: a word
    #: of a name it copied or the reading settled on, a word of the stat it
    #: picked), by the parser's last step
    #: (:func:`~association.query.parse.reading_from_route`), so
    #: ``scripts/claims_ledger.py`` - which deletes each content word in
    #: turn and asks whether the reading moved - checks it from outside. An
    #: unread word is recorded, never a refusal: the roadmap measures them
    #: before anything acts on one.
    #:
    #: .. versionadded:: 6.0.0
    unread: tuple[str, ...] = ()
    #: The compiler's point for the question - its word tables' reading of it
    #: on the relation it names (:func:`~association.query.point.read_point`),
    #: read once, by the parser, so the compiler only plans and runs it
    #: (:func:`~association.query.compose.answer`). A Reading of its
    #: own, since it reads words the templates never see: its scope may carry
    #: a career the question's "ever" or "how many times" implies. ``None``
    #: where the compiler has no reading of the point - ``point_declined``
    #: says why - or a refusal of its own to give (``point_refusal``: its
    #: :class:`Cause` - a stat nothing ranks, a floor no ranking applies -
    #: which the planner says, :func:`~association.query.compose.plan.refusal_result`).
    point: Reading | None = None
    point_declined: str | None = None
    point_refusal: Cause | None = None
    #: A refusal the question's words come to that no point answers past -
    #: a championship, or a player named on a question whose shape has no
    #: reading for one (:data:`CAUSES`: ``championship``,
    #: ``no_player_reading``) - said before any reader runs, by the planner
    #: (:func:`~association.query.compose.plan.plan_point`). Read by the
    #: parser (``parse._reading_from_route_refused``); until Phase 3, step 0
    #: the answering loop re-read the question for each.
    #:
    #: .. versionadded:: 6.0.0
    refused: Cause | None = None
    #: What the words name that nothing here reads - ``ROADMAP-TYPES.md``'s
    #: ``Unsupported(what, as_typed)`` filter, carried as causes whose kind
    #: is the ``what`` and whose facts hold the words as typed (a playoff
    #: round, an age, a stat a quarter does not rebuild, bench points, ...;
    #: :data:`CAUSES`), in the order the answering loop asked
    #: ``refusals.unanswerable``'s checks. Recognized whatever answers: the
    #: first is said, by the planner's sentence, only where the answer side
    #: declined and the coverage floor did not refuse, so a question the
    #: compiler answers is answered.
    #:
    #: .. versionadded:: 6.0.0
    unsupported: tuple[Cause, ...] = ()
    #: A fingerprint's "vs" the reading holds one side of
    #: (:class:`LeftOut`): which names the question's words name beyond the
    #: players it holds, said beside the polygon it draws. Read by the
    #: parser; until Phase 3, step 0 the answering loop re-read the question
    #: for it (``entities.compared_but_unmatched``).
    #:
    #: .. versionadded:: 6.0.0
    left_out: LeftOut | None = None

    def __post_init__(self) -> None:
        """Hold :attr:`intent` to the label :attr:`asked` is named by."""
        object.__setattr__(self, "intent", labeled(self.asked, self.intent))

    @classmethod
    def from_slots(cls, slots: Mapping[str, Any], *, asked: PointShape | None = None, subject: subject_reading.Subject | None = None) -> Reading:
        """A Reading holding nothing but the scope ``slots`` names (through
        :meth:`Scope.from_slots`), for the readers that still build one from a
        slot dict: the agent's dispatch of a routed question to its template,
        a template handing a question to another, and the tests.

        .. versionadded:: 5.0.0

        .. versionchanged:: 6.0.0
           Takes ``asked`` (a :class:`PointShape`) where it took ``intent``.
        """
        return cls(scope=Scope.from_slots(slots), asked=asked, subject=subject)

    def projected(self) -> dict[str, Any]:
        """Every field as a Reading was recorded until Phase 3, step 2
        (:func:`~association.query.stages.plain`): ``subject_span`` as the
        ``span`` and ``season`` pair it replaced, the point's ``offset`` as
        the 0 it always was, its ``position`` as the position group its
        subject carries where it is the league's (the field it was), every
        other field as it is - ``claims`` and ``unread`` included, the two
        fields the record gained (Phase 3, steps 2 and 3) - but the key the
        words asked (``asked``, Phase 3, step 4), which the record holds as
        its label (``intent``), as it did.

        .. versionadded:: 6.0.0
        """
        out = {f.name: getattr(self, f.name) for f in fields(self) if f.name not in ("subject_span", "asked")}
        # The point's position group, a field of its own until Phase 3,
        # step 2: the league's read honors it, and only the point reader's
        # moves that do carry one on the point's subject
        # (``point._everyone_point``); every other Reading held None.
        out["position"] = self.scope.subject.position if self.relation == "everyone" else None
        # The scope's companions print under the with/without split's slot
        # where the point divides the games by their presence.
        out["scope"] = self.scope.projected(split_by_presence=self.asked == PRESENCE_SPLIT or self.group == "presence")
        settled = self.subject_span
        out["span"] = "career" if settled is not None and settled.career else None
        out["season"] = settled.season if settled is not None else None
        # The point's `offset`, 0 on every reading until the window was
        # typed (Phase 3, step 2), when it went.
        out["offset"] = 0
        return out

    def describe(self) -> str:
        """The one trace line: every field that decides the answer."""
        window = f"{self.order}/{self.direction}" + (f"/{self.limit}" if self.limit is not None else "")
        who = self.subject.kind if self.subject is not None else "?"
        return (
            f"relation={self.relation} subject={who} shape={self.shape} by={self.by} on={self.on} measures={self.measures} aggregate={self.aggregate} "
            f"group={self.group} predicates={self.predicates} window={window} scope={self.scope.to_slots(split_by_presence=self.asked == PRESENCE_SPLIT)}"
        )


# What the subject reading needs to know about what the words ask - moved
# here from templates/common.py on 2026-10-02 so the reader does not import
# the answer side for them (ROADMAP.md, Phase 1: the reader's imports of the
# answer side); templates.common re-exported each under its old name until
# Phase 2, step 6. Sets of intent names until Phase 3, step 4: sets of the
# grammar's keys (:class:`PointShape`) since.

PLAYER_REQUIRED_ASKS: frozenset[PointShape] = frozenset({LINE_RECORD, PERIOD_LOG, SHOT_DISTANCE, SEASON_HISTORY})
"""What the words ask that no reader answers at all without a player
(``record_when``, ``period_split``, ``shot_distance``, ``player_history``),
so a player the router left out is worth restoring from the question.

Deliberately not every intent that reads one: where the player is optional -
``threshold_count``, ``single_game_high`` - an empty slot means "the league", and
filling it would turn a league question into a question about somebody the
question may only appear to name ("best" is Travis Best).

``record_when`` has a team branch too (ISSUES.md #144) - a threshold on a
TEAM's own scoring is a real, player-less question - which is why the restore
that reads this set (``subject._apply_restored_player``) puts a name back
only where the reading settled on exactly ONE player, subject or companion,
and never a stray name found elsewhere in the question.

.. versionadded:: 2.1.0

.. versionchanged:: 5.0.0
   ``shot_distance`` and ``player_history`` added: each refuses outright
   without a player ("shot_distance needs a player name"), and each is now
   assigned from the question's words under a parent whose own stages may
   have dropped the player (``subject.KIND_ASSIGNED_ASKS``: a
   ``leaderboard`` drops the filler player a distance question arrives with).

.. versionchanged:: 6.0.0
   ``PLAYER_REQUIRED_INTENTS`` until Phase 3, step 4: the grammar's keys.
"""

SUBJECT_RESTORABLE_ASKS: frozenset[PointShape] = frozenset({GAME_HIGHS, GAMES_COUNTED, PLAYER_SPLITS})
"""What the words ask where a player left out changes the answer
(``single_game_high``, ``threshold_count``, ``player_splits``), but is not required -
an empty slot means "the league" (or, for ``player_splits``, the team's own
splits: "show me Embiid's splits against boston" arrived as the 76ers and
the Celtics meeting with Embiid dropped, once the intent left the router's
prompt in 5.0.0) - so a name is restored only where the
question's own words name exactly one player and that naming survives
:func:`~association.query.subject._named_only_by_a_team_word` and
:func:`~association.query.subject._named_only_by_a_common_word`.

Separate from :data:`PLAYER_REQUIRED_ASKS` on purpose: those templates
cannot answer at all without a player, while these two have a real,
different answer with none (the league's leaders) - "kawhi most threes in a
game" (yardstick-v2 F093) used to answer that league ranking, Kawhi Leonard's
own 7 never mentioned, because ``single_game_high`` was never taught to read
a subject named with no scoring verb and no possessive
(``router._SUBJECT_OF_HIGH`` needs one of those; "NAME most/highest STAT"
has neither).

Measured before shipping, per AGENTS.md's own discipline for this exact
trap ("best" is Travis Best): both ``scripts/check_routing.py``'s cases and
``/home/jeff/association-research/statmuse-2026-09/feed_queries.txt`` (380
questions together) were run through the restoring grammar
(the subject reading) with the two intents here as
the only ones it can touch. 5 false-positive candidates turned up in the
WHOLE corpus - "Best true shooting percentage last season?" (Travis Best),
"Best record from 2010-11 to 2018-19 nba" and "Best NBA record since
January 31st 201" (both Travis Best again), "Celtics vs Bulls head to head
record" (Luther Head), and "Aaron gordan vs 76ers log" (a typo landing on
Gordan Giricek instead of Aaron Gordon) - and NONE of the five route to
``single_game_high`` or ``threshold_count``, so restricting to this pair
alone already clears the measured corpus with zero false positives. The
``best``/``head`` pair is still excluded by
:func:`~association.query.subject._named_only_by_a_common_word` as a
forward-looking gate, since a future question in either intent could still
collide with one of them; the typo case is not addressed here (a wrong
candidate, not an ungrounded one - the entity index's near-spelling pass is
the tool for that: :func:`~association.query.entities.read_near_spelling`
reads a single near spelling as that player and says so, and asks about two
or more).

.. versionadded:: 4.4.0

.. versionchanged:: 6.0.0
   ``SUBJECT_RESTORABLE_INTENTS`` until Phase 3, step 4: the grammar's keys.
"""

OWN_TEAM_RESTORABLE_ASKS: frozenset[PointShape] = frozenset({PLAYER_LINE})
"""What the words ask where a player's OWN team, named beside him and left
out by the router, is worth restoring (``player_stat``) - narrower than :data:`PLAYER_ASKS` on
purpose, since honoring the restored ``own_team`` slot needs the relation to
narrow by it (``player_relation._narrow_player_games``'s ``team`` param,
threaded through ``scoped_games`` only where a caller passes it), which only
``player_stat`` does.

"lebron stats as a starter for Miami" (yardstick-v2 F166) used to answer his
current (Lakers) season, "Miami" never read at all - not even as noise, since
nothing on the relation could have narrowed to it either way.
the subject reading reads "for <team>"/"with the <team>" beside an
already-known player (``subject._apply_own_team``) and, with no season also
named, defaults ``span`` to "career" too - a historical team names a
tenure, not "now". Written to ``own_team``, never the router's own ``team``
slot - see that function's docstring for the recorded case
(``player_stat``'s golden snapshot) where a router-supplied ``team`` sitting
beside a correct ``opponent`` is noise, not a second fact to narrow by, the
same shape ``games._team_slot_for_player`` already treats it as for
``game_log``.

Deliberately not ``game_log``: its own ``team``/``opponent`` dance
(``games._team_slot_for_player``) already reads a ``team`` slot beside a
player, and drops it outright when he actually played for that team ("his
own team narrows nothing") - correct only because no tenure narrowing
existed there. Reusing this flag for ``game_log`` without first reconciling
the two readings would leave one of them silently wrong; filed in
``ISSUES.md`` rather than done here.

.. versionadded:: 4.4.0

.. versionchanged:: 6.0.0
   ``OWN_TEAM_RESTORABLE_INTENTS`` until Phase 3, step 4: the grammar's keys.
"""

TEAM_ONLY_ASKS: frozenset[PointShape] = frozenset({TEAM_RECORD, TEAM_RANKING, TEAM_LINE, TEAM_OUTLOOK})
"""What the words ask with no player-shaped reading at all (``team_record``,
``team_leaderboard``, ``team_stat``, ``team_outlook``) - absent from
:data:`PLAYER_ASKS`, and so never checked by
``subject.apply_subject`` or the answering loop against a stray player name.

A question naming exactly one real player and no team, routed to one of
these, is answering a different subject than the one named -
yardstick-v2 F111, "alperen şengün alltime record" routed to
``team_leaderboard`` with no player and no team slot at all, and answered
the league standings, entirely off Sengun. AGENTS.md's "Refuse by name
where the intent cannot be about the subject" is exactly this shape;
``subject.player_named_on_a_team_only_question`` is the check, read by the
parser into the Reading's ``no_player_reading`` refusal
(:attr:`Reading.refused`), which the planner says before any reader runs -
naming the player it read rather than answering the wrong one.

Deliberately not every team-shaped intent: ``head_to_head`` is only ever
two teams meeting - the parser reads a player's record against a team as
his own games (``player_splits``, #163) from the subject's kind - and
``coach`` reads no point (a cause, ``no_coach_table``) and already refuses on its own terms -
neither needs a second, more general check that could only disagree with
the first.

.. versionadded:: 2.1.0

.. versionchanged:: 5.0.0
   Lives on the reader's side (``templates.common`` re-exported it until Phase 2, step 6).

.. versionchanged:: 6.0.0
   ``TEAM_ONLY_INTENTS`` until Phase 3, step 4: the grammar's keys.
"""

DECLARED_RELATIONS: frozenset[PointRelation] = frozenset({"netpoints", "shots"})
"""The declared relations (``ROADMAP.md``, Phase 2, step 5, and the decision
"Charts are declared shapes"): what the words ask on one of them is read
from its own default point rather than declined "no adapter for" - a
player's NetPoints (a scalar and a split by category) and his fingerprint
(a chart), each on ``netpoints``; a player's shot chart (a chart) and his
average shot distance (a scalar), each on ``shots``. No word of the question
moves their point - the retired templates read their slots alone - so it is
the asked shape's default (:data:`~association.query.point.DEFAULT_POINTS`),
before any move that reads a player's games (:func:`on_a_declared_relation`).

.. versionadded:: 6.0.0
   ``CHART_INTENTS``, the four intents' names, until Phase 3, step 4.
"""


def on_a_declared_relation(asked: PointShape | None) -> bool:
    """Whether the words ask for a shape on a declared relation
    (:data:`DECLARED_RELATIONS`): ``player_netpoints``, ``fingerprint``,
    ``shot_chart``, ``shot_distance``.

    .. versionadded:: 6.0.0
    """
    return asked is not None and asked.relation in DECLARED_RELATIONS


DEFAULT_LIMIT = 5
"""How many rows a shape lists where the question named no count.

.. versionchanged:: 5.0.0
   Lives on the reader's side (``templates.common`` re-exported it until Phase 2, step 6).
"""

MAX_LIMIT = 50
"""The most rows a question's ``limit`` reaches (:func:`_clamp_limit`): the
games a log lists, the players or teams a ranking lists, the runs a streak
listing shows. One cap for every shape; the season-line ranking
(``season_line.ranking_statement``) applies it to its SQL as well.

.. versionchanged:: 5.0.0
   Lives on the reader's side, and is the only one: ``leaderboard`` had a
   ``MAX_LIMIT`` of its own (100, for the retired agent's model-supplied
   count) that no answer reached, since every ranking's count was clamped
   to this one first.
"""

DEFAULT_GAME_LOG_LIMIT = 10
"""How many games a log lists by default.

.. versionchanged:: 5.0.0
   Lives on the reader's side (``templates.games`` re-exported it until Phase 2, step 6).
"""

DEFAULT_SINGLE_GAME_LIMIT = 3
"""How many games a single-game high lists by default.

.. versionchanged:: 5.0.0
   Lives on the reader's side (``templates.players`` re-exported it until Phase 2, step 6).
"""

DEFAULT_STREAK_LIMIT = 5
"""How many runs the league's longest streaks list by default - one per
player, or one per team-season.

.. versionadded:: 5.0.0
   On the reader's side (``templates.splits._DEFAULT_STREAK_LIMIT`` was this).
"""

DEFAULT_NAMED_RUNS = 3
"""How many runs a named player's or team's streak reads: the longest, plus
two to tie it (the sayer shows the ties).

.. versionadded:: 5.0.0
   On the reader's side (``compose.core.DEFAULT_NAMED_RUNS`` was this).
"""


def _clamp_limit(limit: int | None, default: int = DEFAULT_LIMIT) -> int:
    if limit is None:
        return default
    return min(limit, MAX_LIMIT)


def ordinal_word(n: int) -> str:
    """``1`` -> ``"1st"``, ``12`` -> ``"12th"``, ``23`` -> ``"23rd"``."""
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")  # codespell:ignore nd - an ordinal suffix
    return f"{n}{suffix}"


def _career_scope(scope: Scope) -> Scope:
    """The point reader's rule (``route()``'s own, before it) for a count by a player (``_HOW_MANY_OR_OFTEN``, step 2
    B5): with no season named, "how many ... has he" is his career."""
    return scope if scope.span.named else replace(scope, span=scope.span.as_career())


def named_player_in(scope: Scope) -> bool:
    """Whether ``scope`` names one player (:attr:`Subject.player`): the
    player a reader on his games reads - not a pair or a comparison.

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       Reads the typed subject (the ``player`` slot until Phase 3, step 2).
    """
    return scope.subject.player is not None


class Unsupported(Exception):
    """The reader has no reading of this point, or the planner cannot say
    this query - a dimension value it lacks, or a scoping slot the relation
    does not narrow by. The agent may still be able to answer it; this is
    not a claim that nothing can.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Declared on the reader's side, which raises it as a decline
       (``compose.core`` re-exports it).
    """


class PointRefused(Unsupported):
    """The point reader's refusal, carrying its :class:`Cause`: raised where
    the reading comes to one, caught by the parser (``parse.with_point``),
    which puts the cause on the Reading as ``point_refusal``.

    A kind of :class:`Unsupported`, so the reader's shared helpers that
    raise it (``lines.measure_filters``, ``measures.streak_column``,
    ``measures.log_extras``, ``measures.period_split_measure``,
    ``lines.threshold_count_line``) stay declines to the answer side's
    callers, which catch ``Unsupported`` and read ``message`` - the sentence
    those callers have always said; the point reader's caller reads the
    cause.

    .. versionadded:: 5.0.0
    """

    def __init__(self, cause: Cause, message: str | None = None) -> None:
        """Wrap ``cause``; ``message`` is what an answer-side caller that
        catches it as a decline says (the kind where none is given)."""
        super().__init__(message or cause.kind)
        self.cause = cause


class ShapeDeclined(Unsupported):
    """A shape's decline by a :class:`Cause` the planner says: a narrowing
    the point's shape cannot honor (``cannot_honor``), or a point its only
    reader reads nothing of (``no_reading_of_point``) - raised by the
    planner (``compose.plan.cells_declined``) and by the answer side where
    the shape's reader is asked (``compose._read``), with the decline's
    sentence as its message, so every caller that catches
    :class:`Unsupported` reads what it always read. The cause's facts lack
    the page's label (``intent``) its sentence begins with, which the
    answering loop, the label's one reader, adds (:meth:`labeled`).

    .. versionadded:: 6.0.0
    """

    def __init__(self, kind: str, facts: Mapping[str, Any], message: str) -> None:
        """The cause's ``kind`` and ``facts`` but the label, and the decline's sentence."""
        super().__init__(message)
        #: The decline's cause, without the page's label.
        self.cause: Cause = Cause(kind=kind, facts=dict(facts))

    @staticmethod
    def labeled(cause: Cause, intent: str) -> Cause:
        """``cause`` under the page's label ``intent`` - the retired name the
        sentence begins with (``"Nothing here answers this question:
        <intent>: ..."``)."""
        return Cause(kind=cause.kind, facts={"intent": intent, **cause.facts})


_HALF_PERIODS: dict[int, tuple[int, ...]] = {1: (1, 2), 2: (3, 4)}


def period_label(period: int) -> str:
    """``1`` -> ``"1st quarter"``, ``5`` -> ``"overtime"``, ``6`` -> ``"2nd overtime"``.

    .. versionadded:: 5.0.0
       On the reader's side (``templates.common.period_label`` was this).
    """
    if 1 <= period <= 4:
        return f"{ordinal_word(period)} quarter"
    ot = period - 4
    return "overtime" if ot == 1 else f"{ordinal_word(ot)} overtime"


def period_narrowing(scope: Scope) -> tuple[tuple[int, ...], str] | None:
    """The periods a question's ``period`` cell narrows each game to, and
    how an answer names them - ``((3, 4), "2nd half")`` - or None for the
    whole game (:meth:`Period.narrowing`; a period outside 1-10 is no
    period either).

    .. versionadded:: 5.0.0

    .. versionchanged:: 5.0.0
       On the reader's side (``templates.common.period_narrowing`` was this).

    .. versionchanged:: 6.0.0
       Reads the typed :class:`Period` (the slots ``period`` and ``half``
       until Phase 3, step 2).
    """
    return scope.period.narrowing() if scope.period is not None else None


STARTER_SIDES: dict[str, bool] = {"starter": True, "bench": False}
"""A ``split`` naming one half of the starter/bench split, and whether that
half started (``player_games.STARTER_SIDES`` is this).

.. versionadded:: 5.0.0
"""


def scope_reads_box_scores(scope: Scope, measures: list[Any]) -> bool:
    """Whether ANY narrowing sends a player's average to box scores rather
    than the season line: the opponent, venue and absent teammates; a named
    half of the starter/bench split (it narrows the GAMES - the season line
    has no such column); a line on a box-score column ("under 14 fta",
    ``measures``); a game of each playoff series; a range of seasons; a
    calendar ``situation``; a season type left unstated (the season line is
    one row per type and has no "both at once" reading); his own team
    ("lebron stats as a starter for Miami" keeps the games he played for
    that team); a teammate's role; a quarter held as a condition. The
    narrowings themselves are applied by the relation's shared steps.

    .. versionadded:: 5.0.0
       On the reader's side (``templates.players._player_stat_reads_box_scores`` was this).
    """
    split_side = scope.split if scope.split in STARTER_SIDES else None
    cuts = scope.cuts
    return any(
        (
            cuts.opponent,
            cuts.venue,
            split_side,
            scope.span.since,
            measures,
            cuts.game_n,
            cuts.situation,
            scope.span.both,
            cuts.tenure,
            scope.companions,
            any(line.period is not None for line in scope.lines),
        )
    )


# The cells that narrow WHICH games an answer covers, every one a typed
# value's since Phase 3, step 2 (:attr:`Scope.CELLS`). A reader that ignores
# one gives a different answer, not a broader one, and says nothing -
# confirmed three times ("his last game" charting a whole season, and so
# on). The reader reads these CORRECTLY in each case, so nothing upstream
# can catch a reader dropping them; only the planner's check of each cell
# against the relation's table and the shape's row can
# (``compose.plan.cells_unhonored``, over ``player_relation.RELATION_SCOPING``
# and ``team_relation.TEAM_RELATION_SCOPING`` with their per-shape rows).
#
# They are read from the question's words, never asked of the model, for
# the same reason. Measured against real StatMuse queries before they were:
# "jaylen brown last 8 games vs pistons" answered with the Celtics' last 8
# games, "Knicks home record" with their overall record, "career points
# leaders" with this season's, and "Podziemski game log without curry" with
# his whole log. Each was fast, fluent and about something else. The split
# and a range of seasons are read for every intent for the same reason: a
# reader that is not about splits or ranges answered them with one season.
# A line kept under or over a number ("under 14 FTA", "with 25 minutes") is
# read onto the relation by `measure_filters`; a unit asked of a measure
# ("points per 100 possessions", a season total) is the `Measure`'s one
# cell, `rate`, which no relation table declares (a unit narrows no games)
# and two shapes take beyond their relation's (the season line's ranking,
# the team compiler's total). The split - the role family's one cell, whose
# value is not typed yet (:data:`Split`) - is set by any split asked for;
# the bare starter/bench category is a table of both halves, which a shape
# that honors one named half (``ShapeCells.sides``) does not state.


def unhonored_cells(scope: Scope, honored: frozenset[str]) -> list[str]:
    """The slot names of the cells ``scope`` sets that ``honored`` does not
    hold, sorted (:meth:`Scope.cells`, each by :func:`cell_slots`): the
    relation's own check of a cell set against its table
    (``compose.core._check_relation_scoping``).

    Over :meth:`Scope.cells`, every family's and the split's; it read the
    untyped slots' list (``SCOPING_SLOTS``) beside the typed families until
    Phase 3, step 2's closing slice.

    .. versionadded:: 6.0.0
    """
    return sorted(slot for cell in scope.cells() - honored for slot in cell_slots(scope, cell))


# Templates that read a player name at all - resolving it, filtering on it, or
# refusing because of it. A name the question does not support is only worth
# refusing over where the answer would actually be about that player; for
# `team_record` and `head_to_head` the slot is not read, so a stray one changes
# nothing. Guarded by test_no_template_outside_player_intents_reads_a_player,
# which reads the source rather than trusting this list.
PLAYER_ASKS: frozenset[PointShape] = frozenset(
    {
        NETPOINTS_FINGERPRINT,
        PLAYER_LOG,
        PLAYER_RANKING,
        PLAYER_COMPARISON,
        SEASON_HISTORY,
        PLAYER_MEETINGS,
        NETPOINTS_RATINGS,
        PLAYER_SPLITS,
        PERIOD_LOG,
        PLAYER_LINE,
        LINE_RECORD,
        SHOT_CHART,
        SHOT_DISTANCE,
        GAME_HIGHS,
        LINE_RUNS,
        TEAM_PERIOD_TOTAL,
        GAMES_COUNTED,
        PRESENCE_SPLIT,
    }
)
"""What the words ask whose reader reads a ``player`` or ``players`` slot.

.. versionadded:: 2.1.0

.. versionchanged:: 6.0.0
   ``PLAYER_INTENTS`` until Phase 3, step 4: the grammar's keys.
"""
