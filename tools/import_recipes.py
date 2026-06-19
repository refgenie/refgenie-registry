#!/usr/bin/env python3
"""
Converting recipe importer: refgenie-registry (bioconda-style) -> refgenie1 DB.

This tool reads the registry's typed asset classes (``asset_classes/*.yaml``)
and bioconda-style recipes (``recipes/<name>/recipe.yaml``, each carrying a new
``produces:`` field) and loads them into a refgenie1 database via the refgenie1
Python API.

Per the ADR ``registry_recipe_asset_class_format_adr.md``:

* Asset classes are imported FIRST (they have no interdependencies). The registry
  asset-class YAML shape maps directly onto refgenie1's ``AssetClass`` model, so
  they are fed to ``AssetClassManager.add()`` unchanged.
* Recipes are then CONVERTED from the bioconda-style form into refgenie1's native
  recipe shape and fed to ``RecipeManager.add()``.

Conversion mapping (bioconda -> refgenie1):

* ``build.command`` (multi-line string)  -> ``command_templates`` (list of lines)
* ``requires.assets``                     -> ``input_assets`` (dict keyed by name)
* ``requires.files``                      -> ``input_files``
* ``produces``                            -> ``output_asset_class``
* build vars ``{output_dir}``/``{genome}``/``{fasta}``/``{threads}``/``{<file>}``
  are rewritten to refgenie1 Jinja templating
  (``{{values.output_folder}}`` / ``{{values.genome_digest}}`` /
  ``{{values.assets[...].seek_keys_dict[...]}}`` / ``{{values.params[...]}}`` /
  ``{{values.files[...]}}``).
* ``outputs`` / ``test`` / ``resources`` / ``tags`` / ``metadata`` are dropped
  (not part of the refgenie1 recipe model; ``outputs`` is CI-only per the ADR).

Usage::

    python tools/import_recipes.py [--db-config PATH] [--registry-root DIR]

With no ``--db-config`` an in-memory SQLite database is used (useful for tests
and dry runs). The importer is also importable as a library: call
``import_registry(refgenie, registry_root)``.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Build-variable -> refgenie1 templating
# ---------------------------------------------------------------------------

#: Registry build vars that are always parameters in refgenie1.
#: (``threads`` is special-cased; the rest map to ``{{values.params["x"]}}``.)
KNOWN_PARAM_VARS = {
    "threads",
    "kmer",
    "mersize",
    "minocc",
    "memlimit",
    "context",
}

#: Registry build vars that map to a fixed refgenie1 token.
FIXED_VAR_MAP = {
    "output_dir": "{{values.output_folder}}",
    "genome": "{{values.genome_digest}}",
    "refget_store_path": "{{values.refget_store_path}}",
    "genome_digest": "{{values.genome_digest}}",
}



@dataclass
class ConversionResult:
    """Outcome of converting one bioconda recipe to refgenie1 form."""

    recipe: dict[str, Any]
    notes: list[str] = field(default_factory=list)


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


def build_asset_class_index(registry_root: Path) -> dict[str, str]:
    """Map asset-class name -> its (only) version, from the asset_classes dir."""
    index: dict[str, str] = {}
    for path in discover_asset_classes(registry_root):
        d = load_yaml(path)
        index[d["name"]] = d.get("version", "0.0.0")
    return index


def build_primary_seek_key_index(registry_root: Path) -> dict[str, str]:
    """Map asset-class name -> its primary (first) seek key.

    When a registry build command refers to an input asset by a bare variable
    (e.g. ``{fasta}``, ``{ensembl_gtf}``), the file it means is the asset's
    primary file, addressed in refgenie1 via the asset class's first seek key.
    """
    index: dict[str, str] = {}
    for path in discover_asset_classes(registry_root):
        d = load_yaml(path)
        seek_keys = d.get("seek_keys") or {}
        if seek_keys:
            index[d["name"]] = next(iter(seek_keys))
    return index


def build_recipe_produces_index(registry_root: Path) -> dict[str, str]:
    """Map recipe name -> the asset class it produces."""
    index: dict[str, str] = {}
    for path in discover_recipes(registry_root):
        d = load_yaml(path)
        index[d["name"]] = d.get("produces")
    return index


# ---------------------------------------------------------------------------
# Command conversion
# ---------------------------------------------------------------------------


def _asset_seek_key_token(asset_input_name: str, seek_key: str) -> str:
    return (
        f'{{{{values.genome_folder}}}}/{{{{values.assets["{asset_input_name}"]'
        f'.seek_keys_dict["{seek_key}"]}}}}'
    )


def convert_command(
    command: str,
    asset_seek_keys: dict[str, str],
    file_inputs: dict[str, dict[str, Any]],
) -> list[str]:
    """Convert a multi-line bioconda build command into command_templates.

    Each non-blank line becomes one entry. Multi-pipe commands stay on a single
    line (already one entry). Registry ``{var}`` placeholders are rewritten to
    refgenie1 ``{{values...}}`` Jinja tokens. Literal ``{{ }}`` escaping used in
    awk blocks (doubled braces) is collapsed back to single braces, since
    refgenie1 templates are rendered by Jinja where ``{{ }}`` is the variable
    delimiter and shell/awk braces must be literal.

    Args:
        command: The raw multi-line build command.
        asset_seek_keys: Map of input-asset name -> the seek key that the bare
            ``{name}`` variable should resolve to (the asset class's primary
            seek key).
        file_inputs: The recipe's input file dict (names only are used).
    """
    file_names = set(file_inputs)

    lines = [ln for ln in command.splitlines() if ln.strip()]
    out_lines: list[str] = []
    for line in lines:
        out_lines.append(_convert_line(line, asset_seek_keys, file_names))
    return out_lines


# Matches a single {...} group that is NOT part of a doubled {{...}} brace.
_VAR_RE = re.compile(r"(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_]*)\}(?!\})")


def _convert_line(
    line: str, asset_seek_keys: dict[str, str], file_names: set[str]
) -> str:
    # First, collapse awk-style doubled braces {{ ... }} (used in the registry to
    # escape literal braces) down to single braces. Registry recipes double the
    # braces of literal awk blocks; refgenie1 command_templates are plain strings
    # (Jinja is only applied to {{values...}} tokens we emit), so literal awk
    # braces must be single.
    line = line.replace("{{", "\x00").replace("}}", "\x01")

    def repl(match: re.Match) -> str:
        var = match.group(1)
        if var in FIXED_VAR_MAP:
            return FIXED_VAR_MAP[var]
        if var in asset_seek_keys:
            return _asset_seek_key_token(var, asset_seek_keys[var])
        if var in file_names:
            return f'{{{{values.files["{var}"]}}}}'
        if var in KNOWN_PARAM_VARS:
            return f'{{{{values.params["{var}"]}}}}'
        # Unknown bare {var}: treat as a parameter (safest default).
        return f'{{{{values.params["{var}"]}}}}'

    line = _VAR_RE.sub(repl, line)
    # Restore the (now confirmed-literal) awk braces.
    line = line.replace("\x00", "{").replace("\x01", "}")
    return line


# ---------------------------------------------------------------------------
# Input conversion
# ---------------------------------------------------------------------------


def _command_references_colocated_fasta(command: str, asset_input_name: str) -> bool:
    """Detect whether the build command relies on the parent fasta being placed
    inside the output dir (i.e. references ``{output_dir}/{genome}.fa`` or simply
    operates on ``{output_dir}`` expecting the fasta to be present there).

    These are the cases that require a ``colocate`` declaration so refgenie1
    symlinks the parent fasta into the child's output folder at build time.
    """
    if asset_input_name != "fasta":
        return False
    # Direct reference to a fasta placed under the output dir. Require that the
    # `.fa` is the full extension (not the start of `.fai`/`.fa.fai`), so that
    # recipes which merely write an index next to the parent (e.g. fasta_index
    # reading {fasta} in place) are not treated as colocating the fasta.
    if re.search(r"\{output_dir\}/\{genome\}\.fa(?![.a-zA-Z])", command):
        return True
    # bismark_genome_preparation operates on the output dir and expects the
    # fasta to be colocated there.
    if "bismark_genome_preparation" in command:
        return True
    return False


def convert_inputs(
    requires: dict[str, Any],
    command: str,
    asset_class_index: dict[str, str],
    recipe_produces_index: dict[str, str],
    primary_seek_key_index: dict[str, str],
    notes: list[str],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    """Convert ``requires.assets`` -> input_assets and ``requires.files`` ->
    input_files.

    Returns ``(input_assets, input_files, asset_seek_keys)`` where
    ``asset_seek_keys`` maps each input-asset name to the seek key its bare
    ``{name}`` variable should resolve to (the resolved asset class's primary
    seek key).
    """
    input_assets: dict[str, Any] = {}
    asset_seek_keys: dict[str, str] = {}
    for asset in requires.get("assets") or []:
        name = asset["name"]
        # Resolve the referenced name to an asset class.
        if name in asset_class_index:
            asset_class_name = name
        elif name in recipe_produces_index and recipe_produces_index[name]:
            asset_class_name = recipe_produces_index[name]
            notes.append(
                f"input asset '{name}' is a recipe name; resolved to its "
                f"produced asset class '{asset_class_name}'"
            )
        else:
            raise ValueError(
                f"input asset '{name}' does not resolve to a known asset class "
                f"or recipe"
            )
        spec: dict[str, Any] = {
            "asset_class": asset_class_name,
            "default": name,
        }
        if asset.get("description"):
            spec["description"] = asset["description"]
        if _command_references_colocated_fasta(command, name):
            spec["colocate"] = [{"source_key": "fasta"}]
            notes.append(
                f"input asset '{name}' colocated (command references parent fasta "
                f"under the output dir)"
            )
        input_assets[name] = spec
        # The bare {name} variable resolves to the asset class's primary seek key.
        asset_seek_keys[name] = primary_seek_key_index.get(asset_class_name, name)

    input_files: dict[str, Any] = {}
    for f in requires.get("files") or []:
        name = f["name"]
        spec = {}
        if f.get("description"):
            spec["description"] = f["description"]
        input_files[name] = spec

    return input_assets, input_files, asset_seek_keys


def _collect_param_vars(command: str, known_inputs: set[str]) -> list[str]:
    """Find bare {var} placeholders in the command that are parameters."""
    found: list[str] = []
    line = command.replace("{{", "\x00").replace("}}", "\x01")
    for match in _VAR_RE.finditer(line):
        var = match.group(1)
        if var in FIXED_VAR_MAP or var in known_inputs:
            continue
        if var in KNOWN_PARAM_VARS or var not in known_inputs:
            if var not in found:
                found.append(var)
    return found


#: Sensible defaults for known parameters (mirrors the refgenie1 native recipes).
PARAM_DEFAULTS = {
    "threads": "1",
    "kmer": "31",
    "mersize": "30",
    "minocc": "2",
    "memlimit": "8",
    "context": "CG",
}


# ---------------------------------------------------------------------------
# Recipe conversion
# ---------------------------------------------------------------------------


def convert_recipe(
    recipe_dict: dict[str, Any],
    asset_class_index: dict[str, str],
    recipe_produces_index: dict[str, str],
    primary_seek_key_index: dict[str, str],
) -> ConversionResult:
    """Convert a single bioconda-style recipe dict into refgenie1 recipe form."""
    notes: list[str] = []
    name = recipe_dict["name"]
    version = recipe_dict["version"]
    produces = recipe_dict.get("produces")
    if not produces:
        raise ValueError(f"recipe '{name}' has no 'produces' field")

    requires = recipe_dict.get("requires") or {}
    command = (recipe_dict.get("build") or {}).get("command", "")

    input_assets, input_files, asset_seek_keys = convert_inputs(
        requires,
        command,
        asset_class_index,
        recipe_produces_index,
        primary_seek_key_index,
        notes,
    )

    known_inputs = set(input_assets) | set(input_files)
    param_vars = _collect_param_vars(command, known_inputs)
    input_params: dict[str, Any] = {}
    for var in param_vars:
        input_params[var] = {
            "default": PARAM_DEFAULTS.get(var, ""),
            "description": f"{var} parameter",
        }

    command_templates = convert_command(command, asset_seek_keys, input_files)

    converted: dict[str, Any] = {
        "name": name,
        "version": version,
        "output_asset_class": produces,
        "description": recipe_dict.get("description", ""),
        "command_templates": command_templates,
        "input_files": input_files or {},
        "input_params": input_params or {},
        "input_assets": input_assets or {},
        "docker_image": "databio/refgenie",
        # The registry does not encode a refgenie1-style versioned default asset;
        # use the literal "default" asset group.
        "default_asset": "default",
    }
    return ConversionResult(recipe=converted, notes=notes)


# ---------------------------------------------------------------------------
# Importing into refgenie1
# ---------------------------------------------------------------------------


def import_registry(
    refgenie: Any,
    registry_root: Path,
    exists_overwrite: bool = True,
    verbose: bool = True,
) -> dict[str, Any]:
    """Import all asset classes and recipes from ``registry_root`` into the
    provided ``Refgenie`` instance.

    Returns a summary dict with counts and the list of conversion notes.
    """
    import tempfile

    def log(msg: str) -> None:
        if verbose:
            print(msg)

    asset_class_index = build_asset_class_index(registry_root)
    recipe_produces_index = build_recipe_produces_index(registry_root)
    primary_seek_key_index = build_primary_seek_key_index(registry_root)

    summary: dict[str, Any] = {
        "asset_classes_imported": [],
        "recipes_imported": [],
        "notes": [],
        "errors": [],
    }

    # 1. Asset classes first (no interdependencies). Registry shape maps directly.
    for path in discover_asset_classes(registry_root):
        d = load_yaml(path)
        try:
            refgenie.asset_class.add(path, exists_overwrite=exists_overwrite)
            summary["asset_classes_imported"].append(d["name"])
            log(f"[asset_class] imported {d['name']} v{d.get('version')}")
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(f"asset_class {d.get('name')}: {exc}")
            raise

    # 2. Recipes, converted to refgenie1 form, written to a temp YAML so that
    #    RecipeManager.add() (which loads from a path/URL) can ingest them.
    for path in discover_recipes(registry_root):
        recipe_dict = load_yaml(path)
        result = convert_recipe(
            recipe_dict,
            asset_class_index,
            recipe_produces_index,
            primary_seek_key_index,
        )
        for note in result.notes:
            summary["notes"].append(f"{recipe_dict['name']}: {note}")
            log(f"[recipe:{recipe_dict['name']}] {note}")

        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False
        ) as tmp:
            yaml.safe_dump(result.recipe, tmp, default_flow_style=False, sort_keys=False)
            tmp_path = Path(tmp.name)
        try:
            refgenie.recipe.add(tmp_path, exists_overwrite=exists_overwrite)
            summary["recipes_imported"].append(recipe_dict["name"])
            log(
                f"[recipe] imported {recipe_dict['name']} v{recipe_dict['version']} "
                f"-> {result.recipe['output_asset_class']}"
            )
        except Exception as exc:  # noqa: BLE001
            summary["errors"].append(f"recipe {recipe_dict['name']}: {exc}")
            raise
        finally:
            tmp_path.unlink(missing_ok=True)

    return summary


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
    args = parser.parse_args(argv)

    refgenie = build_refgenie(args.db_config, genome_folder=args.genome_folder)
    summary = import_registry(refgenie, args.registry_root)

    print("\n=== Import summary ===")
    print(f"Asset classes imported: {len(summary['asset_classes_imported'])}")
    print(f"Recipes imported:       {len(summary['recipes_imported'])}")
    if summary["notes"]:
        print(f"Conversion notes:       {len(summary['notes'])}")
    if summary["errors"]:
        print(f"Errors:                 {len(summary['errors'])}")
        for err in summary["errors"]:
            print(f"  - {err}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
