"""Sanity tests for the court SVG renderer."""

from association.query.court import render_court_html


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
