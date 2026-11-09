"""A rename must trigger an alias refresh, never a full store load.

Run: pytest build/test_sync_stores.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sync_stores as ss  # noqa: E402

CSV = "fasta,name\na.fa,old a\nb.fa,b\n"


def _store(tmp_path, monkeypatch, text):
    (tmp_path / "s").mkdir(exist_ok=True)
    (tmp_path / "s" / "sources.csv").write_text(text)
    monkeypatch.setattr(ss, "STORES_DIR", tmp_path)
    monkeypatch.setattr(ss, "load_genomes", lambda: [])


def test_rename_changes_alias_stamp_only(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch, CSV)
    full, alias = ss.sources_digest("s"), ss.aliases_digest("s")
    _store(tmp_path, monkeypatch, CSV.replace("old a", "old_a"))
    assert ss.sources_digest("s") == full
    assert ss.aliases_digest("s") != alias


def test_fasta_change_changes_full_stamp(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch, CSV)
    full = ss.sources_digest("s")
    _store(tmp_path, monkeypatch, CSV.replace("b.fa", "c.fa"))
    assert ss.sources_digest("s") != full


def test_yaml_alias_edit_changes_alias_stamp(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch, CSV)
    before = ss.aliases_digest("s")
    monkeypatch.setattr(ss, "load_genomes",
                        lambda: [("b", {"build": {"store": "s"}, "aliases": ["bee"]})])
    assert ss.aliases_digest("s") != before


def _report_with(monkeypatch, published, genomes):
    info = {"genomes": [n for n, _ in genomes], "rows": len(genomes),
            "uncovered": [], "unaligned": [], "errors": []}
    monkeypatch.setattr(ss, "coverage", lambda: ({"s": info}, []))
    monkeypatch.setattr(ss, "nightly_enabled", lambda slug: True)
    monkeypatch.setattr(ss, "published_stores", lambda: published)
    monkeypatch.setattr(ss, "load_genomes", lambda: [
        (n, {"build": {"store": "s", "tier": t}}) for n, t in genomes])


def test_store_only_genome_of_unpublished_store_is_reported(monkeypatch, capsys):
    _report_with(monkeypatch, set(), [("a", "store_only"), ("b", "full")])
    ss.report(check=False)
    out = capsys.readouterr().out
    assert "NOT PUBLISHED a:" in out
    assert "NOT PUBLISHED b:" not in out  # reaches the catalog via genome_init


def test_published_store_is_not_reported(monkeypatch, capsys):
    _report_with(monkeypatch, {"s"}, [("a", "store_only")])
    ss.report(check=False)
    assert "NOT PUBLISHED" not in capsys.readouterr().out


def test_catalog_store_is_not_a_source_store():
    assert "refgenie-main" not in ss.store_slugs()
    assert "refgenie-main" not in ss.published_stores()
    assert {"jungle", "vgp", "igenomes"} <= ss.published_stores()
    assert "plantref" not in ss.published_stores()  # unpublished: too big for the server
