import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "stores"))

from check_catalog_names import check  # noqa: E402
from genome_rows import Genome  # noqa: E402


def _genome(name, aliases=(), digest=None):
    data = {"name": name, "aliases": list(aliases)}
    if digest:
        data["seqcol"] = {"digest": digest}
    return Genome(name, data, Path(f"{name}.yaml"))


GENOMES = [_genome("hg38", ["GRCh38"], digest="D1"), _genome("t7")]


def test_registry_and_store_names_pass():
    exported = {"hg38": "D1", "GRCh38": "D1", "t7": "D2", "hg38-ddbj": "D1"}
    assert check(exported, {"hg38-ddbj"}, GENOMES) == ([], [])


def test_stale_name_is_extra_and_missing_alias_warns():
    exported = {"hg38": "D1", "GRCh38-igenomes-build37-1-ncbi": "D1", "t7": "D2"}
    extra, missing = check(exported, set(), GENOMES)
    assert extra == ["GRCh38-igenomes-build37-1-ncbi -> D1"]
    assert missing == ["GRCh38 -> D1"]


def test_registry_name_on_another_genome_is_extra():
    exported = {"hg38": "D1", "GRCh38": "D2", "t7": "D2"}
    extra, missing = check(exported, set(), GENOMES)
    assert extra == ["GRCh38 -> D2"]
    assert missing == ["GRCh38 -> D1"]
