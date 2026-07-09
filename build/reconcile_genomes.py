#!/usr/bin/env python3
"""Reconcile genome_init sentinels with the persistent refgenie1 build catalog.

The nightly build catalog (SQLite) is now PERSISTENT (see build/run_builds.sh),
but the ``genome_init`` sentinel files it must stay consistent with live on disk
under the persistent alias folder and can outlive the ``genome`` rows they
represent -- e.g. after the old nightly wipe, or on a fresh catalog created on a
new machine. When a sentinel exists but its genome row does NOT, snakemake skips
the ``genome_init`` rule, yet ``refgenie build <g>/fasta:default --stage`` calls
``GenomeManager.get(digest)`` against the empty ``genome`` table and dies with
``MissingGenomeError``, which aborts the whole nightly.

This helper makes the pipeline self-correcting WITHOUT hand-editing anything:
for every genome the PEP queues (``pep/samples.csv``, column ``genome_name``) it
checks whether the persistent catalog actually holds that genome as a DB row. If
NOT, it deletes the stale sentinel (computed from
``Refgenie.get_genome_init_target_template()``), forcing snakemake to re-run
``refgenie genome init ... --force`` (idempotent -- it adds the missing genome +
alias rows) BEFORE any ``build_*`` rule stages. Registered genomes keep their
sentinels so ``genome_init`` is skipped (no wasted work).

Behavior is convergent and idempotent regardless of catalog state:
  * fresh/empty catalog  -> every sentinel pruned -> every genome re-init'd
  * fully-populated one   -> sentinels kept       -> genome_init skipped

Usage::

    python build/reconcile_genomes.py [--db-config PATH]
    python build/reconcile_genomes.py --db-config PATH --count-genomes-only
    python build/reconcile_genomes.py --db-config PATH --check-dispatch-safe

``--count-genomes-only`` prints just the integer count of registered genomes
(for scripting). ``--check-dispatch-safe`` performs NO pruning and exits non-zero
if any PEP genome is still unregistered AND still sentinel-gated (which would
cause its build to fail with ``MissingGenomeError``); it exits 0 when every PEP
genome is either registered or will be initialized by ``genome_init`` (sentinel
absent). Both modes build the Refgenie instance the same way ``update_index.py``
does so the ``alias_folder`` matches what the SLURM ``genome_init`` jobs write.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path


def _registry_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _build_refgenie(db_config: str | None):
    """Construct a Refgenie instance the SAME way build/update_index.py does, so
    its alias_folder matches the folder the SLURM genome_init jobs write to."""
    from refgenie import Refgenie

    if db_config:
        rg = Refgenie(database_config_path=db_config, suppress_migrations=False)
    else:
        rg = Refgenie()
    rg.init()
    return rg


def read_pep_genomes(registry_root: Path) -> list[str]:
    """Return the de-duplicated, order-preserving list of ``genome_name`` values
    from the PEP sample table -- the same source the Snakefile fans out over."""
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


def is_registered(rg, genome_name: str) -> bool:
    """True iff ``genome_name`` resolves to a digest that has a ``genome`` DB
    row in the persistent catalog."""
    from refgenie.exceptions import RefgenieError

    try:
        digest = rg.alias.resolve(genome_name)
    except RefgenieError:
        return False
    except Exception:  # noqa: BLE001 - be defensive; unresolvable => not registered
        return False
    try:
        return bool(rg.genome.exists(digest))
    except Exception:  # noqa: BLE001
        return False


def sentinel_path(rg, genome_name: str) -> Path:
    """The genome_init sentinel path for ``genome_name`` (persistent alias folder)."""
    template = rg.get_genome_init_target_template()
    return Path(str(template).format(genome_name=genome_name))


def _catalog_counts(rg) -> dict[str, int]:
    def _count(fn) -> int:
        try:
            return len(list(fn()))
        except Exception:  # noqa: BLE001
            return -1

    return {
        "recipe": _count(rg.recipe.list_all),
        "asset_class": _count(rg.asset_class.list_all),
        "genome": _count(rg.genome.list_all),
        "alias": _count(rg.alias.list_all),
    }


def reconcile(rg, genomes: list[str], prune: bool = True) -> list[str]:
    """For each genome, decide keep vs. prune. When ``prune`` is True, delete the
    stale sentinel of any unregistered genome so genome_init re-runs. Returns the
    list of genomes that are NOT registered (regardless of ``prune``)."""
    unregistered: list[str] = []
    for name in genomes:
        registered = is_registered(rg, name)
        sp = sentinel_path(rg, name)
        if registered:
            print(f"  reconcile: keep   {name} (registered in catalog)")
            continue
        unregistered.append(name)
        if not prune:
            state = "present" if sp.exists() else "absent"
            print(f"  reconcile: check  {name} (NOT registered; sentinel {state})")
            continue
        if sp.exists():
            try:
                sp.unlink()
                print(f"  reconcile: prune  {name} (NOT registered; removed stale sentinel {sp})")
            except OSError as exc:
                print(f"  reconcile: WARN   {name}: could not remove sentinel {sp}: {exc}")
        else:
            print(f"  reconcile: init   {name} (NOT registered; no sentinel -> genome_init will run)")
    return unregistered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-config", default=os.environ.get("REFGENIE_DB_CONFIG_PATH"))
    parser.add_argument(
        "--count-genomes-only",
        action="store_true",
        help="Print only the integer count of registered genomes and exit.",
    )
    parser.add_argument(
        "--check-dispatch-safe",
        action="store_true",
        help=(
            "Do not prune. Exit non-zero if any PEP genome is unregistered AND "
            "still sentinel-gated (its build would fail with MissingGenomeError)."
        ),
    )
    args = parser.parse_args(argv)

    registry_root = _registry_root()
    rg = _build_refgenie(args.db_config)

    if args.count_genomes_only:
        try:
            print(len(list(rg.genome.list_all())))
        except Exception:  # noqa: BLE001
            print(0)
        return 0

    genomes = read_pep_genomes(registry_root)

    if args.check_dispatch_safe:
        # Read-only safety check. A genome is dispatch-safe iff registered OR its
        # sentinel is absent (genome_init will run and register it).
        doomed: list[str] = []
        for name in genomes:
            if is_registered(rg, name):
                continue
            if sentinel_path(rg, name).exists():
                doomed.append(name)
        if doomed:
            print(
                "reconcile: DISPATCH UNSAFE — unregistered + sentinel-gated genomes: "
                + ", ".join(doomed)
            )
            return 1
        print("reconcile: dispatch-safe (every PEP genome is registered or will be initialized)")
        return 0

    print(f"reconcile: PEP queues {len(genomes)} genome(s): {', '.join(genomes) or '(none)'}")
    unregistered = reconcile(rg, genomes, prune=True)

    counts = _catalog_counts(rg)
    print(
        "reconcile: catalog counts — "
        f"recipe={counts['recipe']}, asset_class={counts['asset_class']}, "
        f"genome={counts['genome']}, alias={counts['alias']}"
    )
    if unregistered:
        print(
            f"reconcile: {len(unregistered)} genome(s) will be (re)initialized by genome_init: "
            + ", ".join(unregistered)
        )
    else:
        print("reconcile: all PEP genomes already registered in the persistent catalog")
    return 0


if __name__ == "__main__":
    sys.exit(main())
