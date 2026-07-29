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


def preview_orphan_removal(store: RefgetStore, targets: list) -> int:
    """Report how many sequences the orphan GC will reclaim, without removing.

    This used to be a `load_all_collections()` force-load, and the reason is
    worth remembering. gtars <= 0.9.1 derived the "still referenced" set from
    `name_lookup`, which a freshly reopened on-disk store does not populate --
    collections come back as stubs. So orphan GC either silently no-opped or, on
    a partially-loaded store, deleted sequences that surviving collections still
    needed. The force-load was a workaround: correctness by convention, enforced
    from the caller, in a script that is not the only writer.

    gtars now derives the live set from disk (reading each collections/<digest>
    .rgsi under the store write lock) and fails closed if any of them is
    unreadable, so no force-load is needed and none of this depends on which
    loading path the caller happened to take.

    `plan_orphan_removal` runs the same computation the real removal runs, so if
    the store is in a state where GC is unsafe this raises HERE, before anything
    has been removed.

    ADVISORY, though. It takes no lock -- deliberately, since blocking every
    concurrent build for the length of a full-store scan (about a minute on
    plantref) to answer a question the operator may decline would be absurd. The
    authoritative scan runs again inside remove_collection() under the lock, so
    if another writer commits a collection in between, a sequence counted here is
    live by then and the real removal correctly spares it. Expect the counts to
    match; a shortfall is ordinary concurrency, not corruption.
    """
    total = 0
    for label, digest in targets:
        doomed = store.plan_orphan_removal(digest)
        total += len(doomed)
        print(f"  {len(doomed):>10,} orphan sequences from {digest}  ({label})")
    return total


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

    predicted_orphans = 0
    if not args.keep_orphan_sequences:
        print("\nOrphan GC preview:")
        predicted_orphans = preview_orphan_removal(store, targets)
        print(f"  {predicted_orphans:>10,} total")

    # Hold the store write lock across ALL the removals, so a multi-target prune
    # is atomic with respect to other writers: no concurrent build can land
    # between target 1 and target 2 and see a half-pruned store.
    #
    # Each remove_collection() also takes (or re-enters) this lock and re-derives
    # its own live set under it, so the safety of the orphan GC does not depend
    # on this batch lock -- it only makes the batch as a whole atomic.
    store.lock_for_batch("remove_collections")
    try:
        print("\nRemoving:")
        for label, digest in targets:
            removed = store.remove_collection(
                digest, remove_orphan_sequences=not args.keep_orphan_sequences
            )
            print(f"  {'removed' if removed else 'NOT FOUND'}  {digest}  ({label})")
            if not removed:
                sys.exit(f"remove_collection returned False for {digest} — stopping.")
    finally:
        store.release_batch_lock()

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

    # Cross-check the outcome against the dry run. The two are computed the same
    # way but at different times, and the preview holds no lock, so they are not
    # required to agree:
    #
    #   reclaimed < predicted  -- a concurrent build committed a collection that
    #     references one of the planned orphans. The removal re-derived the live
    #     set under the lock and correctly spared it. Expected, not alarming.
    #   reclaimed > predicted  -- nothing legitimate produces this. Something was
    #     deleted that the plan did not account for; check the store before
    #     syncing.
    #
    # A predicted count of zero is legitimate and common: every sequence in the
    # removed collections was shared with a survivor, so content-addressing
    # correctly retained all of them. That is the `demo` store, where all
    # collections share the same 3 sequences.
    if not args.keep_orphan_sequences:
        b, a = as_int(before_stats.get("n_sequences")), as_int(after_stats.get("n_sequences"))
        if b is not None and a is not None:
            actual = b - a
            if actual > predicted_orphans:
                print(
                    f"\nWARNING: orphan GC reclaimed {actual:,} sequences but the dry run "
                    f"predicted only {predicted_orphans:,}. Nothing normal reclaims MORE "
                    "than planned. Verify the store on disk before syncing to S3 — a "
                    "--delete sync makes this permanent.",
                    file=sys.stderr,
                )
            elif actual < predicted_orphans:
                print(
                    f"\nNote: reclaimed {actual:,} of the {predicted_orphans:,} predicted "
                    "orphans. A collection committed by another writer between the preview "
                    "and the removal keeps its sequences live; the removal re-checked under "
                    "the lock and spared them."
                )
            elif predicted_orphans == 0:
                print(
                    f"\nn_sequences unchanged ({b:,}), as predicted: every removed "
                    "sequence is shared with a surviving collection."
                )

    print("\nAll targets confirmed absent. Verify the store before syncing to S3.")


if __name__ == "__main__":
    main()
