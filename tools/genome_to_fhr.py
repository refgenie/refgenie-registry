#!/usr/bin/env python3
"""Deterministic mapping from a registry genome YAML to an FHR `.fhr.json` sidecar.

This is the single source of truth for the registry-YAML -> FHR-JSON mapping
(see schema/README.md for the table). Every consumer -- the validator's export
self-check and the Plan 3 store loader -- must go through this module so they
agree on the mapping.

The emitted JSON uses camelCase keys that match the gtars `FhrMetadata` serde
representation exactly (gtars-refget/src/store/fhr_metadata.rs), so
`serde_json::from_str::<FhrMetadata>` accepts it and the RefgetStore round-trips
it unchanged. The seqcol digest is NEVER a JSON body field -- it is the sidecar
*filename* (`<seqcol.digest>.fhr.json`), matching `SIDECAR_EXTENSION` in gtars.

Usage:
    python tools/genome_to_fhr.py genomes/human/hg38.yaml            # print JSON
    python tools/genome_to_fhr.py genomes/human/hg38.yaml --out-dir fhr/
"""

from __future__ import annotations

import argparse
import copy
import datetime
import json
import sys
from collections import OrderedDict
from pathlib import Path

import yaml

# Canonical FHR schema identity, injected as constants into every export.
FHR_SCHEMA_URL = (
    "https://raw.githubusercontent.com/FAIR-bioHeaders/FHR-Specification/main/fhr.json"
)
FHR_SCHEMA_VERSION = 1.0
SIDECAR_EXTENSION = ".fhr.json"

_SENTINEL_SHA256 = "compute_on_registration"

VENDORED_FHR_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "fhr.schema.json"


def normalize_dates(obj):
    """Recursively convert YAML date/datetime values to ISO-8601 strings.

    PyYAML parses an unquoted `2026-05-20` into a `datetime.date`, but the JSON
    Schema (and the JSON sidecar the export becomes) expect strings. Normalizing
    at load time keeps schema validation and FHR export consistent and JSON-
    serializable regardless of whether a contributor quoted their dates.
    """
    if isinstance(obj, dict):
        return {k: normalize_dates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_dates(v) for v in obj]
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    return obj


def _taxon_uri(taxon_id: int) -> str:
    """Derive the identifiers.org taxonomy URI from an NCBI taxon id."""
    return f"https://identifiers.org/taxonomy:{taxon_id}"


def _accession_url(accession: str) -> str | None:
    """Derive a resolvable URL for an assembly accession, or None."""
    if accession.startswith(("GCA_", "GCF_")):
        return f"https://www.ncbi.nlm.nih.gov/datasets/genome/{accession}/"
    return None


def _vital_stats(vs: dict) -> "OrderedDict[str, object]":
    """Map snake_case vital_stats to FHR camelCase (with N50/L50/L90 upper)."""
    mapping = [
        ("n50", "N50"),
        ("l50", "L50"),
        ("l90", "L90"),
        ("total_base_pairs", "totalBasePairs"),
        ("number_contigs", "numberContigs"),
        ("number_scaffolds", "numberScaffolds"),
        ("read_technology", "readTechnology"),
    ]
    out: "OrderedDict[str, object]" = OrderedDict()
    for src, dst in mapping:
        if vs.get(src) is not None:
            out[dst] = vs[src]
    return out


def _authors(authors: list) -> list:
    """Map a list of {name, uri} author objects, dropping empties."""
    out = []
    for a in authors or []:
        obj: "OrderedDict[str, object]" = OrderedDict()
        if a.get("name") is not None:
            obj["name"] = a["name"]
        if a.get("uri") is not None:
            obj["uri"] = a["uri"]
        if obj:
            out.append(obj)
    return out


def genome_yaml_to_fhr(data: dict) -> "OrderedDict[str, object]":
    """Apply the deterministic YAML -> FHR mapping. Returns an ordered dict of
    camelCase FHR fields (the shape gtars `FhrMetadata` deserializes)."""
    data = normalize_dates(data)
    organism = data.get("organism", {}) or {}
    assembly = data.get("assembly", {}) or {}
    fasta = data.get("fasta", {}) or {}
    checksum = fasta.get("checksum", {}) or {}
    fhr = data.get("fhr", {}) or {}

    # Emit in gtars FhrMetadata struct field order for readability.
    out: "OrderedDict[str, object]" = OrderedDict()
    out["schema"] = FHR_SCHEMA_URL
    out["schemaVersion"] = FHR_SCHEMA_VERSION

    scientific_name = organism.get("scientific_name")
    if scientific_name is not None:
        out["genome"] = scientific_name

    taxon_id = organism.get("taxon_id")
    if scientific_name is not None or taxon_id is not None:
        taxon: "OrderedDict[str, object]" = OrderedDict()
        if scientific_name is not None:
            taxon["name"] = scientific_name
        if taxon_id is not None:
            taxon["uri"] = _taxon_uri(taxon_id)
        out["taxon"] = taxon

    # Promoted-to-column extension fields (gtars FhrMetadata carries these in its
    # `extra` catch-all; refgenie1's apply_fhr reads them into real `genome`
    # columns). Not part of upstream FHR 1.0 -- see load_fhr_export_schema.
    common_name = organism.get("common_name")
    if common_name is not None:
        out["commonName"] = common_name

    if data.get("name") is not None:
        out["version"] = data["name"]

    metadata_author = _authors(fhr.get("metadata_author"))
    if metadata_author:
        out["metadataAuthor"] = metadata_author

    assembly_author = _authors(fhr.get("assembly_author"))
    if assembly_author:
        out["assemblyAuthor"] = assembly_author

    if fhr.get("date_created") is not None:
        out["dateCreated"] = fhr["date_created"]

    if fhr.get("voucher_specimen") is not None:
        out["voucherSpecimen"] = fhr["voucher_specimen"]

    if data.get("masking") is not None:
        out["masking"] = data["masking"]

    sha256 = checksum.get("sha256")
    if sha256 and sha256 != _SENTINEL_SHA256:
        out["checksum"] = f"sha256:{sha256}"

    aliases = data.get("aliases")
    if aliases:
        out["genomeSynonym"] = list(aliases)

    accession = assembly.get("accession")
    if accession:
        acc: "OrderedDict[str, object]" = OrderedDict()
        acc["name"] = accession
        url = _accession_url(accession)
        if url:
            acc["url"] = url
        out["accessionID"] = acc

    # Promoted-to-column assembly facets (gtars `extra`; read into `genome`
    # columns by refgenie1's apply_fhr). Not part of upstream FHR 1.0.
    assembly_source = assembly.get("source")
    if assembly_source is not None:
        out["assemblySource"] = assembly_source

    assembly_level = assembly.get("level")
    if assembly_level is not None:
        out["assemblyLevel"] = assembly_level

    if fhr.get("instrument"):
        out["instrument"] = list(fhr["instrument"])

    if fhr.get("scholarly_article") is not None:
        out["scholarlyArticle"] = fhr["scholarly_article"]

    # `documentation`: fhr.documentation overrides the top-level description.
    documentation = fhr.get("documentation")
    if documentation is None:
        documentation = data.get("description")
    if documentation is not None:
        out["documentation"] = documentation

    if fhr.get("identifier"):
        out["identifier"] = list(fhr["identifier"])

    if fhr.get("license") is not None:
        out["license"] = fhr["license"]

    if fhr.get("related_link"):
        out["relatedLink"] = list(fhr["related_link"])

    if fhr.get("funding") is not None:
        out["funding"] = fhr["funding"]

    vs = _vital_stats(fhr.get("vital_stats", {}) or {})
    if vs:
        out["vitalStats"] = vs

    return out


def load_fhr_export_schema() -> dict:
    """Return a JSON Schema for validating a registry FHR *export*.

    Derived from the vendored upstream FHR schema (schema/fhr.schema.json) with
    three documented relaxations, because a registry export is intentionally a
    *partial* FHR record whose real round-trip target is the permissive gtars
    `FhrMetadata` type, not a complete publishable FHR 1.0 record:

      1. Drop the upstream top-level `required` list. Upstream FHR requires 10
         fields (metadataAuthor, assemblyAuthor, dateCreated, masking, checksum,
         ...) that define a *complete* record; the registry treats all FHR
         provenance as optional (design decision D3), and gtars does too.
      2. Allow the `license` property. Upstream FHR uses `reuseConditions`, but
         gtars `FhrMetadata` carries an explicit `license` field and the mapping
         (D4) emits it, so the export must be allowed to contain it.
      3. Relax `checksum` to the `algo:value` form the exporter and gtars use
         (e.g. `sha256:<64 hex>`), rather than the upstream sha2-512/256 base64
         form.

    Every other property type/pattern/enum and `additionalProperties: false`
    are preserved, so typos and malformed provenance are still caught.
    """
    with open(VENDORED_FHR_SCHEMA_PATH) as f:
        schema = json.load(f)
    profile = copy.deepcopy(schema)
    profile.pop("required", None)
    profile.setdefault("properties", {})
    # (2) gtars-native license extension.
    profile["properties"]["license"] = {
        "type": "string",
        "description": "SPDX license id (gtars FhrMetadata extension; not in upstream FHR).",
    }
    # (3) registry checksum form: algo:value.
    profile["properties"]["checksum"] = {
        "type": "string",
        "pattern": "^[a-z0-9]+:.+$",
        "description": "Algorithm-prefixed checksum, e.g. sha256:<64 hex>.",
    }
    # (4) promoted-to-column extension fields: common name and assembly
    # source/level ride the FHR sidecar (gtars `extra`) so refgenie1's apply_fhr
    # can land them in real `genome` columns. Not in upstream FHR; allowed here
    # so the export self-check accepts a sidecar carrying them.
    profile["properties"]["commonName"] = {
        "type": "string",
        "description": "Common organism name (gtars extension; promoted to a genome column).",
    }
    profile["properties"]["assemblySource"] = {
        "type": "string",
        "description": "Assembly provider, e.g. UCSC/NCBI (gtars extension; genome column).",
    }
    profile["properties"]["assemblyLevel"] = {
        "type": "string",
        "description": "Assembly level, e.g. chromosome/scaffold (gtars extension; genome column).",
    }
    return profile


def write_fhr_sidecar(data: dict, out_dir: Path) -> Path:
    """Write `<seqcol.digest>.fhr.json` into out_dir. Returns the path.

    Raises ValueError if the genome has no resolved seqcol digest (still
    `compute: true` or absent) -- the sidecar is digest-addressed and cannot be
    named without it.
    """
    seqcol = data.get("seqcol", {}) or {}
    digest = seqcol.get("digest")
    if not digest:
        raise ValueError(
            "Cannot write FHR sidecar: seqcol.digest is absent or unresolved "
            "(seqcol.compute is true). The seqcol digest is the sidecar filename."
        )
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fhr = genome_yaml_to_fhr(data)
    path = out_dir / f"{digest}{SIDECAR_EXTENSION}"
    with open(path, "w") as f:
        json.dump(fhr, f, indent=2)
        f.write("\n")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Map a registry genome YAML to an FHR .fhr.json sidecar."
    )
    parser.add_argument("genome", type=Path, help="Path to a genome YAML file")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Write <seqcol.digest>.fhr.json into this directory (default: print JSON to stdout)",
    )
    args = parser.parse_args()

    with open(args.genome) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        print(f"ERROR: {args.genome} is not a YAML mapping", file=sys.stderr)
        sys.exit(1)

    if args.out_dir:
        try:
            path = write_fhr_sidecar(data, args.out_dir)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"Wrote {path}")
    else:
        print(json.dumps(genome_yaml_to_fhr(data), indent=2))


if __name__ == "__main__":
    main()
