# plantref

Plant (and assorted algal / protist / model-organism) reference genomes from the
lab's legacy refgenie plant-genome collection.

## Sources

`sources.csv` contains 154 FASTA entries staged from the legacy flat-file dump at
`/project/shefflab/www/refgenie_plantref/` (files dated 2018–2020). Those files
used the old refgenie naming convention `<...descriptor...>-fasta-fasta`, where
the `-fasta-fasta` suffix encodes `{asset=fasta}-{seekkey=fasta}` — i.e. the file
*is* the genome FASTA. Every file was verified to be gzip-compressed valid FASTA
(despite having no `.gz` extension), so staging is a copy + rename to `.fa.gz`
(no recompression needed).

Coverage: 114 distinct organisms (plants, green/red algae, diatoms, a few
protists, and the usual model-organism controls — *Homo sapiens*, *Mus
musculus*, *Drosophila melanogaster*, *Caenorhabditis elegans*,
*Saccharomyces cerevisiae*, *Schizosaccharomyces pombe*).

## Pruned assemblies (2026-07)

Three legacy entries were removed from `sources.csv` **and** deleted from the
built store. All three were pre-chromosome-scale WGS contig dumps whose FASTAs
shredded a genome into millions of short records; together they accounted for
~89% of every sequence in the store:

| Removed collection | Digest | Sequences | Median seq |
|---|---|---|---|
| `picea_abies_ConGenIE_v1_0` | `OrHTWEIrvgF7aSs2Fz4TS-cla0CJAPhM` | 10,253,694 | 317 bp |
| `hordeum_vulgare_MIPS` | `hBkUaFdD-vx4e6KH0j3I3DLdEz0JE6q9` | 2,670,738 | 302 bp |
| `triticum_aestivum_Ensembl_Plants_TGACv1` | `B3vspFHqvYCHo4l8Q38hsZ02z1h-bJlK` | 735,945 | 2,431 bp |

**Barley and wheat needed no replacement.** The store already carries complete
chromosome-scale assemblies of both, verified by base count:
`triticum_aestivum_IWGSC1_1` (22 sequences, 14.55 Gbp) and
`hordeum_vulgare_IBC_PGSB_v2` (10 sequences, 4.83 Gbp), plus
`hordeum_vulgare_Ensembl_Genomes_ASM32608v1` (4.05 Gbp). Dropping the MIPS and
TGACv1 rows costs zero species coverage and zero genome coverage.

**Spruce did need one.** `picea_abies_ConGenIE_v1_0` was the only *Picea* entry,
so it is replaced by **Pabies02** (`GCA_964035815.1`, ENA project `PRJEB69221`,
Umeå Plant Science Centre, PacBio HiFi): 12 chromosomes (`OZ038344`–`OZ038355`)
plus 882 unplaced contigs from WGS set `CAXIVX01` = **894 sequences, ~15.9 Gbp**.
That covers more genome than ConGenIE v1.0 (which captured only ~60% of the
~20 Gbp genome) in four orders of magnitude fewer records.

Net: plantref went from 15,307,118 to roughly 1,547,900 sequences.

Because `build.py` is additive only — it never removes — editing `sources.csv`
alone does **not** drop a collection from a built store. Removal is a separate,
explicit operation via `stores/remove_collections.py`, and the S3 sync must run
with `--delete` so the orphaned objects do not linger.

### Pabies02 header normalization

ENA serves these records with pipe-delimited database prefixes and a long
description (`>ENA|OZ038344|OZ038344.1 Picea abies genome assembly, chromosome:
01`). Headers were normalized to the bare submitter name — `01`..`12` for the
chromosomes and `PA_chr01_sUL001`-style names for the unplaced contigs:

```bash
zcat raw.fa.gz | sed -E 's/^>.*(chromosome|contig): (.+)$/>\2/' | gzip -c > Pabies02.fa.gz
```

This matters because sequence digests depend only on sequence content, but the
**collection digest incorporates the names digest** — so header style permanently
fixes the collection's identity. No other plantref FASTA contains a pipe in its
first header, and only 5 of the original 156 carry any description text; every
other collection uses a short bare name (`>chr1`, `>Chr01`, `>scaffold_1`, `>1`).
Keeping the ENA headers verbatim would have made Pabies02 the sole outlier and
would also have poisoned `build_aliases.py --seq-strategy header_names`, which
would register those full pipe-delimited strings as sequence aliases.

Pabies02 is also the first plantref entry with a populated `accession` column
(see the naming assumptions below — the legacy rows have none).

## Brick layout

Staged copies live under the sibling FASTA brickyard, mirroring the `jungle`
convention (`<store>/<organism>/<source>/<assembly>.fa.gz`):

```
/project/shefflab/brickyard/datasets_downloaded/refgenomes_fasta/fasta/plantref/
  <organism_dir>/<source>/<assembly_slug>.fa.gz
```

`<organism_dir>` is the lowercase `genus_species` (with subspecies/strain
suffixes preserved where present). The originals are COPIES only — the source
files under `www/refgenie_plantref/` are left untouched.

## Naming assumptions

These are old curated copies ingested **without** re-verifying upstream, so the
metadata is parsed from each legacy filename:

- **organism** — the leading `Genus species` tokens. Subspecies / strain
  variants (`Oryza sativa subsp. indica/japonica`, `Saccharomyces cerevisiae
  strain S288C`, `Chlorella sp. NC64A`, `Picochlorum sp. SENEW3`, etc.) are
  special-cased so the species is not truncated.
- **source** — the apparent data-producing authority baked into the filename
  (`JGI`, `Ensembl`, `NCBI`, `RefSeq`, `GenBank`, `MIPS`, `MSU`, `JCVI`,
  `Phytozome`, `Ghent`, `SolGenomics`, `CucurbitGDB`, ... — 40 distinct values).
  When more than one authority appears, the data producer wins (e.g. JGI over
  the Phytozome portal). 24 rows carry no recognizable authority in the
  filename and use the fallback source `plantref`. A handful of build-name-only
  tags (`TAIR10`, `Araport11`, `IWGSC1_1`) are likewise left as `plantref`
  since they name a build, not an authority.
- **genome_assembly** — the remaining descriptor tokens after the organism,
  slugified.
- **accession** — left blank for the legacy rows. These are old curated copies;
  the original GCA/GCF accessions were not recorded in the filenames. The one
  exception is `picea_abies_Pabies02`, which was fetched from ENA against a
  known accession (see above).
- **name** / **pep_sample_name** — `<genus_species>_<assembly_slug>`, made
  unique where two assemblies would otherwise collide.

## Build

```bash
source ../infra/rivanna/env.sh
python build.py plantref            # local
# on Rivanna (submit from repo root):
sbatch --job-name=build-plantref infra/rivanna/build_store.slurm plantref
```

## Aliases (post-build)

Header-name sequence aliases can be registered the same way as `jungle`
(per-source-authority namespaces from the `source` column):

```bash
python build_aliases.py plantref --seq-strategy header_names
```

(There is no `plantref` entry in `build_aliases.STORE_CONFIGS` yet, so pass the
strategy explicitly, or add a config block analogous to `jungle`'s.)
