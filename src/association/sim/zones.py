"""Which zone a shot was taken from, by one rule in Python and in SQL.

Phase 4.1 of ``SIMULATION_PLAN.md``. A shot's zone is read in the rim-origin
frame the warehouse's shot chart uses (``DATA.md``, "Shot coordinates measure
y from the rim"; :mod:`association.query.court` holds the frame and the
three-point geometry, imported here rather than copied): the restricted
area under :data:`RESTRICTED_RADIUS`, the paint from there to
:data:`PAINT_RADIUS`, mid-range from there to the arc, the corner three
(beyond the arc, ``|x - 25| >= CORNER_THREE_X`` and ``y <= CORNER_THREE_Y``)
and the above-the-break three elsewhere beyond it. Two more names from
:mod:`association.sim.params` cover what the zones cannot: :data:`HEAVE` for
a three from :data:`HEAVE_DISTANCE` feet or more with under
:data:`HEAVE_SECONDS` on the period clock, or typed :data:`HEAVE_TYPE` (2026's
label; earlier seasons' heaves are ordinary ``Jump Shot`` rows and need the
geometry), and :data:`UNKNOWN_TWO` / :data:`UNKNOWN_THREE` for a shot with
no position whose value is known. A free throw, and a shot with neither a
position nor a value, have no zone (``None`` / ``NULL``).

**The value wins over the geometry.** Where ESPN labeled the shot a two or a
three (``shot_value`` through the shared ``SHOT_VALUE_SQL``), the zone is on
that side of the arc whatever the rounded coordinates say - a labeled two
stored a quarter-foot past the line is mid-range, a labeled three stored
inside it is above the break - so ``params.value_of(zone)`` always agrees
with the points the shot scored. Measured 2019-2026 (``scripts/sim_p4/
zone_table.py``), 152-211 positioned field goals a season (0.07-0.09%) have a
label on the other side of the arc from their coordinates; 2022 labels only
4% of its shots and the geometry decides the rest (``DATA.md``).

:func:`zone_of` is the rule for one shot; :func:`zone_sql` is the same rule
as a SQL ``CASE`` over ``nba.shot_chart``'s coordinate columns with the
value, the clock and the type as expressions, so an estimate aggregates
zones in DuckDB and a test holds the two to the same answer on the same
points.

**Measured on this checkout's files, 2019-2026, every charted field goal of a
real game** (``scripts/sim_p4/zone_table.py``, output in
``scripts/sim_p4/results/zones_2026-10-11.out``; the clock from
``sim_plays``, the value from ``sim_plays.shot_value``). Share of shots per
zone, then make rate, by season (202,242-234,997 shots a season; the unknown
zones hold 0-13 shots a season and are left out):

=========== ========== ===== ======== ============ ================= =====
season      restricted paint midrange corner_three above_break_three heave
=========== ========== ===== ======== ============ ================= =====
2019 share    31.4%    19.9%  12.7%     8.1%          27.6%           0.30%
2019 made     63.9%    40.1%  40.5%    38.1%          35.0%           4.1%
2020 share    30.5%    19.8%  11.1%     8.7%          29.7%           0.30%
2020 made     64.6%    40.1%  40.8%    39.0%          35.2%           3.2%
2021 share    28.2%    21.9%  10.8%     9.1%          29.9%           0.25%
2021 made     65.2%    42.8%  41.8%    39.3%          36.1%           4.0%
2022 share    26.9%    22.5%  10.5%     9.9%          29.9%           0.29%
2022 made     66.6%    43.0%  41.0%    38.3%          34.7%           4.5%
2023 share    27.7%    23.9%   9.7%     9.6%          28.9%           0.28%
2023 made     67.8%    44.5%  42.5%    38.6%          35.5%           5.6%
2024 share    27.3%    24.1%   9.1%    10.0%          29.2%           0.28%
2024 made     67.5%    44.2%  42.4%    38.8%          36.0%           3.2%
2025 share    25.6%    24.5%   7.8%    10.6%          31.2%           0.34%
2025 made     67.6%    44.7%  41.4%    38.7%          35.4%           4.8%
2026 share    26.1%    24.3%   7.9%    10.5%          30.7%           0.53%
2026 made     68.1%    45.0%  41.2%    38.6%          35.0%           3.1%
=========== ========== ===== ======== ============ ================= =====

The restricted area makes 64-68% and the corner three 38-39% in every
season, as expected, and a heave goes in 3-6% of the time. From 2019 every
charted field goal has a zone and 1-13 a season lack a position (plan 0b,
check 0.9). 2026 is the first season ESPN types heaves (1,153, none made):
7 of them the geometric rule would not reach (inside 35 feet or over 3
seconds), and 91 more shots meet the geometric rule and are typed as
ordinary jump shots, so the two definitions are both kept. On the 6,387
distinct ``(x, y, value, clock under 3, typed heave)`` points those seasons
hold, :func:`zone_of` and :func:`zone_sql` agree on every one.

.. versionadded:: 6.0.0
"""

from __future__ import annotations

import math

from association.query.court import BEYOND_THE_ARC_SQL, CORNER_THREE_X, CORNER_THREE_Y, HAS_POSITION_SQL, HOOP_X, HOOP_Y, SHOT_DISTANCE_SQL, THREE_POINT_CUTOFF
from association.sim.params import HEAVE, UNKNOWN_THREE, UNKNOWN_TWO

RESTRICTED_RADIUS = 4.0
"""Feet from the rim's center within which a shot is in the restricted area.

.. versionadded:: 6.0.0
"""

PAINT_RADIUS = 14.0
"""Feet from the rim's center within which a two is in the paint (and not the
restricted area); beyond it a two is mid-range.

.. versionadded:: 6.0.0
"""

HEAVE_DISTANCE = 35.0
"""Feet from the rim at or beyond which a three with the period clock nearly
out is a heave rather than a shot.

.. versionadded:: 6.0.0
"""

HEAVE_SECONDS = 3.0
"""Seconds left in the period under which a long three is a heave.

.. versionadded:: 6.0.0
"""

HEAVE_TYPE = "Heave Jump Shot"
"""ESPN's own label for a heave, used from the 2026 season only (plan 0b,
check 0.9). A shot so typed is a heave wherever and whenever it was taken.

.. versionadded:: 6.0.0
"""

RESTRICTED = "restricted"
"""The zone under :data:`RESTRICTED_RADIUS`; one of ``params.ZONES``."""
PAINT = "paint"
"""The zone from :data:`RESTRICTED_RADIUS` to :data:`PAINT_RADIUS`."""
MIDRANGE = "midrange"
"""The two from :data:`PAINT_RADIUS` to the arc."""
CORNER_THREE = "corner_three"
"""The three from either corner, below the corner's top (``CORNER_THREE_Y``)."""
ABOVE_BREAK_THREE = "above_break_three"
"""The three from anywhere else beyond the arc."""


def has_position(x: float | None, y: float | None) -> bool:
    """Whether a shot has a recorded position: :data:`HAS_POSITION_SQL`'s rule
    - not NULL, and not the sideline point ``(0, 0)`` ESPN writes for "no
    position" in the seasons before it wrote NULL.

    .. versionadded:: 6.0.0
    """
    return x is not None and y is not None and not (x == 0 and y == 0)


def _in_corner(dx: float, dy: float) -> bool:
    return abs(dx) >= CORNER_THREE_X and dy <= CORNER_THREE_Y


def _is_three(value: int | None, dx: float, dy: float, distance: float) -> bool:
    """The label where there is one, the geometry where there is not."""
    if value in (2, 3):
        return value == 3
    return _in_corner(dx, dy) or distance >= THREE_POINT_CUTOFF


def zone_of(x: float | None, y: float | None, value: int | None, clock_remaining: float | None, shot_type: str | None) -> str | None:
    """The zone of one shot, or ``None`` for a free throw or a shot with
    neither a position nor a value.

    ``x`` and ``y`` are the shot chart's coordinates (rim-origin, feet),
    ``value`` the shot's worth from the shared ``SHOT_VALUE_SQL`` (1, 2, 3 or
    ``None``), ``clock_remaining`` the seconds left in the period from
    ``sim_plays`` (``None`` where unknown, which rules out the geometric
    heave) and ``shot_type`` ESPN's type.

    .. versionadded:: 6.0.0
    """
    if value == 1 or (shot_type is not None and "free throw" in shot_type.lower()):
        return None
    if shot_type == HEAVE_TYPE:
        return HEAVE
    if not has_position(x, y):
        return {2: UNKNOWN_TWO, 3: UNKNOWN_THREE}.get(value or 0)
    assert x is not None and y is not None
    dx, dy = x - HOOP_X, y - HOOP_Y
    distance = math.hypot(dx, dy)
    if _is_three(value, dx, dy, distance):
        if distance >= HEAVE_DISTANCE and clock_remaining is not None and clock_remaining < HEAVE_SECONDS:
            return HEAVE
        return CORNER_THREE if _in_corner(dx, dy) else ABOVE_BREAK_THREE
    if distance < RESTRICTED_RADIUS:
        return RESTRICTED
    if distance < PAINT_RADIUS:
        return PAINT
    return MIDRANGE


def zone_sql(value: str, clock_remaining: str, shot_type: str) -> str:
    """:func:`zone_of` as a SQL ``CASE`` expression, for a statement whose
    ``coordinate_x`` and ``coordinate_y`` resolve to ``nba.shot_chart``'s
    columns (the court module's SQL names them bare). ``value``,
    ``clock_remaining`` and ``shot_type`` are SQL expressions written in
    code - ``"sp.shot_value"``, ``"sp.clock_remaining"``, ``"sc.shot_type"`` -
    never values from outside.

    .. versionadded:: 6.0.0
    """
    corner = f"(ABS(coordinate_x - {HOOP_X}) >= {CORNER_THREE_X} AND coordinate_y - {HOOP_Y} <= {CORNER_THREE_Y:.2f})"
    return f"""(CASE
    WHEN {value} = 1 OR {shot_type} ILIKE '%free throw%' THEN NULL
    WHEN {shot_type} = '{HEAVE_TYPE}' THEN '{HEAVE}'
    WHEN NOT {HAS_POSITION_SQL} THEN (CASE WHEN {value} = 2 THEN '{UNKNOWN_TWO}' WHEN {value} = 3 THEN '{UNKNOWN_THREE}' END)
    WHEN (CASE WHEN {value} IN (2, 3) THEN {value} = 3 ELSE {BEYOND_THE_ARC_SQL} END) THEN
        (CASE WHEN {SHOT_DISTANCE_SQL} >= {HEAVE_DISTANCE} AND {clock_remaining} < {HEAVE_SECONDS} THEN '{HEAVE}'
              WHEN {corner} THEN '{CORNER_THREE}'
              ELSE '{ABOVE_BREAK_THREE}' END)
    WHEN {SHOT_DISTANCE_SQL} < {RESTRICTED_RADIUS} THEN '{RESTRICTED}'
    WHEN {SHOT_DISTANCE_SQL} < {PAINT_RADIUS} THEN '{PAINT}'
    ELSE '{MIDRANGE}'
END)"""
