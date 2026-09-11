"""Sanity tests for the court SVG renderer."""

import math
import re

import pytest

from association.query.court import HOOP_X, HOOP_Y, THREE_POINT_RADIUS, render_court_html


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


def _rim(html: str) -> tuple[float, float]:
    match = re.search(r'<circle cx="([\d.]+)" cy="([\d.]+)" r="7.5"', html)
    assert match is not None, "no rim drawn"
    return float(match.group(1)), float(match.group(2))


def _markers(html: str) -> list[tuple[float, float]]:
    """Made-shot markers, which are the r=5 circles."""
    return [(float(x), float(y)) for x, y in re.findall(r'<circle cx="([-\d.]+)" cy="([-\d.]+)" r="5"', html)]


def test_the_arc_is_centered_on_the_rim() -> None:
    """The three-point line is 23.75 feet from the rim, so the arc's center IS
    the rim. The two were drawn from separate constants once, and nothing but
    this would notice them part."""
    html = render_court_html("Test Player", "sub", [])
    match = re.search(r"three point arc.*?<path d=\"([^\"]+)\"", html, re.S)
    assert match is not None
    d = match.group(1)
    arc_start_line = re.search(r"L\s*([\d.]+)\s+([\d.]+)\s*\n?\s*A", d)
    assert arc_start_line is not None
    x1, y1 = float(arc_start_line.group(1)), float(arc_start_line.group(2))
    rx, _ry, _rot, _large, _sweep, x2, y2 = _arc_params(d)
    rim_x, rim_y = _rim(html)
    assert math.hypot(x1 - rim_x, y1 - rim_y) == pytest.approx(rx, abs=0.05)
    assert math.hypot(x2 - rim_x, y2 - rim_y) == pytest.approx(rx, abs=0.05)


def test_the_half_court_fills_the_canvas() -> None:
    """Baseline on the bottom edge, half court on the top, and the table's
    lowest shot - y = -5, on the baseline - inside. Every relative check above
    still passes with the whole court slid 5.25 feet down the page, which puts
    the baseline and every shot under the rim off the bottom of the drawing."""
    html = render_court_html("T", "s", [(25, -5, True, "Layup", 1, "1:00", "1")])
    height = re.search(r'<svg width="\d+" height="(\d+)"', html)
    baseline = re.search(r'<!-- baseline -->\s*<line x1="0" y1="([-\d.]+)"', html)
    half_court = re.search(r'<!-- half court line -->\s*<line x1="0" y1="([-\d.]+)"', html)
    assert height is not None and baseline is not None and half_court is not None
    assert float(baseline.group(1)) == pytest.approx(float(height.group(1)))
    assert float(half_court.group(1)) == pytest.approx(0)
    ((_mx, my),) = _markers(html)
    assert 0 <= my <= float(height.group(1))


def test_a_shot_at_the_rim_is_drawn_on_the_rim() -> None:
    """Shots are plotted at their stored coordinates, so a shot at HOOP_X,
    HOOP_Y must land on the rim that is drawn. With the rim drawn 5.25 feet up
    from y=0, a dunk landed on the baseline under it."""
    html = render_court_html("T", "s", [(HOOP_X, HOOP_Y, True, "Dunk", 1, "1:00", "1")])
    assert _markers(html) == [_rim(html)]


@pytest.mark.parametrize(
    ("x", "y", "three"),
    [
        (28, 26, True),  # real 2006 three from the top, described as 26 feet
        (24, 27, True),  # real 2023 three, 27 feet
        (44, 9, False),  # real 2023 long two, 21 feet
        (26, 5, False),  # real 2006 short two, 5 feet
    ],
)
def test_real_shots_are_drawn_on_the_side_of_the_arc_they_were_taken(x: int, y: int, three: bool) -> None:
    html = render_court_html("T", "s", [(x, y, True, "Jump Shot", 1, "1:00", "1")])
    ((mx, my),) = _markers(html)
    rim_x, rim_y = _rim(html)
    assert (math.hypot(mx - rim_x, my - rim_y) > THREE_POINT_RADIUS * 10) is three


def test_a_real_corner_three_is_drawn_outside_the_corner_line() -> None:
    """Bojan Bogdanovic, 2023, "23-foot three point jumper" at (2, 2)."""
    html = render_court_html("T", "s", [(2, 2, True, "Jump Shot", 1, "1:00", "1")])
    ((mx, _my),) = _markers(html)
    corner = re.search(r"three point arc.*?<path d=\"M\s*([\d.]+)", html, re.S)
    assert corner is not None
    assert mx < float(corner.group(1))


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
