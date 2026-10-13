"""Insert a test warehouse's rows in one statement per table.

DuckDB's Python API binds ``?`` parameters slowly: ``executemany`` runs one
statement per row, and 62 ``player_box_stats`` rows of 24 columns took 0.11s
that way, where the same rows registered as one Arrow table and inserted
with ``INSERT ... SELECT`` took 0.003s (measured 2026-10-13, the OVH devbox).
A multi-row ``INSERT`` with the values as parameters is no cure (0.05s to
0.08s): the binding is the cost, not the statement count. The fixtures of
``tests/query/test_compose.py`` and ``tests/query/test_conditions.py`` built
their warehouses row by row and spent 34 of the two files' 39 seconds doing
it, rebuilt for each of their 260 tests.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import duckdb
import pyarrow as pa


def insert_rows(con: duckdb.DuckDBPyConnection, table: str, rows: Iterable[Sequence[Any]]) -> None:
    """Append ``rows`` to ``table``, each a full row in the table's column
    order - as ``INSERT INTO table VALUES (...)`` takes it - in one statement.

    The rows go to DuckDB as one Arrow table with a column per position, and
    DuckDB casts each column to the table's type as it would a bound
    parameter. A row of the wrong width fails the insert, and a column whose
    values Arrow cannot type together (a string beside a number) fails
    building the table: both loudly, never a row quietly dropped."""
    rows = list(rows)
    if not rows:
        return
    width = len(rows[0])
    if any(len(row) != width for row in rows):
        raise ValueError(f"rows for {table} differ in width")
    columns = {f"c{i}": [row[i] for row in rows] for i in range(width)}
    view = f"_insert_rows_{table}"
    con.register(view, pa.table(columns))
    try:
        con.execute(f"INSERT INTO {table} SELECT * FROM {view}")
    finally:
        con.unregister(view)
