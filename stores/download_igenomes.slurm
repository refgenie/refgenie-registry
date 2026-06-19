#!/bin/bash
#SBATCH --job-name=dl-igenomes
#SBATCH --partition=standard
#SBATCH --account=shefflab
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=24:00:00
#SBATCH --output=dl_igenomes_%j.log

module load gcc/11.4.0 openmpi/4.1.4 python/3.11.4
source ~/envs/refgetstore-analysis/bin/activate

cd ~/code/refgenie-registry/stores
source ../env.sh

export DOWNLOAD_DIR=$REFGETSTORE_BASE/.downloads_igenomes
mkdir -p $DOWNLOAD_DIR

echo "Downloading igenomes FASTAs to $DOWNLOAD_DIR"
echo "Started at $(date)"

python3 ~/code/refgenie-registry/download_fastas.py igenomes/sources.csv $DOWNLOAD_DIR

echo "Finished at $(date)"
echo "Files in download dir:"
ls $DOWNLOAD_DIR | wc -l
