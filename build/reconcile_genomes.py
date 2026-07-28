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
    python build/reconcile_genomes.py --db-config PATH --no-prune
    python build/reconcile_genomes.py --db-config PATH --count-genomes-only
    python build/reconcile_genomes.py --db-config PATH --check-dispatch-safe

``--count-genomes-only`` prints just the integer count of registered genomes
(for scripting). ``--check-dispatch-safe`` performs NO pruning and exits non-zero
if any PEP genome is still unregistered AND still sentinel-gated (which would
cause its build to fail with ``MissingGenomeError``); it exits 0 when every PEP
genome is either registered or will be initialized by ``genome_init`` (sentinel
absent). ``--no-prune`` runs the full reconcile REPORT -- naming every sentinel
it WOULD have removed -- while unlinking nothing; it is what ``run_builds.sh``
passes under ``DRY_RUN=1``. All three modes build the Refgenie instance the same
way ``update_index.py`` does so the ``alias_folder`` matches what the SLURM
``genome_init`` jobs write.

WHY ``--no-prune`` EXISTS (2026-07-19)
--------------------------------------
``run_builds.sh`` called this script unconditionally, ~35 lines ABOVE its
``DRY_RUN`` early-exit. So ``DRY_RUN=1 bash build/run_builds.sh`` -- the command
an operator reaches for precisely BECAUSE they believe it cannot change
anything -- reached the ``sp.unlink()`` below before it ever reached the branch
that was supposed to make the run harmless. On 2026-07-19 a dry run issued
*while investigating why sentinels were missing* destroyed hg38's and
yeast_s288c's ``.genome_init_complete``. Nothing recreates a sentinel, so the
next nightly re-ran ``genome_init`` for both and marked every downstream asset
stale. A dry run must be read-only end to end; the flag is how that is
enforced at the only place that unlinks.

READ-ONLY IS BEST-EFFORT, NOT ABSOLUTE
--------------------------------------
``--no-prune``/``--check-dispatch-safe``/``--count-genomes-only`` skip
``rg.init()`` (which mkdir -p's genome_folder + genome_stage_folder and can
insert the ``Configuration`` row) and refuse to run at all unless the catalog
SQLite file already exists -- see ``_assert_catalog_present``. That closes the
practical hole, but it is not a guarantee refgenie itself offers: the
``Refgenie(...)`` CONSTRUCTOR calls ``check_for_db_migrations()``, and when the
database has no alembic revision that helper calls ``self.init()`` on its own
and may run alembic migrations. On a truly EMPTY catalog, merely constructing
the object is therefore a write -- it creates the schema and inserts the
``Configuration`` row that permanently fixes ``genome_folder``. The existence
precondition below is what keeps a dry run from ever reaching that state; there
is no read-only Refgenie mode to ask for instead.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path


def _registry_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _catalog_sqlite_path(db_config: str | None) -> Path | None:
    """Best-effort: the SQLite file a refgenie DB config points at.

    The config is a two-key YAML (``path:`` / ``type:``) written by
    run_builds.sh. Parsed by hand so this precondition never depends on an
    import succeeding. Returns None when the path cannot be determined (unknown
    backend, unreadable file) -- callers treat None as "cannot verify", not as
    "missing".
    """
    if not db_config:
        return None
    try:
        with open(db_config) as fh:
            for line in fh:
                key, sep, value = line.partition(":")
                if sep and key.strip() == "path":
                    value = value.strip().strip("'\"")
                    return Path(value) if value else None
    except OSError:
        return None
    return None


def _assert_catalog_present(db_config: str | None) -> None:
    """Precondition for the read-only modes: the catalog must ALREADY exist.

    Constructing ``Refgenie`` against a missing/empty SQLite file is itself a
    write (see the module docstring: the constructor's
    ``check_for_db_migrations`` calls ``init()`` when there is no alembic
    revision). So a mode that promises to change nothing has to refuse BEFORE
    it builds the object rather than discover the problem afterwards.

    Failing here is also the second half of the 2026-07-08 defense: an empty
    catalog makes every PEP genome look unregistered, which is exactly the
    input that turns a reconcile into a delete-every-sentinel run.
    """
    if not db_config:
        raise SystemExit(
            "reconcile: FATAL --db-config is required in read-only mode "
            "(set REFGENIE_DB_CONFIG_PATH or pass --db-config)."
        )
    if not Path(db_config).is_file():
        raise SystemExit(f"reconcile: FATAL DB config does not exist: {db_config}")
    sqlite_path = _catalog_sqlite_path(db_config)
    if sqlite_path is not None and not sqlite_path.exists():
        raise SystemExit(
            f"reconcile: FATAL catalog SQLite file does not exist: {sqlite_path}\n"
            f"  (referenced by {db_config})\n"
            "  Refusing to continue: building a Refgenie against a missing catalog\n"
            "  CREATES it (schema + Configuration row fixing genome_folder), which a\n"
            "  read-only mode must never do -- and an empty catalog makes every PEP\n"
            "  genome look unregistered, the 2026-07-08 delete-every-sentinel input."
        )


def _build_refgenie(db_config: str | None, read_only: bool = False):
    """Construct a Refgenie instance the SAME way build/update_index.py does, so
    its alias_folder matches the folder the SLURM genome_init jobs write to.

    ``read_only=True`` skips ``rg.init()``. On a healthy catalog init() is
    already a near no-op (its mkdirs and its Configuration insert are both
    guarded by existence checks), so skipping it costs nothing and removes the
    only mutation this script performs by construction. See the module
    docstring for the residual, constructor-level mutation that no flag here
    can suppress.
    """
    from refgenie import Refgenie

    if db_config:
        rg = Refgenie(database_config_path=db_config, suppress_migrations=False)
    else:
        rg = Refgenie()
    if not read_only:
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
            # Report the exact action the pruning run WOULD have taken, so a dry
            # run is diagnostically equivalent to a real one without being
            # destructive. "would-prune" is the line an operator greps for.
            if sp.exists():
                print(
                    f"  reconcile: would-prune {name} (NOT registered; "
                    f"WOULD remove stale sentinel {sp}) [--no-prune: left intact]"
                )
            else:
                print(
                    f"  reconcile: would-init  {name} (NOT registered; no sentinel "
                    "-> genome_init would run)"
                )
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
    parser.add_argument(
        "--no-prune",
        action="store_true",
        help=(
            "Report only: name every sentinel that WOULD be pruned, but unlink "
            "nothing. Required for DRY_RUN, which must be read-only end to end."
        ),
    )
    args = parser.parse_args(argv)

    # Every mode that promises not to mutate must also decline to CREATE the
    # catalog it reads (see _assert_catalog_present).
    read_only = args.no_prune or args.check_dispatch_safe or args.count_genomes_only
    if read_only:
        _assert_catalog_present(args.db_config)

    registry_root = _registry_root()
    rg = _build_refgenie(args.db_config, read_only=read_only)

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
    if args.no_prune:
        print("reconcile: --no-prune (read-only) — nothing will be unlinked")
    unregistered = reconcile(rg, genomes, prune=not args.no_prune)

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
