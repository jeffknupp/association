"""URL builders for ESPN's NBA stats endpoints (confirmed live via manual probing)."""

SITE_V2 = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
SITE_LEAGUE_V2 = "https://site.api.espn.com/apis/v2/sports/basketball/nba"
WEB_V3 = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba"
CORE_V2 = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"


def teams_url() -> str:
    return f"{SITE_V2}/teams"


def team_schedule_url(team_id: str) -> str:
    return f"{SITE_V2}/teams/{team_id}/schedule"


def summary_url() -> str:
    return f"{SITE_V2}/summary"


def standings_url() -> str:
    return f"{SITE_LEAGUE_V2}/standings"


def player_career_stats_url(athlete_id: str) -> str:
    return f"{WEB_V3}/athletes/{athlete_id}/stats"


def team_season_stats_url(season: int, season_type: int, team_id: str) -> str:
    return f"{CORE_V2}/seasons/{season}/types/{season_type}/teams/{team_id}/statistics"


def power_index_url(season: int) -> str:
    return f"{CORE_V2}/seasons/{season}/powerindex"
