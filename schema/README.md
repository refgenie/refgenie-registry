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
| `fhr.schema.json` | (reference) vendored upstream FHR schema | see "FHR alignment" below |

A contribution that doesn't conform to its schema is rejected before review, so
validating locally before opening a PR is the fastest way to catch problems:

```bash
pip install -r tools/requirements.txt
python tools/validate_genome.py genomes/<organism>/<assembly>.yaml
python tools/validate_recipe.py recipes/<asset_name>/recipe.yaml
```

See [CONTRIBUTING.md](../CONTRIBUTING.md) for the field-by-field reference and examples.

## FHR alignment

`genome.schema.yaml` is aligned to the [FAIR Headers Reference genome (FHR)](https://github.com/FAIR-bioHeaders/FHR-Specification)
vocabulary. The registry-native keys are the source of truth for the FHR core
(`name`, `aliases`, `organism`, `assembly`, `fasta.checksum`, `seqcol`), and the
optional `fhr:` block is an escape hatch for pure-FHR provenance fields that have
no registry-native home. A single exporter, [`tools/genome_to_fhr.py`](../tools/genome_to_fhr.py),
owns the deterministic YAML → `.fhr.json` mapping — it is the executable source
of truth, and the validator's `--check-fhr` self-check runs it on every file.

The emitted JSON uses camelCase keys that match the gtars `FhrMetadata` type
(`gtars-refget/src/store/fhr_metadata.rs`) so `refget store fhr set <seqcol_digest> <file>`
round-trips it. The **seqcol digest is the sidecar filename**
(`<seqcol.digest>.fhr.json`), never a JSON body field.

### YAML → FHR mapping

| Registry YAML | FHR JSON (camelCase) | Notes |
|---|---|---|
| `organism.scientific_name` | `genome`, `taxon.name` | species name |
| `organism.common_name` | `commonName` | gtars extension; promoted to a `genome` column |
| `organism.taxon_id` | `taxon.uri` | derived: `https://identifiers.org/taxonomy:{id}` |
| `name` | `version` | assembly identifier (e.g. `hg38`) |
| `aliases` | `genomeSynonym` | array |
| `description` | `documentation` | unless `fhr.documentation` overrides |
| `assembly.accession` | `accessionID` | `{name, url}`; url derived for `GCA_`/`GCF_` |
| `assembly.source` | `assemblySource` | gtars extension; promoted to a `genome` column |
| `assembly.level` | `assemblyLevel` | gtars extension; promoted to a `genome` column |
| `masking` | `masking` | enum passthrough |
| `fasta.checksum.sha256` | `checksum` | `sha256:{hash}`; omitted when the sentinel |
| `seqcol.digest` | *(sidecar filename)* | never a JSON body field |
| `fhr.license` | `license` | SPDX id (gtars extension; not in upstream FHR) |
| `fhr.funding` | `funding` | string |
| `fhr.scholarly_article` | `scholarlyArticle` | DOI |
| `fhr.date_created` | `dateCreated` | ISO 8601 date |
| `fhr.voucher_specimen` | `voucherSpecimen` | string |
| `fhr.instrument[]` | `instrument` | array |
| `fhr.related_link[]` | `relatedLink` | array |
| `fhr.identifier[]` | `identifier` | array, `namespace:value` |
| `fhr.metadata_author[]` | `metadataAuthor` | `[{name, uri(ORCID)}]` |
| `fhr.assembly_author[]` | `assemblyAuthor` | `[{name, uri}]` |
| `fhr.vital_stats.*` | `vitalStats` | `N50`/`L50`/`L90` upper; rest camelCase |
| *(exporter constant)* | `schema` | canonical FHR schema URL |
| *(exporter constant)* | `schemaVersion` | `1.0` |

`fasta.sources[].provider`, `fasta.checksum.md5`, `seqcol.length`, and the whole
`metadata` block are registry-only and are dropped from the sidecar.

`commonName`, `assemblySource`, and `assemblyLevel` are gtars-native extension
fields (they ride the sidecar's `extra` catch-all, not upstream FHR 1.0). They
carry `organism.common_name`, `assembly.source`, and `assembly.level` to
refgenie1's `apply_fhr`, which lands them in dedicated, queryable `genome`
columns. `taxon_id` and `assembly.accession` reach those columns too, parsed
from `taxon.uri` and `accessionID.name` respectively.

### Vendored `fhr.schema.json`

`fhr.schema.json` is a pinned, verbatim copy of the upstream FHR schema, vendored
so the export self-check is offline and reproducible.

- **Source:** <https://raw.githubusercontent.com/FAIR-bioHeaders/FHR-Specification/main/fhr.json>
- **Upstream commit:** `ee8dc12365a7c68596d868681a0240339a3a6892` (2026-02-08)

The validator does **not** enforce upstream FHR verbatim on registry exports: it
validates against a *relaxed profile* derived from this file
(`genome_to_fhr.load_fhr_export_schema`). Upstream FHR requires ~10 provenance
fields for a *complete publishable record* and forbids extras; a registry export
is intentionally partial and its true round-trip target is the permissive gtars
`FhrMetadata`. The profile therefore (1) drops upstream's top-level `required`,
(2) allows the gtars-native `license` field (upstream uses `reuseConditions`),
and (3) accepts the `algo:value` checksum form — while keeping every property
type/pattern/enum and `additionalProperties: false` so typos are still caught.

To update the vendored copy, re-download from the source URL and update the
commit hash above.
