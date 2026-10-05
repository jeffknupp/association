"""The NetPoints relation's readers: a player's NetPoints (``player_netpoints``)
and his play-type fingerprint (``fingerprint``), read from ESPN Analytics'
NetPoints tables into a :class:`~association.query.result.Result` -
Phase 2's slice (v) (``ROADMAP.md``, step 5, and the decision "Charts are
declared shapes": each keeps its own reader and renderer, declared with its
relation, and is not ported onto a NetPoints relation before Phase 3).

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from association.query.reading import Scope


@dataclass(frozen=True)
class NetPointsQuery:
    """A point on the NetPoints relation, planned: the scope the question
    settled and the shape the reader names - one player's NetPoints
    (``"scalar"``: a season's ratings and the split by play type behind
    them, or one game's) or his fingerprint (``"chart"``). The reader reads
    the player, the season, its type, the unit, the side and a first or
    last game from the scope, as the retired templates read their slots.

    .. versionadded:: 5.0.0
    """

    scope: Scope
    shape: Literal["scalar", "chart"]
    relation: Literal["netpoints"] = "netpoints"
    #: Whose the point is - one or more named players - for the stage
    #: record, which names a planned query's subject.
    subject: Literal["player"] = "player"
