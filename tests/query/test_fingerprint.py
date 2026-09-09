"""Tests for the NetPoints fingerprint radar plot.

The bug shape these guard against is the one the whole project keeps producing:
a plot that renders beautifully while showing something other than what was
asked. A radar is especially exposed to it - a wrong axis order, a percentile
computed against the wrong pool, a value scaled per-axis, or (as happened here
first time round) a whole side of the ball missing all look like a perfectly
ordinary shape.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import duckdb
import pytest

from association.net_points_categories import FINGERPRINT_CATEGORIES
from association.query.entities import Entity
from association.query.fingerprint import (
    FINGERPRINT_SKILLS,
    FINGERPRINT_VIEWS,
    FingerprintUnavailable,
    build_series,
    load_fingerprints,
    render_fingerprint,
    render_for_players,
    skills_for,
)
from association.query.radar import PLOT_RADIUS, VALUE_ZERO_FRACTION, Axis, Cell, Series, render_fingerprint_html

# One player is a league-best rim scorer who forces no turnovers, one is his
# mirror image, one is average, one is below the minutes floor. Every value
# below is derived from these, so an assertion can be checked by hand.
#
# (athlete_id, name, minutes, possessions, rim scoring (offense), forced
# turnovers (defense))
STARS = [
    ("1", "Ada Star", 2000.0, 5000.0, 100.0, 0.0),
    ("2", "Bo Wall", 2000.0, 5000.0, 0.0, 100.0),
    ("3", "Cy Middle", 1500.0, 4000.0, 20.0, 20.0),
    ("4", "Dee Scrub", 100.0, 200.0, 8.0, 8.0),
]

CATEGORIES = [name for name in FINGERPRINT_CATEGORIES.values()]


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    columns = ", ".join(f"{category}_{side}_net_pts DOUBLE" for category in CATEGORIES for side in ("o", "d", "t"))
    c.execute(f"CREATE TABLE net_points_player_fingerprint (athlete_id VARCHAR, season INTEGER, minutes DOUBLE, total_poss DOUBLE, {columns})")
    placeholders = ", ".join("?" for _ in range(4 + 3 * len(CATEGORIES)))
    for athlete_id, name, minutes, possessions, rim, forced in STARS:
        c.execute("INSERT INTO players VALUES (?, ?)", [athlete_id, name])
        values: list[float] = []
        for category in CATEGORIES:
            # Only rim scoring (offense) and forced turnovers (defense) carry a
            # signal, and they are on different sides - so an axis drawn from
            # the wrong column, or in the wrong position, shows up as a value on
            # a spoke that should be flat.
            offense = rim if category in ("rim", "total") else 0.0
            defense = forced if category in ("turnover", "total") else 0.0
            values.extend([offense, defense, offense + defense])
        c.execute(f"INSERT INTO net_points_player_fingerprint VALUES ({placeholders})", [athlete_id, 2026, minutes, possessions, *values])
    return c


def _entity(athlete_id: str) -> Entity:
    return Entity(id=athlete_id, name=next(row[1] for row in STARS if row[0] == athlete_id))


# ---------------- the skills ----------------


def test_every_skill_reads_a_real_column(con: duckdb.DuckDBPyConnection) -> None:
    """A typo in a column name is a SQL error at render time, not at import."""
    stored = {row[0] for row in con.execute("DESCRIBE net_points_player_fingerprint").fetchall()}
    for skill in FINGERPRINT_SKILLS:
        assert skill.column in stored, skill


def test_both_sides_of_the_ball_are_drawn() -> None:
    """The first version plotted one side per plot and defense was simply
    absent from the default chart - a whole half of the game missing, and
    nothing on the page saying so."""
    assert {skill.side for skill in FINGERPRINT_SKILLS} == {"offense", "defense"}
    assert sum(1 for skill in FINGERPRINT_SKILLS if skill.side == "defense") >= 5


def test_the_summary_category_is_not_a_spoke() -> None:
    """`total` is the sum the spokes break down - drawn as one it would be the
    largest axis on every plot and would double-count everything else."""
    assert not any(skill.category == "total" for skill in FINGERPRINT_SKILLS)


def test_skills_are_grouped_the_way_the_reference_groups_them() -> None:
    assert {skill.group for skill in FINGERPRINT_SKILLS} == {"scoring", "shot types", "creation", "rebounding", "defense"}


def test_each_group_is_drawn_contiguously() -> None:
    """Related skills adjacent is what makes a shape readable as "scores at the
    rim" rather than as 25 unrelated spikes."""
    seen: list[str] = []
    for skill in FINGERPRINT_SKILLS:
        if not seen or seen[-1] != skill.group:
            assert skill.group not in seen, f"{skill.group} is split across the plot"
            seen.append(skill.group)


def test_no_skill_is_drawn_on_two_axes() -> None:
    assert len({(skill.category, skill.side) for skill in FINGERPRINT_SKILLS}) == len(FINGERPRINT_SKILLS)
    assert len({skill.label for skill in FINGERPRINT_SKILLS}) == len(FINGERPRINT_SKILLS)


def test_a_view_selects_skills_rather_than_columns() -> None:
    for view in FINGERPRINT_VIEWS:
        selected = skills_for(view)
        assert selected, view
        if view != "total":
            assert {skill.side for skill in selected} == {view}
    assert skills_for("total") == FINGERPRINT_SKILLS


def test_an_unknown_view_is_refused_rather_than_defaulted() -> None:
    with pytest.raises(FingerprintUnavailable, match="view must be"):
        skills_for("offence")


# ---------------- percentiles and the pool ----------------


def test_percentile_is_measured_against_the_minutes_qualified_pool(con: duckdb.DuckDBPyConnection) -> None:
    fingerprints, league = load_fingerprints(con, [_entity("1")], 2026, min_minutes=500)
    assert league.pool_size == 3  # Dee Scrub's 100 minutes are below the floor
    rim = next(v for v in fingerprints[0].values if v.skill.label == "rim scoring")
    assert rim.per_100 == pytest.approx(2.0)  # 100 net points over 5000 possessions
    assert rim.percentile == pytest.approx(1.0)
    assert rim.league_best_per_100 == pytest.approx(2.0)
    assert rim.league_average_per_100 == pytest.approx((2.0 + 0.0 + 0.5) / 3)


def test_offense_and_defense_read_different_columns(con: duckdb.DuckDBPyConnection) -> None:
    """Ada Star scores at the rim and forces nothing; Bo Wall is the mirror.
    Reading the wrong column swaps them, and both plots look normal."""
    ada = {v.skill.label: v.per_100 for v in load_fingerprints(con, [_entity("1")], 2026)[0][0].values}
    bo = {v.skill.label: v.per_100 for v in load_fingerprints(con, [_entity("2")], 2026)[0][0].values}
    assert ada["rim scoring"] == pytest.approx(2.0)
    assert ada["forcing TOs"] == pytest.approx(0.0)
    assert bo["rim scoring"] == pytest.approx(0.0)
    assert bo["forcing TOs"] == pytest.approx(2.0)


def test_the_headline_carries_overall_offense_and_defense(con: duckdb.DuckDBPyConnection) -> None:
    ada = load_fingerprints(con, [_entity("1")], 2026)[0][0]
    assert ada.offense_per_100 == pytest.approx(2.0)
    assert ada.defense_per_100 == pytest.approx(0.0)
    assert ada.overall_per_100 == pytest.approx(2.0)


def test_a_player_below_the_floor_is_still_drawn_and_said_to_be_below_it(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    """Dropping them would be worse: the question named a player, and a plot
    that silently is not of them is the failure this project keeps producing."""
    fingerprints, _ = load_fingerprints(con, [_entity("4")], 2026, min_minutes=500)
    assert fingerprints[0].qualified is False
    message, _ = render_for_players(con, tmp_path, [_entity("4")], [], 2026)
    assert "under 500 minutes" in message


def test_a_player_with_no_row_is_named_rather_than_drawn_as_zeroes(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    message, _ = render_for_players(con, tmp_path, [_entity("1"), Entity(id="99", name="Ghost Player")], [], 2026)
    assert "No fingerprint on record for: Ghost Player" in message
    assert "Ghost Player" not in message.split("No fingerprint on record")[0]


def test_a_season_with_no_fingerprint_rows_says_so(con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(FingerprintUnavailable, match="no NetPoints fingerprint data for season 1999"):
        load_fingerprints(con, [_entity("1")], 1999)


def test_a_season_where_nobody_qualifies_says_so_rather_than_ranking_against_nothing(con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(FingerprintUnavailable, match="nothing to compare against"):
        load_fingerprints(con, [_entity("1")], 2026, min_minutes=99999)


# ---------------- the two scales ----------------


def _radii(series: Series) -> dict[str, float]:
    return {axis.label: axis.radius for axis in series.axes}


def test_the_value_scale_is_shared_across_axes(con: duckdb.DuckDBPyConnection) -> None:
    """espnanalytics.com's "true value" scale exists so that a big skill draws
    bigger than a small one. Scaling each axis to its own maximum would put
    every league leader on the outer ring and destroy exactly that."""
    fingerprints, league = load_fingerprints(con, [_entity("1")], 2026)
    radii = _radii(build_series(fingerprints[0], "value", league))
    assert radii["rim scoring"] == pytest.approx(1.0)  # the league's best value in any skill
    assert radii["mid-range"] == pytest.approx(VALUE_ZERO_FRACTION)  # exactly zero


def test_a_negative_value_draws_inside_the_zero_ring(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("UPDATE net_points_player_fingerprint SET turnover_o_net_pts = -50.0 WHERE athlete_id = '1'")
    fingerprints, league = load_fingerprints(con, [_entity("1")], 2026)
    assert _radii(build_series(fingerprints[0], "value", league))["turnover cost"] < VALUE_ZERO_FRACTION


def test_the_percentile_scale_puts_the_league_best_on_the_outer_ring(con: duckdb.DuckDBPyConnection) -> None:
    fingerprints, league = load_fingerprints(con, [_entity("1")], 2026)
    assert _radii(build_series(fingerprints[0], "percentile", league))["rim scoring"] == pytest.approx(1.0)


def test_an_unknown_scale_is_refused(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    with pytest.raises(FingerprintUnavailable, match="scale must be"):
        render_for_players(con, tmp_path, [_entity("1")], [], 2026, scale="logarithmic")


# ---------------- the drawing ----------------


def _polygons(html: str) -> list[list[tuple[float, ...]]]:
    return [[tuple(float(n) for n in point.split(",")) for point in match.split()] for match in re.findall(r'<polygon points="([^"]+)"', html)]


def test_the_first_axis_points_straight_up_and_they_run_clockwise() -> None:
    axes = [Axis(label=f"a{i}", group="scoring", radius=1.0, tooltip="t") for i in range(4)]
    html = render_fingerprint_html("T", "S", [Series(name="P", headline=[], axes=axes)], [(1.0, "")], "note", [], [])
    points = _polygons(html)[-1]
    assert points[0] == pytest.approx((0.0, -PLOT_RADIUS))
    assert points[1] == pytest.approx((PLOT_RADIUS, 0.0))  # clockwise in SVG's downward-y space


def test_every_polygon_has_one_vertex_per_axis(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    _, path = render_for_players(con, tmp_path, [_entity("1"), _entity("2")], [], 2026)
    for points in _polygons(path.read_text()):
        assert len(points) == len(FINGERPRINT_SKILLS)


def test_no_vertex_escapes_the_outer_ring(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    """A radius over 1.0 draws outside the ring it is measured against, which
    reads as better than the league best."""
    _, path = render_for_players(con, tmp_path, [_entity("1")], [], 2026, scale="value")
    for points in _polygons(path.read_text()):
        for x, y in points:
            # A hair over, not 1e-6: coordinates are written to two decimals.
            assert math.hypot(x, y) <= PLOT_RADIUS + 0.01


def test_the_headline_is_rendered_above_the_plot(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    _, path = render_for_players(con, tmp_path, [_entity("1")], [], 2026)
    html = path.read_text()
    head = html.split("<svg")[0]
    assert "<b>+2.00</b> total net pts / 100" in head
    assert "<b>+2.00</b> offense" in head and "<b>+0.00</b> defense" in head
    assert "100th pct" in head  # the league best of the three qualified players


def test_the_table_carries_the_numbers_the_plot_only_shows_as_a_shape(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    _, path = render_for_players(con, tmp_path, [_entity("1")], [], 2026)
    html = path.read_text()
    assert html.count('<tr><th scope="row"') == len(FINGERPRINT_SKILLS)
    assert "+2.00" in html  # Ada Star's rim value per 100, spelled out


def test_a_view_draws_only_that_side(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    _, path = render_for_players(con, tmp_path, [_entity("1")], [], 2026, view="defense")
    html = path.read_text()
    assert html.count('<tr><th scope="row"') == len(skills_for("defense"))
    assert "defensive skills only" in html


# ---------------- the comparison highlight ----------------


def _shaded(html: str) -> list[tuple[str, str, float]]:
    """(row label, series letter, alpha) for every shaded cell in the table."""
    out = []
    for row in re.findall(r"<tr><th scope=\"row\"[^>]*>([^<]+)</th>(.*?)</tr>", html):
        for series, alpha in re.findall(r"rgba\(var\(--series-(\w)-rgb\), ([\d.]+)\)", row[1]):
            out.append((row[0], series, float(alpha)))
    return out


def test_the_leader_of_each_category_is_shaded_in_their_own_color(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    _, path = render_for_players(con, tmp_path, [_entity("1"), _entity("2")], [], 2026)
    shaded = dict((label, series) for label, series, _ in _shaded(path.read_text()))
    # Ada Star is series a and leads rim scoring; Bo Wall is series b and leads
    # forced turnovers. Shading the wrong side is the whole point of this test.
    assert shaded["rim scoring"] == "a"
    assert shaded["forcing TOs"] == "b"


def test_only_one_cell_per_row_is_shaded(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    _, path = render_for_players(con, tmp_path, [_entity("1"), _entity("2")], [], 2026)
    labels = [label for label, _, _ in _shaded(path.read_text())]
    assert len(labels) == len(set(labels))


def test_a_tied_category_is_not_shaded_for_either_player(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    """Every skill but those two is 0.0 for both. A highlight anywhere else
    would invent a winner out of a tie."""
    _, path = render_for_players(con, tmp_path, [_entity("1"), _entity("2")], [], 2026)
    labels = {label for label, _, _ in _shaded(path.read_text())}
    assert labels == {"rim scoring", "forcing TOs"}


def test_the_shade_is_proportional_to_the_gap(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    """A near-tie must look like a near-tie. Ada leads rim scoring by 2.00 per
    100, the widest gap in the table; a skill she leads by a tenth of that must
    be visibly lighter."""
    con.execute("UPDATE net_points_player_fingerprint SET corner_o_net_pts = 10.0 WHERE athlete_id = '1'")
    _, path = render_for_players(con, tmp_path, [_entity("1"), _entity("2")], [], 2026)
    alphas = {label: alpha for label, _, alpha in _shaded(path.read_text())}
    assert alphas["rim scoring"] > alphas["corner 3s"]
    assert alphas["corner 3s"] > 0  # but still visible: a real lead, narrowly


def test_a_single_player_table_is_not_shaded(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    _, path = render_for_players(con, tmp_path, [_entity("1")], [], 2026)
    assert _shaded(path.read_text()) == []


def test_a_name_with_html_in_it_cannot_break_out_of_the_page() -> None:
    axes = [Axis(label="<script>x</script>", group="scoring", radius=0.5, tooltip="t")]
    html = render_fingerprint_html("<b>T</b>", "S", [Series(name="P", headline=[], axes=axes)], [], "note", [], [("<img>", "scoring", [Cell("<i>")])])
    assert "<script>" not in html and "<img>" not in html
    assert "&lt;script&gt;" in html


# ---------------- the name-resolving entry point ----------------


def test_render_fingerprint_resolves_a_partial_name(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    assert "Ada Star" in render_fingerprint(con, tmp_path, "Ada", season=2026)


def test_render_fingerprint_compares_two_names(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    assert "Ada Star vs Bo Wall" in render_fingerprint(con, tmp_path, "Ada vs Bo", season=2026)


def test_render_fingerprint_reports_an_unknown_name_rather_than_raising(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    assert render_fingerprint(con, tmp_path, "Nobody At All", season=2026) == "No player found matching 'Nobody At All'."


def test_render_fingerprint_reports_a_missing_season_rather_than_raising(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    assert "no NetPoints fingerprint data for season 1999" in render_fingerprint(con, tmp_path, "Ada", season=1999)


def test_the_table_is_grouped_the_way_the_plot_is(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    """One section per group, in plot order. Sorting across groups instead
    would list the skills in an order the radar never shows, and the table is
    what the radar is checked against."""
    _, path = render_for_players(con, tmp_path, [_entity("1")], [], 2026)
    sections = re.findall(r'<th scope="rowgroup"[^>]*>([^<]+)</th>', path.read_text())
    assert sections == list(dict.fromkeys(skill.group for skill in FINGERPRINT_SKILLS))
