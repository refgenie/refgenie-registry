#!/usr/bin/env python3
"""Report which (genome, asset_group) requests in the PEP did not get built.

Motivation
----------
On 2026-07-23 the nightly widened the build queue from 3 genomes to 7. Six of
the 42 requested assets -- every athaliana asset -- simply did not exist when the
run finished. The run still pushed 21 asset modes, still refreshed ``index/``,
still committed and pushed to the registry, and nothing anywhere named the gap.
The only signal was the process exit code, which says *that* something failed but
never *what is missing*.

That is the failure mode this closes. ``snakemake --keep-going`` is deliberate --
one broken recipe must not abort the batch -- but its consequence is that a run
can be substantially incomplete and still look like a normal night's work in the
log. Every new genome or recipe added to the registry widens the queue and widens
that blind spot, so the check has to be derived from the PEP rather than from a
hardcoded expectation.

What it compares
----------------
Requests come from ``pep/samples.csv`` -- one row per (genome_name,
asset_group_name). Reality comes from the persistent catalog: an ``assetgroup``
row for that genome digest and name means the asset was built and registered.

Genome names are resolved through the alias manager, so a genome whose alias does
not resolve is reported as entirely missing rather than crashing the check -- that
is exactly the athaliana state, and the report should describe it, not die on it.

Usage
-----
    python build/check_coverage.py --db-config PATH            # report, exit 0
    python build/check_coverage.py --db-config PATH --strict   # exit 1 if any gap

``--strict`` is for when you want an incomplete run to be loud. The default is a
report, so this can be run after a partial build without masking the real
build/push exit codes that the caller re-raises.
"""

# Matches reconcile_genomes.py: run_builds.sh invokes these with a bare `python3`,
# which on a Rivanna login node is 3.6, so PEP 585/604 annotations must stay lazy.
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path


def registry_root() -> Path:
    return Path(__file__).resolve().parent.parent


def read_pep_requests(root: Path) -> list[tuple[str, str]]:
    """Return the de-duplicated (genome_name, asset_group_name) pairs the PEP asks for."""
    samples = root / "pep" / "samples.csv"
    seen: set[tuple[str, str]] = set()
    requests: list[tuple[str, str]] = []
    with open(samples, newline="") as handle:
        for row in csv.DictReader(handle):
            genome = (row.get("genome_name") or "").strip()
            group = (row.get("asset_group_name") or "").strip()
            if not genome or not group:
                continue
            if (genome, group) not in seen:
                seen.add((genome, group))
                requests.append((genome, group))
    return requests


def build_refgenie(db_config: str | None):
    from refgenie import Refgenie

    if db_config:
        os.environ["REFGENIE_DB_CONFIG_PATH"] = db_config
    return Refgenie()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-config", default=os.environ.get("REFGENIE_DB_CONFIG_PATH"))
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if any requested asset is missing (default: report and exit 0).",
    )
    args = parser.parse_args(argv)

    root = registry_root()
    requests = read_pep_requests(root)
    rg = build_refgenie(args.db_config)

    # Resolve each genome once. An unresolvable alias is not an error here: it
    # means the genome never registered, and every asset it requested is missing.
    digests: dict[str, str | None] = {}
    for genome, _ in requests:
        if genome in digests:
            continue
        try:
            digests[genome] = rg.alias.resolve(genome)
        except Exception:  # noqa: BLE001 - unresolvable reads as "not registered"
            digests[genome] = None

    # asset.list_all returns ({digest: ["<asset_group>:<asset_name>", ...]}, {digest: alias}).
    # Only the asset_group half is compared: the PEP requests groups, and the
    # asset NAME is resolved from the building tool's version, so pinning it here
    # would make the gate fail on every legitimate toolchain bump.
    built: set[tuple[str, str]] = set()
    for genome, digest in digests.items():
        if digest is None:
            continue
        try:
            assets, _aliases = rg.asset.list_all(genome_digests=[digest])
        except Exception as exc:  # noqa: BLE001
            print(f"  WARNING: could not list assets for {genome}: {exc}")
            continue
        for entry in assets.get(digest, []):
            built.add((genome, entry.split(":", 1)[0]))

    missing = [(g, a) for (g, a) in requests if (g, a) not in built]

    print(f"coverage: {len(requests) - len(missing)}/{len(requests)} requested assets built")
    unresolved = sorted(n for n, d in digests.items() if d is None)
    if unresolved:
        print(f"coverage: {len(unresolved)} genome(s) do NOT resolve: {', '.join(unresolved)}")
        print("  A genome that does not resolve has no usable alias, so every build")
        print("  for it fails with 'Genome not found'. Check genome_init.")
    if missing:
        by_genome: dict[str, list[str]] = {}
        for genome, group in missing:
            by_genome.setdefault(genome, []).append(group)
        print(f"coverage: {len(missing)} requested asset(s) MISSING:")
        for genome in sorted(by_genome):
            print(f"  {genome}: {', '.join(sorted(by_genome[genome]))}")
    else:
        print("coverage: no gaps — every asset the PEP requests is registered.")

    if missing and args.strict:
        print(
            "coverage: FAILING (--strict). The run is incomplete; the assets above were "
            "requested by pep/samples.csv but never registered in the catalog.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
