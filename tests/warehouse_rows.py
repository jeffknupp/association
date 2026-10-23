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


def plays_all_complete(con: duckdb.DuckDBPyConnection) -> None:
    """The completeness table a period read bounds its games by
    (``nba.period_line.PLAYS_COMPLETE``), as a view saying every played
    player-game in a game the shot table covers adds up to its box score in
    every column - a gap of 0, on the rows the load-time build writes
    (``fetch.repairs.plays_complete``) - for a fixture whose period figures
    are not the point. A view, so a game a test inserts later is covered
    too; a test about a game that does not add up materializes it
    (:func:`plays_short`).
    """
    from association.nba.period_line import PERIOD_COLUMNS, PLAYS_COMPLETE, gap_column

    gaps = ", ".join(f"CAST(0 AS BIGINT) AS {gap_column(c)}" for c in PERIOD_COLUMNS)
    con.execute(
        f"CREATE OR REPLACE VIEW {PLAYS_COMPLETE} AS SELECT b.event_id, b.season, b.season_type, b.athlete_id, {gaps} "
        "FROM player_game_log b WHERE b.minutes IS NOT NULL AND NOT b.did_not_play "
        "AND b.event_id IN (SELECT DISTINCT cov.event_id FROM shot_chart cov WHERE cov.season = b.season)"
    )


def plays_short(con: duckdb.DuckDBPyConnection, short: dict[tuple[str, str], dict[str, int]]) -> None:
    """Materialize :func:`plays_all_complete`'s view as the table it stands
    for, with each ``(event_id, athlete_id)`` in ``short`` falling from its
    box score by the gap given per column (the box score less the plays, as
    ``plays_complete`` writes it for a game missing plays at the source)."""
    from association.nba.period_line import PLAYS_COMPLETE, gap_column

    con.execute(f"CREATE TABLE _plays_complete AS SELECT * FROM {PLAYS_COMPLETE}")
    con.execute(f"DROP VIEW {PLAYS_COMPLETE}")
    con.execute(f"ALTER TABLE _plays_complete RENAME TO {PLAYS_COMPLETE}")
    for (event, athlete), gaps in short.items():
        for column, gap in gaps.items():
            con.execute(f"UPDATE {PLAYS_COMPLETE} SET {gap_column(column)} = ? WHERE event_id = ? AND athlete_id = ?", [gap, event, athlete])
