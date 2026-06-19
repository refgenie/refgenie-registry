#!/bin/bash
#SBATCH --job-name=build-store
#SBATCH --partition=standard
#SBATCH --account=shefflab
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=8:00:00
#SBATCH --output=build_%x_%j.log

# Build one RefgetStore from its PEP. Pass the store name as the first arg:
#   sbatch --job-name=build-salmon infra/rivanna/build_store.slurm salmon_txomes
# FASTAs are read from the per-store download cache (already populated).

module load gcc/11.4.0 openmpi/4.1.4 python/3.11.4
source ~/envs/refgetstore-analysis/bin/activate

cd ~/code/refgenie-registry/stores
source ~/code/refgenie-registry/infra/rivanna/env.sh

STORE="$1"
echo "Building store '$STORE' at $(date)"
python build.py "$STORE"
echo "Build of '$STORE' finished at $(date)"
echo "=== store on disk ==="
du -sh "$REFGETSTORE_BASE/$STORE" 2>&1
