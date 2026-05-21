# refgenie-registry

Community-curated genome definitions and build recipes for refgenie.

## Overview

A monorepo holding genome definitions, build recipes, and an auto-generated asset index — modeled after [bioconda](https://bioconda.github.io/).

- **`genomes/`** — YAML definitions of genome assemblies (community-contributed via PR)
- **`recipes/`** — YAML build recipes for creating genome assets (community-contributed via PR)
- **`index/`** — Auto-generated manifest of built assets (CI-only, no human edits)

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for how to add genomes, recipes, or request builds.

**Quick start:**
- Add a genome: create `genomes/<organism>/<assembly>.yaml`
- Add a recipe: create `recipes/<asset_name>/recipe.yaml`
- Request a build: [open an issue](../../issues/new?template=build_request.yml)

## Seed Content

### Genomes

| Name | Organism | Assembly | Accession | Source |
|------|----------|----------|-----------|--------|
| hg38 | Homo sapiens | GRCh38.p14 | GCF_000001405.40 | NCBI |
| hg19 | Homo sapiens | GRCh37.p13 | GCF_000001405.25 | NCBI |
| t2t-chm13 | Homo sapiens | T2T-CHM13v2.0 | GCF_009914755.1 | NCBI |
| mm10 | Mus musculus | GRCm38.p6 | GCF_000001635.26 | NCBI |
| mm39 | Mus musculus | GRCm39 | GCF_000001635.27 | NCBI |

### Recipes

| Name | Tool | Version | Purpose |
|------|------|---------|---------|
| fasta | curl/gunzip | (system) | Download and decompress reference FASTA |
| fasta_index | samtools | >=1.17 | FASTA index (.fai) and chrom.sizes |
| bwa_index | bwa | >=0.7.18 | BWA FM-index for short-read alignment |
| bowtie2_index | bowtie2 | >=2.5.0 | Bowtie2 FM-index for short-read alignment |

## Review Process

Contributions go through three layers of review:

1. **Programmatic validation** — schema checks, URL verification, security scanning
2. **AI review** — Claude evaluates appropriateness, quality, and security
3. **Human confirmation** — maintainer reviews AI summary and approves

## Schemas

- [`schema/genome.schema.yaml`](schema/genome.schema.yaml) — JSON Schema for genome entries
- [`schema/recipe.schema.yaml`](schema/recipe.schema.yaml) — JSON Schema for recipe entries

## Local Validation

```bash
pip install -r tools/requirements.txt
python tools/validate_genome.py genomes/human/hg38.yaml
python tools/validate_recipe.py recipes/bwa_index/recipe.yaml
```

## Links

- [refgenie.org](https://refgenie.org) — Documentation
- [github.com/refgenie](https://github.com/refgenie) — Organization
