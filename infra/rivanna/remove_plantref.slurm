#!/bin/bash
#SBATCH --job-name=plantref-remove
#SBATCH --partition=standard
#SBATCH --account=shefflab
#SBATCH --cpus-per-task=2
#SBATCH --mem=48G
#SBATCH --time=24:00:00
#SBATCH --output=/project/shefflab/brickyard/results_pipeline/refgenie/build_reports/plantref_remove_%j.log

# One-off: prune the three shredded assemblies from the plantref RefgetStore.
#
# Usage -- name which targets to remove, optionally with --dry-run:
#   sbatch infra/rivanna/remove_plantref.slurm wheat --dry-run
#   sbatch infra/rivanna/remove_plantref.slurm wheat
#   sbatch infra/rivanna/remove_plantref.slurm barley spruce
#   sbatch infra/rivanna/remove_plantref.slurm all
#
# Targets are removable independently and in any order -- the store is
# content-addressed, so each removal recomputes orphan status against whatever
# collections remain. Doing the smallest (wheat, 736k sequences) first is a
# deliberate cheap probe: it exercises the whole path end to end and times the
# GPFS delete rate before committing to spruce's 10.25M.
#
# Note the force-load cost is per-INVOCATION, not per-target: every run loads all
# remaining collections before removing anything. So three separate jobs pay it
# three times. That is the price of the incremental approach, and it is worth it
# for the first run.
#
# Why a batch job and not a login-node one-liner: plantref's sequences.rgsi is
# 1.45 GB and remove_collections.py force-loads all collections (~15.2M sequence
# records) before removing anything. A login-node attempt was OOM-killed outright.
#
# --mem=48G is MEASURED, not guessed. `sacct` on the completed wheat removal
# (job 17188078: 149 collections force-loaded, 735,945 orphans reclaimed) reports
# MaxRSS 18,653,824K = 17.8 GB.
#
# The footprint scales with the TOTAL SEQUENCE RECORDS RESIDENT after the
# force-load -- not with orphan count, and not with collection count. 17.8 GB was
# measured with all ~15.2M records loaded, which is this store's worst case, so
# 48G (~2.7x) covers every removal here and shrinks as the prune proceeds.
#
# Concretely, collections/ is 1.3 GB across 148 files but two of them are 94% of
# it: spruce 924 MB (10,253,694 records) and barley 305 MB (2,670,738). The next
# largest is 25 MB. That is also why the force-load costs tens of minutes rather
# than seconds -- it deserializes and materializes every sequence record, so the
# cost is per-record, not per-file. Once spruce and barley are gone the remaining
# ~146 collections total ~75 MB and this step becomes near-instant.
#
# Do NOT inflate this "to be safe". Earlier revisions of this script asked for
# 250G against a 17.8 GB actual -- 7% utilization. On a shared cluster that
# delays scheduling for everyone and burns allocation for nothing. If you think
# it needs more, measure it with `sacct --format=MaxRSS` first.
#
# Why the force-load is mandatory: on a freshly reopened on-disk store the
# collections are stubs (n_collections_loaded == 0) and orphan-sequence GC
# SILENTLY NO-OPS -- remove_collection() still returns True and still drops the
# collection record and its aliases, but n_sequences does not move and the
# orphaned .seq files stay. See load_all_collections() in remove_collections.py.
#
# Deleting ~13.6M small files on shared GPFS is slow and I/O heavy: hours, not
# minutes, hence the 24h limit.
#
# ROLLBACK: s3://refgenie/refget-store/plantref/ holds a complete mirror
# (verified 2026-07-21: all 4096 sequence prefixes present, six sampled prefixes
# match local object-for-object). Do NOT run the --delete sync until the local
# store has been verified. To restore:
#   aws s3 sync s3://refgenie/refget-store/plantref/ $REFGETSTORE_BASE/plantref/ --delete

set -uo pipefail

source /etc/profile.d/00-modulepath.sh
source /etc/profile.d/modules.sh
module load gcc/11.4.0 openmpi/4.1.4 python/3.11.4
source ~/envs/refgetstore-analysis/bin/activate
source ~/code/refgenie-registry/infra/rivanna/env.sh

cd ~/code/refgenie-registry/stores || exit 1

# Short name -> the --expect pair remove_collections.py cross-checks.
EXPECT_spruce="picea_abies_ConGenIE_v1_0=OrHTWEIrvgF7aSs2Fz4TS-cla0CJAPhM"

# wheat and barley are targeted by BARE DIGEST, not --expect, because their
# aliases are already gone. remove_collection persists the alias-namespace
# rewrite BEFORE the orphan cleanup, so a run cancelled after that point leaves
# the collection and all its sequences in place while the `name` and
# `genome_assembly` entries are already gone from aliases/collections/*.tsv.
# `--expect` then cannot resolve them and correctly refuses.
#
#   wheat  -- cancelled pre-fix run (job 17181867, 2026-07-21), quadratic GC
#   barley -- cancelled job 17189359, killed for over-requesting memory
#
# Both digests below are the ones those runs resolved and printed, cross-checked
# against the plan. Spruce's aliases are intact, so it keeps the full check.
DIGEST_wheat="B3vspFHqvYCHo4l8Q38hsZ02z1h-bJlK"
DIGEST_barley="hBkUaFdD-vx4e6KH0j3I3DLdEz0JE6q9"

targets=()
passthru=()
for arg in "$@"; do
  case "$arg" in
    all)    targets+=(wheat barley spruce) ;;
    wheat|barley|spruce) targets+=("$arg") ;;
    -*)     passthru+=("$arg") ;;
    *)      echo "Unknown target: $arg (want wheat|barley|spruce|all)" >&2; exit 2 ;;
  esac
done

if [ "${#targets[@]}" -eq 0 ]; then
  echo "No targets named. Usage: sbatch $0 <wheat|barley|spruce|all>... [--dry-run]" >&2
  exit 2
fi

expects=()
for t in "${targets[@]}"; do
  evar="EXPECT_$t"
  dvar="DIGEST_$t"
  if [ -n "${!evar:-}" ]; then
    expects+=(--expect "${!evar}")
  elif [ -n "${!dvar:-}" ]; then
    expects+=(--digest "${!dvar}")
  else
    echo "No EXPECT_$t or DIGEST_$t defined" >&2; exit 2
  fi
done

echo "===== plantref removal  $(date) ====="
echo "  targets   : ${targets[*]}"
echo "  passthru  : ${passthru[*]:-(none)}"
df -h /project/shefflab | tail -1

start=$SECONDS
# -u: unbuffered. Without it Python's stdout is block-buffered into the SLURM
# log while gtars' Rust output goes straight to the fd, so the log shows only
# gtars chatter and none of this script's progress until the job exits -- during
# a multi-hour GPFS delete that means no visibility at all.
python -u remove_collections.py plantref "${passthru[@]}" "${expects[@]}"
rc=$?
elapsed=$((SECONDS - start))

echo "  exit $rc  $(date)"
printf '  elapsed: %dh%02dm%02ds\n' $((elapsed/3600)) $(((elapsed%3600)/60)) $((elapsed%60))
df -h /project/shefflab | tail -1
echo "ALL DONE $(date)"
exit $rc
