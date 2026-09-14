#!/usr/bin/env bash
# Snapshot the warehouse and its Parquet tree, so parallel work can read a
# frozen copy while the live files are rebuilt underneath it.
#
# Why this exists. `warehouse.build` REPLACES tables inside nba.duckdb in
# place, and `data pull --force` rewrites Parquet files in place. Either one
# run centrally while other sessions are reading gives those readers a file
# that changed mid-query. A snapshot is the cheap fix: take one, point the
# readers at it, then rebuild the live copy freely.
#
# Two consequences worth knowing before you reach for something faster:
#
#   - The DuckDB file must be a REAL copy, never a hardlink. A hardlink shares
#     the inode, and an in-place rebuild writes straight through it into every
#     "copy" you made. The Parquet tree can be hardlinked (--link) because a
#     pull writes new files rather than editing existing ones - but --force
#     breaks even that, so --link is off by default.
#   - `cp` of a DuckDB file that is being written is not atomic. Check nothing
#     holds it first; this script refuses if something does, unless --force.
#
# Usage:
#   scripts/snapshot_warehouse.sh                     # archive to the default store
#   scripts/snapshot_warehouse.sh --extract-to DIR    # also materialize a usable snapshot
#   scripts/snapshot_warehouse.sh --restore ARCHIVE   # restore over the live files
#   scripts/snapshot_warehouse.sh --list
#
# The archive is self-describing: it carries a MANIFEST with the row counts,
# file counts, the git SHA the warehouse was built at, and a sha256 of itself.
set -euo pipefail

SRC_DB="${ASSOCIATION_DB:-/home/jeff/code/association/nba.duckdb}"
SRC_PARQUET="${ASSOCIATION_DATA:-/home/jeff/code/association/data/parquet}"
STORE="${ASSOCIATION_SNAPSHOTS:-/home/jeff/association-snapshots}"
LABEL="$(date +%Y%m%d-%H%M%S)"
FORCE=0
LINK=0
EXTRACT_TO=""
MODE="create"
ARCHIVE_ARG=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force)       FORCE=1; shift ;;
        --link)        LINK=1; shift ;;
        --label)       LABEL="$2"; shift 2 ;;
        --extract-to)  EXTRACT_TO="$2"; shift 2 ;;
        --restore)     MODE="restore"; ARCHIVE_ARG="$2"; shift 2 ;;
        --list)        MODE="list"; shift ;;
        -h|--help)     sed -n '2,30p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

# Fastest available, in order. zstd -3 on this data runs at roughly disk speed;
# gzip is the fallback that exists everywhere.
pick_compressor() {
    if command -v zstd >/dev/null;      then echo "zstd -3 -T0"; return; fi
    if command -v pigz >/dev/null;      then echo "pigz -6"; return; fi
    echo "gzip -6"
}
COMPRESSOR="$(pick_compressor)"
EXT="$([[ "$COMPRESSOR" == zstd* ]] && echo tar.zst || echo tar.gz)"

human() { numfmt --to=iec --suffix=B "$1" 2>/dev/null || echo "$1"; }

case "$MODE" in
list)
    mkdir -p "$STORE"
    if ! ls -1 "$STORE"/*.tar.* >/dev/null 2>&1; then echo "no snapshots in $STORE"; exit 0; fi
    for a in "$STORE"/*.tar.*; do
        printf '%s  %s\n' "$(human "$(stat -c %s "$a")")" "$a"
        [[ -f "$a.manifest" ]] && sed 's/^/    /' "$a.manifest"
    done
    exit 0
    ;;

restore)
    [[ -f "$ARCHIVE_ARG" ]] || { echo "no such archive: $ARCHIVE_ARG" >&2; exit 1; }
    if [[ -f "$ARCHIVE_ARG.sha256" ]]; then
        echo "verifying checksum..."
        (cd "$(dirname "$ARCHIVE_ARG")" && sha256sum -c "$(basename "$ARCHIVE_ARG").sha256")
    fi
    if [[ $FORCE -ne 1 ]] && fuser "$SRC_DB" >/dev/null 2>&1; then
        echo "REFUSING: something holds $SRC_DB. Stop it, or pass --force." >&2; exit 1
    fi
    echo "restoring over the LIVE files:"
    echo "   $SRC_DB"
    echo "   $SRC_PARQUET"
    read -r -p "Continue? [y/N] " reply
    [[ "$reply" == [yY] ]] || { echo "aborted"; exit 1; }
    tar --use-compress-program="$(echo "$COMPRESSOR" | awk '{print $1" -d"}')" \
        -xf "$ARCHIVE_ARG" -C "$(dirname "$SRC_PARQUET")/.." 2>/dev/null \
      || tar -xaf "$ARCHIVE_ARG" -C "$(dirname "$SRC_PARQUET")/.."
    echo "restored."
    exit 0
    ;;
esac

# ---- create -----------------------------------------------------------------
[[ -f "$SRC_DB" ]] || { echo "no warehouse at $SRC_DB" >&2; exit 1; }
[[ -d "$SRC_PARQUET" ]] || { echo "no parquet tree at $SRC_PARQUET" >&2; exit 1; }

if [[ $FORCE -ne 1 ]] && fuser "$SRC_DB" >/dev/null 2>&1; then
    echo "REFUSING: something holds $SRC_DB open; a copy taken mid-write is not a snapshot." >&2
    echo "Close it, or pass --force if you know the holder is a read-only reader." >&2
    exit 1
fi

need=$(( $(stat -c %s "$SRC_DB") + $(du -sb "$SRC_PARQUET" | cut -f1) ))
avail=$(( $(df -B1 --output=avail "$STORE" 2>/dev/null | tail -1 || df -B1 --output=avail /home | tail -1) ))
echo "source: $(human "$need")   free: $(human "$avail")"
if (( avail < need )); then
    echo "REFUSING: less free space than the uncompressed source. Snapshot would be tight." >&2; exit 1
fi

mkdir -p "$STORE"
ARCHIVE="$STORE/warehouse-$LABEL.$EXT"
[[ -e "$ARCHIVE" && $FORCE -ne 1 ]] && { echo "exists: $ARCHIVE (use --label or --force)" >&2; exit 1; }

SHA="$(git -C "$(dirname "$SRC_DB")" rev-parse --short HEAD 2>/dev/null || echo unknown)"
PARQUET_FILES=$(find "$SRC_PARQUET" -name '*.parquet' | wc -l)

echo "archiving with: $COMPRESSOR"
echo "  -> $ARCHIVE"
# Paths are stored relative to the checkout root, so --restore and --extract-to
# both land on the same ./nba.duckdb + ./data/parquet shape.
ROOT="$(cd "$(dirname "$SRC_DB")" && pwd)"
tar --use-compress-program="$COMPRESSOR" \
    -cf "$ARCHIVE" \
    -C "$ROOT" \
    "$(basename "$SRC_DB")" \
    "data/parquet"

SIZE=$(stat -c %s "$ARCHIVE")
(cd "$STORE" && sha256sum "$(basename "$ARCHIVE")" > "$(basename "$ARCHIVE").sha256")

cat > "$ARCHIVE.manifest" <<MANIFEST
taken:          $(date -Is)
git:            $SHA
source db:      $SRC_DB ($(human "$(stat -c %s "$SRC_DB")"))
source parquet: $SRC_PARQUET ($PARQUET_FILES files)
archive:        $(human "$SIZE")
ratio:          $(awk -v a="$SIZE" -v b="$need" 'BEGIN{printf "%.1f%%", 100*a/b}')
compressor:     $COMPRESSOR
MANIFEST
echo "--- manifest ---"; sed 's/^/  /' "$ARCHIVE.manifest"

if [[ -n "$EXTRACT_TO" ]]; then
    echo "materializing a readable snapshot at $EXTRACT_TO"
    mkdir -p "$EXTRACT_TO"
    if [[ $LINK -eq 1 ]]; then
        # Parquet only, and only because a pull writes NEW files. Never the db.
        cp -al "$SRC_PARQUET" "$EXTRACT_TO/data-parquet-linked"
        cp "$SRC_DB" "$EXTRACT_TO/nba.duckdb"
        echo "  parquet hardlinked, duckdb copied (a hardlinked db would be written through)"
    else
        tar -xaf "$ARCHIVE" -C "$EXTRACT_TO"
    fi
    chmod -R a-w "$EXTRACT_TO" 2>/dev/null || true
    echo "  read-only at: $EXTRACT_TO"
    echo "  point a reader at: --db-path $EXTRACT_TO/nba.duckdb --data-dir $EXTRACT_TO/data/parquet"
fi
echo "done."
