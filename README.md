# ob-flow-datasets

Prepared datasets used by the benchmark pipeline.

## Prepared layout contract

Prepared files are organized as:

`prepared/<platform>/<dataset_name>/<shortname>/`

- `platform`: `cytof` or `fcm`
- `dataset_name`: benchmark-facing dataset id (the value passed as `--dataset_name`)
- `shortname`: compact cohort/abbreviation label (also exported in metadata)

Each `<shortname>` directory stores compressed sample files:

- `*.csv.zst`
- or contiguous zero-based `*.csv.zst.partNNNN` objects whose ordered bytes form
  the missing whole object
- matching checksum files `*.csv.zst.sha256`

## Notes

- Each checksum must have exactly one whole or split representation. Missing,
  ambiguous, noncontiguous, duplicate, and orphan objects are invalid.
- The importer verifies whole bytes, including ordered split assembly, against
  the whole-object `.sha256` before packaging.

## Prepared-data manifest

`scripts/prepared_data_manifest.py` is the stable, read-only manifest and
completeness checker. It binds the generated manifest and every source URL to a
required full dataset commit, enumerates all prepared samples without packaging
them, and can stream-verify every whole checksum from a local Git object store
or GitHub:

```bash
python scripts/prepared_data_manifest.py \
  --revision <full-40-character-commit> \
  --repository . \
  --output /tmp/prepared-data-manifest.json
```

The deterministic `1.0.0` JSON schema records dataset/sample relative path,
whole or split representation, ordered source objects and sizes, whole SHA-256,
immutable source and preparation references, dataset revision, and license
status. Licenses and per-sample preparation methods were not recorded in this
repository, so the manifest reports `unknown/not-recorded` rather than inferring
them. This is a publication caveat.

## Source and intent

- Datasets originate from FlowRepository exports and course-specific prepared variants.
- This repository exists to provide stable, benchmark-ready inputs without repeated ad hoc downloads.

## License

No dataset license records are currently present in this repository. Consumers
must treat every generated manifest's `unknown/not-recorded` license status as
a publication caveat and resolve it from the original source before reuse.
