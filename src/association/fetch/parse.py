"""Parse raw ESPN JSON payloads into flat row dicts, one function per source shape.

Stat keys/names from ESPN follow a "madeThing-attemptedThing" convention for
compound stats (e.g. "fieldGoalsMade-fieldGoalsAttempted" / "44-88"). We split
those generically by splitting both the key and the value on "-", rather than
hardcoding a stat list, so the parser tracks whatever ESPN exposes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import date as _date
from datetime import timedelta as _timedelta
from typing import Any

from association.net_points_categories import FINGERPRINT_CATEGORIES

TEAM_REF_RE = re.compile(r"/teams/(\d+)")

# Raw ESPN response payload (or a nested dict within one) - a plain dict, since
# these are decoded straight from JSON and we index into them dynamically.
JSON = dict[str, Any]


def _num(value: Any) -> int | float | str | None:
    if value is None:
        return None
    s = str(value).strip()
    if s in ("", "--", "-", "N/A"):
        return None
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return s


def _assign_stat(row: dict[str, Any], name: str | None, value: Any) -> None:
    if not name:
        return
    if "-" in name:
        n1, _, n2 = name.partition("-")
        v1, _, v2 = str(value).partition("-") if value is not None else ("", "", "")
        row[n1] = _num(v1)
        row[n2] = _num(v2)
    else:
        row[name] = _num(value)


def _glossary_rows(
    names: Iterable[str] | None,
    labels: Iterable[str | None] | None,
    descriptions: Iterable[str | None] | None,
    source: str,
) -> list[dict]:
    labels = list(labels or [])
    descriptions = list(descriptions or [])
    rows = []
    for i, name in enumerate(names or []):
        rows.append(
            {
                "stat_key": name,
                "label": labels[i] if i < len(labels) else None,
                "description": descriptions[i] if i < len(descriptions) else None,
                "source": source,
            }
        )
    return rows


def parse_teams(data: JSON | None) -> list[dict]:
    """Flatten the teams response into one row per team."""
    rows: list[dict] = []
    if not data:
        return rows
    for sport in data.get("sports") or []:
        for league in sport.get("leagues") or []:
            for t in league.get("teams") or []:
                team = t.get("team") or {}
                logos = team.get("logos") or []
                rows.append(
                    {
                        "team_id": team.get("id"),
                        "uid": team.get("uid"),
                        "abbreviation": team.get("abbreviation"),
                        "location": team.get("location"),
                        "name": team.get("name"),
                        "nickname": team.get("nickname"),
                        "display_name": team.get("displayName"),
                        "short_display_name": team.get("shortDisplayName"),
                        "slug": team.get("slug"),
                        "color": team.get("color"),
                        "alternate_color": team.get("alternateColor"),
                        "logo_url": logos[0].get("href") if logos else None,
                        "is_active": team.get("isActive"),
                        "is_all_star": team.get("isAllStar"),
                    }
                )
    return rows


def parse_schedule_event_ids(data: JSON | None) -> list[str]:
    """Game ids from a team schedule, filtered to one season and season type."""
    if not data:
        return []
    return [e.get("id") for e in data.get("events") or [] if e.get("id")]


def parse_game_summary(data: JSON | None, season: int, season_type: int) -> dict[str, Any]:
    """Returns dict with: game (dict|None), player_box, team_box, plays,
    shot_chart, win_probability (lists of dict), players_seen (athlete_id -> bio dict),
    glossary (list of dict)."""
    result: dict[str, Any] = {
        "game": None,
        "player_box": [],
        "team_box": [],
        "plays": [],
        "shot_chart": [],
        "win_probability": [],
        "players_seen": {},
        "glossary": [],
    }
    if not data:
        return result

    header = data.get("header") or {}
    event_id = header.get("id")
    comp = (header.get("competitions") or [{}])[0]
    competitors = comp.get("competitors") or []
    game_info = data.get("gameInfo") or {}
    venue = game_info.get("venue") or {}
    status = (comp.get("status") or {}).get("type") or {}

    home: dict[str, Any] = next((c for c in competitors if c.get("homeAway") == "home"), {})
    away: dict[str, Any] = next((c for c in competitors if c.get("homeAway") == "away"), {})
    home_team_id = (home.get("team") or {}).get("id")
    away_team_id = (away.get("team") or {}).get("id")
    opponent_of = {home_team_id: away_team_id, away_team_id: home_team_id}

    def linescore_str(c: dict[str, Any]) -> str:
        """Period-by-period scores as a compact string, since the row is otherwise
        one column per period of a variable-length game."""
        return ",".join(str(x.get("displayValue", "")) for x in (c.get("linescores") or []))

    winner_team_id = None
    if home.get("winner"):
        winner_team_id = home_team_id
    elif away.get("winner"):
        winner_team_id = away_team_id

    result["game"] = {
        "event_id": event_id,
        "season": season,
        "season_type": season_type,
        "date": comp.get("date"),
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "home_score": _num(home.get("score")),
        "away_score": _num(away.get("score")),
        "winner_team_id": winner_team_id,
        "home_linescores": linescore_str(home),
        "away_linescores": linescore_str(away),
        "neutral_site": comp.get("neutralSite"),
        "conference_game": comp.get("conferenceCompetition"),
        "venue_id": venue.get("id"),
        "venue_name": venue.get("fullName"),
        "venue_city": (venue.get("address") or {}).get("city"),
        "venue_state": (venue.get("address") or {}).get("state"),
        "attendance": _num(game_info.get("attendance")),
        "status": status.get("name"),
        "status_completed": bool(status.get("completed")),
        "status_state": status.get("state"),  # 'pre' | 'in' | 'post' - 'post' + not completed = postponed/cancelled, not pending
    }

    boxscore = data.get("boxscore") or {}

    for t in boxscore.get("teams") or []:
        team_id = (t.get("team") or {}).get("id")
        row = {
            "event_id": event_id,
            "season": season,
            "season_type": season_type,
            "team_id": team_id,
            "opponent_team_id": opponent_of.get(team_id),
            "home_away": t.get("homeAway"),
        }
        for stat in t.get("statistics") or []:
            _assign_stat(row, stat.get("name"), stat.get("displayValue"))
        result["team_box"].append(row)

    for team_block in boxscore.get("players") or []:
        team_id = (team_block.get("team") or {}).get("id")
        stat_groups = team_block.get("statistics") or []
        if not stat_groups:
            continue
        sg = stat_groups[0]
        keys = sg.get("keys") or sg.get("names") or []
        result["glossary"].extend(
            _glossary_rows(keys, sg.get("labels"), sg.get("descriptions"), "player_box_stats")
        )
        for ath in sg.get("athletes") or []:
            athlete = ath.get("athlete") or {}
            athlete_id = athlete.get("id")
            if athlete_id is None:
                continue
            row = {
                "event_id": event_id,
                "season": season,
                "season_type": season_type,
                "team_id": team_id,
                "opponent_team_id": opponent_of.get(team_id),
                "athlete_id": athlete_id,
                "starter": ath.get("starter", False),
                "did_not_play": ath.get("didNotPlay", False),
                "dnp_reason": ath.get("reason"),
                "ejected": ath.get("ejected", False),
            }
            # stats can be shorter than keys (e.g. empty for a DNP player) - truncate, don't error
            for k, v in zip(keys, ath.get("stats") or [], strict=False):
                _assign_stat(row, k, v)
            result["player_box"].append(row)

            pos = athlete.get("position") or {}
            result["players_seen"][athlete_id] = {
                "athlete_id": athlete_id,
                "display_name": athlete.get("displayName"),
                "short_name": athlete.get("shortName"),
                "jersey": athlete.get("jersey"),
                "position_abbr": pos.get("abbreviation"),
                "position_name": pos.get("name"),
                "headshot_url": (athlete.get("headshot") or {}).get("href"),
            }

    for p in data.get("plays") or []:
        play_id = p.get("id")
        period = (p.get("period") or {}).get("number")
        clock = (p.get("clock") or {}).get("displayValue")
        team_id = (p.get("team") or {}).get("id")
        participants = p.get("participants") or []
        athlete_ids = [
            str(pp["athlete"]["id"]) for pp in participants if pp.get("athlete", {}).get("id")
        ]
        primary_athlete_id = athlete_ids[0] if athlete_ids else None

        result["plays"].append(
            {
                "event_id": event_id,
                "play_id": play_id,
                "season": season,
                "season_type": season_type,
                "period": period,
                "clock": clock,
                "team_id": team_id,
                "athlete_id": primary_athlete_id,
                "participant_athlete_ids": ",".join(athlete_ids) or None,
                "type": (p.get("type") or {}).get("text"),
                "text": p.get("text"),
                "home_score": p.get("homeScore"),
                "away_score": p.get("awayScore"),
                "scoring_play": p.get("scoringPlay"),
            }
        )

        if p.get("shootingPlay"):
            coord = p.get("coordinate") or {}
            # Free throws (and occasionally other plays) carry an ESPN sentinel
            # "no real coordinate" value instead of a court position; null it out
            # rather than passing along garbage magnitudes.
            cx, cy = coord.get("x"), coord.get("y")
            if cx is None or cy is None or abs(cx) > 500 or abs(cy) > 500:
                cx, cy = None, None
            result["shot_chart"].append(
                {
                    "event_id": event_id,
                    "play_id": play_id,
                    "season": season,
                    "season_type": season_type,
                    "athlete_id": primary_athlete_id,
                    "participant_athlete_ids": ",".join(athlete_ids) or None,
                    "team_id": team_id,
                    "period": period,
                    "clock": clock,
                    "made": bool(p.get("scoringPlay")),
                    "shot_type": (p.get("type") or {}).get("text"),
                    "points_attempted": p.get("pointsAttempted"),
                    "coordinate_x": cx,
                    "coordinate_y": cy,
                    "description": p.get("text"),
                }
            )

    for wp in data.get("winprobability") or []:
        result["win_probability"].append(
            {
                "event_id": event_id,
                "play_id": wp.get("playId"),
                "season": season,
                "season_type": season_type,
                "home_win_pct": wp.get("homeWinPercentage"),
                "tie_pct": wp.get("tiePercentage"),
            }
        )

    return result


def parse_standings(data: JSON | None, season: int) -> tuple[list[dict], list[dict]]:
    """One row per team: wins, losses, streak, seed and the rest of the standings."""
    seen: dict[str, dict] = {}
    glossary: list[dict] = []

    def walk(node: JSON | None) -> None:
        """Recurse ESPN's nested stat groups, flattening leaves into ``out``."""
        standings = (node or {}).get("standings") or {}
        for entry in standings.get("entries") or []:
            team = entry.get("team") or {}
            team_id = team.get("id")
            if team_id is None or team_id in seen:
                continue
            row = {"season": season, "team_id": team_id}
            names, labels, descs = [], [], []
            for stat in entry.get("stats") or []:
                name = stat.get("name")
                if not name:
                    continue
                row[name] = _num(stat.get("value", stat.get("displayValue")))
                names.append(name)
                labels.append(stat.get("displayName"))
                descs.append(stat.get("description"))
            if names:
                glossary.extend(_glossary_rows(names, labels, descs, "standings"))
            seen[team_id] = row
        for child in (node or {}).get("children") or []:
            walk(child)

    if data:
        walk(data)
    return list(seen.values()), glossary


def parse_player_career_stats(data: JSON | None, athlete_id: str, season_type: int) -> tuple[list[dict], list[dict]]:
    """A player's season-by-season totals and averages, one row per season and
    season type."""
    rows: dict[tuple, dict] = {}
    glossary: list[dict] = []
    if not data:
        return [], []
    for cat in data.get("categories") or []:
        names = cat.get("names") or []
        glossary.extend(
            _glossary_rows(names, cat.get("displayNames"), cat.get("descriptions"), "player_season_stats")
        )
        for entry in cat.get("statistics") or []:
            season_year = (entry.get("season") or {}).get("year")
            team_id = entry.get("teamId")
            key = (season_year, team_id)
            row = rows.setdefault(
                key,
                {
                    "athlete_id": athlete_id,
                    "season": season_year,
                    "season_type": season_type,
                    "team_id": team_id,
                    "position": entry.get("position"),
                },
            )
            for name, val in zip(names, entry.get("stats") or [], strict=False):
                _assign_stat(row, name, val)
    return list(rows.values()), glossary


def parse_team_season_stats(
    data: JSON | None, season: int, season_type: int, team_id: str
) -> tuple[dict[str, Any] | None, list[dict]]:
    """A team's season aggregate, flattened from ESPN's nested category/stat
    structure into a single wide row."""
    if not data:
        return None, []
    row: dict[str, Any] = {"season": season, "season_type": season_type, "team_id": team_id}
    glossary: list[dict] = []
    splits = data.get("splits") or {}
    for cat in splits.get("categories") or []:
        names, labels, descs = [], [], []
        for stat in cat.get("stats") or []:
            name = stat.get("name")
            if not name:
                continue
            row[name] = _num(stat.get("value", stat.get("displayValue")))
            names.append(name)
            labels.append(stat.get("displayName"))
            descs.append(stat.get("description"))
        if names:
            glossary.extend(_glossary_rows(names, labels, descs, "team_season_stats"))
    return row, glossary


def parse_power_index(data: JSON | None) -> tuple[list[dict], list[dict]]:
    """ESPN's Basketball Power Index for one season, one row per team."""
    rows: list[dict] = []
    glossary: list[dict] = []
    if not data:
        return rows, glossary
    for item in data.get("items") or []:
        team_ref = (item.get("team") or {}).get("$ref", "")
        m = TEAM_REF_RE.search(team_ref)
        team_id = m.group(1) if m else None
        row = {
            "season": item.get("season"),
            "season_type": item.get("seasonType"),
            "team_id": team_id,
            "last_updated": item.get("lastUpdated"),
        }
        names, labels, descs = [], [], []
        for stat in item.get("stats") or []:
            name = stat.get("name")
            if not name:
                continue
            row[name] = _num(stat.get("value", stat.get("displayValue")))
            names.append(name)
            labels.append(stat.get("displayName"))
            descs.append(stat.get("description"))
        if names:
            glossary.extend(_glossary_rows(names, labels, descs, "team_power_index"))
        rows.append(row)
    return rows, glossary


# NetPoints (espnanalytics.com) uses its own short team codes that differ from
# ESPN's own api.espn.com abbreviations (confirmed live by cross-checking every
# team) - translate before resolving to team_id. The player file and the team
# file don't even agree with each other for San Antonio ("SAN" vs "SAS"), so
# this covers both variants seen across both files, not just one.
NET_POINTS_ABBREV_TO_ESPN = {
    "BRK": "BKN",
    "GSW": "GS",
    "NOR": "NO",
    "NYK": "NY",
    "PHO": "PHX",
    "SAN": "SA",
    "SAS": "SA",
    "UTA": "UTAH",
    "WAS": "WSH",
}


def _net_points_team_id(abbrev: str | None, team_abbr_to_id: dict[str, str]) -> str | None:
    if not abbrev:
        return None
    return team_abbr_to_id.get(NET_POINTS_ABBREV_TO_ESPN.get(abbrev, abbrev))


def parse_net_points_player(
    data: list[JSON] | None,
    team_abbr_to_id: dict[str, str],
    rate_data: list[JSON] | None = None,
) -> list[dict]:
    """NetPoints publishes one flat JSON array covering every historical
    season in one request - no per-season fetch exists, so this parses the
    whole thing every pull; the pipeline only writes out season+type files
    that aren't already on disk.

    NetPoints labels a season by the year it STARTS (e.g. 2025 = the 2025-26
    season) - ESPN's convention used everywhere else in this project is the
    year a season ENDS, so `season` here is stored as netpoints_season + 1
    to stay directly comparable/joinable with every other table's `season`
    column (confirmed live: a player's netpoints_season=2025 row has the same
    games-played count as this project's own season=2026 data for them).

    `rate_data` is a second, separate flat file (nba_net_pts100_data.json) on
    the same public bucket - ESPN Analytics' own site fetches it only when its
    "Net Points / 100 Poss" toggle is selected (confirmed live), rather than
    computing the rate client-side. It carries the same identity fields plus
    tNet100/oNet100/dNet100 (real per-100-possession values, not our own
    approximation) and totMin - joined in here by (dot_com_id, min_season,
    seasonType), confirmed live to be a unique key in both files. A handful of
    degenerate stints (e.g. a single scoreless playoff game) are dropped from
    this file but not the main one - left NULL here rather than guessed."""
    rate_by_key: dict[tuple[Any, Any, Any], JSON] = {}
    for item in rate_data or []:
        key = (item.get("dot_com_id"), item.get("min_season"), item.get("seasonType"))
        rate_by_key[key] = item

    rows: list[dict] = []
    for item in data or []:
        key = (item.get("dot_com_id"), item.get("min_season"), item.get("seasonType"))
        rate = rate_by_key.get(key)
        rows.append(
            {
                "athlete_id": item.get("dot_com_id"),
                "season": (item.get("min_season") or 0) + 1,
                "net_points_season_type": item.get("seasonType"),
                "team_id": _net_points_team_id(item.get("tm"), team_abbr_to_id),
                "position": item.get("position"),
                "draft_year": item.get("draftYear"),
                "games": item.get("net_pts_games"),
                "overall": item.get("overall"),
                "offense": item.get("offense"),
                "defense": item.get("defense"),
                "overall_per_100_poss": rate.get("tNet100") if rate else None,
                "offense_per_100_poss": rate.get("oNet100") if rate else None,
                "defense_per_100_poss": rate.get("dNet100") if rate else None,
                "total_minutes": rate.get("totMin") if rate else None,
            }
        )
    return rows


def parse_net_points_team(data: JSON | None, team_abbr_to_id: dict[str, str]) -> list[dict]:
    """team_nba.json nests each stat block as a JSON string in pandas'
    orient="columns" shape ({"col": {"0": v, "1": v, ...}, ...}) rather than a
    plain list of row dicts - transpose it before use. Confirmed live: this
    file only ever reflects the single current season (no historical team-
    level data), unlike the player file above."""
    if not data:
        return []
    team4f = data.get("team4f")
    if not team4f:
        return []
    block = json.loads(team4f) if isinstance(team4f, str) else team4f
    columns = list(block.keys())
    if not columns:
        return []
    indices = sorted(block[columns[0]].keys(), key=int)
    rows: list[dict] = []
    for i in indices:
        raw = {col: block[col].get(i) for col in columns}
        rows.append(
            {
                "team_id": _net_points_team_id(raw.get("teamId"), team_abbr_to_id),
                "season": int(raw["season"]) + 1 if raw.get("season") is not None else None,
                "side": raw.get("Side"),
                "avg_team_score": raw.get("team_score"),
                "fast_break": raw.get("FB"),
                "fg2": raw.get("FG2"),
                "fg3": raw.get("FG3"),
                "free_throw": raw.get("FT"),
                "putback": raw.get("Putback"),
                "rebound": raw.get("REB"),
                "turnover": raw.get("TOV"),
                "total": raw.get("Total"),
            }
        )
    return rows


def _resolve_net_points_game(
    team_id: str | None, date: str, team_date_to_game: dict[tuple[str, str], tuple[str, int, int]]
) -> tuple[str, int, int] | None:
    """NetPoints' per-date files have no ESPN event_id anywhere, but a team
    plays at most one game on a given real-world date - so (team_id, date)
    against this project's OWN already-fetched games table resolves it
    deterministically, without needing NBA.com's gmID/gameId at all.

    The one wrinkle (confirmed live): ESPN's games.date is a UTC timestamp,
    and NetPoints' date-keyed files use the US-local date - for an evening
    game these disagree by one day (a real example: an OKC @ NYK game ESPN
    stores as "2026-03-05T00:00Z" is filed by NetPoints under "2026-03-04").
    UTC is always ahead of US local time, never behind, so the ESPN date is
    almost always `date + 1 day` - checked FIRST, not as a fallback. Trying
    exact `date` first is a real, confirmed bug: a team playing the same
    opponent on back-to-back nights (e.g. New Orleans @ LA Clippers on both
    2026-03-19 and -20 ESPN-dates) has its OWN unrelated game sitting at the
    exact NetPoints-label date, which would silently steal the match before
    the +1 case - the offset case - ever got a chance to run.
    """
    if team_id is None:
        return None
    next_day = (_date.fromisoformat(date) + _timedelta(days=1)).isoformat()
    game = team_date_to_game.get((team_id, next_day))
    if game is not None:
        return game
    return team_date_to_game.get((team_id, date))


def parse_net_points_daily(
    data: JSON | None,
    date: str,
    team_abbr_to_id: dict[str, str],
    team_date_to_game: dict[tuple[str, str], tuple[str, int, int]],
    name_to_athlete_id: dict[str, str],
) -> tuple[list[dict], list[dict]]:
    """One date's file covers every game played that date, as two blocks:
    player_box (one row per player per game) and team_box (one row per team
    per game) - both mix real box-score stats (already fetched from ESPN,
    skipped here to avoid a second, possibly-disagreeing source of truth for
    the same facts) with NetPoints-only fields (kept). Note the inconsistent
    field naming ESPN Analytics itself uses across blocks in this same
    payload - team id is "teamId" in player_box but "tmID" in team_box, and
    game id is "gmId" here vs "gmID" in the sibling _player.json file (not
    used by this parser) - neither is used anyway since event_id is resolved
    via team+date against this project's own games table instead.

    Rows that can't be resolved to a known local game (team unmapped, or no
    matching game within the date/date+1 window) are dropped rather than
    written with a null event_id. Player rows are matched to athlete_id by
    exact displayName - ambiguous or unmatched names are left with
    athlete_id=None rather than guessed."""
    player_rows: list[dict] = []
    team_rows: list[dict] = []
    if not data:
        return player_rows, team_rows

    for raw in data.get("player_box") or []:
        team_id = _net_points_team_id(raw.get("tmName"), team_abbr_to_id)
        game = _resolve_net_points_game(team_id, date, team_date_to_game)
        if game is None:
            continue
        event_id, season, season_type = game
        player_rows.append(
            {
                "event_id": event_id,
                "season": season,
                "season_type": season_type,
                "team_id": team_id,
                "athlete_id": name_to_athlete_id.get(raw.get("displayName")),
                "o_net_pts": raw.get("oNetPts"),
                "d_net_pts": raw.get("dNetPts"),
                "t_net_pts": raw.get("tNetPts"),
                "o_usage": raw.get("oUsg"),
                "d_usage": raw.get("dUsg"),
                "o_poss": raw.get("oPoss"),
                "d_poss": raw.get("dPoss"),
                "t_poss": raw.get("tPoss"),
                "o_wpa": raw.get("oWPA"),
                "d_wpa": raw.get("dWPA"),
                "t_wpa": raw.get("tWPA"),
            }
        )

    for raw in data.get("team_box") or []:
        team_id = _net_points_team_id(raw.get("tmName"), team_abbr_to_id)
        game = _resolve_net_points_game(team_id, date, team_date_to_game)
        if game is None:
            continue
        event_id, season, season_type = game
        team_rows.append(
            {
                "event_id": event_id,
                "season": season,
                "season_type": season_type,
                "team_id": team_id,
                "net_pts_2pt": raw.get("netPts2s"),
                "net_pts_3pt": raw.get("netPts3s"),
                "net_pts_shooting": raw.get("netPtsShooting"),
                "net_pts_turnover": raw.get("netPtsTurnover"),
                "net_pts_rebound": raw.get("netPtsRebound"),
                "net_pts_freethrow": raw.get("netPtsFreethrow"),
                "tot_poss": raw.get("totPoss"),
                "opp_poss": raw.get("oppPoss"),
            }
        )

    return player_rows, team_rows


def parse_net_points_fingerprint(
    data: JSON | None,
    team_abbr_to_id: dict[str, str],
    name_to_athlete_id: dict[str, str],
) -> list[dict]:
    """One file per season (NetPoints' own start-year label, converted to this
    project's season-ends convention same as parse_net_points_player), keyed
    by NBA.com's own player id with no ESPN crosswalk provided - resolved
    instead by exact display-name match against `players`, same fail-safe
    pattern as the per-game NetPoints data (ambiguous/unmatched names are
    dropped, not guessed). Bio fields the source also carries (height,
    draftYear, dob) are NOT kept - real, already-sourced-from-ESPN data on
    `players`, not something to duplicate from a second, possibly-disagreeing
    source."""
    rows: list[dict] = []
    for raw in (data or {}).values():
        athlete_id = name_to_athlete_id.get(raw.get("displayName"))
        if athlete_id is None:
            continue
        row: dict[str, Any] = {
            "athlete_id": athlete_id,
            "season": (raw.get("season") or 0) + 1,
            "team_id": _net_points_team_id(raw.get("deanAbbrev"), team_abbr_to_id),
            "games": raw.get("games_played"),
            "minutes": raw.get("minutes_played"),
            "total_poss": raw.get("tPoss"),
            "average_position": raw.get("average_position"),
            "usage": raw.get("offensive_usage"),
            "assisted_rate": raw.get("assisted_rate"),
        }
        for src_category, our_prefix in FINGERPRINT_CATEGORIES.items():
            for side in ("o", "d", "t"):
                row[f"{our_prefix}_{side}_net_pts"] = raw.get(f"{src_category}_{side}NetPts")
        rows.append(row)
    return rows
