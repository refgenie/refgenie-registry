#!/usr/bin/env python3
"""Validate genome YAML files against the refgenie-registry (FHR-aligned) schema.

Runs, per file:
  1. YAML syntax
  2. JSON Schema validation (schema/genome.schema.yaml)
  3. Content checks the schema can't express (checksum hex, taxon id, ORCID/DOI
     shape in the optional `fhr:` block, ...)
  4. Name matches filename; alias-conflict scan across the corpus
  5. FHR export self-check: map the YAML to its .fhr.json via genome_to_fhr and
     confirm the result is JSON-serializable and structurally FHR-valid (on by
     default; disable with --no-fhr-check)
  6. FASTA URL reachability (optional, slow; disable with --no-url-check)

Usage:
    python tools/validate_genome.py genomes/human/hg38.yaml
    python tools/validate_genome.py genomes/**/*.yaml --no-url-check
    python tools/validate_genome.py genomes/human/hg38.yaml --verbose
"""

import argparse
import re
import sys
from pathlib import Path

import requests
import yaml
from jsonschema import Draft202012Validator

import genome_to_fhr

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "genome.schema.yaml"
GENOMES_DIR = Path(__file__).resolve().parent.parent / "genomes"

MASKING_VALUES = {"soft-masked", "hard-masked", "not-masked", "unknown"}
ORCID_RE = re.compile(r"^https://orcid\.org/\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")
DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
SPDX_RE = re.compile(r"^[A-Za-z0-9.\-+]+$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
VITAL_INT_FIELDS = (
    "n50",
    "l50",
    "l90",
    "total_base_pairs",
    "number_contigs",
    "number_scaffolds",
)


def load_schema() -> dict:
    with open(SCHEMA_PATH) as f:
        return yaml.safe_load(f)


def load_genome(path: Path) -> dict:
    with open(path) as f:
        data = yaml.safe_load(f)
    # Normalize YAML date/datetime -> ISO strings so schema `type: string` checks
    # (and the FHR export) behave the same whether or not dates were quoted.
    return genome_to_fhr.normalize_dates(data)


def validate_schema(data: dict, schema: dict) -> list[str]:
    """Validate data against JSON Schema. Return list of error messages."""
    validator = Draft202012Validator(schema)
    msgs = []
    for e in validator.iter_errors(data):
        loc = "/".join(str(p) for p in e.absolute_path)
        prefix = f"{loc}: " if loc else ""
        msgs.append(f"schema: {prefix}{e.message}")
    return msgs


def check_yaml_syntax(path: Path) -> str | None:
    """Return error message if YAML is malformed, else None."""
    try:
        with open(path) as f:
            yaml.safe_load(f)
        return None
    except yaml.YAMLError as exc:
        return f"YAML syntax error: {exc}"


def check_required_fields(data: dict) -> list[str]:
    """Belt-and-suspenders required-field check (the schema is authoritative)."""
    errors = []
    if not data.get("name"):
        errors.append("Missing required field: name")
    if not data.get("description"):
        errors.append("Missing required field: description")
    return errors


def check_checksum_format(data: dict) -> list[str]:
    """Verify checksum strings are well-formed. The sentinel
    `compute_on_registration` is accepted for sha256."""
    errors = []
    checksum = data.get("fasta", {}).get("checksum", {}) or {}
    sha = checksum.get("sha256", "")
    if sha and sha != "compute_on_registration":
        if len(sha) != 64 or not all(c in "0123456789abcdef" for c in sha):
            errors.append(
                f"Invalid sha256 (expected 64 lowercase hex chars or the "
                f"'compute_on_registration' sentinel): {sha[:24]}..."
            )
    md5 = checksum.get("md5", "")
    if md5 and (len(md5) != 32 or not all(c in "0123456789abcdef" for c in md5)):
        errors.append(f"Invalid md5 (expected 32 lowercase hex chars): {md5[:16]}...")
    return errors


def check_taxon(data: dict) -> list[str]:
    """`organism.taxon_id` must be a positive integer."""
    errors = []
    organism = data.get("organism", {}) or {}
    taxon_id = organism.get("taxon_id")
    if taxon_id is None:
        errors.append("Missing required field: organism.taxon_id")
    elif not isinstance(taxon_id, int) or isinstance(taxon_id, bool) or taxon_id < 1:
        errors.append(f"organism.taxon_id must be a positive integer, got: {taxon_id!r}")
    return errors


def check_masking(data: dict) -> list[str]:
    errors = []
    masking = data.get("masking")
    if masking is not None and masking not in MASKING_VALUES:
        errors.append(
            f"masking must be one of {sorted(MASKING_VALUES)}, got: {masking!r}"
        )
    return errors


def check_fhr_block(data: dict) -> list[str]:
    """Content checks for the optional `fhr:` provenance block."""
    errors = []
    fhr = data.get("fhr")
    if not isinstance(fhr, dict):
        return errors

    for role in ("metadata_author", "assembly_author"):
        for i, author in enumerate(fhr.get(role, []) or []):
            uri = (author or {}).get("uri")
            # ORCID is required only for metadata_author per FHR; check shape when present.
            if uri and role == "metadata_author" and not ORCID_RE.match(uri):
                errors.append(
                    f"fhr.{role}[{i}].uri is not a valid ORCID URI "
                    f"(https://orcid.org/0000-0002-1825-0097): {uri!r}"
                )

    doi = fhr.get("scholarly_article")
    if doi is not None and not DOI_RE.match(str(doi)):
        errors.append(
            f"fhr.scholarly_article should be a bare DOI (e.g. 10.1038/nature12345): {doi!r}"
        )

    lic = fhr.get("license")
    if lic is not None and (not isinstance(lic, str) or not SPDX_RE.match(lic)):
        errors.append(
            f"fhr.license should be an SPDX id (e.g. CC0-1.0, MIT): {lic!r}"
        )

    dc = fhr.get("date_created")
    if dc is not None and not ISO_DATE_RE.match(str(dc)):
        errors.append(f"fhr.date_created must be an ISO date (YYYY-MM-DD): {dc!r}")

    vs = fhr.get("vital_stats")
    if isinstance(vs, dict):
        for field in VITAL_INT_FIELDS:
            val = vs.get(field)
            if val is not None and (not isinstance(val, int) or isinstance(val, bool)):
                errors.append(f"fhr.vital_stats.{field} must be an integer, got: {val!r}")
    return errors


def check_fhr_export(data: dict) -> list[str]:
    """Map the YAML to FHR and confirm it is JSON-serializable and structurally
    valid against the vendored FHR schema (relaxed export profile). This is what
    guarantees the sidecar round-trips through gtars FhrMetadata."""
    import json

    errors = []
    try:
        fhr = genome_to_fhr.genome_yaml_to_fhr(data)
        json.dumps(fhr)  # serializability
    except Exception as exc:  # noqa: BLE001
        return [f"FHR export failed: {exc}"]

    try:
        export_schema = genome_to_fhr.load_fhr_export_schema()
    except Exception as exc:  # noqa: BLE001
        return [f"FHR export self-check could not load vendored schema: {exc}"]

    validator = Draft202012Validator(export_schema)
    for e in validator.iter_errors(fhr):
        loc = "/".join(str(p) for p in e.absolute_path)
        prefix = f"{loc}: " if loc else ""
        errors.append(f"fhr-export: {prefix}{e.message}")
    return errors


def check_url_reachable(url: str, timeout: int = 15) -> str | None:
    """HEAD-request the URL. Return error message on failure. ftp:// is skipped."""
    if url.startswith("ftp://"):
        return None
    try:
        resp = requests.head(url, allow_redirects=True, timeout=timeout)
        if resp.status_code >= 400:
            return f"URL returned HTTP {resp.status_code}: {url}"
        return None
    except requests.RequestException as exc:
        return f"URL unreachable: {url} ({exc})"


def check_alias_conflicts(data: dict, current_path: Path) -> list[str]:
    """Check whether any alias/name conflicts with names/aliases in other files."""
    errors = []
    proposed_names = {data.get("name", "").lower()}
    for alias in data.get("aliases", []):
        proposed_names.add(alias.lower())
    proposed_names.discard("")

    for genome_file in GENOMES_DIR.rglob("*.yaml"):
        if genome_file.resolve() == current_path.resolve():
            continue
        try:
            with open(genome_file) as f:
                other = yaml.safe_load(f)
        except Exception:
            continue
        if not isinstance(other, dict):
            continue
        other_names = {other.get("name", "").lower()}
        for alias in other.get("aliases", []):
            other_names.add(alias.lower())
        other_names.discard("")
        conflicts = proposed_names & other_names
        if conflicts:
            errors.append(
                f"Alias conflict with {genome_file.relative_to(GENOMES_DIR)}: "
                f"conflicting name(s): {', '.join(sorted(conflicts))}"
            )
    return errors


def check_name_matches_filename(data: dict, path: Path) -> list[str]:
    """The `name` field should match the YAML filename (without extension)."""
    errors = []
    expected = path.stem
    actual = data.get("name", "")
    if actual and actual != expected:
        errors.append(
            f"Genome name '{actual}' does not match filename '{expected}.yaml'. "
            f"These should be identical."
        )
    return errors


def validate_genome(
    path: Path,
    schema: dict,
    check_urls: bool = True,
    check_fhr: bool = True,
    verbose: bool = False,
) -> list[str]:
    """Run all validation checks on a single genome file. Return list of errors."""
    errors = []

    syntax_err = check_yaml_syntax(path)
    if syntax_err:
        return [syntax_err]

    data = load_genome(path)
    if not isinstance(data, dict):
        return [f"Expected a YAML mapping, got {type(data).__name__}"]

    errors.extend(validate_schema(data, schema))
    errors.extend(check_required_fields(data))
    errors.extend(check_taxon(data))
    errors.extend(check_checksum_format(data))
    errors.extend(check_masking(data))
    errors.extend(check_fhr_block(data))
    errors.extend(check_name_matches_filename(data, path))
    errors.extend(check_alias_conflicts(data, path))

    if check_fhr:
        errors.extend(check_fhr_export(data))

    if verbose:
        taxon_id = (data.get("organism") or {}).get("taxon_id")
        if taxon_id is not None:
            print(f"  taxon.uri -> {genome_to_fhr._taxon_uri(taxon_id)}")

    if check_urls:
        for source in (data.get("fasta", {}) or {}).get("sources", []) or []:
            url = source.get("url")
            if url:
                url_err = check_url_reachable(url)
                if url_err:
                    errors.append(url_err)

    return errors


def main():
    parser = argparse.ArgumentParser(description="Validate refgenie genome YAML files")
    parser.add_argument("files", nargs="+", type=Path, help="Genome YAML files to validate")
    parser.add_argument(
        "--no-url-check",
        action="store_true",
        help="Skip URL reachability checks (faster, offline-friendly)",
    )
    parser.add_argument(
        "--check-fhr",
        action="store_true",
        help="Run the FHR export self-check (this is the default; kept for explicitness in CI)",
    )
    parser.add_argument(
        "--no-fhr-check",
        action="store_true",
        help="Skip the FHR export self-check (on by default)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print derived values (e.g. taxon.uri) for reviewer sanity",
    )
    args = parser.parse_args()

    schema = load_schema()
    all_passed = True

    for filepath in args.files:
        if not filepath.exists():
            print(f"SKIP {filepath} (file not found)")
            continue

        errors = validate_genome(
            filepath,
            schema,
            check_urls=not args.no_url_check,
            check_fhr=not args.no_fhr_check,
            verbose=args.verbose,
        )
        if errors:
            all_passed = False
            print(f"FAIL {filepath}")
            for err in errors:
                print(f"  - {err}")
        else:
            print(f"PASS {filepath}")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
