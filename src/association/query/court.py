"""Renders a simplified NBA half-court as a self-contained HTML/SVG page."""

from __future__ import annotations

from typing import Any

# The hoop's position in ESPN's shot coordinate system, in feet - NOT the
# origin. Defined here because this module owns that coordinate system; the
# shot-distance math in templates.py measures from the same point, and a rim
# drawn somewhere other than where distance is measured from is a silent
# disagreement no test would catch.
HOOP_X, HOOP_Y = 25, 5.25


def render_court_html(title: str, subtitle: str, shots: list[tuple[Any, ...]]) -> str:
    """Simplified NBA half-court in ESPN's shot coordinate system (x: 0-50 court width,
    y: 0 at baseline increasing toward half court), makes/misses as distinct markers."""
    W, H = 500, 470  # 10px per foot, court width 50 x half-court length 47
    scale = 10

    def sx(x: float) -> float:
        """Court x (0-50 feet, sideline to sideline) to SVG x."""
        return x * scale

    def sy(y: float) -> float:
        """Court y (0 at the baseline) to SVG y, which grows downward."""
        return H - y * scale  # baseline at bottom

    court = f"""
      <rect x="0" y="0" width="{W}" height="{H}" fill="var(--court)" stroke="var(--line)" stroke-width="2"/>
      <!-- baseline -->
      <line x1="0" y1="{sy(0)}" x2="{W}" y2="{sy(0)}" stroke="var(--line)" stroke-width="2"/>
      <!-- backboard -->
      <line x1="{sx(22)}" y1="{sy(4)}" x2="{sx(28)}" y2="{sy(4)}" stroke="var(--line)" stroke-width="2"/>
      <!-- rim -->
      <circle cx="{sx(HOOP_X)}" cy="{sy(HOOP_Y)}" r="{0.75 * scale}" fill="none" stroke="var(--rim)" stroke-width="2"/>
      <!-- paint -->
      <rect x="{sx(17)}" y="{sy(19)}" width="{16 * scale}" height="{19 * scale}" fill="none" stroke="var(--line)" stroke-width="2"/>
      <!-- free throw circle -->
      <circle cx="{sx(25)}" cy="{sy(19)}" r="{6 * scale}" fill="none" stroke="var(--line)" stroke-width="1.5" stroke-dasharray="4,3"/>
      <!-- three point arc (approx, radius 23.75, corners straight to y=14).
           sweep-flag=1: the arc must bulge away from the baseline (toward
           half court), not back toward it - with sweep-flag=0 here it drew
           the minor arc on the near side of the chord, dipping the arc
           *below* the baseline instead of arcing out toward half court. -->
      <path d="M {sx(3)} {sy(0)} L {sx(3)} {sy(14)}
               A {23.75 * scale} {23.75 * scale} 0 0 1 {sx(47)} {sy(14)}
               L {sx(47)} {sy(0)}" fill="none" stroke="var(--line)" stroke-width="2"/>
      <!-- half court line -->
      <line x1="0" y1="{sy(47)}" x2="{W}" y2="{sy(47)}" stroke="var(--line)" stroke-width="1" stroke-dasharray="3,3"/>
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
