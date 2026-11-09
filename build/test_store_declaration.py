"""The published store list comes from stores/*/project_config.yaml.

Run: pytest build/test_store_declaration.py
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import store_declaration as sd  # noqa: E402

URL = "https://example.org/refget-store"


def _store(root, name, text, sources=True):
    d = root / name
    d.mkdir()
    (d / "project_config.yaml").write_text(text)
    if sources:
        (d / "sources.csv").write_text("fasta\n")


def _pub(priority, extra=""):
    return f"pep_version: 2.1.0\n{extra}publish:\n  priority: {priority}\n  description: d{priority}\n"


@pytest.fixture
def stores(tmp_path):
    _store(tmp_path, "jungle", _pub(10))
    _store(tmp_path, "main", _pub(30, "kind: catalog\n"), sources=False)
    _store(tmp_path, "plantref", _pub(50))
    _store(tmp_path, "held", "pep_version: 2.1.0\nnightly: false\n")
    _store(tmp_path, "quiet", "pep_version: 2.1.0\n")
    return tmp_path


def test_declaration_lists_published_source_stores(stores):
    out = sd.declaration(sd.read_stores(stores, URL + "/"))
    assert out == [
        {"name": "jungle", "url": f"{URL}/jungle/", "type": "remote",
         "priority": 10, "description": "d10"},
        {"name": "plantref", "url": f"{URL}/plantref/", "type": "remote",
         "priority": 50, "description": "d50"},
    ]
    json.dumps(out)


def test_catalog_store(stores):
    store = sd.catalog_store(sd.read_stores(stores, URL))
    assert (store["name"], store["url"], store["priority"]) == ("main", f"{URL}/main/", 30)


def _error(root):
    with pytest.raises(sd.DeclarationError) as exc:
        sd.read_stores(root, URL)
    return str(exc.value)


def test_duplicate_priority(stores):
    _store(stores, "vgp", _pub(10))
    assert "priority 10" in _error(stores)


def test_held_store_cannot_be_published(stores):
    _store(stores, "salmon", _pub(60, "nightly: false\n"))
    assert "salmon: has nightly: false" in _error(stores)


def test_exactly_one_catalog_store(stores):
    _store(stores, "other", _pub(60, "kind: catalog\n"), sources=False)
    assert "exactly one kind: catalog" in _error(stores)


def test_no_catalog_store(tmp_path):
    _store(tmp_path, "jungle", _pub(10))
    assert "found 0" in _error(tmp_path)


def test_url_must_be_set(stores):
    with pytest.raises(sd.DeclarationError, match="REFGETSTORE_PUBLIC_URL"):
        sd.read_stores(stores, "")


def test_registry_stores_are_valid():
    """The real stores/ tree: the published set this repo declares."""
    stores = sd.read_stores(public_url=URL)
    assert [(s["name"], s["priority"]) for s in stores] == [
        ("jungle", 10), ("vgp", 20), ("refgenie-main", 30),
        ("igenomes", 40), ("plantcore", 45),
    ]
    assert sd.catalog_store(stores)["name"] == "refgenie-main"
