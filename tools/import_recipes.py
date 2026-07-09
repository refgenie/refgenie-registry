#!/usr/bin/env python3
"""
Thin loader: refgenie-registry (refgenie-native) -> refgenie1 DB.

Per the ADR "Single canonical recipe model: refgenie-native (refgenie is the
build system)", the registry stores recipes in refgenie1's NATIVE recipe model.
There is exactly one canonical recipe model and NO bioconda->refgenie
translation. refgenie IS the build system: it generates a Snakemake workflow
from its own recipe database and builds each asset via ``refgenie1 build`` in a
container. This loader simply hands the registry's native recipes and asset
classes to refgenie1's managers.

What this tool does:

1. Build a ``Refgenie`` instance (in-memory SQLite by default, or from a
   ``--db-config``).
2. Load every ``asset_classes/*.yaml`` via ``AssetClassManager.add`` unchanged.
3. Load every ``recipes/*/recipe.yaml`` via ``RecipeManager.add`` unchanged
   EXCEPT for stripping optional additive non-runtime keys that refgenie1's
   strict ``Recipe`` model does not accept.

Import is IDEMPOTENT ("sync", not "overwrite"). The build catalog is persistent
and re-imported every night, so any asset class or recipe whose ``(name,
version)`` is already present is left untouched and SKIPPED — versioned
definitions are immutable, so a matching version in the catalog is by definition
identical. A genuinely changed definition bumps its version and imports as a new
version. This deliberately avoids the overwrite path: ``AssetClassManager.add(
exists_overwrite=True)`` would call ``remove()``, which raises ``ConfigError``
when an existing recipe references the asset class, aborting the whole import.
The overwrite path is never exercised.

The ONLY transformation performed is dropping the additive non-runtime keys
``tags``, ``outputs``, ``test``, ``resources``, and ``metadata`` (provenance /
CI / UX metadata that the builder ignores). No runtime field is renamed or
translated -- the native build fields (``command_templates``,
``input_assets``/``input_files``/``input_params``, ``docker_image``,
``custom_seek_keys``, ``default_asset``, ``output_asset_class``) pass through
verbatim.

Usage::

    python tools/import_recipes.py [--db-config PATH] [--registry-root DIR]

With no ``--db-config`` an in-memory SQLite database is used (useful for tests
and dry runs). The loader is also importable as a library: call
``import_registry(refgenie, registry_root)``.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

# Default push_command for the asset remote. folder_sync strategy: one
# `aws s3 sync` per remote, substituting {genome_stage_folder} and {prefix}.
# It handles both archive (.tgz) and file-mode assets, skips unchanged objects,
# and preserves the <genome_digest>/<group>/<asset> layout under the prefix.
DEFAULT_ASSET_PUSH_COMMAND = "aws s3 sync {genome_stage_folder} {prefix}/ --follow-symlinks"

# ---------------------------------------------------------------------------
# Native vs. additive (non-runtime) recipe fields
# ---------------------------------------------------------------------------

#: Optional additive non-runtime keys that may ride alongside a native recipe
#: for provenance / CI / UX. refgenie1's strict Recipe model does not accept
#: them, so they are stripped before the recipe is handed to RecipeManager.add.
#: This is the ONLY transformation this loader performs.
NON_NATIVE_RECIPE_KEYS = ("tags", "outputs", "test", "resources", "metadata")


# ---------------------------------------------------------------------------
# Loading helpers
# ---------------------------------------------------------------------------


def load_yaml(path: Path) -> dict[str, Any]:
    with open(path) as fh:
        return yaml.safe_load(fh)


def discover_asset_classes(registry_root: Path) -> list[Path]:
    return sorted((registry_root / "asset_classes").glob("*.yaml"))


def discover_recipes(registry_root: Path) -> list[Path]:
    return sorted((registry_root / "recipes").glob("*/recipe.yaml"))


def strip_non_native_keys(recipe_dict: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return a copy of ``recipe_dict`` with the additive non-runtime keys
    removed, plus the list of keys that were stripped.

    This is the loader's only transformation. Runtime/native fields are left
    untouched.
    """
    stripped = [k for k in NON_NATIVE_RECIPE_KEYS if k in recipe_dict]
    native = {k: v for k, v in recipe_dict.items() if k not in NON_NATIVE_RECIPE_KEYS}
    return native, stripped


# ---------------------------------------------------------------------------
# Importing into refgenie1
# ---------------------------------------------------------------------------


def import_registry(
    refgenie: Any,
    registry_root: Path,
    verbose: bool = True,
) -> dict[str, Any]:
    """Load all asset classes and (native) recipes from ``registry_root`` into
    the provided ``Refgenie`` instance.

    Idempotent "sync" semantics: any asset class or recipe whose ``(name,
    version)`` is already present in the (persistent) catalog is SKIPPED, not
    re-added or overwritten. Only genuinely new ``(name, version)`` definitions
    are added, via the default ``exists_overwrite=False`` path.

    Returns a summary dict with counts, skips, and any notes.
    """

    def log(msg: str) -> None:
        if verbose:
            print(msg)

    summary: dict[str, Any] = {
        "asset_classes_imported": [],
        "recipes_imported": [],
        "skipped": [],
        "notes": [],
        "errors": [],
    }

    # 1. Asset classes first (no interdependencies). Registry asset classes are
    #    already refgenie-native and are loaded unchanged. Skip any (name,
    #    version) already present so re-import into a populated catalog is a
    #    no-op (never touches the ConfigError-raising overwrite/remove path).
    for path in discover_asset_classes(registry_root):
        d = load_yaml(path)
        name, version = d["name"], d.get("version")
        try:
            if version is not None and refgenie.asset_class.exists(name, version):
                summary["skipped"].append(f"asset_class {name} v{version}")
                log(f"[asset_class] skip {name} v{version} (already present)")
                continue
            refgenie.asset_class.add(path)
            summary["asset_classes_imported"].append(name)
            log(f"[asset_class] loaded {name} v{version}")
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(f"asset_class {name}: {exc}")
            raise

    # 2. Recipes. They are already refgenie-native; the only transformation is
    #    stripping additive non-runtime keys (tags/outputs/test/resources/
    #    metadata), which the strict Recipe model does not accept. The
    #    native-only recipe is written to a temp YAML so RecipeManager.add()
    #    (which loads from a path/URL) can ingest it. Skip any (name, version)
    #    already present; only write the temp file when actually adding.
    for path in discover_recipes(registry_root):
        recipe_dict = load_yaml(path)
        name, version = recipe_dict["name"], recipe_dict["version"]
        if refgenie.recipe.exists(name, version):
            summary["skipped"].append(f"recipe {name} v{version}")
            log(f"[recipe] skip {name} v{version} (already present)")
            continue

        native_recipe, stripped = strip_non_native_keys(recipe_dict)
        if stripped:
            note = f"stripped non-native keys: {', '.join(stripped)}"
            summary["notes"].append(f"{name}: {note}")
            log(f"[recipe:{name}] {note}")

        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as tmp:
            yaml.safe_dump(native_recipe, tmp, default_flow_style=False, sort_keys=False)
            tmp_path = Path(tmp.name)
        try:
            refgenie.recipe.add(tmp_path)
            summary["recipes_imported"].append(name)
            log(
                f"[recipe] loaded {name} v{version} "
                f"-> {native_recipe['output_asset_class']}"
            )
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(f"recipe {name}: {exc}")
            raise
        finally:
            tmp_path.unlink(missing_ok=True)

    return summary


def register_asset_remote(
    refgenie: Any,
    prefix: str,
    push_command: str,
    name: str = "asset-s3",
    verbose: bool = True,
) -> None:
    """Register (upsert) the S3 asset-push remote in the build DB.

    The remote MUST exist before any SLURM build child stages an asset: at
    stage time ``refgenie build ... --push-to <prefix>`` resolves ``<prefix>``
    against ``Remote.prefix`` to write the ``RemoteAssetLink(pushed=False)``
    push-intent record. ``prefix`` here MUST therefore equal the ``--push-to``
    token injected into the generated Snakefile.

    Idempotent: ``upsert_remote`` matches on the ``description`` (``name``)
    field, so re-running against the persistent catalog updates in place rather
    than creating a duplicate remote.
    """
    from refgenie.db.models import RemoteType

    refgenie.configuration.upsert_remote(
        name=name,
        type=RemoteType.s3,
        prefix=prefix,
        push_command=push_command,
    )
    if verbose:
        print(f"[remote] upserted '{name}' (s3) prefix={prefix} push_command={push_command!r}")


def build_refgenie(db_config: str | None, genome_folder: Path | None = None) -> Any:
    """Construct a Refgenie instance.

    With ``db_config`` None, an in-memory SQLite database is used.
    """
    from refgenie import Refgenie
    from sqlmodel import create_engine
    from sqlmodel.pool import StaticPool

    if db_config:
        refgenie = Refgenie(database_config_path=db_config, suppress_migrations=False)
    else:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            echo=False,
        )
        refgenie = Refgenie(database_engine=engine, suppress_migrations=True)

    if genome_folder is not None:
        refgenie.init(genome_folder=genome_folder, genome_stage_folder=genome_folder)
    else:
        refgenie.init()
    return refgenie


def default_registry_root() -> Path:
    """The registry root is the parent of this tools/ directory."""
    return Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-config",
        default=None,
        help="Path to a refgenie1 database config file. Default: in-memory SQLite.",
    )
    parser.add_argument(
        "--registry-root",
        type=Path,
        default=default_registry_root(),
        help="Path to the registry root (containing asset_classes/ and recipes/).",
    )
    parser.add_argument(
        "--genome-folder",
        type=Path,
        default=None,
        help="Genome folder for refgenie init (default: refgenie's default).",
    )
    parser.add_argument(
        "--snakefile",
        type=Path,
        default=None,
        help=(
            "If set, after loading the registry, generate a Snakefile to this "
            "path via populate_snakefile_template (proves the registry drives "
            "the real refgenie build system)."
        ),
    )
    parser.add_argument(
        "--asset-remote-prefix",
        default=os.environ.get("REFGENIE_ASSET_S3"),
        help=(
            "S3 prefix for the built-asset push remote (default: "
            "$REFGENIE_ASSET_S3). MUST equal the --push-to token injected into "
            "the Snakefile. If unset, no asset remote is registered (staging "
            "records no push intent)."
        ),
    )
    parser.add_argument(
        "--asset-remote-push-command",
        default=DEFAULT_ASSET_PUSH_COMMAND,
        help=(
            "push_command template for the asset remote (folder_sync). "
            f"Default: {DEFAULT_ASSET_PUSH_COMMAND!r}"
        ),
    )
    parser.add_argument(
        "--asset-remote-name",
        default="asset-s3",
        help="Description/name used to upsert the asset remote (default: asset-s3).",
    )
    args = parser.parse_args(argv)

    refgenie = build_refgenie(args.db_config, genome_folder=args.genome_folder)
    summary = import_registry(refgenie, args.registry_root)

    # Register the S3 asset-push remote so build children can resolve --push-to
    # at stage time. Idempotent across nightly runs via upsert-by-name. Skip
    # (with a warning) when no prefix is configured so local/in-memory dry runs
    # still work without an S3 target.
    if args.asset_remote_prefix:
        register_asset_remote(
            refgenie,
            prefix=args.asset_remote_prefix,
            push_command=args.asset_remote_push_command,
            name=args.asset_remote_name,
        )
    else:
        print(
            "[remote] WARNING: no asset remote prefix (REFGENIE_ASSET_S3 unset "
            "and --asset-remote-prefix not given); staging will record no push "
            "intent and `refgenie push` will have nothing to upload."
        )

    print("\n=== Load summary ===")
    print(f"Asset classes loaded: {len(summary['asset_classes_imported'])}")
    print(f"Recipes loaded:       {len(summary['recipes_imported'])}")
    print(f"Skipped (present):    {len(summary['skipped'])}")
    if summary["notes"]:
        print(f"Notes:                {len(summary['notes'])}")
    if summary["errors"]:
        print(f"Errors:               {len(summary['errors'])}")
        for err in summary["errors"]:
            print(f"  - {err}")
        return 1

    if args.snakefile is not None:
        from refgenie.snakefile.generate import populate_snakefile_template

        populate_snakefile_template(refgenie, args.snakefile)
        size = args.snakefile.stat().st_size
        print(f"Snakefile generated:  {args.snakefile} ({size} bytes)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
