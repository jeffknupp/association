"""Which ollama model each half of the pipeline uses by default.

One definition, imported by both the CLI and the agent. These were duplicated
for a while, and the copies could drift apart silently: `scripts/check_routing.py`
reads the router default to decide what to validate, so a divergence would have
meant the routing check passing against a model the CLI does not ship. The
prompt and the router model are one unit - swapping either invalidates the
check - which makes a second copy of the name a real hazard rather than
untidiness.

Deliberately free of heavy imports so `cli.py` can read it at module level
without pulling in ollama and duckdb at startup.

.. versionadded:: 1.2.0
   Replaces the copies previously in :mod:`association.cli` and
   :mod:`association.query.agent`.
"""

from __future__ import annotations

# Routing is classification under a JSON schema, not a 7B-sized job:
# constrained decoding does the structural work. Benchmarked over
# scripts/check_routing.py's cases, every model from 1.5B to 8B landed within a
# case or two of the rest, so the router takes the 3B for the speed and the RAM.
DEFAULT_ROUTER_MODEL = "qwen2.5:3b"

# The fall-through agent writes SQL by hand, which does need the capacity.
DEFAULT_MODEL = "qwen2.5:7b"
