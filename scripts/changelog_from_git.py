#!/usr/bin/env python3
"""Build the ``## Unreleased`` section of ``CHANGES.md`` from the commits
(decision D20, Jeff, 2026-10-17): a commit touching ``src/`` carries its
entry in its message - the subject is the entry's lead, the body its text,
with the moves it enumerates - and nobody edits the section by hand.

    python scripts/changelog_from_git.py            # print the entries since the last tag
    python scripts/changelog_from_git.py --write    # write them under ## Unreleased
    python scripts/changelog_from_git.py --since SHA

The section keeps whatever stands above the marker line
``<!-- generated from commits since <ref> -->`` (entries written by hand
before D20) and replaces everything below it; with no marker, the entries
go at the section's end under a new marker. Merge commits are left out
(their lanes' commits carry the entries); ``Co-Authored-By`` and other
trailers are dropped. ``scripts/bump_version.py`` stamps the section as
before; run this first (``docs/releasing.rst``).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGES.md"
MARKER = re.compile(r"^<!-- generated from commits since (?P<ref>\S+) -->\s*$", re.MULTILINE)
TRAILER = re.compile(r"^[A-Za-z-]+: .*$")
SEPARATOR = "\x1e"


def last_tag() -> str:
    """The newest tag reachable from HEAD."""
    return subprocess.run(["git", "describe", "--tags", "--abbrev=0"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def commits_since(ref: str) -> list[tuple[str, str, str]]:
    """``(sha, subject, body)`` for every non-merge commit after ``ref`` that touches ``src/``, oldest first."""
    out = subprocess.run(["git", "log", "--no-merges", "--reverse", f"--format=%h%x1f%s%x1f%b{SEPARATOR}", f"{ref}..HEAD", "--", "src"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    entries = []
    for chunk in out.split(SEPARATOR):
        if not chunk.strip():
            continue
        sha, subject, body = [*chunk.strip("\n").split("\x1f"), "", ""][:3]
        lines = [line for line in body.splitlines() if not TRAILER.match(line.strip())]
        entries.append((sha, subject.strip(), "\n".join(lines).strip()))
    return entries


def entry_text(sha: str, subject: str, body: str) -> str:
    """One changelog bullet: the subject in bold, the body's paragraphs after it, the commit named."""
    paragraphs = [" ".join(p.split()) for p in re.split(r"\n\s*\n", body) if p.strip()]
    text = f"- **{subject.rstrip('.')}.**"
    if paragraphs:
        text += " " + " ".join(paragraphs)
    return f"{text} ({sha})"


def render(ref: str) -> str:
    """The generated block: the marker and one bullet per commit."""
    bullets = [entry_text(*c) for c in commits_since(ref)]
    return f"<!-- generated from commits since {ref} -->\n" + ("\n".join(bullets) + "\n" if bullets else "")


def write(ref: str) -> int:
    """Replace the generated block under ``## Unreleased``; 1 where the section is missing."""
    text = CHANGELOG.read_text(encoding="utf-8")
    match = re.search(r"^## Unreleased\s*$", text, re.MULTILINE)
    if not match:
        print("error: no `## Unreleased` section in CHANGES.md", file=sys.stderr)
        return 1
    start = match.end()
    next_heading = re.search(r"^## ", text[start:], re.MULTILINE)
    end = start + next_heading.start() if next_heading else len(text)
    section = text[start:end]
    marker = MARKER.search(section)
    kept = section[: marker.start()] if marker else section.rstrip("\n") + "\n\n"
    new_section = kept + render(ref) + "\n"
    CHANGELOG.write_text(text[:start] + "\n" + new_section.lstrip("\n") + text[end:], encoding="utf-8")
    print(f"wrote {len(commits_since(ref))} entries under ## Unreleased (commits since {ref})")
    return 0


def main() -> int:
    """Print or write the entries."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", help="the ref the entries start after (default: the marker's ref, else the last tag)")
    parser.add_argument("--write", action="store_true", help="write the section instead of printing it")
    args = parser.parse_args()
    marker = MARKER.search(CHANGELOG.read_text(encoding="utf-8"))
    ref = args.since or (marker.group("ref") if marker else last_tag())
    if args.write:
        return write(ref)
    print(render(ref), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
