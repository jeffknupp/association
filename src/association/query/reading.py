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
from typing import TYPE_CHECKING, Any, ClassVar, Literal

if TYPE_CHECKING:
    from association.query.decisions import Decision
    from association.query.entities import Availability
    from association.query.subject import Subject

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


@dataclass(frozen=True, kw_only=True)
class ConditionSpec:
    """One player named beside the subject and the role the question gives
    him in the games asked about - one ``conditions`` entry, as the relation
    reads it (``player_relation._condition_from_slot``): "when Embiid and
    Paul George start", "in games Maxey had 20+ points". ``stat`` and
    ``threshold`` belong to a ``reached`` role.

    .. versionadded:: 5.0.0
    """

    player: str
    side: Literal["own", "opponent"] = "own"
    predicate: Literal["played", "absent", "started", "bench", "reached"] = "played"
    stat: str | None = None
    threshold: int | None = None

    @classmethod
    def from_slot(cls, entry: Any) -> ConditionSpec:
        """One ``conditions`` slot entry, a dict, as a typed record - raising
        on a shape the relation could not read.

        .. versionadded:: 5.0.0
        """
        if not isinstance(entry, Mapping) or set(entry) - {"player", "side", "predicate", "stat", "threshold"}:
            raise ScopeError(f"scope condition {entry!r} is not a player, side, predicate and line")
        player = entry.get("player")
        if not isinstance(player, str) or not player.strip():
            raise ScopeError(f"scope condition {entry!r} names no player")
        side = _one_of("own", "opponent")("condition side", entry.get("side", "own"))
        predicate = _one_of("played", "absent", "started", "bench", "reached")("condition predicate", entry.get("predicate", "played"))
        stat = entry.get("stat")
        threshold = entry.get("threshold")
        return cls(
            player=player,
            side=side,
            predicate=predicate,
            stat=None if stat is None else _text("condition stat", stat),
            threshold=None if threshold is None else _whole("condition threshold", threshold),
        )

    def to_slot(self) -> dict[str, Any]:
        """The ``conditions`` entry the relation reads.

        .. versionadded:: 5.0.0
        """
        line = {key: value for key, value in (("stat", self.stat), ("threshold", self.threshold)) if value is not None}
        return {"player": self.player, "side": self.side, "predicate": self.predicate, **line}


@dataclass(frozen=True, kw_only=True)
class PeriodCondition:
    """A quarter or half used as a CONDITION on which games count, rather
    than as the part of each game measured: "three points made per game
    after making one three in first quarter" (yardstick-v2 F062) is his
    whole-game threes over the games whose first quarter held one. The
    line is ``stat`` compared to ``threshold`` by ``op`` - ``">="`` ("at
    least one", "10+", "a three"), or ``"="`` for a bare number ("one three"
    is exactly one, the reading the question's key takes; the answer says
    "exactly", and "1+" reaches the other) - in ``period`` (1-10) or ``half``
    (1-2), on the subject's own period line
    (:meth:`~association.query.player_games.Narrowed.narrow_period_condition`).

    .. versionadded:: 5.0.0
    """

    stat: str
    threshold: int
    op: Literal[">=", "="] = ">="
    period: int | None = None
    half: Literal[1, 2] | None = None

    @classmethod
    def from_slot(cls, entry: Any) -> PeriodCondition:
        """One ``period_condition`` slot value, a dict, as a typed record -
        raising on a shape the relation could not read.

        .. versionadded:: 5.0.0
        """
        if isinstance(entry, PeriodCondition):
            return entry
        if not isinstance(entry, Mapping) or set(entry) - {"stat", "threshold", "op", "period", "half"} or "stat" not in entry or "threshold" not in entry:
            raise ScopeError(f"scope period_condition {entry!r} is not a stat, a threshold and a period or half")
        period, half = entry.get("period"), entry.get("half")
        if (period is None) == (half is None):
            raise ScopeError(f"scope period_condition {entry!r} needs exactly one of period and half")
        return cls(
            stat=_text("period_condition stat", entry["stat"]),
            threshold=_whole("period_condition threshold", entry["threshold"]),
            op=_one_of(">=", "=")("period_condition op", entry.get("op", ">=")),
            period=None if period is None else _whole("period_condition period", period),
            half=None if half is None else _one_of(1, 2)("period_condition half", half),
        )

    def to_slot(self) -> dict[str, Any]:
        """The ``period_condition`` slot value the relation reads.

        .. versionadded:: 5.0.0
        """
        return {"stat": self.stat, "threshold": self.threshold, "op": self.op, **({"period": self.period} if self.period is not None else {"half": self.half})}


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


@dataclass(frozen=True)
class Claim:
    """The characters of the question one reader rule consumed - ``start``
    and ``end`` as a slice of the question, and ``what`` it read them as
    (``season``, ``season_type``, ``career``, ``range``, ...). A tagger
    claims each span it read once; two rules claiming overlapping
    characters is a bug the reader says out loud
    (:func:`~association.query.span.claimed`), and the words nothing
    claimed are what Phase 3's step 3 records on the Reading.

    .. versionadded:: 6.0.0
    """

    start: int
    end: int
    what: str


@dataclass(frozen=True, kw_only=True)
class Scope:
    """What narrows the answer, one typed field per scoping slot. A field at
    its default (None, empty, False) is the slot absent - the reading every
    reader already takes of a falsy slot (``compose.plan.unhonored_scoping``
    asks whether the field is truthy). The names are here too, as the question gave them;
    resolving them against the warehouse happens where each is read.

    .. versionadded:: 5.0.0
    """

    #: The subject and the players and teams beside it.
    player: str | None = None
    players: tuple[str, ...] = ()
    team: str | None = None
    teams: tuple[str, ...] = ()
    opponent: str | None = None
    own_team: str | None = None
    with_player: tuple[str, ...] = ()
    without: tuple[str, ...] = ()
    #: Each player named beside the subject, with his role.
    conditions: tuple[ConditionSpec, ...] = ()
    #: What is measured.
    stat: str | None = None
    threshold: int | None = None
    above: tuple[str, ...] = ()
    below: tuple[str, ...] = ()
    #: The columns a ranking asks to see beside its measure.
    fields: tuple[str, ...] = ()
    ranked_by: str | None = None
    per_game: bool = False
    rate: str | None = None
    side: Literal["offense", "defense", "total"] | None = None
    shot_value: Literal[1, 2, 3] | None = None
    #: A streak's kind (``"win"``, ``"loss"``).
    kind: str | None = None
    rank: Literal["most", "fewest", "best", "worst"] | None = None
    #: When: the seasons and the season type, one typed value
    #: (:class:`Span`; six slots until Phase 3, step 2).
    span: Span = field(default_factory=Span)
    #: One calendar day, ``YYYY-MM-DD``.
    date: str | None = None
    #: A calendar or alignment narrowing, as the question worded it.
    situation: str | None = None
    split: Split | None = None
    game_n: int | None = None
    season_n: int | None = None
    round: str | None = None
    period: int | None = None
    half: Literal[1, 2] | None = None
    #: A period as a condition on which games count (:class:`PeriodCondition`).
    period_condition: PeriodCondition | None = None
    venue: Literal["home", "away"] | None = None
    #: The window.
    order: Literal["recent", "first"] | None = None
    limit: int | None = None

    def __post_init__(self) -> None:
        # The one range rule every reader already keeps: a limit below 1 is
        # no window at all (the templates clamp it to their default), so a
        # producer writing one has a bug to say out loud.
        if self.limit is not None and self.limit < 1:
            raise ScopeError(f"scope limit {self.limit} is below 1")

    @classmethod
    def from_slots(cls, slots: Mapping[str, Any]) -> Scope:
        """The scope a slot dict names. An empty value (None, "", an empty
        list, False) is the slot absent; a key nothing here types, or a value
        of the wrong type or outside its closed set, raises - a slot this
        could only drop is a narrowing the answer would silently leave out.

        .. versionadded:: 5.0.0
        """
        unknown = sorted(set(slots) - _SCOPE_FIELDS - _SPAN_SLOT_NAMES)
        if unknown:
            raise ScopeError(f"no scope field for slot(s) {unknown}")
        values: dict[str, Any] = {}
        span_slots: dict[str, Any] = {}
        for name, raw in slots.items():
            # A blank string is the slot absent too: the model files " " for
            # an opponent it has none of, and every template read it as
            # nobody (`_optional_team`), where the relation's resolver read
            # it as a team named nothing and refused.
            if raw is None or raw is False or (isinstance(raw, (str, list, tuple)) and not raw) or (isinstance(raw, str) and not raw.strip()):
                continue
            if name == "span" and isinstance(raw, Span):
                values[name] = raw
            elif name in _SPAN_SLOT_NAMES:
                span_slots[name] = _CHECKS[name](name, raw)
            else:
                values[name] = _CHECKS[name](name, raw)
        if span_slots:
            if "span" in values:
                raise ScopeError(f"a typed span and the slot(s) {sorted(span_slots)} at once")
            values["span"] = Span.from_slots(span_slots)
        return cls(**values)

    def to_slots(self) -> dict[str, Any]:
        """The Scope as a slot dict - every field away from its default,
        sequences as lists: the shape a route is recorded in and the trace
        prints (:meth:`Reading.describe`).

        .. versionadded:: 5.0.0
        """
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if value is None or value is False or value == ():
                continue
            if f.name == "conditions":
                out[f.name] = [condition.to_slot() for condition in value]
            elif f.name == "period_condition":
                out[f.name] = value.to_slot()
            elif f.name == "span":
                out.update(value.to_slots())
            else:
                out[f.name] = list(value) if isinstance(value, tuple) else value
        return out

    def projected(self) -> dict[str, Any]:
        """Every field, at its default or not, with the span as the six
        slots it was until Phase 3, step 2 - the shape a recorded Scope
        kept (:func:`~association.query.stages.plain`), so a reading
        recorded before the span was typed compares identical to one
        recorded after. The typed value is recorded beside the reading
        (``stages._reading_record``, ``span``), never here.

        .. versionadded:: 6.0.0
        """
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name == "span":
                slots = value.to_slots()
                out["season"] = slots.get("season")
                out["season_type"] = slots.get("season_type")
                out["season_type_unstated"] = bool(slots.get("season_type_unstated"))
                out["span"] = slots.get("span")
                out["since"] = slots.get("since")
                out["until"] = slots.get("until")
            else:
                out[f.name] = value
        return out


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
    # One calendar day as the router writes it (router._validate_date): an
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


def _conditions(name: str, raw: Any) -> tuple[ConditionSpec, ...]:
    if not isinstance(raw, (list, tuple)):
        raise ScopeError(f"scope {name}={raw!r} is not a list of conditions")
    return tuple(entry if isinstance(entry, ConditionSpec) else ConditionSpec.from_slot(entry) for entry in raw)


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
    "period_condition": lambda name, raw: PeriodCondition.from_slot(raw),
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
  beside the sentence (``refused`` and ``intent``, as it did).

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
    offset: int = 0
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
    #: A position code, honored on the ``"everyone"`` relation.
    position: str | None = None
    #: The intent label the trace and the presenters use.
    intent: str = ""
    #: Who the question is about, as the subject reading read it.
    subject: Subject | None = None
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
    #: The characters of the question each tagger consumed
    #: (:class:`Claim`), in the question's order: the span's since Phase 3,
    #: step 2, each family's as its slice lands. What they leave unclaimed
    #: is step 3's ``unread``.
    #:
    #: .. versionadded:: 6.0.0
    claims: tuple[Claim, ...] = ()
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

    @classmethod
    def from_slots(cls, slots: Mapping[str, Any], *, intent: str = "", subject: Subject | None = None) -> Reading:
        """A Reading holding nothing but the scope ``slots`` names (through
        :meth:`Scope.from_slots`), for the readers that still build one from a
        slot dict: the agent's dispatch of a routed question to its template,
        a template handing a question to another, and the tests.

        .. versionadded:: 5.0.0
        """
        return cls(scope=Scope.from_slots(slots), intent=intent, subject=subject)

    def projected(self) -> dict[str, Any]:
        """Every field as a Reading was recorded until Phase 3, step 2
        (:func:`~association.query.stages.plain`): ``subject_span`` as the
        ``span`` and ``season`` pair it replaced, every other field as it
        is - ``claims`` included, the one field the record gained.

        .. versionadded:: 6.0.0
        """
        out = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "subject_span"}
        settled = self.subject_span
        out["span"] = "career" if settled is not None and settled.career else None
        out["season"] = settled.season if settled is not None else None
        return out

    def describe(self) -> str:
        """The one trace line: every field that decides the answer."""
        window = f"{self.order}/{self.direction}" + (f"/{self.limit}" if self.limit is not None else "")
        who = self.subject.kind if self.subject is not None else "?"
        return (
            f"relation={self.relation} subject={who} shape={self.shape} by={self.by} on={self.on} measures={self.measures} aggregate={self.aggregate} "
            f"group={self.group} predicates={self.predicates} window={window} scope={self.scope.to_slots()}"
        )


# What the subject reading needs to know about the intents and the question's
# words - moved here from templates/common.py on 2026-10-02 so the reader does
# not import the answer side for them (ROADMAP.md, Phase 1: the reader's
# imports of the answer side); templates.common re-exported each under its
# old name until Phase 2, step 6. Intent goes from the reader in Phase 3, and
# these sets with it.

PLAYER_REQUIRED_INTENTS: frozenset[str] = frozenset({"record_when", "period_split", "shot_distance", "player_history"})
"""Intents whose template cannot answer at all without a player, so a player the
router left out is worth restoring from the question.

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
   have dropped the player (``subject.KIND_ASSIGNED_INTENTS``: a
   ``leaderboard`` drops the filler player a distance question arrives with).
"""

SUBJECT_RESTORABLE_INTENTS: frozenset[str] = frozenset({"single_game_high", "threshold_count", "player_splits"})
"""Intents where a player left out changes the answer, but is not required -
an empty slot means "the league" (or, for ``player_splits``, the team's own
splits: "show me Embiid's splits against boston" arrived as the 76ers and
the Celtics meeting with Embiid dropped, once the intent left the router's
prompt in 5.0.0) - so a name is restored only where the
question's own words name exactly one player and that naming survives
:func:`~association.query.subject._named_only_by_a_team_word` and
:func:`~association.query.subject._named_only_by_a_common_word`.

Separate from :data:`PLAYER_REQUIRED_INTENTS` on purpose: those templates
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
"""

OWN_TEAM_RESTORABLE_INTENTS: frozenset[str] = frozenset({"player_stat"})
"""Intents where a player's OWN team, named beside him and left out by the
router, is worth restoring - narrower than :data:`PLAYER_INTENTS` on
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
"""

#: A question's position word, mapped to :data:`POSITION_CODES`' own letter.
POSITIONS: list[tuple[str, str]] = [
    (r"\bcenters?\b", "C"),
    (r"\bpoint guards?\b", "PG"),
    (r"\bshooting guards?\b", "SG"),
    (r"\bpower forwards?\b", "PF"),
    (r"\bsmall forwards?\b", "SF"),
    (r"\bforwards?\b", "F"),
    (r"\bguards?\b", "G"),
]
"""``(pattern, position code)`` - the words a position-group question uses.

.. versionadded:: 4.4.0

.. versionchanged:: 5.0.0
   Moved here from the point reader (``query/point.py`` since 2026-10-03,
   ``compose/move.py`` before), so the subject reading and the compiler
   share one list without importing each other.
"""

FILLER_PLAYER_WORDS: frozenset[str] = frozenset({"player", "players", "a player", "any player"})
"""What the router writes in ``player`` when the question names nobody -
filler, not a name ("Most points in 15th season played" arrived as
``player: "player"``, yardstick-v2 F099). The subject reading reads none of
them as a player, and the compiler clears the slot.

.. versionadded:: 5.0.0
"""


TEAM_ONLY_INTENTS: frozenset[str] = frozenset({"team_record", "team_leaderboard", "team_stat", "team_outlook"})
"""Intents with no player-shaped reading at all - absent from
``reading.PLAYER_INTENTS``, and so never checked by
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
"""

CHART_INTENTS: frozenset[str] = frozenset({"fingerprint", "player_netpoints", "shot_chart", "shot_distance"})
"""The intents whose point is a declared relation's own, read from the
reader's own set rather than declined "no adapter for" (``ROADMAP.md``,
Phase 2, step 5, and the decision "Charts are declared shapes"): a
player's NetPoints (``player_netpoints``, a scalar and a split by category)
and his fingerprint (``fingerprint``, a chart), each on the ``netpoints``
relation; a player's shot chart (``shot_chart``, a chart) and his average
shot distance (``shot_distance``, a scalar), each on the ``shots`` relation. No word of the question moves their point - the retired
templates read their slots alone - so it is the intent's default
(:data:`~association.query.point.DEFAULT_POINTS`), before any move that
reads a player's games.

.. versionadded:: 5.0.0
"""

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
    """Whether ``scope`` names a player at all.

    .. versionadded:: 5.0.0
    """
    return scope.player is not None and bool(scope.player.strip())


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
    """The periods a question's ``period``/``half`` cell narrows each game to,
    and how an answer names them - ``((3, 4), "2nd half")`` - or None for the
    whole game. A half wins over a quarter, since the parser writes a half
    only where the words said one; a period outside 1-10 is no period.

    .. versionadded:: 5.0.0

    .. versionchanged:: 5.0.0
       On the reader's side (``templates.common.period_narrowing`` was this).
    """
    if scope.half is not None and scope.half in _HALF_PERIODS:
        return _HALF_PERIODS[scope.half], f"{ordinal_word(scope.half)} half"
    if scope.period is not None and 1 <= scope.period <= 10:
        return (scope.period,), period_label(scope.period)
    return None


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
    return any(
        (
            scope.opponent,
            scope.venue,
            scope.without,
            split_side,
            scope.span.since,
            measures,
            scope.game_n,
            scope.situation,
            scope.span.both,
            scope.own_team,
            scope.conditions,
            scope.period_condition,
        )
    )


# Slots that narrow WHICH games an answer covers. A template that ignores one
# gives a different answer, not a broader one, and says nothing - confirmed
# three times ("his last game" charting a whole season, and so on). The router
# extracts these CORRECTLY in each case, so check_routing cannot catch a
# template dropping them; only this can.
#
# The five after those are read from the question text by the router and by
# subject.apply_subject, never asked of the model, and exist for the same
# reason. Measured against real StatMuse queries before they did: "jaylen brown
# last 8 games vs pistons" answered with the Celtics' last 8 games, "Knicks
# home record" with their overall record, "career points leaders" with this
# season's, and "Podziemski game log without curry" with his whole log. Each
# was fast, fluent and about something else. `round` ("finals", "game 7") is
# honored by no template at all: nothing in the warehouse records one. `split`
# and `since` (a range of seasons) are read for every intent for the same reason:
# a template that is not about splits or ranges answered them with one season.
# `below` ("under 14 FTA") and `above` ("with 25 minutes") are lines a game's
# box score is kept under or over - `measure_filters` reads them onto the
# relation for the templates listed with them. `situation` is a weekday, a
# month, a fixed holiday, "since <day>" (`calendar.parse_situation`), or - the
# other half of the same slot, K3-2 - a conference or division the opponent is
# in (`calendar.parse_alignment`), applied together by `apply_situation`
# below. Anything else it could name (back-to-backs, overtime, an age, "since
# returning") is refused by every template: nothing narrows to it yet, and
# answering without it answered the whole season.
# `until` closes a `since`-bounded range at the far end ("2019-20 to 2023-24",
# "the 2010s") - `router._validate_range` - and is declared and read
# everywhere `since` is (`span_of`, `ResolvedSpan.clause`), never on its own: a
# template that honors `since` but not `until` would read a CLOSED range as an
# open one and answer every season after it too, the same silent-widening
# shape `since` itself exists to stop. `test_until_is_declared_wherever_since_is`
# (tests/query/test_templates.py) enforces this pairing by reading the source.
# `game_n` ("game 4") is one game of each playoff series, numbered by date over
# `real_games`; the relation finds it, and a regular-season question refuses.
# `season_n` ("his 18th season") is one season named by its place in a career;
# `settle_ordinal_season` turns it into a year once the player is resolved.
# `rate` is a per-possession rate asked of a metric that has no such form
# ("points per 100 possessions", "netpoints / 90"): set by the router only
# where it could not switch the metric itself, and honored by nothing.
# `season_type_unstated` is not a narrowing at all but its opposite - a
# "last N games" question naming no season type at all
# (`router._route_game_log_recent_span`) - and it is listed here for the same
# reason `situation` is: the discipline that a new slot is declared by the
# templates that honor it and refused by the rest applies whether the slot
# widens or narrows. Only `game_log` can ever see it - the router sets it for
# no other intent - so it is refused everywhere else only in principle.
# `until` (step 3, K1) is the inclusive LAST season of a range whose first the
# router already files as `since` ("2019-20 to 2023-24", a decade) - never
# alone, so a template honors it only by honoring `since` and reading `until`
# beside it (`span_of`/`validated_until`); one not wired to `until` at all
# would otherwise silently read only the range's first half.
# `ranked_by` is read by nothing: the router files it when a
# `leaderboard` question ranks the GAMES that satisfy a boolean stat by another
# measure ("highest scoring triple doubles" - yardstick-v2 F124), the same
# slots as the count "most triple doubles" otherwise. The leaderboard's
# reader does not state it, so it steps aside and the compiler's boolean-game
# ranking answers.
SCOPING_SLOTS = frozenset(
    {
        "order",
        "date",
        "opponent",
        "venue",
        "without",
        "round",
        "split",
        "below",
        "above",
        "game_n",
        "season_n",
        "situation",
        "conditions",
        "rate",
        "ranked_by",
        "period",
        "half",
        "period_condition",
    }
)


def unhonored_cells(scope: Scope, honored: frozenset[str]) -> list[str]:
    """The slot names ``scope`` sets that ``honored`` does not hold, sorted:
    every slot of :data:`SCOPING_SLOTS` that is truthy, and the span's cells
    (:attr:`Span.CELLS`, by the slot names they were refused under -
    :meth:`Span.unhonored`). One reading of "what is set beyond what is
    honored", for the planner's relation check and for a reader's own
    words (:func:`unhonored_scoping`).

    .. versionadded:: 6.0.0
    """
    return sorted([name for name in SCOPING_SLOTS if getattr(scope, name) and name not in honored] + scope.span.unhonored(honored))


#: Templates that honor one NAMED half of the starter/bench split and refuse
#: the bare category, which asks for a table they do not produce.
_SPLIT_SIDE_ONLY = frozenset({"game_log", "player_stat", "period_split", "shot_chart", "shot_distance"})


def unhonored_scoping(intent: str, scope: Scope, honored: frozenset[str]) -> list[str]:
    """The scoping slots ``scope`` sets that ``honored`` does not hold, for
    ``intent``: a reader steps aside for one its retired words do not state
    (:data:`~association.query.compose.plan.STATED_SCOPING`), leaving the
    compiler's own sentence to answer, and the planner refuses one the
    relation cannot honor at all. A slot is set when its field is truthy:
    a field at its default (None, an empty tuple, False) is the slot absent.

    .. versionadded:: 5.0.0
    """
    ignored = unhonored_cells(scope, honored)
    # `split` is honored by the filtering templates only for a NAMED half. The
    # bare category means "show me both groups", which is player_splits' whole
    # answer and something they cannot do - so it is refused here rather than
    # quietly filtered to one side or quietly ignored.
    if scope.split == "starter_bench" and intent in _SPLIT_SIDE_ONLY:
        ignored = sorted({*ignored, "split"})
    return ignored


# Templates that read a player name at all - resolving it, filtering on it, or
# refusing because of it. A name the question does not support is only worth
# refusing over where the answer would actually be about that player; for
# `team_record` and `head_to_head` the slot is not read, so a stray one changes
# nothing. Guarded by test_no_template_outside_player_intents_reads_a_player,
# which reads the source rather than trusting this list.
PLAYER_INTENTS: frozenset[str] = frozenset(
    {
        "fingerprint",
        "game_log",
        "leaderboard",
        "player_compare",
        "player_history",
        "player_matchup",
        "player_netpoints",
        "player_splits",
        "period_split",
        "player_stat",
        "record_when",
        "shot_chart",
        "shot_distance",
        "single_game_high",
        "streak",
        "team_quarter_points",
        "threshold_count",
        "with_without",
    }
)
"""Intents whose template reads a ``player`` or ``players`` slot.

.. versionadded:: 2.1.0
"""
