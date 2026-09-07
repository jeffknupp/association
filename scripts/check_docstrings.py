#!/usr/bin/env python3
"""Fail if any public module, class or function lacks a docstring.

Sphinx with ``-W`` catches malformed docs, but not missing ones: autodoc
renders an undocumented function perfectly happily, just uselessly. This is the
gate that keeps API reference pages from filling up with bare signatures.

Names starting with an underscore are private and exempt.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def main() -> int:
    missing: list[str] = []
    for file in sorted(SRC.rglob("*.py")):
        tree = ast.parse(file.read_text())
        rel = file.relative_to(SRC.parent.parent)
        if ast.get_docstring(tree) is None:
            missing.append(f"{rel}: module docstring")
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if node.name.startswith("_") or ast.get_docstring(node) is not None:
                continue
            missing.append(f"{rel}:{node.lineno}: {node.name}")
    for item in missing:
        print(f"undocumented: {item}", file=sys.stderr)
    if missing:
        print(f"\n{len(missing)} undocumented public item(s).", file=sys.stderr)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
