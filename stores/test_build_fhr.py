"""Tests for build_fhr.py: CSV-derived FHR fields, species-list lookups and species.json.

No refget/gtars needed: a stub `refget.store` module stands in for FhrMetadata,
and a fake store for RefgetStore.

Run: pytest stores/test_build_fhr.py
"""

import json
import sys
import types
from pathlib import Path

import pytest

STORES = Path(__file__).resolve().parent
sys.path.insert(0, str(STORES))
sys.path.insert(0, str(STORES.parent / "tools"))

import build_fhr  # noqa: E402
import species_list  # noqa: E402
from genome_to_fhr import FHR_SCHEMA_URL, FHR_SCHEMA_VERSION  # noqa: E402

SPECIES = species_list.load_species_list()


@pytest.fixture(autouse=True)
def stub_refget(monkeypatch):
    """`from refget.store import FhrMetadata` gets a stub returning its kwargs."""
    refget = types.ModuleType("refget")
    store = types.ModuleType("refget.store")
    store.FhrMetadata = lambda **fields: fields
    refget.store = store
    monkeypatch.setitem(sys.modules, "refget", refget)
    monkeypatch.setitem(sys.modules, "refget.store", store)


class FakeStore:
    def __init__(self, digests):
        self.digests = digests  # {(namespace, value): digest}
        self.fhr_calls = []
        self.writes = 0

    def get_collection_by_alias(self, namespace, value):
        digest = self.digests.get((namespace, value))
        return types.SimpleNamespace(digest=digest) if digest else None

    def set_fhr_metadata(self, digest, fields):
        self.fhr_calls.append((digest, fields))

    def write(self):
        self.writes += 1


def row(name, organism, **extra):
    return {"fasta": f"{name}.fa", "name": name, "organism": organism, **extra}


HUMAN = row("hg38", "Homo sapiens", source="UCSC", genome_assembly="GRCh38",
            accession="GCA_000001405.15")


# --------------------------------------------------------------------------
# fhr_fields_for_row
# --------------------------------------------------------------------------

def test_human_row_gives_spec_fields():
    assert build_fhr.fhr_fields_for_row(HUMAN, SPECIES) == {
        "schema": FHR_SCHEMA_URL,
        "schemaVersion": FHR_SCHEMA_VERSION,
        "genome": "hg38",
        "taxon": {"name": "Homo sapiens", "uri": "https://identifiers.org/taxonomy:9606"},
        "documentation": "Homo sapiens genome hg38 (GRCh38), distributed by UCSC.",
        "assemblySource": "UCSC",
        "accessionID": {"name": "GCA_000001405.15"},
    }


def test_old_dog_name_gives_the_current_scientific_name():
    fields = build_fhr.fhr_fields_for_row(row("canFam3", "Canis familiaris"), SPECIES)
    assert fields["genome"] == "canFam3"
    assert fields["taxon"] == {
        "name": "Canis lupus familiaris",
        "uri": "https://identifiers.org/taxonomy:9615",
    }
    assert "genomeSynonym" not in fields


def test_unnamed_row_has_no_genome():
    assert "genome" not in build_fhr.fhr_fields_for_row(row("  ", "Homo sapiens"), SPECIES)


# --------------------------------------------------------------------------
# fill_from_accession
# --------------------------------------------------------------------------

def test_fill_from_accession_sets_version_and_level():
    fields = {"accessionID": {"name": "GCF_000001405.40"}}
    assert build_fhr.fill_from_accession(fields) is None
    assert fields["version"] == "40"
    assert fields["assemblyLevel"] == "chromosome"


def test_fill_from_accession_leaves_a_record_without_an_accession_alone():
    fields = {"genome": "x"}
    assert build_fhr.fill_from_accession(fields) is None
    assert fields == {"genome": "x"}


def test_fill_from_accession_keeps_a_curated_version_and_reports_an_unknown_level():
    fields = {"accessionID": {"name": "GCA_999999999.2"}, "version": "curated"}
    assert build_fhr.fill_from_accession(fields) == "GCA_999999999.2"
    assert fields["version"] == "curated"
    assert "assemblyLevel" not in fields


# --------------------------------------------------------------------------
# validate_organisms
# --------------------------------------------------------------------------

def test_unknown_organism_fails_naming_the_list(capsys):
    with pytest.raises(SystemExit):
        build_fhr.validate_organisms([HUMAN, row("x", "Notarealus speciesus")], SPECIES)
    err = capsys.readouterr().err
    assert "tools/taxon_ids.json" in err
    assert "Notarealus speciesus" in err


def test_empty_organism_gets_its_own_message(capsys):
    with pytest.raises(SystemExit):
        build_fhr.validate_organisms([HUMAN, row("x", "  ")], SPECIES)
    err = capsys.readouterr().err
    assert "empty organism column" in err
    assert "not on the species list" not in err


def test_rows_with_a_curated_record_are_exempt():
    records = {"x": {"genome": "x", "taxon": {"name": "Notarealus speciesus"}}}
    build_fhr.validate_organisms([HUMAN, row("x", "Notarealus speciesus")], SPECIES, records)


# --------------------------------------------------------------------------
# build_fhr and species.json
# --------------------------------------------------------------------------

MOUSE_RECORD = {
    "genome": "mm39",
    "taxon": {"name": "Mus musculus", "uri": "https://identifiers.org/taxonomy:10090"},
}
ROWS = [HUMAN, row("canFam3", "Canis familiaris"), row("mm39", "")]
DIGESTS = {("name", "hg38"): "D_HUMAN", ("name", "canFam3"): "D_DOG", ("name", "mm39"): "D_MOUSE"}


def test_build_fhr_writes_species_json_with_exactly_its_taxa(tmp_path):
    store = FakeStore(DIGESTS)
    unresolved = build_fhr.build_fhr(store, ROWS, species=SPECIES, store_dir=tmp_path,
                                     records={"mm39": MOUSE_RECORD})
    assert unresolved == 0
    assert sorted(d for d, _ in store.fhr_calls) == ["D_DOG", "D_HUMAN", "D_MOUSE"]
    assert store.writes == 1
    doc = json.loads((tmp_path / "species.json").read_text())
    assert species_list.check_species_document(doc) == []
    assert [s["taxon_id"] for s in doc["species"]] == [9606, 9615, 10090]
    dog = doc["species"][1]
    assert dog["scientific_name"] == "Canis lupus familiaris"
    assert "Canis familiaris" in dog["synonyms"]


def test_build_fhr_dry_run_writes_no_species_json(tmp_path, capsys):
    store = FakeStore(DIGESTS)
    build_fhr.build_fhr(store, ROWS, species=SPECIES, store_dir=tmp_path, dry_run=True,
                        records={"mm39": MOUSE_RECORD})
    assert not (tmp_path / "species.json").exists()
    assert store.fhr_calls == []
    assert "species.json would list 3 species" in capsys.readouterr().out


def test_curated_record_with_an_unlisted_taxon_fails_before_writing(tmp_path, capsys):
    record = dict(MOUSE_RECORD, taxon={"name": "Notarealus speciesus",
                                       "uri": "https://identifiers.org/taxonomy:999999999"})
    store = FakeStore(DIGESTS)
    with pytest.raises(SystemExit):
        build_fhr.build_fhr(store, ROWS, species=SPECIES, store_dir=tmp_path,
                            records={"mm39": record})
    assert store.fhr_calls == []
    assert not (tmp_path / "species.json").exists()
    err = capsys.readouterr().err
    assert "999999999" in err and "mm39" in err and "tools/taxon_ids.json" in err


def test_nothing_to_write_writes_no_species_json(tmp_path):
    store = FakeStore({})
    build_fhr.build_fhr(store, [HUMAN], species=SPECIES, store_dir=tmp_path)
    assert not (tmp_path / "species.json").exists()


# --------------------------------------------------------------------------
# load_registry_records
# --------------------------------------------------------------------------


def _genome(tmp_path, name, digest=None):
    genomes, meta = tmp_path / "genomes", tmp_path / "meta"
    genomes.mkdir(exist_ok=True)
    meta.mkdir(exist_ok=True)
    text = f"name: {name}\nassembly:\n  accession: GCA_000001405.15\nbuild:\n  store: jungle\n"
    if digest:
        text += f"seqcol:\n  digest: {digest}\n"
    (genomes / f"{name}.yaml").write_text(text)
    (meta / f"{name}.fhr.json").write_text(json.dumps({"genome": name}))
    return genomes, meta


def test_accession_matches_only_a_genome_without_a_pinned_digest(tmp_path, monkeypatch):
    monkeypatch.setattr(build_fhr, "_staged_fasta_paths", lambda: {})
    genomes, meta = _genome(tmp_path, "hg38_full_analysis_plus_decoy", digest="D_FULL")
    records = build_fhr.load_registry_records("jungle", genomes, meta)
    other_fasta = {"name": "", "accession": "GCA_000001405.15", "fasta": "noalt.fa"}
    assert build_fhr.registry_record_for_row(other_fasta, records) is None

    genomes, meta = _genome(tmp_path, "unpinned")
    records = build_fhr.load_registry_records("jungle", genomes, meta)
    assert build_fhr.registry_record_for_row(other_fasta, records) == {"genome": "unpinned"}
