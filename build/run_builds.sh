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
#      `refgenie build <genome>/<asset> --stage` inside the recipe's container.
#   4. Refresh index/ from whatever assets are now present (build/update_index.py).
#
# Conservative by default: set DRY_RUN=1 to do everything EXCEPT actually
# submit/run builds (snakemake -n). The nightly mobot job runs it for real.
#
# Env (see infra/rivanna/env.sh + the snakemake profile):
#   REFGENIE_INPUTS   required by the Snakefile/PEP (root of input FASTAs).
#   REFGENIE_DB_CONFIG_PATH  refgenie1 DB config (persistent build DB).
#   REFGENIE_BIN      build-command binary name (default: refgenie).
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

# REFGENIE_INPUTS is required by the generated Snakefile (envvars: stanza) and
# by the PEP sample modifier that derives fasta_file_path. Default it to the
# registry's own genomes input root if the operator did not set one.
export REFGENIE_INPUTS="${REFGENIE_INPUTS:-${REFGETSTORE_FASTA:-$REGISTRY_DIR/build/inputs}}"

REFGENIE_BIN="${REFGENIE_BIN:-refgenie}"
SNAKEMAKE_PROFILE="${SNAKEMAKE_PROFILE:-$REGISTRY_DIR/build/profiles/rivanna}"

BUILD_DIR="$REGISTRY_DIR/build"
SNAKEFILE="$BUILD_DIR/Snakefile"

# CRITICAL: the importer and the generated Snakefile (which builds its own
# `Refgenie()` at parse time) MUST share ONE DB. `Refgenie()` with no args reads
# $REFGENIE_DB_CONFIG_PATH, so we point that at a build-dedicated DB and export it
# for the snakemake subprocess. Without this the importer writes to a throwaway
# DB and the Snakefile can't find any recipes ("MissingRecipeError").
#
# We REBUILD this DB from scratch every run: the registry's asset_classes/ +
# recipes/ are the source of truth and are reloaded in full each time. Re-importing
# into a populated DB fails (an existing recipe blocks overwriting its asset class),
# so starting clean keeps the build idempotent. Genomes are re-initialized with
# `--force` and assets are rebuilt, so nothing of value is lost by resetting.
# Operators can point REFGENIE_DB_CONFIG_PATH elsewhere to use their own DB.
export REFGENIE_DB_CONFIG_PATH="${REFGENIE_DB_CONFIG_PATH:-$BUILD_DIR/.refgenie_build_db_config.yaml}"
REFGENIE_BUILD_DB="${REFGENIE_BUILD_DB:-$BUILD_DIR/.refgenie_build.sqlite}"

echo "$(date) | run_builds: REGISTRY_DIR=$REGISTRY_DIR"
echo "$(date) | run_builds: REFGENIE_INPUTS=$REFGENIE_INPUTS"
echo "$(date) | run_builds: REFGENIE_DB_CONFIG_PATH=$REFGENIE_DB_CONFIG_PATH"
echo "$(date) | run_builds: REFGENIE_BIN=$REFGENIE_BIN  DRY_RUN=${DRY_RUN:-0}"

# Fresh build DB config + sqlite file each run.
cat > "$REFGENIE_DB_CONFIG_PATH" <<EOF
path: $REFGENIE_BUILD_DB
type: sqlite
EOF
rm -f "$REFGENIE_BUILD_DB"
echo "$(date) | run_builds: reset build DB at $REFGENIE_BUILD_DB"

# --- 1. import recipes + render Snakefile (single refgenie1 instance) -----
# Import into the build DB AND render the Snakefile from that same instance.
echo "$(date) | run_builds: importing asset_classes + recipes and generating Snakefile..."
python3 tools/import_recipes.py --db-config "$REFGENIE_DB_CONFIG_PATH" --snakefile "$SNAKEFILE"

# --- 2. patch the generated Snakefile -------------------------------------
# (a) refgenie1's template hardcodes `refgenie1` in shell rules; the installed
#     entry point is `refgenie`. Rewrite only the leading command token.
if [[ "$REFGENIE_BIN" != "refgenie1" ]]; then
    sed -i "s/refgenie1 /$REFGENIE_BIN /g" "$SNAKEFILE"
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

# --- 3. dispatch builds via snakemake ------------------------------------
SNAKEMAKE_ARGS=(
    --snakefile "$SNAKEFILE"
    --directory "$REGISTRY_DIR"
)
if [[ "${DRY_RUN:-0}" == "1" ]]; then
    echo "$(date) | run_builds: DRY RUN (snakemake -n)"
    snakemake "${SNAKEMAKE_ARGS[@]}" -n
    echo "$(date) | run_builds: dry run complete; skipping index update"
    exit 0
fi

echo "$(date) | run_builds: dispatching builds with profile $SNAKEMAKE_PROFILE"
snakemake "${SNAKEMAKE_ARGS[@]}" --profile "$SNAKEMAKE_PROFILE"

# --- 4. refresh the index ------------------------------------------------
echo "$(date) | run_builds: updating index/"
python3 build/update_index.py || echo "$(date) | run_builds: index update skipped/failed (non-fatal)"

echo "$(date) | run_builds: complete"
