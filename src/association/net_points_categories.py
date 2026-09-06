"""Shared list of NetPoints "fingerprint" shot/play-type categories - both the
fetch pipeline (parse.py, building the 66 column names it writes) and the
query engine (metrics.py, building the matching get_leaderboard entries) need
the exact same source-category -> our-column-prefix mapping, so it lives here
once rather than being duplicated (and risking drift) in both places."""

from __future__ import annotations

# Source category name (as espnanalytics.com's fingerprint file names it) ->
# our column prefix. Each gets an _o_net_pts (offense) / _d_net_pts (defense)
# / _t_net_pts (total) column - see fetch/parse.py's parse_net_points_fingerprint
# and query/metrics.py's LEADERBOARD_METRICS for where this drives that.
FINGERPRINT_CATEGORIES: dict[str, str] = {
    "2pt": "two_pt",
    "2ptShooting": "two_pt_shooting",
    "3pt": "three_pt",
    "3ptShooting": "three_pt_shooting",
    "assist": "assist",
    "badpass": "bad_pass",
    "corner": "corner",
    "cutting": "cutting",
    "driving": "driving",
    "fade": "fade",
    "fastbreak": "fast_break",
    "floating": "floating",
    "foul": "foul",
    "freethrow": "free_throw",
    "hook": "hook",
    "layup": "layup",
    "mid": "mid_range",
    "putback": "putback",
    "rebound": "rebound",
    "rim": "rim",
    "total": "total",
    "turnover": "turnover",
}

FINGERPRINT_SIDE_LABELS: dict[str, str] = {"o": "offense", "d": "defense", "t": "total"}
