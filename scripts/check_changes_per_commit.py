#!/usr/bin/env python3
"""Hold every pushed commit, not only the working tree, to the CHANGES rule.

    python scripts/check_changes_per_commit.py                 # the range CI was pushed (from its environment)
    python scripts/check_changes_per_commit.py --range A..B    # every commit in A..B
    python scripts/check_changes_per_commit.py --range A..B --no-floor

``scripts/check_changes_md.sh`` (the pre-commit hook) asks that a change to
``src/`` touches ``CHANGES.md`` - of the index it is run on, never of a
commit. A rebase that folds neighbors together, or a hook skipped once,
leaves a commit holding source with no entry, and nothing local can see it:
three Phase 3 commits did (``bfe09f7``, ``6aa0dca``, ``d1078c8``; ISSUES.md
#380, found by the Phase 3 review), so a bisect or a revert lands on a fix
the message does not name. This reads each non-merge commit of a range and
fails on one that touches ``src/`` without ``CHANGES.md``.

Which range: an explicit ``--range``; else a pull request's own commits
(``PR_BASE``/``PR_HEAD``, from their merge base); else a push's
``BEFORE..AFTER`` - and where ``BEFORE`` is no commit this clone holds or no
ancestor of ``AFTER`` (a new branch, a force-push) the pushed head commit
alone, said so. A merge commit is not checked: its diff is the two sides',
each checked as its own commits.

The history before this check existed is not held to it: commits reachable
from :data:`FLOOR` (master when it was written) are left out, so the three
commits above stay where they are, recorded, rather than failing every push
(``--no-floor`` reads them, which is how the check was watched to fail).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

FLOOR = "8e1a78749898d70cf97e3b1f507f50e81a5298ae"
"""master when this check was written (2026-10-12): every commit reachable
from it is history the check does not hold."""

ZEROS = "0" * 40


def _git(*args: str) -> str:
    """A git command's standard output, stripped; raises on failure."""
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def _is_commit(ref: str) -> bool:
    """Whether this clone holds ``ref`` as a commit."""
    return bool(ref) and ref != ZEROS and subprocess.run(["git", "cat-file", "-e", f"{ref}^{{commit}}"], capture_output=True, check=False).returncode == 0


def _is_ancestor(older: str, newer: str) -> bool:
    """Whether ``older`` is an ancestor of ``newer``."""
    return subprocess.run(["git", "merge-base", "--is-ancestor", older, newer], capture_output=True, check=False).returncode == 0


def pushed_range() -> str:
    """The range the CI run was triggered for (module docstring)."""
    base, head = os.environ.get("PR_BASE", ""), os.environ.get("PR_HEAD", "")
    if _is_commit(base) and _is_commit(head):
        return f"{_git('merge-base', base, head)}..{head}"
    before, after = os.environ.get("BEFORE", ""), os.environ.get("AFTER", "") or _git("rev-parse", "HEAD")
    if _is_commit(before) and _is_ancestor(before, after):
        return f"{before}..{after}"
    print(f"no pushed range to read (BEFORE={before or 'unset'}): checking the pushed head {after[:7]} alone", flush=True)
    return f"{after}^!" if _is_commit(f"{after}^") else after


def commits(revision_range: str, *, floor: bool) -> list[str]:
    """The non-merge commits of ``revision_range``, oldest first, less those
    reachable from :data:`FLOOR` where ``floor`` holds and the clone has it."""
    args = ["rev-list", "--no-merges", "--reverse", *revision_range.split()]
    if floor:
        if _is_commit(FLOOR):
            args.append(f"^{FLOOR}")
        else:
            print(f"the floor {FLOOR[:7]} is not in this clone (a shallow checkout?): every commit of the range is read", flush=True)
    listed = _git(*args)
    return listed.split() if listed else []


def unrecorded(sha: str) -> bool:
    """Whether the commit ``sha`` touches ``src/`` and not ``CHANGES.md``."""
    files = _git("diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha).splitlines()
    return any(path.startswith("src/") for path in files) and "CHANGES.md" not in files


def main() -> int:
    """1 where a commit of the range touches ``src/`` without ``CHANGES.md``."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--range", dest="revision_range", help="a git revision range (A..B); the CI environment's when omitted")
    parser.add_argument("--no-floor", action="store_true", help=f"read the history before the check existed too (reachable from {FLOOR[:7]})")
    args = parser.parse_args()
    revision_range = args.revision_range or pushed_range()
    read = commits(revision_range, floor=not args.no_floor)
    failed = [sha for sha in read if unrecorded(sha)]
    for sha in failed:
        print(f"  {_git('log', '-1', '--format=%h %s', sha)}", flush=True)
    print(f"{len(read)} commit(s) of {revision_range} read: {len(failed)} touch src/ without CHANGES.md", flush=True)
    if failed:
        print("error: each commit that changes src/ carries its CHANGES.md entry (AGENTS.md, 'Before you commit'): amend or split the commits named above.", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
