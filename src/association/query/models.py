"""Which ollama model each half of the pipeline uses by default.

One definition, imported by both the CLI and the agent. These were duplicated
for a while, and the copies could drift apart silently: `scripts/check_routing.py`
reads the router default to decide what to validate, so a divergence would have
meant the routing check passing against a model the CLI does not ship. The
prompt and the router model are one unit - swapping either invalidates the
check - which makes a second copy of the name a real hazard rather than
untidiness.

Deliberately free of heavy imports so ``cli/commands.py`` can read it at module level
without pulling in ollama and duckdb at startup.

.. versionadded:: 1.2.0
   Replaces the copies previously in :mod:`association.cli` and
   :mod:`association.query.agent`.

.. versionchanged:: 4.4.0
   Holds :data:`AGENT_BUDGET_SECONDS` too, for the same reason: the CLI
   states it as an option default and must not import the agent to do so.
"""

from __future__ import annotations

# Routing is classification under a JSON schema, not a 7B-sized job:
# constrained decoding does the structural work. Benchmarked over
# scripts/check_routing.py's cases, every model from 1.5B to 8B landed within a
# case or two of the rest, so the router takes the 3B for the speed and the RAM.
DEFAULT_ROUTER_MODEL = "qwen2.5:3b"

# The fall-through agent writes SQL by hand, which does need the capacity.
DEFAULT_MODEL = "qwen2.5:7b"

AGENT_BUDGET_SECONDS = 120.0
"""Wall-clock seconds the fall-through agent may spend on one question.

An iteration cap alone does not bound the wait, because the cost is per model
call and a 7B on CPU answers in tens of seconds: measured over 24 questions on
2026-09-18, every finished run spent essentially all of its wall time inside
1-4 model calls (6.5s, 81.7, 90.4, 90.7, 115.3, 155.5, 159.1, 167.8, 173.7,
192.4), and **14 of the 24 did not finish at all** - one ran past 17 minutes
before being killed by hand. Of the nine graded finishers, one was correct
(#129).

So the budget is checked before each call rather than left to the cap: the
first call always runs, and a question that has already spent this long is
given up on with a sentence naming why the fast path could not answer it,
which is worth more than another minute of silence. ``0`` removes the bound.

.. versionadded:: 4.4.0
"""
