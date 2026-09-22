# Murine splenocytes C57BL/6

Spectral-flow immune profiling of C57BL/6 mouse splenocytes from
[FlowRepository FR-FCM-Z7CH](https://flowrepository.org/id/FR-FCM-Z7CH),
experiment 7569, associated with **OMIP-111**, DOI
[10.1002/cyto.a.24926](https://doi.org/10.1002/cyto.a.24926).

- Dataset ID: `FR-FCM-Z7CH-C57BL6`.
- Prepared name: `MurineSplenocytesC57BL6_fcm`.
- Ten biological sample files from five mice, each under BFA and Mitogen+BFA
  conditions; 2,113,422 events in total. Reference controls are excluded.
- Original annotations: `OMIP-ICS-C57BL_6.wsp`, group `ICS_C57_TS`.
- Sample metadata: `Supplementary_Note_1.xlsx`.

## Features and labels

The 15 columns are `CD8`, `CD3`, `TCRgd`, `CD62L`, `IL-4/5`, `CD45R`, `NK1.1`,
`IFNg`, `CD44`, `CD45`, `IL-2`, `CD19`, `TNF`, `IL-17A`, and `CD4`, followed by
`label`. Native FCS fluorescence values are retained, including negative values;
no additional compensation, display transform or arcsinh is applied to features.
All source events remain in original order.

| Label | Events |
|---|---:|
| B-1 | 14,423 |
| B-2 | 533,911 |
| NK | 31,796 |
| T | 533,236 |
| unlabeled | 1,000,056 |

Labels come from the saved B-1 Cells, B-2 Cells, NK1.1+ and T Cells gates, using
each sample's original FlowJo hierarchy and transformations. Exactly one selected
lineage must include an event; zero or multiple memberships produce `unlabeled`.
Independent cytokine gates are not treated as exclusive cell-type classes.

The paired BALB/c dataset measures NKp46 rather than NK1.1 and is published
separately as `FR-FCM-Z7CH-BALBc`.

## Reproduction and verification

From the dataset repository, with the source files and download manifest under
ignored `import/FR-FCM-Z7CH/`:

```bash
python -m pip install -r scripts/spectral-flow-requirements.txt
python scripts/prepare-spectral-flow.py
```

The command prepares both strains independently. For a fresh verification run,
select new `--output-root` and `--report-root` directories. Published objects are
`.csv.zst` archives with `.sha256` sidecars; local plain CSVs are ignored by Git.

`preparation-audit.json` records original source URLs/hashes, mouse/treatment
identities, sample-specific gate paths, saved/replayed counts, class counts and
output hashes. Output feature values and row order were checked against the FCS,
and labels were independently reconstructed from retained gate memberships.

Replayed counts differ from the saved FlowJo counts. FlowKit approximates ellipse
gates, and not all discrepancies have been explained. These are workspace-derived
annotations, not verified identical author-exported event labels. No gate parameters
were tuned to force matching counts.

Keep paired treatments from each mouse in the same evaluation split: there are
five independent mice, not ten independent biological replicates.

Source licensing is not recorded here; this preparation does not assert a new
license for the original data.
