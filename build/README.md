# build/ — refgenie-native recipe/asset build layer

This directory drives the **asset** half of the nightly Rivanna pipeline (the
`refgenie-registry-build` job in
[`lab.databio.org/mobot/jobs.d/`](https://github.com/databio/lab.databio.org)).
It is the refgenie-native counterpart to [`stores/`](../stores/), which builds the
underlying RefgetStores.

Per [`design.md`](../design.md): **refgenie is the build system.** This layer
loads the registry's `asset_classes/` + `recipes/` into a refgenie1 database,
asks refgenie to render a Snakemake workflow, and runs that workflow on Rivanna
to build one asset per `(genome, asset)` request in [`pep/samples.csv`](../pep/samples.csv).
There is no conda in the build path — each rule runs `refgenie build` inside the
recipe's container.

## Files

| File | Role |
|------|------|
| `run_builds.sh` | Entry point. Imports recipes + renders the Snakefile, then dispatches builds via snakemake. |
| `profiles/rivanna/config.yaml` | Snakemake SLURM profile (shefflab allocation). One SLURM job per asset; resources live here, not in recipes. |
| `config.yaml` | Snakemake `configfile:` (placeholder — rules don't read it; satisfies the directive). |
| `update_index.py` | After builds, writes `index/<genome>/<recipe>.yaml` entries from the refgenie DB. |
| `Snakefile` | **Generated** each run (gitignored). |

## How it works

1. `tools/import_recipes.py --db-config <db> --snakefile build/Snakefile`
   loads every asset class + recipe into a refgenie1 DB **and** renders the
   Snakefile from the *same* instance (so there is no two-process DB mismatch).
2. `run_builds.sh` patches the generated Snakefile:
   - rewrites the hardcoded `refgenie1` command token to `$REFGENIE_BIN`
     (default `refgenie` — refgenie1's installed entry point on Rivanna);
   - pins the relative `configfile:`/`pepfile:` paths to absolute repo paths.
3. `snakemake --profile build/profiles/rivanna/` builds the DAG from
   [`pep/config.yaml`](../pep/config.yaml): a `genome_init` rule per genome,
   then one `build_<asset>` rule per requested asset. Each rule is its own SLURM
   job.
4. `update_index.py` refreshes `index/` from the DB. (`index/manifest.yaml` is
   regenerated separately by CI.)

## The build queue (PEP)

[`pep/samples.csv`](../pep/samples.csv) — **one row per `(genome, asset)`**.
Rows sharing a `sample_name` are collated by peppy, so each genome's
`asset_group_name` becomes the list of assets to build for it.

| Column | Meaning |
|--------|---------|
| `sample_name` | Genome key (rows with the same value are collated). |
| `genome_name` | Genome name passed to `refgenie genome init` / `refgenie build`. |
| `asset_group_name` | One recipe to build. |
| `fasta_file_path` | `default_input` → derived to `$REFGENIE_INPUTS/<genome>/<genome>.fa`. |

The shipped queue contains one tiny test genome (`t7` phage) so the pipeline can
be exercised cheaply.

## Running it

```bash
# Dry run — import + render + DAG only, no jobs submitted (safe anywhere):
REFGENIE_INPUTS=/path/to/fastas DRY_RUN=1 bash build/run_builds.sh

# Real dispatch on Rivanna (mobot does this nightly):
bash build/run_builds.sh
```

Environment (see [`infra/rivanna/env.sh`](../infra/rivanna/env.sh)):

| Var | Default | Meaning |
|-----|---------|---------|
| `REFGENIE_INPUTS` | `$REFGETSTORE_FASTA` | Root of input FASTAs. |
| `REFGENIE_DB_CONFIG_PATH` | refgenie1 default | Persistent build DB. |
| `REFGENIE_BIN` | `refgenie` | Build-command binary in the Snakefile. |
| `DRY_RUN` | `0` | `1` = `snakemake -n`, no submission, no index update. |

## Prerequisite for a *real* asset build

`refgenie genome init` / the `fasta` recipe read the genome from the RefgetStore,
so a genome must first be built into the store by [`stores/build.py`](../stores/)
(the first mobot executor) before its assets can be built here (the second
executor). The dry run does not require this.
