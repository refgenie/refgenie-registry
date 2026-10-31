"""Tests for the species list module (tools/species_list.py) and its data.

Run: pytest tools/test_species_list.py
"""

import copy
import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import species_list as sl  # noqa: E402

FIXTURE = {
    "Rattus norvegicus": {
        "taxon_id": 10116,
        "common_name": "Norway rat",
        "synonyms": ["brown rat", "rat"],
    },
    "Canis lupus familiaris": {
        "taxon_id": 9615,
        "common_name": "dog",
        "synonyms": ["Canis familiaris"],
    },
    "Homo sapiens": {"taxon_id": 9606, "common_name": "human"},
    "Aus bus": {"taxon_id": 111, "synonyms": ["shared name"]},
    "Aus cus": {"taxon_id": 222, "common_name": "cus", "synonyms": ["shared name"]},
}

# The shared species.json contract example (refgenie reads the same shape).
CONTRACT_RAT = (
    '{"taxon_id": 10116, "scientific_name": "Rattus norvegicus", '
    '"common_name": "Norway rat", "synonyms": ["brown rat", "rat"]}'
)


def species():
    return copy.deepcopy(FIXTURE)


# --------------------------------------------------------------------------
# resolve_organism
# --------------------------------------------------------------------------

def test_resolve_exact_key():
    assert sl.resolve_organism("Rattus norvegicus", species())[0] == "Rattus norvegicus"


def test_resolve_key_ignoring_case():
    sci, entry = sl.resolve_organism("rattus NORVEGICUS", species())
    assert sci == "Rattus norvegicus"
    assert entry["taxon_id"] == 10116


def test_resolve_old_name_through_synonyms():
    sci, entry = sl.resolve_organism("canis familiaris", species())
    assert sci == "Canis lupus familiaris"
    assert entry["taxon_id"] == 9615


def test_resolve_shared_synonym_is_none():
    assert sl.resolve_organism("shared name", species()) is None


def test_resolve_unknown_is_none():
    assert sl.resolve_organism("Danio rerio", species()) is None


@pytest.mark.parametrize("name", ["", "   ", None])
def test_resolve_empty_is_none(name):
    assert sl.resolve_organism(name, species()) is None


# --------------------------------------------------------------------------
# clean_synonyms
# --------------------------------------------------------------------------

def test_clean_drops_names_equal_to_common_or_scientific_name():
    assert sl.clean_synonyms("Rattus norvegicus", "Norway rat",
                             ["NORWAY RAT", "rattus norvegicus", "brown rat"]) == ["brown rat"]


def test_clean_drops_repeats_first_wins():
    assert sl.clean_synonyms("X y", None, ["Brown rat", "brown rat", " brown rat "]) == [
        "Brown rat"]


def test_clean_drops_plain_plurals():
    assert sl.clean_synonyms("Rattus norvegicus", "Norway rat",
                             ["brown rat", "rat", "rats"]) == ["brown rat", "rat"]
    assert sl.clean_synonyms("Dama dama", "Texas fallow deer",
                             ["fallow deer", "Texas fallow deers"]) == ["fallow deer"]


def test_clean_keeps_order_and_strips():
    assert sl.clean_synonyms("X y", "z", ["  b ", "a", "", "c"]) == ["b", "a", "c"]


# --------------------------------------------------------------------------
# species_document
# --------------------------------------------------------------------------

def test_species_document_lists_only_the_given_taxa_sorted():
    doc = sl.species_document([10116, 9606], species())
    assert [item["taxon_id"] for item in doc["species"]] == [9606, 10116]


def test_species_document_leaves_out_empty_fields():
    doc = sl.species_document([9606, 111], species())
    aus, human = doc["species"]  # sorted by taxon id: 111 before 9606
    assert human == {"taxon_id": 9606, "scientific_name": "Homo sapiens", "common_name": "human"}
    assert aus == {"taxon_id": 111, "scientific_name": "Aus bus", "synonyms": ["shared name"]}


def test_species_document_unknown_taxon_raises():
    with pytest.raises(ValueError, match="999"):
        sl.species_document([10116, 999], species())


def test_species_document_matches_the_contract_example():
    doc = sl.species_document([10116], species())
    assert json.dumps(doc["species"][0]) == CONTRACT_RAT
    assert sl.check_species_document(doc) == []


def test_species_document_applies_the_name_rules():
    data = species()
    data["Rattus norvegicus"]["synonyms"] = ["brown rat", "rat", "rats", "Norway Rat"]
    item = sl.species_document([10116], data)["species"][0]
    assert item["synonyms"] == ["brown rat", "rat"]


# --------------------------------------------------------------------------
# check_species_document
# --------------------------------------------------------------------------

def _good():
    return {"species": [
        {"taxon_id": 9606, "scientific_name": "Homo sapiens", "common_name": "human"},
        json.loads(CONTRACT_RAT),
    ]}


def _set(path, value):
    def mutate(doc):
        target = doc
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        return doc
    return mutate


def _drop_species(doc):
    del doc["species"]
    return doc


def _duplicate_taxon(doc):
    doc["species"][1]["taxon_id"] = 9606
    return doc


VIOLATIONS = {
    "missing species": _drop_species,
    "species not a list": _set(["species"], {"taxon_id": 9606}),
    "extra top-level key": _set(["version"], 1),
    "extra item key": _set(["species", 0, "rank"], "species"),
    "bool taxon_id": _set(["species", 0, "taxon_id"], True),
    "string taxon_id": _set(["species", 0, "taxon_id"], "9606"),
    "duplicate taxon_id": _duplicate_taxon,
    "empty scientific_name": _set(["species", 0, "scientific_name"], ""),
    "synonym equal to common name": _set(["species", 1, "synonyms"], ["NORWAY RAT"]),
    "repeated synonym": _set(["species", 1, "synonyms"], ["rat", "Rat"]),
    "empty synonyms list": _set(["species", 1, "synonyms"], []),
}


def test_good_document_passes():
    assert sl.check_species_document(_good()) == []


@pytest.mark.parametrize("violation", sorted(VIOLATIONS))
def test_check_species_document_reports(violation):
    doc = VIOLATIONS[violation](_good())
    assert sl.check_species_document(doc), violation


# --------------------------------------------------------------------------
# write_species_file, taxa_in_store_dir
# --------------------------------------------------------------------------

def test_write_species_file_writes_once(tmp_path):
    assert sl.write_species_file(tmp_path, [10116, 9615], species()) is True
    path = tmp_path / "species.json"
    first = path.read_bytes()
    doc = json.loads(first)
    assert sl.check_species_document(doc) == []
    assert [item["taxon_id"] for item in doc["species"]] == [9615, 10116]

    assert sl.write_species_file(tmp_path, [10116, 9615], species()) is False
    assert path.read_bytes() == first


def test_write_species_file_unknown_taxon_writes_nothing(tmp_path):
    with pytest.raises(ValueError):
        sl.write_species_file(tmp_path, [10116, 999], species())
    assert not (tmp_path / "species.json").exists()


def _sidecar(fhr_dir, name, taxon_id):
    record = {"genome": "x"}
    if taxon_id is not None:
        record["taxon"] = {"name": "x", "uri": f"https://identifiers.org/taxonomy:{taxon_id}"}
    (fhr_dir / f"{name}.fhr.json").write_text(json.dumps(record))


def test_taxa_in_store_dir(tmp_path):
    fhr = tmp_path / "fhr"
    fhr.mkdir()
    _sidecar(fhr, "a", 9606)
    _sidecar(fhr, "b", 10116)
    _sidecar(fhr, "c", None)
    assert sl.taxa_in_store_dir(tmp_path) == {9606, 10116}


def test_taxon_id_from_fhr():
    assert sl.taxon_id_from_fhr(
        {"taxon": {"uri": "https://identifiers.org/taxonomy:9606"}}) == 9606
    assert sl.taxon_id_from_fhr({"taxon": {"uri": "taxonomy:9606"}}) is None
    assert sl.taxon_id_from_fhr({}) is None


def test_cli_check_file(tmp_path, capsys):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(_good()))
    assert sl.main(["check-file", str(good)]) == 0
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(VIOLATIONS["string taxon_id"](_good())))
    assert sl.main(["check-file", str(bad)]) == 1


# --------------------------------------------------------------------------
# check_species_list
# --------------------------------------------------------------------------

def _problems(data, tmp_path):
    """List problems with coverage checked against an empty stores dir."""
    return "\n".join(sl.check_species_list(data, stores_dir=tmp_path))


def test_fixture_list_passes(tmp_path):
    assert sl.check_species_list(species(), stores_dir=tmp_path) == []


def test_check_reports_duplicate_taxon_id(tmp_path):
    data = species()
    data["Aus cus"]["taxon_id"] = 111
    assert "taxon_id 111 is listed more than once" in _problems(data, tmp_path)


def test_check_reports_keys_equal_ignoring_case(tmp_path):
    data = species()
    data["homo sapiens"] = {"taxon_id": 1}
    assert "same name ignoring case" in _problems(data, tmp_path)


def test_check_reports_synonym_equal_to_key(tmp_path):
    data = species()
    data["Homo sapiens"]["synonyms"] = ["HOMO SAPIENS"]
    assert "equals the scientific name" in _problems(data, tmp_path)


def test_check_reports_plural_synonym(tmp_path):
    data = species()
    data["Rattus norvegicus"]["synonyms"] = ["rat", "rats"]
    problems = _problems(data, tmp_path)
    assert "'rats'" in problems and "plain plural" in problems


@pytest.mark.parametrize("field,value,expected", [
    ("taxon_id", "9606", "taxon_id must be an integer"),
    ("taxon_id", True, "taxon_id must be an integer"),
    ("taxon_id", 0, "taxon_id must be an integer"),
    ("common_name", "", "common_name must be a non-empty string"),
    ("synonyms", "rat", "synonyms must be a non-empty list"),
    ("synonyms", [], "synonyms must be a non-empty list"),
    ("synonyms", ["rat", 3], "every synonym must be a string"),
])
def test_check_reports_bad_types(tmp_path, field, value, expected):
    data = species()
    data["Homo sapiens"][field] = value
    assert expected in _problems(data, tmp_path)


def test_check_reports_unknown_entry_key(tmp_path):
    data = species()
    data["Homo sapiens"]["rank"] = "species"
    assert "unknown key(s) ['rank']" in _problems(data, tmp_path)


def test_check_reports_unresolved_and_ambiguous_store_organisms(tmp_path):
    store = tmp_path / "mystore"
    store.mkdir()
    (store / "sources.csv").write_text(
        "fasta,name,organism\na.fa,a,Canis familiaris\nb.fa,b,Danio rerio\n"
        "c.fa,c,shared name\nd.fa,d,\n")
    problems = _problems(species(), tmp_path)
    assert "Canis familiaris" not in problems
    assert "stores/mystore/sources.csv: organism 'Danio rerio' is not on" in problems
    assert "'shared name' is ambiguous" in problems
    assert "'Aus bus'" in problems and "'Aus cus'" in problems


# --------------------------------------------------------------------------
# the real list
# --------------------------------------------------------------------------

def test_real_list_passes_its_check():
    assert sl.check_species_list(sl.load_species_list()) == []


def test_every_committed_genome_taxon_is_listed():
    listed = set(sl.by_taxon_id(sl.load_species_list()))
    missing = []
    for path in sorted(sl.GENOMES_DIR.rglob("*.yaml")):
        organism = (yaml.safe_load(path.read_text()) or {}).get("organism") or {}
        if organism.get("taxon_id") not in listed:
            missing.append(str(path))
    assert missing == []


def test_real_list_resolves_old_dog_name():
    sci, entry = sl.resolve_organism("Canis familiaris", sl.load_species_list())
    assert entry["taxon_id"] == 9615
    assert sci == "Canis lupus familiaris"
