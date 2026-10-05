"""One player's located shots - drawn on a court (``shot_chart``) or
averaged by distance from the rim (``shot_distance``): a declared relation
with its own reader (``ROADMAP.md``, "Charts are declared shapes"), not
ported onto the player-games relation until after Phase 3.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from association.query.reading import Scope


@dataclass(frozen=True)
class ShotQuery:
    """A point on the shot relation: the question's scoping, read whole by
    the relation's reader, and the shape - the shots drawn (``chart``) or
    their average distance (``scalar``). ``subject`` is whose shots they
    are, always one named player's. The shot relation's counterpart of
    :class:`~association.query.compose.core.Query`.

    .. versionadded:: 5.0.0
    """

    #: The question's scoping, read whole by the relation's reader.
    scope: Scope
    shape: Literal["chart", "scalar"] = "chart"
    subject: Literal["player"] = "player"
