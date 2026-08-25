#!/usr/bin/env python3
"""Read per-store PEP (`project_config.yaml`) settings shared by the build scripts.

Centralizes the two store-declared knobs the generic tooling reads:
  - `fasta_root:`  base dir for relative `sources.csv` fasta tokens (env-expanded)
  - `aliasing:`    sequence-alias strategy for build_aliases.py

Requires pyyaml (already used by build.py via peppy).
"""
from __future__ import annotations

import os
import yaml
from pathlib import Path

PEP_CONFIG = "project_config.yaml"
STORES_DIR = Path(__file__).resolve().parent


def get_store_dirs(stores_dir=STORES_DIR) -> list:
    """Every store directory under stores/, i.e. every dir with a PEP config.

    THE definition of "a store exists". stores/build.py re-exports it, and
    tools/validate_genome.py checks `build.store` against it, so a genome YAML
    and the build scripts can never disagree about the store list. Lives here
    (not in build.py) so the check stays importable without peppy/refget.
    """
    stores_dir = Path(stores_dir)
    return sorted(
        d for d in stores_dir.iterdir()
        if d.is_dir() and (d / PEP_CONFIG).exists()
    )


def store_slugs(stores_dir=STORES_DIR) -> list:
    """Sorted slugs of every existing store."""
    return [d.name for d in get_store_dirs(stores_dir)]


def load_pep(store_dir) -> dict:
    """Return the parsed project_config.yaml dict for a store dir ({} if absent/unreadable)."""
    cfg = Path(store_dir) / PEP_CONFIG
    if not cfg.exists():
        return {}
    try:
        with open(cfg) as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        print(f"WARNING: could not read {cfg}: {e}")
        return {}


def fasta_root(store_dir):
    """Absolute root for relative fasta tokens (from `fasta_root:`, env-expanded), or None."""
    fr = load_pep(store_dir).get("fasta_root")
    return os.path.expandvars(str(fr)) if fr else None


def aliasing(store_dir) -> dict:
    """The store's `aliasing:` config, defaulting to collection-aliases-only."""
    default = {"seq_strategy": "none"}
    a = load_pep(store_dir).get("aliasing")
    return {**default, **a} if isinstance(a, dict) else dict(default)
