#!/usr/bin/env bash
# Setup CPython sparse checkout for benchmark3 (Run 3).
# Creates two identical checkouts: one for vanilla agent, one for vectr agent.
# Each checkout includes: Python/, Objects/, Include/, Modules/ (~170 C source files).
#
# Usage: bash setup_run3.sh
set -euo pipefail

VANILLA_DIR="${POC_VANILLA_DIR_RUN3:-/tmp/poc-cpython-vanilla}"
VECTR_DIR="${POC_VECTR_DIR_RUN3:-/tmp/poc-cpython-vectr}"
CPYTHON_URL="https://github.com/python/cpython"
SPARSE_DIRS="Python/ Objects/ Include/ Modules/"

clone_sparse() {
    local dest="$1"
    if [ -d "$dest/.git" ]; then
        echo "[setup_run3] $dest already exists — skipping clone."
        return
    fi
    echo "[setup_run3] Cloning CPython (sparse) into $dest ..."
    git clone --depth 1 --filter=blob:none --sparse "$CPYTHON_URL" "$dest"
    cd "$dest"
    git sparse-checkout set $SPARSE_DIRS
    cd -
    echo "[setup_run3] Done: $dest"
}

clone_sparse "$VANILLA_DIR"
clone_sparse "$VECTR_DIR"

echo ""
echo "CPython sparse checkout ready."
echo "  Vanilla dir : $VANILLA_DIR"
echo "  Vectr dir   : $VECTR_DIR"
echo ""
echo "Start vectr on the CPython workspace, then run the benchmark:"
echo "  vectr start --path /tmp/poc-cpython-vectr"
echo "  cd $(dirname "$0")"
echo "  python3.14 run_poc.py --run run3 --save"
