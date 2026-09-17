"""Load-time repairs of ESPN's faults, and the filtered tables built beside them.

Each module here runs inside :func:`association.fetch.warehouse.build`, over
tables that have just been loaded from Parquet, in the order ``warehouse``
calls them: the game and team-box repairs first, so nothing built afterwards
reads a stored fault, then the rebuilt box line and the filtered game list.
Every one is idempotent and keyed on what it repairs rather than on whether a
value "looks wrong", so a later ``data load`` reproduces it exactly and a fix
ESPN makes upstream stops it by itself. What each fault is, with evidence, is
in ``DATA.md``.

.. versionadded:: 3.0.0
"""
