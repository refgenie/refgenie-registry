"""Tests for the genome-YAML validator's `build:` block checks.

Run: pytest tools/test_validate_genome.py
"""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import genome_to_fhr  # noqa: E402
import validate_genome as vg  # noqa: E402

MINIMAL = {
    "name": "testgenome",
    "description": "A genome record used only by these tests.",
    "organism": {"scientific_name": "Homo sapiens", "taxon_id": 9606},
    "assembly": {"source": "NCBI", "accession": "GCA_000001405.15"},
    "fasta": {"checksum": {"sha256": "compute_on_registration"}},
    "seqcol": {"digest": "EiFob05aCWgVU_B_Ae0cypnQut3cxUP1"},
    "build": {"store": "legacy", "tier": "full", "drop": ["star_index"]},
}


def genome(**build):
    data = copy.deepcopy(MINIMAL)
    data["build"].update(build)
    return data


def test_real_store_slug_passes():
    assert vg.check_build_store(genome(store="jungle")) == []


def test_unknown_store_lists_the_valid_slugs():
    errors = vg.check_build_store(genome(store="nosuchstore"))
    assert len(errors) == 1
    # A contributor has no other way to discover the slugs, so the error must
    # carry them.
    assert "nosuchstore" in errors[0]
    for slug in vg.store_slugs():
        assert slug in errors[0]


def test_known_tier_passes():
    assert vg.check_build_tier(genome(tier="store_only", drop=[])) == []


def test_unknown_tier_is_rejected():
    errors = vg.check_build_tier(genome(tier="deluxe", drop=[]))
    assert len(errors) == 1
    assert "deluxe" in errors[0]
    assert "pep/tiers.yaml" in errors[0]


def test_typoed_asset_in_add_is_rejected():
    """This is the check that catches a typo on the PR instead of at 3am."""
    errors = vg.check_build_tier(genome(add=["suffixerator_indx"], drop=[]))
    assert len(errors) == 1
    assert "suffixerator_indx" in errors[0]
    assert "recipes/" in errors[0]


def test_typoed_asset_in_drop_is_rejected():
    errors = vg.check_build_tier(genome(drop=["star_indx"]))
    assert len(errors) == 1
    assert "star_indx" in errors[0]


def test_real_assets_in_add_and_drop_pass():
    assert vg.check_build_tier(
        genome(add=["suffixerator_index"], drop=["star_index", "tallymer_index"])
    ) == []


def test_build_block_does_not_reach_the_fhr_export():
    """The `build:` block is pipeline state and must never be published.

    genome_yaml_to_fhr() builds its output by explicit whitelist, so a new
    top-level block is ignored by construction -- assert it, so a future exporter
    that starts passing unknown keys through fails here.
    """
    data = genome()
    fhr = genome_to_fhr.genome_yaml_to_fhr(data)
    assert "build" not in fhr
    assert not [k for k in fhr if k.lower().startswith("build")]
    # ...and the same assertion as the validator makes it.
    assert vg.check_build_not_in_fhr(data) == []


def test_fhr_export_is_unchanged_by_adding_a_build_block():
    without = copy.deepcopy(MINIMAL)
    del without["build"]
    assert genome_to_fhr.genome_yaml_to_fhr(without) == genome_to_fhr.genome_yaml_to_fhr(
        MINIMAL
    )


@pytest.mark.parametrize("path", sorted((Path(__file__).resolve().parent.parent / "genomes").rglob("*.yaml")))
def test_every_committed_genome_exports_no_build_key(path):
    data = vg.load_genome(path)
    assert vg.check_build_not_in_fhr(data) == []
