"""A team's own season, read: its line and where it ranks (``team_stat``),
every team ranked by one metric of the line or the standings
(``team_leaderboard``), and its place in ESPN's power index
(``team_outlook``) - the two team-season relations of ``ROADMAP-TYPES.md``
(``team_seasons``, ``team_snapshots``), beside the team-games relation the
team compiler reads (:mod:`~association.query.compose.team`).

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from association.query.reading import Scope


@dataclass(frozen=True, kw_only=True)
class TeamSeasonQuery:
    """A point on a team-season relation: the question's scoping, the
    relation (``team_seasons`` for the line and the standings,
    ``team_snapshots`` for the power index) and the shape (one team's
    ``scalar``, or every team ``grouped`` by the metric ranked). Which
    metric the ``stat`` slot names is the reader's to resolve, as it was the
    retired templates'. The team-season counterpart of
    :class:`~association.query.compose.team.TeamQuery`.

    .. versionadded:: 5.0.0
    """

    #: The question's scoping, read whole by the relation's reader.
    scope: Scope
    relation: Literal["team_seasons", "team_snapshots"] = "team_seasons"
    shape: Literal["scalar", "grouped"] = "scalar"
