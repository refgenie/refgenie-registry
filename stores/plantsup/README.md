# plantsup

The supplementary half of the lab's legacy refgenie plant-genome collection,
which used to be one store, `plantref`. It holds every assembly that did not
make it into [`plantcore`](../plantcore/README.md): **49 genomes, about
1.34 million sequences** (35.9 Gbp).

plantsup is **built nightly and mirrored to S3, but not published**: its
`project_config.yaml` has no `publish:` block, so it never reaches the
catalog or [api.refgenie.org](https://api.refgenie.org). The server holds
every published sequence's metadata in memory, and these 1.34 million
sequences are too many for it today. Publish it once gtars holds sequence
metadata compactly.

The FASTAs are shared with plantcore: both stores read from
`$REFGETSTORE_FASTA/plantref/` (`fasta_root` in `project_config.yaml`). Where
the files came from and how they are named is in plantcore's README.

## What was left out of plantcore, and why

plantcore's rules are in [its README](../plantcore/README.md). A genome is
here for one of two reasons.

**Fragmented assemblies** (20 genomes): more than 6,000 sequences and
not chromosome-scale (N50 under 10 Mb, or more than 60,000 sequences).

| Collection | Species | Sequences | N50 (Mb) |
|---|---|---|---|
| `phyllostachys_edulis_NCGR_v1_0` | *Phyllostachys edulis* | 277,278 | 0.33 |
| `phalaenopsis_equestris_Genomics_v1_0` | *Phalaenopsis equestris* | 236,185 | 0.36 |
| `cajanus_cajan_Penguin_Genomics_v5_0` | *Cajanus cajan* | 137,542 | 0.52 |
| `malus_domestica_IASMA` | *Malus domestica* | 122,163 | 0.02 |
| `petunia_axillaris_Sol_Genomics_v1_6_2` | *Petunia axillaris* | 83,639 | 1.24 |
| `beta_vulgaris_RefBeet_1_1` | *Beta vulgaris* | 43,722 | 1.69 |
| `beta_vulgaris_RefBeet_v1_2` | *Beta vulgaris* | 40,508 | 2.01 |
| `elaeis_guineensis_EG5_1` | *Elaeis guineensis* | 40,072 | 1.27 |
| `elaeis_guineensis_NCBI_Genome_v1_0` | *Elaeis guineensis* | 40,061 | 1.27 |
| `cucumis_melo_Melonomics_v3_5` | *Cucumis melo* | 31,464 | 4.29 |
| `ricinus_communis_JCVI_1_0`, `ricinus_communis_JGI_v0_1` | *Ricinus communis* | 25,828 | 0.50 |
| `corchorus_olitorius_GenBank_v1_0` | *Corchorus olitorius* | 24,918 | 0.04 |
| `carica_papaya_Hawaii_Agriculture_Research_Center` | *Carica papaya* | 17,765 | 1.09 |
| `manihot_esculenta_Cassava4` | *Manihot esculenta* | 12,978 | 0.26 |
| `citrus_sinensis_JGI_v1_assembly_and_v1_0_annotation` | *Citrus sinensis* | 12,575 | 0.25 |
| `tarenaya_hassleriana_RefSeq_v1` | *Tarenaya hassleriana* | 12,249 | 1.60 |
| `zoysia_japonica_Zoysia_Genome_Database_v1_1` | *Zoysia japonica subsp. nagirizaki* | 11,786 | 2.37 |
| `malus_domestica_Penguin_Genomics_v2` | *Malus domestica* | 9,675 | 0.11 |
| `emiliania_huxleyi_ENSEMBL_protist_release_28` | *Emiliania huxleyi* | 7,795 | 0.40 |
| `hevea_brasiliensis_NCBI_Genomes_v1` | *Hevea brasiliensis* | 7,452 | 1.28 |

**Another assembly of the same species is in plantcore** (29 genomes):
plantcore keeps one assembly per species.

| Collection | Species | Sequences | N50 (Mb) |
|---|---|---|---|
| `amborella_trichopoda_Amborella_V1_0` | *Amborella trichopoda* | 5,746 | 4.93 |
| `amborella_trichopoda_Amborella_v1` | *Amborella trichopoda* | 5,745 | 4.93 |
| `arabidopsis_thaliana_COL-0_Araport11` | *Arabidopsis thaliana* | 7 | 23.46 |
| `brachypodium_distachyon_MIPS_1_2` | *Brachypodium distachyon* | 84 | 59.33 |
| `brassica_rapa_Annotation_v1_2_on_assembly_v1_1_Phytozome_9_1` | *Brassica rapa* | 40,368 | 26.27 |
| `chlamydomonas_reinhardtii_JGI_5_5_Phytozome_10_2` | *Chlamydomonas reinhardtii* | 90 | 6.62 |
| `chlamydomonas_reinhardtii_JGI_v5_0_assembly_annot_v5_3_1_based_on_Augustus_u11_6` | *Chlamydomonas reinhardtii* | 56 | 7.78 |
| `citrullus_lanatus_Cucurbit_Genomics_Database` | *Citrullus lanatus* | 13 | 28.94 |
| `eucalyptus_grandis_JGI_assembly_v1_0_annotation_v1_1` | *Eucalyptus grandis* | 4,953 | 53.89 |
| `fragaria_vesca_Strawberry_Genome_1_0` | *Fragaria vesca* | 1,087 | 29.38 |
| `glycine_max_JGI_Glyma1_1_annotation_of_the_chromosome-based_Glyma1_assembly` | *Glycine max* | 1,169 | 47.78 |
| `gossypium_raimondii_JGI_annotation_v2_1_on_assembly_v2_0` | *Gossypium raimondii* | 1,034 | 62.18 |
| `hordeum_vulgare_Ensembl_Genomes_ASM32608v1` | *Hordeum vulgare* | 19,705 | 561.41 |
| `medicago_truncatula_JCVI_4_0` | *Medicago truncatula* | 1,950 | 49.17 |
| `musa_acuminata_Banana_Genome_v1_0`, `musa_acuminata_Genescope-Cirad` | *Musa acuminata* | 12 | 34.15 |
| `oryza_sativa_9311_BGF_2005` | *Oryza sativa subsp. indica* | 10,493 | 31.24 |
| `oryza_sativa_JGI_v7_0` | *Oryza sativa subsp. japonica* | 14 | 29.96 |
| `physcomitrella_patens_JGI_assembly_release_v1_1_and_COSMOSS_annotation_v1_6` | *Physcomitrella patens* | 2,108 | 1.32 |
| `physcomitrella_patens_Phytozome_9_1_v1_6` | *Physcomitrella patens* | 2,108 | 1.32 |
| `populus_trichocarpa_JGI_assembly_release_v3_0_annotation_v3_0` | *Populus trichocarpa* | 1,447 | 19.47 |
| `prunus_persica_JGI_release_v1_0` | *Prunus persica* | 203 | 26.81 |
| `selaginella_moellendorffii_NCBIr100` | *Selaginella moellendorffii* | 757 | 1.75 |
| `solanum_lycopersicum_ITAG_2_3` | *Solanum lycopersicum* | 14 | 64.84 |
| `solanum_tuberosum_ITAG_001` | *Solanum tuberosum* | 13 | 61.10 |
| `sorghum_bicolor_JGI_1_4` | *Sorghum bicolor* | 3,306 | 62.35 |
| `theobroma_cacao_D_Gilbert_public_gene_set_8_Mar_2012_on_assembly_v1_1` | *Theobroma cacao* | 713 | 34.40 |
| `vitis_vinifera_genescope_v1` | *Vitis vinifera* | 35 | 23.01 |
| `zea_mays_B73_5b_60` | *Zea mays* | 13 | 217.87 |
| `zea_mays_PH207_v1_0` | *Zea mays* | 43,291 | 215.15 |

## History

### Split from plantref (2026-11)

`plantref` held all 147 collections (about 1.57 million sequences). That was
too many sequences for the server to load, so plantref was never published.
It was split by row into plantcore (98 genomes, about 305,000 sequences,
published) and plantsup (this store). Rows that share a collection stayed in
the same store. Both stores were built fresh from the same FASTAs, so every
collection digest is unchanged.

### Pruned from plantref (2026-07)

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

Because `build.py` is additive only, it never removes, editing `sources.csv`
alone does **not** drop a collection from a built store. Removal is a separate,
explicit operation via `stores/remove_collections.py`, and the S3 sync must run
with `--delete` so the orphaned objects do not linger.

How the Pabies02 FASTA headers were normalized is in plantcore's README.
