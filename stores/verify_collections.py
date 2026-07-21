#!/usr/bin/env python3
"""Assert that named collections in a store are byte-exact.

Used as the gate after removing collections from a content-addressed store. The
danger of orphan-sequence GC is that it reclaims a sequence still referenced by a
collection that is supposed to survive. Collection counts cannot detect that --
only re-reading a survivor and re-totalling its bases can.

Each --expect is `<alias-or-digest>=<n_sequences>:<total_bp>`. Both numbers must
match exactly; any drift means the removal took something it should not have.

Usage:
    source ../infra/rivanna/env.sh
    python verify_collections.py plantref \
        --expect triticum_aestivum_IWGSC1_1=22:14547261565 \
        --expect hordeum_vulgare_IBC_PGSB_v2=10:4834432680

Requires: $REFGETSTORE_BASE set; refget/gtars installed.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from refget.store import RefgetStore

ALIAS_NAMESPACE = "name"


def store_path(store_name: str) -> Path:
    base = os.environ.get("REFGETSTORE_BASE")
    if not base:
        sys.exit("REFGETSTORE_BASE not set. Source env.sh first.")
    return Path(base) / store_name


def resolve(store: RefgetStore, ident: str) -> str | None:
    """Resolve an alias to a digest, or pass a bare digest through."""
    meta = store.get_collection_metadata_by_alias(ALIAS_NAMESPACE, ident)
    if meta is not None:
        return meta.digest
    # Might already be a digest.
    try:
        if store.get_collection(ident) is not None:
            return ident
    except Exception:
        pass
    return None


def main():
    parser = argparse.ArgumentParser(description="Verify collections are byte-exact.")
    parser.add_argument("store", help="Store name (e.g. plantref)")
    parser.add_argument(
        "--expect", action="append", default=[], required=True,
        metavar="IDENT=NSEQ:TOTALBP",
        help="Collection and its exact expected sequence count and base total. Repeatable.",
    )
    args = parser.parse_args()

    sp = store_path(args.store)
    if not sp.exists():
        sys.exit(f"Store not found on disk: {sp}")

    print(f"VERIFY collections in '{args.store}' at {sp}")
    store = RefgetStore.on_disk(str(sp))
    print(f"  stats: {store.stats()}\n")

    failures = []
    for spec in args.expect:
        try:
            ident, nums = spec.split("=", 1)
            n_exp_s, bp_exp_s = nums.split(":", 1)
            n_exp, bp_exp = int(n_exp_s), int(bp_exp_s)
        except ValueError:
            sys.exit(f"Bad --expect (want IDENT=NSEQ:TOTALBP): {spec!r}")

        digest = resolve(store, ident)
        if digest is None:
            print(f"  FAIL {ident}: does not resolve")
            failures.append(ident)
            continue

        coll = store.get_collection(digest)
        n_got = len(coll.sequences)
        # SequenceRecord exposes only `metadata` and `sequence`; the length lives
        # on the nested SequenceMetadata, NOT as a top-level `.length`.
        bp_got = sum(s.metadata.length for s in coll.sequences)

        ok = (n_got == n_exp) and (bp_got == bp_exp)
        print(f"  {'OK  ' if ok else 'FAIL'} {ident} ({digest})")
        print(f"       sequences {n_got:>10,}  expected {n_exp:>10,}")
        print(f"       bases     {bp_got:>16,}  expected {bp_exp:>16,}")
        if not ok:
            failures.append(ident)

    print()
    if failures:
        print(f"VERIFICATION FAILED for {len(failures)}: {', '.join(failures)}", file=sys.stderr)
        print("Do NOT sync to S3. Restore from S3 and investigate.", file=sys.stderr)
        sys.exit(1)
    print(f"All {len(args.expect)} collections verified byte-exact.")


if __name__ == "__main__":
    main()
