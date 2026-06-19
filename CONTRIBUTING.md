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

**Required fields:** `name`, `organism.scientific_name`, `fasta.primary_url`, `fasta.checksum.sha256`

**Example** (see `genomes/human/hg38.yaml` for a complete reference):

```yaml
name: my_genome
aliases:
  - alternative_name

description: |
  Brief description of this genome assembly.

organism:
  scientific_name: Genus species
  common_name: common name
  taxon_id: 12345

assembly:
  source: NCBI
  accession: GCF_...
  level: chromosome

fasta:
  primary_url: https://ftp.ncbi.nlm.nih.gov/...
  checksum:
    sha256: <sha256 of uncompressed FASTA>

seqcol:
  compute: true

metadata:
  added: 2026-01-01
  added_by: your_github_username
```

**Notes:**
- The checksum must be the SHA-256 of the **uncompressed** FASTA file.
- Use NCBI, Ensembl, or UCSC as the primary source.
- The `name` field must match the filename (without `.yaml`).

## Adding a Recipe

Recipes use a **bioconda-style format**. A recipe needs two things: the recipe
file itself, and a matching **asset class** that types its output (defines the
seek keys). Both reference asset classes by name.

1. Fork this repo and create a branch.
2. **Write the recipe** at `recipes/<asset_name>/recipe.yaml`, including a
   `produces:` field naming the output asset class.
3. **Add or reference an asset class** at `asset_classes/<name>.yaml` for the
   class named in `produces:` (and for any input class in
   `requires.assets[].name`). If a matching asset class already exists, just
   reference it; if not, contribute it in the same PR.
4. Open a PR with title: "Add recipe: \<asset_name\>"

**Required recipe fields:** `name`, `version`, `description`, `produces`, `build.command`, `outputs`

**Example recipe** (see `recipes/bwa_index/recipe.yaml` for a complete reference):

```yaml
name: my_asset
version: 1.0.0

description: |
  What this recipe builds and why.

# Output asset class this recipe produces. Must match an asset_classes/<name>.yaml.
produces: my_asset

requires:
  # Input asset classes. Each `name` must match an asset_classes/<name>.yaml.
  assets:
    - name: fasta
      description: Reference FASTA file
  tools:
    - name: toolname
      version: ">=1.0"
      source: bioconda

build:
  command: |
    toolname build {fasta} -o {output_dir}/{genome}

  resources:
    memory: 8GB
    disk: 10GB
    time: 2h

# CI / file validation only. These globs do NOT define seek keys — the asset
# class does (see below).
outputs:
  - pattern: "*.ext"
    description: What this output file is

test:
  commands:
    - test -f {output_dir}/{genome}.ext

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

**Build variables** (available in `build.command` and `test.commands`):
`{fasta}`, `{output_dir}`, `{genome}`, `{threads}`.

> The recipe and asset class are separate layers: the recipe says *how to build*,
> the asset class says *what the output is* (its seek keys / serving modes). At
> import time a converting importer translates both into refgenie1
> (`build.command` -> command templates, `requires.assets` -> input assets,
> `produces` -> output asset class, seek keys from the asset class). See
> [design.md](./design.md) and the recipe-format ADR for details.

**Security guidelines:**
- No `curl | bash` or `wget | sh`
- No hardcoded credentials or tokens
- No file access outside `{output_dir}`
- Tools must come from approved sources (bioconda, conda-forge)
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

Validation also checks that every recipe's `produces` and
`requires.assets[].name` reference an existing `asset_classes/<name>.yaml`, so add
the asset class in the same PR if it doesn't already exist.

## Review Process

Your PR will go through three layers of review:

1. **Programmatic checks** — schema validation, URL verification, security scanning (< 2 min)
2. **AI review** — automated quality and security assessment (< 5 min)
3. **Human review** — a maintainer reviews and approves
