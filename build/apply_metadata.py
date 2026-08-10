#!/usr/bin/env python3
"""Apply per-genome FHR metadata to the persistent build catalog + store sidecars.

The sentinel-independent metadata update step of the nightly Rivanna pipeline. It
is the LOAD-BEARING half of the metadata bridge: the genome_init rule carries
metadata for a genome only the night it is first built (its sentinel then makes
snakemake skip the rule forever), so a description edited in a genomes/*.yaml long
after the genome was registered would never reach the catalog without this step.

For every genome the PEP queues (pep/samples.csv, column ``genome_name``) it reads
the generated pep/metadata/<genome_name>.fhr.json and calls
``GenomeManager.apply_fhr(digest, record)`` -- the SAME helper ``genome init --fhr``
and ``genome set-metadata --fhr`` funnel through -- which upserts the genome row's
description/species_name columns and (re)writes the RefgetStore FHR sidecar. The
sidecars this writes are picked up by run_builds.sh's ``aws s3 sync .refget_store``;
the columns ride the subsequent ``catalog-export``.

Idempotent and convergent by construction (apply_fhr is a pure upsert of the desired
state), so:
  * a brand-new genome, whose row genome_init just created, gets its metadata here;
  * a metadata-only YAML edit propagates on the next nightly with NO rebuild and no
    sentinel change; and
  * re-running over an already-correct catalog is a no-op.

Metadata is NON-BLOCKING: a genome that is not yet registered (its build failed) or
whose sidecar is missing is a WARNING, never a fatal error -- a build must never fail
because a description could not be applied.

Usage::

    python build/apply_metadata.py [--db-config PATH]
    python build/apply_metadata.py --db-config PATH --dry-run

``--dry-run`` is report-only: it resolves what it WOULD apply and writes nothing
(what run_builds.sh would use under DRY_RUN). It builds the Refgenie instance the
same way build/reconcile_genomes.py does so the store/alias folders match the SLURM
genome_init jobs.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path


def _registry_root() -> Path:
    return Path(__file__).resolve().parent.parent


def read_pep_genomes(registry_root: Path) -> list[str]:
    """De-duplicated, order-preserving ``genome_name`` values from pep/samples.csv."""
    samples = registry_root / "pep" / "samples.csv"
    genomes: list[str] = []
    seen: set[str] = set()
    with open(samples, newline="") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("genome_name") or "").strip()
            if name and name not in seen:
                seen.add(name)
                genomes.append(name)
    return genomes


def _build_refgenie(db_config: str | None):
    """Construct a Refgenie the SAME way build/reconcile_genomes.py does.

    No ``rg.init()``: applying metadata needs only the engine and the store, both
    of which read the persistent catalog's Configuration row (genome_folder etc.),
    so init's mkdirs/Configuration-insert are unnecessary here.
    """
    from refgenie import Refgenie

    if db_config:
        return Refgenie(database_config_path=db_config, suppress_migrations=False)
    return Refgenie()


def _resolve_digest(rg, genome_name: str) -> str | None:
    """Digest for ``genome_name`` if it is a registered genome, else None."""
    from refgenie.exceptions import RefgenieError

    try:
        digest = rg.alias.resolve(genome_name)
    except RefgenieError:
        return None
    except Exception:  # noqa: BLE001 - unresolvable reads as not-registered
        return None
    try:
        return digest if rg.genome.exists(digest) else None
    except Exception:  # noqa: BLE001
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-config", default=os.environ.get("REFGENIE_DB_CONFIG_PATH"))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report only: resolve what WOULD be applied, write nothing.",
    )
    args = parser.parse_args(argv)

    registry_root = _registry_root()
    metadata_dir = registry_root / "pep" / "metadata"

    if not args.db_config:
        print(
            "apply_metadata: FATAL --db-config is required "
            "(set REFGENIE_DB_CONFIG_PATH or pass --db-config).",
            file=sys.stderr,
        )
        return 1
    if not Path(args.db_config).is_file():
        print(f"apply_metadata: FATAL DB config does not exist: {args.db_config}", file=sys.stderr)
        return 1

    genomes = read_pep_genomes(registry_root)
    print(f"apply_metadata: PEP queues {len(genomes)} genome(s)")
    if args.dry_run:
        print("apply_metadata: --dry-run (report-only) — nothing will be written")

    rg = _build_refgenie(args.db_config)

    applied = 0
    skipped = 0
    failures = 0
    for name in genomes:
        digest = _resolve_digest(rg, name)
        if digest is None:
            print(f"  apply_metadata: skip {name} (not registered in catalog yet)")
            skipped += 1
            continue
        sidecar = metadata_dir / f"{name}.fhr.json"
        if not sidecar.is_file():
            print(f"  apply_metadata: skip {name} (no sidecar {sidecar})")
            skipped += 1
            continue
        if args.dry_run:
            print(f"  apply_metadata: would-apply {name} ({digest}) <- {sidecar.name}")
            continue
        try:
            with open(sidecar) as fh:
                record = json.load(fh)
            rg.genome.apply_fhr(digest, record)
            print(f"  apply_metadata: applied {name} ({digest})")
            applied += 1
        except Exception as exc:  # noqa: BLE001 - metadata is non-blocking
            print(f"  apply_metadata: WARN {name}: could not apply metadata: {exc}", file=sys.stderr)
            failures += 1

    print(
        f"apply_metadata: done — applied={applied}, skipped={skipped}, failed={failures}"
        + (" (dry-run)" if args.dry_run else "")
    )
    # Non-zero only on real apply errors; run_builds.sh treats this step as
    # non-fatal, so this is signal for the log, not a build-aborting condition.
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
