#!/usr/bin/env python3
"""Refresh index/<genome>/<recipe>.yaml from the refgenie1 build database.

After build/run_builds.sh dispatches asset builds, this script reads the
refgenie1 database (the SAME one populated by tools/import_recipes.py and built
into by `refgenie build`) and writes one index entry per built asset. The
entries match the schema consumed by .github/scripts/regenerate-manifest.py:

    build:
      status: complete
      timestamp: <ISO8601>
    recipe_version: <recipe version>
    files: [<seek key names>]

The manifest.yaml roll-up is regenerated separately by CI (regenerate-manifest),
so this script only writes the per-asset entries and leaves manifest.yaml alone.

Conservative: if the database is empty/unavailable, it writes nothing and exits
0 (so a dry run or a night with no completed builds produces no spurious index
churn).

Usage:
    python build/update_index.py [--db-config PATH] [--index-dir DIR]

With no --db-config it falls back to $REFGENIE_DB_CONFIG_PATH, then to refgenie1's
default config. The index is keyed by genome ALIAS (human-readable name) when one
exists, else by digest.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


def _registry_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _build_refgenie(db_config: str | None):
    from refgenie import Refgenie

    if db_config:
        rg = Refgenie(database_config_path=db_config, suppress_migrations=False)
    else:
        rg = Refgenie()
    rg.init()
    return rg


def _alias_for(rg, digest: str) -> str:
    try:
        aliases = [a.name for a in rg.alias.list_all(genome_digest=digest)]
        if aliases:
            return sorted(aliases)[0]
    except Exception:
        pass
    return digest


def write_index(rg, index_dir: Path) -> int:
    """Write index entries for every built asset. Returns the count written."""
    asset_data, _aliases = rg.asset.list_all(include_seek_keys=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    written = 0

    for genome_digest, asset_strings in asset_data.items():
        genome_key = _alias_for(rg, genome_digest)
        # Collapse "group.seekkey:name" strings into per-recipe seek-key lists.
        #
        # A group can hold SEVERAL asset names at once -- rebuilding an asset under
        # a corrected name (e.g. hisat2_index 7.5.0 -> 2.2.2) adds a new,
        # separately digested asset and leaves the old one in place. list_all()
        # returns both, in no defined order, so picking the first-seen name (the
        # old `setdefault` behaviour) published whichever row the query happened to
        # yield first -- in practice the OLDEST, i.e. exactly the name the rebuild
        # was meant to retire. It also unioned the seek keys of every asset in the
        # group into one `files` list, describing an asset that does not exist.
        #
        # That is a silent, client-visible fault: index/ is what clients resolve,
        # so a stale asset_name republishes the superseded object, and it strands
        # the newly built one. It also blocks garbage collection -- the superseded
        # S3 object cannot be deleted while the published index still points at it.
        #
        # The data model already answers "which name does this group publish":
        # AssetName.is_default, one per group (enforced by a partial unique index)
        # and settable through asset.set_default(). Honour it, and take the seek
        # keys of THAT asset only. If a group somehow has no default, skip it
        # rather than guess -- publishing an arbitrary name is what caused this.
        try:
            group_names = {
                s.partition(":")[0].partition(".")[0] for s in asset_strings
            }
            defaults = {}
            for group in group_names:
                try:
                    defaults[group] = rg.asset.get_default(
                        asset_group_name=group, genome_digest=genome_digest
                    )
                except Exception as e:
                    print(f"  index: SKIP {genome_key}/{group}: no default asset ({e})")
        except Exception as e:  # pragma: no cover - defensive
            print(f"  index: cannot resolve defaults for {genome_key}: {e}")
            defaults = {}

        by_recipe: dict[str, dict[str, set]] = {}
        for s in asset_strings:
            # form: "<group>.<seek>:<name>"  (include_seek_keys=True)
            group_seek, _, asset_name = s.partition(":")
            group, _, seek = group_seek.partition(".")
            if group not in defaults or asset_name != defaults[group]:
                continue
            entry = by_recipe.setdefault(group, {"name": asset_name, "files": set()})
            if seek:
                entry["files"].add(seek)

        # get_default() returns the literal string "default" when a group has no
        # is_default row, rather than raising. For fasta/fasta_index that IS the
        # real asset name and matches; for a versioned group it matches nothing and
        # the group would vanish from the index without a word. Dropping an entry
        # is the safe direction (a missing pointer beats a wrong one), but it must
        # not be silent -- say so, so a gap is diagnosable from the build log.
        for group in sorted(group_names - set(by_recipe)):
            print(
                f"  index: WARNING no asset in {genome_key}/{group} matches its "
                f"default name {defaults.get(group)!r}; leaving its index entry unchanged"
            )

        gdir = index_dir / genome_key
        gdir.mkdir(parents=True, exist_ok=True)
        for recipe_name, info in by_recipe.items():
            recipe_version = ""
            try:
                recipe_version = rg.recipe.get(recipe_name).version or ""
            except Exception:
                pass
            entry = {
                "genome": genome_key,
                "genome_digest": genome_digest,
                "recipe": recipe_name,
                "recipe_version": recipe_version,
                "asset_name": info["name"],
                "build": {"status": "complete", "timestamp": now},
                "files": sorted(info["files"]),
            }
            out = gdir / f"{recipe_name}.yaml"
            with open(out, "w") as fh:
                yaml.safe_dump(entry, fh, default_flow_style=False, sort_keys=False)
            written += 1
            print(f"  index: wrote {out.relative_to(index_dir.parent)}")

    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-config", default=os.environ.get("REFGENIE_DB_CONFIG_PATH"))
    parser.add_argument("--index-dir", type=Path, default=_registry_root() / "index")
    args = parser.parse_args(argv)

    try:
        rg = _build_refgenie(args.db_config)
    except Exception as exc:  # noqa: BLE001
        print(f"update_index: could not open refgenie DB ({exc}); nothing to do.")
        return 0

    args.index_dir.mkdir(parents=True, exist_ok=True)
    count = write_index(rg, args.index_dir)
    print(f"update_index: wrote {count} index entr{'y' if count == 1 else 'ies'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
