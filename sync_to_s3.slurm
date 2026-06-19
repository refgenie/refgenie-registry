#!/bin/bash
#SBATCH --job-name=s3-sync
#SBATCH --partition=standard
#SBATCH --account=shefflab
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --time=24:00:00
#SBATCH --output=s3_sync_%j.log

# Push built RefgetStores to S3, replacing what's there (--delete = true mirror).
# Uses the Rivanna 'refgenie' AWS profile (RefgenieDataBot); the default s3user is denied.
# Small -> large so quick stores land first; vgp (~378G) is last.

module load gcc/11.4.0 openmpi/4.1.4 python/3.11.4
source ~/envs/refgetstore-analysis/bin/activate
source ~/code/refgenie-registry/env.sh

# RefgetStores are millions of tiny per-sequence files (salmon ~1.5M, vrs ~780k).
# aws s3 sync defaults to 10 concurrent requests -> latency-bound, days slow.
# Crank concurrency on the profile actually used.
aws configure set s3.max_concurrent_requests 256 --profile refgenie
aws configure set s3.max_queue_size 20000 --profile refgenie

for s in demo refseq vrs jungle igenomes pangenome vgp; do
  echo "===== sync $s  $(date) ====="
  aws s3 sync "$REFGETSTORE_BASE/$s" "$REFGETSTORE_S3/$s" --profile refgenie --delete --no-progress
  echo "  $s done (exit $?)  $(date)"

  # The store manifest is the one mutable file that must never go stale: S3 serves it with
  # no Cache-Control, so browsers heuristically cache the old copy after a rebuild. Re-PUT
  # rgstore.json in place with `Cache-Control: no-cache` so clients always revalidate it
  # (cheap 304 via ETag). The larger index/sequence files rely on the explorer's
  # mismatch-revalidation + content addressing, so we don't pay a revalidation on those.
  aws s3 cp "$REFGETSTORE_S3/$s/rgstore.json" "$REFGETSTORE_S3/$s/rgstore.json" \
    --profile refgenie --metadata-directive REPLACE \
    --content-type application/json --cache-control no-cache \
    && echo "  $s rgstore.json -> Cache-Control: no-cache"
done
echo "ALL DONE $(date)"
