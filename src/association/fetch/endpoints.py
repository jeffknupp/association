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


def scoreboard_url() -> str:
    """Every game ESPN lists on one date, as ``?dates=YYYYMMDD``.

    A second source of event ids, and the only one that has the 2000 and 2001
    playoff games missing from every team's schedule. ``event_ids_for`` gathers
    ids from :func:`team_schedule_url`, and ESPN's schedules simply stop: the
    2000 postseason ends on 2000-06-01, losing the whole LAL-IND Final, and
    2001's ends on 2001-05-28. Probed live, the scoreboard answers those dates
    with the real games - ``200607013`` is Finals Game 1 - and their summaries
    carry full box scores.

    Each event names its own season and season type (``{"season": {"year":
    2000, "type": 3}}``), which is what a caller must file it under. The
    response's ``leagues[].season`` block does NOT: it reads type 2 even for a
    June playoff date, so reading the league's season instead of the event's
    files a Finals game as a regular-season one.

    .. versionadded:: 2.2.0
    """
    return f"{SITE_V2}/scoreboard"


def standings_url() -> str:
    """League standings: wins, losses, streak, seed."""
    return f"{SITE_LEAGUE_V2}/standings"


def player_career_stats_url(athlete_id: str) -> str:
    """One player's season-by-season career totals and averages."""
    return f"{WEB_V3}/athletes/{athlete_id}/stats"


def player_season_totals_url(season: int, season_type: int, athlete_id: str) -> str:
    """One player's aggregate for a single season - the totals the career
    endpoint above sometimes omits.

    Keyed by (season, season type, athlete) and carrying **no team dimension**,
    so a player traded mid-season gets his COMBINED figure back against every
    one of his stint rows: all three of David Wood's 1995-96 stints (21, 4 and
    37 games) answer 208 points. Anything filled from here therefore has to be
    matched on games played first - see
    :func:`~association.fetch.parse.fill_missing_season_totals`.

    .. versionadded:: 2.2.0
    """
    return f"{CORE_V2}/seasons/{season}/types/{season_type}/athletes/{athlete_id}/statistics"


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
