"""Sanity tests for the court SVG renderer."""

import math
import re

from association.query.court import render_court_html


def _arc_params(d: str) -> tuple[float, float, float, int, int, float, float]:
    """Parse a single SVG ``A`` (elliptical arc) command's own end coordinates
    out of a ``d`` path string built as ``... A rx ry rot large sweep x y ...``."""
    match = re.search(
        r"A\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([01])\s+([01])\s+([\d.]+)\s+([\d.]+)",
        d,
    )
    assert match is not None, f"no arc command found in path: {d!r}"
    rx, ry, rot, large, sweep, x, y = match.groups()
    return float(rx), float(ry), float(rot), int(large), int(sweep), float(x), float(y)


def _arc_midpoint_y(start: tuple[float, float], d: str) -> float:
    """The SVG-space y of the arc's own midpoint, via the spec's endpoint-to-center
    parameterization (https://www.w3.org/TR/SVG/implnote.html#ArcConversionEndpointToCenter)."""
    x1, y1 = start
    rx, ry, _rot, large_arc, sweep, x2, y2 = _arc_params(d)

    x1p, y1p = (x1 - x2) / 2, (y1 - y2) / 2
    ratio = max((rx**2 * ry**2 - rx**2 * y1p**2 - ry**2 * x1p**2) / (rx**2 * y1p**2 + ry**2 * x1p**2), 0)
    sign = -1 if large_arc == sweep else 1
    cxp = sign * math.sqrt(ratio) * (rx * y1p / ry)
    cyp = sign * math.sqrt(ratio) * (-ry * x1p / rx)
    cy = cyp + (y1 + y2) / 2

    def angle(ux: float, uy: float, vx: float, vy: float) -> float:
        dot = ux * vx + uy * vy
        norm = math.hypot(ux, uy) * math.hypot(vx, vy)
        a = math.degrees(math.acos(max(-1.0, min(1.0, dot / norm))))
        return a if (ux * vy - uy * vx) >= 0 else -a

    theta1 = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dtheta = angle((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if sweep == 0 and dtheta > 0:
        dtheta -= 360
    if sweep == 1 and dtheta < 0:
        dtheta += 360
    mid = math.radians(theta1 + 0.5 * dtheta)
    return cy + ry * math.sin(mid)


def test_three_point_arc_bulges_toward_half_court() -> None:
    """The arc must bow away from the baseline (toward smaller SVG y), not
    back toward it - a wrong sweep-flag once drew it dipping *below* the
    baseline instead, which reads as the three-point line rendering upside down."""
    html = render_court_html("Test Player", "sub", [])
    match = re.search(r"three point arc.*?<path d=\"([^\"]+)\"", html, re.S)
    assert match is not None
    d = match.group(1)
    start = re.match(r"M\s*([\d.]+)\s+([\d.]+)", d)
    assert start is not None
    corner = (float(start.group(1)), float(start.group(2)))
    # the corner-to-corner straight run lands the arc's actual start one line
    # segment later, at the same y as the arc's end point (both corners, 14ft
    # up from the baseline) - reuse that y with the parsed arc's own x.
    baseline_y = corner[1]
    arc_start_line = re.search(r"L\s*([\d.]+)\s+([\d.]+)\s*\n?\s*A", d)
    assert arc_start_line is not None
    arc_start = (float(arc_start_line.group(1)), float(arc_start_line.group(2)))
    assert arc_start[1] < baseline_y  # corner runs up from the baseline first

    mid_y = _arc_midpoint_y(arc_start, d)
    assert mid_y < arc_start[1]  # bulges toward half court (smaller y), not back toward the baseline


def test_render_court_html_includes_title_and_subtitle() -> None:
    html = render_court_html("Test Player", "season 2024 - 1/2 (50.0%) shown", [])
    assert "Test Player" in html
    assert "season 2024" in html


def test_render_court_html_marks_makes_and_misses_distinctly() -> None:
    shots = [
        (25, 20, True, "Jump Shot", 1, "10:00", "100"),
        (24, 22, False, "Jump Shot", 1, "9:00", "100"),
    ]
    html = render_court_html("Test Player", "1/2 shown", shots)
    assert "MADE" in html
    assert "MISS" in html
    assert html.count("<circle") >= 1  # at least the made-shot marker
    assert "<line" in html  # the missed-shot X marker uses two <line> segments


def test_render_court_html_is_theme_aware() -> None:
    html = render_court_html("Test Player", "sub", [])
    assert "prefers-color-scheme: dark" in html


def test_render_court_html_valid_svg_wrapper() -> None:
    html = render_court_html("Test Player", "sub", [])
    assert "<svg" in html and "</svg>" in html
