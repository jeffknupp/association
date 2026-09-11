Data sources
============

.. warning::

   Everything below is an **undocumented** endpoint. None of it is a published,
   supported API, and none of it carries a stability guarantee. Field names,
   nesting, and availability can change without notice or deprecation, and a
   change will usually surface here as a parse error or a silently missing
   column rather than an HTTP failure. Treat a warehouse built from these
   sources as a snapshot of what the endpoints returned on the day it was
   built.

What is used
------------

Two families of endpoint, both **publicly readable without authentication** —
no API key, no account, no OAuth. They are the same endpoints that serve
ESPN's own public pages.

ESPN site and core APIs
    Scoreboards, box scores, team and player metadata, standings, play-by-play,
    and the shot chart / win-probability data derived from it. See
    :mod:`association.fetch.endpoints` for the exact URLs and
    :mod:`association.fetch.parse` for how each response is flattened.

ESPN Analytics NetPoints
    The NetPoints player and team ratings behind espnanalytics.com. The season
    ratings and the season play-type "fingerprint" are public flat files,
    included in every pull with no credentials at all (see
    :mod:`association.fetch.endpoints`). The per-game files — each game's
    ratings and its play-type split — sit in a bucket that rejects unsigned
    requests, and reaching them needs a Cognito credential exchange. That is
    unauthenticated in the sense that matters here — the credentials are issued
    to anyone who asks, with no account — but it is an extra step, so it lives
    behind the opt-in ``--include-net-points-daily`` flag. See
    :mod:`association.fetch.netpoints_client`.

How far back the data goes
--------------------------

A season is named for the year it ends, so 1993-94 is ``1994``. Each kind of
data starts in a different season, and the gaps are ESPN's: its endpoints
return nothing earlier, so no pull fills them in. :mod:`association.coverage`
holds these floors, and :doc:`usage` says what a question below one gets.

.. list-table::
   :header-rows: 1
   :widths: 40 20 40

   * - Data
     - First season
     - Notes
   * - Standings
     - 1987-88
     - League-wide from the start.
   * - Playoff games and team box scores
     - 1989 playoffs
     - The 1987-88 playoffs are not in ESPN's archive.
   * - Regular-season games, player and team box scores, team season stats
     - 1993-94
     - Before it, at most one team's 82 games a season.
   * - A named player's season stats
     - 1976-77
     - Only for players whose careers reached 1993-94, so a ranking starts in
       1993-94.
   * - Play-by-play
     - 2001-02
     - About half of 2001-02.
   * - Shot charts
     - 2001-02
     - Derived from play-by-play. Only part of 2001-02 (509 of 1,190 games)
       and 2002-03 (986).
   * - ESPN's power index
     - 2016-17
     - ESPN publishes none earlier.
   * - Win probability
     - 2017-18
     - ESPN publishes none earlier.
   * - NetPoints, season and per-game
     - 2018-19
     - The source answers 403 for anything earlier.

One more gap sits inside that range. Every game Chicago or New Orleans played
from 2012-13 to 2017-18, playoffs included, has an empty box score apart from
two: every player on both teams is listed with no minutes and every stat zero.
Those games' play-by-play and shots survived, and so did the season totals.
Answers built from box scores over those seasons say how many games they could
not see.

Being a good citizen
--------------------

The fetcher is deliberately unhurried, and you should keep it that way:

* **Rate limiting is on by default.** ``--rate-limit`` defaults to 5 requests
  per second and is enforced in :class:`association.fetch.client.ESPNClient`.
  There is no reason to raise it; a full multi-season pull is a background task,
  not an interactive one.
* **Fetches are checkpointed and resumable.** A completed season/season-type is
  marked done and skipped on the next run, so re-running a pull costs almost
  nothing and re-downloads almost nothing. Interrupting a pull is safe.
* **Nothing is re-fetched without being asked.** ``--force`` exists precisely so
  that refetching is a deliberate act rather than a default.

If you are iterating on parsing or the warehouse schema, work from the Parquet
files already on disk with ``association data load`` rather than pulling again —
it needs no network at all.

Legal and practical notes
-------------------------

This project is not affiliated with or endorsed by ESPN. The data is fetched
for personal analysis. Anyone extending it should keep the throttle in place,
avoid distributing bulk copies of the underlying data, and expect that the
endpoints may change or disappear.
