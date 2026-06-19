# RefgetStores

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
| **demo** | Test data for development |

## Per-store structure

Each store folder is a PEP project:

```
store_name/
├── README.md            # what's in this store
├── sources.csv          # sample table of FASTAs (paths or URLs)
└── project_config.yaml  # PEP config
```

## Building

```bash
source ../env.sh                # Set REFGETSTORE_BASE and REFGETSTORE_S3
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

On the HPC, submit via `build_store.slurm` (8 CPU / 32 GB):
`sbatch --job-name=build-<store> build_store.slurm <store>`.

## Validation

```bash
python validate_sources.py jungle/sources.csv   # schema/structure of a sources.csv
python validate_files.py jungle                 # every fasta path/URL resolves (local exists / cached)
python validate_files.py all --check-urls        # also HEAD-probe uncached URLs
```

## Aliases (post-build)

Aliases are registered **after** building, as a separate step (build.py itself only adds the
collection-level `name`/`accession`/`genome_assembly` aliases from sources.csv). Run:

```bash
source ../env.sh
python build_aliases.py vgp       # NCBI assembly-report based: insdc/refseq/ucsc seq + insdc/refseq collection aliases
python build_aliases.py jungle    # header-name seq aliases per source authority (+ accession cross-aliases where present)
python build_aliases.py vgp --dry-run   # preview without writing
```

Notes:
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
source ../env.sh
# aws CLI needs the module env (libffi) + the venv aws; the ~/.local/bin/aws is broken:
module load gcc/11.4.0 openmpi/4.1.4 python/3.11.4 && source ~/envs/refgetstore-analysis/bin/activate
aws s3 sync "$REFGETSTORE_BASE/vgp" "$REFGETSTORE_S3/vgp" --profile refgenie --delete
```

`--delete` makes S3 exactly mirror the local store (true replace; drops stale files). Large stores
(vgp ~378 GB) are best pushed as a background/SLURM job. Verify after:
`curl -sI https://refgenie.s3.us-east-1.amazonaws.com/refget-store/<store>/store_metadata.json`.

Note: `build.py --sync` runs `aws s3 sync` with the **default** profile, so it fails on this bucket —
either export `AWS_PROFILE=refgenie` first, or sync manually as above.
