"""
Tests for the thin registry loader (tools/import_recipes.py).

These build an in-memory SQLite refgenie1 database, load ALL registry asset
classes and refgenie-native recipes, and assert that:

* every asset class loads;
* every recipe loads (after stripping additive non-runtime keys);
* each recipe's output_asset_class and input_assets[].asset_class resolve to
  loaded asset classes;
* THEN that the loaded registry drives refgenie's REAL build system: calling
  ``populate_snakefile_template`` produces a non-empty Snakefile containing a
  ``rule build_<name>`` for several recipes.

Run with::

    python -m pytest tools/test_import_recipes.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlmodel import create_engine
from sqlmodel.pool import StaticPool

# Make the loader module importable whether tests are run from the repo root
# or from within the tools/ directory.
TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import import_recipes as ir  # noqa: E402

import pytest  # noqa: E402

REGISTRY_ROOT = ir.default_registry_root()


@pytest.fixture
def refgenie(tmp_path):
    """In-memory SQLite refgenie1 with an isolated genome folder."""
    from refgenie import Refgenie

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    r = Refgenie(database_engine=engine, suppress_migrations=True)
    r.init(genome_folder=tmp_path, genome_stage_folder=tmp_path)
    return r


@pytest.fixture
def imported(refgenie):
    """Run the full load and return (refgenie, summary)."""
    summary = ir.import_registry(refgenie, REGISTRY_ROOT, verbose=False)
    return refgenie, summary


# ---------------------------------------------------------------------------
# Registry content / loader transformation
# ---------------------------------------------------------------------------


def test_registry_has_content():
    """Sanity: the registry actually contains asset classes and recipes."""
    asset_classes = ir.discover_asset_classes(REGISTRY_ROOT)
    recipes = ir.discover_recipes(REGISTRY_ROOT)
    assert len(asset_classes) >= 27
    assert len(recipes) >= 29


def test_strip_only_non_native_keys():
    """The loader's ONLY transformation is dropping additive non-runtime keys;
    every native/runtime field passes through untouched."""
    recipe = ir.load_yaml(REGISTRY_ROOT / "recipes" / "bwa_index" / "recipe.yaml")
    native, stripped = ir.strip_non_native_keys(recipe)

    # Additive keys (if present) are stripped.
    for key in ir.NON_NATIVE_RECIPE_KEYS:
        assert key not in native
    assert set(stripped) <= set(ir.NON_NATIVE_RECIPE_KEYS)

    # Native runtime fields pass through verbatim (no rename/translation).
    for key in (
        "name",
        "version",
        "output_asset_class",
        "command_templates",
        "input_assets",
        "docker_image",
        "custom_seek_keys",
        "default_asset",
    ):
        assert native[key] == recipe[key]


# ---------------------------------------------------------------------------
# Loading into refgenie1
# ---------------------------------------------------------------------------


def test_all_asset_classes_load(imported):
    refgenie, summary = imported
    discovered = ir.discover_asset_classes(REGISTRY_ROOT)
    assert len(summary["asset_classes_imported"]) == len(discovered)
    assert not summary["errors"]

    registered = {ac.name for ac in refgenie.asset_class.list_all()}
    for path in discovered:
        name = ir.load_yaml(path)["name"]
        assert name in registered, f"asset class '{name}' not registered"


def test_all_recipes_load(imported):
    refgenie, summary = imported
    discovered = ir.discover_recipes(REGISTRY_ROOT)
    assert len(summary["recipes_imported"]) == len(discovered)
    assert not summary["errors"]

    registered = {r.name for r in refgenie.recipe.list_all()}
    for path in discovered:
        name = ir.load_yaml(path)["name"]
        assert name in registered, f"recipe '{name}' not registered"


def test_recipe_output_and_inputs_resolve(imported):
    """Every recipe's output_asset_class and input_assets[].asset_class resolve
    to loaded asset classes."""
    refgenie, _ = imported
    registered_asset_classes = {ac.name for ac in refgenie.asset_class.list_all()}

    recipe_names = [r.name for r in refgenie.recipe.list_all()]
    for recipe_name in recipe_names:
        recipe = refgenie.recipe.get(recipe_name)
        # Output asset class resolves.
        assert recipe.output_asset_class is not None, (
            f"recipe '{recipe.name}' has no output_asset_class"
        )
        assert recipe.output_asset_class.name in registered_asset_classes

        # Each input asset class resolves to a registered asset class. This API
        # eager-loads the resolved AssetClass within a session.
        for asset_class, _default in refgenie.recipe.get_required_input_asset_classes(
            recipe_name
        ):
            assert asset_class is not None, (
                f"recipe '{recipe_name}' has an input that did not resolve"
            )
            assert asset_class.name in registered_asset_classes


def test_counts(imported):
    """The headline numbers: 27 asset classes + 29 recipes.

    Was 28 + 30 until 2026-07-23, when bismark_bt1_index was retired: bismark
    3.x dropped bowtie1 entirely, and the recipe relied on "no --bowtie2 flag
    means bowtie1", so under 3.x it would have built a bowtie2 index and
    published it as a bowtie1 asset.
    """
    _, summary = imported
    assert len(summary["asset_classes_imported"]) == 27
    assert len(summary["recipes_imported"]) == 29


# ---------------------------------------------------------------------------
# The registry drives refgenie's REAL build system
# ---------------------------------------------------------------------------


def test_registry_drives_snakefile_generation(imported, tmp_path):
    """After loading the registry, refgenie's real build entrypoint
    (populate_snakefile_template) produces a non-empty Snakefile with a
    `rule build_<name>` for several recipes. This proves the registry drives
    the actual builder, not a parallel format."""
    from refgenie.snakefile.generate import populate_snakefile_template

    refgenie, _ = imported
    snakefile = tmp_path / "Snakefile"
    populate_snakefile_template(refgenie, snakefile)

    assert snakefile.exists()
    content = snakefile.read_text()
    assert content.strip(), "generated Snakefile is empty"
    assert "refgenie1 build" in content, "Snakefile does not invoke 'refgenie1 build'"

    # The generated workflow contains a build rule for several recipes, named
    # `rule build_<recipe_name>` (see refgenie1 snakefile template).
    for recipe_name in ("fasta", "bwa_index", "bowtie2_index", "hisat2_index", "salmon_index"):
        assert f"rule build_{recipe_name}:" in content, (
            f"Snakefile missing 'rule build_{recipe_name}'"
        )
