#!/usr/bin/env bash
# Create the GitHub release for an already-pushed tag.
#
# Publishing the release is what triggers .github/workflows/publish.yml, which
# uploads to PyPI. That upload is irreversible: PyPI refuses a second file for
# a version that already exists, even after a deletion, so a mistake is fixed
# by burning the next patch number rather than by re-uploading. Everything here
# is therefore explicit and confirmed rather than inferred.
#
# Usage: release.sh X.Y.Z [--draft]
set -euo pipefail

cd "$(dirname "$0")/.."

VERSION="${1:-}"
DRAFT="${2:-}"

if [[ -z "$VERSION" ]]; then
    echo "usage: release.sh X.Y.Z [--draft]" >&2
    exit 1
fi

if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "error: '$VERSION' is not MAJOR.MINOR.PATCH" >&2
    exit 1
fi

TAG="v$VERSION"

# The packaged version and the tag must agree. publish.yml checks this too, but
# failing here costs nothing, whereas failing there has already spent the tag.
packaged=$(python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
if [[ "$packaged" != "$VERSION" ]]; then
    echo "error: pyproject.toml says $packaged, not $VERSION - run scripts/bump_version.py first" >&2
    exit 1
fi

if ! git rev-parse "$TAG" >/dev/null 2>&1; then
    echo "error: tag $TAG does not exist locally - run scripts/bump_version.py $VERSION --tag" >&2
    exit 1
fi

if ! git ls-remote --exit-code --tags origin "$TAG" >/dev/null 2>&1; then
    echo "error: tag $TAG has not been pushed - run: git push --tags" >&2
    exit 1
fi

# The release notes are the changelog section for this version, so the release
# page and CHANGES.md cannot disagree.
notes=$(awk -v v="## $VERSION " '
    $0 ~ "^" v {found=1; next}
    found && /^## / {exit}
    found {print}
' CHANGES.md)

if [[ -z "${notes// /}" ]]; then
    echo "error: no '## $VERSION' section in CHANGES.md to use as release notes" >&2
    exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
    cat >&2 <<MSG
error: the GitHub CLI (gh) is not installed.

Install it (https://cli.github.com), run 'gh auth login', and re-run this
script - or create the release by hand at:

  https://github.com/jeffknupp/association/releases/new?tag=$TAG

using the '## $VERSION' section of CHANGES.md as the notes.
MSG
    exit 1
fi

echo "About to create a GitHub release for $TAG."
echo "Publishing it triggers publish.yml, which uploads $VERSION to PyPI permanently."
echo
echo "--- notes ---"
echo "$notes"
echo "-------------"
read -r -p "Continue? [y/N] " reply
[[ "$reply" == "y" || "$reply" == "Y" ]] || { echo "aborted"; exit 1; }

args=(release create "$TAG" --title "association $VERSION" --notes "$notes")
[[ "$DRAFT" == "--draft" ]] && args+=(--draft)

gh "${args[@]}"
