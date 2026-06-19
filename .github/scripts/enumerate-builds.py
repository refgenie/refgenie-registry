#!/usr/bin/env python3
"""Enumerate genome/recipe build pairs based on changed files.

Given a list of changed files (from git diff), determines which genome+recipe
combinations need to be built. Logic:
  - If a genome was added/changed, queue builds against ALL recipes.
  - If a recipe was added/changed, queue builds against ALL genomes.
  - Skip pairs that already have a current index entry (recipe SHA matches).

Outputs a GitHub Actions matrix JSON.
"""

import argparse
import hashlib
import json
import os
import re
import sys

import yaml


def parse_args():
    parser = argparse.ArgumentParser(description="Enumerate builds from changed files")
    parser.add_argument(
        "--changed-files",
        required=True,
        help="Comma-separated list of changed file paths",
    )
    parser.add_argument(
        "--genomes-dir", default="genomes", help="Path to genomes directory"
    )
    parser.add_argument(
        "--recipes-dir", default="recipes", help="Path to recipes directory"
    )
    parser.add_argument(
        "--index-dir", default="index", help="Path to index directory"
    )
    parser.add_argument(
        "--output-json", default="matrix.json", help="Output file for matrix JSON"
    )
    return parser.parse_args()


def file_sha256(path):
    """Compute SHA-256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def discover_genomes(genomes_dir):
    """Find all genome YAML files. Returns list of (genome_name, genome_path)."""
    genomes = []
    if not os.path.isdir(genomes_dir):
        return genomes
    for root, dirs, files in os.walk(genomes_dir):
        for f in files:
            if f.endswith(".yaml") or f.endswith(".yml"):
                path = os.path.join(root, f)
                with open(path) as fh:
                    data = yaml.safe_load(fh)
                name = data.get("name", os.path.splitext(f)[0])
                genomes.append((name, path))
    return genomes


def discover_recipes(recipes_dir):
    """Find all recipe YAML files. Returns list of (recipe_name, recipe_path)."""
    recipes = []
    if not os.path.isdir(recipes_dir):
        return recipes
    for entry in os.listdir(recipes_dir):
        recipe_file = os.path.join(recipes_dir, entry, "recipe.yaml")
        if os.path.isfile(recipe_file):
            with open(recipe_file) as fh:
                data = yaml.safe_load(fh)
            name = data.get("name", entry)
            recipes.append((name, recipe_file))
    return recipes


def classify_resources(recipe_path):
    """Classify a recipe as small/medium/large based on memory requirements."""
    with open(recipe_path) as f:
        data = yaml.safe_load(f)
    memory_str = data.get("build", {}).get("resources", {}).get("memory", "4GB")
    # Parse memory string like "8GB", "2GB", "512MB"
    match = re.match(r"(\d+)\s*(GB|MB|TB)", memory_str, re.IGNORECASE)
    if not match:
        return "medium"
    value = int(match.group(1))
    unit = match.group(2).upper()
    if unit == "MB":
        gb = value / 1024
    elif unit == "TB":
        gb = value * 1024
    else:
        gb = value
    if gb <= 4:
        return "small"
    elif gb <= 16:
        return "medium"
    else:
        return "large"


def get_index_recipe_sha(index_dir, genome_name, recipe_name):
    """Check if an index entry exists and return its recipe_sha, or None."""
    index_file = os.path.join(index_dir, genome_name, f"{recipe_name}.yaml")
    if not os.path.isfile(index_file):
        return None
    with open(index_file) as f:
        data = yaml.safe_load(f)
    return data.get("recipe_sha")


def main():
    args = parse_args()

    changed_files = [f.strip() for f in args.changed_files.split(",") if f.strip()]

    all_genomes = discover_genomes(args.genomes_dir)
    all_recipes = discover_recipes(args.recipes_dir)

    # Determine which genomes and recipes changed
    changed_genomes = set()
    changed_recipes = set()

    for cf in changed_files:
        # Normalize path separators
        cf = cf.replace("\\", "/")
        if cf.startswith(args.genomes_dir + "/"):
            # Find which genome this file belongs to
            for gname, gpath in all_genomes:
                if os.path.normpath(cf) == os.path.normpath(gpath):
                    changed_genomes.add(gname)
                    break
        elif cf.startswith(args.recipes_dir + "/"):
            for rname, rpath in all_recipes:
                if os.path.normpath(cf) == os.path.normpath(rpath):
                    changed_recipes.add(rname)
                    break

    # Build the matrix of (genome, recipe) pairs
    build_pairs = []

    # If a genome changed, pair it with ALL recipes
    for gname, gpath in all_genomes:
        if gname in changed_genomes:
            for rname, rpath in all_recipes:
                build_pairs.append((gname, gpath, rname, rpath))

    # If a recipe changed, pair it with ALL genomes
    for rname, rpath in all_recipes:
        if rname in changed_recipes:
            for gname, gpath in all_genomes:
                # Avoid duplicates (genome+recipe both changed)
                if (gname, gpath, rname, rpath) not in build_pairs:
                    build_pairs.append((gname, gpath, rname, rpath))

    # Filter out pairs that already have a current index entry
    matrix_includes = []
    for gname, gpath, rname, rpath in build_pairs:
        current_sha = file_sha256(rpath)
        index_sha = get_index_recipe_sha(args.index_dir, gname, rname)
        if index_sha == current_sha:
            print(f"SKIP {gname}/{rname}: index entry is current (SHA match)")
            continue

        size_class = classify_resources(rpath)
        runs_on = "ubuntu-latest"
        if size_class == "large":
            runs_on = "ubuntu-latest-16core"

        matrix_includes.append(
            {
                "genome_name": gname,
                "genome_path": gpath,
                "recipe_name": rname,
                "recipe_path": rpath,
                "size_class": size_class,
                "runs_on": runs_on,
            }
        )

    matrix = {"include": matrix_includes}

    print(f"Enumerated {len(matrix_includes)} build(s):")
    for item in matrix_includes:
        print(f"  {item['genome_name']}/{item['recipe_name']} ({item['size_class']})")

    with open(args.output_json, "w") as f:
        json.dump(matrix, f, indent=2)

    # Also write to GITHUB_OUTPUT if available
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"matrix={json.dumps(matrix)}\n")
            f.write(f"has_builds={'true' if matrix_includes else 'false'}\n")

    if not matrix_includes:
        print("No builds needed.")


if __name__ == "__main__":
    main()
