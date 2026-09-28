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

.. versionadded:: 4.5.0
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields
from datetime import date
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from association.query.decisions import Decision
    from association.query.entities import Availability
    from association.query.subject import Subject

Shape = Literal["rows", "scalar", "grouped"]
"""Which reader answers the point: rows, one number, or one row per group.

.. versionadded:: 4.5.0
"""

Aggregate = Literal["none", "per_game", "total", "count", "max", "min", "rate", "record"]
"""How the rows are reduced.

.. versionadded:: 4.5.0
"""

Group = Literal["none", "venue", "starter", "season", "season_type", "month", "opponent", "won", "player"]
"""``"none"`` or a key of :data:`~association.query.compose.core.GROUPS` (a
test holds the two to the same names).

.. versionadded:: 4.5.0
"""

Relation = Literal["player", "everyone", "team"]
"""Which relation answers: one named player's games, the league's, or a team's.

.. versionadded:: 4.5.0
"""

SeasonType = Literal[2, 3]
"""ESPN's season type: 2 the regular season, 3 the postseason.

.. versionadded:: 4.5.0
"""

Split = Literal["home_away", "starter_bench", "wins_losses", "month", "starter", "bench"]
"""A split the question asks for: the two-sided category, or the one side
("starter", "bench") a filtering template honors.

.. versionadded:: 4.5.0
"""


class ScopeError(ValueError):
    """A slot value the Scope cannot hold - a key nothing types, a value of
    the wrong type or outside its closed set, a window of fewer than one
    game. Raised where a slot dict comes in (:meth:`Scope.from_slots`), so
    the question falls through the way a template's refusal does rather than
    an answer quietly leaving the narrowing out.

    .. versionadded:: 4.5.0
    """


@dataclass(frozen=True, kw_only=True)
class ConditionSpec:
    """One player named beside the subject and the role the question gives
    him in the games asked about - one ``conditions`` entry, as the relation
    reads it (``templates.common._condition_from_slot``): "when Embiid and
    Paul George start", "in games Maxey had 20+ points". ``stat`` and
    ``threshold`` belong to a ``reached`` role.

    .. versionadded:: 4.5.0
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

        .. versionadded:: 4.5.0
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

        .. versionadded:: 4.5.0
        """
        line = {key: value for key, value in (("stat", self.stat), ("threshold", self.threshold)) if value is not None}
        return {"player": self.player, "side": self.side, "predicate": self.predicate, **line}


@dataclass(frozen=True, kw_only=True)
class Scope:
    """What narrows the answer, one typed field per scoping slot. A field at
    its default (None, empty, False) is the slot absent - the reading every
    reader already takes of a falsy slot (``check_scope`` asks
    ``slots.get(name)``). The names are here too, as the question gave them;
    resolving them against the warehouse happens where each is read.

    .. versionadded:: 4.5.0
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
    #: When.
    season: int | None = None
    season_type: SeasonType | None = None
    #: No season type was named, and the answer reads both.
    season_type_unstated: bool = False
    span: Literal["career"] | None = None
    since: int | None = None
    until: int | None = None
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

        .. versionadded:: 4.5.0
        """
        unknown = sorted(set(slots) - _SCOPE_FIELDS)
        if unknown:
            raise ScopeError(f"no scope field for slot(s) {unknown}")
        values: dict[str, Any] = {}
        for name, raw in slots.items():
            if raw is None or raw is False or (isinstance(raw, (str, list, tuple)) and not raw):
                continue
            values[name] = _CHECKS[name](name, raw)
        return cls(**values)

    def to_slots(self) -> dict[str, Any]:
        """The Scope as a slot dict - every field away from its default,
        sequences as lists: the shape a route is recorded in and the trace
        prints (:meth:`Reading.describe`).

        .. versionadded:: 4.5.0
        """
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if value is None or value is False or value == ():
                continue
            if f.name == "conditions":
                out[f.name] = [condition.to_slot() for condition in value]
            else:
                out[f.name] = list(value) if isinstance(value, tuple) else value
        return out


def _text(name: str, raw: Any) -> str:
    if not isinstance(raw, str):
        raise ScopeError(f"scope {name}={raw!r} is not text")
    return raw


def _texts(name: str, raw: Any) -> tuple[str, ...]:
    # A bare string is one item: the readers have always taken it so
    # (``templates.common.teammate_names``: slot values are advisory, and a
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


@dataclass(frozen=True, kw_only=True)
class Reading:
    """One question, read. The point fields (shape, measures, aggregate,
    group, predicates, order, direction, limit) mirror
    :class:`~association.query.compose.core.Query` on purpose: the planner
    is a copy, not a second decision. Every construction names its fields.

    .. versionadded:: 4.5.0
    """

    #: The scoping the relation narrows by.
    scope: Scope = field(default_factory=Scope)
    shape: Shape = "rows"
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
    span: Literal["career"] | None = None
    season: int | None = None
    #: ``"games"`` (the player-games relation) or ``"seasons"`` (the season line).
    source: Literal["games", "seasons"] = "games"
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

    @classmethod
    def from_slots(cls, slots: Mapping[str, Any], *, intent: str = "", subject: Subject | None = None) -> Reading:
        """A Reading holding nothing but the scope ``slots`` names (through
        :meth:`Scope.from_slots`), for the readers that still build one from a
        slot dict: the agent's dispatch of a routed question to its template,
        a template handing a question to another, and the tests.

        .. versionadded:: 4.5.0
        """
        return cls(scope=Scope.from_slots(slots), intent=intent, subject=subject)

    def describe(self) -> str:
        """The one trace line: every field that decides the answer."""
        window = f"{self.order}/{self.direction}" + (f"/{self.limit}" if self.limit is not None else "")
        who = self.subject.kind if self.subject is not None else "?"
        return (
            f"relation={self.relation} subject={who} shape={self.shape} measures={self.measures} aggregate={self.aggregate} "
            f"group={self.group} predicates={self.predicates} window={window} source={self.source} scope={self.scope.to_slots()}"
        )
