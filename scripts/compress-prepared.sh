#!/usr/bin/env bash
set -euo pipefail

# Compress prepared CSVs >50MB to zstd archives (idempotent)
# Creates .zst and .zst.sha256 files next to originals; keeps originals unchanged.

NUM_JOBS=4
LEVEL=19

usage() {
  cat <<'USAGE'
Usage: ./scripts/compress-prepared.sh [--jobs N]

Options:
  --jobs N   number of parallel compress jobs (default 4)
  --level N  zstd compression level (default 19)
  --help     show this message

This script finds CSV files under ./prepared larger than 50MB and
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

echo "Compressing CSVs >50MB under ./prepared with zstd -$LEVEL (jobs=$NUM_JOBS)"

export LEVEL

# Find files and compress in parallel; skip if .zst exists
find ./prepared -type f -name '*.csv' -size +50M -print0 \
  | xargs -0 -n1 -P "$NUM_JOBS" sh -c '
    f="$0"
    if [ -f "$f.zst" ]; then
      echo "SKIP: $f.zst exists"
      exit 0
    fi
    echo "Compressing: $f"
    zstd -"$LEVEL" -T0 -k --quiet "$f"
    sha256sum "$f.zst" > "$f.zst.sha256"
  '

echo "Done. To verify a file: zstd -d -c file.csv.zst > /tmp/file.csv && sha256sum -c file.csv.zst.sha256"
