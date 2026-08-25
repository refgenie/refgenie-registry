# tools/

Validation scripts and helpers used by CI and by contributors before opening a PR.

| File | Purpose |
|------|---------|
| `validate_genome.py` | Validate genome YAML files against [`schema/genome.schema.yaml`](../schema/genome.schema.yaml), including an FHR export self-check (`--check-fhr`, on by default) |
| `genome_to_fhr.py` | Deterministic mapping from a genome YAML to its FHR `.fhr.json` sidecar (single source of truth; see [`schema/README.md`](../schema/README.md)) |
| `validate_recipe.py` | Validate refgenie-native recipe YAML files against [`schema/recipe.schema.yaml`](../schema/recipe.schema.yaml), and hard-check that `output_asset_class` / every `input_assets[].asset_class` reference an existing asset class |
| `validate_asset_class.py` | Validate asset-class YAML files in `asset_classes/` against `schema/asset_class.schema.yaml` (forthcoming) |
| `import_recipes.py` | Thin loader: loads `asset_classes/` and the refgenie-native recipes directly into refgenie1 (no conversion, recipes are already in the native model). Used by CI/mobot (forthcoming) |
| `migrate_build_matrix.py` | One-time, idempotent migration that folded `pep/build_matrix.yaml` into each genome YAML's `build:` block. The reproducible record of that change, including how each genome's store was resolved |
| `sources_to_genomes.py` | Generate genome YAMLs in bulk from a store's `sources.csv`, resolving taxon ids through NCBI Taxonomy (cached in `taxon_ids.json`). How a hub import lands |
| `taxon_ids.json` | Committed NCBI Taxonomy cache (`organism name -> {taxon_id, common_name}`) so `sources_to_genomes.py --offline` is reproducible |
| `changed_files.sh` | List changed genome/recipe YAML files in the current branch vs. a base ref (used by CI to scope validation) |
| `requirements.txt` | Python dependencies for the validators |

## Setup

```bash
pip install -r tools/requirements.txt
```

## Usage

```bash
# Validate specific entries
python tools/validate_genome.py genomes/human/hg38.yaml
python tools/validate_recipe.py recipes/bwa_index/recipe.yaml

# Validate everything changed in this branch vs. main
python tools/validate_genome.py $(./tools/changed_files.sh genomes)
python tools/validate_recipe.py $(./tools/changed_files.sh recipes)

# Preview the FHR sidecar a genome maps to (or write it into a directory)
python tools/genome_to_fhr.py genomes/human/hg38.yaml
python tools/genome_to_fhr.py genomes/human/hg38.yaml --out-dir fhr/
```
