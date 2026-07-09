#!/usr/bin/env python3
"""Validate a published refgenie data channel.

Checks a channel directory (built by ``build_channel.py``) or a remote channel
base URL: every file listed in ``index.yaml`` exists, parses as YAML, and
carries the fields the refgenie1 client requires.

    asset classes: name, serving_modes, seek_keys
    recipes:       name, version, output_asset_class, command_templates

Usage:
    python tools/validate_channel.py channel
    python tools/validate_channel.py https://refgenie.github.io/refgenie-registry/
"""

import argparse
import sys
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import yaml

ASSET_CLASS_REQUIRED = ("name", "serving_modes", "seek_keys")
RECIPE_REQUIRED = ("name", "version", "output_asset_class", "command_templates")
UA = {"User-Agent": "refgenie-channel-validator/1.0"}


def _is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


def _load(base: str, relpath: str) -> dict:
    """Load a YAML file from a local dir or a remote base URL."""
    if _is_url(base):
        url = urljoin(base.rstrip("/") + "/", relpath)
        with urlopen(Request(url, headers=UA), timeout=30) as resp:
            return yaml.safe_load(resp.read())
    return yaml.safe_load((Path(base) / relpath).read_text())


def _check_fields(doc: dict, required: tuple[str, ...], where: str, errors: list[str]) -> None:
    if not isinstance(doc, dict):
        errors.append(f"{where}: not a YAML mapping")
        return
    for key in required:
        if key not in doc:
            errors.append(f"{where}: missing required field '{key}'")


def validate(base: str) -> list[str]:
    errors: list[str] = []
    try:
        index = _load(base, "index.yaml")
    except Exception as e:  # noqa: BLE001 - report any load failure
        return [f"index.yaml: failed to load ({e})"]

    for section, required in (("asset_class", ASSET_CLASS_REQUIRED),
                              ("recipe", RECIPE_REQUIRED)):
        block = (index or {}).get(section) or {}
        files = block.get("files") or []
        d = block.get("dir", "")
        if not files:
            errors.append(f"index.yaml: section '{section}' lists no files")
        for f in files:
            relpath = f"{d}/{f}" if d else str(f)
            try:
                doc = _load(base, relpath)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{relpath}: failed to load ({e})")
                continue
            _check_fields(doc, required, relpath, errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("channel", help="Local channel dir or remote channel base URL")
    args = parser.parse_args()

    errors = validate(args.channel)
    if errors:
        print(f"Channel validation FAILED ({len(errors)} problem(s)):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"Channel OK: {args.channel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
