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
