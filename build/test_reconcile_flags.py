"""Stale build flags must not hide an asset the catalog lost.

Run: pytest build/test_reconcile_flags.py
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

import reconcile_genomes as rc  # noqa: E402


class FakeRg:
    """Just the refgenie surface reconcile_flags touches."""

    def __init__(self, builds: Path, catalog: dict[str, set[str]]):
        self._catalog = catalog
        self.alias = SimpleNamespace(resolve=self._resolve)
        self.asset = SimpleNamespace(list_all=self._list_all)
        self.build = SimpleNamespace(
            target_template=lambda group, name: builds
            / "{genome_name}"
            / group
            / name
            / f"{{genome_name}}_{group}__{name}.flag"
        )

    def _resolve(self, name):
        if name not in self._catalog:
            raise KeyError(name)
        return f"digest-{name}"

    def _list_all(self, genome_digests):
        name = genome_digests[0].removeprefix("digest-")
        return {genome_digests[0]: [f"{g}:default" for g in self._catalog[name]]}, {}


def test_prunes_completion_flags_of_missing_asset_only(tmp_path):
    folder = tmp_path / "hg19" / "fasta" / "default"
    folder.mkdir(parents=True)
    done = [
        folder / "hg19_fasta__default.flag",
        folder / "refgenie_hg19_fasta_default_DEFAULT_SAMPLE_NAME_completed.flag",
    ]
    in_flight = [
        folder / "lock.hg19_fasta__default.flag",
        folder / "refgenie_hg19_fasta_default_DEFAULT_SAMPLE_NAME_running.flag",
    ]
    kept = tmp_path / "hg19" / "bwa_index" / "0.7.19" / "hg19_bwa_index__0.7.19.flag"
    kept.parent.mkdir(parents=True)
    for f in [*done, *in_flight, kept]:
        f.touch()
    rg = FakeRg(tmp_path, {"hg19": {"bwa_index"}})

    found = rc.reconcile_flags(rg, [("hg19", "fasta"), ("hg19", "bwa_index")])

    assert sorted(found) == sorted(done)
    assert not any(f.exists() for f in done)
    assert all(f.exists() for f in [*in_flight, kept])
