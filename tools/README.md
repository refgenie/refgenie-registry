# tools/

Validation scripts and helpers used by CI and by contributors before opening a PR.

| File | Purpose |
|------|---------|
| `validate_genome.py` | Validate genome YAML files against [`schema/genome.schema.yaml`](../schema/genome.schema.yaml), including an FHR export self-check (`--check-fhr`, on by default) |
| `genome_to_fhr.py` | Deterministic mapping from a genome YAML to its FHR `.fhr.json` sidecar (single source of truth; see [`schema/README.md`](../schema/README.md)) |
| `validate_recipe.py` | Validate refgenie-native recipe YAML files against [`schema/recipe.schema.yaml`](../schema/recipe.schema.yaml), and hard-check that `output_asset_class` / every `input_assets[].asset_class` reference an existing asset class |
| `validate_asset_class.py` | Validate asset-class YAML files in `asset_classes/` against `schema/asset_class.schema.yaml` (forthcoming) |
| `import_recipes.py` | Thin loader: loads `asset_classes/` and the refgenie-native recipes directly into refgenie1 (no conversion, recipes are already in the native model). Used by CI/mobot (forthcoming) |
| `sources_to_genomes.py` | Generate genome YAMLs in bulk from a store's `sources.csv`, taking each organism's taxon id from the species list and adding any species it lacks (from NCBI Taxonomy) to `taxon_ids.json`. How a hub import lands |
| `taxon_ids.json` | The species list: NCBI scientific name -> `{taxon_id, common_name, synonyms}`. The one place species names are edited (by PR). Every store's `species.json` and the CSV-derived FHR `taxon` (`stores/build_fhr.py`) come from it. Every genome's `organism.taxon_id` must be on it |
| `species_list.py` | The one module for the species list: load/save, organism lookup (key, then synonym), the name rules, the list check (`check`), and writing (`store-file`) and checking (`check-file`) a store's `species.json`. Standard library only |
| `fetch_species_names.py` | Refreshes the species list from NCBI Taxonomy by hand (common names, other names, missing store species); `--dry-run` previews. Builds never call NCBI |
| `update_recipe_images.py` | Moves each recipe's `quay.io/biocontainers` pin to the newest tag and bumps the recipe version (`--write`); recipes with other images are listed in `HOLD`. Run monthly by `.github/workflows/update-recipe-images.yaml`, which opens a PR |
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
