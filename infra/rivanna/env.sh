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
