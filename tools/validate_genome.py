#!/usr/bin/env python3
"""Validate genome YAML files against the refgenie-registry (FHR-aligned) schema.

Runs, per file:
  1. YAML syntax
  2. JSON Schema validation (schema/genome.schema.yaml)
  3. Content checks the schema can't express (checksum hex, taxon id, ORCID/DOI
     shape in the optional `fhr:` block, ...)
  3b. `build:` checks: the store slug names a real stores/<slug>/, the tier is
     one of pep/tiers.yaml, every add/drop asset has a recipe, and the block
     never leaks into the FHR export
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

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schema" / "genome.schema.yaml"
GENOMES_DIR = REPO_ROOT / "genomes"
STORES_DIR = REPO_ROOT / "stores"
RECIPES_DIR = REPO_ROOT / "recipes"
TIERS_PATH = REPO_ROOT / "pep" / "tiers.yaml"

# `stores/store_config.py` owns the single definition of "a store exists"
# (get_store_dirs). It depends on pyyaml only, so importing it here does not drag
# peppy/refget into the validator's (CI) dependency set.
sys.path.insert(0, str(STORES_DIR))
from store_config import store_slugs  # noqa: E402

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


def load_tiers(path: Path = TIERS_PATH) -> dict:
    """The tier ladder from pep/tiers.yaml ({tier_name: [asset, ...]})."""
    with open(path) as f:
        return (yaml.safe_load(f) or {}).get("tiers") or {}


def known_recipes(recipes_dir: Path = RECIPES_DIR) -> set[str]:
    """Every buildable asset name (a recipes/<name>/recipe.yaml exists)."""
    if not recipes_dir.is_dir():
        return set()
    return {d.name for d in recipes_dir.iterdir() if (d / "recipe.yaml").is_file()}


def check_build_store(data: dict, stores_dir: Path = STORES_DIR) -> list[str]:
    """`build.store` must name an existing store directory under stores/.

    The valid slugs are listed in the error: a contributor has no other way to
    discover them.
    """
    build = data.get("build")
    if not isinstance(build, dict):
        return []
    store = build.get("store")
    if store is None:
        return []
    slugs = store_slugs(stores_dir)
    if store not in slugs:
        return [
            f"build.store '{store}' is not a store. Valid stores: "
            f"{', '.join(slugs)}. (A store is a directory under stores/ with a "
            f"project_config.yaml.)"
        ]
    return []


def check_build_tier(
    data: dict, tiers: dict | None = None, recipes: set[str] | None = None
) -> list[str]:
    """`build.tier` must exist in pep/tiers.yaml; add/drop must name real recipes.

    Catches a typo'd asset name on the PR instead of at 3am in the nightly.
    """
    build = data.get("build")
    if not isinstance(build, dict):
        return []
    if tiers is None:
        tiers = load_tiers()
    if recipes is None:
        recipes = known_recipes()

    errors = []
    tier = build.get("tier")
    if tier is not None and tier not in tiers:
        errors.append(
            f"build.tier '{tier}' is not a known tier. Valid tiers: "
            f"{', '.join(sorted(tiers))} (defined in pep/tiers.yaml)."
        )
    for key in ("add", "drop"):
        value = build.get(key)
        if value is None:
            continue
        if not isinstance(value, list):
            errors.append(f"build.{key} must be a list, got {type(value).__name__}")
            continue
        for asset in value:
            if asset not in recipes:
                errors.append(
                    f"build.{key} names '{asset}', which has no recipe under "
                    f"recipes/. Check the spelling against the recipe directory names."
                )
    return errors


def check_build_not_in_fhr(data: dict) -> list[str]:
    """The `build:` block must never reach the FHR sidecar.

    genome_yaml_to_fhr() builds its output by explicit whitelist, so this holds
    by construction, assert it anyway, so a future exporter change that starts
    passing unknown keys through fails here instead of shipping pipeline
    instructions into published metadata.
    """
    if "build" not in data:
        return []
    try:
        fhr = genome_to_fhr.genome_yaml_to_fhr(data)
    except Exception as exc:  # noqa: BLE001
        return [f"FHR export failed while checking build-block isolation: {exc}"]
    leaked = sorted(k for k in fhr if k.lower().startswith("build"))
    if leaked:
        return [
            f"build: leaked into the FHR export as {leaked}. The build block is "
            f"registry pipeline state and must not be published as metadata."
        ]
    return []


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


def _record_names(data: dict) -> set[str]:
    """Lowercased name + aliases for one record."""
    names = {str(data.get("name", "")).lower()}
    for alias in data.get("aliases") or []:
        names.add(str(alias).lower())
    names.discard("")
    return names


def build_alias_index(genomes_dir: Path = GENOMES_DIR) -> dict[str, list[Path]]:
    """{lowercased name-or-alias: [file, ...]} across the whole corpus.

    Built ONCE per run and reused. The check is inherently a corpus-wide
    question, so the naive form re-read every genome YAML for every genome YAML:
    at 707 files that is half a million file reads and the validator does not
    finish. This is the same comparison, done in one pass.
    """
    index: dict[str, list[Path]] = {}
    for genome_file in sorted(genomes_dir.rglob("*.yaml")):
        try:
            with open(genome_file) as f:
                other = yaml.safe_load(f)
        except Exception:  # noqa: BLE001 - a malformed file is reported by its own check
            continue
        if not isinstance(other, dict):
            continue
        for name in _record_names(other):
            index.setdefault(name, []).append(genome_file)
    return index


def check_alias_conflicts(
    data: dict, current_path: Path, index: dict[str, list[Path]] | None = None
) -> list[str]:
    """Check whether any alias/name conflicts with names/aliases in other files."""
    if index is None:
        index = build_alias_index()
    current = current_path.resolve()

    conflicts: dict[Path, set[str]] = {}
    for name in _record_names(data):
        for other_file in index.get(name, []):
            if other_file.resolve() == current:
                continue
            conflicts.setdefault(other_file, set()).add(name)

    return [
        f"Alias conflict with {other.relative_to(GENOMES_DIR)}: "
        f"conflicting name(s): {', '.join(sorted(names))}"
        for other, names in sorted(conflicts.items())
    ]


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
    tiers: dict | None = None,
    recipes: set[str] | None = None,
    alias_index: dict | None = None,
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
    errors.extend(check_build_store(data))
    errors.extend(check_build_tier(data, tiers, recipes))
    errors.extend(check_build_not_in_fhr(data))
    errors.extend(check_fhr_block(data))
    errors.extend(check_name_matches_filename(data, path))
    errors.extend(check_alias_conflicts(data, path, alias_index))

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
    # Loaded once for the whole run: the tier ladder and the recipe list are the
    # same for every file, and re-globbing recipes/ per file is 700x wasted work
    # on a full-corpus validate.
    tiers = load_tiers()
    recipes = known_recipes()
    alias_index = build_alias_index()
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
            tiers=tiers,
            recipes=recipes,
            alias_index=alias_index,
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
