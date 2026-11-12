# RefgetStores

> Part of [refgenie-registry](../README.md). See the top-level README for the
> repo layout (`genomes/`, `recipes/`, `index/`, `schema/`, `tools/`).

Each subfolder defines a refgetstore, a content-addressable sequence collection. Store folders contain source manifests and any store-specific scripts. The core build tooling is in the `refget`/`gtars` packages.

## Stores

| Store | Contents |
|-------|----------|
| **legacy** | The genomes the old refgenomes.databio.org server published |
| **jungle** | Reference genome jungle paper dataset |
| **pangenome** | HPRC pangenome haplotypes |
| **vgp** | VGP vertebrate genomes |
| **refseq** | NCBI protein + transcript sequences |
| **vrs** | VRS allele identification reference sequences |
| **salmon_txomes** | Salmon/tximeta transcriptomes |
| **igenomes** | AWS iGenomes reference genomes |
| **plantcore** | Well-assembled plant, algal + model-organism genomes, one per species (published) |
| **plantsup** | The rest of the legacy plant collection: fragmented or duplicate assemblies (not published) |
| **decoys** | Decoy and spike-in sequences |
| **demo** | Test data for development |
| **refgenie-main** | The build catalog's own store: genomes the nightly builds (`kind: catalog`, no `sources.csv`) |

## The link to `genomes/`

Every genome YAML in [`genomes/`](../genomes) names one of these stores in its
`build.store` field, claiming that the store holds its sequence.
[`build/sync_stores.py`](../build/sync_stores.py) is what checks that claim:

```bash
python build/sync_stores.py                  # coverage report, all stores
python build/sync_stores.py --check          # non-zero if a genome is not loadable or not aligned
python build/sync_stores.py --changed        # stores needing a full load
python build/sync_stores.py --alias-changed  # stores needing only an alias refresh
```

A genome whose declared store has no row for it is **registered but unloadable**,
and nothing else reports that.

### One name per genome

A genome's names are defined in its YAML only: `name` plus `aliases`. When a
store row has a YAML, **the row's `name` is exactly the YAML `name`**. That string
is the link between the two lists. Any other name for the genome (the
provider's, the old row name) goes in the YAML `aliases`, and the store
registers every one of them. `sync_stores.py --check` fails on a row that is not
named after its genome ("NOT ALIGNED"), and
[`tools/align_store_names.py`](../tools/align_store_names.py) renames such rows
and moves the old name into `aliases`. New YAMLs from
`tools/sources_to_genomes.py` follow the rule on their own.

Rows are matched to YAMLs by exact name, then by `seqcol.digest`, then by YAML
`aliases`, then by the staged FASTA path in `pep/config.yaml`, and, for a YAML
with no digest only, by an accession that appears once. A YAML whose
`seqcol.digest` disagrees with its row is an error. See
[`genome_rows.py`](genome_rows.py).

### What triggers a reload

The nightly reloads a store fully only when its rows' `fasta` values change
(rows added, removed or pointed at another file), checked against the
`.sources.sha256` stamp inside the built store, so a large store's objects are
not re-synced every night. A change to a row's name or other columns, or to a
genome YAML's `name` or `aliases`, only changes the `.aliases.sha256` stamp: the
nightly then runs `build_aliases.py`, `build_fhr.py` and `build.py --sync-only`,
which read no FASTA. `build_aliases.py` finds each row's collection through the
`.row_digests.json` map (row `fasta` -> collection digest) that `build.py` writes
into the store, so a renamed row still resolves.

### `sources.csv` is NOT generated from the genome list

`pep/samples.csv` and `pep/metadata/` are generated from `genomes/**/*.yaml` and
must never be hand-edited. **`sources.csv` is the opposite: it is hand-maintained,
and `sync_stores.py` never rewrites it.** The asymmetry is deliberate, for two
reasons:

1. **The two files name different things.** A store row's `fasta` is the path or
   URL the store actually ingests, jungle's are staged relative paths like
   `homo_sapiens/ENA/hg38/fasta/GRCh38-ena-15_GCA_000001405.fa.gz`, while a
   genome YAML records the upstream provider URL. They are different strings for
   the same sequence, and a genome YAML does not carry enough information to
   reconstruct the store's.
2. **Most store rows have no genome YAML.** Only `vgp` and `legacy` are fully
   covered. Generating `sources.csv` from the genome list would silently delete
   most of `igenomes`, `refseq`, `salmon_txomes`, `plantcore` and `plantsup`.

So the direction of truth differs by file: the genome list drives the build
queue and the published metadata, and `sources.csv` drives what a store holds.
`sync_stores.py` reconciles them and reports disagreements rather than resolving
them one way by fiat.

## Per-store structure

Each store folder is a PEP project:

```
store_name/
├── README.md            # what's in this store
├── sources.csv          # sample table: one FASTA per row (URL, or path relative to fasta_root)
└── project_config.yaml  # PEP config (+ optional `fasta_root:`, see below)
```

The store-build scripts in this directory are generic, they take a store name
and read everything store-specific from that store's `project_config.yaml` /
`sources.csv`. One optional `project_config.yaml` key configures them:

- **`fasta_root:`**, base directory for **relative** `fasta` paths in `sources.csv`.
  Environment variables are expanded, so the absolute machine location stays out of
  the committed data: e.g. `fasta_root: $REFGETSTORE_FASTA/jungle` (with
  `$REFGETSTORE_FASTA` set by [`infra/rivanna/env.sh`](../infra/rivanna/env.sh)) lets
  `sources.csv` hold `homo_sapiens/ENA/.../GRCh38.fa.gz`. A `fasta` value that is a URL
  or an absolute path is used as-is and ignores `fasta_root`.

A store with genuinely bespoke logic can instead ship its own script in its
folder (e.g. [`vrs/build_aliases.py`](vrs/build_aliases.py)).

## Adding a new store

1. **Create the folder** `stores/<name>/`.

2. **Write `project_config.yaml`** (a PEP). Minimal:

   ```yaml
   pep_version: 2.1.0
   sample_table: sources.csv
   sample_table_index: fasta        # or another unique column, e.g. pep_sample_name
   ```

   Add `fasta_root:` if `sources.csv` uses relative local paths (see
   [Per-store structure](#per-store-structure)). Aliases need no configuration
   (see [Aliases](#aliases-post-build)).

3. **Write `sources.csv`**, one FASTA per row. Only the **`fasta`** column is
   required; each value is either a URL (`http(s)://`, `ftp://`, `s3://`) or a
   path relative to `fasta_root` (a row may list several space-separated FASTAs
   that get concatenated into one collection). Recommended columns, used for
   collision-free download caching and for collection aliases:
   `name`, `organism`, `source`, `genome_assembly`, `accession`.

4. **Write `README.md`** describing what's in the store.

5. **Validate** before building:

   ```bash
   python validate_sources.py <name>/sources.csv   # schema/structure
   python validate_files.py <name>                 # every fasta path/URL resolves
   ```

6. **Build** (on Rivanna; sets `$REFGETSTORE_*` first):

   ```bash
   source ../infra/rivanna/env.sh
   python build.py <name>                           # local
   sbatch --job-name=build-<name> infra/rivanna/build_store.slurm <name>   # or as a SLURM job
   ```

7. **Aliases** (optional, post-build) and **deploy to S3**, see the sections below.

8. **Publish** it, if its genomes should appear in the public catalog, see
   [Publishing a store](#publishing-a-store).

## Publishing a store

A store is **published** when its `project_config.yaml` has a `publish:` block.
Published stores are the ones the nightly ingests into the build catalog
(`refgenie store sync`) and the catalog carries to api.refgenie.org. A store
with no `publish:` block is not published, and its `store_only` genomes never
reach the catalog (`sync_stores.py` reports them as NOT PUBLISHED).

```yaml
publish:
  priority: 40                                 # lower wins alias ties; unique across stores
  description: Illumina iGenomes reference FASTAs
```

The URL is not written: it is `$REFGETSTORE_PUBLIC_URL/<name>/` (set in
[`infra/rivanna/env.sh`](../infra/rivanna/env.sh)), so deploy the store to S3
first. The next nightly picks it up:
[`build/store_declaration.py`](../build/store_declaration.py) turns the
`publish:` blocks into a store list, and `refgenie store apply` makes the build
catalog's store table match it (a store whose `publish:` block is removed is
disabled, not deleted). Do not register stores by hand with `refgenie store add`;
the next nightly would undo it. Check a change with:

```bash
REFGETSTORE_PUBLIC_URL=https://refgenie.s3.us-east-1.amazonaws.com/refget-store \
  python build/store_declaration.py
```

Rules, checked by `store_declaration.py`: priorities are unique, and a store with
`nightly: false` cannot be published.

`stores/refgenie-main/` is the one folder that is not a source store. It has
`kind: catalog` and no `sources.csv`: it stands for the build catalog's own
store, which genome_init fills and the nightly syncs to
`$REFGETSTORE_S3/refgenie-main`. Its `publish:` block sets the store row that
`refgenie catalog-export` publishes for it. Store tooling (`build.py`,
`sync_stores.py`, the validators) skips it, and no genome YAML may name it as
its `build.store`.

## Building

```bash
source ../infra/rivanna/env.sh                # Set REFGETSTORE_BASE and REFGETSTORE_S3
python build.py demo            # Build one store
python build.py all             # Build all stores
python build.py jungle --sync   # Build and sync to S3
python build.py vgp -j 6        # Limit parallel ingest workers (default: $SLURM_CPUS_PER_TASK or 8)
```

Stores are built to `$REFGETSTORE_BASE/<store_name>` and synced to `$REFGETSTORE_S3/<store_name>`.
Ingest runs in parallel in Rust (`add_sequence_collections_from_fastas`); `-j`/`$SLURM_CPUS_PER_TASK`
sets the worker count. Memory is bounded (streaming), but high-sequence-count transcriptomes are
heavier per worker, drop `-j` if a build OOMs. Each build writes a **`<store>_build_report.json`**
(start/end/duration, loaded/skipped/failed counts, n_collections/n_sequences, gtars/refget versions,
git rev, per-collection records) to a **local reports dir**, `$REFGENIE_BUILD_REPORTS_DIR`, else a
`_build_reports/` sibling of the store dirs. It is operator provenance nothing consumes, so it is kept
out of the store dir (which is synced to the public bucket) and never published.

On the HPC, submit via [`infra/rivanna/build_store.slurm`](../infra/rivanna/build_store.slurm) (8 CPU / 32 GB):
`sbatch --job-name=build-<store> infra/rivanna/build_store.slurm <store>`.

## Validation

```bash
python validate_sources.py jungle/sources.csv   # schema/structure of a sources.csv
python validate_files.py jungle                 # every fasta path/URL resolves (local exists / cached)
python validate_files.py all --check-urls        # also HEAD-probe uncached URLs
```

## Aliases (post-build)

[`build_aliases.py`](build_aliases.py) builds every alias of a store and replaces the store's
whole alias set on each run. An alias resolves to exactly one digest within its namespace, so
the script registers two kinds of keys:

- **The registry's own names**, as collection aliases: the `name` and `genome_assembly`
  columns of `sources.csv` (a value is used when all rows carrying it are the same
  collection), and the `aliases` of each row's genome YAML under `name`.
- **Accessions from INSDC and RefSeq**, under the `insdc` and `refseq` namespaces, for
  sequences and for whole assemblies. Each link is proven against data the authority
  publishes, and the report records the proof:

  | Evidence | Proof |
  |---|---|
  | `checksum` | [ENA](https://www.ebi.ac.uk/ena/browser/)'s MD5 for the INSDC sequence accession equals the MD5 of a sequence in the store (ENA publishes MD5s for the current version of chromosome-level records) |
  | `ncbi-identical` | NCBI's assembly report marks the GenBank and RefSeq accessions identical (`=`), and one of them is proven |
  | `name+length` | for sequences ENA has no checksum for (unplaced scaffolds, RefSeq): the collection names the sequence with the accession and NCBI's length matches, in a collection whose checksummed sequences all match ENA |
  | `content` | an assembly accession names a collection whose sequence names are exactly that assembly's accessions (GenBank for `GCA_`, RefSeq for `GCF_`) with NCBI's lengths |

  A row's `accession` column tells the script which assembly to check; it is a claim, and a
  collection gets the accession only when its content passes. Analysis sets, decoy-augmented
  and masked FASTAs keep their own `name` aliases and resolve by those.

Sequence names from FASTA headers (`chr1`, `1`, ...) are each collection's `names`, looked up
within a collection with `get_sequence_by_name(collection, name)`.

Authority data is fetched once per accession and kept in a folder shared by all stores,
`$REFGET_ALIAS_SOURCES` (default `$REFGETSTORE_BASE/../alias-sources`):

```
alias-sources/
├── ncbi/<GCA_|GCF_...>_assembly_report.txt   # NCBI assembly report
└── ena/<GCA_...>.tsv                         # ENA accession, version, MD5, length
```

Run:

```bash
source ../infra/rivanna/env.sh
python build_aliases.py vgp                         # rebuild vgp's aliases
python build_aliases.py jungle --dry-run --out /tmp/jungle   # compute, write maps + report only
python build_aliases.py jungle --out /tmp/jungle \
    --store-url https://refgenie.s3.us-east-1.amazonaws.com/refget-store/jungle
    # read-only, against a published store
```

Each run writes `aliases_<store>.md` (what was registered, by evidence, and what was not, with
the reason) to the build reports dir, and moves the store's previous `aliases/` folder to
`<reports dir>/aliases_previous/<store>`. `--out` also writes one `alias, digest, evidence`
TSV per namespace.

**vrs** ships its own [`vrs/build_aliases.py`](vrs/build_aliases.py) with VRS-specific
namespaces (one namespace per assembly version, Ensembl ENST/ENSP); the generic script skips it.

## FHR metadata (post-build)

FHR (FAIR Headers Reference genome) sidecars, `fhr/<digest>.fhr.json`, carry per-collection
genome/assembly metadata (`schema`, `schemaVersion`, `genome`, `taxon`, `version`,
`documentation`, `assemblySource`, `accessionID`, `assemblyLevel`). `genome` is the
genome's own name and `taxon` its species, as the FHR spec defines them. refgenie reads
the description, the species (`taxon.name`), the taxon id (`taxon.uri`) and the assembly
source/accession/level into its faceted columns for store-overlay genomes, so a store
without sidecars syncs as genomes with null metadata. It does not read `genome` (the name
already arrives as an alias) or `version`. Common names are not in FHR; they come from
`species.json` (below).

`build_fhr.py` resolves each `sources.csv` row to its collection digest (via the aliases
`build.py` registered) and writes that collection's sidecar. Fields come from three
sources, least to most specific:

1. **The CSV derivation**, name → `genome`; synonyms (split on `;`) →
   `genomeSynonym`; organism → `taxon` (scientific name + taxon
   URI); name/genome_assembly/source → a one-sentence `documentation`; source →
   assemblySource; accession → accessionID and `version` (the accession's version number,
   `GCA_000001405.15` → `"15"`), plus assemblyLevel from the script's static per-accession
   `ASSEMBLY_LEVELS` map sourced from NCBI Datasets. `schema` and `schemaVersion` are the
   same constants as in YAML-derived records. The organism is looked up in the
   species list, [`tools/taxon_ids.json`](../tools/taxon_ids.json) (by key, the NCBI
   scientific name, then by synonym, so "Canis familiaris" finds *Canis lupus
   familiaris*), and the build **fails loudly**, naming the list, on any organism it
   cannot find: the fix is a PR to the list, never a guess. An accession missing from
   `ASSEMBLY_LEVELS` just gets no assemblyLevel.
2. **The curated registry record**, if a `genomes/**/*.yaml` names this store in its
   `build.store` and matches the row (on `name` or `accession`), or pins the row's
   collection digest in `seqcol.digest` (whatever its store), the row's fields come from
   that genome's `pep/metadata/<genome>.fhr.json` instead. So every store that holds a
   pinned genome publishes its curated description. That sidecar is generated from
   the genome YAML through the one mapping module (`tools/genome_to_fhr.py`), so the store's
   published metadata and the registry catalog's are the same record. Rows with a curated
   record are exempt from the organism validation. Pass `--no-registry` to derive every
   row from the CSV instead.
3. **A per-genome override YAML**, see below.

Idempotent; re-runs overwrite. It also re-commits `rgstore.json` so the manifest's
`fhr_digest` advertises the sidecars (required for remote `pull_fhr`).

**Per-genome YAML overrides**, "use YAML if it exists, use CSV otherwise":
`stores/<store>/genomes/<row_name>.yaml` is a flat camelCase FHR-field mapping merged
over that row's CSV-derived fields, field by field (a YAML field wins wholesale; absent
fields keep the CSV derivation). Use these for hand-curated metadata the CSV can't
express, corrected `assemblySource`, richer `documentation`, `relatedLink` provenance
URLs, an explicit `assemblyLevel` (which beats the static map) or `version`. For rows that share a
digest, the last row still wins; put the override on the digest-winning row (mirroring
it on same-digest twins keeps it reorder-safe). jungle's `genomes/` dir carries
overrides capturing true provenance for the Heng-Li-guide references
(hs37d5, hs37-1kg, hs38/hg38-refgenie, hs38DH, b37-broad).

```bash
source ../infra/rivanna/env.sh
python build_fhr.py jungle --dry-run   # preview
python build_fhr.py jungle             # write sidecars + refresh manifest
```

After running, sync the store's `fhr/` dir, `species.json` **and** `rgstore.json` to S3
(see below).

### species.json (species names)

`species.json` sits at the store root, next to `rgstore.json`. It lists the species
the store holds, with every name refgenie should find them by, so a search for
"brown rat" finds *Rattus norvegicus* genomes even though their common name is
"Norway rat". It is not FHR: an FHR record has one `genome`, one `taxon`, and a
`genomeSynonym` list meant for names of the genome itself, and it forbids extra
fields. gtars ignores the file, and the RefgetStore format does not change. Readers
treat it as optional.

The contract (shared with refgenie, which reads the file during `store sync`):

```json
{"species": [
  {"taxon_id": 10116, "scientific_name": "Rattus norvegicus", "common_name": "Norway rat", "synonyms": ["brown rat", "rat"]}
]}
```

- `taxon_id`: the NCBI taxon id, an integer; required, and unique in the file.
- `scientific_name`: required.
- `common_name`: optional.
- `synonyms`: an optional list of other names.
- The file lists only that store's species, sorted by `taxon_id`.

**Who writes it.** `build_fhr.py` writes it from the species list,
[`tools/taxon_ids.json`](../tools/taxon_ids.json), with exactly the taxa named by the
sidecars it writes, under the same lock as the sidecars, so it rides the same S3 sync.
A sidecar naming a taxon the list lacks stops the run before anything is written. The
catalog's own sequence store (`refgenie-main`) gets its copy from
`python tools/species_list.py store-file "$REFGENIE_GENOME_FOLDER/.refget_store"`,
which the nightly runs just before syncing that store.

**Refreshing after a list edit.** The nightly republishes only stores whose
`sources.csv` changed, so it also reruns `species_list.py store-file` on every built
store and uploads each `species.json` whose text changed. A merged list edit reaches
every published store the next night, before `refgenie store sync` reads the files.

**Checking a published copy:**

```bash
curl -s https://refgenie.s3.us-east-1.amazonaws.com/refget-store/<store>/species.json -o species.json \
    && python tools/species_list.py check-file species.json
```

## Deploying to S3

Stores are served publicly from `s3://refgenie/refget-store/<store>/`
(`https://refgenie.s3.us-east-1.amazonaws.com/refget-store/<store>/`). Push runs **on Rivanna**
using its own `~/.aws` `refgenie` profile (authenticates as `RefgenieDataBot`), **use
`--profile refgenie`**; the default profile (`s3user`) is AccessDenied on this bucket.

```bash
source ../infra/rivanna/env.sh
# The ~/.local/bin/aws is broken; use the cluster's awscli:
export PATH="$REFGENIE_AWS_BINDIR:$PATH"
aws s3 sync "$REFGETSTORE_BASE/vgp" "$REFGETSTORE_S3/vgp" --profile refgenie --delete
```

`--delete` makes S3 exactly mirror the local store (true replace; drops stale files). Large stores
(vgp ~378 GB) are best pushed as a background/SLURM job. Verify after:
`curl -sI https://refgenie.s3.us-east-1.amazonaws.com/refget-store/<store>/store_metadata.json`.

Note: `build.py --sync` runs `aws s3 sync` with the **default** profile, so it fails on this bucket , 
either export `AWS_PROFILE=refgenie` first, or sync manually as above.

## Files & infrastructure

Build/validation tooling (env-agnostic Python) lives in this `stores/` directory.
The **Rivanna/HPC execution layer**, environment, SLURM jobs, and yoke config , 
is isolated under [`../infra/rivanna/`](../infra/rivanna/) so it stays out of the
way of external viewers. See [`infra/rivanna/README.md`](../infra/rivanna/README.md).

Build/validation tooling (`stores/`):

| Path | Role |
|------|------|
| [`stores/build.py`](build.py) | Build one/all stores (parallel Rust ingest) |
| [`stores/build_aliases.py`](build_aliases.py) | Register sequence/collection aliases (post-build) |
| [`stores/validate_sources.py`](validate_sources.py) | Check a `sources.csv` schema/structure |
| [`stores/validate_files.py`](validate_files.py) | Check every FASTA path/URL resolves |
| [`stores/compare_store_to_sources.py`](compare_store_to_sources.py) | Diff a built store against its `sources.csv` |
| [`stores/fasta_naming.py`](fasta_naming.py) | Shared FASTA name-parsing helpers |
| [`download_fastas.py`](../download_fastas.py) | Helper to fetch source FASTAs into a download cache |

Rivanna/HPC execution layer ([`infra/rivanna/`](../infra/rivanna/)):

| Path | Role |
|------|------|
| [`infra/rivanna/env.sh`](../infra/rivanna/env.sh) | Sets `REFGETSTORE_BASE` / `REFGETSTORE_S3`, `source ../infra/rivanna/env.sh` before building |
| [`infra/rivanna/build_store.slurm`](../infra/rivanna/build_store.slurm) | SLURM job (8 CPU / 32 GB): `sbatch --job-name=build-<store> infra/rivanna/build_store.slurm <store>` |
| [`infra/rivanna/download_igenomes.slurm`](../infra/rivanna/download_igenomes.slurm) | SLURM job to stage iGenomes FASTAs |
| [`infra/rivanna/download_salmon_txomes.slurm`](../infra/rivanna/download_salmon_txomes.slurm) | SLURM job to stage Salmon transcriptomes |
| [`infra/rivanna/sync_to_s3.slurm`](../infra/rivanna/sync_to_s3.slurm) | SLURM job to `aws s3 sync` a store to S3 |
| [`yoke.toml`](../yoke.toml) | Yoke session config (at the repo root, so mutagen syncs the whole repo) |
