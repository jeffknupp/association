"""Which ollama model the normalizer uses by default.

One definition, imported by both the CLI and the answering loop. These were duplicated
for a while, and the copies could drift apart silently: the routing check read
the router default to decide what to validate, so a divergence would have meant
the check passing against a model the CLI does not ship. The same holds for the
normalizer that replaced the router's classification (5.0.0): its prompt and its
model are one unit, measured together - swapping either invalidates the
measurement - which makes a second copy of the name a real hazard rather than
untidiness.

Deliberately free of heavy imports so ``cli/commands.py`` can read it at module level
without pulling in ollama and duckdb at startup.

.. versionadded:: 1.2.0
   Replaces the copies previously in :mod:`association.cli` and
   :mod:`association.query.agent`.

.. versionchanged:: 4.4.0
   Held ``AGENT_BUDGET_SECONDS`` too, for the same reason.

.. versionchanged:: 5.0.0
   ``DEFAULT_MODEL`` (the fall-through agent's 7B) and
   ``AGENT_BUDGET_SECONDS`` are gone with the agent; the normalizer's model
   is the only one.
"""

from __future__ import annotations

# The normalizer's model (the name is the router's, which it served until
# 5.0.0). Copying names and picking one stat key under a JSON schema is not a
# 7B-sized job: measured over the 277 day10 wordings, the 3B copies 299 of 302
# names verbatim at 0.82s a question median, and the 7B adds nothing on names
# and 13 of 162 on the stat at 1.84s
# (~/association-research/parser-greenfield/RESULT.md) - so the 3B keeps it,
# for the speed and the RAM.
DEFAULT_ROUTER_MODEL = "qwen2.5:3b"
