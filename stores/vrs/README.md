# VRS RefgetStore

A VRS (Variant Representation Specification) compatible RefgetStore with
sequence-level aliases matching what vrs-python expects.

## Contents

This store includes human reference sequences from multiple sources:

**NCBI Genome Assemblies:**
- GRCh38 (GCF_000001405.26)
- GRCh38.p14 (GCF_000001405.40)
- GRCh37 (GCF_000001405.13)
- GRCh37.p13 (GCF_000001405.25)

**NCBI RefSeq Transcripts/Proteins:**
- 16 human protein FASTA files (NP_*, XP_*)
- 16 human transcript FASTA files (NM_*, NR_*, XM_*, XR_*)

**Ensembl (Release 113):**
- cdna transcripts (ENST*)
- ncrna transcripts
- protein sequences (ENSP*)

## Alias Namespaces

The store provides these alias namespaces for sequence lookup:

| Namespace | Description | Example |
|-----------|-------------|---------|
| GRCh38 | GRCh38 chromosome aliases | chr1, NC_000001.11, 1 |
| GRCh38.p14 | GRCh38.p14 aliases (incl. patches) | chr1, NC_000001.11 |
| GRCh37 | GRCh37 chromosome aliases | chr1, NC_000001.10, 1 |
| GRCh37.p13 | GRCh37.p13 aliases (incl. patches) | chr1, NC_000001.10 |
| refseq | RefSeq accessions | NM_001005484.2, NP_001005484.1 |
| insdc | GenBank/INSDC accessions | CM000663.2 |
| ensembl | Ensembl transcript/protein IDs | ENST00000456328.2 |

## Build Instructions

1. Build the store with the standard build script:

```bash
source ../../infra/rivanna/env.sh
python ../build.py vrs
```

2. Generate sequence-level alias TSVs:

```bash
python build_aliases.py $REFGETSTORE_BASE/vrs
```

3. Load aliases into the store (in Python):

```python
from pathlib import Path
from refget.store import RefgetStore

store = RefgetStore.on_disk("/path/to/vrs")
alias_dir = Path("aliases")

for tsv in alias_dir.glob("*.tsv"):
    namespace = tsv.stem
    count = store.load_sequence_aliases(namespace, str(tsv))
    print(f"Loaded {count} aliases for namespace {namespace}")
```

## Usage with vrs-python

The alias namespaces support the lookup patterns used by vrs-python:

```python
# Look up by UCSC-style name
seq = store.get_sequence_by_alias("GRCh38", "chr1")

# Look up by RefSeq accession
seq = store.get_sequence_by_alias("refseq", "NC_000001.11")

# Look up by Ensembl transcript
seq = store.get_sequence_by_alias("ensembl", "ENST00000456328.2")

# Get the GA4GH digest for VRS
digest = seq.metadata.sha512t24u  # e.g., "SQ.Ya6Rs7DHhDeg..."
```

## Related

- [GA4GH VRS Specification](https://vrs.ga4gh.org/)
- [vrs-python](https://github.com/ga4gh/vrs-python)
- [GA4GH Refget](https://samtools.github.io/hts-specs/refget.html)
