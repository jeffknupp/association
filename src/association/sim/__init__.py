"""A possession-level NBA game simulator built from the warehouse.

**Experimental.** Everything under ``association.sim`` is outside the
compatibility promise in ``CHANGES.md``: names, tables and output shapes may
change between minor versions while the simulator is being built. The plan
is ``SIMULATION_PLAN.md`` at the repository root; its section 0b holds what
was measured about the data before any of this was written, and its "Build
order under D15" says which module comes next.

The package reads ``nba.duckdb`` and writes its own derived tables to a
separate ``sim.duckdb`` (:mod:`association.sim.database`), so a rebuild of
the simulator's tables never touches the warehouse and the warehouse build
stays as fast as it is.

.. versionadded:: 6.0.0
"""
