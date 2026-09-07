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
    The NetPoints player and team ratings behind espnanalytics.com, including
    the play-type "fingerprint" breakdown. Reaching these needs a Cognito
    credential exchange, which is unauthenticated in the sense that matters
    here — the credentials are issued to anyone who asks, with no account — but
    it is an extra step, so it lives behind an opt-in flag. See
    :mod:`association.fetch.netpoints_client`.

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
