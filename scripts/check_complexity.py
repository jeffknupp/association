#!/usr/bin/env python3
"""The per-function complexity gate: no function or method in ``src`` or
``scripts`` worse than radon grade C (cyclomatic complexity 20), unless the
``def`` line carries ``# complexity: <reason>`` - a flat rule or dispatch
table that reads better whole than split into named steps (decision D18,
Jeff, 2026-10-17). The reason is required, as a lint suppression's is; a
bare marker fails. Modules' grades and the average stay ``xenon``'s
(``--max-modules C --max-average B``), run at the push.

    python scripts/check_complexity.py          # the gate
    python scripts/check_complexity.py --list   # every function over C and its marker
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from radon.complexity import cc_visit

ROOT = Path(__file__).resolve().parent.parent
TREES = ("src", "scripts")
MAX_COMPLEXITY = 20
MARKER = re.compile(r"#\s*complexity:\s*(?P<reason>\S.*)$")


def over_limit(path: Path) -> list[tuple[str, int, int, str | None]]:
    """Every block in ``path`` past the limit: name, line, complexity, and the
    reason on its ``def`` line or ``None``."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    found = []
    for block in cc_visit(text):
        blocks = [block, *getattr(block, "methods", [])]
        for b in blocks:
            if b.complexity > MAX_COMPLEXITY:
                marker = MARKER.search(lines[b.lineno - 1]) if b.lineno - 1 < len(lines) else None
                found.append((b.fullname if hasattr(b, "fullname") else b.name, b.lineno, b.complexity, marker.group("reason").strip() if marker else None))
    return found


def main() -> int:
    """Run the gate."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true", help="print every function over the limit with its marker")
    args = parser.parse_args()
    failures = 0
    for tree in TREES:
        for path in sorted((ROOT / tree).rglob("*.py")):
            for name, line, complexity, reason in over_limit(path):
                where = f"{path.relative_to(ROOT)}:{line} {name} (complexity {complexity})"
                if reason:
                    if args.list:
                        print(f"{where} - allowed: {reason}")
                else:
                    failures += 1
                    print(f"{where} - over grade C: split it into named steps, or, for a flat rule or dispatch table, mark the def line '# complexity: <why it reads better whole>'")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
