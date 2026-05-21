#!/usr/bin/env python3
"""Validate genome YAML files against the refgenie-registry schema.

Usage:
    python tools/validate_genome.py genomes/human/hg38.yaml
    python tools/validate_genome.py genomes/**/*.yaml
"""

import argparse
import sys
from pathlib import Path

import requests
import yaml
from jsonschema import Draft202012Validator, ValidationError


SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "genome.schema.yaml"
GENOMES_DIR = Path(__file__).resolve().parent.parent / "genomes"


def load_schema() -> dict:
    with open(SCHEMA_PATH) as f:
        return yaml.safe_load(f)


def load_genome(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def validate_schema(data: dict, schema: dict) -> list[str]:
    """Validate data against JSON Schema. Return list of error messages."""
    validator = Draft202012Validator(schema)
    return [e.message for e in validator.iter_errors(data)]


def check_yaml_syntax(path: Path) -> str | None:
    """Return error message if YAML is malformed, else None."""
    try:
        with open(path) as f:
            yaml.safe_load(f)
        return None
    except yaml.YAMLError as exc:
        return f"YAML syntax error: {exc}"


def check_required_fields(data: dict) -> list[str]:
    """Check required fields beyond what JSON Schema catches."""
    errors = []
    if not data.get("name"):
        errors.append("Missing required field: name")
    fasta = data.get("fasta", {})
    if not fasta.get("primary_url"):
        errors.append("Missing required field: fasta.primary_url")
    checksum = fasta.get("checksum", {})
    if not checksum.get("sha256"):
        errors.append("Missing required field: fasta.checksum.sha256")
    return errors


def check_url_reachable(url: str, timeout: int = 15) -> str | None:
    """HEAD-request the URL. Return error message on failure."""
    try:
        resp = requests.head(url, allow_redirects=True, timeout=timeout)
        if resp.status_code >= 400:
            return f"URL returned HTTP {resp.status_code}: {url}"
        return None
    except requests.RequestException as exc:
        return f"URL unreachable: {url} ({exc})"


def check_checksum_format(data: dict) -> list[str]:
    """Verify checksum strings are well-formed."""
    errors = []
    checksum = data.get("fasta", {}).get("checksum", {})
    sha = checksum.get("sha256", "")
    if sha and (len(sha) != 64 or not all(c in "0123456789abcdef" for c in sha)):
        errors.append(
            f"Invalid sha256 format (expected 64 hex chars, got {len(sha)} chars): {sha[:20]}..."
        )
    return errors


def check_alias_conflicts(data: dict, current_path: Path) -> list[str]:
    """Check whether any alias in this genome conflicts with names/aliases in other genome files."""
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


def validate_genome(path: Path, schema: dict, check_urls: bool = True) -> list[str]:
    """Run all validation checks on a single genome file. Return list of errors."""
    errors = []

    # 1. YAML syntax
    syntax_err = check_yaml_syntax(path)
    if syntax_err:
        return [syntax_err]

    data = load_genome(path)
    if not isinstance(data, dict):
        return [f"Expected a YAML mapping, got {type(data).__name__}"]

    # 2. JSON Schema validation
    errors.extend(validate_schema(data, schema))

    # 3. Required fields (belt-and-suspenders)
    errors.extend(check_required_fields(data))

    # 4. Checksum format
    errors.extend(check_checksum_format(data))

    # 5. Name matches filename
    errors.extend(check_name_matches_filename(data, path))

    # 6. Alias conflicts
    errors.extend(check_alias_conflicts(data, path))

    # 7. URL reachability (optional, slow)
    if check_urls:
        url = data.get("fasta", {}).get("primary_url")
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
    args = parser.parse_args()

    schema = load_schema()
    all_passed = True

    for filepath in args.files:
        if not filepath.exists():
            print(f"SKIP {filepath} (file not found)")
            continue

        errors = validate_genome(filepath, schema, check_urls=not args.no_url_check)
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
