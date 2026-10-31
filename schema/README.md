# schema/

JSON Schemas that define the required structure of registry contributions.
Every genome and recipe entry submitted to the registry is **validated against
these schemas**, locally by `tools/validate_genome.py` / `tools/validate_recipe.py`
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
owns the deterministic YAML → `.fhr.json` mapping, it is the executable source
of truth, and the validator's `--check-fhr` self-check runs it on every file.

The emitted JSON uses camelCase keys that match the gtars `FhrMetadata` type
(`gtars-refget/src/store/fhr_metadata.rs`) so `refget store fhr set <seqcol_digest> <file>`
round-trips it. The **seqcol digest is the sidecar filename**
(`<seqcol.digest>.fhr.json`), never a JSON body field.

### YAML → FHR mapping

| Registry YAML | FHR JSON (camelCase) | Notes |
|---|---|---|
| `name` | `genome` | the genome's own name (e.g. `hg38`) |
| `organism.scientific_name` | `taxon.name` | species name |
| `organism.taxon_id` | `taxon.uri` | derived: `https://identifiers.org/taxonomy:{id}` |
| `aliases` | `genomeSynonym` | array; genome aliases only (e.g. `GRCh38`) |
| `description` | `documentation` | unless `fhr.documentation` overrides |
| `assembly.accession` | `accessionID` | `{name, url}`; url derived for `GCA_`/`GCF_` |
| `assembly.accession` version number | `version` | the number after the dot of a `GCA_`/`GCF_` accession (`GCA_000001405.15` → `"15"`); left out without one |
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
`metadata` and `build` blocks are registry-only and are dropped from the sidecar.

Species names are not FHR fields and do not appear in genome YAMLs; they live in
[`tools/taxon_ids.json`](../tools/taxon_ids.json) and ship to stores as `species.json`
(see [`stores/README.md`](../stores/README.md)).

### The registry/FHR boundary

`genome.schema.yaml`'s top-level fields fall into two groups, and the nesting says
which is which:

- **FHR-exported**, `name`, `aliases`, `description`, `organism`, `assembly`,
  `masking`, `fasta.checksum`, `seqcol`, and the `fhr:` escape hatch. These are
  facts about the genome.
- **Registry-only**, `metadata:` (bookkeeping: who added it, when) and `build:`
  (pipeline instructions: which store holds the sequence, how far to build it).
  These describe what *we* do with the genome, not what the genome *is*, so they
  are fenced off in their own blocks and never exported.

The exporter enforces this by construction: `genome_yaml_to_fhr()` builds its
output by explicit whitelist (`out["genome"] = ...`), never by passing unknown
keys through, so a new top-level block is ignored automatically. The validator
asserts it anyway, `check_build_not_in_fhr` runs the export and fails if any
`build` key appears, so a future exporter that starts forwarding unknown keys
fails here rather than publishing pipeline state as metadata.

`assemblySource` and `assemblyLevel` (plus `license`) are extension fields: they
ride the sidecar's `extra` catch-all in gtars and are not in upstream FHR. They
carry `assembly.source` and `assembly.level` to refgenie's `apply_fhr`, which
lands them in queryable `genome` columns. refgenie also reads `taxon.name` (the
species), `taxon.uri` (the taxon id), `genomeSynonym`, `documentation` and
`accessionID.name`. It does not read `genome` (the name already reaches it as an
alias) or `version`.

### Known FHR v2.0 gaps

The exports do not yet pass the current upstream schema (v2.0). Every record
lacks `metadataAuthor`, `assemblyAuthor`, `masking` and `checksum` (most also
lack `dateCreated`); `schemaVersion` is the number `1.0` where v2.0 wants the
string `"2.0"` (gtars still types it as a number, so it cannot change first);
genomes without an NCBI accession have no `version`; and `assemblySource` and
`assemblyLevel` are extensions the spec does not allow. These are left for a
separate "FHR v2.0 conformance" change (gtars first, then the registry, then
refgenie).

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
(3) accepts the `algo:value` checksum form, and (4) allows the `assemblySource`
and `assemblyLevel` extension fields, while keeping every property
type/pattern/enum and `additionalProperties: false` so typos are still caught.

To update the vendored copy, re-download from the source URL and update the
commit hash above.
