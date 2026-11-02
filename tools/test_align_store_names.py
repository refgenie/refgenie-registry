"""Tests for linking genome YAMLs to store rows by one shared name.

Covers the matcher (stores/genome_rows.py), the renamer
(tools/align_store_names.py) and the name write-back in
tools/sources_to_genomes.py.

Run: pytest tools/test_align_store_names.py
"""

import csv
import sys
from pathlib import Path

import yaml

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS.parent / "stores"))

import align_store_names as asn  # noqa: E402
import sources_to_genomes as stg  # noqa: E402
from genome_rows import Genome, match_store  # noqa: E402


def g(name, aliases=None, digest=None, accession=None):
    data = {"name": name, "build": {"store": "s"}}
    if aliases:
        data["aliases"] = aliases
    if digest:
        data["seqcol"] = {"digest": digest}
    if accession:
        data["assembly"] = {"accession": accession}
    return Genome(name, data, Path(f"{name}.yaml"))


def row(name, fasta=None, accession=""):
    return {"name": name, "fasta": fasta or f"{name}.fa.gz", "accession": accession}


def test_exact_name_wins():
    found = match_store([g("hg19")], [row("other"), row("hg19")])
    assert found.matches == {"hg19": (1, "name")}


def test_digest_picks_row_and_breaks_ties_by_alias_order():
    rows = [row("a-sm"), row("a-plain"), row("b")]
    digests = {0: "D", 1: "D", 2: "E"}
    found = match_store([g("a", aliases=["a-plain", "a-sm"], digest="D")], rows, digests)
    assert found.matches == {"a": (1, "digest")}


def test_digest_with_several_rows_and_no_alias_is_ambiguous():
    found = match_store([g("a", digest="D")], [row("x"), row("y")], {0: "D", 1: "D"})
    assert not found.matches and found.problems


def test_alias_match_contradicted_by_digest_is_an_error():
    found = match_store([g("a", aliases=["x"], digest="D")], [row("x")], {0: "E"})
    assert not found.matches
    assert found.errors


def test_digest_not_in_store_is_an_error():
    found = match_store([g("a", digest="D")], [row("x")], {0: "E"})
    assert found.errors and not found.matches


def test_staged_fasta_links_genome_to_row():
    found = match_store([g("athaliana")], [row("at_tair10", fasta="at/tair10.fa.gz")],
                        staged={"athaliana": "at/tair10.fa.gz"})
    assert found.matches == {"athaliana": (0, "staged")}


def test_accession_only_without_digest_and_only_one_to_one():
    rows = [row("r1", accession="GCA_1.1")]
    assert match_store([g("a", accession="GCA_1.1")], rows).matches == {"a": (0, "accession")}
    # two genomes share the accession: neither may claim the row
    two = match_store([g("a", accession="GCA_1.1"), g("b", accession="GCA_1.1")], rows)
    assert not two.matches
    # a genome with a digest never falls back to accession
    assert not match_store([g("a", accession="GCA_1.1", digest="D")], rows).matches


def test_row_claimed_twice_is_reported_not_matched():
    found = match_store([g("a", aliases=["x"]), g("b", aliases=["x"])], [row("x")])
    assert not found.matches
    assert {n for n, _ in found.problems} == {"a", "b"}


def test_add_alias_appends_to_existing_list():
    text = "name: a\naliases:\n  - old1\n\ndescription: |\n  x\n"
    out = asn.add_alias(text, "old2")
    assert out == "name: a\naliases:\n  - old1\n  - old2\n\ndescription: |\n  x\n"


def test_add_alias_creates_list_and_quotes_when_needed():
    out = asn.add_alias("name: a\ndescription: x\n", "mEubGla1.1.hap2.+ XY")
    assert yaml.safe_load(out)["aliases"] == ["mEubGla1.1.hap2.+ XY"]
    out = asn.add_alias("name: a\ndescription: x\n", "odd: name")
    assert yaml.safe_load(out)["aliases"] == ["odd: name"]


def test_rename_rows_changes_only_name(tmp_path):
    (tmp_path / "s").mkdir()
    path = tmp_path / "s" / "sources.csv"
    path.write_text("fasta,name,organism\na.fa,old a,Homo sapiens\nb.fa,b,Mus musculus\n")
    asn.rename_rows("s", {0: "old_a"}, stores_dir=tmp_path)
    assert path.read_text() == (
        "fasta,name,organism\na.fa,old_a,Homo sapiens\nb.fa,b,Mus musculus\n"
    )


def test_sources_to_genomes_writes_slug_back(tmp_path, monkeypatch):
    stores = tmp_path / "stores"
    (stores / "s").mkdir(parents=True)
    csv_path = stores / "s" / "sources.csv"
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["fasta", "name", "organism", "accession"])
        w.writerow(["x.fa.gz", "bAlcTor1 primary", "Homo sapiens", "GCA_000000001.1"])
    monkeypatch.setattr(stg, "STORES_DIR", stores)
    monkeypatch.setattr(stg, "existing_records", lambda: ({}, {}))
    out = tmp_path / "genomes"
    assert stg.generate("s", out, dry_run=False, offline=True) == 0

    data = yaml.safe_load((out / "bAlcTor1_primary.yaml").read_text())
    assert data["name"] == "bAlcTor1_primary"
    assert data["aliases"] == ["bAlcTor1 primary"]
    assert list(csv.DictReader(open(csv_path)))[0]["name"] == "bAlcTor1_primary"


def test_row_aliases_skip_shared_values():
    from genome_rows import row_aliases

    rows = [
        {"name": "a", "accession": "GCA_1.1", "genome_assembly": "hg38"},
        {"name": "b", "accession": "GCA_1.1", "genome_assembly": "hg19"},
    ]
    assert row_aliases(rows) == [
        [("name", "a"), ("genome_assembly", "hg38")],
        [("name", "b"), ("genome_assembly", "hg19")],
    ]
