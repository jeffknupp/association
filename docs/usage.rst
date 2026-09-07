Usage recipes
=============

Worked examples for the things you actually do. Every command is safe to
re-run: fetches are checkpointed, and warehouse builds are idempotent.

Fetching a range of seasons
---------------------------

``season`` follows ESPN's convention — the year a season **ends**, so the
2023-24 season is ``2024``.

.. code-block:: console

   $ association data pull --seasons 2022-2026

That pulls regular season and postseason (``--season-types`` defaults to
``2,3``) and builds the warehouse afterwards. It is a background task: at the
default 5 requests/second, several seasons take a while, and there is no reason
to raise the limit.

Add play-by-play — which is where shot charts and win probability come from —
with ``--include-pbp``. It costs no extra requests, only parse time and disk:

.. code-block:: console

   $ association data pull --seasons 2026 --include-pbp

NetPoints, including the play-type fingerprint, is a separate opt-in because it
needs a credential exchange (see :doc:`data-sources`):

.. code-block:: console

   $ association data pull --seasons 2026 --include-net-points-daily

Interrupting any of these is safe. Re-running skips whatever already completed.

Checking data consistency
-------------------------

Report what is on disk, per season and season type, without touching the
network:

.. code-block:: console

   $ association data check --seasons 2026

To cross-check against what ESPN says *should* exist — the real test of whether
a pull is complete — add ``--live``:

.. code-block:: console

   $ association data check --seasons 2022-2026 --live

``--live`` only re-verifies seasons not already checkpointed complete; add
``--force`` to re-verify everything.

Note that a missing game is not always a gap. Postponed, cancelled and
forfeited games are recorded as such, and :mod:`association.check.report`
accounts for them rather than reporting them as missing data.

Forcing a refetch
-----------------

Checkpointing means a completed season is skipped. When you actually want the
data again — a parser fix, or a suspicion that ESPN backfilled something —
``--force`` ignores the checkpoints:

.. code-block:: console

   $ association data pull --seasons 2026 --force

Use it narrowly. ``--force`` over a wide range re-downloads everything.

If the fix is in *parsing* rather than fetching, you do not need the network at
all — rebuild from the Parquet already on disk:

.. code-block:: console

   $ association data load
   $ association data load --tables games,player_box_stats   # a subset, faster

Keeping a season up to date
---------------------------

During a live season the current year is never "complete", so re-running the
pull picks up new games and refreshes season aggregates:

.. code-block:: console

   $ association data pull --seasons 2026

Run it as often as you like — completed games are checkpointed and skipped, so
a daily update fetches roughly a day's worth of work. Season-aggregate
endpoints (standings, season stats, power index) are treated as live and
refreshed while the season is in progress, so per-game averages do not go
stale.

Asking questions
----------------

One-shot:

.. code-block:: console

   $ association query "who leads the league in assists?"
   $ association query "how many times did the 76ers play Boston?"
   $ association query "Klay Thompson's 3pt percentage over the past 4 seasons"

Interactive, keeping context between questions:

.. code-block:: console

   $ association ai

Add ``--verbose`` to watch the routing decision and every tool call as they
happen. The same trace is always written to ``.history/`` regardless, so a
surprising answer can be diagnosed after the fact:

.. code-block:: console

   $ association query --verbose "most games with 20+ rebounds this season?"
     [timing] model inference #1: 1.59s
     -> (router) intent='threshold_count' slots={'stat': 'rebounds', 'threshold': 20, 'season': 2026}
     [timing] template threshold_count: 0.02s

Setting up the models
---------------------

Two models, for the two jobs described in :doc:`architecture`:

.. code-block:: console

   $ ollama pull qwen2.5:3b    # router, ~1.9GB
   $ ollama pull qwen2.5:7b    # fall-through agent, ~4.7GB

Both fit in memory together. If a question falls through to the agent it will
take noticeably longer — that path prefills a much larger prompt — which is
expected, not a fault.

``--no-fast-path`` forces every question through the agent, which is useful for
comparing the two paths but slow.
