#!/bin/bash
#SBATCH --job-name=build-store
#SBATCH --partition=standard
#SBATCH --account=shefflab
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=8:00:00
#SBATCH --output=build_%x_%j.log

REGISTRY="${REGISTRY_DIR:-$HOME/mobot/checkouts/refgenie-registry}"

# Build one RefgetStore from its PEP. Pass the store name as the first arg:
#   sbatch --job-name=build-salmon infra/rivanna/build_store.slurm salmon_txomes
# FASTAs are read from the per-store download cache (already populated).

# env.sh first: it defines REFGENIE_VENV, the build system's own environment.
# This used to load `python/3.11.4` and activate ~/envs/refgetstore-analysis.
# Both were wrong: that venv's base interpreter has been superseded on the
# cluster (it now dies on `libffi.so.8`), and it is not the environment the
# nightly uses, so a store built here could see a different gtars than a store
# built by the nightly. One environment for the whole build system.
cd "$REGISTRY"/stores
source "$REGISTRY"/infra/rivanna/env.sh

if [[ ! -x "${REFGENIE_VENV:-}/bin/python" ]]; then
    echo "FATAL: build venv missing (REFGENIE_VENV=${REFGENIE_VENV:-unset})." >&2
    echo "       create it with: bash infra/rivanna/setup_env.sh" >&2
    exit 1
fi
source "$REFGENIE_VENV/bin/activate"

STORE="$1"
echo "Building store '$STORE' at $(date)"
python build.py "$STORE"
echo "Build of '$STORE' finished at $(date)"
echo "=== store on disk ==="
du -sh "$REFGETSTORE_BASE/$STORE" 2>&1
