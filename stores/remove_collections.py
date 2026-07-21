#!/usr/bin/env python3
"""Remove collections from a built RefgetStore.

`build.py` is additive only: it calls `add_sequence_collections_from_fastas`
and never removes anything. Deleting a row from a store's `sources.csv` and
rebuilding therefore does NOT drop that collection — it persists in the store
forever. Removal has to be an explicit, separate operation, which is what this
script is.

Removal goes through `RefgetStore.remove_collection(digest,
remove_orphan_sequences=True)`. That drops the collection record, its name
lookup, its FHR metadata, and every collection alias pointing at it, and then
garbage-collects the sequences that no remaining collection references. Because
the store is content-addressed, sequences shared with a surviving collection are
correctly retained — do NOT try to do this by deleting `.seq` files by hand.

For `on_disk` stores `store.is_persisting` is True, so changes hit disk as they
are made; there is no explicit `write()` to call.

Safety: every target is named as `<alias>=<expected_digest>`. The alias is
resolved through the store and the resolved digest is asserted against the
expected one before anything is removed. An upstream rename must not silently
delete the wrong collection.

Usage:
    source ../infra/rivanna/env.sh

    # Resolve and report, remove nothing:
    python remove_collections.py plantref --dry-run \
        --expect hordeum_vulgare_MIPS=hBkUaFdD-vx4e6KH0j3I3DLdEz0JE6q9

    # Actually remove (requires --yes):
    python remove_collections.py plantref --yes \
        --expect hordeum_vulgare_MIPS=hBkUaFdD-vx4e6KH0j3I3DLdEz0JE6q9

A bare digest with no alias is also accepted (`--digest <digest>`), but the
`--expect` form is strongly preferred: it cross-checks two independent
identifiers instead of trusting one.

Note that the S3 mirror is only updated by a sync run with `--delete`
(`build.py --sync --delete`). A plain sync leaves the removed objects orphaned
in the bucket.

Requires: $REFGETSTORE_BASE set (see infra/rivanna/env.sh); refget/gtars installed.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from refget.store import RefgetStore

# The alias namespace build.py registers `name` values into (see ALIAS_COLUMNS
# in build.py). --expect aliases are resolved here.
ALIAS_NAMESPACE = "name"


def store_path(store_name: str) -> Path:
    base = os.environ.get("REFGETSTORE_BASE")
    if not base:
        sys.exit("REFGETSTORE_BASE not set. Source env.sh first.")
    return Path(base) / store_name


def as_int(val) -> int | None:
    """stats() reports its counts as strings; coerce for arithmetic/formatting."""
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def parse_expect(spec: str) -> tuple[str, str]:
    """Parse an `<alias>=<expected_digest>` target."""
    if "=" not in spec:
        sys.exit(f"--expect needs <alias>=<digest>, got: {spec!r}")
    alias, digest = spec.split("=", 1)
    alias, digest = alias.strip(), digest.strip()
    if not alias or not digest:
        sys.exit(f"--expect needs a non-empty alias and digest, got: {spec!r}")
    return alias, digest


def enumerate_collections(store: RefgetStore) -> list:
    """All collection metadata records, paging until exhausted."""
    page, page_size, out = 0, 500, []
    while True:
        res = store.list_collections(page=page, page_size=page_size)
        out.extend(res["results"])
        pag = res["pagination"]
        if (pag["page"] + 1) * pag["page_size"] >= pag["total"]:
            break
        page += 1
    return out


def load_all_collections(store: RefgetStore, metas: list) -> None:
    """Force every collection into memory before removing anything.

    THIS IS NOT OPTIONAL. On a freshly reopened on-disk store, collections are
    stubs (`n_collections_loaded` == 0) and orphan-sequence GC SILENTLY DOES
    NOTHING: `remove_collection(..., remove_orphan_sequences=True)` still returns
    True and still drops the collection record and its aliases, but `n_sequences`
    does not move and the orphaned `.seq` files stay on disk. Verified on
    gtars 0.9.1 / refget 0.11.0 with a two-collection scratch store:

        reopened, no load : n_sequences 3 -> 3, .seq files 3 -> 3   (GC no-op)
        after force-load  : n_sequences 3 -> 2, .seq files 3 -> 2   (correct)

    In both cases the shared sequence was retained and the surviving collection
    stayed readable, so the retention half of the content-addressing is fine —
    it is only the reclaim half that needs the collections resident.

    The GC has to know which sequences the REMAINING collections reference, and
    it can only see that for collections it has actually loaded. This loads
    metadata only (`n_sequences_loaded` stays 0), not sequence payloads.

    Use `load_all_collections()`, NOT a `get_collection()` loop. Both populate
    `name_lookup`, which is all the GC needs, but `get_collection()` also
    materializes the whole collection to hand back: it clones the metadata for
    every sequence, allocates a fresh String per name, builds a Vec of every
    record, and marshals all of it into Python objects -- which a force-load then
    throws away. On plantref that meant ~14.5M records built and discarded, and
    the loop took 18-40 minutes. `load_all_collections()` only parses the index
    into the store's internal maps.
    """
    print(f"  loading {len(metas)} collections (required for orphan GC)...")
    store.load_all_collections()


def resolve_targets(
    store: RefgetStore, expects: list[str], digests: list[str]
) -> list[tuple[str, str]]:
    """Resolve every target to (label, digest), aborting on any mismatch.

    Nothing is removed until ALL targets resolve cleanly — a partial removal
    followed by an abort would leave the store in a state neither the old nor
    the new sources.csv describes.
    """
    present = {m.digest for m in enumerate_collections(store)}
    targets: list[tuple[str, str]] = []
    problems: list[str] = []

    for spec in expects:
        alias, expected = parse_expect(spec)
        meta = store.get_collection_metadata_by_alias(ALIAS_NAMESPACE, alias)
        if meta is None:
            problems.append(f"alias {ALIAS_NAMESPACE}:{alias!r} does not resolve")
            continue
        if meta.digest != expected:
            problems.append(
                f"alias {ALIAS_NAMESPACE}:{alias!r} resolves to {meta.digest}, "
                f"expected {expected} — REFUSING (upstream rename?)"
            )
            continue
        print(f"  resolved {alias} -> {meta.digest} (n_sequences={meta.n_sequences})")
        targets.append((alias, meta.digest))

    for digest in digests:
        if digest not in present:
            problems.append(f"digest {digest} is not in the store")
            continue
        print(f"  resolved (bare digest) -> {digest}")
        targets.append((digest, digest))

    if problems:
        print("\nRefusing to proceed:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        sys.exit(1)

    seen: dict[str, str] = {}
    for label, digest in targets:
        if digest in seen:
            sys.exit(f"Duplicate target: {label} and {seen[digest]} are the same collection")
        seen[digest] = label
    return targets


def main():
    parser = argparse.ArgumentParser(
        description="Remove collections from a built RefgetStore.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("store", help="Store name (e.g. plantref)")
    parser.add_argument(
        "--expect", action="append", default=[], metavar="ALIAS=DIGEST",
        help="Collection to remove, named as <name-alias>=<expected digest>. "
             "The alias is resolved and asserted against the digest. Repeatable.",
    )
    parser.add_argument(
        "--digest", action="append", default=[], metavar="DIGEST",
        help="Collection to remove by bare digest, with no alias cross-check. "
             "Prefer --expect. Repeatable.",
    )
    parser.add_argument(
        "--keep-orphan-sequences", action="store_true",
        help="Do NOT garbage-collect sequences left unreferenced by the removal. "
             "Default is to remove them, which is the point of the exercise.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Resolve and report; remove nothing")
    parser.add_argument("--yes", action="store_true", help="Required to actually remove")
    args = parser.parse_args()

    if not args.expect and not args.digest:
        sys.exit("Nothing to do: pass at least one --expect or --digest.")
    if not args.dry_run and not args.yes:
        sys.exit("Refusing to remove without --yes (or use --dry-run).")

    sp = store_path(args.store)
    if not sp.exists():
        sys.exit(f"Store not found on disk: {sp}")

    print("=" * 78)
    print(f"REMOVE COLLECTIONS  —  store '{args.store}'")
    print(f"  store path : {sp}")
    print(f"  mode       : {'DRY RUN' if args.dry_run else 'LIVE REMOVAL'}")
    print(f"  orphan seqs: {'RETAINED' if args.keep_orphan_sequences else 'REMOVED'}")
    print("=" * 78)

    store = RefgetStore.on_disk(str(sp))
    print(f"  is_persisting: {store.is_persisting}")

    before_stats = store.stats()
    before = {m.digest: m for m in enumerate_collections(store)}
    print(f"\nBEFORE: {before_stats}")
    print(f"  enumerated {len(before)} collections")

    print("\nResolving targets:")
    targets = resolve_targets(store, args.expect, args.digest)
    doomed_seqs = sum(before[d].n_sequences for _, d in targets)
    print(
        f"\n{len(targets)} collection(s) targeted, holding {doomed_seqs:,} sequence slots "
        f"(the actual store reduction will be smaller if any are shared)."
    )

    if args.dry_run:
        print("\nDRY RUN — nothing removed.")
        return

    # Must happen before any removal: see load_all_collections' docstring.
    if not args.keep_orphan_sequences:
        print()
        load_all_collections(store, list(before.values()))
        mid_stats = store.stats()
        loaded = as_int(mid_stats.get("n_collections_loaded"))
        total = as_int(mid_stats.get("n_collections"))
        print(f"  n_collections_loaded: {loaded}/{total}")
        if loaded is None or total is None or loaded < total:
            sys.exit(
                "Not every collection loaded. Orphan sequence GC would silently "
                "no-op and free nothing. Refusing to proceed."
            )

    print("\nRemoving:")
    for label, digest in targets:
        removed = store.remove_collection(
            digest, remove_orphan_sequences=not args.keep_orphan_sequences
        )
        print(f"  {'removed' if removed else 'NOT FOUND'}  {digest}  ({label})")
        if not removed:
            sys.exit(f"remove_collection returned False for {digest} — stopping.")

    after_stats = store.stats()
    after = {m.digest for m in enumerate_collections(store)}
    print(f"\nAFTER: {after_stats}")
    print(f"  enumerated {len(after)} collections")

    print("\nDIFF:")
    for key in ("n_collections", "n_sequences"):
        # stats() returns its counts as strings, not ints.
        b, a = as_int(before_stats.get(key)), as_int(after_stats.get(key))
        if b is None or a is None:
            print(f"  {key:15s} {before_stats.get(key)} -> {after_stats.get(key)}")
        else:
            print(f"  {key:15s} {b:>12,} -> {a:>12,}   ({a - b:+,})")

    still_present = [d for _, d in targets if d in after]
    if still_present:
        print("\nERROR: these digests are STILL in the store:", file=sys.stderr)
        for d in still_present:
            print(f"  - {d}", file=sys.stderr)
        sys.exit(1)

    # Surface the case where collections went away but nothing was reclaimed.
    # Two readings, and this cannot tell them apart from the counts alone:
    #   1. Legitimate — every sequence in the removed collections was shared with
    #      a survivor, so content-addressing correctly retained all of them. Seen
    #      on the `demo` store, where all collections share the same 3 sequences.
    #   2. Broken — orphan GC no-opped (see load_all_collections). The load guard
    #      above should have caught this, so treat it as a signal something else
    #      is wrong.
    # Not fatal: the store is internally consistent either way. But if you ran
    # this to reclaim space, this means you did not.
    if not args.keep_orphan_sequences:
        b, a = as_int(before_stats.get("n_sequences")), as_int(after_stats.get("n_sequences"))
        if b is not None and a is not None and a == b:
            print(
                f"\nWARNING: n_sequences did not move ({b:,}). Either every removed "
                "sequence was shared with a surviving collection (legitimate), or "
                "orphan GC did not run (broken). If you removed these to reclaim "
                "space, verify on disk before syncing to S3 — a --delete sync makes "
                "this permanent.",
                file=sys.stderr,
            )

    print("\nAll targets confirmed absent. Verify the store before syncing to S3.")


if __name__ == "__main__":
    main()
