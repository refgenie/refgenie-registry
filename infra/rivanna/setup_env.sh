#!/usr/bin/env bash
# Create (or rebuild) the refgenie build system's dedicated virtualenv.
#
# WHY THIS EXISTS
#
# Until 2026-07-28 the nightly had no environment of its own. `refgenie` and
# `snakemake` were `~/.local/bin` entry points on the cluster miniforge python,
# so every package came from `~/.local/lib/python3.11/site-packages` -- the
# account-wide user site that every other python3.11 process on this account
# also sees. Three things followed from that:
#
#   * A gtars built on 2026-07-18 was still what the nightly imported ten days
#     later, because nothing owned the environment enough to update it.
#   * Updating it had account-wide blast radius, so nobody wanted to.
#   * pandas/bottleneck in `~/.local` were built against numpy 1.x while numpy
#     2.4.6 was installed, which is the `_ARRAY_API not found` traceback in
#     every nightly log.
#
# A venv ignores `~/.local` by default (ENABLE_USER_SITE is false under a venv),
# so this environment is genuinely isolated. env.sh points REFGENIE_BIN and
# SNAKEMAKE_BIN into it, which means the nightly, a hand-run of run_builds.sh,
# and a one-off `stores/build.py` all get the same interpreter with no thought
# and no activation step.
#
# BASE PYTHON -- read before changing
#
# The venv is built from the SAME miniforge module the SLURM jobs load. Do not
# build it from an arbitrary `python3` on a login node. The cautionary tale is
# ~/envs/refgetstore-analysis, created from a python under `applications/202512`
# that the cluster has since superseded with `202606_build`: its base
# interpreter is gone, so it now dies with `libffi.so.8: cannot open shared
# object file` on any import of ctypes. Expect to re-run this script when the
# cluster rolls its application tree -- that is normal, and it is why this is a
# script and not a one-time manual setup.
#
# USAGE
#   bash infra/rivanna/setup_env.sh            # create or update in place
#   bash infra/rivanna/setup_env.sh --rebuild  # delete and recreate from scratch
set -euo pipefail

MINIFORGE_MODULE="miniforge/24.3.0-py3.11"
VENV="${REFGENIE_VENV:-$HOME/envs/refgenie-build}"
REFGENIE_SRC="${REFGENIE_SRC:-$HOME/deploy/refgenie1}"
REFGET_SRC="${REFGET_SRC:-$HOME/deploy/refget}"
GTARS_SRC="${GTARS_SRC:-$HOME/code/gtars}"

REBUILD=0
[[ "${1:-}" == "--rebuild" ]] && REBUILD=1

echo "===== refgenie build env  $(date) ====="
echo "  venv:    $VENV"
echo "  refgenie1: $REFGENIE_SRC"
echo "  refget:    $REFGET_SRC"
echo "  gtars:     $GTARS_SRC"

# shellcheck disable=SC1091
source /etc/profile.d/modules.sh
module load "$MINIFORGE_MODULE"
BASE_PY="$(command -v python3)"
echo "  base python: $BASE_PY ($($BASE_PY --version 2>&1))"

for d in "$REFGENIE_SRC" "$REFGET_SRC" "$GTARS_SRC/gtars-python"; do
    [[ -d "$d" ]] || { echo "MISSING source dir: $d" >&2; exit 1; }
done

if [[ "$REBUILD" == "1" && -d "$VENV" ]]; then
    echo "  --rebuild: removing $VENV"
    rm -rf "$VENV"
fi

if [[ ! -d "$VENV" ]]; then
    echo "  creating venv ..."
    "$BASE_PY" -m venv "$VENV"
else
    echo "  venv exists; updating in place"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"
[[ "$VIRTUAL_ENV" == "$VENV" ]] || { echo "venv activation failed" >&2; exit 1; }
python -m pip install --quiet --upgrade pip wheel

# ORDER MATTERS.
#
# refgenie1's pyproject declares `gtars>=0.9.2` and `refget>=0.11.0` as ordinary
# PyPI dependencies, so installing it pulls published wheels of both. We want
# the LOCAL builds instead: refget must be the seqcol-store-app-factory branch
# (refgenie.server.main needs create_seqcol_app / prepare_store, which are not
# in 0.11.0), and gtars must be built from source so the store write lock is
# present. So: refgenie1 first, then overwrite both with local installs.
echo "  [1/4] refgenie1 (editable, with snakemake extra) ..."
python -m pip install --quiet -e "${REFGENIE_SRC}[snakemake]"

echo "  [2/4] snakemake SLURM executor plugin ..."
python -m pip install --quiet 'snakemake-executor-plugin-slurm'

echo "  [3/4] refget (editable, local branch -- overrides the PyPI wheel) ..."
python -m pip install --quiet --force-reinstall --no-deps -e "$REFGET_SRC"

echo "  [4/4] gtars (built from source -- overrides the PyPI wheel) ..."
python -m pip install --quiet --upgrade 'maturin>=1.8.1'
python -m pip install --quiet --force-reinstall --no-deps "$GTARS_SRC/gtars-python"

echo
echo "===== verification ====="
FAIL=0
check() {  # check <label> <python expr> <expected substring>
    local label="$1" expr="$2" want="$3" got
    got="$(python -c "$expr" 2>&1 | tail -1)"
    if [[ "$got" == *"$want"* ]]; then
        echo "  OK    $label: $got"
    else
        echo "  FAIL  $label: $got  (expected to contain '$want')" >&2
        FAIL=1
    fi
}

# Every NON-EDITABLE import must resolve INSIDE the venv. If any of these
# reports a path under ~/.local, isolation is broken and we are back to the
# shared environment. refgenie and refget are deliberately excluded here: they
# are editable installs and resolve to their source trees by design, which the
# two dedicated checks below assert instead.
for m in gtars snakemake peppy; do
    check "$m resolves in venv" \
        "import $m; print($m.__file__)" "$VENV"
done
check "refgenie is the deploy tree" "import refgenie; print(refgenie.__file__)" "$REFGENIE_SRC"
check "refget is the deploy tree"   "import refget; print(refget.__file__)"   "$REFGET_SRC"

# ...and that the editable install is registered in THIS venv, not inherited
# from ~/.local. The source path alone cannot tell those apart: ~/.local also
# had editable installs pointing at the same deploy trees, which is exactly the
# ambiguity that let the shared environment masquerade as a working one.
for m in refgenie refget; do
    check "$m dist metadata is in venv" \
        "from importlib.metadata import distribution; print(distribution('$m')._path)" "$VENV"
done

# The whole point: the store write lock must be present in the gtars we install.
check "gtars has the store lock" \
    "from gtars.refget import RefgetStore as R; print('lock_api', hasattr(R,'plan_orphan_removal') and hasattr(R,'lock_for_batch'))" \
    "lock_api True"

# The entry points env.sh will pin.
for b in refgenie snakemake; do
    if [[ -x "$VENV/bin/$b" ]]; then
        echo "  OK    $b entry point: $VENV/bin/$b"
    else
        echo "  FAIL  $b entry point missing from $VENV/bin" >&2
        FAIL=1
    fi
done

echo
if [[ "$FAIL" == "0" ]]; then
    echo "refgenie build env READY: $VENV"
    echo "env.sh pins REFGENIE_BIN and SNAKEMAKE_BIN here; nothing needs to activate it by hand."
else
    echo "refgenie build env INCOMPLETE -- see FAIL lines above." >&2
    exit 1
fi
