#!/usr/bin/env python3
"""Bump the project version and stamp the changelog for release.

Usage::

    scripts/bump_version.py (major | minor | patch | X.Y.Z) [--commit] [--tag]

``pyproject.toml`` holds the only copy of the version number: the package reads
it back through :func:`importlib.metadata.version`, ``docs/conf.py`` imports
that, and the publish workflow parses the same file to check the git tag agrees.
So a bump is one edit here plus a changelog heading, and nothing else can drift.

The changelog contract is a section headed ``## Unreleased``. This script
renames it to ``## X.Y.Z - <today>``; if there is no such section it stops
rather than inventing one, because a release with no description of what
changed is worse than a release that failed to happen.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
CHANGELOG = ROOT / "CHANGES.md"
LOCK = ROOT / "uv.lock"

# Deliberately stricter than PEP 440, which would also accept 1.0.0.post1,
# 1.0.0a1 and 1.0 - this project promises plain semantic versioning, and the
# tag/version equality check in publish.yml assumes the two spellings match
# exactly.
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
VERSION_LINE = re.compile(r'^version = "([^"]+)"$', re.MULTILINE)
UNRELEASED = re.compile(r"^## Unreleased\s*$", re.MULTILINE)

BUMPS = ("major", "minor", "patch")


def current_version() -> str:
    """Return the version currently recorded in ``pyproject.toml``."""
    match = VERSION_LINE.search(PYPROJECT.read_text())
    if match is None:
        sys.exit('error: no `version = "..."` line in pyproject.toml')
    return match.group(1)


def next_version(current: str, spec: str) -> str:
    """Resolve ``spec`` - a bump keyword or a literal version - against ``current``."""
    if spec not in BUMPS:
        if not SEMVER.match(spec):
            sys.exit(f"error: {spec!r} is neither a bump ({'/'.join(BUMPS)}) nor a MAJOR.MINOR.PATCH version")
        return spec

    match = SEMVER.match(current)
    if match is None:
        sys.exit(f"error: current version {current!r} is not MAJOR.MINOR.PATCH, so it cannot be bumped by keyword - pass an explicit version")
    major, minor, patch = (int(g) for g in match.groups())
    if spec == "major":
        return f"{major + 1}.0.0"
    if spec == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def check_clean_tree() -> None:
    """Refuse to bump on top of uncommitted changes."""
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    if dirty:
        sys.exit("error: working tree is not clean - commit or stash first:\n" + dirty)


def check_tag_free(version: str) -> None:
    """Refuse to reuse a tag, since PyPI will not let the version be reused either."""
    tags = subprocess.run(["git", "tag", "--list", f"v{version}"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    if tags:
        sys.exit(f"error: tag v{version} already exists - PyPI will not accept a second upload for it either")


def check_changelog() -> None:
    """Stop before anything is rewritten unless CHANGES.md has exactly one ``## Unreleased`` section.

    Checked here rather than in stamp_changelog, which runs after pyproject.toml
    and uv.lock are already bumped. A second heading is the failure that happened:
    only the first is stamped, and the rest sits in the history as "Unreleased"
    under an older version."""
    headings = len(UNRELEASED.findall(CHANGELOG.read_text()))
    if headings == 0:
        sys.exit("error: no `## Unreleased` section in CHANGES.md - describe the release before cutting it")
    if headings > 1:
        sys.exit(f"error: CHANGES.md has {headings} `## Unreleased` headings - only the first would be stamped, leaving the rest in the history as unreleased; merge them first")


def rewrite_pyproject(current: str, version: str) -> None:
    """Replace the version line in ``pyproject.toml``."""
    text = PYPROJECT.read_text()
    PYPROJECT.write_text(text.replace(f'version = "{current}"', f'version = "{version}"', 1))


def relock(version: str) -> None:
    """Refresh ``uv.lock`` so the tagged commit is self-consistent.

    The lock records the project's own version, and rewriting pyproject.toml
    alone leaves it a release behind - v1.0.0 and v1.1.0 were both tagged with a
    stale lock, and one needed a follow-up "sync uv.lock" commit. CI and Read
    the Docs both install with ``--frozen``, so the lock is what they actually
    build from.
    """
    subprocess.run(["uv", "lock", "--quiet"], cwd=ROOT, check=True)
    if f'version = "{version}"' not in LOCK.read_text():
        sys.exit(f"error: uv.lock still does not record {version} after `uv lock`")


def stamp_changelog(version: str) -> None:
    """Rename the ``## Unreleased`` heading to this version and today's date."""
    text = CHANGELOG.read_text()
    if not UNRELEASED.search(text):
        sys.exit("error: no `## Unreleased` section in CHANGES.md - describe the release before cutting it")
    CHANGELOG.write_text(UNRELEASED.sub(f"## {version} - {date.today().isoformat()}", text, count=1))


def main() -> int:
    """Parse arguments, apply the bump, and optionally commit and tag it."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("spec", help="major, minor, patch, or an explicit MAJOR.MINOR.PATCH")
    parser.add_argument("--commit", action="store_true", help="commit the bump")
    parser.add_argument("--tag", action="store_true", help="annotated tag vX.Y.Z (implies --commit)")
    args = parser.parse_args()

    if args.tag:
        args.commit = True

    current = current_version()
    version = next_version(current, args.spec)
    if version == current:
        sys.exit(f"error: already at {current}")

    check_clean_tree()
    check_tag_free(version)
    check_changelog()

    rewrite_pyproject(current, version)
    relock(version)
    stamp_changelog(version)
    print(f"{current} -> {version}")

    if args.commit:
        subprocess.run(["git", "add", "pyproject.toml", "uv.lock", "CHANGES.md"], cwd=ROOT, check=True)
        subprocess.run(["git", "commit", "-m", f"Release {version}"], cwd=ROOT, check=True)
    if args.tag:
        subprocess.run(["git", "tag", "-a", f"v{version}", "-m", f"association {version}"], cwd=ROOT, check=True)
        print(f"tagged v{version}")

    # Nothing here pushes. A push is what makes a release irreversible - the tag
    # becomes public and, once the GitHub release is published, the version
    # number is spent on PyPI forever.
    print("\nnothing has been pushed. Next:")
    print("  git push && git push --tags")
    print(f"  scripts/release.sh {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
