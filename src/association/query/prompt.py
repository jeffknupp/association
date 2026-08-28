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
}

TABLE_SUMMARY = """
teams               - one row per NBA team (team_id, abbreviation, display_name, ...)
players             - one row per player (athlete_id, display_name, position_abbr, ...)
games               - one row per game (event_id, season, season_type, date, home/away team_id + score, venue, status)
player_box_stats    - one row per player PER GAME (points, rebounds, assists, fieldGoalsMade/Attempted, threePointFieldGoalsMade/Attempted, freeThrowsMade/Attempted, etc.)
team_box_stats      - one row per team PER GAME (team-level totals for the same categories)
player_season_stats - one row per player per season per season_type: SEASON totals (fieldGoalsMade, points, ...) and PER-GAME averages (avgPoints, avgAssists, ...), both already computed by ESPN
team_season_stats   - one row per team per season per season_type (100+ advanced team stats: pace, offReboundRate, trueShootingPct, ...)
standings           - one row per team per season (wins, losses, winPercent, playoffSeed, streak, ...)
team_power_index    - one row per team per season per season_type (ESPN BPI: bpi, bpioffense, bpidefense, projectedw, ...)
shot_chart          - one row per shot ATTEMPT (event_id, athlete_id, team_id, period, clock, made, shot_type, coordinate_x, coordinate_y, points_attempted)
win_probability     - one row per play (event_id, play_id, home_win_pct, tie_pct)
stat_glossary       - stat_key -> label/description; self-documents what a column means
player_game_log     - convenience view: player_box_stats joined with player/game/team names

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
            "description": "Render a static HTML shot chart (made/missed shots on a simplified court) for one player, optionally filtered by season, season_type, event_id (one game), period (quarter/OT), shot_value (2 or 3 pointers, or 1 for free throws), and made_only.",
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
                        "description": "Only set this if the user explicitly asked for made shots only or missed shots only. true = only made shots, false = only missed shots. Omit (do not guess) to show both, which is what most requests want.",
                    },
                },
                "required": ["player_name"],
            },
        },
    },
]
