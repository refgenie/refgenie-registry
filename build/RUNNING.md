# Running the build pipeline manually (Rivanna)

The nightly `refgenie-registry-build` mobot job runs `build/run_builds.sh` as a
SLURM driver. Sometimes you need to run it **by hand**, to test a change, force
a rebuild, or do a scoped re-push, without waiting for the nightly. This is how.

## Where the build tools come from

Each recipe names its container image in `docker_image`, and that is the only
record of which image builds the asset. Every build rule runs
`refgenie build <genome>/<asset> --stage --container ...`, and refgenie runs the
recipe's commands inside that image with Apptainer (`module load apptainer`).
It reads the tool version the asset is named after inside the same image. A
recipe with no image (`fasta`, the `cp` and `awk` recipes) builds on the host.
Apptainer caches the images under `$APPTAINER_CACHEDIR` (set in
`infra/rivanna/env.sh`); the first use of an image pulls it.

Real builds go through `build/run_builds.sh`, which regenerates the Snakefile
and fans out one SLURM job per asset. To check a change on one asset without a
full run, see [Checking one asset](#checking-one-asset) below.

## What is shared vs. what the checkout provides

The **catalog** (`refgenie_build.sqlite`) and the **stage folder** live on
brickyard and are pointed to by `infra/rivanna/env.sh`, they are shared no
matter which checkout you run from. The checkout only provides the code:
`run_builds.sh`, `recipes/`, `asset_classes/`, `pep/samples.csv`, the snakemake
profile (`build/profiles/rivanna`), and `build/update_index.py`.

Key paths (from `infra/rivanna/env.sh`):

| Thing | Path |
|---|---|
| Build catalog | `/project/shefflab/brickyard/results_pipeline/refgenie/catalog/refgenie_build.sqlite` |
| DB config | `.../catalog/refgenie_build_db_config.yaml` |
| Asset S3 prefix | `s3://refgenie/assets` |
| Working `aws` (host aws is broken) | `/apps/software/standard/core/awscli/2.35.13/bin` |
| Deployed `refgenie` (editable) | `/home/ns5bc/.local/bin/refgenie` → `~/deploy/refgenie1` |

## Prerequisites

1. **The code behavior you're testing must be deployed.** The build/stage/push
   logic is in **refgenie1**, run from `~/deploy/refgenie1` (editable install, so
   updating the source updates the running CLI, no reinstall). Put it on the
   commit you want:
   ```
   cd ~/deploy/refgenie1 && git fetch origin && git merge --ff-only origin/<branch>
   ```
   `refgenie-registry` changes (`run_builds.sh`, recipes, `update_index.py`) come
   from the **checkout you run from** (below).

2. **Snapshot the catalog** before anything destructive:
   ```
   cd /project/shefflab/brickyard/results_pipeline/refgenie/catalog
   cp refgenie_build.sqlite refgenie_build.sqlite.pre-<label>-$(date +%Y%m%d-%H%M%S)
   ```

## Scoping a rebuild: remove, then let snakemake refill

`run_builds.sh` builds the whole PEP, but snakemake only rebuilds **missing**
assets. So to rebuild a subset, remove exactly those assets first, everything
else is left in place (already built → skipped; already pushed → skipped):

```
export REFGENIE_DB_CONFIG_PATH=/project/shefflab/brickyard/results_pipeline/refgenie/catalog/refgenie_build_db_config.yaml
/home/ns5bc/.local/bin/refgenie remove <genome>/<asset_group> --force
```

`remove` cascades: asset row, on-disk content, seek keys, the RemoteAssetLink,
and (if last in its group) the asset group + alias files. It does **not** delete
the S3 object (S3 delete is unimplemented), so old name-based objects linger as
orphans, harmless; a separate cleanup can remove them later.

Removing an asset makes its next build a fresh stage + push. Under
content-addressed storage, the rebuild stages `{genome_digest}/{group}/{asset_digest}.tgz`
and pushes to that key.

## Submitting the driver

`run_builds.sh` is a lightweight DRIVER (it just dispatches SLURM jobs and polls),
so 2 cores / 8 GB is plenty; the wall-clock must exceed the longest dependency
chain (genome_init → fasta → suffixerator_index), not the longest single build.
Submit it as its own SLURM job so it survives a disconnect:

```
sbatch -p standard-rivanna -A shefflab -c 2 --mem 8G -t 02:00:00 \
  -J refgenie-registry-build --dependency=singleton \
  -o ~/refgenie-build-manual-%j.out \
  --wrap 'source /etc/profile.d/modules.sh; \
          module load apptainer; module load miniforge/26.9.4; \
          cd /home/ns5bc/mobot/checkouts/refgenie-registry && bash build/run_builds.sh'
```

Notes:
- **The job name matches the nightly's** (`refgenie-registry-build`), so
  `--dependency=singleton` queues a manual run behind a running nightly, and
  the nightly behind it, instead of both driving snakemake in one checkout.
  `run_builds.sh` also takes a lock and exits if another driver holds it.
- **`module load apptainer` is load-bearing** and must be in the submitted script
  (mirrors `jobs.d/refgenie-registry-build.json` → `mobot.setup`): the build jobs
  inherit the driver's PATH, and refgenie runs every recipe image with it.
- **`run_builds.sh` resolves `refgenie`, `snakemake`, and `aws` to absolute
  paths itself** (SLURM children get a bare PATH), and sources `infra/rivanna/env.sh`,
  so you don't set those. snakemake is the build venv's, which has the slurm
  executor plugin.
- **Bump the driver time up to `08:00:00` for a full mammalian rebuild**
  (hg38/mm39 suffixerator alone needs ~480 min at the job level). For the 5 small
  genomes, 02:00:00 is ample.
- **DRY RUN first** to preview the DAG without submitting: prepend `DRY_RUN=1` to
  the `bash build/run_builds.sh` (or run it directly on a login shell, the
  dry-run does no heavy work and prints the job DAG + a push preview).
- The mobot checkout (`$CHECKOUTS/refgenie-registry`) is git-only and gets
  `git reset --hard` at the start of each nightly. Running from it by hand is fine
  between nightlies, but your local edits there will be wiped; use a private
  checkout if you need edits to persist.

## Checking one asset

`run_builds.sh` is the whole nightly: besides the builds it loads changed
stores, pushes, refreshes `index/`, and publishes the store and the catalog to
S3. To check a refgenie or recipe change on one asset, build just that asset
instead. Use a small genome (`t7`, `rCRSd`) and a version-named recipe
(`bowtie2_index`, `bwa_index`), so the name probe is exercised too.

1. Snapshot the catalog (see Prerequisites).
2. Regenerate the Snakefile without building anything. `DRY_RUN=1` imports the
   recipes, writes and patches `build/Snakefile`, and resolves every queued
   recipe's asset name inside its image, then stops:
   ```
   cd /home/ns5bc/mobot/checkouts/refgenie-registry
   module load apptainer miniforge/26.9.4
   DRY_RUN=1 bash build/run_builds.sh
   ```
3. Remove the asset so snakemake sees it missing (see above), e.g.
   `refgenie remove t7/bowtie2_index --force` with `REFGENIE_DB_CONFIG_PATH`
   exported.
4. Build it as a one-target snakemake run, then push it. The target is the
   asset's build flag, `<genome folder>/builds/<genome>/<group>/<asset name>/<genome>_<group>__<asset name>.flag`:
   ```
   sbatch -p standard -A shefflab -c 1 --mem 4G -t 01:00:00 \
     -J refgenie-build-check -o ~/refgenie-build-check-%j.out \
     --wrap 'source /etc/profile.d/modules.sh; module load apptainer miniforge/26.9.4; \
             cd /home/ns5bc/mobot/checkouts/refgenie-registry && source infra/rivanna/env.sh; \
             export PATH=$REFGENIE_VENV/bin:$PATH REFGENIE_INPUTS=$REFGETSTORE_FASTA REFGENIE_REGISTRY_DIR=$PWD; \
             cd $REFGENIE_BUILD_WORKDIR && $SNAKEMAKE_BIN --snakefile $REFGENIE_REGISTRY_DIR/build/Snakefile \
               --profile $REFGENIE_REGISTRY_DIR/build/profiles/rivanna \
               $REFGENIE_GENOME_FOLDER/builds/t7/bowtie2_index/2.5.5/t7_bowtie2_index__2.5.5.flag \
             && $REFGENIE_BIN push --strategy per_asset'
   ```
   The build job's log (under `$REFGENIE_BUILD_WORKDIR/.snakemake/slurm_logs/`)
   shows the `apptainer exec ... docker://<image>` line for each recipe command.

## Monitoring

```
sacct -S $(date +%Y-%m-%d) -X --format=JobID,JobName%28,State,Elapsed
tail -f ~/refgenie-build-manual-<jobid>.out          # driver log
# per-asset build logs:
ls .../build_workdir/.snakemake/slurm_logs/rule_build_*/
```

The driver runs: snakemake fan-out → `refgenie push --strategy per_asset` →
`build/update_index.py`. Push and index are GATED: a failed push skips the index
refresh (so index/ never advertises an asset that isn't in S3).

## Verifying content-addressed output

```
GEN=<genome_digest>      # e.g. yeast_s288c = ia9g4Myony-xxLsSc7eQOC_NdClGgPGu
export AWS_PROFILE=refgenie
export PATH=/apps/software/standard/core/awscli/2.35.13/bin:$PATH
aws s3 ls s3://refgenie/assets/$GEN/<asset_group>/    # expect {content_digest}.tgz, NOT {name}.tgz
```

Round-trip a pull (through-server; verifies the tarball checksum after download):

```
/home/ns5bc/.local/bin/refgenie pull <genome>/<asset_group>
```

The index entry (`index/<genome>/<asset_group>.yaml`, committed to
`refgenie-registry` master by the nightly) should carry `asset_digest`.
