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
VENV="$HOME/envs/refgetstore-analysis"

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
echo "  source check OK: single-pass orphan cleanup present, quadratic form absent"

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
python - <<'PYEOF'
from refget.store import RefgetStore
s = RefgetStore.on_disk("store")
mA, _ = s.add_sequence_collection_from_fasta("f/A.fa")
mB, _ = s.add_sequence_collection_from_fasta("f/B.fa")
digA, digB = mA.digest, mB.digest
del s

# Reopen: collections are stubs, so force-load before removing (the SECOND bug,
# not fixed upstream -- orphan GC silently no-ops without this).
s = RefgetStore.on_disk("store")
for m in s.list_collections(page=0, page_size=500)["results"]:
    s.get_collection(m.digest)
before = int(s.stats()["n_sequences"])
assert s.remove_collection(digA, remove_orphan_sequences=True) is True
after = int(s.stats()["n_sequences"])
print(f"    n_sequences {before} -> {after} (expect 3 -> 2)")
assert before == 3 and after == 2, f"orphan GC wrong: {before} -> {after}"
kept = s.get_collection(digB)
print(f"    survivor readable with {len(kept.sequences)} sequences (expect 2)")
assert len(kept.sequences) == 2
print("    FUNCTIONAL CHECK PASSED")
PYEOF
rc=$?
cd / && rm -rf "$WORK"

echo "  functional check exit $rc"
echo "ALL DONE $(date)"
exit $rc
