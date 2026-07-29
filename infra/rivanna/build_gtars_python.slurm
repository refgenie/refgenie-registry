#!/bin/bash
#SBATCH --job-name=build-gtars-py
#SBATCH --partition=standard
#SBATCH --account=shefflab
#SBATCH --cpus-per-task=8
#SBATCH --mem=8G
#SBATCH --time=4:00:00
#SBATCH --output=/project/shefflab/brickyard/results_pipeline/refgenie/build_reports/build_gtars_python_%j.log

# Build the gtars Python extension from ~/code/gtars and install it into the
# refgetstore-analysis venv.
#
# WHY: the venv ships gtars 0.9.0, whose RefgetStore.remove_collection is
# quadratic in orphan cleanup -- it calls md5_lookup.retain() once per orphan,
# and md5_lookup holds one entry per sequence in the store. On plantref
# (15.2M sequences) removing the 736k-sequence wheat collection ran 78 minutes
# at 100% CPU and deleted nothing. The fix (single HashSet pass, gtars dev
# branch, commit 7ecc71ff merged via 59d442f1) makes it O(sequences + orphans).
#
# ~/code/gtars is populated by the yoke/mutagen session `yoke-atacbase-gtars`
# (alpha: ~/Dropbox/workspaces/intervals/repos/gtars). NOTE it is NOT a git
# checkout on this side -- mutagen ignores VCS dirs by default -- so verify the
# source content directly rather than trusting `git log`.
#
# target/ is in the sync's ignore list, so building in-tree does not push 23G of
# artifacts back to the laptop.

set -uo pipefail

source /etc/profile.d/00-modulepath.sh
source /etc/profile.d/modules.sh
module load gcc/11.4.0 openmpi/4.1.4 python/3.11.4

export PATH="$HOME/.cargo/bin:$PATH"

SRC="$HOME/code/gtars"
# The build system's own venv (infra/rivanna/setup_env.sh), NOT
# ~/envs/refgetstore-analysis. That was the target until 2026-07-28, and it was
# the wrong one twice over: nothing in the build path uses it, so a rebuild here
# never reached the nightly; and its base interpreter (an `applications/202512`
# python) has since been superseded on the cluster, so it now fails on
# `libffi.so.8` for any import of ctypes. Installing into the environment the
# jobs actually run in is the whole point.
VENV="${REFGENIE_VENV:-$HOME/envs/refgenie-build}"

echo "===== build gtars-python  $(date) ====="
command -v cargo || { echo "cargo not found on PATH" >&2; exit 1; }
cargo --version

# Guard: refuse to build unless the fix is actually present in the synced source.
# A silent no-op here would send us back to a multi-hour removal that frees nothing.
if ! grep -q "orphan_set" "$SRC/gtars-refget/src/store/readonly.rs"; then
  echo "FIX NOT PRESENT in $SRC/gtars-refget/src/store/readonly.rs -- is the sync current?" >&2
  exit 1
fi
if grep -qE 'retain\(\|_, v\| v != (orphan_)?key\)' "$SRC/gtars-refget/src/store/readonly.rs"; then
  echo "OLD QUADRATIC retain still present -- refusing to build" >&2
  exit 1
fi
# Second fix: the shard-dir rmdir must be hoisted out of the unlink loop. Without
# it the delete phase issues one failing rmdir per orphan (~10.25M for spruce) on
# top of the unlinks, in a loop that is already filesystem-metadata bound.
if ! grep -q "parent_dirs" "$SRC/gtars-refget/src/store/readonly.rs"; then
  echo "RMDIR HOIST NOT PRESENT -- is the sync current? Expected branch fix/orphan-rmdir-hoist" >&2
  exit 1
fi
# Third fix: commits must be a DELTA against the index on disk, not a write-back
# of everything the handle has in memory. Without this, a writer that opened
# before a removal re-adds the removed collection and its sequence rows on its
# next commit -- after the .seq files are gone. build.py holds one handle across
# a multi-hour ingest, so that window is the normal case, not a rare race.
if ! grep -q "PendingChanges" "$SRC/gtars-refget/src/store/readonly.rs"; then
  echo "DELTA COMMIT NOT PRESENT -- is the sync current? Removals will be resurrected." >&2
  exit 1
fi
if grep -q "struct Tombstones" "$SRC/gtars-refget/src/store/readonly.rs"; then
  echo "OLD MERGE-AT-COMMIT tombstones still present -- refusing to build" >&2
  exit 1
fi
echo "  source check OK: single-pass orphan cleanup + hoisted rmdir + delta commit present"

source "$VENV/bin/activate" || exit 1
echo "  venv: $VIRTUAL_ENV"
echo "  gtars BEFORE: $(python -c 'import gtars; print(gtars.__version__)' 2>&1)"

# maturin is the build backend declared in gtars-python/pyproject.toml.
pip install --quiet --upgrade 'maturin>=1.8.1' || exit 1

cd "$SRC/gtars-python" || exit 1
echo "  building (release) with $SLURM_CPUS_PER_TASK jobs ... $(date +%H:%M:%S)"
pip install --force-reinstall --no-deps . || { echo "BUILD/INSTALL FAILED" >&2; exit 1; }

echo "  gtars AFTER: $(python -c 'import gtars; print(gtars.__version__, gtars.__file__)' 2>&1)"

# Functional check: the fix must be reachable through the Python path, not just
# compiled into the crate. Build a tiny store where one collection's sequences
# are unique and another's are shared, remove the first, and confirm the orphan
# is reclaimed while the shared sequence survives.
echo "  running functional check ... $(date +%H:%M:%S)"
WORK=$(mktemp -d)
cd "$WORK" || exit 1
mkdir -p f
printf '>s1\nACGTACGTAA\n>s2\nTTTTGGGGCC\n' > f/A.fa
printf '>s2\nTTTTGGGGCC\n>s3\nCCCCAAAATT\n' > f/B.fa
printf '>s4\nGGGGCCCCAA\n' > f/C.fa
python - <<'PYEOF'
from refget.store import RefgetStore
s = RefgetStore.on_disk("store")
mA, _ = s.add_sequence_collection_from_fasta("f/A.fa")
mB, _ = s.add_sequence_collection_from_fasta("f/B.fa")
digA, digB = mA.digest, mB.digest
del s

# Check 1: orphan GC. No force-load needed -- the live set is derived from disk,
# so a store of stubs is fine. (It was NOT before 2026-07-28: the old
# name_lookup-derived live set was empty on a fresh open and would have unlinked
# the shared sequence.)
s = RefgetStore.on_disk("store")
before = int(s.stats()["n_sequences"])
doomed = s.plan_orphan_removal(digA)
assert s.remove_collection(digA, remove_orphan_sequences=True) is True
after = int(s.stats()["n_sequences"])
print(f"    n_sequences {before} -> {after} (expect 3 -> 2), planned {len(doomed)} (expect 1)")
assert before == 3 and after == 2, f"orphan GC wrong: {before} -> {after}"
assert len(doomed) == 1, f"dry-run disagreed: {doomed}"
kept = s.get_collection(digB)
print(f"    survivor readable with {len(kept.sequences)} sequences (expect 2)")
assert len(kept.sequences) == 2
del s

# Check 2: a stale handle must not resurrect another writer's removal. Holding a
# handle across a removal by someone else is what build.py does for hours at a
# time -- open once, ingest, commit at the end -- so this is the normal case.
stale = RefgetStore.on_disk("store")
mC, _ = stale.add_sequence_collection_from_fasta("f/C.fa")
remover = RefgetStore.on_disk("store")   # opened while C is still in the index
assert remover.remove_collection(mC.digest, remove_orphan_sequences=True) is True
del remover                              # removal fully committed before...
stale.write()                            # ...the stale handle commits
del stale

check = RefgetStore.on_disk("store")
digests = {m.digest for m in check.list_collections(page=0, page_size=500)["results"]}
print(f"    after stale commit: {len(digests)} collection(s) (expect 1)")
assert mC.digest not in digests, "REMOVAL RESURRECTED by a stale handle's commit"
assert digB in digests, "the surviving collection was dropped"
print("    FUNCTIONAL CHECK PASSED")
PYEOF
rc=$?
cd / && rm -rf "$WORK"

echo "  functional check exit $rc"
echo "ALL DONE $(date)"
exit $rc
