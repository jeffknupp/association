"""The read-only warehouse connection: the database untouched, and the disk
out of reach."""

from pathlib import Path

import duckdb

from association.query.connection import connect_read_only


def _warehouse(tmp_path: Path) -> str:
    path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO players VALUES ('1', 'Stephen Curry')")
    con.close()
    return str(path)


def test_the_connection_cannot_read_files_off_the_disk(tmp_path: Path) -> None:
    """``read_only=True`` protects the database and nothing else.

    Confirmed against the real warehouse before this was closed:
    ``read_csv('/etc/passwd')`` returned rows and ``glob('/home/<user>/*')``
    listed dotfiles, through the same connection every answer reads. Kept
    after the SQL-writing agent went (5.0.0): no template reads a file, so
    the guard costs nothing and keeps the warehouse the entire surface.
    """
    secret = tmp_path / "secret.txt"
    secret.write_text("sk-not-a-real-key")
    con = connect_read_only(_warehouse(tmp_path))
    reads = [
        f"SELECT content FROM read_text('{secret}')",
        f"SELECT * FROM read_csv('{secret}', header=false, columns={{'line': 'VARCHAR'}})",
        f"SELECT * FROM glob('{tmp_path}/*')",
    ]
    for query in reads:
        try:
            rows = con.execute(query).fetchall()
        except duckdb.Error:
            continue
        raise AssertionError(f"{query} read {rows!r}")


def test_closing_the_disk_off_did_not_close_the_warehouse_off(tmp_path: Path) -> None:
    """The other half of the check above: a guard that broke ordinary querying
    would pass it just as well."""
    con = connect_read_only(_warehouse(tmp_path))
    assert con.execute("SELECT display_name FROM players").fetchall() == [("Stephen Curry",)]


def test_the_connection_cannot_write(tmp_path: Path) -> None:
    con = connect_read_only(_warehouse(tmp_path))
    try:
        con.execute("DELETE FROM players")
    except duckdb.Error:
        return
    raise AssertionError("a read-only connection ran a DELETE")
