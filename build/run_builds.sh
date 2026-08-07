#!/bin/bash
# build/run_builds.sh — refgenie-native recipe/asset build dispatch.
#
# This is the recipe-build layer of the nightly Rivanna pipeline (mobot job
# `refgenie-registry-build`, see lab.databio.org/mobot/jobs.d/). It is the
# refgenie-native counterpart to `stores/build.py` (which builds RefgetStores).
#
# Pipeline (per design.md §"refgenie is the build system"):
#   1. Load all asset_classes/ + recipes/ into a refgenie1 DB AND render the
#      Snakefile in one shot via tools/import_recipes.py (so the Snakefile is
#      generated from the SAME instance the recipes were loaded into — no
#      two-process DB mismatch).
#   2. Patch the generated Snakefile so its shell rules call the installed
#      `refgenie` binary (the refgenie1 template still emits `refgenie1`; on
#      Rivanna the entry point is `refgenie`). Override with $REFGENIE_BIN.
#   3. Run snakemake against the Rivanna SLURM profile to fan out one SLURM job
#      per (genome, asset) in pep/samples.csv. Each rule runs
#      `refgenie build <genome>/<asset> --stage --push-to <asset S3 prefix>`
#      inside the recipe's container; staging records a RemoteAssetLink push
#      intent (pushed=False) for each asset.
#   4. Push staged assets to S3 with `refgenie push` (once, on the driver host):
#      uploads every pushed=False link and marks it pushed. Idempotent.
#   5. Refresh index/ from whatever assets are now present (build/update_index.py).
#
# Conservative by default: set DRY_RUN=1 to do everything EXCEPT actually
# submit/run builds (snakemake -n). The nightly mobot job runs it for real.
#
# DRY_RUN=1 MUST NOT DESTROY ANYTHING. It is the command an operator reaches for
# while diagnosing a sick pipeline, so it has to be safe to run at the worst
# possible moment. Concretely: the reconcile step is passed --no-prune (it
# reports what it WOULD unlink and unlinks nothing) and the DB config is not
# rewritten. Known remaining side effects, deliberate because the dry run cannot
# render a Snakefile without them: tools/import_recipes.py syncs recipes and
# asset classes into the catalog (idempotent inserts, additive only), and the
# generated build/Snakefile is written and sed-patched in place. Neither touches
# genome/alias rows or the .genome_init_complete sentinels. If you add a step
# here, keep it above the DRY_RUN branch ONLY if it is read-only.
#
# Env (see infra/rivanna/env.sh + the snakemake profile):
#   REFGENIE_INPUTS   required by the Snakefile/PEP (root of input FASTAs).
#   REFGENIE_DB_CONFIG_PATH  refgenie1 DB config for the persistent build
#                     catalog. REQUIRED, absolute, and must already exist —
#                     there is NO fallback (see the validation block below).
#   REFGENIE_BUILD_DB the persistent catalog SQLite file. Same rules.
#   REFGENIE_BIN      build-command binary name (default: refgenie).
#   SNAKEMAKE_BIN     workflow-driver binary; pin to the HOST snakemake so the
#                     driver isn't a bulker shim missing the slurm executor.
#   REFGENIE_ASSET_S3 S3 prefix for built-asset push (e.g. s3://refgenie/assets).
#                     When set, build rules get --push-to and a `refgenie push`
#                     step runs after the fan-out. Unset => stage-only (no push).
#   DRY_RUN=1         snakemake dry-run only (no jobs submitted).
#   SNAKEMAKE_PROFILE override the profile dir (default: build/profiles/rivanna).

set -euo pipefail

REGISTRY_DIR="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
cd "$REGISTRY_DIR"

# --- environment ---------------------------------------------------------
if [[ -f infra/rivanna/env.sh ]]; then
    # shellcheck disable=SC1091
    source infra/rivanna/env.sh
fi

# Put the build venv's bin FIRST, so a bare `python` in this script or anything
# it calls is the build system's interpreter and not the cluster miniforge one
# (whose packages come from the account-wide ~/.local user site). REFGENIE_BIN
# and SNAKEMAKE_BIN are already absolute paths into this venv; this covers
# everything else. Same reasoning as the aws prepend below: the bin dir comes
# from env.sh, but the PATH line lives here in plain bash because yoke's
# env_files parser mangles a self-referential PATH inside env.sh.
if [[ -n "${REFGENIE_VENV:-}" && -x "$REFGENIE_VENV/bin/python" ]]; then
    case ":$PATH:" in
        *":$REFGENIE_VENV/bin:"*) ;;
        *) export PATH="$REFGENIE_VENV/bin:$PATH" ;;
    esac
    echo "$(date) | run_builds: using build venv $REFGENIE_VENV"
else
    echo "$(date) | run_builds: FATAL build venv missing or incomplete (REFGENIE_VENV=${REFGENIE_VENV:-unset})." >&2
    echo "$(date) | run_builds:   create it with: bash infra/rivanna/setup_env.sh" >&2
    exit 1
fi

# --- guard: pep/samples.csv must be generated from pep/build_matrix.yaml ---------
# samples.csv is a GENERATED artifact: build/generate_samples.py reads
# pep/build_matrix.yaml (the single source of truth for the per-genome asset queue) and
# emits samples.csv. Committing samples.csv IS launching the builds, so a
# hand-edit of the CSV -- or a build_matrix.yaml change whose samples.csv was never
# regenerated -- must never reach dispatch. Regenerate here and fail the nightly
# loudly on ANY diff. This runs in DRY_RUN too: a stale/hand-edited queue is
# exactly what a dry run should surface, and regenerating an already-correct file
# is a byte no-op (nothing to destroy). generate_samples.py also runs the source
# and dependency-closure validations, so an invalid build_matrix.yaml aborts here.
echo "$(date) | run_builds: regenerating pep/samples.csv from pep/build_matrix.yaml"
if ! python3 build/generate_samples.py; then
    echo "$(date) | run_builds: FATAL generate_samples.py failed -- pep/build_matrix.yaml is invalid." >&2
    echo "  Fix build_matrix.yaml (see the error above); refusing to build." >&2
    exit 1
fi
if ! git diff --exit-code pep/samples.csv; then
    echo "$(date) | run_builds: FATAL pep/samples.csv is out of sync with pep/build_matrix.yaml." >&2
    echo "  samples.csv is GENERATED -- never hand-edit it. Either the CSV was edited" >&2
    echo "  directly, or build_matrix.yaml changed without regenerating. Run" >&2
    echo "    python build/generate_samples.py" >&2
    echo "  review the samples.csv diff (it IS the go/no-go build gate), and commit both." >&2
    exit 1
fi
echo "$(date) | run_builds: pep/samples.csv is in sync with pep/build_matrix.yaml"

# Put a working `aws` ahead of the broken host ~/.local/bin/aws (dead-anaconda
# shebang) so the folder_sync push_command resolves a real CLI. The bin dir
# comes from env.sh ($REFGENIE_AWS_BINDIR) but the PATH prepend lives HERE, in
# plain bash, because a `PATH="...:$PATH"` line inside env.sh gets mangled by
# yoke's env_files parser. This runs in the mobot nightly AND the canaries.
if [[ -n "${REFGENIE_AWS_BINDIR:-}" && -x "$REFGENIE_AWS_BINDIR/aws" ]]; then
    case ":$PATH:" in
        *":$REFGENIE_AWS_BINDIR:"*) ;;
        *) export PATH="$REFGENIE_AWS_BINDIR:$PATH" ;;
    esac
    echo "$(date) | run_builds: prepended aws bindir $REFGENIE_AWS_BINDIR to PATH"
else
    echo "$(date) | run_builds: WARNING no working aws at \$REFGENIE_AWS_BINDIR (${REFGENIE_AWS_BINDIR:-unset}); push may fail" >&2
fi

# REFGENIE_INPUTS is required by the generated Snakefile (envvars: stanza) and
# by the PEP sample modifier that derives fasta_file_path. Default it to the
# registry's own genomes input root if the operator did not set one.
export REFGENIE_INPUTS="${REFGENIE_INPUTS:-${REFGETSTORE_FASTA:-$REGISTRY_DIR/build/inputs}}"

# Resolve refgenie to an ABSOLUTE path. snakemake submits each build rule as its
# own `srun` SLURM child whose non-interactive, non-login shell does NOT inherit
# the dispatcher's PATH (e.g. ~/.local/bin), so a bare `refgenie` token fails with
#   FATAL: "refgenie": executable file not found in $PATH
# Substituting the absolute path into the Snakefile makes every rule PATH-immune.
REFGENIE_BIN="${REFGENIE_BIN:-refgenie}"
if [[ "$REFGENIE_BIN" != /* ]]; then
    _refgenie_abs="$(command -v "$REFGENIE_BIN" 2>/dev/null || true)"
    if [[ -n "$_refgenie_abs" ]]; then
        REFGENIE_BIN="$_refgenie_abs"
        echo "$(date) | run_builds: resolved REFGENIE_BIN -> $REFGENIE_BIN"
    else
        echo "$(date) | run_builds: WARNING could not resolve absolute path for '$REFGENIE_BIN'; build rules may fail in SLURM children with PATH issues" >&2
    fi
fi
# Put the refgenie bin dir on PATH and EXPORT it. snakemake's SLURM executor
# sbatch's children with --export=ALL, so the driver's PATH propagates to every
# build job. This covers the recipe sub-commands too (e.g. `refgenie-build-fasta`,
# which the fasta recipe runs on the host) — not just the top-level `refgenie`.
if [[ "$REFGENIE_BIN" == /* ]]; then
    _refgenie_bindir="$(dirname "$REFGENIE_BIN")"
    case ":$PATH:" in
        *":$_refgenie_bindir:"*) ;;
        *) export PATH="$_refgenie_bindir:$PATH" ;;
    esac
    echo "$(date) | run_builds: PATH includes $_refgenie_bindir for SLURM children"
fi
# Resolve snakemake to an ABSOLUTE HOST path — same reasoning as REFGENIE_BIN,
# but for a different failure. The mobot driver runs under a bulker activation
# (databio/refgenie:1.1.0) so the build children can see the index builders
# (bowtie2-build/hisat2-build). But under it a bare `snakemake` resolves to a
# bulker SHIM that runs snakemake inside a crate container, whose snakemake
# LACKS the SLURM executor plugin (--executor {local,dryrun,touch}). The driver
# then dies with "argument --executor/-e: invalid choice: 'slurm'" and ZERO
# builds run.
# snakemake here is the workflow DRIVER (it submits SLURM jobs via sbatch); a
# SLURM-submitting driver belongs on the host, not in a container. The host
# snakemake (~/.local/bin/snakemake) HAS the slurm plugin. Pin
# it so the driver is shim-immune regardless of what crates are activated; the
# build RULES still containerize via bulker (each rule shells out to $REFGENIE_BIN
# build, and the slurm executor sbatches children with --export=ALL so the crate
# shims + BULKERCRATE propagate). Override with $SNAKEMAKE_BIN.
SNAKEMAKE_BIN="${SNAKEMAKE_BIN:-snakemake}"
if [[ "$SNAKEMAKE_BIN" != /* ]]; then
    _snakemake_abs="$(command -v "$SNAKEMAKE_BIN" 2>/dev/null || true)"
    if [[ -n "$_snakemake_abs" ]]; then
        SNAKEMAKE_BIN="$_snakemake_abs"
        echo "$(date) | run_builds: resolved SNAKEMAKE_BIN -> $SNAKEMAKE_BIN"
    else
        echo "$(date) | run_builds: WARNING could not resolve absolute path for '$SNAKEMAKE_BIN'; the driver may hit a bulker shim missing the slurm executor" >&2
    fi
fi

SNAKEMAKE_PROFILE="${SNAKEMAKE_PROFILE:-$REGISTRY_DIR/build/profiles/rivanna}"

BUILD_DIR="$REGISTRY_DIR/build"
SNAKEFILE="$BUILD_DIR/Snakefile"

# CRITICAL: the importer and the generated Snakefile (which builds its own
# `Refgenie()` at parse time) MUST share ONE DB. `Refgenie()` with no args reads
# $REFGENIE_DB_CONFIG_PATH, so we point that at a build-dedicated DB and export it
# for the snakemake subprocess. Without this the importer writes to a throwaway
# DB and the Snakefile can't find any recipes ("MissingRecipeError").
#
# This catalog is PERSISTENT and shared across nightly runs (and by every SLURM
# build child via the exported REFGENIE_DB_CONFIG_PATH). It is refgenie1's
# durable metadata store that drives the build->stage->push lifecycle, so it is
# NOT wiped each run. Instead:
#   - recipes/asset_classes are synced idempotently (import_recipes.py skips any
#     (name, version) already present), and
#   - genomes are reconciled (reconcile_genomes.py) so a fresh/empty catalog
#     always ends up with its genome + alias rows before any build stages.
# The default paths (see infra/rivanna/env.sh) live on brickyard, OUTSIDE the
# git checkout, so a nightly git pull/clean on the mobot host cannot destroy the
# catalog. Operators point REFGENIE_DB_CONFIG_PATH/REFGENIE_BUILD_DB elsewhere
# (e.g. a laptop) by EXPORTING them; there is deliberately no in-repo fallback.
#
# NO SILENT FALLBACK (2026-07-08). These two used to be `${VAR:-$BUILD_DIR/...}`.
# That fallback is a loaded gun: if the variable arrived unset or garbage, the
# run did not fail -- it quietly created a brand-new EMPTY catalog inside the git
# checkout and built against it. An empty catalog has no `genome` rows, so
# reconcile_genomes.py correctly concluded that every PEP genome was
# unregistered and pruned every sentinel. That is how one bad env var deletes
# the whole sentinel tree, and it is what happened on 2026-07-08 (the orphaned
# .refgenie_build.sqlite it created is still sitting in the old checkout).
#
# "Garbage" is not hypothetical either. yoke's env_files parser mangles the
# `${VAR:-default}` form in infra/rivanna/env.sh into a literal string that
# STARTS WITH ':-' (and can carry a trailing '}'). Such a value is non-empty, so
# `${VAR:-default}` happily accepts it and refgenie then opens a catalog at a
# nonsense relative path. Hence the explicit ':-' / '}' checks below: emptiness
# is not the only way this variable goes wrong.
#
# The file must ALREADY EXIST. Creating the catalog is a deliberate, one-time
# bootstrap, never a side effect of a build; set ALLOW_CATALOG_BOOTSTRAP=1 to
# opt into it explicitly.
_validate_catalog_var() {
    local name="$1" value="$2"
    if [[ -z "$value" ]]; then
        echo "$(date) | run_builds: FATAL $name is unset or empty." >&2
        return 1
    fi
    if [[ "$value" == :-* || "$value" == *"}"* ]]; then
        echo "$(date) | run_builds: FATAL $name looks mangled: '$value'" >&2
        echo "  A leading ':-' or a '}' means a \${VAR:-default} expansion was passed" >&2
        echo "  through literally -- yoke's env_files parser does this to env.sh." >&2
        echo "  Export a plain absolute path instead." >&2
        return 1
    fi
    if [[ "$value" != /* ]]; then
        echo "$(date) | run_builds: FATAL $name must be an ABSOLUTE path, got: '$value'" >&2
        return 1
    fi
    return 0
}

_CATALOG_DEFAULT_CONFIG=/project/shefflab/brickyard/results_pipeline/refgenie/catalog/refgenie_build_db_config.yaml
_CATALOG_DEFAULT_DB=/project/shefflab/brickyard/results_pipeline/refgenie/catalog/refgenie_build.sqlite

if ! _validate_catalog_var REFGENIE_DB_CONFIG_PATH "${REFGENIE_DB_CONFIG_PATH:-}" \
    || ! _validate_catalog_var REFGENIE_BUILD_DB "${REFGENIE_BUILD_DB:-}"; then
    echo "  The persistent refgenie1 build catalog must be named explicitly. Expected:" >&2
    echo "    REFGENIE_DB_CONFIG_PATH=$_CATALOG_DEFAULT_CONFIG" >&2
    echo "    REFGENIE_BUILD_DB=$_CATALOG_DEFAULT_DB" >&2
    echo "  Both are exported by infra/rivanna/env.sh, which this script sources." >&2
    echo "  If you saw this, that source failed or something overrode it." >&2
    exit 1
fi
export REFGENIE_DB_CONFIG_PATH
export REFGENIE_BUILD_DB

# Existence gate. An absent catalog is the empty-catalog failure mode in its
# most dangerous form: refgenie CREATES it on first touch, so nothing errors --
# the build just proceeds against zero genomes and prunes every sentinel.
if [[ ! -f "$REFGENIE_BUILD_DB" || ! -f "$REFGENIE_DB_CONFIG_PATH" ]]; then
    if [[ "${ALLOW_CATALOG_BOOTSTRAP:-0}" == "1" ]]; then
        echo "$(date) | run_builds: ALLOW_CATALOG_BOOTSTRAP=1 — creating a NEW EMPTY catalog." >&2
        echo "  Every PEP genome will look unregistered; every sentinel will be pruned" >&2
        echo "  and every genome re-initialized. This is correct ONLY for a first run." >&2
    else
        echo "$(date) | run_builds: FATAL persistent build catalog is missing." >&2
        [[ -f "$REFGENIE_BUILD_DB" ]]        || echo "  missing SQLite:    $REFGENIE_BUILD_DB" >&2
        [[ -f "$REFGENIE_DB_CONFIG_PATH" ]]  || echo "  missing DB config: $REFGENIE_DB_CONFIG_PATH" >&2
        echo "  Refusing to build: refgenie would CREATE an empty catalog here, in which" >&2
        echo "  no PEP genome is registered, and the reconcile step would then delete every" >&2
        echo "  .genome_init_complete sentinel under the alias tree (see 2026-07-08)." >&2
        echo "  If this really is a first-time bootstrap, re-run with:" >&2
        echo "    ALLOW_CATALOG_BOOTSTRAP=1 bash build/run_builds.sh" >&2
        echo "  Otherwise fix the path / restore the catalog from a sibling .bak in" >&2
        echo "  $(dirname "$_CATALOG_DEFAULT_DB")" >&2
        exit 1
    fi
fi

echo "$(date) | run_builds: REGISTRY_DIR=$REGISTRY_DIR"
echo "$(date) | run_builds: REFGENIE_INPUTS=$REFGENIE_INPUTS"
echo "$(date) | run_builds: REFGENIE_DB_CONFIG_PATH=$REFGENIE_DB_CONFIG_PATH"
echo "$(date) | run_builds: REFGENIE_BUILD_DB=$REFGENIE_BUILD_DB"
echo "$(date) | run_builds: REFGENIE_BIN=$REFGENIE_BIN  SNAKEMAKE_BIN=$SNAKEMAKE_BIN  DRY_RUN=${DRY_RUN:-0}"

# Keep the small DB config in sync with $REFGENIE_BUILD_DB. The sqlite file
# itself is NOT removed — it persists across runs and is updated in place.
#
# Written only when the content actually differs, and never under DRY_RUN: a dry
# run must not touch the filesystem, and a config whose `path:` disagrees with
# $REFGENIE_BUILD_DB is exactly the sort of drift an operator runs a dry run to
# discover. Reporting it beats silently repairing it mid-diagnosis.
mkdir -p "$(dirname "$REFGENIE_BUILD_DB")"
mkdir -p "$(dirname "$REFGENIE_DB_CONFIG_PATH")"
_desired_db_config="path: $REFGENIE_BUILD_DB
type: sqlite"
if [[ -f "$REFGENIE_DB_CONFIG_PATH" ]] && [[ "$(cat "$REFGENIE_DB_CONFIG_PATH")" == "$_desired_db_config" ]]; then
    echo "$(date) | run_builds: DB config already points at $REFGENIE_BUILD_DB"
elif [[ "${DRY_RUN:-0}" == "1" ]]; then
    echo "$(date) | run_builds: DRY RUN — WOULD rewrite $REFGENIE_DB_CONFIG_PATH to point at $REFGENIE_BUILD_DB" >&2
    echo "  (current content left untouched; a dry run does not repair drift)" >&2
else
    printf '%s\n' "$_desired_db_config" > "$REFGENIE_DB_CONFIG_PATH"
    echo "$(date) | run_builds: wrote DB config -> $REFGENIE_BUILD_DB"
fi
echo "$(date) | run_builds: using persistent build DB at $REFGENIE_BUILD_DB"

# --- 1. import recipes + render Snakefile (single refgenie1 instance) -----
# Import into the build DB AND render the Snakefile from that same instance.
# Recipe/asset-class import is idempotent (sync): anything already present is
# skipped, so re-importing into the populated persistent catalog is safe.
echo "$(date) | run_builds: importing asset_classes + recipes and generating Snakefile..."
python3 tools/import_recipes.py --db-config "$REFGENIE_DB_CONFIG_PATH" --snakefile "$SNAKEFILE"

# --- 2. patch the generated Snakefile -------------------------------------
# (a) refgenie1's template hardcodes `refgenie1` in shell rules; the installed
#     entry point is `refgenie`. Rewrite only the leading command token.
if [[ "$REFGENIE_BIN" != "refgenie1" ]]; then
    # Build rules emit `refgenie1 ...`; the genome_init sentinel rule emits a
    # literal `refgenie genome init ...`. Rewrite BOTH leading command tokens to
    # $REFGENIE_BIN (absolute path) so every rule is PATH-immune in SLURM children.
    # Use '#' delimiter because $REFGENIE_BIN may contain '/'.
    sed -i \
        -e "s#refgenie1 #$REFGENIE_BIN #g" \
        -e "s#\"refgenie genome init #\"$REFGENIE_BIN genome init #g" \
        "$SNAKEFILE"
    echo "$(date) | run_builds: patched Snakefile shell rules -> '$REFGENIE_BIN'"
fi
# (b) The template uses relative `configfile:`/`pepfile:` paths resolved against
#     snakemake's --directory. Pin them to this repo so the build works from any
#     working directory and without copying config.yaml to the repo root.
sed -i \
    -e "s#^configfile: \"config.yaml\"#configfile: \"$BUILD_DIR/config.yaml\"#" \
    -e "s#^pepfile: \"pep/config.yaml\"#pepfile: \"$REGISTRY_DIR/pep/config.yaml\"#" \
    "$SNAKEFILE"
echo "$(date) | run_builds: pinned configfile/pepfile paths in Snakefile"
# (c) refgenie1's Snakefile template emits the singular `--param name=value` flag
#     in build shell rules, but the installed `refgenie build` CLI expects the
#     PLURAL `--params name=value` (see `refgenie build --help`). Without this the
#     every build_* rule fails immediately with
#     "refgenie: error: unrecognized arguments: --param threads=4".
#     Trailing space anchors the match so an already-plural `--params ` is untouched.
sed -i "s/--param /--params /g" "$SNAKEFILE"
echo "$(date) | run_builds: patched Snakefile build flag --param -> --params"
# (d) Inject `--push-to <asset prefix>` into every staged build rule so staging
#     records a RemoteAssetLink(pushed=False) push-intent for each asset. Every
#     build_* rule's shell contains `--stage ` (trailing space); genome_init and
#     `rule all` do not, so this anchors only on build rules. The token MUST
#     equal the asset Remote.prefix registered by import_recipes.py. The single
#     quotes are literal inside the Snakefile's double-quoted Python shell
#     string, so snakemake hands the shell one clean `--push-to 's3://...'` arg.
if [[ -n "${REFGENIE_ASSET_S3:-}" ]]; then
    sed -i "s#--stage #--stage --push-to '$REFGENIE_ASSET_S3' #g" "$SNAKEFILE"
    echo "$(date) | run_builds: injected --push-to '$REFGENIE_ASSET_S3' into build rules"
else
    echo "$(date) | run_builds: REFGENIE_ASSET_S3 unset; builds will stage without push intent"
fi

# --- 2b. reconcile genomes with the persistent catalog --------------------
# The genome_init sentinels (under the persistent alias folder) can outlive the
# genome rows they represent (e.g. an earlier wipe, or a fresh catalog on a new
# machine). When that happens snakemake skips genome_init but the catalog has no
# `genome` row, so `refgenie build .../fasta --stage` dies with MissingGenomeError.
# reconcile_genomes.py prunes stale sentinels for any PEP genome NOT registered
# in the persistent catalog, forcing genome_init to re-run and repopulate the
# genome + alias rows before any build stages. It also prints catalog counts.
#
# DRY_RUN passes --no-prune. This call sits ~35 lines above the DRY_RUN
# early-exit, and until 2026-07-19 it ran unconditionally — so a dry run reached
# the unlink() long before it reached the branch meant to make it harmless, and
# one such invocation (issued while investigating MISSING sentinels) destroyed
# hg38's and yeast_s288c's. Nothing recreates a sentinel; the next nightly
# re-ran genome_init and marked every downstream asset stale.
#
# The flag is used rather than moving this call below the exit, because the
# ordering here is load-bearing: reconcile must run BEFORE the dispatch-safety
# check and before snakemake evaluates the genome_init sentinels. Moving it
# would drag the guard with it and change what a real run checks; --no-prune
# changes only the dry run, and only by making it read-only.
RECONCILE_ARGS=(--db-config "$REFGENIE_DB_CONFIG_PATH")
if [[ "${DRY_RUN:-0}" == "1" ]]; then
    RECONCILE_ARGS+=(--no-prune)
    echo "$(date) | run_builds: DRY RUN — reconcile is read-only (--no-prune); reporting what it WOULD prune"
fi
echo "$(date) | run_builds: reconciling genomes with persistent catalog..."
python3 build/reconcile_genomes.py "${RECONCILE_ARGS[@]}"

# Guard: for a real run, refuse to dispatch a build that is doomed to
# MissingGenomeError. After reconcile, a PEP genome is safe to build iff it is
# EITHER already registered in the catalog OR its genome_init sentinel is now
# absent (so snakemake's genome_init rule will run and register it). A genome
# that is still unregistered AND still sentinel-gated would have genome_init
# skipped and its build would crash at staging. reconcile_genomes.py exits
# non-zero from --check-dispatch-safe if any such genome remains. (A fresh
# catalog legitimately has genome=0 here: reconcile prunes all sentinels, so
# every genome is dispatch-safe and gets initialized during the snakemake run.)
if [[ "${DRY_RUN:-0}" != "1" ]]; then
    if ! python3 build/reconcile_genomes.py --db-config "$REFGENIE_DB_CONFIG_PATH" --check-dispatch-safe; then
        echo "$(date) | run_builds: FATAL — one or more PEP genomes are unregistered in the" >&2
        echo "  persistent catalog AND still sentinel-gated, so genome_init would be skipped" >&2
        echo "  and their builds would fail with MissingGenomeError. Aborting before dispatch." >&2
        echo "  Check genome_init inputs/logs and the reconcile output above." >&2
        exit 1
    fi
fi

# --- 3. dispatch builds via snakemake ------------------------------------
# Run from a neutral working dir, NOT the registry root: the registry has an
# `index/` dir, and bulker's shimlink absolutizes a bare `index` arg that matches
# a CWD path, breaking `bwa index`. All snakemake inputs are absolute (snakefile,
# profile, pinned config/pepfile, REFGENIE_INPUTS, genome outputs), so the
# working dir is free to move. Falls back to the registry root if unset.
BUILD_WORKDIR="${REFGENIE_BUILD_WORKDIR:-$REGISTRY_DIR}"
mkdir -p "$BUILD_WORKDIR"
echo "$(date) | run_builds: snakemake --directory=$BUILD_WORKDIR"
SNAKEMAKE_ARGS=(
    --snakefile "$SNAKEFILE"
    --directory "$BUILD_WORKDIR"
)
if [[ "${DRY_RUN:-0}" == "1" ]]; then
    echo "$(date) | run_builds: DRY RUN (snakemake -n)"
    "$SNAKEMAKE_BIN" "${SNAKEMAKE_ARGS[@]}" -n
    # Preview the push without uploading. With a fresh/empty DB (no builds
    # executed) there may be nothing to preview; handle_push prints "Nothing to
    # push" and returns cleanly.
    if [[ -n "${REFGENIE_ASSET_S3:-}" ]]; then
        echo "$(date) | run_builds: previewing push (dry-run)"
        "$REFGENIE_BIN" push --strategy per_asset --dry-run \
            || echo "$(date) | run_builds: push --dry-run preview failed (non-fatal)"
    fi
    echo "$(date) | run_builds: dry run complete; skipping index update"
    exit 0
fi

echo "$(date) | run_builds: dispatching builds with profile $SNAKEMAKE_PROFILE"
# Capture snakemake's exit status instead of letting `set -e` abort here. With
# keep-going, snakemake builds every asset it can and returns non-zero if ANY
# rule failed. We must still reach the push step below so the assets that DID
# build get uploaded -- one failing recipe must not block publishing the
# successful ones. The status is preserved and re-raised at the very end so the
# nightly still reports failure to the monitor.
snakemake_rc=0
"$SNAKEMAKE_BIN" "${SNAKEMAKE_ARGS[@]}" --profile "$SNAKEMAKE_PROFILE" || snakemake_rc=$?
if [[ "$snakemake_rc" -ne 0 ]]; then
    echo "$(date) | run_builds: snakemake exited $snakemake_rc (one or more builds failed); continuing to push the assets that succeeded"
fi

# --- 4. push staged assets to S3 -----------------------------------------
# Push runs ONCE here on the driver (coordinator) host AFTER the snakemake
# fan-out returns -- deliberately NOT inside the per-asset build jobs, which
# hold requested compute cores that would sit idle burning the allocation during
# what is only network transfer. It reads the shared build DB
# ($REFGENIE_DB_CONFIG_PATH) and uploads each RemoteAssetLink(pushed=False)
# individually (per_asset strategy): one asset at a time, marking a link
# pushed=True only after its own upload succeeds. This makes each asset atomic --
# archive assets upload as a single .tgz object, and a failed or interrupted
# upload leaves that one asset pushed=False for the next run without touching the
# others. per_asset also uploads only assets the DB recorded as successfully
# staged, unlike folder_sync which syncs whatever files happen to sit in the
# stage folder. Use the absolute $REFGENIE_BIN so push is PATH-immune on the
# driver (same as the build rules). Non-fatal, consistent with the index step.
push_rc=0
if [[ -n "${REFGENIE_ASSET_S3:-}" ]]; then
    echo "$(date) | run_builds: pushing staged assets -> $REFGENIE_ASSET_S3"
    "$REFGENIE_BIN" push --strategy per_asset || push_rc=$?
    if [[ "$push_rc" -ne 0 ]]; then
        echo "$(date) | run_builds: push exited $push_rc; links stay pushed=False for retry"
    fi
else
    echo "$(date) | run_builds: REFGENIE_ASSET_S3 unset; skipping push"
fi

# --- 5. refresh the index ------------------------------------------------
# GATED ON PUSH. update_index.py writes `build: {status: complete}` for every
# asset in the build DB with no push awareness whatsoever, and the mobot job
# then git-commits and pushes index/ to refgenie-registry master. So a failed
# upload used to publish an index entry advertising an asset that is not in
# s3://refgenie/assets -- a client resolving it gets a 404. That happened on
# 2026-07-21 (job 17162885): "Push complete: 0 succeeded, 6 failed", and
# 769ccbd committed 18 entries anyway.
#
# Refusing to refresh the index leaves index/ byte-identical to the commit it
# was reset to, so the mobot git_commit step finds nothing staged and pushes
# nothing. Stale-but-true beats fresh-but-dangling: every entry already in
# index/ describes an asset that was successfully pushed at the time.
#
# This gate is deliberately COARSE -- one failed asset holds back the whole
# index refresh. Per-asset gating is the right end state (RemoteAssetLink
# carries a per-asset `pushed` flag, queryable via
# ConfigurationManager.get_unpushed_links), but write_index() iterates
# rg.asset.list_all(), which does not expose the asset_digest needed to join
# against those links. Wiring that mapping through is a separate change.
if [[ "$push_rc" -ne 0 ]]; then
    echo "$(date) | run_builds: SKIPPING index update -- push failed (rc=$push_rc)." >&2
    echo "  Refreshing index/ now would publish entries for assets that are not" >&2
    echo "  in $REFGENIE_ASSET_S3. Fix the push, then re-run; the index will" >&2
    echo "  catch up on the next clean run." >&2
else
    echo "$(date) | run_builds: updating index/"
    python3 build/update_index.py || echo "$(date) | run_builds: index update skipped/failed (non-fatal)"
fi

# --- publish the sequence store -------------------------------------------
# Keep the public copy of the registry's RefgetStore current. The catalog's
# sequence store ($REFGENIE_GENOME_FOLDER/.refget_store) is what genome_init
# ingests into; api.refgenie.org serves sequences from its S3 mirror at
# $REFGETSTORE_S3/refgenie-main (see refgenie1 deployment/task_defs/
# primary.json). First published 2026-08-07. `aws s3 sync` uploads only
# changed files, so a night with no new genomes is a fast no-op. No --delete:
# a content-addressed store only grows, and never deleting from the public
# mirror while a server reads it is the safe default. The lock file and any
# operator scratch files stay local.
if [[ -n "${REFGETSTORE_S3:-}" ]]; then
    echo "$(date) | run_builds: syncing sequence store -> $REFGETSTORE_S3/refgenie-main"
    aws s3 sync "$REFGENIE_GENOME_FOLDER/.refget_store/" "$REFGETSTORE_S3/refgenie-main/" \
        --exclude ".rgstore.lock" --exclude "*.preracetest*" --no-progress \
        || echo "$(date) | run_builds: store sync failed (non-fatal); mirror catches up next run" >&2
else
    echo "$(date) | run_builds: REFGETSTORE_S3 unset; skipping sequence-store sync"
fi

# --- publish the catalog metadata ------------------------------------------
# api.refgenie.org's SQL catalog is populated from this artifact: `refgenie
# catalog-export` writes a filtered SQLite (only pushed assets, aliases
# materialized, download links minted from $REFGENIE_ASSET_HTTPS) and the
# server imports it at startup and daily (REFGENIE_CATALOG_URL). GATED ON PUSH
# like the index refresh, and for the same reason: metadata must never
# advertise an asset whose upload failed. A skipped night just leaves the
# previous artifact serving; the server catches up on the next clean run.
if [[ -n "${REFGENIE_CATALOG_S3:-}" && -n "${REFGENIE_ASSET_HTTPS:-}" ]]; then
    if [[ "$push_rc" -ne 0 ]]; then
        echo "$(date) | run_builds: SKIPPING catalog publish -- push failed (rc=$push_rc)" >&2
    else
        echo "$(date) | run_builds: exporting publish catalog -> $REFGENIE_CATALOG_S3/publish_catalog.sqlite"
        catalog_artifact="$(dirname "$REFGENIE_DB_CONFIG_PATH")/publish_catalog.sqlite"
        if "$REFGENIE_BIN" catalog-export --dest "$catalog_artifact" \
                --https-prefix "$REFGENIE_ASSET_HTTPS"; then
            aws s3 cp "$catalog_artifact" "$REFGENIE_CATALOG_S3/publish_catalog.sqlite" --no-progress \
                || echo "$(date) | run_builds: catalog upload failed (non-fatal); server keeps previous artifact" >&2
        else
            echo "$(date) | run_builds: catalog export failed (non-fatal); server keeps previous artifact" >&2
        fi
    fi
else
    echo "$(date) | run_builds: REFGENIE_CATALOG_S3/REFGENIE_ASSET_HTTPS unset; skipping catalog publish"
fi

# --- coverage report -------------------------------------------------------
# Name what is MISSING, not just that something failed.
#
# `--keep-going` is deliberate (one broken recipe must not abort the batch), but
# its consequence is that a badly incomplete run still pushes assets, refreshes
# index/, commits, and reads like a normal night in the log. On 2026-07-23 six of
# 42 requested assets -- all of athaliana -- did not exist when the run finished,
# and the only signal was a bare exit code that says THAT something failed while
# never saying WHAT is absent. Diagnosing it took reading four separate logs.
#
# Derived from pep/samples.csv rather than a hardcoded expectation, so it widens
# automatically as genomes and recipes are added to the registry.
#
# Reported, not enforced: the real build/push exit codes are re-raised below and
# must stay the thing that fails the run. Pass --strict to make gaps fatal.
#
# --build-status carries snakemake's exit code INTO the report. Coverage is a
# question about the catalog, not about this run: an assetgroup row means the
# asset is registered by some run, so a failed REBUILD of an asset that already
# existed is invisible to it. On 2026-07-29 five builds failed and this check
# still printed "42/42" and "no gaps" -- both true, and both read as an all-clear
# sitting directly above the failures. With the status passed in, the summary line
# says outright that full coverage is not a clean run. It does not change any exit
# code; $snakemake_rc is still what gets re-raised below.
echo "$(date) | run_builds: checking asset coverage against the PEP..."
python3 build/check_coverage.py --db-config "$REFGENIE_DB_CONFIG_PATH" \
    --build-status "$snakemake_rc" \
    || echo "$(date) | run_builds: coverage check failed to run (non-fatal)"

# Re-raise a build failure now that the successful assets have been pushed and
# the index refreshed, so the nightly still surfaces as failed for monitoring.
if [[ "$snakemake_rc" -ne 0 ]]; then
    echo "$(date) | run_builds: exiting with snakemake status $snakemake_rc (some builds failed; successful assets were pushed)"
    exit "$snakemake_rc"
fi

# A failed push must also surface. Until 2026-07-21 push was fully non-fatal, so
# job 17162885 reported COMPLETED to SLURM while uploading nothing ("0 succeeded,
# 6 failed") -- the nightly looked healthy for as long as nobody read the log.
# The publishing step failing is a failed run.
if [[ "$push_rc" -ne 0 ]]; then
    echo "$(date) | run_builds: exiting with push status $push_rc (assets built but not published; index left unchanged)"
    exit "$push_rc"
fi

echo "$(date) | run_builds: complete"
