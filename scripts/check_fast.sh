#!/usr/bin/env bash
# The check to run WHILE ITERATING: every gate a commit must pass except the
# Sphinx build, plus the whole test suite, in parallel across the cores
# available.
#
# Measured 2026-10-02 on 24 cores, nothing else running:
#
#   this script                                         24s
#   the hooks it runs (all but Sphinx)                   4s
#   uv run pre-commit run --all-files                   17s   (13s of it Sphinx)
#   uv run pytest -q                                    87s   serial
#   uv run pytest -q -n auto                            20s
#
# So the full check is `uv run pre-commit run --all-files && uv run pytest -q -n auto`
# at about 37s, and this one - the 4s of hooks plus the 20s of tests - is the
# one to run several times per commit.
#
# It is NOT a substitute for that full check before committing. Exactly one
# thing is left out, named so nobody has to guess: the Sphinx build (`docs`),
# which rebuilds from scratch with -E on purpose (see AGENTS.md) and is 13s of
# the 17s the hooks cost. It is the gate that catches a malformed docstring,
# so a commit touching one runs the full check.
#
# Both commands read their exit status through `set -e`, which is the point:
# `pre-commit run --all-files | tail` hides a failure in the FIRST hook, and
# ruff is first.

set -euo pipefail

cd "$(dirname "$0")/.."

SKIP=docs uv run pre-commit run --all-files
uv run pytest -q -n auto
