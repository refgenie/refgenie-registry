"""Tests for pep/samples.csv generation from the genome list.

Run: pytest build/test_generate_samples.py
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "build"))

import generate_samples as gs  # noqa: E402

TIERS = gs.load_tiers()
RECIPES = gs.load_recipes()


def build(**kwargs):
    block = {"store": "legacy", "tier": "standard"}
    block.update(kwargs)
    return block


# --------------------------------------------------------------------------
# resolve_genome
# --------------------------------------------------------------------------

def test_store_only_resolves_to_no_assets():
    assert gs.resolve_genome("g", build(tier="store_only"), TIERS) == []


def test_tier_order_then_add_then_drop():
    assets = gs.resolve_genome(
        "g",
        build(tier="standard", add=["suffixerator_index"], drop=["star_index"]),
        TIERS,
    )
    assert assets == [
        "fasta",
        "fasta_index",
        "bwa_index",
        "bowtie2_index",
        "hisat2_index",
        "suffixerator_index",
    ]


def test_unknown_tier_is_fatal():
    with pytest.raises(gs.GenerationError, match="unknown build.tier"):
        gs.resolve_genome("g", build(tier="deluxe"), TIERS)


def test_missing_tier_is_fatal():
    with pytest.raises(gs.GenerationError, match="build.tier is not set"):
        gs.resolve_genome("g", {"store": "legacy"}, TIERS)


# --------------------------------------------------------------------------
# validate_fasta_source -- the check that was missing
# --------------------------------------------------------------------------

def test_missing_fasta_source_names_the_genome_and_the_key():
    """A queued genome with no staged FASTA must fail HERE.

    Before this check, such a genome generated rows pointing at a nonexistent
    `<genome>_fa` derive source with no error raised, and blew up much later
    inside peppy with a message that never named the genome.
    """
    with pytest.raises(gs.GenerationError) as exc:
        gs.validate_fasta_source("rAllMis1", {"hg38_fa": "..."})
    message = str(exc.value)
    assert "rAllMis1" in message
    assert "rAllMis1_fa" in message
    assert "store_only" in message  # tells the contributor the way out


def test_present_fasta_source_passes():
    assert gs.validate_fasta_source("hg38", {"hg38_fa": "..."}) is None


def test_build_rows_raises_for_a_queued_genome_with_no_staged_fasta():
    genomes = [("rAllMis1", {"build": build(tier="sequence_only")})]
    with pytest.raises(gs.GenerationError, match="rAllMis1_fa"):
        gs.build_rows(genomes, TIERS, RECIPES, {})


def test_build_rows_skips_store_only_without_needing_a_fasta_source():
    """store_only genomes have no staged FASTA and must not be asked for one."""
    genomes = [("rAllMis1", {"build": build(tier="store_only")})]
    assert gs.build_rows(genomes, TIERS, RECIPES, {}) == []


# --------------------------------------------------------------------------
# whole-corpus properties
# --------------------------------------------------------------------------

def test_generation_is_deterministic_and_name_sorted():
    content = gs.generate()
    rows = content.splitlines()[1:]
    names = [r.split(",")[1] for r in rows]
    assert names == sorted(names)
    assert gs.generate() == content


def test_committed_samples_csv_is_up_to_date():
    assert (REPO / "pep" / "samples.csv").read_text() == gs.generate()


def test_no_store_only_genome_appears_in_the_queue():
    import glob
    import yaml

    store_only = set()
    for path in glob.glob(str(REPO / "genomes" / "**" / "*.yaml"), recursive=True):
        data = yaml.safe_load(open(path))
        if (data.get("build") or {}).get("tier") == "store_only":
            store_only.add(data["name"])
    queued = {r.split(",")[1] for r in gs.generate().splitlines()[1:]}
    assert not (store_only & queued)
