#!/usr/bin/env python3
"""Post-build FHR metadata registration for refgenie-registry RefgetStores.

This is a *post-build* step, sibling to ``build_aliases.py``: it runs after
``build.py`` has ingested a store's FASTAs and registered collection aliases.
It does NOT re-ingest anything. Each ``sources.csv`` row is resolved to its
already-built collection digest (via the collection aliases build.py wrote)
and given an FHR (FAIR Headers Reference genome) sidecar --
``fhr/<digest>.fhr.json``, built from the row's columns:

    genome         <- organism            (scientific name)
    commonName     <- derived from organism (known organisms only; the script
                                            refuses to guess and fails loudly)
    taxon          <- {name: organism, uri: identifiers.org taxonomy URI}
    documentation  <- one deterministic sentence from name/genome_assembly/source
    assemblySource <- source
    accessionID    <- {name: accession}   (only when the column is non-empty)
    assemblyLevel  <- static per-accession map (ASSEMBLY_LEVELS, from NCBI
                      Datasets; only for accession-backed records, never guessed)

Curated registry records (the PRIMARY source)
---------------------------------------------
A row whose collection has a ``genomes/**/*.yaml`` record in this repo, matched
on the row's ``name`` or ``accession``, against genomes whose ``build.store`` is
this store, takes its FHR fields from that genome's CURATED sidecar,
``pep/metadata/<genome>.fhr.json``, rather than from the CSV columns. That
sidecar is generated from the genome YAML by ``build/generate_genome_metadata.py``
through the one mapping module (``tools/genome_to_fhr.py``), so the store's
published metadata and the registry's catalog metadata are the same record.

This is what lets a store of non-model organisms carry real metadata: the CSV
derivation below can only annotate organisms listed in ``ORGANISMS``, and the
``vgp`` store holds 605 vertebrate species. Rows WITHOUT a curated record still
go through the CSV derivation and are still validated against ``ORGANISMS``.

Per-genome YAML overrides, "use YAML if it exists, use CSV otherwise":
``stores/<store>/genomes/<row_name>.yaml`` (override with ``--overrides``) is a
flat camelCase FHR-field mapping merged OVER the CSV-derived fields for that
row, field by field: a field present in the YAML wins wholesale (including
nested values like ``accessionID`` or list values like ``relatedLink``); fields
absent from the YAML keep their CSV derivation. Multi-row semantics: an
override applies to the row it is named for, and the merged fields then flow
through the normal pipeline, the last row sharing a digest still wins, the
accession carry-forward still applies, and the post-dedup assemblyLevel is
only filled in when the winning record does not already carry one (so an
override's explicit assemblyLevel beats the ASSEMBLY_LEVELS map). Overrides on
digest-losing rows are legal (used as reorder-safety mirrors) but inert.

These are exactly the fields refgenie's ``GenomeManager.apply_fhr`` consumes,
so a store with these sidecars gives `refgenie genome sync` overlay genomes
their description, species/common-name/taxon, and assembly
source/accession/level columns.

Idempotent: re-running overwrites sidecars with identical content. After
writing, the store manifest (rgstore.json) is re-committed so its
``fhr_digest`` advertises the sidecars, required for ``pull_fhr`` on remote
opens. Sync the ``fhr/`` dir and ``rgstore.json`` to S3 afterward.

Usage:
    source ../infra/rivanna/env.sh
    python build_fhr.py jungle
    python build_fhr.py jungle --dry-run
    python build_fhr.py jungle --store-path /tmp/test_store --sources /tmp/s.csv
    python build_fhr.py jungle --overrides /path/to/genomes_dir
    python build_fhr.py vgp --no-registry     # CSV derivation only

Requirements: refget + gtars (RefgetStore) and pyyaml; Python stdlib otherwise.
"""

import argparse
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
REPO = SCRIPT_DIR.parent
GENOMES_DIR = REPO / "genomes"
REGISTRY_METADATA_DIR = REPO / "pep" / "metadata"
sys.path.insert(0, str(SCRIPT_DIR))  # for build_aliases/store_config imports

from build_aliases import read_sources, resolve_collection_digest

# The organisms this script knows how to annotate. Anything else in the
# organism column is a hard error: guessing a common name or taxon id would
# write wrong metadata that then propagates into every refgenie catalog.
ORGANISMS = {
    "Homo sapiens": ("human", 9606),
    "Mus musculus": ("mouse", 10090),
}

# NCBI assembly level per accession, lowercased to match the registry's
# committed pep/metadata/*.fhr.json convention ("chromosome"). Static so this
# script makes no network calls at run time; values retrieved 2026-08-18 from
# the NCBI Datasets API (https://api.ncbi.nlm.nih.gov/datasets/v2/genome/
# accession/<acc>/dataset_report?filters.assembly_version=all_assemblies,
# field assembly_info.assembly_level). An accession absent here simply gets no
# assemblyLevel, never guess; extend the map when sources.csv gains one.
ASSEMBLY_LEVELS = {
    "GCA_000001405.14": "chromosome",  # GRCh37.p13
    "GCA_000001405.15": "chromosome",  # GRCh38
    "GCF_000001405.25": "chromosome",  # GRCh37.p13
    "GCF_000001405.26": "chromosome",  # GRCh38
    "GCF_000001405.27": "chromosome",  # GRCh38.p1
    "GCF_000001405.28": "chromosome",  # GRCh38.p2
    "GCF_000001405.32": "chromosome",  # GRCh38.p6
    "GCF_000001405.33": "chromosome",  # GRCh38.p7
    "GCF_000001405.34": "chromosome",  # GRCh38.p8
    "GCF_000001405.38": "chromosome",  # GRCh38.p12
    "GCF_000001405.39": "chromosome",  # GRCh38.p13
    "GCF_000001405.40": "chromosome",  # GRCh38.p14
    "GCA_000001635.8": "chromosome",  # GRCm38.p6
    "GCA_000001635.9": "chromosome",  # GRCm39
    "GCF_000001635.26": "chromosome",  # GRCm38.p6
    "GCF_000001635.27": "chromosome",  # GRCm39
}


def documentation_for_row(row):
    """A deterministic one-sentence description from sources.csv columns only."""
    organism = (row.get("organism") or "").strip()
    name = (row.get("name") or "").strip()
    assembly = (row.get("genome_assembly") or "").strip()
    source = (row.get("source") or "").strip()
    sentence = f"{organism} genome {name}"
    if assembly:
        sentence += f" ({assembly})"
    if source:
        sentence += f", distributed by {source}"
    return sentence + "."


def fhr_fields_for_row(row):
    """Build the camelCase FHR dict for one sources.csv row."""
    organism = (row.get("organism") or "").strip()
    common_name, taxon_id = ORGANISMS[organism]
    fields = {
        "genome": organism,
        "commonName": common_name,
        "taxon": {
            "name": organism,
            "uri": f"https://identifiers.org/taxonomy:{taxon_id}",
        },
        "documentation": documentation_for_row(row),
    }
    source = (row.get("source") or "").strip()
    if source:
        fields["assemblySource"] = source
    accession = (row.get("accession") or "").strip()
    if accession:
        fields["accessionID"] = {"name": accession}
    return fields


# pep/config.yaml and every store's `fasta_root:` root their paths at the same
# env var, written either way. Normalizing lets one be compared to the other
# textually, without needing the var to be set.
_FASTA_VAR = ("${REFGETSTORE_FASTA}", "$REFGETSTORE_FASTA")


def _normalize_root(path):
    for form in _FASTA_VAR:
        if path.startswith(form):
            return "@FASTA@" + path[len(form):]
    return path


def _staged_fasta_paths(pep_config=None):
    """{genome: staged FASTA path} from pep/config.yaml's `<genome>_fa` sources."""
    import yaml

    pep_config = Path(pep_config or (REPO / "pep" / "config.yaml"))
    if not pep_config.is_file():
        return {}
    with open(pep_config) as f:
        cfg = yaml.safe_load(f) or {}
    sources = ((cfg.get("sample_modifiers") or {}).get("derive") or {}).get("sources") or {}
    return {
        key[: -len("_fa")]: _normalize_root(str(value))
        for key, value in sources.items()
        if key.endswith("_fa")
    }


def _store_fasta_root_token(store):
    """The store's `fasta_root:`, normalized to compare with a staged path."""
    from store_config import load_pep

    root = load_pep(SCRIPT_DIR / store).get("fasta_root")
    return _normalize_root(str(root)).rstrip("/") if root else None


def _slug(name):
    """The genome-name form of a sources.csv name (see tools/sources_to_genomes.py)."""
    import re

    slug = re.sub(r"\s+", "_", (name or "").strip())
    slug = re.sub(r"[^A-Za-z0-9_.-]", "", slug)
    return re.sub(r"_+", "_", slug).strip("._-")


def load_registry_records(store, genomes_dir=GENOMES_DIR, metadata_dir=REGISTRY_METADATA_DIR):
    """Curated FHR records for the genomes this store holds.

    Returns {key: fields} where key is a genome name, its slug, its assembly
    accession, or the staged FASTA path it is built from (both absolute and
    relative to this store's ``fasta_root``), every string a sources.csv row
    might be matched on. The staged path matters because a genome YAML records
    the UPSTREAM provider URL while a store row records the local file: for the
    model organisms in ``plantref`` those are different strings for the same
    sequence, and only ``pep/config.yaml`` links them.

    Only genomes whose ``build.store`` names this store are included, so one
    store's curated metadata can never leak into another's sidecars.
    """
    import json

    import yaml

    records = {}
    if not Path(genomes_dir).is_dir():
        return records
    staged = _staged_fasta_paths()
    fasta_root = _store_fasta_root_token(store)
    for path in sorted(Path(genomes_dir).rglob("*.yaml")):
        with open(path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict) or not data.get("name"):
            continue
        if ((data.get("build") or {}).get("store")) != store:
            continue
        sidecar = Path(metadata_dir) / f"{data['name']}.fhr.json"
        if not sidecar.is_file():
            print(
                f"NOTE: genome {data['name']} claims store {store!r} but has no "
                f"curated sidecar at {sidecar}; run "
                f"build/generate_genome_metadata.py.",
                file=sys.stderr,
            )
            continue
        with open(sidecar) as f:
            fields = json.load(f)
        keys = {data["name"], _slug(data["name"]),
                (data.get("assembly") or {}).get("accession")}
        staged_path = staged.get(data["name"])
        if staged_path:
            keys.add(staged_path)
            keys.add(os.path.basename(staged_path))
            if fasta_root and staged_path.startswith(fasta_root + "/"):
                keys.add(staged_path[len(fasta_root) + 1:])
        for key in keys:
            if key:
                records[key] = fields
    return records


def registry_record_for_row(row, records):
    """The curated record for a sources.csv row, or None."""
    keys = [
        (row.get("name") or "").strip(),
        _slug(row.get("name") or ""),
        (row.get("accession") or "").strip(),
    ]
    for token in (row.get("fasta") or "").split():
        keys.append(token)
        keys.append(os.path.basename(token))
    for key in keys:
        if key and key in records:
            return records[key]
    return None


def load_overrides(overrides_dir):
    """Read per-genome override YAMLs: ``<overrides_dir>/<row_name>.yaml``.

    Returns {row_name: fields_dict}. Each file must be a flat mapping of
    camelCase FHR fields; anything else is a hard error (a malformed override
    silently ignored would defeat its whole purpose).
    """
    import yaml

    overrides = {}
    if overrides_dir is None or not Path(overrides_dir).is_dir():
        return overrides
    for path in sorted(Path(overrides_dir).glob("*.yaml")):
        with open(path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            print(f"FATAL: override {path} is not a mapping.", file=sys.stderr)
            sys.exit(1)
        overrides[path.stem] = data
    return overrides


def merge_override(fields, override):
    """Field-level merge: override values win wholesale, CSV fills the rest."""
    merged = dict(fields)
    merged.update(override)
    return merged


def validate_organisms(rows, records=None):
    """Fail loudly (before writing anything) on any organism this script
    cannot annotate.

    Rows covered by a curated registry record are exempt: their organism, common
    name and taxon come from the genome YAML, not from the ORGANISMS map, so
    demanding a mapping for them would block every non-model store for no reason.
    """
    records = records or {}
    unknown = sorted(
        {
            (row.get("organism") or "").strip()
            for row in rows
            if registry_record_for_row(row, records) is None
        }
        - set(ORGANISMS)
    )
    if unknown:
        print(
            "FATAL: sources.csv contains organisms this script has no "
            f"commonName/taxon mapping for: {unknown!r}.\n"
            "Add them to ORGANISMS in build_fhr.py; refusing to guess.",
            file=sys.stderr,
        )
        sys.exit(1)


def build_fhr(store, rows, dry_run=False, overrides=None, records=None):
    from refget.store import FhrMetadata

    records = records or {}
    validate_organisms(rows, records)
    overrides = overrides or {}
    used_overrides = set()
    n_curated = 0

    planned = {}  # digest -> (label, fields)
    n_unresolved = 0
    for i, row in enumerate(rows, 1):
        label = (row.get("name") or row.get("accession") or row.get("fasta", "")).strip()
        coll_digest = resolve_collection_digest(store, row)
        if coll_digest is None:
            n_unresolved += 1
            print(f"  [{i}/{len(rows)}] UNRESOLVED {label}", file=sys.stderr)
            continue
        # Precedence, least to most specific: CSV derivation, then the curated
        # registry record (a real genome YAML beats a sentence built from four
        # CSV columns), then a hand-written override YAML.
        curated = registry_record_for_row(row, records)
        if curated is None:
            fields = fhr_fields_for_row(row)
        else:
            n_curated += 1
            fields = dict(curated)
        row_name = (row.get("name") or "").strip()
        if row_name in overrides:
            fields = merge_override(fields, overrides[row_name])
            used_overrides.add(row_name)
        # Several rows can share one collection (same content, different
        # distributor). Last row wins, EXCEPT that an accession from an earlier
        # row is carried forward when the winner has none: rows sharing a
        # digest never disagree on accession (only on having one), so dropping
        # it would discard the only externally-resolvable identifier.
        if coll_digest in planned:
            prev_fields = planned[coll_digest][1]
            if "accessionID" not in fields and "accessionID" in prev_fields:
                fields["accessionID"] = prev_fields["accessionID"]
        planned[coll_digest] = (label, fields)

    unused = sorted(set(overrides) - used_overrides)
    if unused:
        print(
            f"NOTE: override YAML(s) matched no sources.csv row name: {unused} "
            "(typo, or the row was removed?)",
            file=sys.stderr,
        )

    # assemblyLevel is applied AFTER dedup, keyed on whichever accession each
    # record ended up with (including one carried forward from an earlier row).
    # A record that already carries assemblyLevel (only possible via an
    # override) keeps it, the override beats the static map.
    unknown_levels = set()
    for _digest, (_label, fields) in planned.items():
        if "assemblyLevel" in fields:
            continue
        accession = (fields.get("accessionID") or {}).get("name")
        if not accession:
            continue
        level = ASSEMBLY_LEVELS.get(accession)
        if level:
            fields["assemblyLevel"] = level
        else:
            unknown_levels.add(accession)
    if unknown_levels:
        print(
            f"NOTE: no assemblyLevel for accession(s) {sorted(unknown_levels)}; "
            "extend ASSEMBLY_LEVELS in build_fhr.py (never guessed).",
            file=sys.stderr,
        )

    to_write = [(label, digest, fields) for digest, (label, fields) in planned.items()]
    print(f"\nResolved {len(to_write)} collections ({n_unresolved} unresolved)")
    print(f"  {n_curated}/{len(rows)} row(s) used a curated registry record; "
          f"{len(rows) - n_curated} fell back to the CSV derivation")

    if dry_run:
        print("\n[DRY RUN] not writing. Sample:")
        for label, digest, fields in to_write[:3]:
            print(f"  {digest}  {label}: {fields}")
        return n_unresolved

    if not to_write:
        print("Nothing to write.")
        return n_unresolved

    # One lock around all sidecar writes plus the manifest commit, mirroring
    # build_aliases._load: the FHR step should land as a unit, and
    # set_fhr_metadata alone does NOT refresh rgstore.json, store.write()
    # recomputes fhr_digest and publishes the manifest last. lock_for_batch
    # is gtars > 0.9.2; without it each call still takes its own lock.
    print("\nWriting FHR sidecars...")
    has_batch_lock = hasattr(store, "lock_for_batch")
    if has_batch_lock:
        store.lock_for_batch("build_fhr")
    try:
        for label, digest, fields in to_write:
            store.set_fhr_metadata(digest, FhrMetadata(**fields))
        store.write()
    finally:
        if has_batch_lock:
            store.release_batch_lock()

    print(f"Done: {len(to_write)} FHR sidecar(s) written, {n_unresolved} row(s) unresolved.")
    return n_unresolved


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("store", help="Store name (a stores/<store> dir)")
    parser.add_argument(
        "--store-path", help="Override built-store path (default $REFGETSTORE_BASE/<store>)"
    )
    parser.add_argument("--sources", help="Override sources.csv path")
    parser.add_argument(
        "--overrides",
        help="Per-genome override YAML dir (default stores/<store>/genomes/)",
    )
    parser.add_argument(
        "--no-registry",
        action="store_true",
        help="ignore the curated pep/metadata sidecars; derive every row from the CSV",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from refget.store import RefgetStore

    sources_path = (
        Path(args.sources) if args.sources else SCRIPT_DIR / args.store / "sources.csv"
    )
    if not sources_path.exists():
        print(f"sources.csv not found: {sources_path}", file=sys.stderr)
        sys.exit(1)

    if args.store_path:
        store_path = Path(args.store_path)
    else:
        base = os.environ.get("REFGETSTORE_BASE")
        if not base:
            print("REFGETSTORE_BASE not set and --store-path not given.", file=sys.stderr)
            sys.exit(1)
        store_path = Path(base) / args.store
    if not store_path.exists():
        print(f"Store not found: {store_path}", file=sys.stderr)
        sys.exit(1)

    overrides_dir = (
        Path(args.overrides) if args.overrides else SCRIPT_DIR / args.store / "genomes"
    )
    overrides = load_overrides(overrides_dir)

    records = {} if args.no_registry else load_registry_records(args.store)

    rows = read_sources(sources_path)
    print(f"Store:    {store_path}")
    print(f"Sources:  {sources_path} ({len(rows)} rows)")
    print(f"Overrides: {overrides_dir} ({len(overrides)} file(s))")
    print(f"Registry: {len(records)} curated key(s) for store {args.store!r}")
    print(f"Dry run:  {args.dry_run}")

    store = RefgetStore.on_disk(str(store_path))
    if hasattr(store, "set_quiet"):
        store.set_quiet(True)

    n_unresolved = build_fhr(
        store, rows, dry_run=args.dry_run, overrides=overrides, records=records
    )
    # Unresolved rows are warnings, not failures: a store legitimately may not
    # contain every sources.csv row (build in progress, removed collections).
    sys.exit(0 if n_unresolved < len(rows) else 1)


if __name__ == "__main__":
    main()
