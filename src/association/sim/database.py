"""The simulator's own DuckDB file, with the warehouse attached read-only.

Decision D2 of ``SIMULATION_PLAN.md``: derived tables live in ``sim.duckdb``
beside ``nba.duckdb``, never inside it. Three reasons, each a lesson from the
main project. The warehouse build stays as fast as it is today. Nothing new
appears where a reader of the warehouse could mistake a DERIVED table for a
fetched one (the reasoning behind ``KNOWN_TABLES`` in
``fetch/repairs/reconstructed_box.py``). And a rebuild of the simulator's
tables, which will happen often while the simulator is being built, can never
corrupt the warehouse - the attachment is ``READ_ONLY``, so a stray ``UPDATE``
fails instead of landing.

The warehouse is attached under the catalog name :data:`WAREHOUSE`, so every
statement here reads ``nba.plays``, ``nba.real_games`` and so on, and the
simulator's own tables are the bare names (``sim_plays``).

.. versionadded:: 6.0.0
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from pathlib import Path

import duckdb

SIM_SCHEMA_VERSION = 1
"""Bumped when a table's shape changes so a stale ``sim.duckdb`` is rebuilt
rather than read.

.. versionadded:: 6.0.0
"""

WAREHOUSE = "nba"
"""The catalog name the warehouse is attached under.

.. versionadded:: 6.0.0
"""


def connect(sim_path: Path, warehouse_path: Path, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open ``sim.duckdb`` with the warehouse attached read-only as ``nba``.

    ``read_only`` opens the simulator's file read-only too, for a reader
    (the engine, the CLI's summaries) that builds nothing. The warehouse must
    exist; the simulator's file is created when it does not.

    ``preserve_insertion_order`` is off for the same reason as in the
    warehouse build (``fetch.warehouse._tune``): ``sim_plays`` is built from a
    14-million-row scan, row order carries no meaning in any table here
    (``seq`` orders a game's plays explicitly), and buffering the scan in
    arrival order is what ran the warehouse build out of memory.

    .. versionadded:: 6.0.0
    """
    if not warehouse_path.exists():
        raise FileNotFoundError(f"no warehouse at {warehouse_path}")
    con = duckdb.connect(str(sim_path), read_only=read_only)
    con.execute("SET preserve_insertion_order = false")
    # ATTACH takes no bound parameter, so the path is quoted by hand.
    quoted = str(warehouse_path).replace("'", "''")
    con.execute(f"ATTACH IF NOT EXISTS '{quoted}' AS {WAREHOUSE} (READ_ONLY)")
    return con


def write_meta(con: duckdb.DuckDBPyConnection, *, seasons: Iterable[int], warehouse_path: Path) -> None:
    """Record what a build was made from: the schema version, the seasons,
    the warehouse file and the time. ``sim_meta`` is a two-column key/value
    table so a later version can add a fact without a migration.

    .. versionadded:: 6.0.0
    """
    con.execute("CREATE OR REPLACE TABLE sim_meta (key VARCHAR PRIMARY KEY, value VARCHAR)")
    rows = [
        ("schema_version", str(SIM_SCHEMA_VERSION)),
        ("seasons", ",".join(str(s) for s in sorted(set(seasons)))),
        ("warehouse", str(warehouse_path)),
        ("built_at", dt.datetime.now(dt.UTC).isoformat(timespec="seconds")),
    ]
    con.executemany("INSERT INTO sim_meta VALUES (?, ?)", rows)


def read_meta(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    """The ``sim_meta`` facts as a dict, empty when nothing has been built.

    .. versionadded:: 6.0.0
    """
    tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' AND table_catalog = current_database()").fetchall()}
    if "sim_meta" not in tables:
        return {}
    return dict(con.execute("SELECT key, value FROM sim_meta").fetchall())
