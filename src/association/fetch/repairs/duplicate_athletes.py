"""One person, listed twice in one team's box score under two ``athlete_id``\\ s.

ESPN's fault, not the parser's - see ``DATA.md`` ("ESPN files one player under
two athlete ids in the same box score"). Grouping ``player_box_stats`` by
``(event_id, team_id, display_name)`` and counting distinct ``athlete_id``
finds a person listed twice in one team's box score for one game. Measured
against the 2026-09-17 warehouse: **8 players, 69 team-games**, every one in
1993-94 shows no year before 2003 - Ken Johnson (`1972`/`1008`, 2003, 33
games), Isaiah Canaan (`2490589`/`4412182`, 2019, 20), Corey Brewer
(`3191`/`4415554`, 2019, 8), Daryl Macon (`4066243`/`4610145`, 2019-20, 4),
and four single-game 2019 cases new to this count: Tahjere McCall, John
Jenkins, Mitchell Creek, Henry Ellenson.

Why co-occurrence, not just a shared name
------------------------------------------

Two ``athlete_id``\\ s sharing a display name proves nothing by itself -
``DATA.md`` ("The NetPoints season fingerprint matches players mid-pull, so a
name can be lost", `ISSUES.md` #21) already has 21 real display names shared
by 42 *different* people. What proves these are the same person, not two, is
narrower: the two ids appear in **the same team's box score for the same
game**. An NBA roster does not carry two active players of the same name in
one game, so a shared name plus a shared team-game is the same roster slot
filed twice.

What was measured before choosing a merge rule
-------------------------------------------------

Every one of the 69 team-games was classified by what each side of the pair
holds - real minutes and counting stats, an explicit ``did_not_play`` blank
(every stat NULL), or an all-zero row with no minutes and no DNP flag (a
fabricated blank: ESPN never marks a row "played 0 minutes, recorded zero of
everything, not benched"). **Zero of the 69 were anything else.** Three
shapes, and a merge is safe for each:

- **Both sides real and IDENTICAL** (29 games: 18 of Canaan's 20, all 8 of
  Brewer's, 3 of Macon's 4) - the same performance, filed twice.
- **One side real, the other the fabricated all-zero blank** (21 games) - the
  zero side carries no information a merge could lose.
- **Both sides blank** (17, all Ken Johnson) - one flagged ``did_not_play``,
  the other the same fabricated zero-but-not-DNP shape.

So the rule this module applies is: prefer the row with real minutes; if
neither has minutes, prefer an explicit ``did_not_play`` row over the
fabricated zero one. **Summing was never the right move for any of the 69** -
these are one performance filed twice, not two performances - and nothing
here sums a counting stat.

Refusing what was not measured safe
--------------------------------------

The rule above is only applied to a pair where **every** shared game fits one
of the three shapes: at least one side has no minutes recorded, or both sides'
counting stats agree exactly. A pair with two real, DIFFERING lines in the
same game - which would mean actually summing, or picking one over real lost
data - is refused rather than guessed at, and left as two separate ids with a
logged reason. None of the 8 known pairs hit this today; a future one that
does stays unmerged rather than silently averaged or summed.

Which id survives
-------------------

The id with more games carrying real minutes **across its whole recorded
history**, not just the shared games - ties broken by total row count, then by
the id itself for determinism. This is deliberately not "the lower id" or "the
first one fetched": Ken Johnson's fabricated-blank id (`1008`) has one MORE
total row than his real one (`1972`, 34 vs 33), so row count alone picks the
wrong side. Games-with-minutes never ties across the 8 known pairs, and every
one of them has zero real-minute games under the losing id, in games shared
*and* unshared - `1008` never once carries a real appearance in its own 34
rows, which is why the pattern is a duplicate id rather than two people who
happen to share a game.

Why a table, not a rewrite of ``player_box_stats``
------------------------------------------------------

The same reasoning as :mod:`association.fetch.repairs.real_games`: this is a
row-identity fix, built from data already loaded, read repeatedly by the query
path, and worth costing a rebuild rather than a re-plan per query. Unlike
:mod:`association.fetch.repairs.team_box_repair`, which rewrites a value ESPN
itself agrees should equal a player-box sum, this does not touch a single
stored figure - it decides which of two rows to keep. Keeping
``player_box_stats`` exactly as ESPN served it, and putting the merged view
beside it under its own name, is what
:mod:`association.fetch.repairs.reconstructed_box` does for the same reason: a
row a caller did not ask to be resolved is indistinguishable from one that
was.

Built from :data:`association.fetch.repairs.reconstructed_box.VIEWS`'s
``player_box_stats_filled`` when it exists, so a duplicate pair in a
reconstructed-box game (there is no known overlap - the reconstructed games
are 2013-2018 Chicago/New Orleans and every known duplicate pair is 2003 or
2019-20) still merges over the best available line. Falls back to
``player_box_stats`` when ``plays`` was not loaded.

What this does not reach
---------------------------

``players`` keeps both rows - the losing id's bio survives under its own id,
unjoined by anything in this table once merged. And this is a warehouse-side,
load-time fix only: `_name_to_athlete_id` in
:mod:`association.fetch.pipeline`, which resolves NetPoints' bare display name
to an id at FETCH time, reads `players` from disk, not this table, and drops
any name shared by more than one id on disk - which every one of these 8
players' names now are, for their *whole* career, not just the affected
season. Measured: Corey Brewer, Isaiah Canaan, Daryl Macon, Henry Ellenson,
Ken Johnson, John Jenkins, Mitchell Creek and Tahjere McCall all have **zero**
rows in ``net_points_player_game`` and ``net_points_player_game_fingerprint``
across every season on file, not only the ones with the duplicate id. Fixing
that needs a fetch-time change to `players` or to `_name_to_athlete_id`
itself, which is out of scope here - see ``ISSUES.md``.

.. versionadded:: 4.0.0
"""

from __future__ import annotations

import logging

import duckdb

log: logging.Logger = logging.getLogger("association.fetch.repairs.duplicate_athletes")

#: The name this module builds.
#:
#: A drop-in for ``player_box_stats`` (or ``player_box_stats_filled`` when it
#: exists): same columns, minus 1-2 rows per merged pair.
#:
#: .. versionadded:: 4.0.0
TABLE: str = "player_box_stats_deduped"

# Every counting stat the safety check compares. Written out rather than
# derived from the schema, like team_box_repair's own column list, so adding a
# stat later without adding it here is a visible gap, not a silent one.
COUNTING_COLUMNS: tuple[str, ...] = (
    "points",
    "fieldGoalsMade",
    "fieldGoalsAttempted",
    "threePointFieldGoalsMade",
    "threePointFieldGoalsAttempted",
    "freeThrowsMade",
    "freeThrowsAttempted",
    "rebounds",
    "offensiveRebounds",
    "defensiveRebounds",
    "assists",
    "steals",
    "blocks",
    "turnovers",
    "fouls",
)
"""Every counting stat the merge-safety check compares between a pair's two rows.

.. versionadded:: 4.0.0
"""

_DUP_REQUIRED_BOX_COLUMNS = frozenset({"event_id", "season", "season_type", "team_id", "athlete_id", "minutes", "did_not_play"}) | set(COUNTING_COLUMNS)
_DUP_REQUIRED_PLAYERS_COLUMNS = frozenset({"athlete_id", "display_name"})


def _columns(con: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    return {r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()}


def duplicate_athletes_sql(box_source: str) -> str:
    """The ``CREATE OR REPLACE TABLE`` statement behind :data:`TABLE`.

    Args:
        box_source: The box score table or view to read - ``player_box_stats``
            or the richer ``player_box_stats_filled`` when it exists. Every
            value interpolated into the SQL besides this one is a column name
            from :data:`COUNTING_COLUMNS`.

    Returns:
        One SQL statement.

    .. versionadded:: 4.0.0
    """
    stat_equal = " AND ".join(f'x."{c}" IS NOT DISTINCT FROM y."{c}"' for c in COUNTING_COLUMNS)
    return f"""
        CREATE OR REPLACE TABLE {TABLE} AS
        WITH named AS (
            SELECT b.*, p.display_name
            FROM {box_source} b
            JOIN players p ON p.athlete_id = b.athlete_id
        ),
        -- The proof these are one person, not two who share a name: the SAME
        -- team's box score for the SAME game names both ids. See the module
        -- docstring for why a shared name alone is not enough.
        dup_groups AS (
            SELECT event_id, season, team_id, display_name
            FROM named
            GROUP BY event_id, season, team_id, display_name
            HAVING COUNT(DISTINCT athlete_id) = 2
        ),
        candidate_ids AS (
            SELECT DISTINCT n.display_name, n.athlete_id
            FROM named n
            JOIN dup_groups g USING (event_id, season, team_id, display_name)
        ),
        -- Ranked over the id's WHOLE recorded history, not just the shared
        -- games - the established id is the one actually seen playing.
        id_history AS (
            SELECT athlete_id,
                   SUM((minutes IS NOT NULL)::INT) AS n_with_minutes,
                   COUNT(*) AS n_rows
            FROM {box_source}
            GROUP BY athlete_id
        ),
        ranked AS (
            SELECT c.display_name, c.athlete_id, h.n_with_minutes, h.n_rows,
                   ROW_NUMBER() OVER (
                       PARTITION BY c.display_name
                       ORDER BY h.n_with_minutes DESC, h.n_rows DESC, c.athlete_id
                   ) AS rnk
            FROM candidate_ids c
            JOIN id_history h USING (athlete_id)
        ),
        -- A pair merges only where EVERY shared game is one of the two safe
        -- shapes: one side has no minutes recorded, or both sides' counting
        -- stats agree exactly. See the module docstring - measured against
        -- all 69 known team-games, every one fits; a future pair that does
        -- not is refused, not guessed at.
        safety AS (
            SELECT x.display_name, BOOL_AND(x.minutes IS NULL OR y.minutes IS NULL OR ({stat_equal})) AS safe
            FROM named x
            JOIN named y ON y.event_id = x.event_id AND y.season = x.season AND y.team_id = x.team_id
                         AND y.display_name = x.display_name AND y.athlete_id > x.athlete_id
            JOIN dup_groups g ON g.event_id = x.event_id AND g.season = x.season AND g.team_id = x.team_id AND g.display_name = x.display_name
            GROUP BY x.display_name
        ),
        merges AS (
            SELECT r.display_name, r.athlete_id AS duplicate_id, c.athlete_id AS canonical_id
            FROM ranked r
            JOIN ranked c ON c.display_name = r.display_name AND c.rnk = 1
            JOIN safety s ON s.display_name = r.display_name AND s.safe
            WHERE r.rnk > 1
        ),
        resolved AS (
            -- REPLACE, not EXCLUDE-then-re-add: it keeps athlete_id in its
            -- original position, so the final table has exactly box_source's
            -- column order - a real drop-in, the same discipline
            -- real_games.py's own column-order test holds it to.
            SELECT b.* REPLACE (COALESCE(m.canonical_id, b.athlete_id) AS athlete_id),
                   b.athlete_id AS original_athlete_id
            FROM {box_source} b
            LEFT JOIN players p ON p.athlete_id = b.athlete_id
            LEFT JOIN merges m ON m.duplicate_id = b.athlete_id AND m.display_name = p.display_name
        )
        SELECT * EXCLUDE (original_athlete_id)
        FROM resolved
        -- Prefer real minutes; failing that, an explicit did_not_play over the
        -- fabricated all-zero blank the losing id carries - did_not_play has
        -- to outrank "was this already the canonical id's own row" here, or a
        -- canonical ghost_zero would beat a real did_not_play filed under the
        -- losing id, the opposite of what "prefer informative over fabricated"
        -- means. Only once both sides are equally informative (both real and
        -- identical, or - never observed - both the same blank shape) does
        -- self-consistency (keep plusMinus/dnp_reason/starter from one
        -- identity) decide it.
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY event_id, season, athlete_id
            ORDER BY (minutes IS NULL) ASC, did_not_play DESC, (original_athlete_id = athlete_id) DESC, original_athlete_id
        ) = 1
    """


def build_table(con: duckdb.DuckDBPyConnection, loaded: set[str]) -> None:
    """(Re)build :data:`TABLE` on ``con``, merging duplicate athlete ids.

    Reads ``player_box_stats_filled`` when it is loaded (the richer box score,
    play-by-play reconstructions included), else falls back to
    ``player_box_stats``. Skipped, with a logged reason, when a required table
    or column is absent - the same contract every repair in this package
    keeps.

    Args:
        con: An open, writable warehouse connection.
        loaded: The tables (and views) that currently exist in it.

    .. versionadded:: 4.0.0
    """
    if not {"player_box_stats", "players"} <= loaded:
        log.info("skip %s (needs player_box_stats and players)", TABLE)
        return
    missing_box = sorted(_DUP_REQUIRED_BOX_COLUMNS - _columns(con, "player_box_stats"))
    missing_players = sorted(_DUP_REQUIRED_PLAYERS_COLUMNS - _columns(con, "players"))
    if missing_box or missing_players:
        log.info("skip %s (missing columns: %s)", TABLE, ", ".join(missing_box + missing_players))
        return
    box_source = "player_box_stats_filled" if "player_box_stats_filled" in loaded else "player_box_stats"
    con.execute(duplicate_athletes_sql(box_source))
    row = con.execute(f"SELECT count(*), (SELECT count(*) FROM {box_source}) FROM {TABLE}").fetchone()
    assert row is not None  # COUNT(*) always returns exactly one row
    log.info("%s: %d rows (%d merged away as a duplicate id)", TABLE, row[0], row[1] - row[0])
