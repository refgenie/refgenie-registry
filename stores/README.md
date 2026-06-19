# RefgetStores

> Part of [refgenie-registry](../README.md). See the top-level README for the
> repo layout (`genomes/`, `recipes/`, `index/`, `schema/`, `tools/`).

Each subfolder defines a refgetstore — a content-addressable sequence collection. Store folders contain source manifests and any store-specific scripts. The core build tooling is in the `refget`/`gtars` packages.

## Stores

| Store | Contents |
|-------|----------|
| **jungle** | Reference genome jungle paper dataset |
| **pangenome** | HPRC pangenome haplotypes |
| **vgp** | VGP vertebrate genomes |
| **refseq** | NCBI protein + transcript sequences |
| **vrs** | VRS allele identification reference sequences |
| **salmon_txomes** | Salmon/tximeta transcriptomes |
| **igenomes** | AWS iGenomes reference genomes |
| **plantref** | Plant + model-organism genomes (recovered from legacy big.databio.org) |
| **demo** | Test data for development |

## Per-store structure

Each store folder is a PEP project:

```
store_name/
├── README.md            # what's in this store
├── sources.csv          # sample table: one FASTA per row (URL, or path relative to fasta_root)
└── project_config.yaml  # PEP config (+ optional `fasta_root:` / `aliasing:`, see below)
```

The store-build scripts in this directory are generic — they take a store name
and read everything store-specific from that store's `project_config.yaml` /
`sources.csv`. Two optional `project_config.yaml` keys configure them:

- **`fasta_root:`** — base directory for **relative** `fasta` paths in `sources.csv`.
  Environment variables are expanded, so the absolute machine location stays out of
  the committed data: e.g. `fasta_root: $REFGETSTORE_FASTA/jungle` (with
  `$REFGETSTORE_FASTA` set by [`infra/rivanna/env.sh`](../infra/rivanna/env.sh)) lets
  `sources.csv` hold `homo_sapiens/ENA/.../GRCh38.fa.gz`. A `fasta` value that is a URL
  or an absolute path is used as-is and ignores `fasta_root`.
- **`aliasing:`** — non-default sequence-alias strategy (see [Aliases](#aliases-post-build)).

A store with genuinely bespoke logic can instead ship its own script in its
folder (e.g. [`vrs/build_aliases.py`](vrs/build_aliases.py)).

## Adding a new store

1. **Create the folder** `stores/<name>/`.

2. **Write `project_config.yaml`** (a PEP). Minimal:

   ```yaml
   pep_version: 2.1.0
   sample_table: sources.csv
   sample_table_index: fasta        # or another unique column, e.g. pep_sample_name
   ```

   Add `fasta_root:` if `sources.csv` uses relative local paths (see
   [Per-store structure](#per-store-structure)), and an `aliasing:` block if the
   store needs sequence aliases (see [Aliases](#aliases-post-build)).

3. **Write `sources.csv`** — one FASTA per row. Only the **`fasta`** column is
   required; each value is either a URL (`http(s)://`, `ftp://`, `s3://`) or a
   path relative to `fasta_root` (a row may list several space-separated FASTAs
   that get concatenated into one collection). Recommended columns, used for
   collision-free download caching and for collection aliases:
   `name`, `organism`, `source`, `genome_assembly`, `accession`.

4. **Write `README.md`** describing what's in the store.

5. **Validate** before building:

   ```bash
   python validate_sources.py <name>/sources.csv   # schema/structure
   python validate_files.py <name>                 # every fasta path/URL resolves
   ```

6. **Build** (on Rivanna; sets `$REFGETSTORE_*` first):

   ```bash
   source ../infra/rivanna/env.sh
   python build.py <name>                           # local
   sbatch --job-name=build-<name> infra/rivanna/build_store.slurm <name>   # or as a SLURM job
   ```

7. **Aliases** (optional, post-build) and **deploy to S3** — see the sections below.

## Building

```bash
source ../infra/rivanna/env.sh                # Set REFGETSTORE_BASE and REFGETSTORE_S3
python build.py demo            # Build one store
python build.py all             # Build all stores
python build.py jungle --sync   # Build and sync to S3
python build.py vgp -j 6        # Limit parallel ingest workers (default: $SLURM_CPUS_PER_TASK or 8)
```

Stores are built to `$REFGETSTORE_BASE/<store_name>` and synced to `$REFGETSTORE_S3/<store_name>`.
Ingest runs in parallel in Rust (`add_sequence_collections_from_fastas`); `-j`/`$SLURM_CPUS_PER_TASK`
sets the worker count. Memory is bounded (streaming), but high-sequence-count transcriptomes are
heavier per worker — drop `-j` if a build OOMs. Each build writes a **`build_report.json`** into the
store dir (start/end/duration, loaded/skipped/failed counts, n_collections/n_sequences, gtars/refget
versions, git rev, per-collection records).

On the HPC, submit via [`infra/rivanna/build_store.slurm`](../infra/rivanna/build_store.slurm) (8 CPU / 32 GB):
`sbatch --job-name=build-<store> infra/rivanna/build_store.slurm <store>`.

## Validation

```bash
python validate_sources.py jungle/sources.csv   # schema/structure of a sources.csv
python validate_files.py jungle                 # every fasta path/URL resolves (local exists / cached)
python validate_files.py all --check-urls        # also HEAD-probe uncached URLs
```

## Aliases (post-build)

Aliases are registered **after** building, as a separate step (build.py itself only adds the
collection-level `name`/`accession`/`genome_assembly` aliases from sources.csv). The sequence-alias
strategy is read from each store's `aliasing:` block in `project_config.yaml`; a store with no block
gets collection aliases only. Recognized keys:

```yaml
# stores/<store>/project_config.yaml
aliasing:
  seq_strategy: none | header_names | assembly_report
  header_namespace_col: source        # column naming the namespace (header_names)
  assembly_report_when_accession: true # also pull assembly-report aliases for accession rows
```

Run:

```bash
source ../infra/rivanna/env.sh
python build_aliases.py vgp       # strategy from vgp/project_config.yaml (assembly_report)
python build_aliases.py jungle    # strategy from jungle/project_config.yaml (header_names + accession cross-aliases)
python build_aliases.py vgp --dry-run            # preview without writing
python build_aliases.py jungle --seq-strategy header_names  # CLI override of the config
```

Notes:
- **vrs** is not config-driven — it ships its own [`vrs/build_aliases.py`](vrs/build_aliases.py) with
  VRS-specific namespace logic (Ensembl ENST/ENSP, multiple assembly versions). Run that directly.
- **vgp** fetches NCBI `assembly_report.txt` per accession (rate-limited). Reuse the already-staged
  reports at `$REFGETSTORE_BASE/../refget_staging/assembly_reports/` (`<accession>_assembly_report.txt`)
  to avoid re-downloading — pre-seed the script's `.assembly_reports_vgp/` cache from there.
- **jungle** uses per-authority namespaces (the `source` column: ucsc/ensembl/ncbi/ENA/...) for
  sequence aliases; only the ~24 rows with a GCA/GCF accession also get assembly-report cross-aliases.
- `build_aliases.py` strips the VRS `SQ.` prefix from level-2 digests before registering (the bare
  `sha512t24u` is the alias-index key); the legacy `register_aliases.py`/`backfill_*` scripts do not.

## Deploying to S3

Stores are served publicly from `s3://refgenie/refget-store/<store>/`
(`https://refgenie.s3.us-east-1.amazonaws.com/refget-store/<store>/`). Push runs **on Rivanna**
using its own `~/.aws` `refgenie` profile (authenticates as `RefgenieDataBot`) — **use
`--profile refgenie`**; the default profile (`s3user`) is AccessDenied on this bucket.

```bash
source ../infra/rivanna/env.sh
# aws CLI needs the module env (libffi) + the venv aws; the ~/.local/bin/aws is broken:
module load gcc/11.4.0 openmpi/4.1.4 python/3.11.4 && source ~/envs/refgetstore-analysis/bin/activate
aws s3 sync "$REFGETSTORE_BASE/vgp" "$REFGETSTORE_S3/vgp" --profile refgenie --delete
```

`--delete` makes S3 exactly mirror the local store (true replace; drops stale files). Large stores
(vgp ~378 GB) are best pushed as a background/SLURM job. Verify after:
`curl -sI https://refgenie.s3.us-east-1.amazonaws.com/refget-store/<store>/store_metadata.json`.

Note: `build.py --sync` runs `aws s3 sync` with the **default** profile, so it fails on this bucket —
either export `AWS_PROFILE=refgenie` first, or sync manually as above.

## Files & infrastructure

Build/validation tooling (env-agnostic Python) lives in this `stores/` directory.
The **Rivanna/HPC execution layer** — environment, SLURM jobs, and yoke config —
is isolated under [`../infra/rivanna/`](../infra/rivanna/) so it stays out of the
way of external viewers. See [`infra/rivanna/README.md`](../infra/rivanna/README.md).

Build/validation tooling (`stores/`):

| Path | Role |
|------|------|
| [`stores/build.py`](build.py) | Build one/all stores (parallel Rust ingest) |
| [`stores/build_aliases.py`](build_aliases.py) | Register sequence/collection aliases (post-build) |
| [`stores/validate_sources.py`](validate_sources.py) | Check a `sources.csv` schema/structure |
| [`stores/validate_files.py`](validate_files.py) | Check every FASTA path/URL resolves |
| [`stores/compare_store_to_sources.py`](compare_store_to_sources.py) | Diff a built store against its `sources.csv` |
| [`stores/fasta_naming.py`](fasta_naming.py) | Shared FASTA name-parsing helpers |
| [`download_fastas.py`](../download_fastas.py) | Helper to fetch source FASTAs into a download cache |

Rivanna/HPC execution layer ([`infra/rivanna/`](../infra/rivanna/)):

| Path | Role |
|------|------|
| [`infra/rivanna/env.sh`](../infra/rivanna/env.sh) | Sets `REFGETSTORE_BASE` / `REFGETSTORE_S3` — `source ../infra/rivanna/env.sh` before building |
| [`infra/rivanna/build_store.slurm`](../infra/rivanna/build_store.slurm) | SLURM job (8 CPU / 32 GB): `sbatch --job-name=build-<store> infra/rivanna/build_store.slurm <store>` |
| [`infra/rivanna/download_igenomes.slurm`](../infra/rivanna/download_igenomes.slurm) | SLURM job to stage iGenomes FASTAs |
| [`infra/rivanna/download_salmon_txomes.slurm`](../infra/rivanna/download_salmon_txomes.slurm) | SLURM job to stage Salmon transcriptomes |
| [`infra/rivanna/sync_to_s3.slurm`](../infra/rivanna/sync_to_s3.slurm) | SLURM job to `aws s3 sync` a store to S3 |
| [`yoke.toml`](../yoke.toml) | Yoke session config (at the repo root, so mutagen syncs the whole repo) |
