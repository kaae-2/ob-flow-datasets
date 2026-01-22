#!/usr/bin/env bash
set -euo pipefail

# Compress prepared CSVs to zstd archives (idempotent)
# Creates .zst and .zst.sha256 files next to originals; keeps originals unchanged.

NUM_JOBS=4
LEVEL=19
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
PREPARED_DIR="$ROOT_DIR/datasets/prepared"

usage() {
  cat <<'USAGE'
Usage: ./scripts/compress-prepared.sh [--jobs N]

Options:
  --jobs N   number of parallel compress jobs (default 4)
  --level N  zstd compression level (default 19)
  --help     show this message

This script finds CSV files under datasets/prepared (including subfolders) and
creates corresponding .zst and .zst.sha256 files. It is safe to run
multiple times (skips files where .zst already exists).
USAGE
}

while [ ${#@} -gt 0 ]; do
  case "$1" in
    --jobs) NUM_JOBS=${2:-$NUM_JOBS}; shift 2 ;;
    --level) LEVEL=${2:-$LEVEL}; shift 2 ;;
    --help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

if [ ! -d "$PREPARED_DIR" ]; then
  echo "Prepared directory not found: $PREPARED_DIR" >&2
  exit 1
fi

echo "Compressing CSVs under $PREPARED_DIR with zstd -$LEVEL (jobs=$NUM_JOBS)"

export LEVEL

# Find files and compress in parallel; skip if .zst exists
find "$PREPARED_DIR" -type f -name '*.csv' -print0 \
  | xargs -0 -n1 -P "$NUM_JOBS" sh -c '
    f="$1"
    if [ -f "$f.zst" ]; then
      echo "SKIP: $f.zst exists"
      exit 0
    fi
    echo "Compressing: $f"
    zstd -"$LEVEL" -T0 -k --quiet "$f"
    sha256sum "$f.zst" > "$f.zst.sha256"
  ' _

echo "Done. To verify a file: zstd -d -c file.csv.zst > /tmp/file.csv && sha256sum -c file.csv.zst.sha256"
