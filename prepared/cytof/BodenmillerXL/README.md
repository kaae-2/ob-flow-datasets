# BodenmillerXL label provenance

The prepared cohort is derived from HDCytoData `Bodenmiller_BCR_XL`
(ExperimentHub `EH2254`). The authoritative object used for validation has
SHA-256 `954badd9d92c24b62f1f1c6bb1768f9a543236043deb6740cf8cdb5b60c82405`.

HDCytoData's label-generation script uses `surface-` as shorthand for the
expert-annotated population that the Nowicka et al. CyTOF workflow describes as
"surface negative cells". The workflow explains that this coarse population
combines two finer groups from the original study: `surface- CD14+ cells` and
`surface- CD14- cells`.

Prepared labels therefore use this canonical mapping:

| Incorrect prepared label | Upstream shorthand | Canonical label |
|---|---|---|
| `ungated` | `surface-` | `surface negative cells` |

These events are a target population. They must not be mapped to the pipeline's
non-target `ungated`/label-0 class or assigned to either finer CD14 subgroup.

Regenerate and validate the prepared archives with:

```bash
python3 scripts/prepare-bodenmiller.py
python3 -m unittest tests.test_bodenmiller_preparation -v
```

Authoritative references:

- HDCytoData preparation at commit `ca8c9781c7b502430bc497d3c0beac16f2ca078f`:
  <https://github.com/lmweber/HDCytoData/blob/ca8c9781c7b502430bc497d3c0beac16f2ca078f/inst/scripts/cell_population_labels_BCR_XL.R>
- Nowicka et al. workflow source at commit
  `d6a264befefc90fbce522ec47dceff63e6949780`:
  <https://github.com/gosianow/cytofWorkflow/blob/d6a264befefc90fbce522ec47dceff63e6949780/vignettes/cytofWorkflow.Rmd>
- Bodenmiller et al. (2012), DOI `10.1038/nbt.2317`:
  <https://pmc.ncbi.nlm.nih.gov/articles/PMC3627543/>
