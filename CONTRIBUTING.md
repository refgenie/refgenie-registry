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

1. Fork this repo and create a branch.
2. Create `recipes/<asset_name>/recipe.yaml` following the schema.
3. Open a PR with title: "Add recipe: \<asset_name\>"

**Required fields:** `name`, `version`, `description`, `build.command`, `outputs`

**Example** (see `recipes/bwa_index/recipe.yaml` for a complete reference):

```yaml
name: my_asset
version: 1.0.0

description: |
  What this recipe builds and why.

requires:
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

**Build variables:** `{fasta}`, `{output_dir}`, `{genome}`, `{threads}`

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

## Review Process

Your PR will go through three layers of review:

1. **Programmatic checks** — schema validation, URL verification, security scanning (< 2 min)
2. **AI review** — automated quality and security assessment (< 5 min)
3. **Human review** — a maintainer reviews and approves
