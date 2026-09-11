"""ESPN's shot coordinate frame, and a simplified NBA half-court drawn in it as
a self-contained HTML/SVG page."""

from __future__ import annotations

import math
from typing import Any

# The frame is defined here, once, because this module draws in it: the
# shot-distance math in templates.py and the shot-value rule in shotchart.py
# measure from the same point, and a rim drawn somewhere other than where
# distance is measured from is a silent disagreement no test would catch.

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
"""

RIM_TO_BASELINE = 5.25
"""Feet from the rim's center back to the baseline. Used only to draw the
court: the data has no baseline, only the rim.

.. versionadded:: 2.1.0
"""

THREE_POINT_RADIUS = 23.75
"""The three-point arc's radius from the rim's center, in feet. Unchanged for
every season shot data covers (2002 on).

.. versionadded:: 2.1.0
"""

CORNER_THREE_X = 22
"""The corner three's distance from the rim, measured across the court. The
line runs straight up from the baseline at this distance until it meets the
arc.

.. versionadded:: 2.1.0
"""

CORNER_THREE_Y = math.sqrt(THREE_POINT_RADIUS**2 - CORNER_THREE_X**2)
"""Where the straight corner line meets the arc, in feet above the rim
(8.95).

.. versionadded:: 2.1.0
"""

THREE_POINT_CUTOFF = 23.5
"""The distance beyond which a shot off the corner counts as a three, in
this table's coordinates - a quarter-foot inside the painted 23.75.

The allowance is for the coordinates being whole feet: a shot taken just
beyond the line can be stored at a point that measures 23.5-something. Chosen
by measurement against ESPN's own labels, which it matches on 99.83-99.94% of
shots in every labeled season 2004-2026, against 99.69-99.94% at 23.75.

.. versionadded:: 2.1.0
"""

SHOT_DISTANCE_SQL = f"SQRT(POWER(coordinate_x - {HOOP_X}, 2) + POWER(coordinate_y - {HOOP_Y}, 2))"
"""A ``shot_chart`` row's distance from the rim in feet, as SQL.

.. versionadded:: 2.1.0
"""

HAS_POSITION_SQL = "(coordinate_x IS NOT NULL AND NOT (coordinate_x = 0 AND coordinate_y = 0))"
"""Whether a ``shot_chart`` row records where the shot was taken, as SQL.

NULL is ESPN's "no position" from 2019 on. ``(0, 0)`` is the same thing
earlier: it is a point on the sideline, where nobody can shoot from, and 2002
puts 7,109 shots there - a third of them described as layups or two-pointers.
Every other season has at most 107.

.. versionadded:: 2.1.0
"""

BEYOND_THE_ARC_SQL = f"((ABS(coordinate_x - {HOOP_X}) >= {CORNER_THREE_X} AND coordinate_y - {HOOP_Y} <= {CORNER_THREE_Y:.2f}) OR {SHOT_DISTANCE_SQL} >= {THREE_POINT_CUTOFF})"
"""Whether a positioned ``shot_chart`` row was taken beyond the three-point
line, as SQL: in either corner, or past :data:`THREE_POINT_CUTOFF` anywhere
else. Only meaningful where :data:`HAS_POSITION_SQL` holds.

.. versionadded:: 2.1.0
"""


def render_court_html(title: str, subtitle: str, shots: list[tuple[Any, ...]]) -> str:
    """Simplified NBA half-court in ESPN's shot coordinate system, makes and
    misses as distinct markers.

    Shots are plotted at their coordinates as stored - x 0-50 across the court,
    y in feet from the rim - and the court is drawn around :data:`HOOP_X`,
    :data:`HOOP_Y`, so a shot and the lines it is judged against share one
    frame.

    .. versionchanged:: 2.1.0
       The court is drawn around the rim at (25, 0), where the data puts it.
       It previously put the rim 5.25 feet up from ``y = 0``, which drew every
       shot 5.25 feet short of where it was taken: the typical three landed on
       or inside the arc.
    """
    W, H = 500, 470  # 10px per foot, court width 50 x half-court length 47
    scale = 10
    baseline = HOOP_Y - RIM_TO_BASELINE

    def sx(x: float) -> float:
        """Court x (0-50 feet, sideline to sideline) to SVG x."""
        return x * scale

    def sy(y: float) -> float:
        """Court y (feet from the rim) to SVG y, which grows downward."""
        return H - (y - baseline) * scale  # baseline at bottom

    left_corner, right_corner = HOOP_X - CORNER_THREE_X, HOOP_X + CORNER_THREE_X
    corner_top = HOOP_Y + CORNER_THREE_Y
    court = f"""
      <rect x="0" y="0" width="{W}" height="{H}" fill="var(--court)" stroke="var(--line)" stroke-width="2"/>
      <!-- baseline -->
      <line x1="0" y1="{sy(baseline)}" x2="{W}" y2="{sy(baseline)}" stroke="var(--line)" stroke-width="2"/>
      <!-- backboard, 4ft in from the baseline -->
      <line x1="{sx(HOOP_X - 3)}" y1="{sy(baseline + 4)}" x2="{sx(HOOP_X + 3)}" y2="{sy(baseline + 4)}" stroke="var(--line)" stroke-width="2"/>
      <!-- rim -->
      <circle cx="{sx(HOOP_X)}" cy="{sy(HOOP_Y)}" r="{0.75 * scale}" fill="none" stroke="var(--rim)" stroke-width="2"/>
      <!-- paint, 16ft wide out to the free throw line 19ft from the baseline -->
      <rect x="{sx(HOOP_X - 8)}" y="{sy(baseline + 19)}" width="{16 * scale}" height="{19 * scale}" fill="none" stroke="var(--line)" stroke-width="2"/>
      <!-- free throw circle -->
      <circle cx="{sx(HOOP_X)}" cy="{sy(baseline + 19)}" r="{6 * scale}" fill="none" stroke="var(--line)" stroke-width="1.5" stroke-dasharray="4,3"/>
      <!-- three point arc: straight up each corner, then an arc centered on
           the rim. sweep-flag=1: the arc must bulge away from the baseline
           (toward half court), not back toward it - with sweep-flag=0 here it
           drew the minor arc on the near side of the chord, dipping the arc
           *below* the baseline instead of arcing out toward half court. -->
      <path d="M {sx(left_corner):.2f} {sy(baseline):.2f} L {sx(left_corner):.2f} {sy(corner_top):.2f}
               A {THREE_POINT_RADIUS * scale} {THREE_POINT_RADIUS * scale} 0 0 1 {sx(right_corner):.2f} {sy(corner_top):.2f}
               L {sx(right_corner):.2f} {sy(baseline):.2f}" fill="none" stroke="var(--line)" stroke-width="2"/>
      <!-- half court line -->
      <line x1="0" y1="{sy(baseline + 47)}" x2="{W}" y2="{sy(baseline + 47)}" stroke="var(--line)" stroke-width="1" stroke-dasharray="3,3"/>
    """

    markers = []
    for x, y, made, shot_type, period, clock, event_id in shots:
        color = "var(--make)" if made else "var(--miss)"
        label = f"{'MADE' if made else 'MISS'} - {shot_type} - Q{period} {clock} (game {event_id})"
        if made:
            markers.append(f'<circle cx="{sx(x)}" cy="{sy(y)}" r="5" fill="{color}" fill-opacity="0.75" stroke="{color}" stroke-width="1"><title>{label}</title></circle>')
        else:
            d = 4
            markers.append(
                f'<g stroke="{color}" stroke-width="2" opacity="0.75"><title>{label}</title>'
                f'<line x1="{sx(x) - d}" y1="{sy(y) - d}" x2="{sx(x) + d}" y2="{sy(y) + d}"/>'
                f'<line x1="{sx(x) - d}" y1="{sy(y) + d}" x2="{sx(x) + d}" y2="{sy(y) - d}"/></g>'
            )

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{title} - Shot Chart</title>
<style>
  :root {{
    --bg: #ffffff; --fg: #1a1a1a; --muted: #666666;
    --court: #f7ede1; --line: #b08968; --rim: #d94f4f;
    --make: #2a9d5c; --miss: #c0392b;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #16181d; --fg: #e8e8e8; --muted: #9aa0a6;
      --court: #2a2320; --line: #8a6a4a; --rim: #ff6b6b;
      --make: #4cd07d; --miss: #ff6b6b; }}
  }}
  body {{ background: var(--bg); color: var(--fg); font-family: -apple-system, sans-serif;
          display: flex; flex-direction: column; align-items: center; padding: 24px; }}
  h1 {{ font-size: 20px; margin: 0 0 2px 0; }}
  p.sub {{ color: var(--muted); margin: 0 0 16px 0; font-size: 14px; }}
  .legend {{ display: flex; gap: 20px; margin-top: 12px; font-size: 13px; color: var(--muted); }}
  .legend span {{ display: inline-flex; align-items: center; gap: 6px; }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; background: var(--make); display: inline-block; }}
  .x {{ width: 10px; height: 10px; display: inline-block; position: relative; }}
</style>
</head>
<body>
  <h1>{title}</h1>
  <p class="sub">{subtitle}</p>
  <svg width="{W}" height="{H}" viewBox="0 0 {W} {H}">
    {court}
    {"".join(markers)}
  </svg>
  <div class="legend">
    <span><span class="dot"></span> Made</span>
    <span>&#10005; Missed</span>
  </div>
</body>
</html>
"""
