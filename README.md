# refgenie-registry

## Overview

Community-curated genome definitions and build recipes for refgenie.

> **This registry is the single source of truth for refgenie1 recipes, asset
> classes, and genomes** — and the one place to contribute them. It **replaces
> the legacy [`recipes`](https://github.com/refgenie/recipes) repository**, which
> served the end-of-life refgenie (`refgenie` / `refgenconf` / `refgenieserver`)
> and is not used by refgenie1. refgenie1 builds directly from this registry
> (the nightly `import_recipes` loader syncs it into the build catalog), so new
> recipes and build requests belong here, not in `recipes`.

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
reference-genome metadata, organized like [bioconda](https://bioconda.github.io/) for
contribution flow (small YAML files via pull request): contributors add files,
automated checks plus a maintainer validate them, and CI builds and indexes the
resulting assets, which are then published to **api.refgenie.org**. Rather than storing
large genome files, the registry stores the **definitions** (where a genome comes from,
how to build its assets) and a generated index of what has been built.

**refgenie is the build system.** Recipes are written in refgenie's own native
recipe model and consumed directly — there is no separate registry format and no
conversion step. `refgenie generate snakefile` renders a Snakemake workflow from
the recipes; each rule runs `refgenie1 build` inside the recipe's `docker_image`,
executing its `command_templates` and tagging the asset from `custom_seek_keys` +
`default_asset`. Asset classes (`asset_classes/`) are the source of truth for seek
keys. See [design.md](./design.md) for the full data model.

The repository is organized as:

- **`genomes/`** — YAML definitions of genome assemblies (community-contributed via PR)
- **`recipes/`** — refgenie-native YAML build recipes for creating genome assets (community-contributed via PR)
- **`asset_classes/`** — Typed asset-class definitions: the **source of truth** for an asset's seek keys and serving modes. Recipes reference these by name (community-contributed via PR)
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

Define how to build an asset (e.g. an aligner index) in refgenie's native recipe
model. Fork, branch, create `recipes/<asset_name>/recipe.yaml`, add or reference
a matching `asset_classes/<name>.yaml`, then open a PR titled
"Add recipe: \<asset_name\>".

- **Required recipe fields:** `name`, `version`, `output_asset_class`, `command_templates`
- **`output_asset_class`** names the output asset class; `input_assets[].asset_class` names input asset classes. Both must reference an existing `asset_classes/<name>.yaml` (the source of truth for seek keys). Optional `outputs` globs are human-facing only.
- **Template values:** `{{values.output_folder}}`, `{{values.genome_digest}}`, `{{values.params["<name>"]}}`, `{{values.assets["<handle>"].seek_keys_dict["<seek_key>"]}}`
- Recipes must follow the [security guidelines](./CONTRIBUTING.md#adding-a-recipe) (no piped shell installs, no credentials, no access outside the output folder, no sudo/daemons).
- See [`recipes/bwa_index/recipe.yaml`](recipes/bwa_index/recipe.yaml) for a complete reference and [design.md](./design.md) for the data model.

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
