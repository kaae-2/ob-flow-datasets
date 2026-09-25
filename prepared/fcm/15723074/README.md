# Spectral flow cytometry of healthy adults

Seven donor samples from Zenodo record
[15723074](https://zenodo.org/records/15723074), prepared as
`SpectralFlowHealthyAdults_fcm`.

## Features and scaling

The prepared CSVs contain the publication's eight gating markers followed by
`label`: `CD14`, `CD19`, `CD3`, `CD56`, `CD45RA`, `CD8`, `CD4`, and `CCR7`.

Each marker is stored on a common cofactor-150 input scale:

`stored_value = source_value * 150 / publication_cofactor`

| Marker | Publication cofactor |
|---|---:|
| CD14 | 10000 |
| CD19 | 1000 |
| CD3 | 3000 |
| CD56 | 2000 |
| CD45RA | 4000 |
| CD8 | 3000 |
| CD4 | 5000 |
| CCR7 | 6000 |

The benchmark importer then applies `arcsinh(stored_value / 150)`. This is
mathematically equivalent to the publication-specific
`arcsinh(source_value / publication_cofactor)` transform while keeping one clear
import rule for FCM datasets. No arcsinh transform is stored in these CSVs.

## Reproduction

With the source donor CSVs under ignored `import/15723074/`:

```bash
python scripts/prepare-zenodo-15723074.py
```

The preparation report is written to
`import/_reports/zenodo-15723074-prep-report.json`. Published sample objects are
compressed `.csv.zst` files with matching SHA-256 sidecars.
