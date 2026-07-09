#!/usr/bin/env python3
"""Publish the refgenie data channel from this registry.

A "data channel" is what a refgenie1 client syncs recipes and asset classes
from: an ``index.yaml`` plus the recipe / asset-class YAML files, served over
HTTP (GitHub Pages). This registry is the single source of truth for those
files, so the channel is published in the registry's OWN native layout -- no
flattening, no format conversion. A channel path maps 1:1 to a registry path:

    asset_classes/<name>.yaml        ->  channel/asset_classes/<name>.yaml
    recipes/<name>/recipe.yaml       ->  channel/recipes/<name>/recipe.yaml

The generated ``index.yaml`` uses the ``dir`` + ``files`` structure the client
parses (``refgenie.managers.sources.manager.IndexFile``); recipe entries carry
the ``<name>/recipe.yaml`` subpath, which the client resolves to
``<base>/recipes/<name>/recipe.yaml`` on fetch.

Only the channel artifact (``index.yaml``, ``asset_classes/``, ``recipes/``) is
written to the output dir -- nothing else from the repo is exposed.

Usage:
    python tools/build_channel.py                 # -> ./channel/
    python tools/build_channel.py -o /tmp/channel
    python tools/build_channel.py --registry-root . -o channel
"""

import argparse
import shutil
import sys
from pathlib import Path

import yaml

ASSET_CLASSES_DIR = "asset_classes"
RECIPES_DIR = "recipes"
RECIPE_FILE = "recipe.yaml"


def collect_asset_classes(registry_root: Path) -> list[str]:
    """Return sorted ``<name>.yaml`` basenames under ``asset_classes/``."""
    src = registry_root / ASSET_CLASSES_DIR
    if not src.is_dir():
        return []
    return sorted(f.name for f in src.iterdir() if f.is_file() and f.suffix == ".yaml")


def collect_recipes(registry_root: Path) -> list[str]:
    """Return sorted ``<name>/recipe.yaml`` relative paths under ``recipes/``.

    Every recipe lives in its own directory as ``recipe.yaml``. A recipe
    directory missing ``recipe.yaml`` is an error (we do not silently skip it).
    """
    src = registry_root / RECIPES_DIR
    if not src.is_dir():
        return []
    entries: list[str] = []
    missing: list[str] = []
    for d in sorted(p for p in src.iterdir() if p.is_dir()):
        if (d / RECIPE_FILE).is_file():
            entries.append(f"{d.name}/{RECIPE_FILE}")
        else:
            missing.append(d.name)
    if missing:
        raise SystemExit(
            f"error: recipe directories missing {RECIPE_FILE}: {', '.join(missing)}"
        )
    return entries


def build_channel(registry_root: Path, out_dir: Path) -> dict:
    asset_files = collect_asset_classes(registry_root)
    recipe_files = collect_recipes(registry_root)

    # Fresh output dir; copy the two source trees verbatim (nested layout kept).
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    shutil.copytree(registry_root / ASSET_CLASSES_DIR, out_dir / ASSET_CLASSES_DIR)
    shutil.copytree(registry_root / RECIPES_DIR, out_dir / RECIPES_DIR)

    index = {
        "asset_class": {"dir": ASSET_CLASSES_DIR, "files": asset_files},
        "recipe": {"dir": RECIPES_DIR, "files": recipe_files},
    }
    with open(out_dir / "index.yaml", "w") as f:
        yaml.dump(index, f, default_flow_style=False, sort_keys=False)
    return index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--registry-root", default=".",
                        help="Registry root containing asset_classes/ and recipes/ (default: .)")
    parser.add_argument("-o", "--out-dir", default="channel",
                        help="Output channel directory (default: channel)")
    args = parser.parse_args()

    registry_root = Path(args.registry_root).resolve()
    out_dir = Path(args.out_dir).resolve()

    index = build_channel(registry_root, out_dir)
    print(f"Built channel at {out_dir}")
    print(f"  asset classes: {len(index['asset_class']['files'])}")
    print(f"  recipes:       {len(index['recipe']['files'])}")
    print(f"  index:         {out_dir / 'index.yaml'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
