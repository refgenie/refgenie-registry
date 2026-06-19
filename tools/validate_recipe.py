#!/usr/bin/env python3
"""Validate refgenie-native recipe YAML files against the registry schema.

Recipes use the single canonical refgenie-native model (see the ADR
"Single canonical recipe model: refgenie-native"). refgenie is the build
system; a recipe is consumed directly by refgenie1.

Usage:
    python tools/validate_recipe.py recipes/bwa_index/recipe.yaml
    python tools/validate_recipe.py recipes/*/recipe.yaml
    python tools/validate_recipe.py --no-url-check recipes/*/recipe.yaml
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

import requests
import yaml
from jsonschema import Draft202012Validator


REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schema" / "recipe.schema.yaml"
RECIPES_DIR = REPO_ROOT / "recipes"
ASSET_CLASSES_DIR = REPO_ROOT / "asset_classes"

SUSPICIOUS_PATTERNS = [
    (r"curl\s.*\|\s*(?:ba)?sh", "Piping curl output to shell is not allowed"),
    (r"wget\s.*\|\s*(?:ba)?sh", "Piping wget output to shell is not allowed"),
    (r"wget\s+-O\s*-\s*.*\|\s*(?:ba)?sh", "Piping wget output to shell is not allowed"),
    (r"\bsudo\b", "sudo is not allowed in recipes"),
    (r"\bsu\s+-", "su is not allowed in recipes"),
    (r"\bchmod\s+[0-7]*[2367][0-7]*\b", "World-writable permissions are suspicious"),
    (r"\b(AWS_SECRET|PASSWORD|TOKEN|API_KEY)\b", "Possible hardcoded credential reference"),
    (r"\bnohup\b", "Background processes (nohup) are not allowed"),
    (r"\bdaemon\b", "Daemon processes are not allowed"),
    (r">\s*/dev/tcp/", "Network exfiltration via /dev/tcp is not allowed"),
    (r"\beval\s+\$", "eval with variable expansion is suspicious"),
]


def load_schema() -> dict:
    with open(SCHEMA_PATH) as f:
        return yaml.safe_load(f)


def load_recipe(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def check_yaml_syntax(path: Path) -> str | None:
    try:
        with open(path) as f:
            yaml.safe_load(f)
        return None
    except yaml.YAMLError as exc:
        return f"YAML syntax error: {exc}"


def validate_schema(data: dict, schema: dict) -> list[str]:
    validator = Draft202012Validator(schema)
    return [e.message for e in validator.iter_errors(data)]


def check_required_fields(data: dict) -> list[str]:
    errors = []
    if not data.get("name"):
        errors.append("Missing required field: name")
    if not data.get("version"):
        errors.append("Missing required field: version")
    if not data.get("output_asset_class"):
        errors.append("Missing required field: output_asset_class (output asset class name)")
    command_templates = data.get("command_templates")
    if not command_templates:
        errors.append("Missing required field: command_templates (at least one command)")
    elif not isinstance(command_templates, list):
        errors.append("command_templates must be a list of shell command strings")
    return errors


def check_name_matches_directory(data: dict, path: Path) -> list[str]:
    """The `name` field should match the parent directory name."""
    errors = []
    expected = path.parent.name
    actual = data.get("name", "")
    if actual and actual != expected:
        errors.append(
            f"Recipe name '{actual}' does not match directory name '{expected}'. "
            f"These should be identical."
        )
    return errors


def _command_text(data: dict) -> str:
    """Join all command templates into a single string for scanning."""
    templates = data.get("command_templates") or []
    if not isinstance(templates, list):
        return ""
    return "\n".join(str(t) for t in templates)


def check_security_patterns(data: dict) -> list[str]:
    """Scan command templates for suspicious patterns."""
    errors = []
    command_text = _command_text(data)
    for pattern, message in SUSPICIOUS_PATTERNS:
        if re.search(pattern, command_text, re.IGNORECASE):
            errors.append(f"Security: {message}")
    return errors


def check_shellcheck(data: dict) -> list[str]:
    """Run shellcheck on the command templates if shellcheck is available.

    Note: command templates contain Jinja ({{ ... }}) which shellcheck does not
    understand. We strip Jinja expressions to placeholder tokens so shellcheck
    can still catch structural shell issues.
    """
    if not shutil.which("shellcheck"):
        return []  # Skip silently if not installed

    script = _command_text(data)
    if not script:
        return []

    # Replace Jinja expressions with a benign placeholder so shellcheck parses.
    script = re.sub(r"\{\{.*?\}\}", "PLACEHOLDER", script)
    script_with_shebang = f"#!/usr/bin/env bash\n{script}"

    errors = []
    result = subprocess.run(
        ["shellcheck", "--severity=warning", "-"],
        input=script_with_shebang,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        for line in result.stdout.strip().splitlines():
            if line.strip():
                errors.append(f"shellcheck (command_templates): {line.strip()}")
    return errors


def check_test_data_urls(data: dict, timeout: int = 15) -> list[str]:
    """Verify optional test_data URLs are reachable."""
    errors = []
    test_data = (data.get("test") or {}).get("test_data", {}) or {}
    for key, url in test_data.items():
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        try:
            resp = requests.head(url, allow_redirects=True, timeout=timeout)
            if resp.status_code >= 400:
                errors.append(f"test_data.{key} URL returned HTTP {resp.status_code}: {url}")
        except requests.RequestException as exc:
            errors.append(f"test_data.{key} URL unreachable: {url} ({exc})")
    return errors


def _asset_class_exists(name: str) -> bool:
    return (ASSET_CLASSES_DIR / f"{name}.yaml").exists()


def check_output_asset_class(data: dict) -> list[str]:
    """Hard check: `output_asset_class` must reference an existing asset class.

    The asset class must have a definition file at asset_classes/<name>.yaml.
    """
    errors = []
    output_asset_class = data.get("output_asset_class")
    if not output_asset_class:
        return errors  # missing-field error handled in check_required_fields

    if not ASSET_CLASSES_DIR.is_dir():
        errors.append(
            f"Cannot verify 'output_asset_class' reference '{output_asset_class}': "
            f"asset class directory not found ({ASSET_CLASSES_DIR})"
        )
        return errors

    if not _asset_class_exists(output_asset_class):
        errors.append(
            f"output_asset_class references asset class '{output_asset_class}', "
            f"but asset_classes/{output_asset_class}.yaml does not exist"
        )
    return errors


def check_input_asset_classes(data: dict) -> list[str]:
    """Hard check: every input_assets[handle].asset_class must reference an
    existing asset class (asset_classes/<name>.yaml)."""
    errors = []
    input_assets = data.get("input_assets") or {}
    if not isinstance(input_assets, dict):
        return errors  # schema validation reports the type error
    if not input_assets:
        return errors

    if not ASSET_CLASSES_DIR.is_dir():
        errors.append(
            f"Cannot verify input_assets references: asset class directory not "
            f"found ({ASSET_CLASSES_DIR})"
        )
        return errors

    for handle, spec in input_assets.items():
        if not isinstance(spec, dict):
            continue  # schema validation reports the type error
        asset_class = spec.get("asset_class")
        if not asset_class:
            continue  # schema validation reports the missing required field
        if not _asset_class_exists(asset_class):
            errors.append(
                f"input_assets['{handle}'].asset_class '{asset_class}' does not "
                f"resolve to an asset class (asset_classes/{asset_class}.yaml missing)"
            )
    return errors


def check_version_format(data: dict) -> list[str]:
    """Validate semver format."""
    errors = []
    version = data.get("version", "")
    if version and not re.match(r"^\d+\.\d+\.\d+$", version):
        errors.append(
            f"Version '{version}' is not valid semver. Expected format: MAJOR.MINOR.PATCH"
        )
    return errors


def validate_recipe(
    path: Path, schema: dict, check_urls: bool = True
) -> tuple[list[str], list[str]]:
    """Run all validation checks on a single recipe file.

    Return (errors, warnings). Errors are fatal (non-zero exit); warnings are
    reported but do not fail validation.
    """
    errors = []
    warnings = []

    # 1. YAML syntax
    syntax_err = check_yaml_syntax(path)
    if syntax_err:
        return [syntax_err], []

    data = load_recipe(path)
    if not isinstance(data, dict):
        return [f"Expected a YAML mapping, got {type(data).__name__}"], []

    # 2. JSON Schema validation
    errors.extend(validate_schema(data, schema))

    # 3. Required fields
    errors.extend(check_required_fields(data))

    # 4. Version format
    errors.extend(check_version_format(data))

    # 5. Name matches directory
    errors.extend(check_name_matches_directory(data, path))

    # 6. Security scan
    errors.extend(check_security_patterns(data))

    # 7. Output asset class reference (output_asset_class) -- hard error
    errors.extend(check_output_asset_class(data))

    # 8. Input asset class references (input_assets[].asset_class) -- hard error
    errors.extend(check_input_asset_classes(data))

    # 9. Shellcheck
    errors.extend(check_shellcheck(data))

    # 10. Test data URL reachability (optional)
    if check_urls:
        errors.extend(check_test_data_urls(data))

    return errors, warnings


def main():
    parser = argparse.ArgumentParser(
        description="Validate refgenie-native recipe YAML files"
    )
    parser.add_argument("files", nargs="+", type=Path, help="Recipe YAML files to validate")
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

        errors, warnings = validate_recipe(
            filepath, schema, check_urls=not args.no_url_check
        )
        if errors:
            all_passed = False
            print(f"FAIL {filepath}")
            for err in errors:
                print(f"  - {err}")
        else:
            print(f"PASS {filepath}")
        for warn in warnings:
            print(f"  ! WARNING: {warn}")

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
