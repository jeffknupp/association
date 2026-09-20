"""Describing a single game a chart or fingerprint was drawn for.

A bare event id ("game 401705764") tells a reader nothing about which game was
drawn - not the date, the opponent, or the result, although ``games`` holds
all three for that id. This is the one place that builds the sentence -
"2025-04-13 vs POR, W 118-104" - so :mod:`association.query.shotchart` and
:mod:`association.query.fingerprint` say it identically rather than drifting
into two descriptions of the same game (see `ISSUES.md` #155 and the "One
concept, one definition" rule in `AGENTS.md`).

.. versionadded:: 4.4.0
"""

from __future__ import annotations

import duckdb

from association.nba.season import eastern_date

_GAME_LABEL_SQL = """
SELECT pgl.game_date, pgl.opponent_abbr,
       g.home_team_id = pgl.team_id AS at_home,
       g.winner_team_id = pgl.team_id AS won,
       CASE WHEN g.home_team_id = pgl.team_id THEN g.home_score ELSE g.away_score END AS team_score,
       CASE WHEN g.home_team_id = pgl.team_id THEN g.away_score ELSE g.home_score END AS opponent_score
FROM player_game_log pgl
JOIN games g ON g.event_id = pgl.event_id AND g.season = pgl.season
WHERE pgl.athlete_id = ? AND pgl.event_id = ?
LIMIT 1
"""


def game_label(con: duckdb.DuckDBPyConnection, athlete_id: str, event_id: str) -> str | None:
    """One player's game, as ``"<date> vs|@ <OPP>, <W/L> <score>-<score>"``.

    Read from ``player_game_log`` - which already carries season-correct team
    abbreviations through a franchise rename, see
    :func:`association.nba.franchises.season_name` - joined to ``games`` for
    the final score. ``game_date`` is a raw UTC stamp, so it is dated through
    :func:`association.nba.season.eastern_date`, never a fixed hour offset.

    Returns None where the warehouse cannot support the sentence: a fixture or
    an old warehouse with no ``player_game_log``/``games`` (caught as a
    :class:`duckdb.CatalogException`, not a bare ``except``), or a game with no
    usable box-score row for this athlete - a placeholder event, or one whose
    score is not yet posted. A caller that gets None keeps naming the game by
    its event id, exactly as before this existed.

    .. versionadded:: 4.4.0
    """
    try:
        row = con.execute(_GAME_LABEL_SQL, [athlete_id, event_id]).fetchone()
    except duckdb.CatalogException:
        return None
    if row is None or any(value is None for value in row):
        return None
    date, opponent_abbr, at_home, won, team_score, opponent_score = row
    # "vs" for a home game and "@" for a road one, as every other log here
    # writes it. Saying "vs" for both was wrong on exactly half the games -
    # measured on the first example this was checked against, Curry's
    # 2026-01-25 game, which was at Minnesota - and placing the game is the
    # whole purpose of the label.
    return f"{eastern_date(date)} {'vs' if at_home else '@'} {opponent_abbr}, {'W' if won else 'L'} {team_score}-{opponent_score}"
