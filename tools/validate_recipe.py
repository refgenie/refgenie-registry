#!/usr/bin/env python3
"""Validate recipe YAML files against the refgenie-registry schema.

Usage:
    python tools/validate_recipe.py recipes/bwa_index/recipe.yaml
    python tools/validate_recipe.py recipes/*/recipe.yaml
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


SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "recipe.schema.yaml"
RECIPES_DIR = Path(__file__).resolve().parent.parent / "recipes"

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
    build = data.get("build", {})
    if not build.get("command"):
        errors.append("Missing required field: build.command")
    if not data.get("outputs"):
        errors.append("Missing required field: outputs (must declare at least one output)")
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


def check_security_patterns(data: dict) -> list[str]:
    """Scan build commands for suspicious patterns."""
    errors = []
    build = data.get("build", {})
    command_text = (build.get("setup") or "") + "\n" + (build.get("command") or "")
    for pattern, message in SUSPICIOUS_PATTERNS:
        if re.search(pattern, command_text, re.IGNORECASE):
            errors.append(f"Security: {message}")
    return errors


def check_tool_sources(data: dict) -> list[str]:
    """Warn about tools from non-standard sources."""
    errors = []
    tools = data.get("requires", {}).get("tools", [])
    approved_sources = {"bioconda", "conda-forge", "pip", "apt"}
    for tool in tools:
        source = tool.get("source", "")
        if source and source not in approved_sources:
            errors.append(
                f"Tool '{tool.get('name', '?')}' uses unapproved source '{source}'. "
                f"Approved sources: {', '.join(sorted(approved_sources))}"
            )
    return errors


def check_shellcheck(data: dict) -> list[str]:
    """Run shellcheck on build commands if shellcheck is available."""
    if not shutil.which("shellcheck"):
        return []  # Skip silently if not installed

    errors = []
    build = data.get("build", {})
    for field in ("setup", "command"):
        script = build.get(field)
        if not script:
            continue
        # shellcheck requires a shebang; prepend one
        script_with_shebang = f"#!/usr/bin/env bash\n{script}"
        result = subprocess.run(
            ["shellcheck", "--severity=warning", "-"],
            input=script_with_shebang,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            for line in result.stdout.strip().splitlines():
                if line.strip():
                    errors.append(f"shellcheck ({field}): {line.strip()}")
    return errors


def check_test_data_urls(data: dict, timeout: int = 15) -> list[str]:
    """Verify test_data URLs are reachable."""
    errors = []
    test_data = data.get("test", {}).get("test_data", {})
    for key, url in test_data.items():
        if not url.startswith(("http://", "https://")):
            continue
        try:
            resp = requests.head(url, allow_redirects=True, timeout=timeout)
            if resp.status_code >= 400:
                errors.append(f"test_data.{key} URL returned HTTP {resp.status_code}: {url}")
        except requests.RequestException as exc:
            errors.append(f"test_data.{key} URL unreachable: {url} ({exc})")
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
) -> list[str]:
    """Run all validation checks on a single recipe file. Return list of errors."""
    errors = []

    # 1. YAML syntax
    syntax_err = check_yaml_syntax(path)
    if syntax_err:
        return [syntax_err]

    data = load_recipe(path)
    if not isinstance(data, dict):
        return [f"Expected a YAML mapping, got {type(data).__name__}"]

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

    # 7. Tool source check
    errors.extend(check_tool_sources(data))

    # 8. Shellcheck
    errors.extend(check_shellcheck(data))

    # 9. Test data URL reachability (optional)
    if check_urls:
        errors.extend(check_test_data_urls(data))

    return errors


def main():
    parser = argparse.ArgumentParser(description="Validate refgenie recipe YAML files")
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

        errors = validate_recipe(filepath, schema, check_urls=not args.no_url_check)
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
