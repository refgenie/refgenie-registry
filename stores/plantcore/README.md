# plantcore

Well-assembled plant, algal and model-organism reference genomes. This is the
published half of the lab's legacy refgenie plant-genome collection, which used
to be one store, `plantref`. The other half is [`plantsup`](../plantsup/README.md),
which is built but not published.

plantcore holds **98 genomes, one per species, about 305,000 sequences**
(94.5 Gbp). It is published to [api.refgenie.org](https://api.refgenie.org)
(see the `publish:` block in `project_config.yaml`).

## Which genomes are in plantcore

A genome is in plantcore if it passes one of these rules:

1. **It has refgenie assets.** The 5 genomes with a genome YAML and built
   assets always stay: `athaliana` (TAIR10), `celegans`, `dmelanogaster`,
   `spombe` and `yeast_s288c`.
2. **It has at most 6,000 sequences.**
3. **It is chromosome-scale** (N50 of at least 10 Mb) **with at most 60,000
   sequences.** This adds pearl millet, red clover, pepper, *Brassica
   oleracea*, wild sugarcane, asparagus and *Oryza brachyantha*.

Then each species keeps **one** assembly. For key species the assembly was
picked by hand: TAIR10, rice MSU 7.2 (japonica) and MBKBASE (indica), maize
B73 v4, wheat IWGSC 1.1, barley IBC_PGSB v2, soybean Wm82.a2, tomato ITAG 2.4,
potato v4.03, *Medicago* Mt4.0, poplar v3.1, grape 12x, *Brachypodium* v3.1,
sorghum v3.1, *Chlamydomonas* v5.5, *Physcomitrium* v3.3, *Marchantia* v3.1,
*Amborella* JGI v1.0 and spruce Pabies02. Where any other species had more than
one passing assembly, one was chosen and the rest went to plantsup.

Everything else is in [`plantsup`](../plantsup/README.md), whose README lists
each genome left out and why.

The FASTAs are shared with plantsup: both stores read from
`$REFGETSTORE_FASTA/plantref/` (`fasta_root` in `project_config.yaml`).

## Where the FASTAs came from

The collection started as 154 FASTA entries staged from the legacy flat-file dump at
`/project/shefflab/www/refgenie_plantref/` (files dated 2018–2020). Those files
used the old refgenie naming convention `<...descriptor...>-fasta-fasta`, where
the `-fasta-fasta` suffix encodes `{asset=fasta}-{seekkey=fasta}`, i.e. the file
*is* the genome FASTA. Every file was verified to be gzip-compressed valid FASTA
(despite having no `.gz` extension), so staging is a copy + rename to `.fa.gz`
(no recompression needed).

Coverage: 114 distinct organisms (plants, green/red algae, diatoms, a few
protists, and the usual model-organism controls, *Homo sapiens*, *Mus
musculus*, *Drosophila melanogaster*, *Caenorhabditis elegans*,
*Saccharomyces cerevisiae*, *Schizosaccharomyces pombe*).

## Pabies02 header normalization

ENA serves these records with pipe-delimited database prefixes and a long
description (`>ENA|OZ038344|OZ038344.1 Picea abies genome assembly, chromosome:
01`). Headers were normalized to the bare submitter name, `01`..`12` for the
chromosomes and `PA_chr01_sUL001`-style names for the unplaced contigs:

```bash
zcat raw.fa.gz | sed -E 's/^>.*(chromosome|contig): (.+)$/>\2/' | gzip -c > Pabies02.fa.gz
```

This matters because sequence digests depend only on sequence content, but the
**collection digest incorporates the names digest**, so header style permanently
fixes the collection's identity. No other plantref FASTA contains a pipe in its
first header, and only 5 of the original 156 carry any description text; every
other collection uses a short bare name (`>chr1`, `>Chr01`, `>scaffold_1`, `>1`).
Keeping the ENA headers verbatim would have made Pabies02 the sole outlier and
would also have poisoned `build_aliases.py --seq-strategy header_names`, which
would register those full pipe-delimited strings as sequence aliases.

Pabies02 is also the first plantref entry with a populated `accession` column
(see the naming assumptions below; the legacy rows have none).

## Brick layout

Staged copies live under the sibling FASTA brickyard, mirroring the `jungle`
convention (`<store>/<organism>/<source>/<assembly>.fa.gz`):

```
/project/shefflab/brickyard/datasets_downloaded/refgenomes_fasta/fasta/plantref/
  <organism_dir>/<source>/<assembly_slug>.fa.gz
```

`<organism_dir>` is the lowercase `genus_species` (with subspecies/strain
suffixes preserved where present). The originals are COPIES only, the source
files under `www/refgenie_plantref/` are left untouched.

## Naming assumptions

These are old curated copies ingested **without** re-verifying upstream, so the
metadata is parsed from each legacy filename:

- **organism**, the leading `Genus species` tokens. Subspecies / strain
  variants (`Oryza sativa subsp. indica/japonica`, `Saccharomyces cerevisiae
  strain S288C`, `Chlorella sp. NC64A`, `Picochlorum sp. SENEW3`, etc.) are
  special-cased so the species is not truncated.
- **source**, the apparent data-producing authority baked into the filename
  (`JGI`, `Ensembl`, `NCBI`, `RefSeq`, `GenBank`, `MIPS`, `MSU`, `JCVI`,
  `Phytozome`, `Ghent`, `SolGenomics`, `CucurbitGDB`, ..., 40 distinct values).
  When more than one authority appears, the data producer wins (e.g. JGI over
  the Phytozome portal). 24 rows carry no recognizable authority in the
  filename and use the fallback source `plantref`. A handful of build-name-only
  tags (`TAIR10`, `Araport11`, `IWGSC1_1`) are likewise left as `plantref`
  since they name a build, not an authority.
- **genome_assembly**, the remaining descriptor tokens after the organism,
  slugified.
- **accession**, left blank for the legacy rows. These are old curated copies;
  the original GCA/GCF accessions were not recorded in the filenames. The one
  exception is `picea_abies_Pabies02`, which was fetched from ENA against a
  known accession (see above).
- **name** / **pep_sample_name**, `<genus_species>_<assembly_slug>`, made
  unique where two assemblies would otherwise collide.

## Build

```bash
source ../infra/rivanna/env.sh
python build.py plantcore
# on Rivanna (submit from repo root):
sbatch --job-name=build-plantcore infra/rivanna/build_store.slurm plantcore
```

The nightly builds it like any other store when its `sources.csv` changes.
