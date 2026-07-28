#!/bin/bash
#SBATCH --job-name=s3-sync
#SBATCH --partition=standard
#SBATCH --account=shefflab
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --time=24:00:00
#SBATCH --output=s3_sync_%j.log

REGISTRY="${REGISTRY_DIR:-$HOME/mobot/checkouts/refgenie-registry}"

# Push built RefgetStores to S3, replacing what's there (--delete = true mirror).
# Uses the Rivanna 'refgenie' AWS profile (RefgenieDataBot); the default s3user is denied.
# Small -> large so quick stores land first; vgp (~378G) is last.

module load gcc/11.4.0 openmpi/4.1.4 python/3.11.4
source ~/envs/refgetstore-analysis/bin/activate
source "$REGISTRY"/infra/rivanna/env.sh

# RefgetStores are millions of tiny per-sequence files (salmon ~1.5M, vrs ~780k).
# aws s3 sync defaults to 10 concurrent requests -> latency-bound, days slow.
# Crank concurrency on the profile actually used.
aws configure set s3.max_concurrent_requests 256 --profile refgenie
aws configure set s3.max_queue_size 20000 --profile refgenie

# Transient files a writer may leave in a store directory while it is committing:
# the exclusive write lock, a stolen (stale) lock awaiting cleanup, and the temp
# files that back atomic publish. None of them belong in S3 -- a lockfile there is
# inert for readers (nothing reads it over the remote path) but confusing, and a
# half-written temp file is pure wasted transfer.
LOCK_EXCLUDES=(--exclude '.rgstore.lock' --exclude '.rgstore.lock.stale.*' --exclude '.rgstore.tmp.*')

for s in demo refseq vrs jungle igenomes pangenome vgp; do
  echo "===== sync $s  $(date) ====="

  # Preflight: never mirror a store mid-commit. `aws s3 sync --delete` is a true
  # mirror, so copying a store while a writer is between publishing an index and
  # publishing the manifest puts an inconsistent snapshot in S3 -- and the
  # --delete makes it authoritative.
  if gtars refget lock-status "$REFGETSTORE_BASE/$s" | grep -q 'HELD'; then
    echo "  SKIP $s: a writer holds the store lock. Re-run after it finishes;" >&2
    echo "        'gtars refget lock-status $REFGETSTORE_BASE/$s' names the holder." >&2
    continue
  fi

  # Two phases, and the ORDER is load-bearing. rgstore.json advertises SHA-256
  # digests of sequences.rgsi and collections.rgci; if the manifest lands before
  # the files it describes, S3 serves a manifest pointing at bytes that are not
  # there yet, and a client that validates digests sees a corrupt store. So:
  # everything EXCEPT the manifest first, then the manifest.
  aws s3 sync "$REFGETSTORE_BASE/$s" "$REFGETSTORE_S3/$s" \
    --profile refgenie --delete --no-progress \
    "${LOCK_EXCLUDES[@]}" --exclude 'rgstore.json'
  echo "  $s data done (exit $?)  $(date)"

  # The store manifest is the one mutable file that must never go stale: S3 serves it with
  # no Cache-Control, so browsers heuristically cache the old copy after a rebuild. Upload
  # it with `Cache-Control: no-cache` so clients always revalidate it (cheap 304 via ETag).
  # The larger index/sequence files rely on the explorer's mismatch-revalidation + content
  # addressing, so we don't pay a revalidation on those.
  aws s3 cp "$REFGETSTORE_BASE/$s/rgstore.json" "$REFGETSTORE_S3/$s/rgstore.json" \
    --profile refgenie --no-progress \
    --content-type application/json --cache-control no-cache \
    && echo "  $s rgstore.json published last -> Cache-Control: no-cache"
done
echo "ALL DONE $(date)"
