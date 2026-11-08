#!/usr/bin/env python3
"""Read per-store PEP (`project_config.yaml`) settings shared by the build scripts.

Centralizes the store-declared knobs the generic tooling reads:
  - `fasta_root:`  base dir for relative `sources.csv` fasta tokens (env-expanded)
  - `kind:`        `catalog` for the build catalog's own store (refgenie-main),
                   which has no sources.csv; absent for an ordinary source store
  - `publish:`     {priority, description} for a store the catalog publishes;
                   absent means not published (read by build/store_declaration.py)

Requires pyyaml (already used by build.py via peppy).
"""
from __future__ import annotations

import os
import yaml
from pathlib import Path

PEP_CONFIG = "project_config.yaml"
STORES_DIR = Path(__file__).resolve().parent
CATALOG_KIND = "catalog"


def get_store_dirs(stores_dir=STORES_DIR, include_catalog=False) -> list:
    """Every source store directory under stores/, i.e. every dir with a PEP config.

    THE definition of "a store exists". stores/build.py re-exports it, and
    tools/validate_genome.py checks `build.store` against it, so a genome YAML
    and the build scripts can never disagree about the store list. Lives here
    (not in build.py) so the check stays importable without peppy/refget.

    A `kind: catalog` folder (refgenie-main) is left out unless include_catalog:
    it has no sources.csv, nothing builds it from this repo, and no genome YAML
    may name it as its build.store.
    """
    stores_dir = Path(stores_dir)
    return sorted(
        d for d in stores_dir.iterdir()
        if d.is_dir() and (d / PEP_CONFIG).exists()
        and (include_catalog or not is_catalog(d))
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



def is_catalog(store_dir) -> bool:
    """True for the build catalog's own store (`kind: catalog`)."""
    return load_pep(store_dir).get("kind") == CATALOG_KIND


def publish_settings(store_dir):
    """The store's `publish:` block (a dict), or None if it is not published."""
    block = load_pep(store_dir).get("publish")
    return block if isinstance(block, dict) else None
