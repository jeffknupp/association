#!/usr/bin/env bash
# Fail unless the package's public API is 100% type-complete.
#
# `pyright --verifytypes` inspects an INSTALLED package, and the editable
# install in .venv resolves through an import hook pyright cannot follow - it
# reports "No py.typed file found" and scores 0% of zero symbols. So the
# package is installed into a scratch directory first. --no-deps keeps that
# fast; --ignoreexternal means third-party gaps are not counted against us.
set -euo pipefail

THRESHOLD="${TYPE_COMPLETENESS_THRESHOLD:-100}"
target="$(mktemp -d)"
trap 'rm -rf "$target"' EXIT

uv pip install --quiet --target "$target" --no-deps . >/dev/null

# The scratch install is --no-deps, so nothing the package imports is beside it
# - and .venv's site-packages has to be on the path explicitly for pyright to
# resolve them. --ignoreexternal is not enough on its own: a class whose BASE
# cannot be resolved (association.web.app's pydantic models, once the `web`
# extra is in play) is reported as partially unknown and counted against the
# score, not ignored as external.
site="$(.venv/bin/python -c 'import site; print(site.getsitepackages()[0])')"

report="$(PYTHONPATH="$target:$site" .venv/bin/pyright --verifytypes association --ignoreexternal 2>&1 || true)"
score="$(printf '%s\n' "$report" | sed -n 's/^Type completeness score: *\([0-9.]*\)%.*/\1/p' | tail -1)"

if [[ -z "$score" ]]; then
    printf '%s\n' "$report" >&2
    echo "type completeness: could not parse a score from pyright" >&2
    exit 1
fi

if awk "BEGIN{exit !($score < $THRESHOLD)}"; then
    printf '%s\n' "$report" | grep -E 'error:|warning:' >&2 || true
    echo "type completeness ${score}% is below the required ${THRESHOLD}%" >&2
    exit 1
fi

echo "type completeness: ${score}%"
