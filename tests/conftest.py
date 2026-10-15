"""Suite-wide test configuration: the day the tests are run "on".

Tests encode the 2025-26 season - a fixture's game dates beside
``current_season()``, an expected ISO date, a sentence naming "2026" - and
call the reader or a relation directly, where the season is the calendar's,
which turns over on October 1. The suite is a fixed world, so its "today"
is pinned here, once. A test about another day sets
``ASSOCIATION_TODAY`` itself (``monkeypatch.setenv``), and an explicit value
in the environment wins, so the suite can still be run "on" any date by
hand.

And, when asked for, the recording of every call the suite makes across a
stage boundary (``tests/stage_calls.py``).

And no git variable of the environment reaches a test: every ``GIT_*``
variable is removed before any test runs (:func:`without_git_variables`).
``git rebase --exec`` exports ``GIT_DIR`` to the command it runs, and a
test that builds a scratch repository - ``git init``, ``git config``,
``git commit`` in a ``tmp_path`` - or the scripts and package code it
calls, which run git themselves, would write into the real repository
instead: its config and a worktree's HEAD. Removed here, once, for the whole process, rather than
in each test's ``subprocess.run``: the git a test runs through
``scripts/check_changes_per_commit.py``, ``scripts/bump_version.py``,
``association.cli.paths`` or ``association.query.history`` inherits the
process's environment too.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, MutableMapping

import pytest

from association.nba.season import TODAY_ENV

os.environ.setdefault(TODAY_ENV, "2026-09-30")


def without_git_variables(environ: MutableMapping[str, str]) -> list[str]:
    """Remove every ``GIT_*`` variable from ``environ`` - ``GIT_DIR``,
    ``GIT_WORK_TREE``, ``GIT_INDEX_FILE``, ``GIT_AUTHOR_NAME``, ... - so a
    git command run with it reads the repository its working directory or
    ``-C`` names and nothing else; returns the names removed. ``GITHUB_*``
    (CI's) is no git variable and stays."""
    removed = sorted(name for name in environ if name.startswith("GIT_"))
    for name in removed:
        del environ[name]
    return removed


without_git_variables(os.environ)

# Off unless asked for: every call the suite makes across a stage boundary,
# written out for comparing two trees (tests/stage_calls.py). Installed here, before any test module is imported, so a test
# that imports a boundary by name gets the recording one.
if os.environ.get("ASSOCIATION_STAGE_CALLS"):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from stage_calls import install

    install(Path(os.environ["ASSOCIATION_STAGE_CALLS"]))


@pytest.fixture(autouse=True)
def _names_loaded() -> Iterator[None]:
    """Every test runs inside one ``names.loaded()`` block, as every
    question does inside ``Agent.ask``: a name lookup outside a block is an
    error (``names.NotLoaded``), so a test that calls a reader, a matcher
    or the parser directly needs one. The players' and teams' tables are
    read once per connection per test; a test that changes one of them
    after a lookup opens a block of its own to read it again."""
    from association.query import names

    with names.loaded():
        yield
