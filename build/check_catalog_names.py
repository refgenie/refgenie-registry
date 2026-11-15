#!/usr/bin/env python3
"""Refuse to publish genome names the registry does not give.

People write genome names in one place, the registry (genome YAML ``name`` and
``aliases``, and each store's sources.csv). The catalog export unions two
copies of them: the build catalog's alias rows, which ``store sync`` replaces
every night from each published store's index, and the build node's own
RefgetStore names, which apply_metadata.py resets every night from the YAMLs.

This check reads the exported catalog before upload. Each exported name must be
a store's row in the build catalog, or a name the registry gives that genome.
Anything else is an extra: the check prints it and exits 1, and run_builds.sh
keeps the previous artifact. A registry name missing from the export is only a
warning, since its genome or store may not be built yet.

Usage: python3 build/check_catalog_names.py EXPORT.sqlite --db-config PATH
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "stores"))
from genome_rows import load_genomes  # noqa: E402


def read_aliases(path: str) -> list[tuple[str, str, str | None]]:
    """(name, digest, store_name or None) for every alias row of a catalog SQLite."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    return con.execute("SELECT name, genome_digest, store_name FROM alias").fetchall()


def check(exported: dict[str, str], store_owned: set[str], genomes) -> tuple[list, list]:
    """Extra exported names, and registry names the export lacks, as 'name -> digest'."""
    registry: dict[str, set[str]] = {}
    for g in genomes:
        digest = g.digest or exported.get(g.name)
        if digest:
            registry.setdefault(digest, set()).update([g.name, *g.aliases])
    extra = [
        f"{name} -> {digest}"
        for name, digest in sorted(exported.items())
        if name not in store_owned and name not in registry.get(digest, set())
    ]
    published = set(exported.values())
    missing = [
        f"{name} -> {digest}"
        for digest, names in sorted(registry.items())
        if digest in published
        for name in sorted(names)
        if "/" not in name and exported.get(name) != digest
    ]
    return extra, missing


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", help="the publish_catalog.sqlite catalog-export wrote")
    parser.add_argument("--db-config", default=os.environ.get("REFGENIE_DB_CONFIG_PATH"))
    args = parser.parse_args(argv)

    with open(args.db_config) as fh:
        build_catalog = yaml.safe_load(fh)["path"]
    exported = {name: digest for name, digest, _ in read_aliases(args.export)}
    store_owned = {name for name, _, store in read_aliases(build_catalog) if store}
    extra, missing = check(exported, store_owned, load_genomes())

    for line in missing:
        print(f"  names: WARN registry name not published: {line}")
    for line in extra:
        print(f"  names: EXTRA published name the registry does not give: {line}")
    print(f"names: {len(exported)} published, {len(extra)} extra, {len(missing)} missing")
    return 1 if extra else 0


if __name__ == "__main__":
    sys.exit(main())
