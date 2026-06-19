# salmon_txomes

Transcriptomes used by the Salmon/tximeta toolchain. Sources are parsed directly from tximeta's [`hashtable.csv`](https://github.com/thelovelab/tximeta/blob/devel/inst/extdata/hashtable.csv).

## Contents

- 314 entries, 225 unique transcriptomes
- **GENCODE**: human releases 23–49, mouse M6–M38 (60 entries)
- **Ensembl**: human/mouse/drosophila releases 76–115 (236 entries, cdna and cdna+ncrna variants)
- **RefSeq**: human GRCh38.p1–p13, mouse GRCm38.p2–p6 (18 entries)

## Notes

- 118 Ensembl rows have space-separated FASTA URL pairs (cdna + ncrna). These must be downloaded separately and concatenated before loading.
- The `sha256` column contains Salmon's legacy index hash for cross-referencing.
- `sources.csv` is a direct copy of tximeta's hashtable.csv.
- GTF URLs are included for each transcriptome, enabling annotation metadata.

## Purpose

This store enables migration of tximeta from its bespoke SHA-256 lookup table to GA4GH seqcol identifiers. Once built, a seqcolapi instance serving this store replaces tximeta's manually curated `hashtable.csv`.

## References

- [tximeta](https://github.com/thelovelab/tximeta) (Bioconductor R package)
- [Salmon](https://github.com/COMBINE-lab/salmon) (RNA-seq quantification)
- [Love et al., 2020](https://doi.org/10.1371/journal.pcbi.1007664) (tximeta paper)
