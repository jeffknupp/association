#!/usr/bin/env bash
# Regenerate completions/association.{bash,zsh,fish} from the current CLI
# definition (src/association/cli.py). Click derives these directly from the
# command/option definitions, so there's nothing to hand-edit here - just
# re-run this after adding/renaming a command or option.
#
# Usage:
#   ./scripts/generate_completions.sh

set -euo pipefail

cd "$(dirname "$0")/.."

_ASSOCIATION_COMPLETE=bash_source .venv/bin/association > completions/association.bash
_ASSOCIATION_COMPLETE=zsh_source .venv/bin/association > completions/association.zsh
_ASSOCIATION_COMPLETE=fish_source .venv/bin/association > completions/association.fish

echo "Regenerated completions/association.{bash,zsh,fish}"
