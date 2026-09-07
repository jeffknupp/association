"""Everything that shapes what the model knows: the schema summary, the tool
definitions, and the growing knowledge base of schema/domain gotchas."""

from __future__ import annotations

import json
import re
from typing import Any

from .metrics import CORE_METRIC_NAMES, EXTRA_FIELD_COLUMNS

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
    "player_season_stats_deduped",
    "plays",
    "player_advanced_stats",
    "player_season_advanced_stats",
    "net_points_player",
    "net_points_player_fingerprint",
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
plays               - one row per PLAY (event_id, play_id, period, clock, team_id, athlete_id, type, text,
                      home_score/away_score - the RUNNING score after this play, scoring_play - opt-in,
                      see below. NOT points-per-play - see the per-quarter-scoring entry below for that)
stat_glossary       - stat_key -> label/description; self-documents what a column means
player_game_log     - convenience view: player_box_stats joined with player/game/team names, plus ts_pct/efg_pct/usage_pct/game_score
player_season_stats_deduped - convenience view: player_season_stats already collapsed to one row
                      per player/season/season_type (picks the combined row for a traded player) -
                      prefer this over player_season_stats directly, no QUALIFY needed
player_advanced_stats        - COMPUTED (not from ESPN), one row per player PER GAME (ts_pct, efg_pct, usage_pct, game_score)
player_season_advanced_stats - COMPUTED (not from ESPN), one row per player per season per season_type (ts_pct, efg_pct, usage_pct, avg_game_score, games_played)
net_points_player   - one row per player per season per net_points_season_type (games, position, draft_year;
                      overall/offense/defense are SEASON TOTALS; *_per_100_poss + total_minutes are the RATE form)
net_points_player_fingerprint - one row per player per season (NO season_type - not split by regular/post).
                      Skill/play-type breakdown behind espnanalytics.com's "Net Pts Fingerprint": 22
                      categories (two_pt, two_pt_shooting, three_pt, three_pt_shooting, assist, bad_pass,
                      corner, cutting, driving, fade, fast_break, floating, foul, free_throw, hook, layup,
                      mid_range, putback, rebound, rim, total, turnover), each as <category>_o_net_pts /
                      _d_net_pts / _t_net_pts (offense/defense/total) - 66 NetPoints columns total. Also
                      games, minutes, total_poss, average_position, usage, assisted_rate.
net_points_team     - one row per team per season per side ('Offense'/'Defense'/'Total') (avg_team_score, fast_break, fg2, fg3, free_throw, putback, rebound, turnover, total) - current season only
net_points_player_game - one row per player PER GAME (o/d/t_net_pts, o/d_usage, o/d/t_poss, o/d/t_wpa) - opt-in flag below; normal numeric season_type, unlike the two tables above
net_points_team_game   - one row per team PER GAME (net_pts_2pt/3pt/shooting/turnover/rebound/freethrow, tot_poss, opp_poss) - opt-in, same as above

net_points_player_game / net_points_team_game only exist if fetched with --include-net-points-daily - a
player row can be legitimately absent (not zero, just missing) for a game if their display name couldn't
be matched to exactly one local player.
plays / shot_chart / win_probability only exist if fetched with --include-pbp.

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

# Schema/domain facts a model can't get right by guessing - each one was added
# because a real question produced a wrong or awkward answer without it. Keep
# entries concrete: a copy-pasteable SQL pattern fixes small local models far
# more reliably than an abstract instruction does.
#
# This list only has to serve the FALL-THROUGH path now: questions no template
# covers, where the agent still writes SQL by hand. Fourteen entries were
# removed once their whole subject moved into a template (game logs, records,
# leaderboards, double-doubles, shot charts, minimum samples, NetPoints rate-vs-
# total and fingerprint categories) - those rules live in code now, tested,
# where they cannot be truncated away or half-remembered. Recoverable from git
# if a gap turns up.
#
# What stays is what an arbitrary hand-written query can still trip on: silent
# traps (a string season_type that matches nothing, an ISO timestamp that makes
# `date = 'YYYY-MM-DD'` return zero rows), math that is wrong rather than empty
# (fieldGoalsMade already includes threes), and derivations no template does
# (per-quarter scoring, shot distance). Add here only when the fall-through
# path needs it; a shape a template should own belongs in templates.py.
KNOWLEDGE_BASE: list[dict[str, Any]] = [
    {
        "topic": "No season named in the question -> default to the CURRENT season",
        "note": (
            "If the question doesn't name a season ('this season', 'this year', or no season "
            "mentioned at all), default to the CURRENT season using the current_season() SQL "
            "function already defined in this warehouse - NOT MAX(season) from whatever data "
            "happens to be loaded, and not a CASE/EXTRACT expression written out by hand (that "
            "logic is exactly what current_season() already does). If that computed season has "
            "no rows yet (a new season hasn't been fetched, or hasn't started), say so plainly - "
            "do NOT silently fall back to an older season that does have data without saying "
            "that's what you did; the user asked about the current season, not whichever one "
            "happens to be available."
        ),
        "example": (
            "-- \"who leads the league in points?\" (no season named) -> use the CURRENT season\n"
            "SELECT p.display_name, ps.avgPoints\n"
            "FROM player_season_stats ps JOIN players p ON p.athlete_id = ps.athlete_id\n"
            "WHERE ps.season = current_season() AND ps.season_type = 2\n"
            "ORDER BY ps.avgPoints DESC LIMIT 1"
        ),
    },
    {
        "topic": "fieldGoalsMade/Attempted already INCLUDES 3-pointers",
        "keywords": ['twos', 'two', 'pointers', 'shooting', 'field', 'goals'],
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
        "topic": "Points scored in a specific quarter/period",
        "keywords": ['quarter', 'period', 'half', 'overtime', 'q1', 'q2', 'q3', 'q4'],
        "note": (
            "NOT a stored column anywhere - player_box_stats/player_season_stats only have GAME "
            "totals. It has to be derived from plays (needs --include-pbp): each scoring play "
            "carries the RUNNING home_score/away_score, so a made play's own point value is that "
            "running score minus the immediately PRIOR scoring play's score for the same side - "
            "computed with LAG() over ALL scoring plays in the game (ordered by period, then by "
            "clock converted to seconds-remaining - NOT by play_id, which is NOT reliably "
            "sortable as an integer across a whole game, confirmed live: it broke chronological "
            "order badly enough to make some plays 'earn' 90+ points). CRITICAL: compute the "
            "LAG() over every scoring play in the game first, THEN filter to one player in an "
            "OUTER query/CTE - filtering to one player's rows BEFORE the window function (e.g. "
            "in the same CTE) breaks the ordering context so LAG() jumps across whichever other "
            "plays happen to be missing, again producing impossible values (confirmed live, this "
            "exact mistake was made twice while building this pattern). Known limitation, be "
            "upfront about it: even correctly written, this derivation disagrees with the "
            "official player_box_stats game total for a small fraction of player-games (~1%, "
            "confirmed live across the full dataset) - likely genuine ESPN play-by-play vs. "
            "final-box-score inconsistencies, not something fixable in SQL. Mention this as an "
            "approximation when answering, don't state a count as exact fact. If plays isn't "
            "loaded (--include-pbp wasn't used), say this can't be answered rather than guessing "
            "from a table that only has full-game totals."
        ),
        "example": (
            "-- games where a player scored more than 15 points in a single quarter\n"
            "WITH ordered AS (\n"
            "    SELECT event_id, period, athlete_id, team_id, home_score, away_score,\n"
            "        CASE WHEN clock LIKE '%:%'\n"
            "             THEN CAST(split_part(clock, ':', 1) AS DOUBLE) * 60 + CAST(split_part(clock, ':', 2) AS DOUBLE)\n"
            "             ELSE CAST(clock AS DOUBLE) END AS secs_remaining\n"
            "    FROM plays WHERE scoring_play = true\n"
            "),\n"
            "deltas AS (\n"
            "    SELECT o.event_id, o.period, o.athlete_id,\n"
            "        CASE WHEN o.team_id = g.home_team_id\n"
            "             THEN o.home_score - COALESCE(LAG(o.home_score) OVER (PARTITION BY o.event_id ORDER BY o.period, o.secs_remaining DESC), 0)\n"
            "             ELSE o.away_score - COALESCE(LAG(o.away_score) OVER (PARTITION BY o.event_id ORDER BY o.period, o.secs_remaining DESC), 0)\n"
            "        END AS pts\n"
            "    FROM ordered o JOIN games g ON g.event_id = o.event_id\n"
            ")\n"
            "-- filter to one player only here, AFTER the window function above has already run\n"
            "SELECT event_id, period, SUM(pts) AS period_points FROM deltas\n"
            "WHERE athlete_id = ? GROUP BY event_id, period HAVING SUM(pts) > 15"
        ),
    },
    {
        "topic": "Shot distance / shot location math",
        "keywords": ['distance', 'far', 'deep', 'long', 'feet', 'range', 'location', 'coordinates'],
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
        "keywords": ['date', 'day', 'january', 'february', 'march', 'april', 'may', 'june', 'november', 'december', 'night'],
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
        "topic": "Advanced stats: what's computed vs. what doesn't exist",
        "keywords": ['advanced', 'rating', 'per', 'vorp', 'bpm', 'win', 'shares'],
        "note": (
            "player_advanced_stats / player_season_advanced_stats hold true shooting % "
            "(ts_pct), effective FG% (efg_pct), usage rate (usage_pct), and Hollinger game "
            "score (game_score / avg_game_score) - use these instead of recomputing the "
            "formulas yourself. PER, Win Shares, BPM, and VORP are "
            "not computed anywhere in this dataset - if asked for one of those, say it isn't "
            "available rather than substituting a different stat or inventing a number."
        ),
    },
]


# The agent's context budget. ollama truncates an over-long prompt head-first
# and silently: a 10,295-token preamble against NUM_CTX 8192 left only 4,098
# tokens reaching the model, and what it discarded was TABLE_SUMMARY, both
# standing rules, and the first ~15 KNOWLEDGE_BASE entries. Nothing errored.
# See FAST-PATH-MIGRATION.md.
# Measured behaviour, not a guess: a prompt UNDER num_ctx is evaluated in full
# (a ~3,700-token prompt at num_ctx 8192 came back with prompt_eval_count
# 3,696), and one OVER it is cut to roughly half (10,093 tokens at num_ctx 8192
# came back 4,098). So the whole prompt - preamble plus the conversation on top
# of it - has to stay under NUM_CTX, and the cliff is silent when it does not.
#
# Raised from 8192 now that this path only handles questions no template
# covers. A ~5,400-token preamble costs ~100s of CPU prefill on a fall-through
# question, against being quietly wrong at 8192; for a path this rare that is
# the right trade.
NUM_CTX = 16384
# The preamble's share, leaving ~10k for tool-result JSON, the model's replies,
# and several tool-call rounds. A preamble past this is a bug, not a knob.
PREAMBLE_TOKEN_BUDGET = 6000

# Rules that apply to ANY SQL the agent writes, so they are never selected
# against - they would be relevant to every question anyway.
ALWAYS_ON_TOPICS = frozenset(
    {
        "No season named in the question -> default to the CURRENT season",
        "Always call run_sql - never print SQL as your answer",
        "IDs in run_sql results",
        "Column aliases starting with a digit",
        "Filtering SQL to one named player or team",
    }
)

MAX_SELECTED_ENTRIES = 3

# Words too common in NBA questions to discriminate between entries.
_STOPWORDS = frozenset(
    """the and for with what which who whom whose how many much most least best worst top from this that
    they them their there then than have has had was were been being does did doing your you not but all
    any some more over under about into during season seasons game games player players team teams league
    show tell give list find get make plot draw are was has had did who how why out per vs the its his her
    one two set use way new old own see put run also each such only very just than then now""".split()
)


def _terms(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z_]{3,}", text.lower()) if w not in _STOPWORDS}


def select_knowledge(question: str, entries: list[dict[str, Any]] | None = None, limit: int = MAX_SELECTED_ENTRIES) -> list[dict[str, Any]]:
    """Pick the few KNOWLEDGE_BASE entries this question actually needs.

    The KB grew 2,416 -> 10,295 tokens in eight days by the reasonable-looking
    method of appending an entry whenever a real question produced a wrong
    answer. That method is self-defeating once the total stops fitting: every
    new entry makes it likelier that the entry which MATTERS is the one
    truncated away, and every question pays prompt-eval time for all of them.

    Selection is a plain keyword overlap rather than embeddings: it needs no
    model call (the point is to spend less time, not more), it is deterministic
    and testable, and a miss is cheap - a missing entry is what the agent had
    before any of them existed, while a truncated prompt loses the schema
    itself."""
    entries = KNOWLEDGE_BASE if entries is None else entries
    asked = _terms(question)
    scored: list[tuple[int, dict[str, Any]]] = []
    for entry in entries:
        if entry["topic"] in ALWAYS_ON_TOPICS:
            continue
        topic_hits = len(asked & _terms(entry["topic"] + " " + " ".join(entry.get("keywords", []))))
        body_hits = len(asked & _terms(entry["note"]))
        score = topic_hits * 3 + body_hits
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda pair: -pair[0])
    return [entry for _, entry in scored[:limit]]


def build_system_prompt(question: str) -> str:
    """The agent's system prompt, assembled per question: the always-on core
    plus only the entries this question needs."""
    always_on = [e for e in KNOWLEDGE_BASE if e["topic"] in ALWAYS_ON_TOPICS]
    prompt = SYSTEM_PROMPT_TEMPLATE.format(knowledge=format_knowledge_base(always_on + select_knowledge(question)))
    estimated = estimate_tokens(prompt) + estimate_tokens(json.dumps(TOOLS))
    if estimated > PREAMBLE_TOKEN_BUDGET:
        raise PreambleTooLarge(
            f"Assembled preamble is ~{estimated} tokens, over the {PREAMBLE_TOKEN_BUDGET} budget "
            f"(NUM_CTX={NUM_CTX}). ollama would truncate this head-first and SILENTLY, dropping the "
            "schema summary and the standing rules while leaving the tool schemas intact. Shorten "
            "TABLE_SUMMARY, trim a tool description, or lower MAX_SELECTED_ENTRIES - do not raise "
            "the budget without also raising NUM_CTX."
        )
    return prompt


def estimate_tokens(text: str) -> int:
    """Deliberately a slight over-estimate (measured ~4.08 chars/token on this
    prompt), so the budget check errs toward failing loudly rather than
    silently truncating."""
    return len(text) // 4


class PreambleTooLarge(RuntimeError):
    """Raised instead of letting ollama quietly discard most of the prompt."""


def format_knowledge_base(entries: list[dict[str, Any]]) -> str:
    blocks = []
    for e in entries:
        block = f"- {e['topic']}: {e['note']}"
        if e.get("example"):
            example_lines = "\n".join(f"    {line}" for line in e["example"].splitlines())
            block += f"\n  Example:\n{example_lines}"
        blocks.append(block)
    return "\n".join(blocks)


SYSTEM_PROMPT_TEMPLATE = f"""You are a data analyst answering natural-language questions about NBA \
statistics using a local, read-only DuckDB database. You have four tools:

- describe_table(table_name): get exact column names/types for a table. Call this before \
writing SQL against a table you have not already described in this conversation - do not \
guess column names.
- get_leaderboard(metric, season, season_type, min_sample, limit): rank players by one of a \
fixed set of known metrics ({", ".join(sorted(CORE_METRIC_NAMES))}) - PLUS every NetPoints \
"fingerprint" shot/play-type category (two_pt, two_pt_shooting, three_pt, three_pt_shooting, \
assist, bad_pass, corner, cutting, driving, fade, fast_break, floating, foul, free_throw, hook, \
layup, mid_range, putback, rebound, rim, total, turnover), each as <category>_o_net_pts / \
_d_net_pts / _t_net_pts (offense/defense/total) - e.g. rim_o_net_pts for "best at scoring at the \
rim". ALWAYS use this instead of run_sql for a "top/best/worst N players by <metric>" question \
when the metric is one of these. It already applies the current-season default, the right \
minimum-sample qualifier, and traded-player dedup - you do not need to (and should not) \
re-derive those with run_sql for a metric this tool covers.
- run_sql(query): run a read-only SELECT query and get rows back as JSON. Use this for anything \
get_leaderboard doesn't cover (a metric not in its list, a leaderboard that also needs an \
opponent/box-score join, single-player lookups, comparisons, standings, counts, distances, etc).
- render_shot_chart(player_name, season, season_type, event_id, period, shot_value, \
made_only): renders a static HTML shot chart (makes vs misses on a simplified court) for one \
player. Use this only for requests to see/plot/visualize shots.

Available tables:
{TABLE_SUMMARY}

STANDING RULE - apply this to EVERY query, not just ones about "this season": if the question \
does not name a season, add a season filter for the CURRENT season, in every query you write, \
even ones that also need other filters (a minimum games/minutes, a team, a position, etc.) - \
never leave a multi-condition query without it just because another condition is also present. \
A current_season() SQL function is already defined in the warehouse - use `season = current_season()` \
directly rather than computing it yourself. If that season has no rows yet, say so rather than \
silently answering from an older season.

STANDING RULE - if you cannot construct a query that actually answers what was asked (after a \
reasonable number of attempts), say so explicitly and describe what you tried. NEVER answer a \
different, easier question instead and present it as if it satisfies the original request - \
confirmed live, a question asking for a specific per-quarter scoring count got a completely \
unrelated season-averages summary back after a few failed attempts, with no indication the real \
question had been abandoned. Silently substituting an easier question is worse than admitting \
you couldn't answer the real one.

Known gotchas and patterns for this schema - read before writing SQL or calling a tool. Treat \
each worked example as the exact pattern to copy, not just an illustration:
{{knowledge}}

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
            "description": (
                "Run a read-only SQL SELECT query against the DuckDB warehouse and return rows as JSON. "
                "For a \"top/best/worst N players by <metric>\" question, use get_leaderboard instead if "
                "the metric is one of its known metrics - only use run_sql for that shape of question "
                "when the metric isn't covered there."
            ),
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
            "name": "get_leaderboard",
            "description": (
                "Rank players by one of a fixed set of known metrics - the correct table, join, season "
                "default, minimum-sample qualifier, and traded-player handling are all applied for you. "
                "Optionally restrict to one team, or add extra box-score columns (points/rebounds/etc.) "
                "alongside the ranked metric. ALWAYS prefer this over run_sql for a \"top/best/worst N "
                "players by <metric>\" question when the metric is one of: "
                + ", ".join(sorted(CORE_METRIC_NAMES))
                + " - or a NetPoints \"fingerprint\" shot/play-type category (2pt, 3pt, driving, "
                "fastbreak, rebound, turnover, rim, etc. - see the metric enum for the full list), each "
                "as <category>_o_net_pts / _d_net_pts / _t_net_pts for offense/defense/total."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "metric": {
                        # Deliberately not an enum of all ~80 names: spelled out, that
                        # single field was ~700 tokens on EVERY agent call. A wrong name
                        # comes back with a close-match suggestion and the full list (see
                        # leaderboard.run_leaderboard), so the model recovers in one turn
                        # and pays for the list only when it actually needs it.
                        "type": "string",
                        "description": (
                            "Which metric to rank by. Core: "
                            + ", ".join(sorted(CORE_METRIC_NAMES))
                            + ". Also any NetPoints play-type category as <category>_o_net_pts / _d_net_pts / "
                            "_t_net_pts, where <category> is one of two_pt, three_pt, assist, bad_pass, corner, "
                            "cutting, driving, fade, fast_break, floating, foul, free_throw, hook, layup, "
                            "mid_range, putback, rebound, rim, total, turnover. Call with a best guess if "
                            "unsure - a wrong name returns the full list of valid ones."
                        ),
                    },
                    "season": {
                        "type": "integer",
                        "description": (
                            "ESPN season year (season-ending year), e.g. 2024 for the 2023-24 season. "
                            "Omit if the question doesn't name a season - defaults to the CURRENT season."
                        ),
                    },
                    "season_type": {
                        "type": "integer",
                        "description": "1=preseason, 2=regular season (default), 3=postseason.",
                    },
                    "min_sample": {
                        "type": "integer",
                        "description": (
                            "Minimum games/minutes (depends on the metric) to qualify. Omit to use a "
                            "sensible built-in default - only set this if the user's question gives its "
                            "own minimum."
                        ),
                    },
                    "team": {
                        "type": "string",
                        "description": "Restrict to one team (name or abbreviation, e.g. 'Lakers' or 'LAL'). Omit for all teams.",
                    },
                    "fields": {
                        "type": "array",
                        "items": {"type": "string", "enum": sorted(EXTRA_FIELD_COLUMNS)},
                        "description": "Extra per-game box-score columns to include alongside the ranked metric. Omit if not asked for.",
                    },
                    "limit": {"type": "integer", "description": "How many players to return. Defaults to 10."},
                },
                "required": ["metric"],
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
