#!/usr/bin/env python3
"""Compare a built RefgetStore against what its current sources.csv would produce.

This reconciles two views of a store:

  1. The BUILT store on disk ($REFGETSTORE_BASE/<store>): the collections,
     aliases, and sequence counts actually present.
  2. The DECLARED sources (stores/<store>/sources.csv, the "new sources
     approach"): the FASTA rows that the current build.py would load, and
     the aliases it would register (from the name/accession/genome_assembly
     columns).

It reports:
  - built store totals (stats) and per-collection digests + aliases + n_sequences
  - sources declared / present-on-disk / missing-on-disk
  - diff by alias/identifier:
      * sources whose identifiers ARE found as a store alias  (accounted for)
      * sources whose identifiers are NOT found in the store   (unmatched)
      * store collections with aliases NOT declared by any source (orphans)
  - a reconciliation summary line.

IMPORTANT LIMITATION: matching here is by ALIAS / IDENTIFIER STRING, not by
recomputed content digest. Recomputing FASTA content digests for ~16 GB of
sources is far too expensive to do for a comparison, so a source and a store
collection are considered "the same" only when a declared identifier
(name / accession / genome_assembly) appears as a registered alias in the
store. Collections that were built by an older/different pipeline and never
had matching aliases registered will therefore show up as "orphan" even if
their content is in fact reproducible from some source.

Usage:
    python compare_store_to_sources.py            # defaults to 'jungle'
    python compare_store_to_sources.py jungle
    python compare_store_to_sources.py salmon_txomes

Requires:  $REFGETSTORE_BASE set (see env.sh); refget/gtars installed.
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

from refget.store import RefgetStore

SCRIPT_DIR = Path(__file__).parent  # the stores/ directory
# Columns that build.py registers as collection aliases.
ALIAS_COLUMNS = ("name", "accession", "genome_assembly")


def store_path(store_name: str) -> Path:
    base = os.environ.get("REFGETSTORE_BASE")
    if not base:
        sys.exit("REFGETSTORE_BASE not set. Source env.sh first.")
    return Path(base) / store_name


def sources_csv(store_name: str) -> Path:
    return SCRIPT_DIR / store_name / "sources.csv"


def resolve_local(token: str) -> Path:
    """Resolve a non-URL fasta token the way build.py would (relative to stores/)."""
    p = Path(token)
    return p if p.is_absolute() else (SCRIPT_DIR / p).resolve()


def is_url(token: str) -> bool:
    return token.startswith(("http://", "https://", "ftp://", "s3://"))


def collect_store_aliases(store: RefgetStore) -> tuple[dict, set]:
    """Return (digest -> {namespace: [aliases]}, set-of-all-alias-strings)."""
    by_digest: dict[str, dict[str, list[str]]] = {}
    all_alias_values: set[str] = set()
    for ns in store.list_collection_alias_namespaces():
        for alias in store.list_collection_aliases(ns):
            all_alias_values.add(alias)
            meta = store.get_collection_metadata_by_alias(ns, alias)
            if meta is None:
                continue
            by_digest.setdefault(meta.digest, {}).setdefault(ns, []).append(alias)
    return by_digest, all_alias_values


def read_sources(csv_path: Path) -> list[dict]:
    with open(csv_path) as f:
        return list(csv.DictReader(f))


def source_identifiers(row: dict) -> list[str]:
    """The identifier strings build.py would register as aliases for this row."""
    out = []
    for col in ALIAS_COLUMNS:
        val = (row.get(col) or "").strip()
        if val:
            out.append(val)
    return out


def main():
    store_name = sys.argv[1] if len(sys.argv) > 1 else "jungle"

    sp = store_path(store_name)
    cp = sources_csv(store_name)
    if not sp.exists():
        sys.exit(f"Store not found on disk: {sp}")
    if not cp.exists():
        sys.exit(f"sources.csv not found: {cp}")

    print("=" * 78)
    print(f"COMPARE built store vs. sources.csv  —  store '{store_name}'")
    print(f"  store path : {sp}")
    print(f"  sources    : {cp}")
    print("=" * 78)
    print("NOTE: matching is by alias/identifier string, NOT by recomputed")
    print("      content digest (re-digesting source FASTAs is too expensive).")
    print()

    store = RefgetStore.on_disk(str(sp))
    stats = store.stats()

    # ---- 1. Built store ----------------------------------------------------
    print("-" * 78)
    print("1. BUILT STORE")
    print("-" * 78)
    print(f"  stats: {stats}")
    try:
        print(f"  store_metadata: {store.store_metadata()}")
    except Exception as e:
        print(f"  store_metadata: (unavailable: {e})")

    by_digest, all_alias_values = collect_store_aliases(store)
    namespaces = store.list_collection_alias_namespaces()
    n_aliases = sum(len(v) for ns in by_digest.values() for v in ns.values())
    print(f"  alias namespaces present: {namespaces}")
    print(f"  collections carrying >=1 alias: {len(by_digest)}")
    print(f"  total alias entries: {n_aliases}")
    print()

    # Enumerate all collections (digest, n_sequences, aliases).
    page, page_size, all_meta = 0, 500, []
    while True:
        res = store.list_collections(page=page, page_size=page_size)
        all_meta.extend(res["results"])
        pag = res["pagination"]
        if (pag["page"] + 1) * pag["page_size"] >= pag["total"]:
            break
        page += 1
    print(f"  enumerated {len(all_meta)} collections (digest / n_seq / aliases):")
    for m in all_meta:
        aliases = by_digest.get(m.digest, {})
        flat = "; ".join(f"{ns}:{','.join(vs)}" for ns, vs in aliases.items()) or "-"
        print(f"    {m.digest}  nseq={m.n_sequences:<6} {flat}")
    print()

    # ---- 2. Sources --------------------------------------------------------
    print("-" * 78)
    print("2. SOURCES (current new-sources-approach declarations)")
    print("-" * 78)
    rows = read_sources(cp)
    present, missing = [], []
    for i, row in enumerate(rows, start=1):
        field = (row.get("fasta") or "").strip()
        tokens = field.split()
        # A row is buildable only if every non-URL token exists on disk.
        ok = True
        for tok in tokens:
            if is_url(tok):
                continue  # remote: cannot judge cheaply; treat as buildable
            if not resolve_local(tok).exists():
                ok = False
                break
        (present if ok else missing).append((i, row))
    print(f"  declared rows : {len(rows)}")
    print(f"  present (file on disk / remote) : {len(present)}")
    print(f"  MISSING (local file absent)     : {len(missing)}")
    if missing:
        print(f"  missing rows ({len(missing)}):")
        for i, row in missing:
            ident = row.get("name") or row.get("pep_sample_name") or "?"
            print(f"    row {i}: {ident}  ->  {(row.get('fasta') or '').strip()}")
    print()

    # ---- 3. Diff by alias/identifier --------------------------------------
    print("-" * 78)
    print("3. DIFF BY ALIAS / IDENTIFIER")
    print("-" * 78)
    matched, unmatched = [], []
    matched_alias_values: set[str] = set()
    for i, row in present:
        idents = source_identifiers(row)
        hits = [v for v in idents if v in all_alias_values]
        if hits:
            matched.append((i, row, hits))
            matched_alias_values.update(hits)
        else:
            unmatched.append((i, row, idents))

    print(f"  present sources whose identifier IS a store alias : {len(matched)}")
    for i, row, hits in matched:
        print(f"    row {i}: {row.get('name')}  matched={hits}")
    print()
    print(f"  present sources NOT found as a store alias        : {len(unmatched)}")
    for i, row, idents in unmatched:
        print(f"    row {i}: {row.get('name')}  idents={idents or '(none declared)'}")
    print()

    orphan_values = sorted(all_alias_values - matched_alias_values)
    print(f"  store alias values NOT claimed by any present source : {len(orphan_values)}")
    for v in orphan_values:
        # which namespace + digest
        loc = []
        for dig, ns_map in by_digest.items():
            for ns, vals in ns_map.items():
                if v in vals:
                    loc.append(f"{ns}->{dig}")
        print(f"    {v}  ({'; '.join(loc)})")
    print()

    # Collections with no alias at all (cannot be matched by identifier).
    unaliased = [m for m in all_meta if m.digest not in by_digest]
    print(f"  store collections with NO alias at all : {len(unaliased)}")
    print(f"    (these cannot be matched to any source by identifier)")
    print()

    # ---- 4. Reconciliation summary ----------------------------------------
    print("=" * 78)
    print("4. SUMMARY")
    print("=" * 78)
    print(
        f"  Store has {stats['n_collections']} collections "
        f"({len(by_digest)} carry aliases, {len(unaliased)} have none)."
    )
    print(
        f"  Sources declare {len(rows)} rows "
        f"({len(present)} present, {len(missing)} missing on disk)."
    )
    print(
        f"  Of present sources: {len(matched)} matched a store alias, "
        f"{len(unmatched)} did not."
    )
    print(
        f"  {len(unaliased)} store collections are NOT represented by any "
        f"current source identifier (orphan/legacy content)."
    )
    print()
    print("  Interpretation: a large gap between store collection count and")
    print("  matched sources indicates the built store was produced by a")
    print("  different/older pipeline than the current sources.csv. See the")
    print("  alias-namespace mismatch above for confirmation.")


if __name__ == "__main__":
    main()
