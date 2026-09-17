#!/usr/bin/env bash
# Audit every locked dependency, extras included, against the PyPI advisory
# database (pip-audit). Run by CI on every push and weekly on a schedule
# (.github/workflows/audit.yml), because a vulnerability published against an
# unchanged uv.lock would otherwise go unnoticed until the next push. Not a
# pre-commit hook: it needs the network, and commits here work offline.
#
# The audit reads the exact pins in uv.lock rather than the installed venv, so
# what it checks is what CI and a release build install. Hashes are exported
# too, which is what lets --disable-pip skip resolving and installing anything:
# the whole run takes a couple of seconds.
#
# Usage: scripts/audit_dependencies.sh [extra pip-audit args...]
set -euo pipefail

cd "$(dirname "$0")/.."

# Pinned so a new pip-audit release cannot change what passes without a commit.
PIP_AUDIT_VERSION="2.10.0"

# Accepted advisories, one --ignore-vuln per line, each with a comment saying
# why it does not apply and when to revisit it. Empty is the normal state.
IGNORED=(
)

requirements="$(mktemp --suffix=.txt)"
trap 'rm -f "$requirements"' EXIT

# --no-emit-project: association itself is not on PyPI, so there is nothing to
# look up, and --strict would fail on the skip.
uv export --frozen --all-extras --no-emit-project --quiet --output-file "$requirements"

uvx "pip-audit==${PIP_AUDIT_VERSION}" --requirement "$requirements" --disable-pip --strict --progress-spinner off "${IGNORED[@]}" "$@"
