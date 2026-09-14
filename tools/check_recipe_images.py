#!/usr/bin/env python3
"""Assert each recipe's `docker_image` matches the bulker crate's pin for its tool.

Why this exists
---------------
refgenie1 runs a recipe's `custom_seek_keys` probes and its `--docker` builds
entirely inside the ONE image named in that recipe's `docker_image` field
(`docker run --rm --entrypoint sh <docker_image> -c <cmd>`). A recipe whose
`docker_image` names the wrong tool, or a stale version of the right one,
fails at build time -- either the probe comes back empty (`asset_name_required`)
or the command it runs is not on that image's PATH.

Each recipe's `docker_image` should therefore always equal the per-tool image
the bulker crate `databio/refgenie` pins for that recipe's one tool, so a
recipe with no tool command (only `cp`/`gzip`/coreutils) runs on the host
(`docker_image: null`), and a recipe with one tool command names that tool's
crate image, kept in sync as the crate's pins move.

What "the recipe's tool" means
-------------------------------
Derived the same way `check_crate_coverage.py` derives required commands
(leading token of every `command_templates` / `custom_seek_keys` statement),
minus shell builtins, minus bulker/coreutils commands, minus the
HOST_PROVIDED allowlist (plumbing that runs on the host even under bulker).
What's left is the recipe's real tool commands -- normally zero (host-only
recipe) or one (single-tool recipe).

Usage
-----
    python tools/check_recipe_images.py             # fetch crate from hub, check
    python tools/check_recipe_images.py --write      # also fix drifted pins in place
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from check_crate_coverage import (
    DEFAULT_CRATE,
    EXCLUDED,
    HOST_PROVIDED,
    REGISTRY_URL,
    SHELL_BUILTINS,
    fetch,
    leading_token,
    manifest_url,
    statements,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
RECIPES_DIR = REPO_ROOT / "recipes"

# Recipes that legitimately run more than one container. Their pins are not
# single crate-tool pins and are out of scope for this check. See the
# recipes_recipe_tool_images plan / the experimental bulker_manifest plan.
MULTI_CONTAINER = {"epilog_index", "salmon_partial_sa_index"}


def recipe_commands(recipe: dict) -> set[str]:
    """Leading command tokens used anywhere in one recipe (templates + seek keys)."""
    found: set[str] = set()
    for template in recipe.get("command_templates") or []:
        for statement in statements(template):
            token = leading_token(statement)
            if token:
                found.add(token)
    for expr in (recipe.get("custom_seek_keys") or {}).values():
        if not isinstance(expr, str):
            continue
        expr = expr.split("#", 1)[0]
        for statement in statements(expr):
            token = leading_token(statement)
            if token:
                found.add(token)
    return found


def coreutils_commands() -> set[str]:
    manifest = fetch(f"{REGISTRY_URL}/bulker/coreutils.yaml")
    return {e["command"] for e in manifest.get("commands") or []} | set(
        manifest.get("host_commands") or []
    )


def crate_tool_images(manifest: dict) -> dict[str, str]:
    """Map command -> docker_image, from the crate's OWN commands only.

    Deliberately does not resolve imports: imported crates (bulker/coreutils)
    are plumbing, filtered out before this is consulted.
    """
    return {
        e["command"]: e["docker_image"]
        for e in manifest.get("commands") or []
        if e.get("docker_image")
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--crate", default=DEFAULT_CRATE, help="crate to check against")
    ap.add_argument(
        "--write", action="store_true", help="rewrite drifted docker_image pins in place"
    )
    args = ap.parse_args()

    coreutils = coreutils_commands()
    manifest = fetch(manifest_url(args.crate))
    tool_images = crate_tool_images(manifest)

    infra = SHELL_BUILTINS | coreutils | set(HOST_PROVIDED)

    problems: list[str] = []
    fixes: list[tuple[Path, str, str]] = []  # (path, old_image, new_image)

    for recipe_path in sorted(RECIPES_DIR.glob("*/recipe.yaml")):
        rel = str(recipe_path.relative_to(REPO_ROOT))
        with open(recipe_path) as handle:
            recipe = yaml.safe_load(handle) or {}
        name = recipe.get("name", recipe_path.parent.name)
        if name in MULTI_CONTAINER:
            print(f"SKIP  {rel}: multi-container recipe, not checked here")
            continue

        docker_image = recipe.get("docker_image")
        tools = {c for c in recipe_commands(recipe) if c not in infra}

        if not tools:
            if docker_image is not None:
                problems.append(
                    f"{rel}: no tool commands found, but docker_image is {docker_image!r} "
                    f"(expected null)"
                )
            else:
                print(f"OK    {rel}: host-only, docker_image: null")
            continue

        verifiable = {c: tool_images[c] for c in tools if c in tool_images}
        unverifiable = {c for c in tools if c not in tool_images}

        if docker_image is None:
            problems.append(
                f"{rel}: uses tool command(s) {sorted(tools)} but docker_image is null"
            )
            continue

        images_needed = set(verifiable.values())
        if len(images_needed) > 1:
            problems.append(
                f"{rel}: tool commands {sorted(tools)} need different images "
                f"({verifiable}); this looks like a multi-container recipe -- "
                f"add it to MULTI_CONTAINER if so"
            )
            continue

        if not images_needed:
            # Every tool command this recipe uses is excluded from the crate
            # (e.g. cellranger). Nothing to check the pin against.
            excluded_note = ", ".join(sorted(unverifiable & set(EXCLUDED)))
            print(f"NOTE  {rel}: tool command(s) excluded from crate ({excluded_note}), pin unverified")
            continue

        expected_image = images_needed.pop()
        if docker_image != expected_image:
            problems.append(
                f"{rel}: docker_image is {docker_image!r}, crate pins {expected_image!r} for {sorted(verifiable)}"
            )
            fixes.append((recipe_path, docker_image, expected_image))
        else:
            print(f"OK    {rel}: {docker_image}")

    if fixes and args.write:
        for path, old, new in fixes:
            text = path.read_text()
            new_text = text.replace(f"docker_image: {old}\n", f"docker_image: {new}\n", 1)
            if new_text == text:
                print(f"WARN  could not rewrite {path} (docker_image line not found verbatim)")
                continue
            path.write_text(new_text)
            print(f"FIXED {path.relative_to(REPO_ROOT)}: {old} -> {new}")
        return 0

    if problems:
        print("\nFAIL: recipe docker_image pins are out of sync with the crate:")
        for problem in problems:
            print(f"  {problem}")
        print("\nFix by hand, or run with --write to apply the crate's pins.")
        return 1

    print(f"\nPASS: all {len(list(RECIPES_DIR.glob('*/recipe.yaml'))) - len(MULTI_CONTAINER)} checked recipes match the crate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
