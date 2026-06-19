# schema/

JSON Schemas that define the required structure of registry contributions.
Every genome and recipe entry submitted to the registry is **validated against
these schemas** — locally by `tools/validate_genome.py` / `tools/validate_recipe.py`
and again automatically in CI on each pull request. They are the source of truth
for which fields are required, their types, and their allowed values.

| Schema | Validates | Applies to |
|--------|-----------|------------|
| `genome.schema.yaml` | Genome assembly definitions | `genomes/<organism>/<assembly>.yaml` |
| `recipe.schema.yaml` | Asset build recipes | `recipes/<asset_name>/recipe.yaml` |

A contribution that doesn't conform to its schema is rejected before review, so
validating locally before opening a PR is the fastest way to catch problems:

```bash
pip install -r tools/requirements.txt
python tools/validate_genome.py genomes/<organism>/<assembly>.yaml
python tools/validate_recipe.py recipes/<asset_name>/recipe.yaml
```

See [CONTRIBUTING.md](../CONTRIBUTING.md) for the field-by-field reference and examples.
