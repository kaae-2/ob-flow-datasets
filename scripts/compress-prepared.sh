#!/usr/bin/env bash
set -euo pipefail

# Compress prepared CSVs to zstd archives (idempotent)
# Creates .zst and .zst.sha256 files next to originals; keeps originals unchanged.

NUM_JOBS=4
LEVEL=12
FORCE=0
MAX_MB=100
SPLIT_MB=
ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
PREPARED_DIR="$ROOT_DIR/datasets/prepared"

usage() {
  cat <<'USAGE'
Usage: ./scripts/compress-prepared.sh [--jobs N]

Options:
  --jobs N    number of parallel compress jobs (default 4)
  --level N   zstd compression level (default 12)
  --force     recompress and overwrite existing .zst files
  --max-mb N  fail if any .zst exceeds this size (default 100)
  --split-mb N  size of .zst parts when splitting (default: --max-mb)
  --help     show this message

This script finds CSV files under datasets/prepared (including subfolders) and
creates corresponding .zst and .zst.sha256 files. It is safe to run
multiple times (skips files where .zst already exists unless --force).
USAGE
}

while [ ${#@} -gt 0 ]; do
  case "$1" in
    --jobs) NUM_JOBS=${2:-$NUM_JOBS}; shift 2 ;;
    --level) LEVEL=${2:-$LEVEL}; shift 2 ;;
    --force) FORCE=1; shift ;;
    --max-mb) MAX_MB=${2:-$MAX_MB}; shift 2 ;;
    --split-mb) SPLIT_MB=${2:-$SPLIT_MB}; shift 2 ;;
    --help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

if [ ! -d "$PREPARED_DIR" ]; then
  echo "Prepared directory not found: $PREPARED_DIR" >&2
  exit 1
fi

if [ -z "$SPLIT_MB" ]; then
  SPLIT_MB=$MAX_MB
fi

echo "Compressing CSVs under $PREPARED_DIR with zstd -$LEVEL (jobs=$NUM_JOBS)"

export LEVEL FORCE MAX_MB SPLIT_MB

# Find files and compress in parallel; skip if .zst exists
find "$PREPARED_DIR" -type f -name '*.csv' -print0 \
  | xargs -0 -n1 -P "$NUM_JOBS" sh -c '
    f="$1"
    if [ "$FORCE" -ne 1 ]; then
      if [ -f "$f.zst" ]; then
        size_bytes=$(stat -c %s "$f.zst")
        max_bytes=$((MAX_MB * 1024 * 1024))
        if [ "$size_bytes" -le "$max_bytes" ]; then
          echo "SKIP: $f.zst within ${MAX_MB}MB"
          exit 0
        fi
      else
        for part in "$f.zst.part"*; do
          if [ -e "$part" ]; then
            echo "SKIP: split parts exist for $f"
            exit 0
          fi
        done
      fi
    else
      rm -f "$f.zst.part"*
    fi
    echo "Compressing: $f"
    zstd -"$LEVEL" -T0 -k -f --quiet "$f"
    sha256sum "$f.zst" > "$f.zst.sha256"
    size_bytes=$(stat -c %s "$f.zst")
    max_bytes=$((MAX_MB * 1024 * 1024))
    if [ "$size_bytes" -gt "$max_bytes" ]; then
      echo "Splitting: $f.zst (>${MAX_MB}MB)"
      split -b "${SPLIT_MB}M" -d -a 4 "$f.zst" "$f.zst.part"
      rm -f "$f.zst"
    fi
  ' _

echo "Checking for compressed files over ${MAX_MB}MB"
oversize=$(find "$PREPARED_DIR" -type f \( -name '*.csv.zst' -o -name '*.csv.zst.part*' \) -size +"${MAX_MB}"M -print)
if [ -n "$oversize" ]; then
  echo "Error: compressed files exceed ${MAX_MB}MB:" >&2
  echo "$oversize" >&2
  exit 1
fi

echo "Done. To verify a file: zstd -d -c file.csv.zst > /tmp/file.csv && sha256sum -c file.csv.zst.sha256"
