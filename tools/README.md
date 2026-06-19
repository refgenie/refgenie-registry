# tools/

Validation scripts and helpers used by CI and by contributors before opening a PR.

| File | Purpose |
|------|---------|
| `validate_genome.py` | Validate genome YAML files against [`schema/genome.schema.yaml`](../schema/genome.schema.yaml) |
| `validate_recipe.py` | Validate recipe YAML files against [`schema/recipe.schema.yaml`](../schema/recipe.schema.yaml) |
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
```
