#!/usr/bin/env python3
"""Move each recipe's container pin to the newest biocontainers tag.

A recipe's `docker_image` is the only record of which image builds its asset:
refgenie runs the recipe there (Docker on a laptop, Apptainer on Rivanna), both
to build and to read the tool version the asset is named after. This keeps
those pins fresh.

For each recipe whose image is `quay.io/biocontainers/<package>:<tag>`, ask
quay.io for every tag of that package and pick the newest: the highest version
(the part before `--`), then the highest build number, then the highest
`pyXXX` variant. A pin never moves backwards. With `--write`, the new pin is
written into the recipe and the recipe `version` is bumped, so the nightly
imports it as a new recipe version.

A pin that moves to a new tool version renames the assets that tool builds
(the asset name is the tool version), forcing rebuilds, so this never runs
unattended: the monthly workflow opens a pull request for a person to merge.

Images outside biocontainers have no such tag scheme. Their recipes are listed
in HOLD with the reason, and skipped. A recipe that is neither biocontainers
nor in HOLD is an error, so a new one cannot slip by unchecked.

Usage
-----
    python tools/update_recipe_images.py           # report what would change
    python tools/update_recipe_images.py --write   # also rewrite the recipes
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
RECIPES_DIR = REPO_ROOT / "recipes"

BIOCONTAINERS = "quay.io/biocontainers/"
QUAY_TAGS = "https://quay.io/api/v1/repository/biocontainers/{package}/tag/"

# Recipes whose image is left alone, with the reason.
HOLD = {
    "cellranger_reference": "not a biocontainers image (quay.io/xujishu/cellranger)",
    "epilog_index": "runs several tools; waits on the multi-image recipe plan",
    "salmon_partial_sa_index": "runs several tools; waits on the multi-image recipe plan",
}

# Version strings that mark a pre-release. Never picked.
PRERELEASE = re.compile(r"(alpha|beta|rc|dev|pre)", re.IGNORECASE)


def tag_key(tag: str) -> tuple | None:
    """Sort key for a biocontainers tag `<version>--<hash>_<build>`, or None to skip it.

    Version parts compare as numbers where they are numbers, so 0.7.19 beats
    0.7.9, and a trailing letter sorts after the bare number (STAR 2.7.11b
    beats 2.7.11a beats 2.7.11).
    """
    version, sep, rest = tag.partition("--")
    if not sep or PRERELEASE.search(version):
        return None
    match = re.search(r"_(\d+)$", rest)
    if not match:
        return None
    tokens = re.findall(r"\d+|[A-Za-z]+", version)
    parts = tuple((1, int(t)) if t.isdigit() else (0, t) for t in tokens)
    python = re.search(r"py(\d+)", rest)
    return (parts, int(match.group(1)), int(python.group(1)) if python else 0)


def newest(current: str, tags: list[str]) -> str:
    """The tag to pin: the newest of `tags`, or `current` if nothing is newer."""
    best, best_key = current, tag_key(current)
    if best_key is None:
        return current
    for tag in tags:
        key = tag_key(tag)
        if key is not None and key > best_key:
            best, best_key = tag, key
    return best


def quay_tags(package: str) -> list[str]:
    """Every active tag of a biocontainers package."""
    tags: list[str] = []
    for page in range(1, 51):
        url = f"{QUAY_TAGS.format(package=package)}?limit=100&onlyActiveTags=true&page={page}"
        headers = {"User-Agent": "refgenie-registry-pins/1.0"}
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.load(response)
        tags += [t["name"] for t in body.get("tags", [])]
        if not body.get("has_additional"):
            break
    return tags


def bump_patch(version: str) -> str:
    """0.0.2 -> 0.0.3."""
    parts = version.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    return ".".join(parts)


def rewrite(path: Path, old_image: str, new_image: str, old_version: str) -> str:
    """Write the new pin and a bumped `version` into the recipe; returns the new version."""
    text = path.read_text()
    new_version = bump_patch(old_version)
    pin_line = rf"^docker_image:\s*{re.escape(old_image)}\s*$"
    version_line = rf"^version:\s*['\"]?{re.escape(old_version)}['\"]?\s*$"
    text, pins = re.subn(pin_line, f"docker_image: {new_image}", text, flags=re.M)
    text, versions = re.subn(version_line, f"version: {new_version}", text, flags=re.M)
    if pins != 1 or versions != 1:
        raise SystemExit(f"ERROR: could not rewrite {path}: docker_image or version line not found")
    path.write_text(text)
    return new_version


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--write", action="store_true", help="rewrite pins and bump recipe versions")
    args = ap.parse_args()

    changes: list[str] = []
    problems: list[str] = []
    for path in sorted(RECIPES_DIR.glob("*/recipe.yaml")):
        recipe = yaml.safe_load(path.read_text()) or {}
        name, image = recipe.get("name", path.parent.name), recipe.get("docker_image")
        if not image:
            continue
        if name in HOLD:
            print(f"HOLD  {name}: {image} ({HOLD[name]})")
            continue
        if not image.startswith(BIOCONTAINERS) or ":" not in image:
            problems.append(
                f"{name}: {image} is not a pinned biocontainers image; add it to HOLD with a reason"
            )
            continue
        package, current = image[len(BIOCONTAINERS) :].split(":", 1)
        latest = newest(current, quay_tags(package))
        if latest == current:
            print(f"OK    {name}: {image}")
            continue
        new_image = f"{BIOCONTAINERS}{package}:{latest}"
        line = f"{name}: {current} -> {latest}"
        if args.write:
            new_version = rewrite(path, image, new_image, str(recipe["version"]))
            line += f" (recipe {recipe['version']} -> {new_version})"
        print(f"BUMP  {line}")
        changes.append(line)

    if problems:
        print("\nFAIL:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print(f"\n{len(changes)} pin(s) {'updated' if args.write else 'out of date'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
