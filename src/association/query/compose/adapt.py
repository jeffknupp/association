"""Router intent + slots -> :class:`~association.query.compose.core.Query`:
each intent is the default point of the algebra, as code, for the six
templates on the player-games relation where they read box scores.

.. versionadded:: 4.4.0
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from association.query.reading import Reading, Scope
from association.query.templates.splits import _with_without_named

from .core import Unsupported

WITH_WITHOUT_STATED: frozenset[str] = frozenset({"span", "without", "opponent", "conditions"})
"""The scoping ``with_without``'s words state - a career, the teammates
divided by, one opponent (both rows narrow together, #163) and a
companion's role - the retired template's own declaration; any other
narrowing is refused by name.

.. versionadded:: 5.0.0
"""


def _adapt_with_without(scope: Scope) -> Reading:
    """``with_without``'s default point: a team's record in the games named
    teammates played against the games they missed - the team relation's
    ``presence`` group (``compose.team._compile_team_presence``) - with the
    subject's averages in each where a player is named. The retired
    template's own early refusals are the point's (ROADMAP plan item 6, step
    (g)): a narrowing its words do not state (:data:`WITH_WITHOUT_STATED`),
    and no teammate to divide by - the teammates come from ``without`` or
    ``with_player``, a ``conditions`` role, or failing those the one name
    beside a team or the second of two; more than that is "record when A
    and B and C play", which nobody has defined.

    .. versionadded:: 5.0.0
    """
    mate_texts, _asked_without, _roles = _with_without_named(scope)
    if not mate_texts:
        texts = list(dict.fromkeys(n.strip() for n in (scope.player, *scope.players) if n is not None and n.strip()))
        team_named = bool(scope.team and scope.team.strip())
        if not ((team_named and len(texts) == 1) or (not team_named and len(texts) == 2)):
            raise Unsupported(f"with_without needs exactly one teammate, got {texts!r}")
    return Reading(scope=scope, shape="grouped", measures=["record"], aggregate="record", group="presence", predicates=[], relation="team")


#: Intent -> its default-point adapter, over the typed scope. Kept as a
#: mapping rather than an if/elif chain so a new intent is one entry, not a
#: longer function.
_ADAPTERS: dict[str, Callable[[Scope], Reading]] = {
    "with_without": _adapt_with_without,
}


def to_reading(intent: str, slots: dict[str, Any]) -> Reading:
    """The intent's default point of the algebra: the query a bare router
    intent means before any of the question's own words move it (see
    :func:`association.query.point.read_point`). ``slots`` comes in
    by :meth:`~association.query.reading.Scope.from_slots`, the one door a
    slot dict has, and the adapters read the typed scope.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       An unnarrowed ``player_stat`` is a point on the season line
       (``source="seasons"``) rather than :class:`~association.query.compose.core.Unsupported`.

    .. versionchanged:: 5.0.0
       ``game_log`` with ``season_type_unstated`` ("his last 5 games") is a
       point - both season types, which the relation reads at once - rather
       than :class:`~association.query.compose.core.Unsupported`.
    """
    from association.query.point import default_point

    return default_point(intent, Scope.from_slots(slots))


def _to_reading_scope(intent: str, scope: Scope) -> Reading:
    """:func:`to_reading` over a scope already read - for the compiler's own
    moves (:func:`association.query.point.read_point` reads the scope
    once) and the presenters, which have the Query's scope and no slot dict."""
    adapter = _ADAPTERS.get(intent)
    if adapter is None:
        raise Unsupported(f"no adapter for {intent}")
    # The point says whose default it is: the planner declines by it
    # (compose.plan._shape_declines) until intent leaves the reader in
    # Phase 3.
    return replace(adapter(scope), intent=intent)
