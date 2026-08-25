#!/usr/bin/env python3
"""Generate pep/metadata/<genome_name>.fhr.json from genomes/**/*.yaml.

The per-genome FHR sidecars are a GENERATED artifact -- the metadata companion to
pep/samples.csv. build/run_builds.sh regenerates them and fails the nightly on any
drift, exactly like samples.csv, so the committed pep/metadata/ tree is the go/no-go
metadata gate. Editing these files by hand is forbidden; edit the source
genomes/*/*.yaml and regenerate.

One file is written per genome YAML -- EVERY genome, not just the ones the PEP
queues. Most of the corpus sits at ``build.tier: store_only``: no PEP row, no
assets, sequence loaded into a store. The sidecar is exactly what gives such a
genome its organism and taxonomy in that store, so scoping this generator to the
build queue is what left hundreds of collections federating in with null species.

Each record is normalized to the FHR shape by the single mapping module
tools/genome_to_fhr.py, so this generator and the store loader agree on the
mapping. Queued genomes are a subset, so the derived PEP ``fhr_file_path``
attribute and the post-build apply step always have their file. A queued genome
with no YAML at all gets a minimal ``{"name": <genome_name>}`` record and a
WARNING: metadata is non-blocking, and a build must never fail because a
description is absent.

Deterministic key order (tools/genome_to_fhr emits a fixed field order; this writer
does not sort) so the committed diff is stable.

Usage:
    python build/generate_genome_metadata.py            # write pep/metadata/*.fhr.json
    python build/generate_genome_metadata.py --check     # verify only, non-zero on drift
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
GENOMES_DIR = os.path.join(REPO, "genomes")
SAMPLES_CSV = os.path.join(REPO, "pep", "samples.csv")
METADATA_DIR = os.path.join(REPO, "pep", "metadata")

# The FHR normalizer is the SINGLE source of truth for the YAML -> FHR mapping
# (tools/genome_to_fhr.py). Import it so this generator can never disagree with
# the validator's export self-check or the store loader.
sys.path.insert(0, os.path.join(REPO, "tools"))
from genome_to_fhr import genome_yaml_to_fhr  # noqa: E402

SIDECAR_SUFFIX = ".fhr.json"


def read_pep_genomes(samples_csv: str = SAMPLES_CSV) -> list[str]:
    """De-duplicated, order-preserving ``genome_name`` values from the PEP table.

    The same source (and the same read) reconcile_genomes.py uses, so the metadata
    set and the build queue can never fall out of step.
    """
    genomes: list[str] = []
    seen: set[str] = set()
    with open(samples_csv, newline="") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("genome_name") or "").strip()
            if name and name not in seen:
                seen.add(name)
                genomes.append(name)
    return genomes


def index_yaml_records(genomes_dir: str = GENOMES_DIR) -> dict[str, dict]:
    """Map every genome YAML's ``name:`` field to its parsed record.

    Keyed by the in-file ``name``, not the filename -- the two can differ, and the
    build queue addresses genomes by ``name``.
    """
    records: dict[str, dict] = {}
    for path in sorted(glob.glob(os.path.join(genomes_dir, "**", "*.yaml"), recursive=True)):
        with open(path) as fh:
            data = yaml.safe_load(fh)
        if isinstance(data, dict) and data.get("name"):
            records[data["name"]] = data
    return records


def _render(record: dict) -> str:
    """Serialize an FHR record to stable JSON text (no key sorting)."""
    return json.dumps(record, indent=2) + "\n"


def generate(
    samples_csv: str = SAMPLES_CSV,
    genomes_dir: str = GENOMES_DIR,
) -> tuple[dict[str, str], list[str]]:
    """Return ({genome_name: json_text}, [genomes with no YAML]).

    Every genome YAML gets a sidecar, plus a minimal record for any queued genome
    that has no YAML. The missing list is advisory (WARNING), never fatal.

    Sorted by genome name so the written set -- and therefore the committed diff
    and the drift check -- never depends on directory walk order.
    """
    records = index_yaml_records(genomes_dir)
    out: dict[str, str] = {}
    for genome in sorted(records):
        out[genome] = _render(genome_yaml_to_fhr(records[genome]))

    missing: list[str] = []
    for genome in read_pep_genomes(samples_csv):
        if genome not in out:
            missing.append(genome)
            out[genome] = _render({"name": genome})
    return out, missing


def _existing_sidecars(metadata_dir: str) -> set[str]:
    if not os.path.isdir(metadata_dir):
        return set()
    return {
        os.path.basename(p) for p in glob.glob(os.path.join(metadata_dir, "*" + SIDECAR_SUFFIX))
    }


def write(desired: dict[str, str], metadata_dir: str = METADATA_DIR) -> None:
    """Write the desired sidecars and prune any stale ones, so the tree matches the queue.

    Writes are IDEMPOTENT: a sidecar whose on-disk content already matches is left
    untouched, mtime and all. This matters because the nightly regenerates every
    sidecar on every run -- rewriting unchanged files would bump every mtime a
    night, and the Rivanna profile drives snakemake from mtime alone
    (rerun-triggers: mtime), so that churn would re-trigger every genome's build.
    """
    os.makedirs(metadata_dir, exist_ok=True)
    wanted = {f"{g}{SIDECAR_SUFFIX}" for g in desired}
    for stale in _existing_sidecars(metadata_dir) - wanted:
        os.remove(os.path.join(metadata_dir, stale))
        print(f"generate_genome_metadata: removed stale {stale}")
    for genome, text in desired.items():
        path = os.path.join(metadata_dir, f"{genome}{SIDECAR_SUFFIX}")
        if os.path.isfile(path):
            with open(path) as fh:
                if fh.read() == text:
                    continue
        with open(path, "w") as fh:
            fh.write(text)


def check(desired: dict[str, str], metadata_dir: str = METADATA_DIR) -> list[str]:
    """Return a list of drift problems; empty means the on-disk tree is up to date."""
    problems: list[str] = []
    wanted = {f"{g}{SIDECAR_SUFFIX}": text for g, text in desired.items()}
    for filename, text in wanted.items():
        path = os.path.join(metadata_dir, filename)
        if not os.path.isfile(path):
            problems.append(f"missing {filename}")
            continue
        with open(path) as fh:
            if fh.read() != text:
                problems.append(f"stale {filename}")
    for extra in _existing_sidecars(metadata_dir) - set(wanted):
        problems.append(f"extra {extra} (no genomes/**/*.yaml defines it)")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify pep/metadata/ is up to date; exit non-zero on drift without writing.",
    )
    args = parser.parse_args(argv)

    desired, missing = generate()
    for genome in missing:
        print(
            f"generate_genome_metadata: WARNING queued genome '{genome}' has no "
            f"genomes/**/*.yaml; "
            f"wrote a minimal record (metadata is non-blocking).",
            file=sys.stderr,
        )

    if args.check:
        problems = check(desired)
        if problems:
            print(
                "generate_genome_metadata: pep/metadata/ is STALE; regenerate with "
                "`python build/generate_genome_metadata.py`:",
                file=sys.stderr,
            )
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            return 1
        print(f"generate_genome_metadata: pep/metadata/ is up to date ({len(desired)} genomes).")
        return 0

    write(desired)
    print(f"generate_genome_metadata: wrote {len(desired)} sidecar(s) to pep/metadata/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
