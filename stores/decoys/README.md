# decoys

Prealignment decoy sequences. These are **not** reference assemblies -- they are
derived constructs used to soak up reads before the main alignment, and they are
kept in their own store so they are not mistaken for references in `jungle`.

## Sources

Two doubled mitochondrial genomes, both PEPATAC prealignment targets:

| name | organism | derived from |
|---|---|---|
| `rCRSd` | Homo sapiens | revised Cambridge Reference Sequence, duplicated |
| `chrM2x` | Mus musculus | UCSC mm10 `chrM`, duplicated |

Each is a single ~33 kb sequence. The mitochondrial genome is circular, so a
plain linear chrM loses reads that span the origin; concatenating the sequence
to itself lets those reads align, which is why PEPATAC uses the doubled form.

**No `accession` column by design.** The obvious candidates (`NC_012920.1` for
rCRS, the mm10 assembly for chrM) identify the *singular* source sequences. These
collections contain doubled sequence and therefore different content and
different digests. Recording those accessions would assert an identity that does
not hold.

## Provenance

Recovered on 2026-07-27 from the Accbase-local refgenie1 deployment at
`datasets_downloaded/refgenie1/genomes/data/<digest>/fasta/default/`, which was
the only remaining copy of either sequence. They are needed by the lab-wide
registry before that deployment can be retired.

Their source digests were `jthDpfNIgzM5AGJlOkRtfnky4rXMBIUP` (rCRSd) and
`k1DiUF4K3GOEyfBV6kMtnSFAGVrkMixo` (chrM2x); ingest here should reproduce both,
keeping the Accbase catalog's references valid.
