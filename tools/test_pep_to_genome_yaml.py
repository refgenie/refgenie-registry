"""Tests for the PEP -> genome YAML mapping.

``sample_to_genome`` is pure, so the mapping is testable without a PEP, a
network, or the CLI. Only that function is exercised here; the typer commands
are thin I/O around it.

The module declares its dependencies inline for ``uv run`` and imports ``typer``
at module scope, which is not a test dependency. It is stubbed before import so
the mapping can be tested on a bare interpreter.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest
import yaml

TOOLS = Path(__file__).resolve().parent
REPO = TOOLS.parent


def _load_module():
    if "typer" not in sys.modules:
        stub = types.ModuleType("typer")
        stub.Typer = lambda **k: types.SimpleNamespace(
            command=lambda *a, **k: (lambda f: f)
        )
        stub.Option = lambda *a, **k: None
        stub.Argument = lambda *a, **k: None
        stub.BadParameter = type("BadParameter", (Exception,), {})
        stub.Exit = type("Exit", (Exception,), {})
        stub.echo = lambda *a, **k: None
        stub.secho = lambda *a, **k: None
        sys.modules["typer"] = stub
    spec = importlib.util.spec_from_file_location(
        "pep_to_genome_yaml", TOOLS / "pep_to_genome_yaml.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load_module()


def row(**over):
    base = dict(
        sample_name="rAllMis1",
        species_name="Alligator mississippiensis",
        taxon_id="8496",
        description="American alligator",
        fasta_url="https://hgdownload.soe.ucsc.edu/hubs/GCF/030/867/095/x.fa.gz",
        ncbi_id="GCF_030867095.1",
    )
    base.update(over)
    return base


def convert(**over):
    store = over.pop("store", "vgp")
    tier = over.pop("tier", "store_only")
    added = over.pop("added", "2026-01-01")
    return mod.sample_to_genome(
        row(**over), store=store, tier=tier, folder="vertebrates", added=added
    )


class TestBuildBlock:
    def test_build_block_is_emitted(self):
        _, _, doc = convert()
        assert doc["build"] == {"store": "vgp", "tier": "store_only"}

    def test_output_satisfies_the_genome_schema(self):
        from jsonschema import Draft202012Validator

        _, _, doc = convert()
        parsed = yaml.safe_load(mod.dump_yaml(doc))
        schema = yaml.safe_load((REPO / "schema" / "genome.schema.yaml").read_text())
        assert [e.message for e in Draft202012Validator(schema).iter_errors(parsed)] == []

    def test_unknown_store_is_rejected(self):
        with pytest.raises(ValueError, match="not a store"):
            convert(store="not-a-real-store")

    def test_unknown_tier_is_rejected(self):
        with pytest.raises(ValueError, match="not a tier"):
            convert(tier="turbo")

    def test_every_declared_store_is_accepted(self):
        for slug in mod.store_slugs():
            _, _, doc = convert(store=slug)
            assert doc["build"]["store"] == slug


class TestMapping:
    def test_required_fields_are_enforced(self):
        for missing in ("sample_name", "species_name", "description", "fasta_url"):
            with pytest.raises(ValueError):
                convert(**{missing: ""})

    def test_bad_name_is_rejected(self):
        with pytest.raises(ValueError, match="violates"):
            convert(sample_name="has spaces")

    def test_accession_prefix_picks_the_source(self):
        assert convert(ncbi_id="GCF_1.1")[2]["assembly"]["source"] == "RefSeq"
        assert convert(ncbi_id="GCA_1.1")[2]["assembly"]["source"] == "GenBank"

    def test_seqcol_defaults_to_compute(self):
        assert convert()[2]["seqcol"] == {"compute": True}
        assert convert(seqcol_digest="abc")[2]["seqcol"] == {"digest": "abc"}

    def test_added_date_is_pinnable(self):
        """Same input plus the same --added gives byte-identical output."""
        a = mod.dump_yaml(convert(added="2026-05-05")[2])
        b = mod.dump_yaml(convert(added="2026-05-05")[2])
        assert a == b
        assert "2026-05-05" in a

    def test_identifiers_become_fhr_entries(self):
        _, _, doc = convert(BioProject="PRJNA1", id_id="SAMN2")
        assert doc["fhr"]["identifier"] == ["bioproject:PRJNA1", "biosample:SAMN2"]

    def test_non_biosample_id_is_ignored(self):
        _, _, doc = convert(id_id="not-a-biosample")
        assert "identifier" not in doc.get("fhr", {})


class TestInputSchema:
    def test_default_input_schema_exists_and_parses(self):
        """The --schema default must point at a real file, or convert cannot run."""
        import json

        assert mod.DEFAULT_SCHEMA.is_file(), f"{mod.DEFAULT_SCHEMA} is missing"
        schema = json.loads(mod.DEFAULT_SCHEMA.read_text())
        required = schema["properties"]["samples"]["items"]["required"]
        assert {"sample_name", "species_name", "taxon_id", "description", "fasta_url"} <= set(required)
