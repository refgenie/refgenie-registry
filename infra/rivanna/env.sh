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

# Publish-catalog artifact (metadata for api.refgenie.org). The nightly runs
# `refgenie catalog-export` and uploads the artifact here; the server imports
# it from the matching public https URL (REFGENIE_CATALOG_URL in refgenie1
# deployment/task_defs/primary.json). REFGENIE_ASSET_HTTPS must be the public
# https face of REFGENIE_ASSET_S3 -- download links are minted from it.
export REFGENIE_CATALOG_S3="${REFGENIE_CATALOG_S3:-s3://refgenie/catalog}"
export REFGENIE_ASSET_HTTPS="${REFGENIE_ASSET_HTTPS:-https://refgenie.s3.us-east-1.amazonaws.com/assets}"

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
# on PATH, so a working `aws` must be put ahead of it. That PATH fix is applied
# in build/run_builds.sh (REFGENIE_AWS_BINDIR), NOT here: this file is loaded by
# yoke's env_files parser, which mangles a `PATH="...:$PATH"` self-reference and
# wipes the interactive session PATH. run_builds.sh sources this file in plain
# bash (both the real mobot nightly and the canaries), so the prepend belongs
# there where $PATH expands correctly and yoke never sees it.
export REFGENIE_AWS_BINDIR=/apps/software/standard/core/awscli/2.35.13/bin

# Neutral working directory for the snakemake build fan-out (snakemake's
# --directory). It MUST NOT contain an entry named after any tool subcommand:
# bulker's shimlink absolutizes a bare argument that matches a real path in the
# process CWD (to bind-mount it), so `bwa index ...` run from the registry root
# (which has an `index/` dir) turns `index` into `<cwd>/index` and bwa dies with
# "unrecognized command". Running the build from this empty, dedicated dir keeps
# the CWD collision-free. Literal path (no $VAR) so yoke's env_files parser does
# not mangle it. run_builds.sh mkdir -p's it.
export REFGENIE_BUILD_WORKDIR=/project/shefflab/brickyard/results_pipeline/refgenie/build_workdir

# Local home for per-store build reports (stores/build.py). These are operator
# provenance (hostname, absolute paths, tool versions, counts) that nothing
# consumes; they MUST stay out of the store dir, which is aws s3 sync'd to the
# public bucket. Kept alongside the other build artifacts here, never published.
# Literal path (no $VAR) so yoke's env_files parser does not mangle it.
export REFGENIE_BUILD_REPORTS_DIR=/project/shefflab/brickyard/results_pipeline/refgenie/build_reports

# Absolute path to the host refgenie (refgenie1) entry point used by the build
# rules. MUST be the real host binary, NOT a bulker shim: the mobot driver job
# runs under a bulker activation, so a bare `command -v refgenie`
# resolves to an EPHEMERAL bulker shim under /scratch/.../bulker_XXXX/ that does
# not exist in the snakemake-submitted SLURM build children (genome_init then
# fails with "command exited with non-zero exit code"). Pin the host wrapper so
# run_builds.sh substitutes a stable absolute path into the generated Snakefile.
#
# ASSIGNED UNCONDITIONALLY, not `${REFGENIE_BIN:-...}`. The fallback form was
# actively harmful here: yoke's env_files parser pre-exports a value it cached
# from an EARLIER version of this file, and `${VAR:-default}` then keeps that
# stale value instead of the one written below. Observed 2026-07-28, hours after
# this line was repointed at the venv -- a yoke shell that sourced this file
# still came out with REFGENIE_BIN=/home/ns5bc/.local/bin/refgenie, i.e. the
# shared ~/.local environment we had just finished escaping. The nightly was
# unaffected (mobot's env has the variable unset, so the default won), but a
# hand-run through yoke silently used the wrong interpreter, which is exactly
# the failure the venv exists to prevent.
#
# Nothing in this repo overrides these two, and both are hardcoded /home/ns5bc
# paths in a Rivanna-specific file, so the override the fallback bought was
# theoretical. Every other path in this file is already assigned unconditionally
# for the same reason. To point elsewhere, edit this file.
export REFGENIE_BIN=/home/ns5bc/envs/refgenie-build/bin/refgenie

# Absolute path to the host snakemake — the workflow DRIVER that submits the
# per-asset SLURM jobs. MUST be the host binary, NOT a bulker shim: the driver
# runs under `bulker activate databio/refgenie:1.1.0`, and under that a bare
# `snakemake` shims into a crate container whose snakemake lacks the SLURM
# executor plugin (--executor {local,dryrun,touch}) -> the driver dies with
# "invalid choice: 'slurm'" and no builds run. The host snakemake HAS the plugin.
# A SLURM-submitting driver belongs on the host anyway; the build rules still
# containerize via bulker inside `refgenie build`. Pin the host path.
#
# (Until 2026-07-23 the activation was the two-crate union
# `databio/lab,databio/refgenie:1.0.0`; the same shim hazard applied. It is now
# refgenie-only -- databio/refgenie:1.1.0 imports bulker/coreutils, so nothing
# needs databio/lab. Pinning the host path is what makes this line
# crate-agnostic, so it did not need to change with the switch.)
#
# Unconditional for the same reason as REFGENIE_BIN above -- see that comment.
export SNAKEMAKE_BIN=/home/ns5bc/envs/refgenie-build/bin/snakemake

# The build system's own virtualenv. Created and verified by
# infra/rivanna/setup_env.sh; see that script's header for why it exists.
#
# Both binaries above are entry points inside it, so pinning them is what makes
# the environment automatic: the nightly, a hand-run of build/run_builds.sh, and
# a one-off `stores/build.py` all get the same interpreter without anyone
# activating anything. Before this, they were `~/.local/bin` scripts on the
# cluster miniforge python, which meant every package came from the
# account-wide user site -- shared with every other python3.11 process on this
# account. That is how a 2026-07-18 gtars was still what the nightly imported
# on 2026-07-28, with no store write lock in it.
#
# Literal path, no $VAR expansion: yoke's env_files parser mangles
# self-referential and nested expansions in this file (see the PATH note above).
# Scripts that need the venv's `python` (not just the two pinned entry points)
# should prepend "$REFGENIE_VENV/bin" to PATH in plain bash, the same way
# run_builds.sh handles REFGENIE_AWS_BINDIR.
export REFGENIE_VENV=/home/ns5bc/envs/refgenie-build

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

# Genome + stage folders for the persistent build catalog.
#
# refgenie resolves BOTH of these from the DB `configuration` row at runtime
# (the Refgenie.genome_folder / .genome_stage_folder properties), and
# init_backend SKIPS its insert when a Configuration row already exists -- so
# these env vars do NOT steer where assets actually stage. What they do steer is
# refgenie.init(), which mkdir -p's whatever the env-derived config says before
# handing off to init_backend. Left unset that default is
# $HOME/.refgenie/{genomes,archives}, so every nightly logged "Genome stage
# folder ready: /home/ns5bc/.refgenie/archives" while actually staging to
# brickyard, and re-created two phantom empty dirs in $HOME each run. Setting
# them makes the log honest and keeps $HOME out of the build path entirely.
#
# The stage folder moved off $HOME on 2026-07-27. The 07-26 from-scratch rebuild
# staged 120 GB of tarballs there -- nothing prunes the stage dir after a
# successful push -- which filled the 200 GB home quota, and the 07-27 nightly
# then died 18 seconds in on `sed: write error` while patching the generated
# Snakefile. Note `archives` (plural, live push staging) is NOT the sibling
# `legacy_archive/` (673 dirs, ~1.8 TB, unreferenced pre-2026 payloads).
export REFGENIE_GENOME_FOLDER="${REFGENIE_GENOME_FOLDER:-/project/shefflab/brickyard/results_pipeline/refgenie/genomes}"
export REFGENIE_GENOME_STAGE_FOLDER="${REFGENIE_GENOME_STAGE_FOLDER:-/project/shefflab/brickyard/results_pipeline/refgenie/archives}"
