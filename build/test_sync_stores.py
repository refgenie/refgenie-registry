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
