# RefgetStore registry environment.
# Source before building, e.g. from the stores/ directory:
#   source ../infra/rivanna/env.sh
# (the SLURM jobs in this folder source it by absolute path).

# Local build output directory (Rivanna brickyard).
export REFGETSTORE_BASE=/project/shefflab/brickyard/datasets_downloaded/refgenomes_fasta/refget-store

# Root of the staged input FASTAs. Stores' sources.csv hold paths relative to
# `$REFGETSTORE_FASTA/<store>` (see each store's `fasta_root:` in project_config.yaml),
# so the absolute Rivanna location lives only here.
export REFGETSTORE_FASTA=/project/shefflab/brickyard/datasets_downloaded/refgenomes_fasta/fasta

# S3 sync target.
export REFGETSTORE_S3=s3://refgenie/refget-store

# Absolute path to the host refgenie (refgenie1) entry point used by the build
# rules. MUST be the real host binary, NOT a bulker shim: the mobot driver job
# runs under `bulker activate databio/lab`, so a bare `command -v refgenie`
# resolves to an EPHEMERAL bulker shim under /scratch/.../bulker_XXXX/ that does
# not exist in the snakemake-submitted SLURM build children (genome_init then
# fails with "command exited with non-zero exit code"). Pin the host wrapper so
# run_builds.sh substitutes a stable absolute path into the generated Snakefile.
export REFGENIE_BIN="${REFGENIE_BIN:-/home/ns5bc/.local/bin/refgenie}"

# Persistent refgenie1 build catalog (SQLite) + its DB config. This is
# refgenie1's durable metadata store that drives the build->stage->push
# lifecycle; it MUST persist across nightly runs, not be wiped. Co-locate it on
# brickyard next to the genome store and the genome_init sentinels it must stay
# consistent with (a nightly git pull/clean on the mobot host would blow away
# anything kept inside the repo checkout). run_builds.sh mkdir -p's the parent
# and writes the DB config here each run (idempotent); recipes are synced
# idempotently and genomes are reconciled so the catalog self-heals.
export REFGENIE_BUILD_DB="${REFGENIE_BUILD_DB:-/project/shefflab/brickyard/results_pipeline/refgenie/catalog/refgenie_build.sqlite}"
export REFGENIE_DB_CONFIG_PATH="${REFGENIE_DB_CONFIG_PATH:-/project/shefflab/brickyard/results_pipeline/refgenie/catalog/refgenie_build_db_config.yaml}"
