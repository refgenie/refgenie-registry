#!/bin/bash
#SBATCH --job-name=s3-sync-plantref
#SBATCH --partition=standard
#SBATCH --account=shefflab
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --time=48:00:00
#SBATCH --output=/project/shefflab/brickyard/results_pipeline/refgenie/build_reports/s3_sync_plantref_%j.log

# One-off: finish the plantref RefgetStore upload.
#
# plantref is the largest store by object COUNT: 15,207,554 sequences
# (~50 GB logical, 134.5 Gbases) vs ~653k objects already in S3 from a cancelled
# run -- i.e. ~4% done. Sync resumes: the per-sequence files are content-addressed
# and immutable, so already-uploaded objects are skipped after the initial LIST.
#
# Not part of sync_to_s3.slurm's loop because that would re-walk all 7 other
# stores. Run this OFF the nightly (the nightly is assets-only and does not sync
# sequence stores).

module load gcc/11.4.0 openmpi/4.1.4 python/3.11.4
source ~/envs/refgetstore-analysis/bin/activate
source ~/code/refgenie-registry/infra/rivanna/env.sh

# Millions of tiny per-sequence files -> latency-bound. Default 10 concurrent
# requests would take days; crank it on the profile actually used.
aws configure set s3.max_concurrent_requests 256 --profile refgenie
aws configure set s3.max_queue_size 20000 --profile refgenie

echo "===== sync plantref  $(date) ====="
aws s3 sync "$REFGETSTORE_BASE/plantref" "$REFGETSTORE_S3/plantref" \
  --profile refgenie --delete --no-progress
rc=$?
echo "  plantref done (exit $rc)  $(date)"

# The manifest is the one mutable file that must never go stale: S3 serves it with
# no Cache-Control, so browsers heuristically cache the old copy after a rebuild.
# Re-PUT rgstore.json with `Cache-Control: no-cache` so clients always revalidate.
aws s3 cp "$REFGETSTORE_S3/plantref/rgstore.json" "$REFGETSTORE_S3/plantref/rgstore.json" \
  --profile refgenie --metadata-directive REPLACE \
  --content-type application/json --cache-control no-cache \
  && echo "  plantref rgstore.json -> Cache-Control: no-cache"

echo "ALL DONE $(date) (sync exit $rc)"
exit $rc
