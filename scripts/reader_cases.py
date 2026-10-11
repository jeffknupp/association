#!/usr/bin/env python3
"""Regenerate the reader's question-to-Reading snapshot cases
(``tests/query/reader_cases.json``): every case's ``expected`` record is
read again through the parser's door over the case warehouse
(``tests/reader_cases.py``), under the suite's pinned date, and written
back. Run it when a change moves a case on purpose, and name every moved
case in the commit, as a population baseline's moves are named::

    uv run python scripts/reader_cases.py            # rewrite expected values
    uv run python scripts/reader_cases.py --check    # exit 1 on any case that moved

A case's inputs - its name, reason, question, the model's names and stat -
are edited by hand; its expected value never is.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
# The suite's own date (tests/conftest.py), so "last season" reads the same
# here as under pytest; an explicit value in the environment wins there too.
os.environ.setdefault("ASSOCIATION_TODAY", "2026-09-30")


def main() -> int:
    """Read every case again and rewrite, or check, its expected record."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="report the cases that moved and exit 1, writing nothing")
    args = parser.parse_args()
    import reader_cases as rc

    from association.query import names
    from association.query.stages import value_differences

    cases = rc.load_cases()
    con = rc.warehouse()
    moved = 0
    with names.loaded():
        for case in cases:
            got = rc.record(rc.read(con, case["question"], case["names"] or None, case["stat"]))
            differences = value_differences("reading", case["expected"], got)
            if differences:
                moved += 1
                print(f"{case['name']}: {case['question']!r}")
                for difference in differences[:6]:
                    print(f"    {difference.path}: {difference.before!r} -> {difference.after!r}")
            case["expected"] = got
    print(f"{len(cases)} cases, {moved} moved")
    if args.check:
        return 1 if moved else 0
    rc.CASES.write_text(json.dumps(cases, indent=1, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
