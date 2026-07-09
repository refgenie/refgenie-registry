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
# Env (see infra/rivanna/env.sh + the snakemake profile):
#   REFGENIE_INPUTS   required by the Snakefile/PEP (root of input FASTAs).
#   REFGENIE_DB_CONFIG_PATH  refgenie1 DB config (persistent build DB).
#   REFGENIE_BIN      build-command binary name (default: refgenie).
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
# catalog. Operators can point REFGENIE_DB_CONFIG_PATH/REFGENIE_BUILD_DB
# elsewhere (e.g. a laptop) via the ${VAR:-default} fallbacks below.
export REFGENIE_DB_CONFIG_PATH="${REFGENIE_DB_CONFIG_PATH:-$BUILD_DIR/.refgenie_build_db_config.yaml}"
REFGENIE_BUILD_DB="${REFGENIE_BUILD_DB:-$BUILD_DIR/.refgenie_build.sqlite}"

echo "$(date) | run_builds: REGISTRY_DIR=$REGISTRY_DIR"
echo "$(date) | run_builds: REFGENIE_INPUTS=$REFGENIE_INPUTS"
echo "$(date) | run_builds: REFGENIE_DB_CONFIG_PATH=$REFGENIE_DB_CONFIG_PATH"
echo "$(date) | run_builds: REFGENIE_BUILD_DB=$REFGENIE_BUILD_DB"
echo "$(date) | run_builds: REFGENIE_BIN=$REFGENIE_BIN  DRY_RUN=${DRY_RUN:-0}"

# (Re)write the small DB config each run (idempotent) and ensure the persistent
# catalog's parent directory exists. The sqlite file itself is NOT removed — it
# persists across runs and is updated in place.
mkdir -p "$(dirname "$REFGENIE_BUILD_DB")"
mkdir -p "$(dirname "$REFGENIE_DB_CONFIG_PATH")"
cat > "$REFGENIE_DB_CONFIG_PATH" <<EOF
path: $REFGENIE_BUILD_DB
type: sqlite
EOF
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
echo "$(date) | run_builds: reconciling genomes with persistent catalog..."
python3 build/reconcile_genomes.py --db-config "$REFGENIE_DB_CONFIG_PATH"

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
SNAKEMAKE_ARGS=(
    --snakefile "$SNAKEFILE"
    --directory "$REGISTRY_DIR"
)
if [[ "${DRY_RUN:-0}" == "1" ]]; then
    echo "$(date) | run_builds: DRY RUN (snakemake -n)"
    snakemake "${SNAKEMAKE_ARGS[@]}" -n
    # Preview the push without uploading. With a fresh/empty DB (no builds
    # executed) there may be nothing to preview; handle_push prints "Nothing to
    # push" and returns cleanly.
    if [[ -n "${REFGENIE_ASSET_S3:-}" ]]; then
        echo "$(date) | run_builds: previewing push (dry-run)"
        "$REFGENIE_BIN" push --strategy folder_sync --dry-run \
            || echo "$(date) | run_builds: push --dry-run preview failed (non-fatal)"
    fi
    echo "$(date) | run_builds: dry run complete; skipping index update"
    exit 0
fi

echo "$(date) | run_builds: dispatching builds with profile $SNAKEMAKE_PROFILE"
snakemake "${SNAKEMAKE_ARGS[@]}" --profile "$SNAKEMAKE_PROFILE"

# --- 4. push staged assets to S3 -----------------------------------------
# Push runs ONCE here on the driver host AFTER the snakemake fan-out returns. It
# reads the shared build DB ($REFGENIE_DB_CONFIG_PATH) and the staged assets on
# brickyard, uploads every RemoteAssetLink(pushed=False), and marks them pushed.
# Idempotency is handled by refgenie1: only pushed=False links are uploaded,
# failures leave pushed=False for the next run, and `aws s3 sync` re-uploads only
# changed objects. Use the absolute $REFGENIE_BIN so push is PATH-immune on the
# driver (same as the build rules). Non-fatal, consistent with the index step.
if [[ -n "${REFGENIE_ASSET_S3:-}" ]]; then
    echo "$(date) | run_builds: pushing staged assets -> $REFGENIE_ASSET_S3"
    "$REFGENIE_BIN" push --strategy folder_sync \
        || echo "$(date) | run_builds: push failed (non-fatal); links stay pushed=False for retry"
else
    echo "$(date) | run_builds: REFGENIE_ASSET_S3 unset; skipping push"
fi

# --- 5. refresh the index ------------------------------------------------
echo "$(date) | run_builds: updating index/"
python3 build/update_index.py || echo "$(date) | run_builds: index update skipped/failed (non-fatal)"

echo "$(date) | run_builds: complete"
