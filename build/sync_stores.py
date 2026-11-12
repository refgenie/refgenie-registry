#!/usr/bin/env python3
"""Keep genomes/**/*.yaml and stores/*/sources.csv in step, and say which stores changed.

Every genome YAML declares, in `build.store`, the store that holds its sequence.
This script is the link between the two halves of that claim:

  * COVERAGE, for each genome, is there actually a row in that store's
    sources.csv for it? A genome whose store does not hold its sequence is
    registered but unloadable, and nothing else notices. A store_only genome
    whose store has no `publish:` block in its project_config.yaml is reported
    too: the catalog never ingests that store, so the genome never reaches it.
    (A genome with assets reaches the catalog through genome_init and the
    catalog's own store, refgenie-main, whatever store its FASTA comes from.)
  * CHANGE   -- has a store's sources.csv changed since the store was last built?
    Rebuilding every store nightly is wasteful (the largest hold hundreds of
    thousands of objects), so the nightly rebuilds a store only when its sources
    changed. This is the question that gates it.

A row's `name` (and its genome YAML's `aliases`) are only aliases, so a
rename must not re-read every FASTA. Two stamps inside the built store
($REFGETSTORE_BASE/<slug>/), both written by `--record` after a successful run,
split the question:

  * `.sources.sha256` covers what affects sequence: the rows' `fasta` values.
    When it differs the store needs a full load (`--changed`).
  * `.aliases.sha256` covers the whole sources.csv plus the `name` and
    `aliases` of the store's genome YAMLs. When only it differs, the store
    needs just an alias refresh (`--alias-changed`).

A store with no stamp has never been built and always counts as changed.

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
    python build/sync_stores.py --changed       # print slugs of stores needing a full load
    python build/sync_stores.py --alias-changed # print slugs needing only an alias refresh
    python build/sync_stores.py --record vgp    # stamp vgp as built from its current sources
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import os
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
GENOMES_DIR = REPO / "genomes"
STORES_DIR = REPO / "stores"

sys.path.insert(0, str(STORES_DIR))
import genome_rows  # noqa: E402
from store_config import get_store_dirs, load_pep, publish_settings, store_slugs  # noqa: E402

PEP_CONFIG = REPO / "pep" / "config.yaml"
STAMP = ".sources.sha256"
ALIAS_STAMP = ".aliases.sha256"


class SyncError(Exception):
    pass


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------

def load_genomes(genomes_dir: Path = GENOMES_DIR) -> list:
    """[(name, record)] for every genome YAML, sorted by name."""
    return [(g.name, g.data) for g in genome_rows.load_genomes(genomes_dir)]


def store_rows(slug: str) -> list:
    return genome_rows.read_rows(slug, STORES_DIR)


# --------------------------------------------------------------------------
# coverage
# --------------------------------------------------------------------------

def coverage() -> tuple:
    """({slug: info}, unknown) for every store some genome declares.

    info is {"genomes", "rows", "uncovered", "unaligned", "errors"}:
      uncovered  genomes with no row (or an ambiguous one), with the reason
      unaligned  (genome, row name) where a row was found but its `name` is
                 not the genome's name; tools/align_store_names.py fixes these
      errors     genomes whose seqcol.digest contradicts the store, or whose
                 staged FASTA (pep/config.yaml) is not the file of their row
    Matching rules are in stores/genome_rows.py.
    """
    slugs = set(store_slugs())
    declared = collections.defaultdict(list)
    unknown = []
    for g in genome_rows.load_genomes(GENOMES_DIR):
        if g.store not in slugs:
            unknown.append((g.name, g.store))
            continue
        declared[g.store].append(g)

    report = {}
    for slug in sorted(declared):
        rows = store_rows(slug)
        staged = genome_rows.staged_fastas(slug, PEP_CONFIG, STORES_DIR)
        found = genome_rows.match_store(
            declared[slug], rows, genome_rows.store_row_digests(slug, rows), staged
        )
        report[slug] = {
            "genomes": [g.name for g in declared[slug]],
            "rows": len(rows),
            "uncovered": found.problems,
            "unaligned": [
                (name, genome_rows.row_name(rows[i]))
                for name, (i, rule) in sorted(found.matches.items()) if rule != "name"
            ],
            "errors": found.errors + staged_mismatches(found, rows, staged),
        }
    return report, unknown


def staged_mismatches(found, rows: list, staged: dict) -> list:
    """(genome, message) for each genome the nightly builds from another row's file.

    The build reads the staged FASTA, while the catalog describes the genome's
    row, so a mismatch builds one collection under another's name.
    """
    errors = []
    for name, (i, _rule) in sorted(found.matches.items()):
        path, row = staged.get(name), rows[i]
        if path and path != genome_rows.row_fasta(row):
            holder = next((genome_rows.row_name(r) for r in rows
                           if genome_rows.row_fasta(r) == path), "none")
            errors.append((name, f"stages {path} (row {holder}) but its row is "
                                 f"{genome_rows.row_name(row)} ({genome_rows.row_fasta(row)})"))
    return errors


# --------------------------------------------------------------------------
# change detection
# --------------------------------------------------------------------------

def sources_digest(slug: str) -> str | None:
    """sha256 of the rows' `fasta` values (sorted): what a full load depends on."""
    rows = store_rows(slug)
    if not (STORES_DIR / slug / "sources.csv").is_file():
        return None
    fastas = sorted(" ".join((r.get("fasta") or "").split()) for r in rows)
    return hashlib.sha256("\n".join(fastas).encode()).hexdigest()


def legacy_sources_digest(slug: str) -> str | None:
    """sha256 of the whole sources.csv: what `.sources.sha256` held before the
    fasta-only stamp. Accepted as up to date so the first nightly after the
    change does not reload every store; delete once every store is re-stamped.
    """
    path = STORES_DIR / slug / "sources.csv"
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def aliases_digest(slug: str) -> str | None:
    """sha256 of the whole sources.csv plus the store's YAML names and aliases."""
    path = STORES_DIR / slug / "sources.csv"
    if not path.is_file():
        return None
    h = hashlib.sha256(path.read_bytes())
    for name, data in load_genomes():
        if (data.get("build") or {}).get("store") == slug:
            h.update(f"\n{name}\t{sorted(map(str, data.get('aliases') or []))}".encode())
    return h.hexdigest()


def stamp_path(slug: str, name: str = STAMP) -> Path | None:
    """$REFGETSTORE_BASE/<slug>/<name>, or None if the base is unset."""
    base = os.environ.get("REFGETSTORE_BASE")
    if not base:
        return None
    return Path(base) / slug / name


def _stamp(slug: str, name: str) -> str | None:
    stamp = stamp_path(slug, name)
    if stamp is None:
        raise SyncError(
            "REFGETSTORE_BASE is not set, so there is no built store to compare "
            "against. Source infra/rivanna/env.sh first."
        )
    return stamp.read_text().strip() if stamp.is_file() else None


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


def published_stores() -> set:
    """Slugs of source stores with a `publish:` block (see build/store_declaration.py)."""
    return {d.name for d in get_store_dirs(STORES_DIR) if publish_settings(d) is not None}


def changed_stores() -> list:
    """Slugs whose `fasta` rows differ from what the built store was loaded from.

    Stores with ``nightly: false`` are never reported, however stale they are.
    """
    changed = []
    for slug in store_slugs():
        if not nightly_enabled(slug):
            continue
        digest = sources_digest(slug)
        if digest is None:
            continue
        stamp = _stamp(slug, STAMP)
        if stamp not in (digest, legacy_sources_digest(slug)):
            changed.append(slug)
    return changed


def alias_changed_stores() -> list:
    """Slugs that need only an alias refresh: sequence unchanged, names changed."""
    full = set(changed_stores())
    changed = []
    for slug in store_slugs():
        if slug in full or not nightly_enabled(slug):
            continue
        digest = aliases_digest(slug)
        if digest is not None and _stamp(slug, ALIAS_STAMP) != digest:
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
    stamp_path(slug, ALIAS_STAMP).write_text(aliases_digest(slug) + "\n")
    print(f"sync_stores: recorded {slug} as built from sources.csv {digest[:12]}")
    return 0


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def report(check: bool) -> int:
    data, unknown = coverage()
    print(f"{'store':16} {'genomes':>8} {'rows':>6} {'uncovered':>10} {'unaligned':>10} "
          f"{'errors':>7}")
    totals = collections.Counter()
    published = published_stores()
    store_only = {name for name, rec in load_genomes()
                  if (rec.get("build") or {}).get("tier", "store_only") == "store_only"}
    for slug, info in data.items():
        if not nightly_enabled(slug):
            # A store kept out of the nightly (nightly: false) is on hold: its
            # genomes cannot load yet, which is expected, not a gap.
            info["held"], info["uncovered"] = info["uncovered"], []
        elif slug not in published:
            info["unpublished"] = [n for n in info["genomes"] if n in store_only]
            totals["unpublished"] += len(info["unpublished"])
        for key in ("uncovered", "unaligned", "errors"):
            totals[key] += len(info[key])
        print(f"{slug:16} {len(info['genomes']):8} {info['rows']:6} "
              f"{len(info['uncovered']):10} {len(info['unaligned']):10} "
              f"{len(info['errors']):7}")
    print()
    for slug, info in data.items():
        for name, _why in info.get("held", []):
            print(f"  ON HOLD {name}: store '{slug}' is not built nightly (nightly: false)")
        for name in info.get("unpublished", []):
            print(f"  NOT PUBLISHED {name}: build.store is '{slug}', which has no "
                  f"publish: block in stores/{slug}/project_config.yaml")
        for name, why in info["uncovered"]:
            print(f"  NOT LOADABLE {name}: build.store is '{slug}', but "
                  f"stores/{slug}/sources.csv has no row for it ({why})")
        for name, why in info["errors"]:
            print(f"  ERROR {name} in {slug}: {why}")
        for name, row in info["unaligned"]:
            print(f"  NOT ALIGNED {name}: its row in stores/{slug}/sources.csv is "
                  f"named {row!r}")
    if totals["uncovered"]:
        print(f"\nsync_stores: {totals['uncovered']} genome(s) declare a store that does "
              f"not hold their sequence. Add the row to that store's sources.csv, or "
              f"correct build.store.")
    if totals["unpublished"]:
        print(f"\nsync_stores: WARNING {totals['unpublished']} store_only genome(s) name a "
              f"store that is not published, so the catalog never ingests them. Add a publish: "
              f"block to that store (stores/README.md), or correct build.store.")
    if totals["unaligned"]:
        print(f"\nsync_stores: {totals['unaligned']} row(s) are not named after their "
              f"genome. Run tools/align_store_names.py --apply.")

    if unknown:
        for name, store in unknown:
            print(f"sync_stores: FATAL genome '{name}' declares build.store "
                  f"'{store}', which is not a store.", file=sys.stderr)
        return 1
    if totals["errors"]:
        return 1
    return 1 if (check and (totals["uncovered"] or totals["unaligned"])) else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true",
                       help="exit non-zero if any genome is not loadable from its store, "
                            "or its row is not named after it")
    group.add_argument("--changed", action="store_true",
                       help="print, one per line, the stores whose rows' fasta "
                            "changed since they were loaded (a full load)")
    group.add_argument("--alias-changed", action="store_true",
                       help="print, one per line, the stores whose sequences are "
                            "unchanged but whose names or aliases changed")
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
        if args.alias_changed:
            for slug in alias_changed_stores():
                print(slug)
            return 0
        return report(args.check)
    except SyncError as exc:
        print(f"sync_stores: ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
