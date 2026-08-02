# FR-FCM-ZYVT pediatric B-ALL preparation

## Authoritative sources

- FlowRepository accession: `FR-FCM-ZYVT`, experiment 2045, "Automated flow
  cytometric MRD Assessment in Childhood Acute B-Lymphoblastic Leukemia using
  Supervised Machine Learning".
- FlowRepository record: <http://flowrepository.org/id/FR-FCM-ZYVT>.
- Associated publication: Reiter et al., *Cytometry Part A* 95(9):966-975
  (2019), PMID 31282025, DOI `10.1002/cyto.a.23852`.
- Prepared scope: the 72 Berlin `Bln*.30.fcs` samples that have exact paired
  author-provided Kaluza `.analysis` workspaces. The publication contains 337
  samples from three laboratories; the unpaired FACSDiva/XML cohorts are not
  represented by this prepared benchmark cohort.

## Gate hierarchy and targets

Every paired Kaluza workspace defines the same gate graph:

```text
Syto + -> time axis -> singlets -> viable -> corrected
corrected -> pre -> Erythroblasts
corrected -> CD19+ -> Blasts
                    -> mature B-cells
                    -> plasmacells
```

`Syto +`, `time axis`, `singlets`, `viable`, and `corrected` are technical or
quality-control gates. `pre` and `CD19+` are nonterminal biological parent
gates. The benchmark targets are therefore the four terminal workspace gates,
using the workspace spelling unchanged: `Erythroblasts`, `Blasts`, `mature
B-cells`, and `plasmacells`.

The terminal source masks overlap for 4,394 events across 39 samples. Kaluza
does not encode sibling precedence, so preparation does not infer one: only an
event in exactly one terminal mask receives that target. Events in no terminal
mask or more than one terminal mask are `unlabeled`. Aggregate source-mask and
prepared counts are:

| Population | Source mask | Exclusive prepared target |
|---|---:|---:|
| Erythroblasts | 940,953 | 940,935 |
| Blasts | 173,989 | 169,727 |
| mature B-cells | 2,582,515 | 2,578,138 |
| plasmacells | 196 | 64 |
| unlabeled | not applicable | 23,446,961 |

All 27,135,825 source events are retained. The preparation report records the
mask, exclusive, ambiguous, and unlabeled counts for each sample.

## Features

The seven model features are `CD58`, `CD10`, `CD34`, `CD19`, `CD38`, `CD20`,
and `CD45`. `SYTO41` (also written `SY41` in 66 workspaces) is the nucleic-acid
channel used by the root `Syto +` gate. It remains available while evaluating
the source gate hierarchy and is recorded as a gating-only measurement, but it
is not an immunophenotyping-antigen model feature.

## Reproduction

Run the source preparation from the dataset repository:

```bash
python scripts/prepare-flowrepository-gated.py --dataset FR-FCM-ZYVT

dataset_dir=prepared/fcm/FR-FCM-ZYVT/PediatricBALL_fcm
for csv in "$dataset_dir"/*.csv; do
  zstd -12 --threads=1 --force --quiet --rm "$csv" -o "$csv.zst"
done
(
  cd "$dataset_dir"
  for archive in *.csv.zst; do
    sha256sum "$archive" > "$archive.sha256"
  done
)
```

The local source inputs are under ignored `import/2045/`. The generated audit
is `import/_reports/flowrepository-gated-prep-report.json`. Prepared CSVs are
compressed with Zstandard level 12 and each `*.csv.zst.sha256` binds the exact
archive bytes. The preparation was validated with Python 3.12, pandas 2.3.3,
FlowKit 1.3.0, NumPy 2.4.1, and Zstandard CLI 1.5.7.

No dataset license record is present in this repository; resolve reuse terms
from FlowRepository before redistribution.
