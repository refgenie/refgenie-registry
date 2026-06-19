# plantref

Plant (and assorted algal / protist / model-organism) reference genomes from the
lab's legacy refgenie plant-genome collection.

## Sources

`sources.csv` contains 156 FASTA entries staged from the legacy flat-file dump at
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
- **accession** — left blank. These are old curated copies; the original
  GCA/GCF accessions were not recorded in the filenames.
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
