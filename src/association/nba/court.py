"""ESPN's shot coordinate frame: where the rim is, where the three-point line
runs, and the SQL that reads a ``shot_chart`` row's distance, position and
side of the arc in it.

Both the fetch path (the period line's completeness table,
:mod:`association.fetch.repairs.plays_complete`, values every shot through
:data:`association.nba.shot_value.SHOT_VALUE_SQL`, which reads the arc) and
the query path (the court the chart is drawn on, :mod:`association.query.court`;
the shot reads, :mod:`association.query.shots`) measure in this frame, so it
is defined here, once.

.. versionadded:: 7.0.0
   The frame and its SQL moved here from :mod:`association.query.court`,
   which keeps the drawing.
"""

from __future__ import annotations

import math

# The court is drawn in this frame (query/court.py), and the shot-distance
# math (query/shots.py) and the shot-value rule (nba/shot_value.py) measure
# from the same point: a rim drawn somewhere other than where distance is
# measured from is a silent disagreement no test would catch.

HOOP_X = 25
"""The rim's x in ESPN's shot coordinates, in feet: x runs 0-50 from one
sideline to the other."""

HOOP_Y = 0
"""The rim's y in ESPN's shot coordinates, in feet. **y is measured from the
rim, not the baseline** - the baseline sits at -5.25, which is why the table's
smallest y is -5.

Measured, not assumed. Most shot descriptions carry their own distance ("makes
26-foot three point jumper"), and from 2002 through 2012 that distance equals
``round(hypot(x - 25, y))`` for every one of them - 100.0% of 1.3 million shots.
From 2013 the coordinates are rounded from a finer position and 99.8% agree to
within a foot. No y-scale is needed: fitting one alongside the hoop gives
1.000.

From 2023-11-02 on, the descriptions run 0.64 feet short of this point, as if
the hoop had moved a foot. The coordinates did not: the three-point line
separates ESPN's own two- and three-point labels exactly as well in 2024-2026
(99.93%) as in 2023 (99.93%), and measurably worse from a hoop a foot out
(99.72%). The prose changed overnight; the frame did not.

.. versionchanged:: 2.1.0
   Was 5.25, the rim's distance from the baseline, on the assumption that y
   started there. It does not, so every distance came out short - Stephen
   Curry's 2026 threes averaged 23.6 feet, inside a 23.75-foot line, and a
   line-geometry rule called only 35,374 of 2023's 89,908 labeled threes a
   three. The chart drew each shot 5.25 feet closer to the baseline than it
   was taken.

.. versionchanged:: 7.0.0
   Moved from ``association.query.court``.
"""

RIM_TO_BASELINE = 5.25
"""Feet from the rim's center back to the baseline. Used only to draw the
court: the data has no baseline, only the rim.

.. versionadded:: 2.1.0

.. versionchanged:: 7.0.0
   Moved from ``association.query.court``.
"""

THREE_POINT_RADIUS = 23.75
"""The three-point arc's radius from the rim's center, in feet. Unchanged for
every season shot data covers (2002 on).

.. versionadded:: 2.1.0

.. versionchanged:: 7.0.0
   Moved from ``association.query.court``.
"""

CORNER_THREE_X = 22
"""The corner three's distance from the rim, measured across the court. The
line runs straight up from the baseline at this distance until it meets the
arc.

.. versionadded:: 2.1.0

.. versionchanged:: 7.0.0
   Moved from ``association.query.court``.
"""

CORNER_THREE_Y = math.sqrt(THREE_POINT_RADIUS**2 - CORNER_THREE_X**2)
"""Where the straight corner line meets the arc, in feet above the rim
(8.95).

.. versionadded:: 2.1.0

.. versionchanged:: 7.0.0
   Moved from ``association.query.court``.
"""

THREE_POINT_CUTOFF = 23.5
"""The distance beyond which a shot off the corner counts as a three, in
this table's coordinates - a quarter-foot inside the painted 23.75.

The allowance is for the coordinates being whole feet: a shot taken just
beyond the line can be stored at a point that measures 23.5-something. Chosen
by measurement against ESPN's own labels, which it matches on 99.83-99.94% of
shots in every labeled season 2004-2026, against 99.69-99.94% at 23.75.

.. versionadded:: 2.1.0

.. versionchanged:: 7.0.0
   Moved from ``association.query.court``.
"""

SHOT_DISTANCE_SQL = f"SQRT(POWER(coordinate_x - {HOOP_X}, 2) + POWER(coordinate_y - {HOOP_Y}, 2))"
"""A ``shot_chart`` row's distance from the rim in feet, as SQL.

.. versionadded:: 2.1.0

.. versionchanged:: 7.0.0
   Moved from ``association.query.court``.
"""

HAS_POSITION_SQL = "(coordinate_x IS NOT NULL AND NOT (coordinate_x = 0 AND coordinate_y = 0))"
"""Whether a ``shot_chart`` row records where the shot was taken, as SQL.

NULL is ESPN's "no position" from 2019 on. ``(0, 0)`` is the same thing
earlier: it is a point on the sideline, where nobody can shoot from, and 2002
puts 7,109 shots there - a third of them described as layups or two-pointers.
Every other season has at most 107.

.. versionadded:: 2.1.0

.. versionchanged:: 7.0.0
   Moved from ``association.query.court``.
"""

BEYOND_THE_ARC_SQL = f"((ABS(coordinate_x - {HOOP_X}) >= {CORNER_THREE_X} AND coordinate_y - {HOOP_Y} <= {CORNER_THREE_Y:.2f}) OR {SHOT_DISTANCE_SQL} >= {THREE_POINT_CUTOFF})"
"""Whether a positioned ``shot_chart`` row was taken beyond the three-point
line, as SQL: in either corner, or past :data:`THREE_POINT_CUTOFF` anywhere
else. Only meaningful where :data:`HAS_POSITION_SQL` holds.

.. versionadded:: 2.1.0

.. versionchanged:: 7.0.0
   Moved from ``association.query.court``.
"""
