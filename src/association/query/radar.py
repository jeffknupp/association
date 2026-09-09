"""Renders a NetPoints "fingerprint" as a self-contained HTML/SVG radar plot.

The drawing half of the fingerprint feature, exactly as :mod:`association.query.court`
is the drawing half of the shot chart: this module knows about angles, radii and
SVG, and nothing about DuckDB. :mod:`association.query.fingerprint` does the
querying and hands the numbers over already scaled.

Modelled on espnanalytics.com's Net Pts Fingerprint - one axis per skill, grouped
into scoring, shot types, creation, rebounding and defense, and the whole polygon
read as a shape rather than as two dozen separate numbers.

.. versionadded:: 1.3.0
"""

from __future__ import annotations

import html
import math
from dataclasses import dataclass

# Radius of the outermost ring, in SVG units. Everything else is a fraction of
# it, so the plot resizes by changing this one number.
PLOT_RADIUS = 180.0

# Room outside the outer ring for the skill labels. The longest of them
# ("D-rebounding") renders about 75px wide at 11px, and a label on the left or
# right spoke starts at LABEL_RADIUS_FRACTION * PLOT_RADIUS - anything less than this clips it
# off the edge of the SVG with no error and no visible cue that it was cut.
LABEL_MARGIN = 130.0

# Vertically the labels are only a line tall, so the same margin top and bottom
# would leave a band of empty page under the plot - which is exactly what the
# first rendering did.
VERTICAL_MARGIN = 26.0

# Where the zero line sits on a value-scaled plot, as a fraction of PLOT_RADIUS.
# NOT zero: net points go negative (a turnover category is negative by
# construction), and with zero at the center every negative value would collapse
# onto the same point and the differences between them would be invisible.
VALUE_ZERO_FRACTION = 0.32

# Series colors, in order, as bare RGB triples: the table shades a leading cell
# with `rgba(var(--series-a-rgb), alpha)`, and a hex color cannot carry an
# alpha that way.
SERIES_RGB = ("42, 111, 176", "201, 121, 58", "92, 158, 87")
SERIES_RGB_DARK = ("108, 178, 240", "240, 160, 92", "127, 208, 122")

# The five skill groups, tinting their axis labels and table sections so the
# grouping is visible on the plot itself and not only in the legend. The hues
# are espnanalytics.com's own, so its chart and this one read the same way.
GROUP_COLORS: dict[str, tuple[str, str]] = {
    # group -> (light theme, dark theme)
    "scoring": ("#3b5fe0", "#7d97f5"),
    "shot types": ("#1f9b7a", "#4fd0ab"),
    "creation": ("#7c4dd6", "#b28cf0"),
    "rebounding": ("#d18416", "#f0ad57"),
    "defense": ("#d94a44", "#f58a85"),
}


@dataclass(frozen=True)
class Axis:
    """One spoke of the radar: a skill, and what to say about it.

    ``radius`` is the only field the geometry uses - a fraction in 0..1 that the
    caller has already computed from whichever scale is in force. Everything
    else is label and tooltip text, so the drawing code never has to know
    whether it is plotting a percentile or a raw rate.

    .. versionadded:: 1.3.0
    """

    label: str
    group: str
    radius: float
    tooltip: str


@dataclass(frozen=True)
class Series:
    """One player's polygon: a name, a bolded headline, and one radius per axis.

    ``headline`` is ``(label, value, note)`` triples - overall, offense and
    defense - shown above the plot, because the shape says how a player is good
    and those three say how much. ``note`` is the muted half, e.g. a percentile.

    .. versionadded:: 1.3.0
    """

    name: str
    headline: list[tuple[str, str, str]]
    axes: list[Axis]


@dataclass(frozen=True)
class Cell:
    """One table cell, optionally shaded in a series' color.

    ``series`` is the index of the player this cell leads the category for, and
    ``intensity`` how far ahead they are as a fraction in 0..1 of the widest gap
    in the table. Both are None/0 for a cell that leads nothing.

    .. versionadded:: 1.3.0
    """

    text: str
    series: int | None = None
    intensity: float = 0.0


# Where a skill label sits, as a fraction of the plot radius - just outside the
# outer ring. Shared by the geometry and the height calculation, which must
# agree or the labels are drawn off the canvas.
LABEL_RADIUS_FRACTION = 1.13


def _point(index: float, count: int, radius_fraction: float) -> tuple[float, float]:
    """The SVG point for axis ``index`` at ``radius_fraction`` of the plot radius.

    The first axis points straight up and they run clockwise, which is what
    makes two plots of the same axis order comparable by eye.
    """
    angle = -math.pi / 2 + 2 * math.pi * index / count
    radius = max(0.0, min(1.0, radius_fraction)) * PLOT_RADIUS
    return radius * math.cos(angle), radius * math.sin(angle)


def _polygon(axes: list[Axis]) -> str:
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in (_point(i, len(axes), a.radius) for i, a in enumerate(axes)))


def _ring(radius_fraction: float, count: int) -> str:
    """A ring drawn as a polygon, not a circle - a circle would sit outside the
    data polygon's corners at every axis and read as a larger value than it is."""
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in (_point(i, count, radius_fraction) for i in range(count)))


def _label_anchor(x: float) -> str:
    if abs(x) < 1e-6:
        return "middle"
    return "start" if x > 0 else "end"


def _group_class(group: str) -> str:
    return "g-" + group.replace(" ", "-")


def _series_var(index: int) -> str:
    return f"var(--series-{'abc'[index % 3]})"


def render_fingerprint_html(
    title: str,
    subtitle: str,
    series: list[Series],
    rings: list[tuple[float, str]],
    axis_note: str,
    table_headers: list[str],
    table_rows: list[tuple[str, str, list[Cell]]],
) -> str:
    """A radar plot of one or more fingerprints, plus the numbers as a table.

    Args:
        title: Heading - the player, or the players being compared.
        subtitle: Season, scope and units, in words.
        series: One entry per player. All entries must carry the same axes in
            the same order; the caller guarantees that, because two polygons
            drawn over different axis orders would compare nothing.
        rings: Reference rings as ``(radius_fraction, label)`` - league average
            and league best on a percentile plot, zero and the league extreme on
            a value one.
        axis_note: One line under the legend saying what a radius means.
        table_headers: Column headings for the table under the plot, the row
            label's own (empty) column excluded.
        table_rows: ``(row label, group, cells)`` for that table.

    Returns:
        A complete standalone HTML document.

    .. versionadded:: 1.3.0
    """
    axes = series[0].axes
    count = len(axes)
    width = 2 * (PLOT_RADIUS + LABEL_MARGIN)
    height = 2 * (PLOT_RADIUS * LABEL_RADIUS_FRACTION + VERTICAL_MARGIN)

    ring_shapes = []
    ring_legend = "".join(f'<span><span class="ringswatch" style="border-style: {"dashed" if fraction < 1 else "dotted"}"></span> {html.escape(label)}</span>' for fraction, label in rings if label)
    for fraction, label in rings:
        dash = ' stroke-dasharray="5,4"' if label else ""
        ring_shapes.append(f'<polygon points="{_ring(fraction, count)}" fill="none" stroke="var(--ring)" stroke-width="1"{dash}/>')
        # A labelled ring is named in the legend, not on the plot. Drawn on the
        # plot it lands under a skill label wherever it is put: every direction
        # out of the center ends at one.

    spokes, labels = [], []
    for index, axis in enumerate(axes):
        ex, ey = _point(index, count, 1.0)
        spokes.append(f'<line x1="0" y1="0" x2="{ex:.2f}" y2="{ey:.2f}" stroke="var(--ring)" stroke-width="0.75"/>')
        lx, ly = _point(index, count, LABEL_RADIUS_FRACTION)
        labels.append(f'<text x="{lx:.2f}" y="{ly:.2f}" class="axis {_group_class(axis.group)}" text-anchor="{_label_anchor(lx)}" dominant-baseline="middle">{html.escape(axis.label)}</text>')

    shapes = []
    for position, entry in enumerate(series):
        color = _series_var(position)
        shapes.append(f'<polygon points="{_polygon(entry.axes)}" fill="{color}" fill-opacity="0.18" stroke="{color}" stroke-width="2" stroke-linejoin="round"/>')
        for index, axis in enumerate(entry.axes):
            px, py = _point(index, count, axis.radius)
            shapes.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="3.5" fill="{color}"><title>{html.escape(entry.name)} - {html.escape(axis.tooltip)}</title></circle>')

    headlines = "".join(
        f'<div class="headline"><span class="who" style="color: {_series_var(i)}">{html.escape(entry.name)}</span>'
        + "".join(f'<span class="stat"><b>{html.escape(value)}</b> {html.escape(label)}' + (f" <i>{html.escape(note)}</i>" if note else "") + "</span>" for label, value, note in entry.headline)
        + "</div>"
        for i, entry in enumerate(series)
    )
    group_legend = "".join(f'<span class="{_group_class(group)}">{html.escape(group)}</span>' for group in GROUP_COLORS)
    header_cells = "".join(f"<th>{html.escape(h)}</th>" for h in table_headers)
    body: list[str] = []
    current_group = None
    for label, group, cells in table_rows:
        # A heading whenever the group changes, the way the reference lists its
        # skills. table_rows arrives grouped; this does not regroup it, so a
        # caller that sorts across groups gets repeated headings rather than
        # rows quietly filed under the wrong one.
        if group != current_group:
            body.append(f'<tr class="section"><th scope="rowgroup" colspan="{len(table_headers) + 1}" class="{_group_class(group)}">{html.escape(group)}</th></tr>')
            current_group = group
        body.append(
            f'<tr><th scope="row">{html.escape(label)}</th>'
            + "".join(
                f'<td style="background: rgba(var(--series-{"abc"[cell.series % 3]}-rgb), {cell.intensity:.3f})">{html.escape(cell.text)}</td>'
                if cell.series is not None and cell.intensity > 0
                else f"<td>{html.escape(cell.text)}</td>"
                for cell in cells
            )
            + "</tr>"
        )
    body_rows = "".join(body)
    group_rules = "".join(f"  .{_group_class(group)} {{ color: {light}; fill: {light}; }}\n" for group, (light, _) in GROUP_COLORS.items())
    group_rules_dark = "".join(f"    .{_group_class(group)} {{ color: {dark}; fill: {dark}; }}\n" for group, (_, dark) in GROUP_COLORS.items())
    series_vars = "".join(f"    --series-{'abc'[i]}-rgb: {rgb};\n" for i, rgb in enumerate(SERIES_RGB))
    series_vars_dark = "".join(f"      --series-{'abc'[i]}-rgb: {rgb};\n" for i, rgb in enumerate(SERIES_RGB_DARK))

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{html.escape(title)} - NetPoints Fingerprint</title>
<style>
  :root {{
    --bg: #ffffff; --fg: #1a1a1a; --muted: #666666;
    --ring: #cfc6bb; --grid: #e6e0d8;
{series_vars}    --series-a: rgb(var(--series-a-rgb));
    --series-b: rgb(var(--series-b-rgb));
    --series-c: rgb(var(--series-c-rgb));
  }}
{group_rules}  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #16181d; --fg: #e8e8e8; --muted: #9aa0a6;
      --ring: #43494f; --grid: #2b3036;
{series_vars_dark}    }}
{group_rules_dark}  }}
  body {{ background: var(--bg); color: var(--fg); font-family: -apple-system, sans-serif;
          display: flex; flex-direction: column; align-items: center; padding: 24px; }}
  h1 {{ font-size: 20px; margin: 0 0 2px 0; }}
  p.sub {{ color: var(--muted); margin: 0 0 12px 0; font-size: 14px; }}
  .headline {{ display: flex; gap: 18px; align-items: baseline; font-size: 14px; margin-bottom: 4px; }}
  .headline .who {{ font-weight: 600; }}
  .headline .stat {{ color: var(--muted); }}
  .headline b {{ color: var(--fg); font-size: 16px; }}
  .headline i {{ font-style: normal; font-size: 12px; font-weight: 600; color: var(--fg); opacity: 0.75; }}
  text.axis {{ font-size: 11px; }}
  text.ringlabel {{ font-size: 10px; fill: var(--muted); }}
  .legend {{ display: flex; gap: 20px; margin-top: 8px; font-size: 13px; color: var(--muted); flex-wrap: wrap; justify-content: center; }}
  .legend span {{ display: inline-flex; align-items: center; gap: 6px; }}
  .swatch {{ width: 12px; height: 12px; border-radius: 3px; display: inline-block; }}
  .ringswatch {{ width: 14px; height: 0; border-top-width: 2px; border-color: var(--ring); display: inline-block; }}
  .groups {{ display: flex; gap: 14px; margin-top: 6px; font-size: 12px; flex-wrap: wrap; justify-content: center; }}
  p.note {{ color: var(--muted); font-size: 12px; margin: 8px 0 0 0; max-width: 560px; text-align: center; }}
  table {{ border-collapse: collapse; margin-top: 20px; font-size: 13px; }}
  th, td {{ padding: 3px 10px; text-align: right; border-bottom: 1px solid var(--grid); }}
  th[scope="row"] {{ text-align: left; font-weight: normal; }}
  thead th {{ color: var(--muted); font-weight: normal; }}
  tr.section th {{ text-align: left; font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; padding-top: 12px; border-bottom: 1px solid currentColor; }}
</style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <p class="sub">{html.escape(subtitle)}</p>
  {headlines}
  <svg width="{width:.0f}" height="{height:.0f}" viewBox="0 0 {width:.0f} {height:.0f}">
    <g transform="translate({width / 2:.1f},{height / 2:.1f})">
      {"".join(ring_shapes)}
      {"".join(spokes)}
      {"".join(shapes)}
      {"".join(labels)}
    </g>
  </svg>
  <div class="legend">{"".join(f'<span><span class="swatch" style="background: {_series_var(i)}"></span> {html.escape(entry.name)}</span>' for i, entry in enumerate(series))}{ring_legend}</div>
  <div class="groups">{group_legend}</div>
  <p class="note">{html.escape(axis_note)}</p>
  <table>
    <thead><tr><th></th>{header_cells}</tr></thead>
    <tbody>{body_rows}</tbody>
  </table>
</body>
</html>
"""
