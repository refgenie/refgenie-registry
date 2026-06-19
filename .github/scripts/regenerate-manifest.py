#!/usr/bin/env python3
"""Regenerate the index/manifest.yaml from all index entries.

Walks all index/<genome>/<recipe>.yaml files and builds a complete
manifest with asset summary, total count, and timestamp.
"""

import argparse
import os
import sys
from datetime import datetime, timezone

import yaml


def parse_args():
    parser = argparse.ArgumentParser(description="Regenerate manifest from index entries")
    parser.add_argument(
        "--index-dir", default="index", help="Path to index directory"
    )
    return parser.parse_args()


def main():
    args = parse_args()
    index_dir = args.index_dir

    if not os.path.isdir(index_dir):
        print(f"Index directory not found: {index_dir}")
        sys.exit(1)

    assets = {}
    total = 0

    # Walk index/<genome>/<recipe>.yaml
    for genome_entry in sorted(os.listdir(index_dir)):
        genome_dir = os.path.join(index_dir, genome_entry)
        if not os.path.isdir(genome_dir):
            continue

        genome_assets = {}
        for recipe_file in sorted(os.listdir(genome_dir)):
            if not (recipe_file.endswith(".yaml") or recipe_file.endswith(".yml")):
                continue

            recipe_path = os.path.join(genome_dir, recipe_file)
            try:
                with open(recipe_path) as f:
                    entry = yaml.safe_load(f)
            except Exception as e:
                print(f"WARNING: Failed to read {recipe_path}: {e}")
                continue

            if not isinstance(entry, dict):
                continue

            recipe_name = os.path.splitext(recipe_file)[0]
            genome_assets[recipe_name] = {
                "status": entry.get("build", {}).get("status", "unknown"),
                "recipe_version": entry.get("recipe_version", ""),
                "built": entry.get("build", {}).get("timestamp", ""),
                "files": len(entry.get("files", [])),
            }
            total += 1

        if genome_assets:
            assets[genome_entry] = genome_assets

    manifest_path = os.path.join(index_dir, "manifest.yaml")

    # Load the existing manifest (if any) so we only refresh the `updated`
    # timestamp when the substantive content actually changed. Otherwise every
    # run would bump the timestamp and produce a no-op "regenerate" commit.
    existing = {}
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path) as f:
                existing = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"WARNING: Failed to read existing manifest: {e}")

    content_changed = (
        existing.get("assets") != assets
        or existing.get("total_assets") != total
    )

    if content_changed or not existing.get("updated"):
        updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        updated = existing["updated"]

    manifest = {
        "updated": updated,
        "assets": assets,
        "total_assets": total,
    }

    with open(manifest_path, "w") as f:
        yaml.dump(manifest, f, default_flow_style=False, sort_keys=False)

    print(f"Regenerated manifest: {manifest_path}")
    print(f"  Genomes: {len(assets)}")
    print(f"  Total assets: {total}")
    print(f"  Content changed: {content_changed}")


if __name__ == "__main__":
    main()
