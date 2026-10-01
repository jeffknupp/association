"""The one way the query side opens the warehouse.

.. versionadded:: 5.0.0
   Moved here from ``association.query.toolbox``, which went with the
   tool-calling agent it served.
"""

from __future__ import annotations

import duckdb


def connect_read_only(db_path: str) -> duckdb.DuckDBPyConnection:
    """Open the warehouse to be queried: read-only, and with no reach off it.

    ``read_only=True`` protects the *database*, and says nothing at all about
    the disk under it. DuckDB's table functions still read whatever the process
    can - confirmed live against the built warehouse, where
    ``SELECT * FROM read_csv('/etc/passwd')`` returns rows and
    ``glob('/home/<user>/*')`` lists dotfiles. That mattered most while a
    model wrote SQL from a question a stranger may have phrased; nothing
    writes SQL from a question any more, and the guard stays because it costs
    nothing and keeps the warehouse the entire surface: no template reads a
    Parquet file, attaches a database or copies anything. The fetch path is
    the opposite case and keeps its own connection: building the warehouse IS
    reading 208,000 files off disk.

    .. versionadded:: 2.1.0
    """
    return duckdb.connect(db_path, read_only=True, config={"enable_external_access": False})


def latest_season_on_record(con: duckdb.DuckDBPyConnection) -> int | None:
    """The latest season the warehouse holds a played regular-season or
    postseason game for - what "this season" can mean before the calendar's
    new season has a game in it (:func:`association.nba.season.season_on_record`).
    Preseason games do not count: a season with only those has no line to
    read. ``None`` for a warehouse with no games table at all (a fixture).

    .. versionadded:: 5.0.0
    """
    for table in ("real_games", "games"):
        try:
            row = con.execute(f"SELECT MAX(season) FROM {table} WHERE season_type IN (2, 3) AND winner_team_id IS NOT NULL").fetchone()
        except duckdb.Error:
            continue
        return int(row[0]) if row and row[0] is not None else None
    return None
