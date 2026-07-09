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

# S3 sync target for the refget SEQUENCE store (content-addressable sequences).
# This is the RefgetStore artifact, NOT built refgenie assets. Do not overload it.
export REFGETSTORE_S3=s3://refgenie/refget-store

# Built-asset publish target for `refgenie push` — DISTINCT from REFGETSTORE_S3.
# This is where the nightly registry build uploads STAGED refgenie assets (the
# <genome_digest>/<group>/<asset> tree), NOT the sequence store. It MUST equal
# the asset Remote.prefix registered by tools/import_recipes.py and the
# `--push-to <prefix>` token injected into the generated Snakefile, so
# ArchiveManager.create resolves the remote at stage time and refgenie push
# substitutes it for {prefix} in the push_command.
export REFGENIE_ASSET_S3="${REFGENIE_ASSET_S3:-s3://refgenie/assets}"

# AWS auth for `refgenie push`. Push runs ONCE on the mobot driver/dispatcher
# host AFTER snakemake returns — it reads the shared build DB + the staged
# assets on brickyard and runs `aws s3 sync`. It is NOT a per-SLURM-child step,
# so credentials only need to exist on the driver host (this box), not on the
# compute nodes.
#
# Profile: the ns5bc driver's ~/.aws/credentials [refgenie] profile is the
# RefgenieDataBot IAM user (acct 721148182619) — the only profile with R/W on
# the s3://refgenie bucket. The `default` profile (s3user, acct 235728444054) is
# a DIFFERENT account and gets AccessDenied on this bucket, so pin the profile
# explicitly (verified: put/ls/rm on s3://refgenie/assets/ succeed under it).
export AWS_PROFILE=refgenie

# aws CLI: ~/.local/bin/aws is BROKEN on this host (its shebang points at a
# removed anaconda python -> "bad interpreter"), and it shadows everything else
# on PATH. Prepend the Lmod awscli module's self-contained binary so a bare
# `aws` (as invoked by the folder_sync push_command) resolves to a working CLI
# WITHOUT relying on `module load` (non-interactive shells don't auto-load Lmod).
# Verified working by absolute path: aws-cli/2.35.13. Bump the version here if
# the module is upgraded/removed.
export PATH="/apps/software/standard/core/awscli/2.35.13/bin:$PATH"

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
