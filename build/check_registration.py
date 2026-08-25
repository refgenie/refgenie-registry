#!/usr/bin/env python3
"""Report which genomes in genomes/**/*.yaml are not registered in the catalog.

Motivation
----------
Twenty-six vertebrate genomes were merged in PRs #6, #8 and #9 between 2026-08-18
and 08-20. None of them existed anywhere afterwards -- not in a store, not in the
catalog, not in ``index/``. The nightly never failed and never warned; it reported
``coverage: 145/145 ... no gaps`` every morning after.

It could not have done otherwise. ``check_coverage.py`` compares
``pep/samples.csv`` to the catalog, so a genome that was never *requested* cannot
appear as a gap. Every check in the nightly was scoped to the build queue, and the
build queue was exactly the thing those 26 genomes were not in.

This is the check for the other question. It compares the GENOME LIST -- every
``genomes/**/*.yaml``, whatever its tier -- to the catalog. A genome at
``build.tier: store_only`` builds no assets and so has no coverage rows by design,
but it must still RESOLVE: its sequence is in a store, and it is browsable. If it
does not resolve, it exists only as a file in this repo.

Run it beside ``coverage:`` in the nightly summary. This is the check that would
have caught those 26 the morning after PR #6 merged.

What it CANNOT see
------------------
Like check_coverage.py, this is a question about STATE. A genome that resolves was
registered by some run, not necessarily tonight's.

Usage
-----
    python build/check_registration.py --db-config PATH            # report, exit 0
    python build/check_registration.py --db-config PATH --strict   # exit 1 if any gap
"""

# Matches check_coverage.py: run_builds.sh invokes these with a bare `python3`,
# which on a Rivanna login node is 3.6, so PEP 585/604 annotations must stay lazy.
from __future__ import annotations

import argparse
import glob
import os
import sys
from pathlib import Path

import yaml


def registry_root() -> Path:
    return Path(__file__).resolve().parent.parent


def read_genome_list(root: Path) -> list:
    """Return [(name, store, tier)] for every genome YAML, sorted by name."""
    genomes = []
    pattern = str(root / "genomes" / "**" / "*.yaml")
    for path in sorted(glob.glob(pattern, recursive=True)):
        with open(path) as handle:
            data = yaml.safe_load(handle)
        if not isinstance(data, dict) or not data.get("name"):
            continue
        build = data.get("build") or {}
        genomes.append((data["name"], build.get("store"), build.get("tier")))
    genomes.sort(key=lambda item: item[0])
    return genomes


def build_refgenie(db_config):
    from refgenie import Refgenie

    if db_config:
        os.environ["REFGENIE_DB_CONFIG_PATH"] = db_config
    return Refgenie()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-config", default=os.environ.get("REFGENIE_DB_CONFIG_PATH"))
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if any genome is unregistered (default: report and exit 0).",
    )
    args = parser.parse_args(argv)

    root = registry_root()
    genomes = read_genome_list(root)
    rg = build_refgenie(args.db_config)

    missing = []
    for name, store, tier in genomes:
        try:
            digest = rg.alias.resolve(name)
        except Exception:  # noqa: BLE001 - unresolvable reads as "not registered"
            digest = None
        if digest is None:
            missing.append((name, store, tier))

    total = len(genomes)
    print(f"registration: {total - len(missing)}/{total} genomes resolve in the catalog")

    if missing:
        by_store = {}
        for name, store, _tier in missing:
            by_store.setdefault(store or "(no store)", []).append(name)
        print(f"registration: {len(missing)} genome(s) NOT REGISTERED:")
        for store in sorted(by_store):
            names = sorted(by_store[store])
            shown = ", ".join(names[:12])
            more = f" (+{len(names) - 12} more)" if len(names) > 12 else ""
            print(f"  {store}: {shown}{more}")
        print("  These have a genomes/**/*.yaml but no catalog entry: their sequence")
        print("  was never loaded into the store named by build.store, or the store")
        print("  was never synced. Check build/sync_stores.py and the store build.")
    else:
        print("registration: no gaps — every genome in genomes/ resolves.")

    if missing and args.strict:
        print(
            "registration: FAILING (--strict). The genomes above are defined in this "
            "repo but do not exist in the catalog.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
