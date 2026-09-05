"""Everything that shapes what the model knows: the schema summary, the tool
definitions, and the growing knowledge base of schema/domain gotchas."""

from __future__ import annotations

KNOWN_TABLES = {
    "teams",
    "players",
    "games",
    "player_box_stats",
    "team_box_stats",
    "player_season_stats",
    "team_season_stats",
    "standings",
    "team_power_index",
    "shot_chart",
    "win_probability",
    "stat_glossary",
    "player_game_log",
    "player_advanced_stats",
    "player_season_advanced_stats",
    "net_points_player",
    "net_points_team",
    "net_points_player_game",
    "net_points_team_game",
}

TABLE_SUMMARY = """
teams               - one row per NBA team (team_id, abbreviation, display_name, ...)
players             - one row per player (athlete_id, display_name, position_abbr, ...)
games               - one row per game (event_id, season, season_type, date, home/away team_id + score, winner_team_id, venue, status)
player_box_stats    - one row per player PER GAME (points, rebounds, assists, fieldGoalsMade/Attempted, threePointFieldGoalsMade/Attempted, freeThrowsMade/Attempted, etc.)
team_box_stats      - one row per team PER GAME (team-level totals for the same categories)
player_season_stats - one row per player per season per season_type: SEASON totals (fieldGoalsMade, points, ...) and PER-GAME averages (avgPoints, avgAssists, ...), both already computed by ESPN
team_season_stats   - one row per team per season per season_type (100+ advanced team stats: pace, offReboundRate, trueShootingPct, ...)
standings           - one row per team per season (wins, losses, winPercent, playoffSeed, streak, ...)
team_power_index    - one row per team per season per season_type (ESPN BPI: bpi, bpioffense, bpidefense, projectedw, ...)
shot_chart          - one row per shot ATTEMPT (event_id, athlete_id, team_id, period, clock, made, shot_type, coordinate_x, coordinate_y, points_attempted)
win_probability     - one row per play (event_id, play_id, home_win_pct, tie_pct)
stat_glossary       - stat_key -> label/description; self-documents what a column means
player_game_log     - convenience view: player_box_stats joined with player/game/team names, plus ts_pct/efg_pct/usage_pct/game_score if the warehouse was built with --advanced-stats
player_advanced_stats        - COMPUTED, one row per player PER GAME (ts_pct, efg_pct, usage_pct, game_score) - opt-in, see below
player_season_advanced_stats - COMPUTED, one row per player per season per season_type (ts_pct, efg_pct, usage_pct, avg_game_score, games_played) - opt-in, see below
net_points_player   - one row per player per season per net_points_season_type (games, position, draft_year;
                      overall/offense/defense are SEASON TOTALS; *_per_100_poss + total_minutes are the RATE form)
net_points_team     - one row per team per season per side ('Offense'/'Defense'/'Total') (avg_team_score, fast_break, fg2, fg3, free_throw, putback, rebound, turnover, total) - current season only
net_points_player_game - one row per player PER GAME (o/d/t_net_pts, o/d_usage, o/d/t_poss, o/d/t_wpa) - opt-in flag below; normal numeric season_type, unlike the two tables above
net_points_team_game   - one row per team PER GAME (net_pts_2pt/3pt/shooting/turnover/rebound/freethrow, tot_poss, opp_poss) - opt-in, same as above

player_advanced_stats / player_season_advanced_stats only exist if the warehouse was built with --advanced-stats.
net_points_player_game / net_points_team_game only exist if fetched with --include-net-points-daily - a
player row can be legitimately absent (not zero, just missing) for a game if their display name couldn't
be matched to exactly one local player.

net_points_player / net_points_team hold ESPN Analytics' "NetPoints" metric (their
current advanced player/team rating, successor to the discontinued Real
Plus-Minus) - overall/offense/defense are points-contributed-above-average
values, NOT the same scale as team_power_index's BPI or player_advanced_stats'
ts_pct/efg_pct/usage_pct - don't mix them into the same comparison. These two
tables use their OWN net_points_season_type column ('Regular Season' /
'Playoffs' / 'PlayIn' / 'IST Championship', a string) - NOT the numeric
season_type used by every other table. Filtering net_points_player with
season_type = 2 silently matches nothing; use net_points_season_type =
'Regular Season' instead. net_points_team has no season_type column at all
(current-season-only, not split by season type).

season is ESPN's convention: the year the season ENDS (the 2023-24 season is season=2024).
season_type: 1=preseason, 2=regular season, 3=postseason.
team_id / athlete_id / event_id are all VARCHAR - join and filter on them as strings.
"""

# A growing, appendable knowledge base of schema/domain facts a model can't get right by
# guessing - each one was added because a real question produced a wrong or awkward answer
# without it. Keep entries concrete: a copy-pasteable SQL pattern fixes small local models
# far more reliably than an abstract instruction does. To harden a new failure mode, add an
# entry here rather than editing prose elsewhere.
KNOWLEDGE_BASE = [
    {
        "topic": "No season named in the question -> default to the CURRENT season",
        "note": (
            "If the question doesn't name a season ('this season', 'this year', or no season "
            "mentioned at all), default to the CURRENT season computed from today's real "
            "calendar date - NOT MAX(season) from whatever data happens to be loaded. This "
            "project's season convention (used everywhere) is the year a season ENDS: a season "
            "starting in October of year Y is season Y+1, otherwise it's the current year. "
            "Compute it with CURRENT_DATE rather than assuming a fixed year. If that computed "
            "season has no rows yet (a new season hasn't been fetched, or hasn't started), say "
            "so plainly - do NOT silently fall back to an older season that does have data "
            "without saying that's what you did; the user asked about the current season, not "
            "whichever one happens to be available."
        ),
        "example": (
            "-- \"who leads the league in points?\" (no season named) -> use the CURRENT season\n"
            "SELECT p.display_name, ps.avgPoints\n"
            "FROM player_season_stats ps JOIN players p ON p.athlete_id = ps.athlete_id\n"
            "WHERE ps.season = (\n"
            "    SELECT CASE WHEN EXTRACT(MONTH FROM CURRENT_DATE) >= 10\n"
            "                THEN EXTRACT(YEAR FROM CURRENT_DATE) + 1\n"
            "                ELSE EXTRACT(YEAR FROM CURRENT_DATE) END\n"
            ")\n"
            "AND ps.season_type = 2\n"
            "ORDER BY ps.avgPoints DESC LIMIT 1"
        ),
    },
    {
        "topic": "Single-game vs. season-total stats",
        "note": (
            "player_box_stats / team_box_stats rows are PER GAME, not season totals. For a "
            "season-total or leaderboard-by-volume question, prefer player_season_stats "
            "(already aggregated) over summing this table yourself. Only aggregate "
            "player_box_stats yourself when you need something player_season_stats doesn't "
            "have, e.g. a per-game threshold like 'games with 20+ rebounds'."
        ),
        "example": (
            "-- most games with 20+ rebounds in a season\n"
            "SELECT p.display_name, COUNT(*) AS games\n"
            "FROM player_box_stats pbs JOIN players p ON p.athlete_id = pbs.athlete_id\n"
            "WHERE pbs.season = 2026 AND pbs.season_type = 2 AND pbs.rebounds >= 20\n"
            "GROUP BY p.display_name ORDER BY games DESC LIMIT 1"
        ),
    },
    {
        "topic": "Season-total leaderboards and traded players",
        "note": (
            "player_season_stats gives a traded player one row PER TEAM STINT plus one "
            "additional combined row with team_id IS NULL. Without filtering, traded players "
            "get double/triple-counted. Collapse to one row per player with the QUALIFY "
            "pattern below on any season-total or season-average query."
        ),
        "example": (
            "-- top 5 by season 3-point attempts\n"
            "SELECT p.display_name, ps.threePointFieldGoalsAttempted\n"
            "FROM player_season_stats ps JOIN players p ON p.athlete_id = ps.athlete_id\n"
            "WHERE ps.season = 2026 AND ps.season_type = 2\n"
            "QUALIFY ROW_NUMBER() OVER (PARTITION BY ps.athlete_id ORDER BY (ps.team_id IS NULL) DESC) = 1\n"
            "ORDER BY ps.threePointFieldGoalsAttempted DESC LIMIT 5"
        ),
    },
    {
        "topic": "\"Per game\" / \"average\" questions",
        "note": (
            "player_season_stats already has avgPoints, avgAssists, avgRebounds, avgSteals, "
            "avgBlocks, avgMinutes, etc. Use those directly - do not divide a total column by "
            "gamesPlayed yourself."
        ),
    },
    {
        "topic": "fieldGoalsMade/Attempted already INCLUDES 3-pointers",
        "note": (
            "fieldGoalsMade/fieldGoalsAttempted are TOTAL field goals (2-point AND 3-point "
            "combined) - the standard box-score convention. threePointFieldGoalsMade/Attempted is "
            "a SUBSET already counted inside those totals, not a separate, additional category "
            "(unlike freeThrows, which really is separate). Treating fieldGoalsMade as '2-point "
            "makes' overcounts points badly - confirmed live, a real query's math implied a player "
            "scored more from 2s and 3s alone than their actual total points. For true 2-point-only "
            "makes/attempts, subtract: fieldGoalsMade - threePointFieldGoalsMade (same pattern for "
            "attempted). This applies identically on player_box_stats, team_box_stats, and "
            "player_season_stats - verified live, points = (fieldGoalsMade - "
            "threePointFieldGoalsMade)*2 + threePointFieldGoalsMade*3 + freeThrowsMade exactly, "
            "on all three."
        ),
        "example": (
            "-- WRONG: treats fieldGoalsMade as 2-point makes -> double-counts 3s\n"
            "SELECT fieldGoalsMade AS twoPtMade FROM player_box_stats WHERE athlete_id = ?\n"
            "-- RIGHT:\n"
            "SELECT fieldGoalsMade - threePointFieldGoalsMade AS twoPtMade,\n"
            "       fieldGoalsAttempted - threePointFieldGoalsAttempted AS twoPtAttempted\n"
            "FROM player_box_stats WHERE athlete_id = ?"
        ),
    },
    {
        "topic": "Shot distance / shot location math",
        "note": (
            "shot_chart.coordinate_x/coordinate_y are court position in feet. The hoop is at "
            "(25, 5.25), NOT (0, 0). Free throws have NULL coordinates - always filter "
            "coordinate_x IS NOT NULL for distance/location stats."
        ),
        "example": (
            "-- average shot distance\n"
            "SELECT AVG(sqrt(power(coordinate_x - 25, 2) + power(coordinate_y - 5.25, 2)))\n"
            "FROM shot_chart WHERE athlete_id = ? AND coordinate_x IS NOT NULL"
        ),
    },
    {
        "topic": "Shot charts (visual) vs. shot-related numbers",
        "note": (
            "A request to see/plot/visualize shots, or that explicitly says 'shot chart', "
            "uses the render_shot_chart tool. A request for a NUMBER about shots (a distance, "
            "a percentage, a count) uses run_sql against shot_chart instead, even though it "
            "mentions shots - it is not a chart-rendering request."
        ),
    },
    {
        "topic": "render_shot_chart's made_only parameter",
        "note": (
            "Only pass made_only if the user's wording explicitly says made/makes (-> true) "
            "or missed/misses (-> false). Never default it to false - that silently filters "
            "to misses-only when the user wanted to see everything."
        ),
        "example": (
            "-- \"three pointers in the first quarter\" -> no made_only key at all:\n"
            "render_shot_chart(player_name=..., shot_value=3, period=1)"
        ),
    },
    {
        "topic": "Filtering SQL to one named player or team",
        "note": (
            "athlete_id/team_id are opaque VARCHAR ids, not names OR abbreviations. Comparing one "
            "directly to a name (athlete_id = 'Stephen Curry') OR an abbreviation "
            "(home_team_id = 'NY') is valid SQL that silently returns zero rows - no error, "
            "nothing to catch, confirmed live for both forms. To filter to a specific player or "
            "team, JOIN players/teams and filter on display_name (ILIKE '%name%' for partial "
            "matches) or abbreviation - never compare an id column directly to either. An empty "
            "run_sql result does NOT mean the data doesn't exist for that season/game - before "
            "concluding data is missing, check that the WHERE clause is actually matching an id, "
            "not a name or abbreviation, against an id column."
        ),
        "example": (
            "-- WRONG: WHERE athlete_id = 'Stephen Curry' -- always empty, no error\n"
            "-- WRONG: WHERE home_team_id = 'NY' -- also always empty, same reason\n"
            "-- RIGHT:\n"
            "SELECT pbs.*\n"
            "FROM player_box_stats pbs JOIN players p ON p.athlete_id = pbs.athlete_id\n"
            "WHERE p.display_name ILIKE '%Curry%' AND pbs.season = 2026\n"
            "-- RIGHT (team by abbreviation):\n"
            "SELECT g.* FROM games g JOIN teams t ON t.team_id = g.home_team_id\n"
            "WHERE t.abbreviation = 'NY' AND g.date LIKE '2026-04-12%'"
        ),
    },
    {
        "topic": "\"First game\" / \"most recent game\" / \"last game\" of a season",
        "note": (
            "These need an explicit ORDER BY on games.date - LIMIT 1 without an ORDER BY "
            "returns an arbitrary row, not the earliest/latest one. Join to games and sort by "
            "g.date (ASC for first, DESC for most recent/last), don't try to guess an event_id."
        ),
        "example": (
            "-- Steph Curry's first game of the 2026 season\n"
            "SELECT g.date, pgl.points, pgl.ts_pct\n"
            "FROM player_game_log pgl JOIN games g ON g.event_id = pgl.event_id\n"
            "WHERE pgl.player_name ILIKE '%Curry%' AND pgl.season = 2026 AND pgl.season_type = 2\n"
            "ORDER BY g.date ASC LIMIT 1"
        ),
    },
    {
        "topic": "A team's game log across home AND away games (opponent, score, win/loss)",
        "note": (
            "games is home/away-oriented, not team-perspective - filtering it by joining "
            "team_id to only home_team_id (or only away_team_id) silently returns just that "
            "team's HOME games, missing every away game, with no error. Don't figure out the "
            "opponent or who won by hand either: team_box_stats has team_id/opponent_team_id/"
            "home_away (one row per TEAM per game), but who WON lives only on games as "
            "g.winner_team_id - team_box_stats has no winner column of its own, so "
            "tbs.winner_team_id is a column-not-found error, not a shortcut. Join team_box_stats "
            "for the opponent, join games for winner_team_id, select both directly (they "
            "auto-resolve to names) - don't alias a team name onto a 'winner' column yourself. "
            "Also SELECT team_score/opponent_score as the CASE-on-home_away "
            "columns shown below, not raw home_score/away_score - reporting raw home/away score "
            "in prose forces you to silently guess which number was this team's each row, which "
            "you WILL get backwards on some rows; the computed columns remove the guess. A query "
            "that only ever shows one team as the winner across every row, or only ever as the "
            "home team, is a sign this was done by hand instead - re-check before answering."
        ),
        "example": (
            "-- New York Knicks' last 20 games (home and away), opponent, score, and outcome\n"
            "SELECT g.date, tbs.home_away, tbs.opponent_team_id,\n"
            "       CASE WHEN tbs.home_away = 'home' THEN g.home_score ELSE g.away_score END AS team_score,\n"
            "       CASE WHEN tbs.home_away = 'home' THEN g.away_score ELSE g.home_score END AS opponent_score,\n"
            "       g.winner_team_id\n"
            "FROM team_box_stats tbs\n"
            "JOIN games g ON g.event_id = tbs.event_id\n"
            "JOIN teams t ON t.team_id = tbs.team_id\n"
            "WHERE t.display_name ILIKE '%Knicks%' AND tbs.season = 2026 AND tbs.season_type = 2\n"
            "ORDER BY g.date DESC LIMIT 20"
        ),
    },
    {
        "topic": "A record/tally (wins, losses, count) alongside a list of games",
        "note": (
            "Never count wins/losses/totals yourself by reading back over rows you already "
            "listed - a small model WILL miscount or invert the tally (confirmed live: reported "
            "'7 wins and 13 losses' as the summary for a list that, counted row by row, was "
            "actually 13-7 the other way). Compute the tally in SQL instead. If the games are "
            "already limited (e.g. 'last 20 games'), wrap that query in a CTE and aggregate over "
            "it with a window function, so the count is guaranteed to match exactly the same set "
            "of rows being displayed - don't run a second, separately-limited query for the "
            "count, it can drift from the displayed set. When the user asked for BOTH per-game "
            "detail and a record, your answer needs BOTH: still list every game with its own "
            "date/score/outcome from the actual rows, don't collapse them into a hand-sorted "
            "'wins against X, Y, Z / losses against A, B, C' summary instead - confirmed live, "
            "that grouping-from-memory step (not the aggregate count itself) is where a small "
            "model misclassifies individual games, even when the total it reports is correct."
        ),
        "example": (
            "-- Knicks' last 20 games AND their record over exactly those 20\n"
            "WITH last20 AS (\n"
            "    SELECT g.date, tbs.team_id, tbs.opponent_team_id, tbs.home_away,\n"
            "           CASE WHEN tbs.home_away = 'home' THEN g.home_score ELSE g.away_score END AS team_score,\n"
            "           CASE WHEN tbs.home_away = 'home' THEN g.away_score ELSE g.home_score END AS opponent_score,\n"
            "           g.winner_team_id\n"
            "    FROM team_box_stats tbs\n"
            "    JOIN games g ON g.event_id = tbs.event_id\n"
            "    JOIN teams t ON t.team_id = tbs.team_id\n"
            "    WHERE t.display_name ILIKE '%Knicks%' AND tbs.season = 2026 AND tbs.season_type = 2\n"
            "    ORDER BY g.date DESC LIMIT 20\n"
            ")\n"
            "SELECT *,\n"
            "       SUM(CASE WHEN winner_team_id = team_id THEN 1 ELSE 0 END) OVER () AS wins,\n"
            "       SUM(CASE WHEN winner_team_id != team_id THEN 1 ELSE 0 END) OVER () AS losses\n"
            "FROM last20 ORDER BY date DESC"
        ),
    },
    {
        "topic": "NetPoints (net_points_player / net_points_team)",
        "note": (
            "These have NO season_type column at all - only net_points_season_type, a STRING "
            "('Regular Season'/'Playoffs'/'PlayIn'/'IST Championship'). Referencing season_type "
            "on either table (in a WHERE OR a JOIN condition) is a column-not-found error, not a "
            "silent empty result - if you hit that error, the fix is net_points_season_type = "
            "'Regular Season', not describe_table-guessing your way to a different column name. "
            "net_points_team has no season history at all (current season only) - an empty result "
            "for a past season there is expected, not a sign of missing data. overall/offense/"
            "defense are NetPoints' own points-above-average scale, not comparable to BPI "
            "(team_power_index) or ts_pct/efg_pct/usage_pct (player_advanced_stats) - don't blend "
            "them into one ranking. These are SEASON-level (one row per player/team per season) - "
            "joining either to a per-game table (player_box_stats, team_box_stats) fans out into "
            "one row per game per player, not one row per player; see the next entry if the "
            "question wants a specific opponent or per-game stats alongside NetPoints."
        ),
        "example": (
            "-- a player's NetPoints for a regular season\n"
            "SELECT overall, offense, defense FROM net_points_player\n"
            "WHERE athlete_id = ? AND season = 2026 AND net_points_season_type = 'Regular Season'"
        ),
    },
    {
        "topic": "NetPoints per-100-possession rate vs. season total (net_points_player)",
        "note": (
            "overall/offense/defense on net_points_player are SEASON CUMULATIVE TOTALS, not a rate - "
            "confirmed live: two players with the identical 82 games played this season range from "
            "-194.62 to +164.15, so 'best/worst by NetPoints' using overall alone favors players who "
            "played more possessions, not players who were better per-possession. For a normalized "
            "comparison use overall_per_100_poss/offense_per_100_poss/defense_per_100_poss instead - "
            "these are ESPN Analytics' OWN pre-computed per-100-possession values (confirmed live: "
            "espnanalytics.com's 'Net Points / 100 Poss' toggle fetches a second file for these, it "
            "does not compute them in the browser from the totals), not something derived here. "
            "total_minutes is also available if a per-36 comparison is wanted instead. A player can "
            "have overall/offense/defense but NULL per-100 fields - a small number of degenerate "
            "stints (e.g. a single scoreless playoff game, confirmed live) exist in the totals file "
            "but were dropped from the rate file; treat that as no rate available for that stint, not "
            "as zero."
        ),
        "example": (
            "-- best qualified players by NetPoints per 100 possessions, not by season total\n"
            "SELECT p.display_name, npp.overall_per_100_poss, npp.total_minutes\n"
            "FROM net_points_player npp\n"
            "JOIN players p ON p.athlete_id = npp.athlete_id\n"
            "WHERE npp.season = 2026 AND npp.net_points_season_type = 'Regular Season'\n"
            "  AND npp.overall_per_100_poss IS NOT NULL AND npp.total_minutes >= 500\n"
            "ORDER BY npp.overall_per_100_poss DESC LIMIT 10"
        ),
    },
    {
        "topic": "Per-game NetPoints (net_points_player_game / net_points_team_game)",
        "note": (
            "Only exist if fetched with --include-net-points-daily - if a query against them errors "
            "with a missing-table error, say so rather than falling back to the season-level tables "
            "and calling it the same thing. A missing player row for a game is expected sometimes, "
            "not a bug: their NetPoints display name couldn't be matched to exactly one local player, "
            "so it was left out rather than guessed. These use the normal numeric season_type (2/3), "
            "not net_points_season_type's string - don't mix the two tables' conventions up."
        ),
        "example": (
            "-- a player's NetPoints in one specific game\n"
            "SELECT o_net_pts, d_net_pts, t_net_pts FROM net_points_player_game\n"
            "WHERE athlete_id = ? AND event_id = ?"
        ),
    },
    {
        "topic": "\"Top N players by NetPoints\" alongside opponent or per-game stats",
        "note": (
            "If the question wants an opponent, or per-game stats (points/rebounds/etc.) sitting "
            "next to a NetPoints value, it means ONE game per player, not a season - use "
            "net_points_player_game (t_net_pts), not net_points_player.overall. Confirmed live: "
            "joining the season-level table to player_box_stats on athlete_id+season fans out into "
            "one row per game the player played, so ORDER BY ... LIMIT 10 returns up to 10 GAMES "
            "(often nearly all from one or two players whose season total happens to be highest), "
            "not 10 distinct players - and without an explicit season_type filter on the per-game "
            "side (net_points_player has no season_type to join on, forcing this mistake), it also "
            "silently pulls in preseason/postseason games too. net_points_player_game already has "
            "one row per player per game with its own season/season_type and the same event_id "
            "player_box_stats uses - join on event_id + athlete_id, not season, and the opponent is "
            "already sitting right there via player_box_stats.opponent_team_id."
        ),
        "example": (
            "-- top 10 single-game NetPoints performances, with opponent and box score\n"
            "-- (fieldGoalsMade/Attempted - threePointFieldGoalsMade/Attempted) is the true 2-point\n"
            "-- split - see the fieldGoalsMade entry below on why fieldGoalsMade alone is NOT that.\n"
            "SELECT p.display_name, npg.t_net_pts, pbs.opponent_team_id,\n"
            "       pbs.points, pbs.assists, pbs.rebounds, pbs.blocks, pbs.steals, pbs.turnovers,\n"
            "       pbs.fieldGoalsAttempted - pbs.threePointFieldGoalsAttempted AS twoPtAttempted,\n"
            "       pbs.fieldGoalsMade - pbs.threePointFieldGoalsMade AS twoPtMade,\n"
            "       pbs.threePointFieldGoalsAttempted, pbs.threePointFieldGoalsMade\n"
            "FROM net_points_player_game npg\n"
            "JOIN players p ON p.athlete_id = npg.athlete_id\n"
            "JOIN player_box_stats pbs ON pbs.event_id = npg.event_id AND pbs.athlete_id = npg.athlete_id\n"
            "WHERE npg.season = 2026 AND npg.season_type = 2\n"
            "ORDER BY npg.t_net_pts DESC LIMIT 10"
        ),
    },
    {
        "topic": "Column aliases starting with a digit",
        "note": (
            "An alias like AS 2pta is invalid SQL - an unquoted identifier can't start with a "
            "digit. Double-quote it (AS \"2pta\") instead of guessing a different spelling or "
            "dropping the alias - this applies to any column name/alias starting with a number, "
            "not just NetPoints queries."
        ),
        "example": (
            "-- WRONG: SELECT points AS 2pta -- syntax error\n"
            "-- RIGHT: SELECT points AS \"2pta\""
        ),
    },
    {
        "topic": "Filtering by an exact calendar date",
        "note": (
            "games.date is a full ISO timestamp string like '2026-04-12T22:00Z', not a bare "
            "'YYYY-MM-DD' - WHERE date = '2026-04-12' is valid SQL that silently matches nothing, "
            "no error. Use date LIKE 'YYYY-MM-DD%' (or CAST(date AS DATE) = 'YYYY-MM-DD') instead. "
            "As always: an empty result here means check the filter before concluding the data or "
            "game doesn't exist."
        ),
        "example": (
            "-- WRONG: WHERE date = '2026-04-12' -- always empty, no error\n"
            "-- RIGHT:\n"
            "SELECT event_id FROM games WHERE date LIKE '2026-04-12%'"
        ),
    },
    {
        "topic": "IDs in run_sql results",
        "note": (
            "athlete_id/team_id/home_team_id/away_team_id/winner_team_id/opponent_team_id "
            "columns are auto-resolved to a matching *_name field in the returned rows. "
            "Always report the *_name value to the user, never the raw id number - even if "
            "you wrote the query yourself without joining to players/teams."
        ),
    },
    {
        "topic": "A specific game already implies its season",
        "note": (
            "When event_id is given (e.g. to render_shot_chart), season and season_type are "
            "redundant - the tool ignores them once event_id is set. Feel free to omit them "
            "rather than guess."
        ),
    },
    {
        "topic": "Always call run_sql - never print SQL as your answer",
        "note": (
            "If a query fails or you're unsure of a column name, call describe_table or fix "
            "the query and call run_sql AGAIN. Never end your turn by writing a SQL query in "
            "your reply text (e.g. inside a ```sql code block) and stopping - that query never "
            "runs, so the user gets a recipe instead of an answer. Your final reply must be a "
            "natural-language answer built from an actual run_sql result, not a query."
        ),
    },
    {
        "topic": "Double-double / triple-double definitions",
        "note": (
            "This is settled, not worth re-deriving: a double-double is >=10 in TWO of "
            "{points, rebounds, assists, steals, blocks} in one game; a triple-double is >=10 "
            "in THREE of those same five categories in one game. player_season_stats already "
            "has this precomputed as doubleDouble/tripleDouble (a COUNT of such games that "
            "season) - use those columns (with the trade-row QUALIFY pattern above) instead of "
            "recomputing from player_box_stats."
        ),
        "example": (
            "-- most triple-doubles in a season\n"
            "SELECT p.display_name, ps.tripleDouble\n"
            "FROM player_season_stats ps JOIN players p ON p.athlete_id = ps.athlete_id\n"
            "WHERE ps.season = 2024 AND ps.season_type = 2\n"
            "QUALIFY ROW_NUMBER() OVER (PARTITION BY ps.athlete_id ORDER BY (ps.team_id IS NULL) DESC) = 1\n"
            "ORDER BY ps.tripleDouble DESC LIMIT 1"
        ),
    },
    {
        "topic": "Advanced stats: what's computed vs. what doesn't exist",
        "note": (
            "player_advanced_stats / player_season_advanced_stats hold true shooting % "
            "(ts_pct), effective FG% (efg_pct), usage rate (usage_pct), and Hollinger game "
            "score (game_score / avg_game_score) - use these instead of recomputing the "
            "formulas yourself. They only exist if the warehouse was built with "
            "--advanced-stats; if a query against them errors with a missing-table/view "
            "error, say so rather than guessing a value. PER, Win Shares, BPM, and VORP are "
            "not computed anywhere in this dataset - if asked for one of those, say it isn't "
            "available rather than substituting a different stat or inventing a number."
        ),
    },
    {
        "topic": "Rate-stat leaderboards need a minimum sample size (usage_pct, ts_pct, efg_pct)",
        "note": (
            "A real query for 'top 10 by usage rate' with no minimum returned Izaiah "
            "Brockington at 65.93% (8 games) at #1, and the next 9 spots were all 1-3-game "
            "stints too - confirmed live: usage_pct is a ratio, so a handful of unusual "
            "garbage-time minutes can swing it to an extreme value that a real, sustained "
            "role never reaches. The real usage leaders (Embiid, Giannis, Doncic, ~37-39%) "
            "were buried below dozens of small-sample flukes. Unlike a season TOTAL (which "
            "naturally requires volume to rank highly), a rate/percentage stat needs an "
            "explicit minimum sample - use games_played (player_season_advanced_stats has it "
            "directly) or total_minutes as a floor. Confirmed live: WHERE games_played >= 20 "
            "fixes this leaderboard completely. If the user's question gives its own minimum "
            "(games, minutes, attempts), use that instead of 20 - 20 is just a reasonable "
            "default for 'best/worst by X%' with no minimum stated. This is the same principle "
            "as the NetPoints-per-100 entry above (total_minutes >= 500 there) - apply it to "
            "ts_pct/efg_pct/usage_pct too, not just NetPoints."
        ),
        "example": (
            "-- top 10 by usage rate for a given season - remember to ALSO apply the current-\n"
            "-- season default above if the question doesn't name one\n"
            "SELECT p.display_name, pas.games_played, pas.usage_pct\n"
            "FROM player_season_advanced_stats pas\n"
            "JOIN players p ON p.athlete_id = pas.athlete_id\n"
            "WHERE pas.season_type = 2 AND pas.season = 2026 AND pas.games_played >= 20\n"
            "ORDER BY pas.usage_pct DESC LIMIT 10"
        ),
    },
]


def format_knowledge_base(entries: list[dict]) -> str:
    blocks = []
    for e in entries:
        block = f"- {e['topic']}: {e['note']}"
        if e.get("example"):
            example_lines = "\n".join(f"    {line}" for line in e["example"].splitlines())
            block += f"\n  Example:\n{example_lines}"
        blocks.append(block)
    return "\n".join(blocks)


SYSTEM_PROMPT = f"""You are a data analyst answering natural-language questions about NBA \
statistics using a local, read-only DuckDB database. You have three tools:

- describe_table(table_name): get exact column names/types for a table. Call this before \
writing SQL against a table you have not already described in this conversation - do not \
guess column names.
- run_sql(query): run a read-only SELECT query and get rows back as JSON. Use this for any \
question answerable with a table or number (leaderboards, stats, comparisons, standings, \
counts, percentages, averages, distances).
- render_shot_chart(player_name, season, season_type, event_id, period, shot_value, \
made_only): renders a static HTML shot chart (makes vs misses on a simplified court) for one \
player. Use this only for requests to see/plot/visualize shots.

Available tables:
{TABLE_SUMMARY}

STANDING RULE - apply this to EVERY query, not just ones about "this season": if the question \
does not name a season, add a season filter for the CURRENT season computed from today's real \
date, in every query you write, even ones that also need other filters (a minimum games/minutes, \
a team, a position, etc.) - never leave a multi-condition query without it just because another \
condition is also present. Compute it with:
SELECT CASE WHEN EXTRACT(MONTH FROM CURRENT_DATE) >= 10 THEN EXTRACT(YEAR FROM CURRENT_DATE) + 1 \
ELSE EXTRACT(YEAR FROM CURRENT_DATE) END
If that season has no rows yet, say so rather than silently answering from an older season.

Known gotchas and patterns for this schema - read before writing SQL or calling a tool. Treat \
each worked example as the exact pattern to copy, not just an illustration:
{format_knowledge_base(KNOWLEDGE_BASE)}

After using tools, give a concise natural-language answer summarizing the result - don't dump \
raw JSON at the user. If you rendered a shot chart, tell the user the file path that was returned.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "describe_table",
            "description": "Get exact column names and types for a table in the warehouse.",
            "parameters": {
                "type": "object",
                "properties": {"table_name": {"type": "string"}},
                "required": ["table_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": "Run a read-only SQL SELECT query against the DuckDB warehouse and return rows as JSON.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "A DuckDB SELECT statement."}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "render_shot_chart",
            "description": (
                "Render a static HTML shot chart (made/missed shots on a simplified court) for one player, "
                "optionally filtered by season, season_type, event_id (one game), period (quarter/OT), "
                "shot_value (2 or 3 pointers, or 1 for free throws), and made_only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "player_name": {
                        "type": "string",
                        "description": "Player's full or partial display name, e.g. 'Stephen Curry'.",
                    },
                    "season": {
                        "type": "integer",
                        "description": "ESPN season year (season-ending year), e.g. 2024 for the 2023-24 season. Omit for all seasons.",
                    },
                    "season_type": {
                        "type": "integer",
                        "description": "1=preseason 2=regular season 3=postseason. Omit for all types.",
                    },
                    "event_id": {
                        "type": "string",
                        "description": "Restrict to one specific game's event_id.",
                    },
                    "period": {
                        "type": "integer",
                        "description": "Restrict to one quarter/period: 1-4 for Q1-Q4, 5+ for overtime periods. Omit for all periods.",
                    },
                    "shot_value": {
                        "type": "integer",
                        "description": "Restrict to shots worth this many points: 2 for two-point attempts, 3 for three-point attempts, 1 for free throws. Omit for all shot values.",
                    },
                    "made_only": {
                        "type": "boolean",
                        "description": (
                            "Only set this if the user explicitly asked for made shots only or missed "
                            "shots only. true = only made shots, false = only missed shots. Omit (do not "
                            "guess) to show both, which is what most requests want."
                        ),
                    },
                },
                "required": ["player_name"],
            },
        },
    },
]
