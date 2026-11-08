#!/usr/bin/env python3
"""Print the published store list, read from stores/*/project_config.yaml.

The registry is the source of truth for which RefgetStores the public catalog
publishes. A store is published when its project_config.yaml has a `publish:`
block:

    publish:
      priority: 40          # lower wins alias ties; unique across stores
      description: Illumina iGenomes reference FASTAs

Its URL is derived, never written: ${REFGETSTORE_PUBLIC_URL}/<name>/.

Two outputs, both for build/run_builds.sh:

  (default)   JSON array of the build catalog's federated stores, every
              published store except the `kind: catalog` one, in the shape
              `refgenie store apply --file` takes:
              [{"name", "url", "type": "remote", "priority", "description"}]
  --catalog   one line, `name url priority`, for the `kind: catalog` store
              (refgenie-main, the catalog's own store), which catalog-export
              publishes as a store row via --catalog-store-name/-url/-priority.

Usage:
    python3 build/store_declaration.py > stores.json
    python3 build/store_declaration.py --catalog
"""

# Matches check_coverage.py: run_builds.sh invokes these with a bare `python3`,
# which on a Rivanna login node is 3.6, so PEP 585/604 annotations must stay lazy.
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STORES_DIR = REPO / "stores"

sys.path.insert(0, str(STORES_DIR))
from store_config import CATALOG_KIND, get_store_dirs, load_pep  # noqa: E402

URL_ENV = "REFGETSTORE_PUBLIC_URL"


class DeclarationError(Exception):
    pass


def read_stores(stores_dir=STORES_DIR, public_url=None) -> list:
    """Every published store as a dict, sorted by priority; raises on bad config.

    Each dict is {"name", "url", "type", "priority", "description", "kind"}.
    """
    if public_url is None:
        public_url = os.environ.get(URL_ENV, "")
    public_url = public_url.strip().rstrip("/")
    if not public_url:
        raise DeclarationError(
            f"{URL_ENV} is not set. Source infra/rivanna/env.sh first."
        )

    errors = []
    published = []
    catalogs = []
    for store_dir in get_store_dirs(stores_dir, include_catalog=True):
        name = store_dir.name
        config = load_pep(store_dir)
        kind = config.get("kind")
        if kind not in (None, CATALOG_KIND):
            errors.append(f"{name}: unknown kind {kind!r} (only {CATALOG_KIND!r} is allowed)")
            continue
        if kind == CATALOG_KIND:
            catalogs.append(name)
            if (store_dir / "sources.csv").exists():
                errors.append(f"{name}: a kind: catalog store has no sources.csv")
        block = config.get("publish")
        if block is None:
            if kind == CATALOG_KIND:
                errors.append(f"{name}: the kind: catalog store must have a publish: block")
            continue
        if not isinstance(block, dict):
            errors.append(f"{name}: publish: must be a mapping with priority and description")
            continue
        if config.get("nightly", True) is False:
            errors.append(f"{name}: has nightly: false, so it cannot be published "
                          f"(remove publish: or turn the nightly back on)")
            continue
        priority = block.get("priority")
        if isinstance(priority, bool) or not isinstance(priority, int):
            errors.append(f"{name}: publish.priority must be an integer, got {priority!r}")
            continue
        description = block.get("description")
        if description is not None and not isinstance(description, str):
            errors.append(f"{name}: publish.description must be a string")
            continue
        unknown = sorted(set(block) - {"priority", "description"})
        if unknown:
            errors.append(f"{name}: unknown publish: keys {', '.join(unknown)}")
            continue
        published.append({
            "name": name,
            "url": f"{public_url}/{name}/",
            "type": "remote",
            "priority": priority,
            "description": description,
            "kind": kind,
        })

    if len(catalogs) != 1:
        errors.append(f"expected exactly one kind: catalog store, found {len(catalogs)}"
                      + (f" ({', '.join(catalogs)})" if catalogs else ""))
    by_priority = {}
    for store in published:
        by_priority.setdefault(store["priority"], []).append(store["name"])
    for priority, names in sorted(by_priority.items()):
        if len(names) > 1:
            errors.append(f"publish.priority {priority} is used by more than one "
                          f"store: {', '.join(names)}")
    if errors:
        raise DeclarationError("\n  ".join(["invalid store configs:"] + errors))
    return sorted(published, key=lambda s: s["priority"])


def declaration(stores: list) -> list:
    """The federated stores (not the catalog store) for `refgenie store apply`."""
    out = []
    for store in stores:
        if store["kind"] == CATALOG_KIND:
            continue
        entry = {k: store[k] for k in ("name", "url", "type", "priority")}
        if store["description"]:
            entry["description"] = store["description"]
        out.append(entry)
    return out


def catalog_store(stores: list) -> dict:
    return next(s for s in stores if s["kind"] == CATALOG_KIND)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--catalog", action="store_true",
                        help="print `name url priority` of the kind: catalog store")
    args = parser.parse_args(argv)
    try:
        stores = read_stores()
    except DeclarationError as exc:
        print(f"store_declaration: ERROR {exc}", file=sys.stderr)
        return 1
    if args.catalog:
        store = catalog_store(stores)
        print(f"{store['name']} {store['url']} {store['priority']}")
    else:
        print(json.dumps(declaration(stores), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
