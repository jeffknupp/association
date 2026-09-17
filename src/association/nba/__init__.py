"""What this project knows about the NBA itself, independent of ESPN's API and of how a question is asked.

The season calendar and US Eastern game dates (:mod:`~association.nba.season`),
the names each franchise played under (:mod:`~association.nba.franchises`),
the season each table's data starts in (:mod:`~association.nba.coverage`), and
ESPN Analytics' NetPoints play-type categories (:mod:`~association.nba.netpoints`).

Both :mod:`association.fetch` and :mod:`association.query` read these, and the
two may not import each other (the import contracts in ``pyproject.toml``), so
the shared knowledge sits in its own package below both. Nothing here imports
anything else from ``association``.

.. versionadded:: 3.0.0
   The modules moved here from the package root; ``association.net_points_categories``
   is now :mod:`association.nba.netpoints`.
"""
