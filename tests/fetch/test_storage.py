"""Sanity tests for the flat-file storage layer: atomic writes and the O(1)
completion-marker primitives."""

from pathlib import Path

import pyarrow.parquet as pq

from association.fetch import storage


def test_write_rows_then_exists(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "data.parquet"
    assert not storage.exists(path)
    storage.write_rows(path, [{"a": 1}, {"a": 2}])
    assert storage.exists(path)


def test_write_rows_is_atomic_no_leftover_tmp_file(tmp_path: Path) -> None:
    path = tmp_path / "data.parquet"
    storage.write_rows(path, [{"a": 1}])
    assert not path.with_name(path.name + ".tmp").exists()


def test_write_rows_empty_list_is_noop(tmp_path: Path) -> None:
    path = tmp_path / "data.parquet"
    storage.write_rows(path, [])
    assert not path.exists()


def test_write_row_single_dict(tmp_path: Path) -> None:
    path = tmp_path / "row.parquet"
    storage.write_row(path, {"x": 1, "y": "hello"})
    assert storage.exists(path)


def test_exists_false_for_zero_byte_file(tmp_path: Path) -> None:
    """A killed process could in principle leave a zero-byte file behind (though
    the atomic tmp+rename design should prevent it) - exists() must not treat
    an empty file as valid data."""
    path = tmp_path / "empty.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    assert not storage.exists(path)


def test_exists_false_for_missing_file(tmp_path: Path) -> None:
    assert not storage.exists(tmp_path / "nope.parquet")


def test_mark_complete_then_is_complete(tmp_path: Path) -> None:
    marker = tmp_path / "season=2024" / "season_type=2.marker"
    assert not storage.is_complete(marker)
    storage.mark_complete(marker)
    assert storage.is_complete(marker)


def test_mark_complete_creates_parent_dirs(tmp_path: Path) -> None:
    marker = tmp_path / "a" / "b" / "c.marker"
    storage.mark_complete(marker)
    assert marker.exists()


def test_concurrent_writers_of_the_same_path_do_not_corrupt_it(tmp_path: Path) -> None:
    """Two games sharing a player both cache that player's bio, so with
    --workers above 1 two threads really do write one path at the same time. A
    shared .tmp name had them interleaving into a single file, which then got
    renamed into place looking perfectly normal."""
    import threading

    path = tmp_path / "players" / "athlete_1.parquet"
    rows = [{"athlete_id": "1", "display_name": "LeBron James", "padding": "x" * 5000}]
    barrier = threading.Barrier(8, timeout=10)
    errors: list[BaseException] = []

    def write() -> None:
        try:
            barrier.wait()
            for _ in range(20):
                storage.write_rows(path, rows)
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=write) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert pq.read_table(path).to_pylist() == rows
    # No scratch files left behind, whichever writer got there last.
    assert [p.name for p in path.parent.iterdir()] == [path.name]
