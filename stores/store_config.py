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
