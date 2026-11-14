#!/usr/bin/env bash
# Create (or rebuild) the refgenie build system's dedicated virtualenv.
#
# WHY THIS EXISTS
#
# The nightly needs an environment of its own. Without this, `refgenie` and
# `snakemake` are `~/.local/bin` entry points on the cluster miniforge python,
# so every package came from `~/.local/lib/python3.11/site-packages`, the
# account-wide user site that every other python3.11 process on this account
# also sees. Three things followed from that:
#
#   * A months-old gtars stayed what the nightly imported, because nothing owned
#     the environment enough to update it.
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
# BASE PYTHON, read before changing
#
# The venv is built from the SAME miniforge module the SLURM jobs load. Do not
# build it from an arbitrary `python3` on a login node. The cautionary tale is
# ~/envs/refgetstore-analysis, created from a python under `applications/202512`
# that the cluster has since superseded with `202606_build`: its base
# interpreter is gone, so it now dies with `libffi.so.8: cannot open shared
# object file` on any import of ctypes. Expect to re-run this script when the
# cluster rolls its application tree, that is normal, and it is why this is a
# script and not a one-time manual setup.
#
# USAGE
#   bash infra/rivanna/setup_env.sh            # create or update in place
#   bash infra/rivanna/setup_env.sh --rebuild  # delete and recreate from scratch
set -euo pipefail

MINIFORGE_MODULE="miniforge/26.9.4"
VENV="${REFGENIE_VENV:-$HOME/envs/refgenie-build}"
REFGENIE_SRC="${REFGENIE_SRC:-$HOME/deploy/refgenie}"

REBUILD=0
[[ "${1:-}" == "--rebuild" ]] && REBUILD=1

echo "===== refgenie build env  $(date) ====="
echo "  venv:    $VENV"
echo "  refgenie: $REFGENIE_SRC"

# shellcheck disable=SC1091
source /etc/profile.d/modules.sh
module load "$MINIFORGE_MODULE"
BASE_PY="$(command -v python3)"
echo "  base python: $BASE_PY ($($BASE_PY --version 2>&1))"

[[ -d "$REFGENIE_SRC" ]] || { echo "MISSING source dir: $REFGENIE_SRC" >&2; exit 1; }

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

# refgenie (a git checkout at a release tag, editable) brings published gtars
# and refget wheels as ordinary dependencies.
echo "  [1/2] refgenie (editable, with snakemake extra) ..."
python -m pip install --quiet -e "${REFGENIE_SRC}[snakemake]"

echo "  [2/2] snakemake SLURM executor plugin ..."
python -m pip install --quiet 'snakemake-executor-plugin-slurm'

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
# shared environment. refgenie is excluded: it is an editable install and
# resolves to its source tree by design, which the check below asserts.
for m in gtars refget snakemake peppy; do
    check "$m resolves in venv" \
        "import $m; print($m.__file__)" "$VENV"
done
check "refgenie is the deploy tree" "import refgenie; print(refgenie.__file__)" "$REFGENIE_SRC"

# ...and that the editable install is registered in THIS venv, not inherited
# from ~/.local. The source path alone cannot tell those apart: ~/.local also
# had editable installs pointing at the same deploy trees, which is exactly the
# ambiguity that let the shared environment masquerade as a working one.
for m in refgenie; do
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
    echo "refgenie build env INCOMPLETE, see FAIL lines above." >&2
    exit 1
fi
