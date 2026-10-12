#!/usr/bin/env python3
"""Read a question into its typed Reading with no warehouse: the parser's
door over the case warehouse (``tests/reader_cases.py``, the names tables
carried as test data), the model's names and stat handed in by hand, under
the suite's pinned date. What the stage snapshot would record of the
reading, as JSON, so a wording can be checked where ``nba.duckdb`` is not::

    uv run python scripts/read_offline.py "points per quarter for Luka" --names "Luka Doncic" --stat points
    uv run python scripts/read_offline.py "maxey record when embiid scores 20+ points" --names "Tyrese Maxey" "Joel Embiid"
    uv run python scripts/read_offline.py "steph stats april 2021" --fields span cuts who

With ``--fields`` only those top-level fields print; with none, the whole
record. The model's reply is the names and the stat alone (``AGENTS.md``,
"What the model sees"), so a run here is the reader on the words plus
exactly what ``--names`` and ``--stat`` say, and nothing a model might have
invented or dropped reaches it unless you hand it in.

.. versionadded:: 6.0.0
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
# The suite's own date (tests/conftest.py), so "this season" reads the same
# here as under pytest; an explicit value in the environment wins.
os.environ.setdefault("ASSOCIATION_TODAY", "2026-09-30")


def main() -> int:
    """Print the typed Reading of one question as the snapshot records it."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("question")
    parser.add_argument("--names", nargs="*", default=[], help="the names the model's reply would carry, as the question spells them")
    parser.add_argument("--stat", default="", help="the stat the model's reply would carry (its normalizer key, e.g. points)")
    parser.add_argument("--fields", nargs="*", default=None, help="print only these top-level fields of the record")
    args = parser.parse_args()
    import reader_cases

    from association.query import names

    con = reader_cases.warehouse()
    with names.loaded():
        record = reader_cases.record(reader_cases.read(con, args.question, args.names or None, args.stat))
    shown = record if args.fields is None else {field: record.get(field) for field in args.fields}
    print(json.dumps(shown, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
