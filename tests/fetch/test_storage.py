"""Sanity tests for the flat-file storage layer: atomic writes and the O(1)
completion-marker primitives."""

from pathlib import Path

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
