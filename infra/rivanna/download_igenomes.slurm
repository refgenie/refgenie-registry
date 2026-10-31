#!/bin/bash
#SBATCH --job-name=dl-igenomes
#SBATCH --partition=standard
#SBATCH --account=shefflab
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=24:00:00
#SBATCH --output=dl_igenomes_%j.log

REGISTRY="${REGISTRY_DIR:-$HOME/mobot/checkouts/refgenie-registry}"

# The build system's own environment: env.sh defines REFGENIE_VENV (the venv
# infra/rivanna/setup_env.sh builds on miniforge/26.9.4). This used to load
# `gcc/11.4.0 openmpi/4.1.4 python/3.11.4` and activate ~/envs/refgetstore-analysis;
# Rivanna removed that python module, and build_store.slurm says why that venv
# was the wrong one anyway.
cd "$REGISTRY"/stores
source "$REGISTRY"/infra/rivanna/env.sh
if [[ ! -x "${REFGENIE_VENV:-}/bin/python" ]]; then
    echo "FATAL: build venv missing (REFGENIE_VENV=${REFGENIE_VENV:-unset})." >&2
    echo "       create it with: bash infra/rivanna/setup_env.sh" >&2
    exit 1
fi
source "$REFGENIE_VENV/bin/activate"

export DOWNLOAD_DIR=$REFGETSTORE_BASE/.downloads_igenomes
mkdir -p $DOWNLOAD_DIR

echo "Downloading igenomes FASTAs to $DOWNLOAD_DIR"
echo "Started at $(date)"

python3 "$REGISTRY"/download_fastas.py igenomes/sources.csv $DOWNLOAD_DIR

echo "Finished at $(date)"
echo "Files in download dir:"
ls $DOWNLOAD_DIR | wc -l
