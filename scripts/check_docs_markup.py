#!/usr/bin/env python3
"""Fail if a built API page prints docstring field markup as text.

    scripts/check_docs_markup.py docs/_build/html

Sphinx's ``-W`` never sees this one. A field marker inside a paragraph
(``:rtype: list[str]`` on the line after a sentence, with no blank line in
front) is valid reStructuredText - one more line of the paragraph - so docutils
has nothing to warn about, and the page ships with the marker printed
verbatim. That is how 68 functions on 18 of 40 API pages came to show a
literal ``:rtype:`` line, each beside the real Return type field Sphinx added
as its fallback, and it was found only by grepping the built HTML. The shim in
``docs/conf.py`` (``_rtype_insert_index``) is what stops it; this is the check
that says so, and it fails when the shim stops working.

Only ``api/`` is checked, because that is where autodoc writes, and the text
inside ``<code>`` and ``<pre>`` is skipped: a docstring may legitimately show
``:rtype:`` as a literal (the shim's own does), and that renders as code.

Exit status 1 when any page has a hit, with the page and the count.
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

FIELD_MARKUP = re.compile(r":(?:rtype|returns|raises|param|type|meta)(?: [^:\n]+)?:")
"""A docstring field marker as reStructuredText spells it, with or without an argument."""


class _ProseText(HTMLParser):
    """Collect the page's text, leaving out what is inside ``<code>`` or ``<pre>``."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._literal_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("code", "pre"):
            self._literal_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("code", "pre") and self._literal_depth:
            self._literal_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._literal_depth:
            self.parts.append(data)


def prose_text(html: str) -> str:
    """The text a reader sees on the page, code blocks and inline code removed."""
    parser = _ProseText()
    parser.feed(html)
    return "".join(parser.parts)


def find_literal_markup(out_dir: Path) -> dict[str, int]:
    """Pages under ``out_dir/api`` whose prose holds field markup, with hit counts."""
    hits: dict[str, int] = {}
    for page in sorted((out_dir / "api").rglob("*.html")):
        count = len(FIELD_MARKUP.findall(prose_text(page.read_text())))
        if count:
            hits[str(page.relative_to(out_dir))] = count
    return hits


def main(argv: list[str]) -> int:
    """Check one built tree and report."""
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    out_dir = Path(argv[1])
    if not (out_dir / "api").is_dir():
        print(f"docs markup: no api/ under {out_dir} - was the build run?", file=sys.stderr)
        return 1
    hits = find_literal_markup(out_dir)
    for page, count in hits.items():
        print(f"docs markup: {page}: {count} field marker(s) printed as text", file=sys.stderr)
    if hits:
        print(f"\n{len(hits)} page(s) print docstring field markup as text. The :rtype: shim in docs/conf.py is what prevents this - see its docstring.", file=sys.stderr)
        return 1
    print(f"docs markup: {sum(1 for _ in (out_dir / 'api').rglob('*.html'))} api page(s) clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
