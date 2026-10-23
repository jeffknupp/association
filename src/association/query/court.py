"""ESPN's shot coordinate frame, and a simplified NBA half-court drawn in it as
a self-contained HTML/SVG page."""

from __future__ import annotations

from typing import Any

from association.nba.court import CORNER_THREE_X, CORNER_THREE_Y, HOOP_X, HOOP_Y, RIM_TO_BASELINE, THREE_POINT_RADIUS

# The frame is ESPN's (association.nba.court): the court is drawn around the
# rim and the lines a shot's value and distance are measured against.


def render_court_html(title: str, subtitle: str, shots: list[tuple[Any, ...]]) -> str:
    """Simplified NBA half-court in ESPN's shot coordinate system, makes and
    misses as distinct markers.

    Shots are plotted at their coordinates as stored - x 0-50 across the court,
    y in feet from the rim - and the court is drawn around :data:`~association.nba.court.HOOP_X`,
    :data:`~association.nba.court.HOOP_Y`, so a shot and the lines it is judged against share one
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
  /* Scale down, never up, to the width available: in the web page's frame on
     a phone that is about 300px against the court's 500, and a fixed width
     clipped the court's far side. The viewBox keeps the geometry exact. */
  svg {{ max-width: 100%; height: auto; }}
  @media (max-width: 520px) {{ body {{ padding: 12px; }} h1 {{ font-size: 17px; }} }}
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
