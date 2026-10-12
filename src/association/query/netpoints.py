"""The NetPoints relation: ESPN Analytics' NetPoints tables, and every
statement read over them - built here and executed through the compiler's
one door (:func:`~association.query.compose.core.values_of`), as the season
line's are (:mod:`association.query.season_line`). Phase 4, step 5
(``ROADMAP.md``, "Every statement is a relation's"): the three statements
the NetPoints reader ran (:mod:`association.query.compose.netpoints`, a
player's season line, the categories behind it, one game's) and the three
the fingerprint's loaders ran (:mod:`association.query.fingerprint`, the
season's pool, the per-game pool, a player's first or last game) moved
here whole, text-identical, with the parameters each took. The readers keep
their words and the fingerprint its pool and drawing; nothing outside this
module writes a NetPoints statement.

Four tables, three encodings of the season type (``DATA.md``):
``net_points_player`` keeps its own STRING (``net_points_season_type``,
"Regular Season"), the per-game tables (``net_points_player_game`` and the
LONG ``net_points_player_game_fingerprint``, one row per category) the
numeric 2/3, and the season fingerprint (``net_points_player_fingerprint``)
has none at all - filtering one with another's silently matches nothing.

A column is named by its category and side (``"rim"``, ``"o"``): the season
fingerprint stores ``rim_o_net_pts`` as a column, the per-game one a
``category`` row whose ``o_net_pts`` is pivoted back
(:func:`game_fingerprints`). The categories and sides are the callers' (the
fingerprint's skills, the reader's breakdown), handed in as those pairs, so
the relation imports neither.

.. versionadded:: 6.0.0
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import duckdb

from association.query.metrics import SEASON_TYPE_LABELS
from association.query.season_line import Statement

NETPOINTS_TOTALS = ("overall", "offense", "defense", "overall_per_100_poss", "total_minutes", "games")
"""``net_points_player``'s season line, in the order :func:`season_totals`
reads it.

.. versionadded:: 6.0.0
   ``compose.netpoints._NETPOINTS_TOTALS`` was this.
"""

Column = tuple[str, str]
"""One NetPoints value as the relation names it: a category
(``FINGERPRINT_CATEGORIES``' values, ``"total"`` the summary) and a side's
letter (``"o"``, ``"d"`` or ``"t"``).

.. versionadded:: 6.0.0
"""


def _values(con: duckdb.DuckDBPyConnection, statement: Statement) -> list[tuple[Any, ...]]:
    """``statement`` run through the compiler's one door
    (:func:`~association.query.compose.core.values_of`), for every read this
    relation makes. At call time: the compiler imports the readers that
    import this module."""
    from association.query.compose.core import values_of

    return values_of(con, statement)


def _first(rows: list[tuple[Any, ...]]) -> tuple[Any, ...] | None:
    """A statement's first row, as ``fetchone`` gave the retired reads."""
    return rows[0] if rows else None


def _season_columns(columns: Sequence[Column]) -> str:
    """The season fingerprint's wide columns for ``columns``, in order:
    ``rim_o_net_pts, rim_d_net_pts, ...``."""
    return ", ".join(f"{category}_{side}_net_pts" for category, side in columns)


def _game_pivots(columns: Sequence[Column]) -> str:
    """The per-game fingerprint's LONG rows pivoted back to the season
    file's wide shape, one ``max(CASE ...)`` per column, in order.
    Interpolated rather than parameterized because both halves are code
    (the fingerprint's skills). Qualified with ``f.``, every one:
    ``net_points_player_game`` carries o/d/t_net_pts of its own, so an
    unqualified name is ambiguous once the join is present."""
    return ", ".join(f"max(CASE WHEN f.category = '{category}' THEN f.{side}_net_pts END)" for category, side in columns)


# --- a player's NetPoints --------------------------------------------------------


def netpoints_totals_statement(athlete_id: str, season: int, season_type: int) -> Statement:
    """One player's season NetPoints line (:data:`NETPOINTS_TOTALS`).
    ``net_points_player`` uses its OWN string season_type; filtering it with
    the numeric one every other table uses silently matches nothing.

    .. versionadded:: 5.0.0
       ``templates.netpoints.player_netpoints``' first statement.

    .. versionchanged:: 6.0.0
       Moved from ``compose.netpoints`` to the relation.
    """
    label = SEASON_TYPE_LABELS.get(season_type, "Regular Season")
    return Statement(f"SELECT {', '.join(NETPOINTS_TOTALS)} FROM net_points_player WHERE athlete_id = ? AND season = ? AND net_points_season_type = ?", [athlete_id, season, label])


def netpoints_fingerprint_statement(athlete_id: str, season: int, categories: Sequence[str]) -> Statement:
    """One player's season fingerprint: his possessions, then offense,
    defense and total for each of ``categories`` in turn. The fingerprint
    table has no season_type column at all.

    .. versionadded:: 5.0.0
       ``templates.netpoints.player_netpoints``' second statement.

    .. versionchanged:: 6.0.0
       Moved from ``compose.netpoints`` to the relation, and takes the
       categories the reader reads.
    """
    selected = _season_columns([(category, side) for category in categories for side in ("o", "d", "t")])
    return Statement(f"SELECT total_poss, {selected} FROM net_points_player_fingerprint WHERE athlete_id = ? AND season = ?", [athlete_id, season])


def netpoints_game_statement(athlete_id: str, season: int, season_type: int, order: str) -> Statement:
    """One player's first or most recent game's NetPoints, from
    ``net_points_player_game`` - opt-in (``data pull
    --include-net-points-daily``) and, unlike ``net_points_player``, on the
    normal numeric season_type - dated from ``games``.

    .. versionadded:: 5.0.0
       ``templates.netpoints._single_game_netpoints``' statement.

    .. versionchanged:: 6.0.0
       Moved from ``compose.netpoints`` to the relation.
    """
    return Statement(
        "SELECT g.event_id, g.date, g.o_net_pts, g.d_net_pts, g.t_net_pts, g.o_poss, g.d_poss, g.t_wpa "
        "FROM (SELECT npg.*, gm.date FROM net_points_player_game npg JOIN games gm ON gm.event_id = npg.event_id "
        "      WHERE npg.athlete_id = ? AND npg.season = ? AND npg.season_type = ?) g "
        f"ORDER BY g.date {'ASC' if order == 'first' else 'DESC'} LIMIT 1",
        [athlete_id, season, season_type],
    )


def season_totals(con: duckdb.DuckDBPyConnection, athlete_id: str, season: int, season_type: int) -> tuple[Any, ...] | None:
    """One player's season NetPoints line (:func:`netpoints_totals_statement`),
    read by :data:`NETPOINTS_TOTALS`' order - or ``None`` with no row.

    .. versionadded:: 6.0.0
    """
    return _first(_values(con, netpoints_totals_statement(athlete_id, season, season_type)))


def season_categories(con: duckdb.DuckDBPyConnection, athlete_id: str, season: int, categories: Sequence[str]) -> tuple[Any, ...] | None:
    """One player's season fingerprint row (:func:`netpoints_fingerprint_statement`):
    his possessions, then each category's offense, defense and total - or
    ``None`` with no row.

    .. versionadded:: 6.0.0
    """
    return _first(_values(con, netpoints_fingerprint_statement(athlete_id, season, categories)))


def game_line(con: duckdb.DuckDBPyConnection, athlete_id: str, season: int, season_type: int, order: str) -> tuple[Any, ...] | None:
    """One player's first (``order="first"``) or most recent game's
    NetPoints (:func:`netpoints_game_statement`): the event, its date, the
    offense, defense and total, the possessions and the win probability
    added - or ``None`` with no row. Raises what DuckDB raises where the
    opt-in per-game table is missing; the reader says so.

    .. versionadded:: 6.0.0
    """
    return _first(_values(con, netpoints_game_statement(athlete_id, season, season_type, order)))


# --- a player's fingerprint ------------------------------------------------------


def season_fingerprints(con: duckdb.DuckDBPyConnection, season: int, columns: Sequence[Column]) -> list[tuple[Any, ...]]:
    """Every player's season fingerprint, in one read: the athlete, his
    minutes and possessions, then ``columns`` in order - the pool a
    percentile is ranked against and the players' own rows out of one
    statement (:func:`~association.query.fingerprint.load_fingerprints`).
    No season_type filter: ``net_points_player_fingerprint`` has no such
    column. Whatever the season's rows cover is what a fingerprint means.

    .. versionadded:: 6.0.0
       ``fingerprint.load_fingerprints``' statement, moved whole.
    """
    return _values(con, Statement(f"SELECT athlete_id, minutes, total_poss, {_season_columns(columns)} FROM net_points_player_fingerprint WHERE season = ?", [season]))


def game_fingerprints(con: duckdb.DuckDBPyConnection, season: int, season_type: int, columns: Sequence[Column]) -> list[tuple[Any, ...]]:
    """Every player-game's fingerprint in a season, pivoted from the LONG
    per-game table (:func:`_game_pivots`): the event, the athlete, the
    game's possessions, then ``columns`` in order. Raises
    :class:`duckdb.CatalogException` where the opt-in tables were never
    pulled; the loader names that cause
    (:func:`~association.query.fingerprint.load_game_fingerprints`).

    .. versionadded:: 6.0.0
       ``fingerprint._load_game_fingerprints_rows``' statement, moved whole.
    """
    return _values(
        con,
        Statement(
            f"SELECT f.event_id, f.athlete_id, max(g.t_poss), {_game_pivots(columns)} "
            "FROM net_points_player_game_fingerprint f "
            # A plain join: net_points_player_game holds one row per
            # player-game. It used to hold two for 611 of them in 2026 alone,
            # disagreeing, because two NetPoints dates resolved to one ESPN
            # game - fixed at the source in NetPointsGameIndex rather than
            # collapsed here. max() over the group is still what reads the
            # possessions, since the group is the category pivot.
            "JOIN net_points_player_game g ON g.event_id = f.event_id AND g.athlete_id = f.athlete_id "
            "WHERE f.season = ? AND f.season_type = ? AND f.athlete_id IS NOT NULL "
            "GROUP BY f.event_id, f.athlete_id",
            [season, season_type],
        ),
    )


def fingerprint_game_of(con: duckdb.DuckDBPyConnection, athlete_id: str, season: int, season_type: int, order: str) -> tuple[Any, ...] | None:
    """One player's first (``order="first"``) or most recent game of a
    season with a per-game fingerprint, by real date: its event and date, or
    ``None``. Dated from ``games`` rather than by event_id order: ids are
    assigned by schedule, and a postponed game keeps the id it was given for
    the date it was meant to be played on.

    .. versionadded:: 6.0.0
       ``fingerprint._game_for``'s statement, moved whole.
    """
    return _first(
        _values(
            con,
            Statement(
                "SELECT f.event_id, gm.date FROM net_points_player_game_fingerprint f "
                "JOIN games gm ON gm.event_id = f.event_id "
                "WHERE f.athlete_id = ? AND f.season = ? AND f.season_type = ? "
                f"GROUP BY f.event_id, gm.date ORDER BY gm.date {'ASC' if order == 'first' else 'DESC'} LIMIT 1",
                [athlete_id, season, season_type],
            ),
        )
    )
