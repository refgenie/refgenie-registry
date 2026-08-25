#!/usr/bin/env python3
"""Keep genomes/**/*.yaml and stores/*/sources.csv in step, and say which stores changed.

Every genome YAML declares, in `build.store`, the store that holds its sequence.
This script is the link between the two halves of that claim:

  * COVERAGE, for each genome, is there actually a row in that store's
    sources.csv for it? A genome whose store does not hold its sequence is
    registered but unloadable, and nothing else notices.
  * CHANGE   -- has a store's sources.csv changed since the store was last built?
    Rebuilding every store nightly is wasteful (the largest hold hundreds of
    thousands of objects), so the nightly rebuilds a store only when its sources
    changed. This is the question that gates it.

A store records the sources.csv it was built from in a `.sources.sha256` file
inside the built store ($REFGETSTORE_BASE/<slug>/), written by `--record` after a
successful build. A store with no such file has never been built and always counts
as changed.

Deliberately NOT a regenerator
------------------------------
An earlier design had this script REGENERATE each sources.csv from the genome
YAMLs. It cannot: a store row's `fasta` is the path or URL the store actually
ingests (jungle's are staged relative paths like
`homo_sapiens/ENA/hg38/fasta/GRCh38-ena-15_GCA_000001405.fa.gz`), and a genome
YAML records the upstream provider URL instead, the two are different strings
for the same sequence. Regenerating would also delete every row that has no genome
YAML yet, which is most of igenomes, refseq, salmon_txomes and plantref. So this
verifies and reports; it never rewrites a sources.csv.

Usage:
    python build/sync_stores.py                 # coverage report, all stores
    python build/sync_stores.py --check         # non-zero if a genome names no real store
    python build/sync_stores.py --changed       # print slugs of stores needing a rebuild
    python build/sync_stores.py --record vgp    # stamp vgp as built from its current sources
"""

from __future__ import annotations

import argparse
import collections
import csv
import glob
import hashlib
import os
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
GENOMES_DIR = REPO / "genomes"
STORES_DIR = REPO / "stores"

sys.path.insert(0, str(STORES_DIR))
from store_config import load_pep, store_slugs  # noqa: E402

PEP_CONFIG = REPO / "pep" / "config.yaml"
STAMP = ".sources.sha256"
# Both pep/config.yaml and every store's `fasta_root:` root their paths at the
# same env var, written either way. Normalizing lets one be compared to the other
# textually, without needing the var to be set on this machine.
FASTA_VAR = ("${REFGETSTORE_FASTA}", "$REFGETSTORE_FASTA")


class SyncError(Exception):
    pass


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------

def load_genomes(genomes_dir: Path = GENOMES_DIR) -> list:
    """[(name, record)] for every genome YAML, sorted by name."""
    out = []
    for path in sorted(glob.glob(str(genomes_dir / "**" / "*.yaml"), recursive=True)):
        data = yaml.safe_load(open(path))
        if isinstance(data, dict) and data.get("name"):
            out.append((data["name"], data))
    out.sort(key=lambda item: item[0])
    return out


def _normalize_root(path: str) -> str:
    for form in FASTA_VAR:
        if path.startswith(form):
            return "@FASTA@" + path[len(form):]
    return path


def staged_paths(pep_config: Path = PEP_CONFIG) -> dict:
    """{genome: staged FASTA path} from pep/config.yaml's `<genome>_fa` sources.

    This is the file the nightly actually ingests. A store row's `fasta` is the
    same file expressed relative to that store's `fasta_root:`, so this is what
    links a queued genome to its row when the genome YAML only records the
    upstream provider URL.
    """
    cfg = yaml.safe_load(pep_config.read_text())
    sources = ((cfg.get("sample_modifiers") or {}).get("derive") or {}).get("sources") or {}
    return {
        key[: -len("_fa")]: _normalize_root(str(value))
        for key, value in sources.items()
        if key.endswith("_fa")
    }


def store_fasta_root(slug: str) -> str | None:
    root = load_pep(STORES_DIR / slug).get("fasta_root")
    return _normalize_root(str(root)).rstrip("/") if root else None


def store_rows(slug: str) -> list:
    path = STORES_DIR / slug / "sources.csv"
    if not path.is_file():
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def row_keys(row: dict) -> set:
    """Every string a genome YAML could plausibly be matched against."""
    keys = set()
    for token in (row.get("fasta") or "").split():
        keys.add(token)
        keys.add(os.path.basename(token))
    for column in ("name", "accession", "genome_assembly"):
        value = (row.get(column) or "").strip()
        if value:
            keys.add(value)
    return keys


def genome_keys(name: str, data: dict, staged: str | None = None,
                fasta_root: str | None = None) -> set:
    """Every string that could match a store row for this genome."""
    keys = {name}
    if staged:
        keys.add(staged)
        keys.add(os.path.basename(staged))
        if fasta_root and staged.startswith(fasta_root + "/"):
            keys.add(staged[len(fasta_root) + 1:])
    for source in (data.get("fasta") or {}).get("sources") or []:
        url = source.get("url") or ""
        if url:
            keys.add(url)
            keys.add(os.path.basename(url))
    accession = (data.get("assembly") or {}).get("accession")
    if accession:
        keys.add(accession)
    for alias in data.get("aliases") or []:
        keys.add(alias)
    return keys


# --------------------------------------------------------------------------
# coverage
# --------------------------------------------------------------------------

def coverage() -> dict:
    """{slug: {"genomes": [...], "uncovered": [...], "rows": n}} for every declared store."""
    slugs = set(store_slugs())
    declared = collections.defaultdict(list)
    unknown = []
    for name, data in load_genomes():
        store = ((data.get("build") or {}).get("store"))
        if store not in slugs:
            unknown.append((name, store))
            continue
        declared[store].append((name, data))

    staged = staged_paths()
    report = {}
    for slug in sorted(declared):
        rows = store_rows(slug)
        fasta_root = store_fasta_root(slug)
        index = set()
        for row in rows:
            index |= row_keys(row)
        uncovered = [
            name
            for name, data in declared[slug]
            if not (genome_keys(name, data, staged.get(name), fasta_root) & index)
        ]
        report[slug] = {
            "genomes": [name for name, _ in declared[slug]],
            "uncovered": uncovered,
            "rows": len(rows),
        }
    return report, unknown


# --------------------------------------------------------------------------
# change detection
# --------------------------------------------------------------------------

def sources_digest(slug: str) -> str | None:
    path = STORES_DIR / slug / "sources.csv"
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stamp_path(slug: str) -> Path | None:
    """$REFGETSTORE_BASE/<slug>/.sources.sha256, or None if the base is unset."""
    base = os.environ.get("REFGETSTORE_BASE")
    if not base:
        return None
    return Path(base) / slug / STAMP


def nightly_enabled(slug: str) -> bool:
    """False when a store opts out of automatic nightly building.

    A store sets ``nightly: false`` in its project_config.yaml to stay out of the
    build loop. This is NOT the same as stamping it: a stamp asserts "the built
    store matches this sources.csv", which would be a lie for a store that has
    never been built. Opting out says what is actually true, we are choosing
    not to build this one, and it survives a sources.csv edit, where a stamp
    would silently go stale and pull the store back into the loop.
    """
    return load_pep(STORES_DIR / slug).get("nightly", True) is not False


def changed_stores() -> list:
    """Slugs whose sources.csv differs from what the built store was made from.

    Stores with ``nightly: false`` are never reported, however stale they are.
    """
    changed = []
    for slug in store_slugs():
        if not nightly_enabled(slug):
            continue
        digest = sources_digest(slug)
        if digest is None:
            continue
        stamp = stamp_path(slug)
        if stamp is None:
            raise SyncError(
                "REFGETSTORE_BASE is not set, so there is no built store to compare "
                "against. Source infra/rivanna/env.sh first."
            )
        if not stamp.is_file() or stamp.read_text().strip() != digest:
            changed.append(slug)
    return changed


def record(slug: str) -> int:
    digest = sources_digest(slug)
    if digest is None:
        raise SyncError(f"stores/{slug}/sources.csv does not exist")
    stamp = stamp_path(slug)
    if stamp is None:
        raise SyncError("REFGETSTORE_BASE is not set; nowhere to record the stamp")
    if not stamp.parent.is_dir():
        raise SyncError(f"{stamp.parent} does not exist, build the store first")
    stamp.write_text(digest + "\n")
    print(f"sync_stores: recorded {slug} as built from sources.csv {digest[:12]}")
    return 0


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def report(check: bool) -> int:
    data, unknown = coverage()
    print(f"{'store':16} {'genomes':>8} {'rows':>6} {'uncovered':>10}")
    total_uncovered = 0
    for slug, info in data.items():
        total_uncovered += len(info["uncovered"])
        print(f"{slug:16} {len(info['genomes']):8} {info['rows']:6} "
              f"{len(info['uncovered']):10}")
    print()
    for slug, info in data.items():
        for name in info["uncovered"]:
            print(f"  NOT LOADABLE {name}: build.store is '{slug}', but "
                  f"stores/{slug}/sources.csv has no row for it")
    if total_uncovered:
        print(f"\nsync_stores: {total_uncovered} genome(s) declare a store that does "
              f"not hold their sequence. Add the row to that store's sources.csv, or "
              f"correct build.store.")

    if unknown:
        for name, store in unknown:
            print(f"sync_stores: FATAL genome '{name}' declares build.store "
                  f"'{store}', which is not a store.", file=sys.stderr)
        return 1
    return 1 if (check and total_uncovered) else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true",
                       help="exit non-zero if any genome is not loadable from its store")
    group.add_argument("--changed", action="store_true",
                       help="print, one per line, the stores whose sources.csv changed "
                            "since they were built")
    group.add_argument("--record", metavar="STORE",
                       help="stamp STORE as built from its current sources.csv")
    args = parser.parse_args(argv)

    try:
        if args.record:
            return record(args.record)
        if args.changed:
            for slug in changed_stores():
                print(slug)
            return 0
        return report(args.check)
    except SyncError as exc:
        print(f"sync_stores: ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
