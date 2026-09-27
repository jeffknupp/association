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
templates onto it. Until the templates read it, :meth:`Scope.to_slots` is the
slot dict they and the compiler read, and :meth:`Scope.from_slots` the one
door a slot dict comes in by, refusing a key or a value nothing here types.

.. versionadded:: 4.5.0
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from association.query.entities import Availability
    from association.query.subject import Companion, Subject

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
    #: The team was put back from the question beside a dropped player.
    team_restored: bool = False
    with_player: tuple[str, ...] = ()
    without: tuple[str, ...] = ()
    #: Each companion with the role the question gives him (``subject.Companion``).
    conditions: tuple[Companion, ...] = ()
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
            raise ValueError(f"scope limit {self.limit} is below 1")

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
            raise ValueError(f"no scope field for slot(s) {unknown}")
        values: dict[str, Any] = {}
        for name, raw in slots.items():
            if raw is None or raw is False or (isinstance(raw, (str, list, tuple)) and not raw):
                continue
            values[name] = _CHECKS[name](name, raw)
        return cls(**values)

    def to_slots(self) -> dict[str, Any]:
        """The slot dict the readers not yet on the Reading take: every field
        away from its default, sequences as lists.

        .. versionadded:: 4.5.0
        """
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if value is None or value is False or value == ():
                continue
            out[f.name] = list(value) if isinstance(value, tuple) else value
        return out


def _text(name: str, raw: Any) -> str:
    if not isinstance(raw, str):
        raise ValueError(f"scope {name}={raw!r} is not text")
    return raw


def _texts(name: str, raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, (list, tuple)) or not all(isinstance(item, str) for item in raw):
        raise ValueError(f"scope {name}={raw!r} is not a list of text")
    return tuple(raw)


def _whole(name: str, raw: Any) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ValueError(f"scope {name}={raw!r} is not a whole number")
    return raw


def _flag(name: str, raw: Any) -> bool:
    if not isinstance(raw, bool):
        raise ValueError(f"scope {name}={raw!r} is not a flag")
    return raw


def _companions(name: str, raw: Any) -> tuple[Any, ...]:
    if not isinstance(raw, (list, tuple)) or not all(hasattr(item, "predicate") for item in raw):
        raise ValueError(f"scope {name}={raw!r} is not a list of companions")
    return tuple(raw)


def _one_of(*allowed: object) -> Callable[[str, Any], Any]:
    def check(name: str, raw: Any) -> Any:
        """``raw`` when it is one of ``allowed``, else the reason it is not."""
        if isinstance(raw, bool) or raw not in allowed:
            raise ValueError(f"scope {name}={raw!r} is not one of {allowed}")
        return raw

    return check


_CHECKS: dict[str, Callable[[str, Any], Any]] = {
    **dict.fromkeys(("player", "team", "opponent", "own_team", "stat", "ranked_by", "rate", "kind", "date", "situation", "round"), _text),
    **dict.fromkeys(("players", "teams", "with_player", "without", "above", "below", "fields"), _texts),
    **dict.fromkeys(("threshold", "season", "since", "until", "game_n", "season_n", "period", "limit"), _whole),
    **dict.fromkeys(("team_restored", "per_game", "season_type_unstated"), _flag),
    "conditions": _companions,
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

    def describe(self) -> str:
        """The one trace line: every field that decides the answer."""
        window = f"{self.order}/{self.direction}" + (f"/{self.limit}" if self.limit is not None else "")
        who = self.subject.kind if self.subject is not None else "?"
        return (
            f"relation={self.relation} subject={who} shape={self.shape} measures={self.measures} aggregate={self.aggregate} "
            f"group={self.group} predicates={self.predicates} window={window} source={self.source} scope={self.scope.to_slots()}"
        )
