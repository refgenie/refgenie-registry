# refgenie-registry

## Overview

Community-curated genome definitions and build recipes for refgenie.

[**refgenie**](https://refgenie.org) manages the storage, access, and sharing of
reference genome resources. It organizes genome data into versioned **assets**
(reference FASTAs, aligner indexes, annotation files, …), each identified by a
unique digest, so analysis tools can fetch exactly the resource they need
instead of every group rebuilding the same indexes by hand. Assets are served
publicly from a refgenie server:

- [**api.refgenie.org**](https://api.refgenie.org) — the **current** refgenie server. This is
  the live service that **this registry populates**: assets built here are published to it,
  and clients pull from it.
- [**refgenomes.databio.org**](http://refgenomes.databio.org) — the **legacy** refgenie server.
  It predates this registry and is fed by a different (older) mechanism; it remains online for
  existing users but is not driven by this repo.
- [**refgenie.org**](https://refgenie.org) — documentation and the `refgenie` CLI.

This repository is the **data layer behind the current service.** It is a monorepo of
reference-genome metadata, modeled after [bioconda](https://bioconda.github.io/):
contributors add small YAML files via pull request, automated checks plus a
maintainer validate them, and CI builds and indexes the resulting assets, which
are then published to **api.refgenie.org**. Rather than storing large genome files,
the registry stores the **definitions** (where a genome comes from, how to build
its assets) and a generated index of what has been built.

The repository is organized as:

- **`genomes/`** — YAML definitions of genome assemblies (community-contributed via PR)
- **`recipes/`** — YAML build recipes for creating genome assets (community-contributed via PR)
- **`index/`** — Auto-generated manifest of built assets (CI-only, no human edits — see [`index/README.md`](index/README.md))
- **`stores/`** — RefgetStore source manifests: content-addressable sequence collections, one PEP project per store (see [`stores/README.md`](stores/README.md))
- **`schema/`** — JSON Schemas that genome and recipe entries are validated against (see [`schema/README.md`](schema/README.md))
- **`tools/`** — Validation scripts and helpers (see [`tools/README.md`](tools/README.md))
- **`infra/`** — Operator-side build/deploy infrastructure, e.g. the Rivanna HPC layer ([`infra/rivanna/`](infra/rivanna/)); not needed to use the registry

## Contributing

Contributions happen through pull requests and build-request issues. There are
three ways to contribute; full field references, examples, and security
guidelines are in [CONTRIBUTING.md](./CONTRIBUTING.md).

### Add a genome

Define a new genome assembly. Fork, branch, and create
`genomes/<organism>/<assembly>.yaml`, then open a PR titled
"Add genome: \<organism\> \<assembly\>".

- **Required fields:** `name`, `organism.scientific_name`, `fasta.primary_url`, `fasta.checksum.sha256`
- The checksum is the SHA-256 of the **uncompressed** FASTA; use NCBI, Ensembl, or UCSC as the source.
- See [`genomes/human/hg38.yaml`](genomes/human/hg38.yaml) for a complete reference and [CONTRIBUTING.md § Adding a Genome](./CONTRIBUTING.md#adding-a-genome).

### Add a recipe

Define how to build an asset (e.g. an aligner index). Fork, branch, and create
`recipes/<asset_name>/recipe.yaml`, then open a PR titled
"Add recipe: \<asset_name\>".

- **Required fields:** `name`, `version`, `description`, `build.command`, `outputs`
- **Build variables:** `{fasta}`, `{output_dir}`, `{genome}`, `{threads}`
- Recipes must follow the [security guidelines](./CONTRIBUTING.md#adding-a-recipe) (no piped shell installs, no credentials, no access outside `{output_dir}`, tools from bioconda/conda-forge only).
- See [`recipes/bwa_index/recipe.yaml`](recipes/bwa_index/recipe.yaml) for a complete reference.

### Request a build

If a genome and recipe both exist but the asset hasn't been built yet,
[open a build request issue](../../issues/new?template=build_request.yml) naming
the genome and recipe. The bot validates both exist and queues the build.

### Validate locally before submitting

```bash
pip install -r tools/requirements.txt
python tools/validate_genome.py genomes/<organism>/<assembly>.yaml
python tools/validate_recipe.py recipes/<asset_name>/recipe.yaml
```

## Review Process

Contributions go through three layers of review:

1. **Programmatic validation** — schema checks, URL verification, security scanning (< 2 min)
2. **AI review** — Claude evaluates appropriateness, quality, and security (< 5 min)
3. **Human confirmation** — a maintainer reviews the AI summary and approves
