# RefgetStore registry environment.
# Source before building, e.g. from the stores/ directory:
#   source ../infra/rivanna/env.sh
# (the SLURM jobs in this folder source it by absolute path).

# Local build output directory (Rivanna brickyard).
export REFGETSTORE_BASE=/project/shefflab/brickyard/datasets_downloaded/refgenomes_fasta/refget-store

# S3 sync target.
export REFGETSTORE_S3=s3://refgenie/refget-store
