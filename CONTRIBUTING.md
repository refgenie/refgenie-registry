# Contributing to refgenie-registry

## Overview

Contributions are welcome via pull requests. You can:

1. **Add a genome** — define a new genome assembly
2. **Add a recipe** — define how to build an asset (e.g., an aligner index)
3. **Request a build** — ask for a specific asset to be built for a genome

## Adding a Genome

1. Fork this repo and create a branch.
2. Create `genomes/<organism>/<assembly>.yaml` following the schema.
3. Open a PR with title: "Add genome: \<organism\> \<assembly\>"

**Required fields:** `name`, `description`, `organism.scientific_name`,
`organism.taxon_id`, `fasta` (at least one `sources[].url` or a `checksum`),
`seqcol` (a `digest`, or `compute: true`)

The schema is aligned to the [FAIR Headers Reference genome (FHR)](https://github.com/FAIR-bioHeaders/FHR-Specification)
vocabulary. The registry-native keys below are the source of truth for the FHR
core; the optional `fhr:` block is an escape hatch for pure-FHR provenance
fields that have no registry-native home. See [`schema/README.md`](schema/README.md)
for the field-by-field YAML → `.fhr.json` mapping.

**Example** (see `genomes/human/hg38.yaml` for a complete reference):

```yaml
name: my_genome                 # required; must equal the filename stem
aliases:
  - alternative_name            # optional

description: |                  # required
  Brief description of this genome assembly.

organism:
  scientific_name: Genus species  # required
  common_name: common name        # optional
  taxon_id: 12345                 # required NCBI Taxonomy ID (integer)

assembly:                       # optional but recommended
  source: NCBI
  accession: GCF_...
  level: chromosome

masking: not-masked             # optional: soft-masked | hard-masked | not-masked | unknown

fasta:                          # required (need sources[].url OR a checksum)
  sources:
    - provider: NCBI
      url: https://ftp.ncbi.nlm.nih.gov/...
  checksum:
    sha256: compute_on_registration   # sentinel, or the 64-hex sha256 of the uncompressed FASTA
    md5: <optional 32-hex md5 of the compressed FASTA>

seqcol:                         # required
  compute: true                 # or: digest: <seqcol digest>

fhr:                            # optional: pure-FHR provenance (all fields optional)
  license: CC0-1.0
  funding: NIH
  scholarly_article: 10.1038/nature...
  date_created: 2013-12-17
  metadata_author:
    - { name: Jane Doe, uri: https://orcid.org/0000-0002-1825-0097 }

metadata:                       # optional registry bookkeeping (not exported to FHR)
  added: 2026-01-01
  added_by: your_github_username
```

**Notes:**
- Provide at least one download URL under `fasta.sources[]` (each with an
  optional `provider`). Use NCBI, Ensembl, or UCSC as the source where possible.
- The checksum, when present, must be the SHA-256 of the **uncompressed** FASTA
  file (64 lowercase hex chars), or the sentinel `compute_on_registration`.
- `organism.taxon_id` is required; the exporter derives the resolvable
  `taxon.uri` (`https://identifiers.org/taxonomy:<id>`) from it.
- The `name` field must match the filename (without `.yaml`).

## Adding a Recipe

Recipes use refgenie's **native recipe model** — the single canonical model.
refgenie is the build system and consumes recipes directly (no conversion step).
A recipe needs two things: the recipe file itself, and a matching **asset class**
that types its output (defines the seek keys). Both reference asset classes by
name.

1. Fork this repo and create a branch.
2. **Write the recipe** at `recipes/<asset_name>/recipe.yaml`, including an
   `output_asset_class:` field naming the output asset class.
3. **Add or reference an asset class** at `asset_classes/<name>.yaml` for the
   class named in `output_asset_class:` (and for any `input_assets[].asset_class`).
   If a matching asset class already exists, just reference it; if not, contribute
   it in the same PR.
4. Open a PR with title: "Add recipe: \<asset_name\>"

**Required recipe fields:** `name`, `version`, `output_asset_class`, `command_templates`

**Example recipe** (see `recipes/bwa_index/recipe.yaml` for a complete reference):

```yaml
name: my_asset
version: 1.0.0
output_asset_class: my_asset
description: What this recipe builds and why.

input_files: {}
input_params: {}
input_assets:
  fasta:
    asset_class: fasta
    description: Reference FASTA asset
    default: fasta
    colocate:
      - source_key: fasta

# Container the command templates run in (use null for none).
docker_image: databio/refgenie

# Ordered shell command templates (Jinja, rendered by refgenie at build time).
command_templates:
  - toolname build {{values.output_folder}}/{{values.genome_digest}}.fa

# Map of name -> shell command; stdout becomes the value used for tagging.
custom_seek_keys:
  version: "toolname --version | awk '{print $2}'"
default_asset: "{{values.custom_seek_keys.version}}"

# Optional additive, non-runtime metadata (the builder ignores these):
tags:
  - alignment
outputs:
  - pattern: "*.ext"
    description: What this output file is
test:
  commands:
    - test -f {{values.output_folder}}/{{values.genome_digest}}.ext
metadata:
  author: your_github_username
  created: 2026-01-01
  license: MIT
```

**Example asset class** (`asset_classes/my_asset.yaml`) — the **source of truth**
for the asset's seek keys:

```yaml
name: my_asset
version: 1.0.0

description: |
  What this asset class represents.

# Named handles into the asset's files, addressable as genome/asset.<seek_key>.
seek_keys:
  index:
    value: "{genome}.ext"
    type: file
    description: The main output file

serving_modes:
  - drs
```

**Template values** available in `command_templates`:
`{{values.output_folder}}`, `{{values.genome_folder}}`,
`{{values.genome_digest}}`, `{{values.params["<name>"]}}`, and
`{{values.assets["<handle>"].seek_keys_dict["<seek_key>"]}}`.

> The recipe and asset class are separate layers: the recipe says *how to build*,
> the asset class says *what the output is* (its seek keys / serving modes). The
> recipe is already refgenie-native, so refgenie loads it directly: builds run via
> `refgenie generate snakefile` -> Snakemake -> `refgenie1 build` inside
> `docker_image`, with the asset tagged from `custom_seek_keys` + `default_asset`.
> The optional `outputs` globs are human-facing only and do NOT define seek keys.
> See [design.md](./design.md) and the recipe-model ADR for details.

**Security guidelines:**
- No `curl | bash` or `wget | sh`
- No hardcoded credentials or tokens
- No file access outside the output folder
- No `sudo` or root operations
- No background processes or daemons

## Requesting a Build

If a genome and recipe both exist but the asset hasn't been built yet:

1. [Open a build request issue](../../issues/new?template=build_request.yml)
2. Specify the genome name and recipe name
3. The bot will validate both exist and queue the build

## Local Validation

Before submitting a PR, validate your files locally:

```bash
pip install -r tools/requirements.txt
python tools/validate_genome.py genomes/<organism>/<assembly>.yaml
python tools/validate_recipe.py recipes/<asset_name>/recipe.yaml
```

Validation also checks that every recipe's `output_asset_class` and every
`input_assets[].asset_class` reference an existing `asset_classes/<name>.yaml`, so
add the asset class in the same PR if it doesn't already exist.

## Review Process

Your PR will go through three layers of review:

1. **Programmatic checks** — schema validation, URL verification, security scanning (< 2 min)
2. **AI review** — automated quality and security assessment (< 5 min)
3. **Human review** — a maintainer reviews and approves
