#!/usr/bin/env bash
# The check to run WHILE ITERATING: every gate a commit must pass except the
# Sphinx build, plus the whole test suite except its one slow test, in
# parallel across the cores available.
#
# Measured 2026-09-20 on 8 cores, with three other agents competing for them:
#
#   this script                                         39s
#   the hooks it runs (all but Sphinx)                   7s
#   uv run pre-commit run --all-files                   26s   (19s of it Sphinx)
#   uv run pytest -q                                   156s   serial
#   uv run pytest -q -n auto                            80s
#   uv run pytest -q -n auto -m "not slow"              30s
#
# So the full check is `uv run pre-commit run --all-files && uv run pytest -q`
# at about 106s, and this one - the 7s of hooks plus the 30s of tests - is the
# one to run several times per commit.
#
# It is NOT a substitute for that full check before committing. Exactly two
# things are left out, both named so nobody has to guess:
#
#   - The Sphinx build (`docs`), which rebuilds from scratch with -E on
#     purpose (see AGENTS.md) and is 19s of the 26s the hooks cost. It is the
#     gate that catches a malformed docstring, so a commit touching one runs
#     the full check.
#   - tests marked `slow` - today one: the Eastern-date agreement test, 57s of
#     the suite's 80s, which every full run still executes.
#
# Both commands read their exit status through `set -e`, which is the point:
# `pre-commit run --all-files | tail` hides a failure in the FIRST hook, and
# ruff is first.

set -euo pipefail

cd "$(dirname "$0")/.."

SKIP=docs uv run pre-commit run --all-files
uv run pytest -q -n auto -m "not slow"
