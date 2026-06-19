#!/usr/bin/env bash
# Identify changed files of a given type in the current branch vs main.
# Usage: ./tools/changed_files.sh genomes  => lists changed genome YAML files
#        ./tools/changed_files.sh recipes  => lists changed recipe YAML files

set -euo pipefail

TYPE="${1:?Usage: changed_files.sh <genomes|recipes>}"
BASE="${2:-main}"

case "$TYPE" in
  genomes)
    git diff --name-only --diff-filter=ACMR "$BASE"...HEAD -- 'genomes/**/*.yaml'
    ;;
  recipes)
    git diff --name-only --diff-filter=ACMR "$BASE"...HEAD -- 'recipes/**/recipe.yaml'
    ;;
  *)
    echo "Unknown type: $TYPE (expected 'genomes' or 'recipes')" >&2
    exit 1
    ;;
esac
