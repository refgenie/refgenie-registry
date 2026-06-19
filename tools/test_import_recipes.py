"""
Tests for the converting recipe importer (tools/import_recipes.py).

These build an in-memory SQLite refgenie1 database, run the importer over ALL
registry asset classes and recipes, and assert that:

* every asset class imports;
* every recipe imports;
* each recipe's output_asset_class and input_assets resolve to imported asset
  classes.

Run with::

    pytest tools/test_import_recipes.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlmodel import create_engine
from sqlmodel.pool import StaticPool

# Make the importer module importable whether tests are run from the repo root
# or from within the tools/ directory.
TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import import_recipes as ir  # noqa: E402

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
    """Run the full import and return (refgenie, summary)."""
    summary = ir.import_registry(refgenie, REGISTRY_ROOT, verbose=False)
    return refgenie, summary


def test_registry_has_content():
    """Sanity: the registry actually contains asset classes and recipes."""
    asset_classes = ir.discover_asset_classes(REGISTRY_ROOT)
    recipes = ir.discover_recipes(REGISTRY_ROOT)
    assert len(asset_classes) >= 28
    assert len(recipes) >= 30


def test_all_asset_classes_import(imported):
    refgenie, summary = imported
    discovered = ir.discover_asset_classes(REGISTRY_ROOT)
    assert len(summary["asset_classes_imported"]) == len(discovered)
    assert not summary["errors"]

    registered = {ac.name for ac in refgenie.asset_class.list_all()}
    for path in discovered:
        name = ir.load_yaml(path)["name"]
        assert name in registered, f"asset class '{name}' not registered"


def test_all_recipes_import(imported):
    refgenie, summary = imported
    discovered = ir.discover_recipes(REGISTRY_ROOT)
    assert len(summary["recipes_imported"]) == len(discovered)
    assert not summary["errors"]

    registered = {r.name for r in refgenie.recipe.list_all()}
    for path in discovered:
        name = ir.load_yaml(path)["name"]
        assert name in registered, f"recipe '{name}' not registered"


def test_recipe_output_and_inputs_resolve(imported):
    """Every recipe's output_asset_class and input_assets resolve to imported
    asset classes."""
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
    """The headline numbers from the plan: 28 asset classes + 30 recipes."""
    _, summary = imported
    assert len(summary["asset_classes_imported"]) == 28
    assert len(summary["recipes_imported"]) == 30


def test_colocate_on_fasta_parent_recipes():
    """Recipes whose build command places the parent fasta under the output dir
    get a colocate declaration; those that read it in place do not."""
    aci = ir.build_asset_class_index(REGISTRY_ROOT)
    rpi = ir.build_recipe_produces_index(REGISTRY_ROOT)
    psk = ir.build_primary_seek_key_index(REGISTRY_ROOT)

    def colocated(recipe_name: str) -> bool:
        d = ir.load_yaml(REGISTRY_ROOT / "recipes" / recipe_name / "recipe.yaml")
        result = ir.convert_recipe(d, aci, rpi, psk)
        fasta = result.recipe["input_assets"].get("fasta", {})
        return "colocate" in fasta

    # bwa_index / bismark operate on a fasta colocated under the output dir.
    assert colocated("bwa_index")
    assert colocated("bismark_bt1_index")
    assert colocated("bismark_bt2_index")
    # fasta_index reads {fasta} in place and only writes the index out; no colocate.
    assert not colocated("fasta_index")
    # bowtie2_index reads the fasta directly from the genome folder; no colocate.
    assert not colocated("bowtie2_index")


def test_recipe_name_inputs_resolve_to_produced_asset_class():
    """When requires.assets references a recipe name (not an asset class), it is
    resolved to that recipe's produced asset class, and a note is logged."""
    aci = ir.build_asset_class_index(REGISTRY_ROOT)
    rpi = ir.build_recipe_produces_index(REGISTRY_ROOT)
    psk = ir.build_primary_seek_key_index(REGISTRY_ROOT)

    d = ir.load_yaml(REGISTRY_ROOT / "recipes" / "feat_annotation" / "recipe.yaml")
    result = ir.convert_recipe(d, aci, rpi, psk)
    # ensembl_gtf is a recipe name producing the 'gtf' asset class.
    assert result.recipe["input_assets"]["ensembl_gtf"]["asset_class"] == "gtf"
    assert any("ensembl_gtf" in note for note in result.notes)
