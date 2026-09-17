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
# -E throws away the saved environment and re-reads every source. Without it
# the build is incremental, and Sphinx re-reads a page only when a source it
# knows about changed - a registered config value, a document, a module
# autodoc noted as a dependency. A function patched in docs/conf.py is none of
# those, so the :rtype: shim there was invisible to an incremental build (18 of
# 40 pages kept the literal line after the fix, 0 in a fresh build), and a
# page Sphinx does not re-read also re-emits none of its warnings, so the -W
# gate can pass locally where CI's clean build fails. Measured: a no-op
# incremental build 2.7s, with -E 11.2s, from an empty directory 13.0s. The
# 8.5s buys a docs gate that says the same thing here as in CI.
#
# The markup check after the build catches what -W cannot: a docstring field
# marker printed as text. See scripts/check_docs_markup.py.
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
if [[ ! -x "${SPHINX}" ]]; then
    echo "docs: ${SPHINX} not found - run 'uv sync --extra docs'" >&2
    exit 1
fi

"${SPHINX}" -b html docs "${OUT}" -q -W --keep-going -E

# The interpreter beside sphinx-build, so the check runs under the same
# environment on Read the Docs as here.
"$(dirname "${SPHINX}")/python" "$(dirname "$0")/check_docs_markup.py" "${OUT}"
