"""URL builders for ESPN's NBA stats endpoints (confirmed live via manual probing)."""

SITE_V2 = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
SITE_LEAGUE_V2 = "https://site.api.espn.com/apis/v2/sports/basketball/nba"
WEB_V3 = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba"
CORE_V2 = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"


def teams_url() -> str:
    """Every NBA team, with ids and abbreviations."""
    return f"{SITE_V2}/teams"


def team_schedule_url(team_id: str) -> str:
    """One team's full schedule, the source of event ids for a season."""
    return f"{SITE_V2}/teams/{team_id}/schedule"


def summary_url() -> str:
    """One game's full summary: box score, and play-by-play when requested."""
    return f"{SITE_V2}/summary"


def standings_url() -> str:
    """League standings: wins, losses, streak, seed."""
    return f"{SITE_LEAGUE_V2}/standings"


def player_career_stats_url(athlete_id: str) -> str:
    """One player's season-by-season career totals and averages."""
    return f"{WEB_V3}/athletes/{athlete_id}/stats"


def team_season_stats_url(season: int, season_type: int, team_id: str) -> str:
    """One team's season aggregate: 100+ advanced team statistics."""
    return f"{CORE_V2}/seasons/{season}/types/{season_type}/teams/{team_id}/statistics"


def power_index_url(season: int) -> str:
    """ESPN's Basketball Power Index (BPI) for a season."""
    return f"{CORE_V2}/seasons/{season}/powerindex"


# NetPoints: ESPN Analytics' advanced player/team metric (successor to Real
# Plus-Minus), published as static JSON on S3 - not an espn.com API, but public,
# unauthenticated, and unrestricted (confirmed live: no robots.txt disallow, no
# CORS/auth barrier, plain curl succeeds - no TLS impersonation needed here).
NET_POINTS_PLAYER_URL = "https://nfl-player-metrics.s3.amazonaws.com/net-pts/nba_net_pts_data.json"
NET_POINTS_PLAYER_100_URL = "https://nfl-player-metrics.s3.amazonaws.com/net-pts/nba_net_pts100_data.json"
NET_POINTS_TEAM_URL = "https://nfl-player-metrics.s3.amazonaws.com/net-pts/team_nba.json"
# One file per season (NetPoints' own start-year label), same public bucket as
# above - backs espnanalytics.com's "Net Pts Fingerprint" page. Confirmed live:
# a season with no file yet (e.g. one that hasn't started) returns 403, not
# 404 - unlike every other espn.com endpoint this project talks to.
NET_POINTS_FINGERPRINT_URL_TEMPLATE = "https://nfl-player-metrics.s3.amazonaws.com/net-pts/fingerprint-files/nbafingerprint_{start_year}.json"


def net_points_player_url() -> str:
    """NetPoints season totals for every player."""
    return NET_POINTS_PLAYER_URL


def net_points_player_100_url() -> str:
    """NetPoints per-100-possession rates for every player."""
    return NET_POINTS_PLAYER_100_URL


def net_points_team_url() -> str:
    """NetPoints for every team, split offense/defense/total."""
    return NET_POINTS_TEAM_URL


def net_points_fingerprint_url(start_year: int) -> str:
    """The play-type "fingerprint" breakdown for one season.

    Keyed by NetPoints' own START-year label, not this project's season-ending
    convention: the 2025-26 season is ``start_year=2025``.
    """
    return NET_POINTS_FINGERPRINT_URL_TEMPLATE.format(start_year=start_year)
