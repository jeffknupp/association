"""Games ESPN serves with the two teams on the wrong sides, put back at load time.

One game so far, and ESPN's fault: ``100614008``, Game 5 of the 1990 Finals
(``DATA.md``, "1990 Finals Game 5 is served with the wrong home team and
winner"). Detroit won it 92-90 at Portland to take the series 4-1. ESPN serves
Detroit at HOME scoring 90 and Portland away scoring 92 and winning, so every
answer read the title clincher as a Detroit home loss and the series as 3-2. A
refetch of the summary on 2026-09-16 served the same thing, so the parser is
not the cause.

What is wrong is the team on each side, not the sides. The home/away flags,
the scores and the winner flag all describe a 90-92 game won by the AWAY team;
only the two team ids are attached to the wrong competitors. Swapping the ids
makes the row the real game - Portland home 90, Detroit away 92 - and the
winner is then the away team, Detroit.

Why a hand-kept list rather than a rule
---------------------------------------

Nothing in the warehouse can detect this from the row alone: 1988-1992 has no
box scores, no plays and no team box values, and the event id itself encodes
Detroit (``...008``) as the home team, so it is wrong the same way. What found
it is the shape of the series. Every postseason pairing in ``real_games`` was
checked on 2026-09-16 - 570 series - for a series that ends as soon as one
team reaches the wins it needs, and for home games in that era's format
(2-2-1 for a best-of-five first round, 2-2-1-1-1, and 2-3-2 for the Finals from
1985 to 2013). This game is the only one that fails outside 2001, whose
failures are all games ESPN is missing (``ISSUES.md``, "The 2001 playoffs are
missing about ten games"). A general rule would have one case to be fitted to,
so it is kept as data, with the evidence beside it.

Guarded on the stored value, never unconditional: a row is changed only while
it still holds the teams ESPN serves. That makes the repair idempotent - it
runs on every build and every partial ``data load`` - and makes it stop by
itself if ESPN ever corrects the game, rather than swapping it back.

.. versionadded:: 2.2.0
"""

from __future__ import annotations

import logging

import duckdb

log: logging.Logger = logging.getLogger("association.fetch.game_repair")

SWAPPED_SIDES: dict[str, tuple[str, str]] = {
    # 1990 Finals Game 5, 14 June 1990 at Portland: Detroit 92, Portland 90.
    # ESPN serves home DET (8) 90, away POR (22) 92, winner POR.
    "100614008": ("8", "22"),
}
"""Event ids whose two team ids ESPN attaches to the wrong sides, mapped to
the ``(home_team_id, away_team_id)`` ESPN serves for them.

.. versionadded:: 2.2.0
"""

_SIDE_GAME_COLUMNS = frozenset({"event_id", "home_team_id", "away_team_id", "home_score", "away_score", "winner_team_id"})
_SIDE_TEAM_BOX_COLUMNS = frozenset({"event_id", "team_id", "home_away"})


def _columns(con: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    return {r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()}


def _served(alias: str) -> str:
    """SQL true for a row that still holds exactly the teams ESPN serves."""
    pairs = " OR ".join(f"({alias}.event_id = '{event_id}' AND {alias}.home_team_id = '{home}' AND {alias}.away_team_id = '{away}')" for event_id, (home, away) in SWAPPED_SIDES.items())
    return f"({pairs})"


def _team_served(alias: str) -> str:
    """SQL true for a team box row that still holds the side ESPN serves."""
    pairs = " OR ".join(
        f"({alias}.event_id = '{event_id}' AND (({alias}.team_id = '{home}' AND {alias}.home_away = 'home') OR ({alias}.team_id = '{away}' AND {alias}.home_away = 'away')))"
        for event_id, (home, away) in SWAPPED_SIDES.items()
    )
    return f"({pairs})"


def repair(con: duckdb.DuckDBPyConnection, loaded: set[str]) -> None:
    """Put the teams of every game in :data:`SWAPPED_SIDES` on their real sides.

    Rewrites ``games`` (the two team ids, and the winner to follow the score)
    and ``team_box_stats`` (``home_away``) for those events, each only where
    the stored row still matches what ESPN serves. Either table is skipped with
    a logged reason when it is absent or missing a column, rather than failing
    the build. Must run before ``real_games`` is built from ``games``.

    Every value interpolated into the SQL is a literal from this module.

    .. versionadded:: 2.2.0
    """
    if "games" in loaded and _SIDE_GAME_COLUMNS <= _columns(con, "games"):
        served = _served("g")
        con.execute(f"""
            CREATE OR REPLACE TABLE games AS
            SELECT g.* REPLACE (
                CASE WHEN {served} THEN g.away_team_id ELSE g.home_team_id END AS home_team_id,
                CASE WHEN {served} THEN g.home_team_id ELSE g.away_team_id END AS away_team_id,
                -- After the swap the winner is whichever side outscored the
                -- other, now carrying the right team.
                CASE WHEN {served} THEN (CASE WHEN g.home_score > g.away_score THEN g.away_team_id ELSE g.home_team_id END) ELSE g.winner_team_id END AS winner_team_id
            )
            FROM games g
        """)
    else:
        log.info("skip game side repair for games (not loaded, or missing columns)")
    if "team_box_stats" in loaded and _SIDE_TEAM_BOX_COLUMNS <= _columns(con, "team_box_stats"):
        served = _team_served("t")
        con.execute(f"""
            CREATE OR REPLACE TABLE team_box_stats AS
            SELECT t.* REPLACE (
                CASE WHEN {served} THEN (CASE t.home_away WHEN 'home' THEN 'away' ELSE 'home' END) ELSE t.home_away END AS home_away
            )
            FROM team_box_stats t
        """)
    else:
        log.info("skip game side repair for team_box_stats (not loaded, or missing columns)")
    log.info("game side repair applied: %d event(s)", len(SWAPPED_SIDES))
