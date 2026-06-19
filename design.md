# refgenie-registry design

This document describes the design of the refgenie-registry: a community-curated,
bioconda-style monorepo of genome definitions, build recipes, and asset-class
type definitions that are imported into [refgenie1](https://refgenie.org).

## 1. Goals

- Make community contribution easy and familiar (a bioconda/biocontainers-style
  recipe format, no Jinja or internal field knowledge required).
- Keep a clean separation between **type** (what an asset *is*, addressed by
  named seek keys) and **build** (how an asset is *produced*).
- Import registry content into refgenie1's native model via a converting
  importer, so refgenie1's internal model stays unchanged.

## 2. Repository layout

- **`genomes/`** — YAML definitions of genome assemblies.
- **`recipes/`** — Bioconda-style YAML build recipes (one per asset class).
- **`asset_classes/`** — Typed asset-class definitions. The **source of truth**
  for seek keys and serving modes (see §3.4).
- **`schema/`** — JSON Schemas that genome, recipe, and asset-class entries are
  validated against.
- **`tools/`** — Validation scripts, the converting importer, and helpers.
- **`index/`** — Auto-generated manifest of built assets (CI-only).
- **`stores/`** — RefgetStore source manifests.
- **`infra/`** — Operator-side build/deploy infrastructure.

## 3. Data model

### 3.1 Genomes

A genome entry (`genomes/<organism>/<assembly>.yaml`) describes a reference
assembly: its names/aliases, organism, assembly accession, and the source FASTA
(URL + checksum). Genomes are the inputs that recipes build assets *for*.

### 3.2 Assets

An **asset** is a versioned bundle of files built for a genome by running a
recipe (e.g. a `bwa_index` for `hg38`). Each asset belongs to an **asset class**
(its type) and exposes its files through named **seek keys** defined by that
class.

### 3.3 Recipes (bioconda-style format)

Recipes are written in a **bioconda-style format**. This is the canonical
registry recipe format: it is the human-facing contribution layer and is
designed to be familiar to bioconda contributors. A recipe declares the tools
and input assets it needs, the build command, the asset class it produces, and
validation tests.

```yaml
name: bwa_index
version: 0.0.1

description: |
  Genome index for Burrows-Wheeler Alignment Tool, produced with bwa index.

tags:
  - alignment
  - dna-seq

# The output asset class this recipe produces. Must reference an existing
# asset class (asset_classes/<name>.yaml). This is the link from build -> type.
produces: bwa_index

requires:
  # Input asset classes this recipe consumes. Each `name` must reference an
  # existing asset class (asset_classes/<name>.yaml).
  assets:
    - name: fasta
      description: FASTA asset for the genome
  tools:
    - name: bwa
      version: ">=0.7.18"
      source: bioconda

build:
  command: |
    bwa index {output_dir}/{genome}.fa
  resources:
    memory: 8GB
    disk: 10GB
    time: 2h

# File-level expectations, used for CI / build validation only (NOT the type
# system). Seek keys come from the asset class, not from these globs.
outputs:
  - pattern: "*.bwt"
    description: BWA index - Burrows-Wheeler transform
  - pattern: "*.sa"
    description: BWA index - suffix array

test:
  commands:
    - test -f {output_dir}/{genome}.fa.bwt
    - test -f {output_dir}/{genome}.fa.sa

metadata:
  author: your_github_username
  created: 2026-01-01
  license: BSD-2-Clause
```

**Build variables** available in `build.command` and `test.commands`:
`{fasta}`, `{output_dir}`, `{genome}`, `{threads}`.

Key fields and their roles:

- **`produces`** — the output asset class (the recipe's *type* link). Required.
  Must reference an existing `asset_classes/<name>.yaml`.
- **`requires.assets[].name`** — input asset classes consumed by the build. Each
  must reference an existing `asset_classes/<name>.yaml`.
- **`requires.tools`** — external tools (from bioconda/conda-forge/etc.).
- **`build.command`** — the shell command that produces the asset's files.
- **`outputs`** — glob patterns used for **CI / file validation only**. They are
  *not* the type system and do *not* define seek keys.
- **`test`** — commands that validate a successful build.

> **Note:** `produces`, `requires`, `build`, `outputs`, and `test` are the
> canonical recipe fields — they are **not** a legacy format. The bioconda-style
> recipe is the contribution surface; refgenie1's native model is produced from
> it by the importer (§3.5).

### 3.4 Asset classes (the type layer)

An asset-class definition (`asset_classes/<name>.yaml`) is a **typed** definition
and the **source of truth** for an asset's seek keys and serving modes. Recipes
reference asset classes but never redefine seek keys; both `produces` (output)
and `requires.assets[].name` (inputs) point at asset-class names.

```yaml
name: bwa_index
version: 0.0.1

description: |
  BWA FM-index for a reference genome.

# Named handles into the asset's files. Seek keys are addressable as
# `genome/asset.<seek_key>` and are defined ONLY here.
seek_keys:
  bwt:
    value: "{genome}.fa.bwt"
    type: file
    description: Burrows-Wheeler transform
  sa:
    value: "{genome}.fa.sa"
    type: file
    description: Suffix array

# How this asset class is served (e.g. via DRS / data channels).
serving_modes:
  - drs
```

Because seek keys live on the asset class:

- Many recipes/versions can produce the same asset class without re-specifying
  its seek keys.
- Seek-key addressing (`genome/asset.<seek_key>`) works regardless of which
  recipe built the asset.
- A recipe's `outputs` globs can change without affecting how the asset is typed
  or addressed.

The exact required fields and allowed types are defined by
`schema/asset_class.schema.yaml`.

### 3.5 Recipe -> refgenie1 conversion (the importer)

Registry content is converted into refgenie1's native model by a **converting
importer** (`tools/import_recipes.py`). refgenie1's internal model is never
authored directly; it is produced from the registry. The importer loads
`asset_classes/` first, then translates each bioconda recipe:

| Registry (bioconda format)            | refgenie1 (native model)                                   |
|---------------------------------------|------------------------------------------------------------|
| `asset_classes/<name>.yaml`           | `AssetClass` (via `AssetClassManager.add()`)               |
| asset class `seek_keys`               | the asset class's seek keys (source of truth)              |
| `build.command`                       | `command_templates`                                        |
| `requires.assets[].name`              | `input_assets` (with `colocate` when the command          |
|                                       | references parent files in `{output_dir}`)                 |
| `produces`                            | `output_asset_class`                                       |
| `outputs`                             | *(not imported — CI/file validation only)*                 |

The importer adds asset classes via `AssetClassManager.add()` and recipes via
`RecipeManager.add()`. Seek keys are taken from the asset class, **never** derived
from `outputs`. Conversion happens at import time so refgenie1 keeps its native
internal model unchanged while the registry remains the human-facing layer.

## 4. Validation and review

Contributions are validated against the schemas in `schema/` (locally and in
CI). Validation also checks that every recipe's `produces` and
`requires.assets[].name` reference an existing asset class. PRs then go through AI
review and human confirmation.

## 5. Related decisions

The recipe/asset-class hybrid described here is the decision recorded in the ADR
**Registry recipe format: bioconda-style recipes referencing typed asset
classes** (`registry_recipe_asset_class_format_adr.md`). See that ADR for the
problem statement, alternatives considered, and consequences.
