Decompressed CSVs and repository guidance
=====================================

What changed
------------
- Large prepared CSVs (>50MB) were losslessly compressed to `.zst` files using `zstd -19 -T0` and
  SHA256 checksums were generated (`.zst.sha256`). Originals are kept on disk but are no longer tracked in git.
- A local history rewrite was performed to purge raw `prepared/*.csv` files from repository history. A backup tag
  was created before the rewrite (search for a tag named `pre-purge-large-csvs-*`).

Why this was done
-----------------
- GitHub enforces a 100 MB per-file limit; the original CSVs exceeded that. Compressing with `zstd` keeps exact
  bytes and typically reduces size by ~70% for these text CSVs.

How to decompress and verify a single file
------------------------------------------
- Decompress to stdout and write to disk:

  ```bash
  zstd -d -c path/to/file.csv.zst > path/to/file.csv
  ```

- Verify checksum (created at compression time):

  ```bash
  sha256sum -c path/to/file.csv.zst.sha256
  ```

Decompress all `.zst` files under `prepared/` safely
--------------------------------------------------
Run this (it writes decompressed CSVs next to the archives):

```bash
find prepared -type f -name '*.csv.zst' -print0 \
  | xargs -0 -n1 -P4 -I{} sh -c 'zstd -d -c "{}" > "${0%.zst}" && sha256sum -c "{}".sha256' {}
```

Repository & push notes
----------------------
- The branch history was rewritten locally to remove large CSV blobs. A backup tag named
  `pre-purge-large-csvs-<timestamp>` exists; do not delete it unless you are sure.
- To publish the rewritten history to the remote you will need to force-push all branches and tags:

  ```bash
  git push --force --all
  git push --force --tags
  ```

- If others have clones of the repo, coordinate with them — rewriting history requires collaborators to
  rebase or re-clone.

Verify that no large CSVs remain in history (optional)
---------------------------------------------------
This one-liner checks for any CSV paths referenced in commits/objects:

```bash
git rev-list --objects --all | grep -E 'prepared/.*\.csv' || echo 'No prepared CSVs found in history.'
```

Questions or next steps
-----------------------
- I can (A) prepare a short note for your README describing how to fetch and use the compressed data, or
  (B) switch the repo to Git LFS instead (migrate/blobs). Tell me which and I’ll follow up.
