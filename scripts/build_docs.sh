#!/usr/bin/env bash
# Build the Sphinx documentation, failing on any warning.
#
# -W turns warnings into errors so a broken cross-reference, a module missing
# from the API tree, or a malformed docstring fails the commit rather than
# rotting silently in the generated HTML.
#
# --keep-going reports every problem in one pass instead of stopping at the
# first, so a batch of doc changes needs one fix cycle rather than several.
#
# Usage: build_docs.sh [OUTPUT_DIR]
#
# The output directory is an argument so Read the Docs can build through this
# same script into $READTHEDOCS_OUTPUT/html. Keeping one caller of sphinx-build
# means the -W that gates a commit is the same -W that gates a published build,
# rather than two flag lists drifting apart.
set -euo pipefail

OUT="${1:-docs/_build/html}"

SPHINX="${SPHINX_BUILD:-.venv/bin/sphinx-build}"
if [[ ! -x "$SPHINX" ]]; then
    echo "docs: $SPHINX not found - run 'uv sync --extra docs'" >&2
    exit 1
fi

exec "$SPHINX" -b html docs "$OUT" -q -W --keep-going
