"""Parse raw ESPN JSON payloads into flat row dicts, one function per source shape.

Stat keys/names from ESPN follow a "madeThing-attemptedThing" convention for
compound stats (e.g. "fieldGoalsMade-fieldGoalsAttempted" / "44-88"). We split
those generically by splitting both the key and the value on "-", rather than
hardcoding a stat list, so the parser tracks whatever ESPN exposes.
"""

from __future__ import annotations

import re
from typing import Any

TEAM_REF_RE = re.compile(r"/teams/(\d+)")


def _num(value):
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


def _assign_stat(row: dict, name: str, value) -> None:
    if not name:
        return
    if "-" in name:
        n1, _, n2 = name.partition("-")
        v1, _, v2 = str(value).partition("-") if value is not None else ("", "", "")
        row[n1] = _num(v1)
        row[n2] = _num(v2)
    else:
        row[name] = _num(value)


def _glossary_rows(names, labels, descriptions, source):
    labels = labels or []
    descriptions = descriptions or []
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


def parse_teams(data) -> list[dict]:
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


def parse_schedule_event_ids(data) -> list[str]:
    if not data:
        return []
    return [e.get("id") for e in data.get("events") or [] if e.get("id")]


def parse_game_summary(data, season: int, season_type: int) -> dict[str, Any]:
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

    def linescore_str(c):
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


def parse_standings(data, season: int) -> tuple[list[dict], list[dict]]:
    seen: dict[str, dict] = {}
    glossary: list[dict] = []

    def walk(node):
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


def parse_player_career_stats(data, athlete_id: str, season_type: int) -> tuple[list[dict], list[dict]]:
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


def parse_team_season_stats(data, season: int, season_type: int, team_id: str):
    if not data:
        return None, []
    row = {"season": season, "season_type": season_type, "team_id": team_id}
    glossary = []
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


def parse_power_index(data) -> tuple[list[dict], list[dict]]:
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
