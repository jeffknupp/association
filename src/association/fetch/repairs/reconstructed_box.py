"""A box line rebuilt from play-by-play, for the games whose box score is empty.

ESPN serves every Chicago and New Orleans game from 2013 to 2018 with each
player listed as having played, no minutes, and every stat zero - see
``DATA.md``, "Every Chicago and New Orleans game from 2013 to 2018 has an empty
box score". That is 1,025 events, **all of them in 2013-2018**; 1,024 carry
play-by-play, the exception being ``400828893`` (2016-03-16, WSH at CHI).

Note what this view does NOT cover. Vancouver's 1996 is a *different* fault -
its team box rows are empty but its player rows are real (``DATA.md``,
"Vancouver 1996 is an empty TEAM box, not an empty player box"), so there is
nothing here to rebuild. And nothing before 2002 could be rebuilt anyway:
``plays`` does not go back further.

**No other ESPN source has the real numbers.**
Probed live on 2026-09-14: the CDN box score, on a different host entirely,
serves the same zeros; the core API exposes no per-athlete per-game statistics
at any path; and the athlete gamelog omits those games outright rather than
returning them empty. The plays, however, survived - 1,024 of the 1,025 empty
events carry play-by-play at normal density.

So rebuilding from ``plays`` is the only route to a per-game number here, and
the number it produces is DERIVED. Everything about this module is shaped by
keeping that visible:

- **Its own view, covering only the empty games.** Nothing is written into
  ``player_box_stats``. A reconstructed value sitting in the same column as a
  fetched one is indistinguishable from it, and every serious failure this
  project has had was an answer that looked exactly like a good one.
- **Deliberately absent from** :data:`association.query.prompt.KNOWN_TABLES`.
  The SQL agent can therefore neither query nor describe it, and it costs
  nothing against the preamble budget, which has no room anyway. Do not add it
  there without first deciding what the agent should say about a derived
  number - "Anthony Davis scored 24" and "Anthony Davis probably scored about
  24" are different sentences.
- **snake_case columns**, like the other derived module
  (:mod:`association.fetch.advanced_stats`), rather than ESPN's camelCase. A
  column named ``field_goals_made`` cannot be mistaken for ``fieldGoalsMade``
  at a glance, which is the point.
- **No** :mod:`association.coverage` **entry.** Floors exist to refuse
  questions, and no template reads this, so there is no question to refuse.

**A player who appears in no play is absent, not zero.** Minutes are gone, so
"did not play" and "played and did nothing" cannot be told apart, and emitting
a zero for both would recreate the exact bug this whole effort is about: a
zero that reads as a real performance.

Accuracy is per column and is NOT uniform
-----------------------------------------

Measured by rebuilding the 2015 regular season the same way and comparing
against the 22,660 player-games whose real box score survived. The percentage
is how often the rebuilt value equals ESPN's exactly:

- ``free_throws_made`` 100.0%, ``free_throws_attempted`` 100.0%
- ``offensive_rebounds`` 99.8%, ``defensive_rebounds`` 99.8%, ``blocks`` 99.8%
- ``assists`` 99.6%, ``field_goals_made`` 99.6%, ``rebounds`` 99.6%
- ``field_goals_attempted`` 99.5%, ``steals`` 99.1%
- ``three_point_field_goals_made`` 98.6%, ``points`` 98.3%
- ``three_point_field_goals_attempted`` 96.8%
- ``turnovers`` 92.5%
- ``fouls`` 83.3% - the weakest, and it is a real ceiling rather than a
  mis-attribution: ``athlete_id`` on a foul play is the player who COMMITTED
  it ("Al Jefferson shooting foul (Brandon Knight draws the foul)"), and
  crediting the other participant instead scores 24.6%.

``minutes`` and ``plus_minus`` are not columns here at all, because
play-by-play cannot recover them and a NULL column invites someone to fill it.

**Per game is not per season, and the difference is large.** Those figures are
per player-game; a season total accumulates them, so it is exact far less
often. Measured over the 230 Chicago and New Orleans player-seasons whose whole
team-season is empty - the only ones where the rebuilt games ARE the whole
season - against ESPN's independent season endpoint: games played exact 91.7%,
**points exact 51.7%** (within 2, 72.2%), rebounds 64.8%, assists 65.2%, blocks
76.5%, steals 67.0%. That is what ~98% per-game accuracy looks like spread over
~70 games. The error is biased LOW rather than symmetric, because a game whose
plays are thin contributes less than it should and never more.

**2016 is much the worst season.** Mean season point error is -0.4 to -2.7 in
2013, 2014, 2015, 2017 and 2018, and **-16.8 in 2016**, where only 7 of 37
player-seasons rebuild exactly. It is not missing coverage - play volume is
normal (72,400 plays over 160 events, 24.3% of them scoring) - but 180 of
2016's scoring plays carry ``type = 'Not Available'``, which no shot rule can
classify, so they land in ``points`` without landing in the shot columns.

The honest summary for a reader: this is good enough to say what a game roughly
looked like, and not good enough to quote as a record.

The two views, and which one to read
------------------------------------

``player_box_stats_reconstructed`` is the rebuild alone: one row per player per
game, for the empty games only. Read it to see what the rebuild says.

``player_box_stats_filled`` is ``player_box_stats`` with those figures dropped
into the empty lines, and it is the one a query should read. It has the same
column names as the stored table, so it is a drop-in, plus one extra:
``reconstructed``, true on exactly the rows whose numbers were rebuilt. A
caller that reads a substituted figure and does not pass that flag on is
quoting a derived number as ESPN's - so the flag is part of the contract, not a
debugging aid. ``minutes`` and ``plusMinus`` stay NULL on a substituted row.

Neither view is in :data:`association.query.prompt.KNOWN_TABLES`, so the SQL
agent reaches neither. That is deliberate for the filled view too: an agent
writing its own SQL has nowhere to put the caveat that the flag demands.

Two notes on how the numbers are read out of ``plays``, both measured rather
than assumed. A shot is identified by what its ``type`` NAMES - several real
shot types contain no word "Shot" (``Driving Finger Roll Layup``,
``Layup Driving Reverse``, ``Dunk Putback Slam``), and a filter on ``'%Shot%'``
alone scored 88.3% on attempts against 99.5% for the positive list below.
Assists, steals and blocks belong to the SECOND id in
``participant_athlete_ids``, not to ``athlete_id``: on "Michael Kidd-Gilchrist
blocks Jabari Parker's layup" the play's athlete is Parker, the shooter.

.. versionadded:: 2.2.0
"""

from __future__ import annotations

import logging

import duckdb

log: logging.Logger = logging.getLogger("association.fetch.repairs.reconstructed_box")

#: The views this module builds.
#:
#: ``player_box_stats_reconstructed`` is the rebuild on its own, covering only
#: the empty games. ``player_box_stats_filled`` is ``player_box_stats`` with
#: those rebuilt figures dropped into the empty lines and a ``reconstructed``
#: flag saying which rows they are - a drop-in for a reader that wants the
#: warehouse's best answer rather than a hole, and which is obliged to pass the
#: flag on to whoever reads it.
#:
#: .. versionadded:: 2.2.0
VIEWS: list[str] = ["player_box_stats_reconstructed", "player_box_stats_filled"]

# Every stat column the filled view substitutes, as (stored camelCase name,
# rebuilt snake_case name). Written out rather than derived from the schema so
# that adding a column to one side and not the other is a visible edit here,
# not a silently missing substitution.
_FILLED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("points", "points"),
    ("fieldGoalsMade", "field_goals_made"),
    ("fieldGoalsAttempted", "field_goals_attempted"),
    ("threePointFieldGoalsMade", "three_point_field_goals_made"),
    ("threePointFieldGoalsAttempted", "three_point_field_goals_attempted"),
    ("freeThrowsMade", "free_throws_made"),
    ("freeThrowsAttempted", "free_throws_attempted"),
    ("rebounds", "rebounds"),
    ("offensiveRebounds", "offensive_rebounds"),
    ("defensiveRebounds", "defensive_rebounds"),
    ("assists", "assists"),
    ("steals", "steals"),
    ("blocks", "blocks"),
    ("turnovers", "turnovers"),
    ("fouls", "fouls"),
)

# Columns each source table must have before the view can be built. A partial
# `data load --tables` subset, or a genuinely thin ESPN response, skips this
# view with a logged reason rather than failing the whole warehouse build - the
# same contract advanced_stats.build_views keeps.
_REQUIRED_PLAYS_COLUMNS = {"event_id", "season", "athlete_id", "participant_athlete_ids", "type", "text", "scoring_play"}
# Derived from _FILLED_COLUMNS rather than listed again, so a column added to
# the substitution table cannot be left out of the guard: the earlier version of
# this set omitted `opponent_team_id`, which the SELECT reads, and a thin table
# would have passed the guard and then failed on the SQL.
_REQUIRED_BOX_COLUMNS = {
    "event_id",
    "season",
    "season_type",
    "team_id",
    "opponent_team_id",
    "athlete_id",
    "minutes",
    "starter",
    "did_not_play",
    "dnp_reason",
    "ejected",
    "plusMinus",
} | {stored for stored, _ in _FILLED_COLUMNS}

# A shot attempt, named positively. See the module docstring: defining it by
# exclusion instead swept in plays that are not attempts and cost 22 points of
# accuracy on field_goals_attempted.
_SHOT = """(
    (p.type ILIKE '%Shot%' OR p.type ILIKE '%Layup%' OR p.type ILIKE '%Dunk%' OR p.type ILIKE '%Jumper%'
     OR p.type ILIKE '%Hook%' OR p.type ILIKE '%Tip%' OR p.type ILIKE '%Finger Roll%')
    AND p.type NOT ILIKE 'Free Throw%' AND p.type NOT ILIKE '%Turnover%'
    AND p.type NOT ILIKE '%Foul%' AND p.type NOT ILIKE '%Block%'
)"""

# ESPN writes the value of a made shot nowhere, so it is read from the text -
# the same place its own play description carries it.
_THREE = "p.text ILIKE '%three point%'"

# Free throws are worth one point each and are never field goals; everything
# else that scores is worth three if the text says so and two otherwise.
_POINTS = f"""CASE
    WHEN NOT p.scoring_play THEN 0
    WHEN p.type ILIKE 'Free Throw%' THEN 1
    WHEN {_THREE} THEN 3
    ELSE 2
END"""


def build_views(con: duckdb.DuckDBPyConnection, loaded: set[str]) -> None:
    """Create ``player_box_stats_reconstructed`` over the empty games.

    Skipped, with a logged reason, when ``plays`` or ``player_box_stats`` is
    absent or missing a column the rebuild needs, rather than failing the whole
    warehouse build.

    .. versionadded:: 2.2.0
    """
    for table, required in (("plays", _REQUIRED_PLAYS_COLUMNS), ("player_box_stats", _REQUIRED_BOX_COLUMNS)):
        if table not in loaded:
            log.info("skip reconstructed box scores (%s not loaded)", table)
            return
        missing = required - {r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()}
        if missing:
            log.info("skip reconstructed box scores (%s missing columns: %s)", table, ", ".join(sorted(missing)))
            return

    con.execute(f"""
        CREATE OR REPLACE VIEW player_box_stats_reconstructed AS
        WITH empty_games AS (
            -- The same test _empty_box_scores draws in query/templates.py: a
            -- team-game where no player has any minutes at all. Keyed on
            -- season as well as event_id, because the phantom 1993 season
            -- shares its event ids with 1994 (see coverage.py).
            SELECT event_id, season
            FROM player_box_stats
            GROUP BY event_id, season
            HAVING MAX(minutes) IS NULL
        ),
        scoped AS (
            SELECT p.*, str_split(p.participant_athlete_ids, ',') AS parts
            FROM plays p
            JOIN empty_games e ON e.event_id = p.event_id AND e.season = p.season
        ),
        by_actor AS (
            -- What the play's own athlete did.
            SELECT
                p.event_id, p.season, p.athlete_id AS athlete_id,
                SUM({_POINTS}) AS points,
                SUM(CASE WHEN {_SHOT} AND p.scoring_play THEN 1 ELSE 0 END) AS field_goals_made,
                SUM(CASE WHEN {_SHOT} THEN 1 ELSE 0 END) AS field_goals_attempted,
                SUM(CASE WHEN {_SHOT} AND p.scoring_play AND {_THREE} THEN 1 ELSE 0 END) AS three_point_field_goals_made,
                SUM(CASE WHEN {_SHOT} AND {_THREE} THEN 1 ELSE 0 END) AS three_point_field_goals_attempted,
                SUM(CASE WHEN p.type ILIKE 'Free Throw%' AND p.scoring_play THEN 1 ELSE 0 END) AS free_throws_made,
                SUM(CASE WHEN p.type ILIKE 'Free Throw%' THEN 1 ELSE 0 END) AS free_throws_attempted,
                SUM(CASE WHEN p.type = 'Offensive Rebound' THEN 1 ELSE 0 END) AS offensive_rebounds,
                SUM(CASE WHEN p.type = 'Defensive Rebound' THEN 1 ELSE 0 END) AS defensive_rebounds,
                SUM(CASE WHEN p.type ILIKE '%Turnover%' THEN 1 ELSE 0 END) AS turnovers,
                SUM(CASE WHEN p.type ILIKE '%Foul%' AND p.type NOT ILIKE '%Technical%' THEN 1 ELSE 0 END) AS fouls,
                0 AS assists, 0 AS steals, 0 AS blocks
            FROM scoped p
            WHERE p.athlete_id IS NOT NULL
            GROUP BY p.event_id, p.season, p.athlete_id
            UNION ALL
            -- What the play's SECOND participant did: the assister on a made
            -- shot, the stealer on a turnover, the blocker on a miss. Never
            -- the play's own athlete, who is the shooter or the loser of the
            -- ball. Fouls are excluded here on purpose - the second id is the
            -- player who DREW the foul, and crediting him scored 24.6%.
            SELECT
                p.event_id, p.season, p.parts[2] AS athlete_id,
                0 AS points, 0 AS field_goals_made, 0 AS field_goals_attempted,
                0 AS three_point_field_goals_made, 0 AS three_point_field_goals_attempted,
                0 AS free_throws_made, 0 AS free_throws_attempted,
                0 AS offensive_rebounds, 0 AS defensive_rebounds,
                0 AS turnovers, 0 AS fouls,
                SUM(CASE WHEN p.text ILIKE '%assists%' THEN 1 ELSE 0 END) AS assists,
                SUM(CASE WHEN p.text ILIKE '%steals%' THEN 1 ELSE 0 END) AS steals,
                SUM(CASE WHEN p.text ILIKE '%blocks%' THEN 1 ELSE 0 END) AS blocks
            FROM scoped p
            WHERE len(p.parts) >= 2
            GROUP BY p.event_id, p.season, p.parts[2]
        ),
        totals AS (
            SELECT
                event_id, season, athlete_id,
                CAST(SUM(points) AS BIGINT) AS points,
                CAST(SUM(field_goals_made) AS BIGINT) AS field_goals_made,
                CAST(SUM(field_goals_attempted) AS BIGINT) AS field_goals_attempted,
                CAST(SUM(three_point_field_goals_made) AS BIGINT) AS three_point_field_goals_made,
                CAST(SUM(three_point_field_goals_attempted) AS BIGINT) AS three_point_field_goals_attempted,
                CAST(SUM(free_throws_made) AS BIGINT) AS free_throws_made,
                CAST(SUM(free_throws_attempted) AS BIGINT) AS free_throws_attempted,
                CAST(SUM(offensive_rebounds) AS BIGINT) AS offensive_rebounds,
                CAST(SUM(defensive_rebounds) AS BIGINT) AS defensive_rebounds,
                CAST(SUM(offensive_rebounds) + SUM(defensive_rebounds) AS BIGINT) AS rebounds,
                CAST(SUM(assists) AS BIGINT) AS assists,
                CAST(SUM(steals) AS BIGINT) AS steals,
                CAST(SUM(blocks) AS BIGINT) AS blocks,
                CAST(SUM(turnovers) AS BIGINT) AS turnovers,
                CAST(SUM(fouls) AS BIGINT) AS fouls
            FROM by_actor
            WHERE athlete_id IS NOT NULL
            GROUP BY event_id, season, athlete_id
        )
        -- Identity comes from the stored row, which survived intact: only the
        -- STATS were zeroed, so team_id, opponent_team_id and the roster are
        -- ESPN's own. An INNER join, so a listed player who appears in no play
        -- is absent rather than zero - see the module docstring.
        SELECT
            b.event_id, b.season, b.season_type, b.team_id, b.opponent_team_id, b.athlete_id,
            t.points, t.field_goals_made, t.field_goals_attempted,
            t.three_point_field_goals_made, t.three_point_field_goals_attempted,
            t.free_throws_made, t.free_throws_attempted,
            t.offensive_rebounds, t.defensive_rebounds, t.rebounds,
            t.assists, t.steals, t.blocks, t.turnovers, t.fouls
        FROM player_box_stats b
        JOIN totals t ON t.event_id = b.event_id AND t.season = b.season AND t.athlete_id = b.athlete_id
    """)

    # The same rows, with the rebuild dropped into the holes. Two rules make
    # this safe to read where reading `player_box_stats` directly is not:
    #
    #   - It substitutes ONLY into an empty line, and that comes entirely from
    #     the rebuild's own scope: `player_box_stats_reconstructed` covers the
    #     wholly-empty games and nothing else, so a player who truly scored 0
    #     in a real game has no row to join to and keeps his 0. An explicit
    #     `AND b.minutes IS NULL` was here and has been REMOVED: perturbation
    #     showed no test could reach it (deleting it broke nothing), and a
    #     guard nothing can exercise is a claim, not a check. If the rebuild is
    #     ever widened past the empty games, put it back AND write the test
    #     that fails without it.
    #   - Every substituted row is flagged. `reconstructed` is not decoration:
    #     a caller that reads these figures without passing it on is quoting a
    #     derived number as ESPN's, which is the failure this whole area is
    #     about. See the accuracy table in the module docstring - per season
    #     these are exact only about half the time.
    #
    # `minutes` needs no handling: the stored value on an empty line is already
    # NULL, which is what makes the join find it.
    #
    # `plusMinus` DOES need handling, and the reason is worth recording.
    # It looks like surviving data on these rows - it is not NULL, unlike every
    # stat beside it - but measured over all 21,169 substituted rows it takes
    # exactly ONE distinct value, 0, and each team-game's plus-minus sums to
    # exactly 0.0. That is the same fabricated zero as the stats, wearing a
    # different face, so it is nulled rather than passed through. Play-by-play
    # cannot recover a real one: it would need the lineup on the floor for every
    # possession, which is what minutes would have told us.
    substitutions = ",\n            ".join(f"CASE WHEN r.athlete_id IS NOT NULL THEN r.{rebuilt} ELSE b.{stored} END AS {stored}" for stored, rebuilt in _FILLED_COLUMNS)
    con.execute(f"""
        CREATE OR REPLACE VIEW player_box_stats_filled AS
        SELECT
            b.event_id, b.season, b.season_type, b.team_id, b.opponent_team_id, b.athlete_id,
            b.starter, b.did_not_play, b.dnp_reason, b.ejected,
            b.minutes,
            CASE WHEN r.athlete_id IS NOT NULL THEN NULL ELSE b.plusMinus END AS plusMinus,
            (r.athlete_id IS NOT NULL) AS reconstructed,
            {substitutions}
        FROM player_box_stats b
        LEFT JOIN player_box_stats_reconstructed r
               ON r.event_id = b.event_id AND r.season = b.season AND r.athlete_id = b.athlete_id
    """)
    log.info("reconstructed box score views built: %s", ", ".join(VIEWS))
