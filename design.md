# refgenie-registry design

This document describes the design of the refgenie-registry: a community-curated
monorepo of genome definitions, build recipes, and asset-class type definitions
consumed directly by [refgenie](https://refgenie.org).

**refgenie is the build system.** A recipe is written in refgenie's own native
model and is consumed directly by refgenie — there is no separate "registry
format" and no bioconda->refgenie translation step. `refgenie generate snakefile`
renders a Snakemake workflow from the recipes; each generated rule runs
`refgenie1 build <genome>/<asset>:<tag>`, which executes the recipe's
`command_templates` inside its `docker_image` and derives the asset tag from
`custom_seek_keys` + `default_asset`. There is no conda in the build path.

## 1. Goals

- Store recipes in **one canonical model** — refgenie's native recipe model — so
  refgenie consumes registry content directly with no lossy conversion.
- Keep a clean separation between **type** (what an asset *is*, addressed by
  named seek keys, defined by the asset class) and **build** (how an asset is
  *produced*, defined by the recipe).
- Allow optional **additive, non-runtime metadata** (tags, tests, output
  descriptions, provenance) to ride alongside recipes for CI and UX; the builder
  ignores these.

## 2. Repository layout

- **`genomes/`** — YAML definitions of genome assemblies.
- **`recipes/`** — refgenie-native YAML build recipes (one per asset class).
- **`asset_classes/`** — Typed asset-class definitions. The **source of truth**
  for seek keys and serving modes (see §3.4).
- **`schema/`** — JSON Schemas that genome, recipe, and asset-class entries are
  validated against.
- **`tools/`** — Validation scripts and helpers.
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

### 3.3 Recipes (refgenie-native model)

Recipes are written in **refgenie's native recipe model** — the single canonical
model. refgenie is the build system and consumes these recipes directly; there is
no separate "registry format" and no conversion step. A recipe declares the asset
class it produces, the input assets/files/params it consumes, the container it
runs in, the ordered command templates that build the asset, and how the asset is
tagged.

```yaml
name: bwa_index
version: 0.1.0
output_asset_class: bwa_index
description: Genome index for Burrows-Wheeler Alignment Tool, produced with bwa index
input_files: {}
input_params: {}
input_assets:
  fasta:
    asset_class: fasta
    description: fasta asset for genome
    default: fasta
    colocate:
      - source_key: fasta
docker_image: databio/refgenie
command_templates:
  - bwa index {{values.output_folder}}/{{values.genome_digest}}.fa
custom_seek_keys:
  version: "bwa 2>&1 | grep Version | cut -d' ' -f2 | awk -F- '{print $1}'"
default_asset: "{{values.custom_seek_keys.version}}"

# Optional additive, non-runtime metadata (the builder ignores these):
tags:
  - alignment
  - dna-seq
outputs:
  - pattern: "*.bwt"
    description: BWA index - Burrows-Wheeler transform
test:
  commands:
    - test -f {{values.output_folder}}/{{values.genome_digest}}.fa.bwt
metadata:
  author: your_github_username
  created: 2026-01-01
  license: BSD-2-Clause
```

**Template values** available in `command_templates` (Jinja, rendered by
refgenie at build time) include `{{values.output_folder}}`,
`{{values.genome_folder}}`, `{{values.genome_digest}}`,
`{{values.params["<name>"]}}`, and
`{{values.assets["<handle>"].seek_keys_dict["<seek_key>"]}}`.

Key fields and their roles:

- **`name`** / **`version`** — recipe identifier and semantic version. Required.
- **`output_asset_class`** — the output asset class (the recipe's *type* link).
  Required. Must reference an existing `asset_classes/<name>.yaml`.
- **`command_templates`** — ordered list of shell command templates that produce
  the asset's files. Required (at least one).
- **`input_assets`** — map of handle -> `{asset_class, default?, description?,
  colocate?}`. Each `asset_class` must reference an existing
  `asset_classes/<name>.yaml`. `colocate` copies named seek-key files from the
  input asset into the output folder before the commands run.
- **`input_files`** / **`input_params`** — user-supplied files and scalar build
  parameters.
- **`docker_image`** — container the command templates run in (`null` to run
  without a per-recipe container).
- **`custom_seek_keys`** — map of name -> shell command whose stdout becomes a
  value (typically a tool version) used for tagging.
- **`default_asset`** — template resolving to the asset tag/name (often
  `"{{values.custom_seek_keys.version}}"` or a literal like `"default"`).

**Optional additive, non-runtime fields** (the builder ignores them; they exist
for CI, provenance, and UX): `tags`, `outputs` (human-facing output
descriptions; *not* the type system and *not* seek keys), `test`, `resources`,
`metadata`. Empty maps may be written as `{}` or `null`.

### 3.4 Asset classes (the type layer)

An asset-class definition (`asset_classes/<name>.yaml`) is a **typed** definition
and the **source of truth** for an asset's seek keys and serving modes. Recipes
reference asset classes but never redefine seek keys; both `output_asset_class`
(output) and `input_assets[].asset_class` (inputs) point at asset-class names.

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

### 3.5 How recipes are built (refgenie is the build system)

There is **no conversion step**. Recipes are already in refgenie's native model,
so refgenie loads them directly (any "import" is a thin loader, not a
translator). Builds run entirely through refgenie:

1. `refgenie generate snakefile` iterates the recipes and renders a Snakemake
   workflow (`refgenie1/refgenie/snakefile/generate.py`).
2. Each generated rule runs `refgenie1 build <genome>/<asset>:<tag>`.
3. `refgenie1 build` executes the recipe's `command_templates` inside its
   `docker_image`, colocating any `input_assets[].colocate` files first.
4. The asset's tag/name is derived from `custom_seek_keys` + `default_asset`.

Seek keys are taken from the asset class, **never** derived from a recipe's
optional `outputs`. There is no conda in the build path — `docker_image` +
`command_templates` are the whole build contract.

## 4. Validation and review

Contributions are validated against the schemas in `schema/` (locally and in
CI). Validation hard-errors if a recipe's `output_asset_class` or any
`input_assets[].asset_class` does not reference an existing
`asset_classes/<name>.yaml`. PRs then go through AI review and human
confirmation.

## 5. Related decisions

The single canonical refgenie-native recipe model described here is the decision
recorded in the ADR **Single canonical recipe model: refgenie-native (refgenie is
the build system)** (`single_recipe_model_refgenie_native_adr.md`). It supersedes
the earlier "bioconda-style recipes referencing typed asset classes" ADR. See the
current ADR for the problem statement, alternatives considered, and consequences.
