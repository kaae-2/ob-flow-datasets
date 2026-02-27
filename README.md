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
- matching checksum files `*.csv.zst.sha256`

## Notes

- The importer consumes only `.csv.zst` inputs under the layout above.
- The importer verifies each `.csv.zst` against its `.sha256` before packaging.
- Split part files (`.csv.zst.part*`) are not part of the active contract.

## Source and intent

- Datasets originate from FlowRepository exports and course-specific prepared variants.
- This repository exists to provide stable, benchmark-ready inputs without repeated ad hoc downloads.

## License

See repository root for licensing information.
