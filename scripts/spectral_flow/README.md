# Spectral-flow preparation

`../prepare-spectral-flow.py` prepares **Z7CH only**, exporting one marker-plus-`label`
CSV per biological sample. It preserves every event in source order and produces
compressed prepared objects with SHA-256 sidecars. Controls are not exported as
biological samples. Z8H9 is excluded because it has only one biological sample.

## Run

From the parent `ob` checkout, after downloading Z7CH:

```bash
python -m pip install -r datasets/scripts/spectral-flow-requirements.txt
python datasets/scripts/prepare-spectral-flow.py
```

The installed preparation versions are pinned in the requirements file. Defaults:

- Sources: `datasets/import/`.
- CSVs: `datasets/prepared/fcm/<dataset-id>/<shortname>/`.
- Provenance, per-sample reports and gate memberships:
  `datasets/import/_reports/spectral-flow/`.

Existing sample outputs and run reports are never overwritten. To reproduce into
new directories, pass `--output-root` and `--report-root`. A small Z7CH run is:

```bash
python datasets/scripts/prepare-spectral-flow.py --dataset Z7CH \
  --sample 'E02 C57_M1_BFA_TS WLSM.fcs' \
  --sample 'E10 BALB_M4_BFA_TS WLSM.fcs' \
  --output-root /tmp/opencode/spectral-pilot/prepared \
  --report-root /tmp/opencode/spectral-pilot/reports
```

The command rejects `--dataset Z8H9` and `--dataset all`. It records partial
progress and exits nonzero on failure. Completed files remain available; use fresh
output/report directories or a narrower sample selection to continue.

## Export contract

- Z7CH has separate `FR-FCM-Z7CH-C57BL6/MurineSplenocytesC57BL6_fcm` and
  `FR-FCM-Z7CH-BALBc/MurineSplenocytesBALBc_fcm` outputs. Their 15-marker panels
  differ at NK1.1 versus NKp46. Each dataset has its own README and portable
  `preparation-audit.json`; full runs regenerate the audits alongside the exports.
- Z7CH labels are `B-1`, `B-2`, `NK`, `T`, or `unlabeled`, using the original
  sample-specific workspace gates and transformations.
- Zero or multiple matching broad classes produce `unlabeled`.
- Native FCS feature values, including negatives, are exported without additional
  compensation, display transformation or arcsinh. Workspace/candidate transforms
  and correction matrices are used to evaluate gates, not to rewrite features.
- Scatter, time, viability, blank and autofluorescence channels remain available
  for gating but are omitted from model features.
- Full-path membership sidecars are CSVs compressed with zstd. The first column
  is zero-based `event_index`; every subsequent column header is a JSON array
  representing the complete gate path. Values are 0/1. Cytokine gates remain
  independent memberships, not mutually exclusive target labels.
- Plain CSVs are kept locally. `.csv.zst` objects exceeding 100 MiB are stored as
  contiguous `.part0000`, `.part0001`, ... objects. Concatenate parts in numerical
  order to reconstruct the compressed stream. The checksum always describes that
  whole stream. No whole/split ambiguity is permitted.
- Prepared sample filenames replace spaces with underscores so the existing raw
  GitHub downloader can fetch them. Original FCS names remain in the audits.

## Annotation limits

Z7CH gate replay is not an exact reproduction of the saved FlowJo counts. Every
count difference is recorded, including ancestors and functional gates. FlowKit
approximates the workspace ellipse with a polygon, but the discrepancies have not
all been attributed to that conversion. No thresholds or geometry are fitted to
make counts agree. Matching counts alone would not prove identical memberships.

### Archived Z8H9 investigation

The earlier Z8H9 investigation is archived locally under ignored
`datasets/import/_reports/spectral-flow-z8h9/`, including its reconstruction code.
It is excluded from the published preparation and datasets because it has only
one biological sample, and its truncated workspace cannot establish trusted labels.

## Verification

```bash
python -m unittest discover -s datasets/tests -p test_spectral_flow_preparation.py -v
python -m unittest discover -s datasets/tests -v
```

The integration tests use local raw downloads and skip when those are absent.
They exercise original-workspace export, exclusive/consensus labeling, numeric and
label roundtrips, separate descriptive panel names, and split packaging.
The exporter checks input hashes, output schema, finite values, exact numeric
roundtrip at source dtype, event order, label alignment and compressed-byte hashes.

Reports retain strain-qualified mouse IDs. Paired BFA/stimulated samples must share
a biological split group in any later benchmark. Z8H9 has only one biological
sample. This command does not configure benchmark splits or launch model runs.
