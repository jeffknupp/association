"""What a shot is worth: a ``shot_chart`` row's value read from its columns
(:data:`SHOT_VALUE_SQL`), and the seasons whose twos and threes cannot be
told apart or are derived.

Both the fetch path (the period line's completeness table,
:mod:`association.fetch.repairs.plays_complete`) and the query path (every
shot-value filter, the period line) read a shot's value, so the one rule is
defined here.

.. versionadded:: 7.0.0
   Moved here from :mod:`association.query.shotchart`, which keeps the
   chart's player resolution.
"""

from __future__ import annotations

from association.nba.court import BEYOND_THE_ARC_SQL, HAS_POSITION_SQL

UNSEPARABLE_SHOT_VALUES: dict[int, str] = {
    2002: (
        "ESPN did not label 2002's shots as twos or threes, and unlike every later season its shot descriptions do not name every three - counted against the "
        "box score they miss about one in sixty - so threes cannot be reliably separated from twos"
    ),
}
"""Seasons where nothing reliably says whether a shot was a two or a three, by
season, with the reason. A question filtered to twos or threes in one of them
is refused rather than answered from whatever subset happens to be labeled.

.. versionadded:: 2.1.0

.. versionchanged:: 7.0.0
   Moved from ``association.query.shotchart``.
"""

DERIVED_SHOT_VALUES: dict[int, str] = {
    2003: ("ESPN did not label 2003's shots as twos or threes, so they are read from each shot's description, whose count of threes matches the box score's to within 0.01%"),
    2022: (
        "ESPN labeled only 4% of 2022's shots as twos or threes, so the rest are read from the description where it says and otherwise from where the shot "
        "was taken against the three-point line, which counts 0.2% more threes than the box score does"
    ),
}
"""Seasons whose shot values are mostly derived rather than labeled by ESPN,
with the caveat an answer filtered to twos or threes carries.

.. versionadded:: 2.1.0

.. versionchanged:: 7.0.0
   Moved from ``association.query.shotchart``.
"""

TEXT_NAMES_EVERY_THREE_UNTIL = 2012
"""The last season whose shot descriptions name every three.

Through 2012 "three point" is in the description of every shot ESPN labels a
three and of no shot it labels a two - 100.00% agreement in each of 2004-2012.
From 2013 step-backs and pull-ups stop saying so (99.66% in 2013, 95.45% by
2024), and only the shot's position is left to decide.

.. versionadded:: 2.1.0

.. versionchanged:: 7.0.0
   Moved from ``association.query.shotchart``.
"""

_UNSEPARABLE = ", ".join(str(s) for s in sorted(UNSEPARABLE_SHOT_VALUES))

SHOT_VALUE_SQL = f"""(CASE
    WHEN shot_type ILIKE '%free throw%' THEN 1
    WHEN points_attempted IN (2, 3) THEN points_attempted
    WHEN description ILIKE '%three point%' THEN 3
    WHEN description ILIKE '%two point%' THEN 2
    WHEN season IN ({_UNSEPARABLE}) THEN NULL
    WHEN season <= {TEXT_NAMES_EVERY_THREE_UNTIL} THEN 2
    WHEN NOT {HAS_POSITION_SQL} THEN NULL
    WHEN {BEYOND_THE_ARC_SQL} THEN 3
    ELSE 2
END)"""
"""A ``shot_chart`` row's value - 1, 2 or 3 - as SQL, or NULL where nothing
establishes it. The one definition every shot-value filter reads.

``points_attempted`` cannot be filtered on directly, because **0 there means
unlabeled, not zero points**: every shot of 2002 and 2003, 96% of 2022's, and
about a quarter of each season's from 2004 to 2012 - every one of those a miss,
so "his twos" answered from the labels alone came out at a 72% field goal
percentage. So the label is used where there is one, and otherwise, in order:

- ``shot_type`` for free throws, which are unlabeled in 2002, 2003 and 2022;
- the description, where it says "three point" or "two point" - a label ESPN
  wrote in prose, which disagrees with its numeric label on at most 3 shots a
  season;
- through :data:`TEXT_NAMES_EVERY_THREE_UNTIL`, a two, since those seasons'
  descriptions name every three;
- after it, the shot's position against the three-point line
  (:data:`association.nba.court.BEYOND_THE_ARC_SQL`), which matches ESPN's
  own labels on 99.83-99.94% of shots in every season it labeled.

Counted against the box score's three-point attempts per player-game, the
result matches in 99.3-100% of games in every season but 2002 - which is
:data:`UNSEPARABLE_SHOT_VALUES`, and NULL here for any shot neither labeled nor
described.

.. versionadded:: 2.1.0

.. versionchanged:: 7.0.0
   Moved from ``association.query.shotchart``.
"""
