#!/usr/bin/env python3
"""Generate sequence-level alias TSVs for VRS-compatible RefgetStore.

This script processes NCBI assembly reports and FASTA files to create
alias mappings for the VRS namespace requirements:
  - GRCh38, GRCh38.p14, GRCh37, GRCh37.p13 (chromosome-level aliases)
  - refseq (NC_*, NM_*, NP_* accessions)
  - insdc (GenBank accessions like CM000663.2)
  - ensembl (ENST*, ENSP* identifiers)

Output: TSV files in aliases/ directory with format: alias<TAB>sha512t24u_digest

Usage:
    python build_aliases.py <store_path>

The script requires a RefgetStore that has already been populated with
sequences. It reads the store to get sha512t24u digests and maps them
to aliases based on the sequence names from FASTA headers.
"""

import gzip
import os
import re
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

# Assembly report URLs for each genome build
ASSEMBLY_REPORTS = {
    "GRCh38": "https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/000/001/405/GCF_000001405.26_GRCh38/GCF_000001405.26_GRCh38_assembly_report.txt",
    "GRCh38.p14": "https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/000/001/405/GCF_000001405.40_GRCh38.p14/GCF_000001405.40_GRCh38.p14_assembly_report.txt",
    "GRCh37": "https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/000/001/405/GCF_000001405.13_GRCh37/GCF_000001405.13_GRCh37_assembly_report.txt",
    "GRCh37.p13": "https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/000/001/405/GCF_000001405.25_GRCh37.p13/GCF_000001405.25_GRCh37.p13_assembly_report.txt",
}


def download_assembly_report(url: str, dest_dir: Path) -> str:
    """Download assembly report if not already cached."""
    filename = url.split("/")[-1]
    dest = dest_dir / filename
    if dest.exists():
        return str(dest)
    print(f"  Downloading {filename}...")
    urllib.request.urlretrieve(url, dest)
    return str(dest)


def parse_assembly_report(report_path: str) -> dict:
    """Parse NCBI assembly report to extract sequence alias mappings.

    The assembly_report.txt format has these columns (tab-separated):
    0: Sequence-Name (e.g., "1", "MT")
    1: Sequence-Role (e.g., "assembled-molecule", "unplaced-scaffold")
    2: Assigned-Molecule (e.g., "1", "MT", "na")
    3: Assigned-Molecule-Location/Type (e.g., "Chromosome", "Mitochondrion")
    4: GenBank-Accn (e.g., "CM000663.2")
    5: Relationship (e.g., "=", "<>")
    6: RefSeq-Accn (e.g., "NC_000001.11")
    7: Assembly-Unit (e.g., "Primary Assembly")
    8: Sequence-Length (e.g., "248956422")
    9: UCSC-style-name (e.g., "chr1")

    Returns:
        Dict with keys 'refseq_to_ucsc', 'refseq_to_genbank', 'refseq_to_name'
        mapping RefSeq accession to the respective alias.
    """
    mappings = {
        "refseq_to_ucsc": {},
        "refseq_to_genbank": {},
        "refseq_to_name": {},
    }

    with open(report_path, "r") as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.strip().split("\t")
            if len(parts) < 10:
                continue

            seq_name = parts[0]
            genbank = parts[4]
            refseq = parts[6]
            ucsc = parts[9] if len(parts) > 9 else "na"

            if refseq and refseq != "na":
                if ucsc and ucsc != "na":
                    mappings["refseq_to_ucsc"][refseq] = ucsc
                if genbank and genbank != "na":
                    mappings["refseq_to_genbank"][refseq] = genbank
                if seq_name and seq_name != "na":
                    mappings["refseq_to_name"][refseq] = seq_name

    return mappings


def build_assembly_aliases(store, alias_dir: Path, cache_dir: Path):
    """Build alias TSVs for genome assemblies from NCBI assembly reports.

    Creates namespace TSVs for GRCh38, GRCh38.p14, GRCh37, GRCh37.p13
    with aliases for:
      - RefSeq accessions (NC_000001.11)
      - UCSC-style names (chr1)
      - GenBank accessions (CM000663.2)
      - Simple names (1, X, MT)
    """
    # Build mapping from sequence name (from FASTA header) to digest
    sequences = store.list_sequences()
    name_to_digest = {}
    for seq in sequences:
        # The FASTA header name is stored in seq.name
        # For NCBI FASTAs, this is typically the RefSeq accession (NC_000001.11)
        if seq.name:
            name_to_digest[seq.name] = seq.sha512t24u

    # Track all RefSeq and GenBank aliases across assemblies
    all_refseq_aliases = {}
    all_insdc_aliases = {}

    for assembly_name, report_url in ASSEMBLY_REPORTS.items():
        print(f"  Processing {assembly_name} assembly report...")

        try:
            report_path = download_assembly_report(report_url, cache_dir)
            mappings = parse_assembly_report(report_path)
        except Exception as e:
            print(f"    Warning: Could not process {assembly_name}: {e}")
            continue

        # Build assembly-specific alias TSV
        aliases = []

        for refseq_acc, ucsc_name in mappings["refseq_to_ucsc"].items():
            digest = name_to_digest.get(refseq_acc)
            if digest:
                # Add UCSC-style alias (chr1)
                aliases.append((ucsc_name, digest))
                # Add RefSeq alias to namespace-specific file too
                aliases.append((refseq_acc, digest))
                # Track for global refseq namespace
                all_refseq_aliases[refseq_acc] = digest

        for refseq_acc, genbank_acc in mappings["refseq_to_genbank"].items():
            digest = name_to_digest.get(refseq_acc)
            if digest:
                # Track for global insdc namespace
                all_insdc_aliases[genbank_acc] = digest

        for refseq_acc, simple_name in mappings["refseq_to_name"].items():
            digest = name_to_digest.get(refseq_acc)
            if digest:
                # Add simple name (1, X, MT)
                aliases.append((simple_name, digest))

        # Write assembly-specific TSV
        if aliases:
            tsv_path = alias_dir / f"{assembly_name}.tsv"
            with open(tsv_path, "w") as f:
                for alias, digest in sorted(set(aliases)):
                    f.write(f"{alias}\t{digest}\n")
            print(f"    Wrote {len(set(aliases))} aliases to {tsv_path.name}")

    # Write global refseq namespace TSV
    if all_refseq_aliases:
        tsv_path = alias_dir / "refseq_assembly.tsv"
        with open(tsv_path, "w") as f:
            for alias, digest in sorted(all_refseq_aliases.items()):
                f.write(f"{alias}\t{digest}\n")
        print(f"    Wrote {len(all_refseq_aliases)} aliases to refseq_assembly.tsv")

    # Write global insdc namespace TSV
    if all_insdc_aliases:
        tsv_path = alias_dir / "insdc.tsv"
        with open(tsv_path, "w") as f:
            for alias, digest in sorted(all_insdc_aliases.items()):
                f.write(f"{alias}\t{digest}\n")
        print(f"    Wrote {len(all_insdc_aliases)} aliases to insdc.tsv")


def build_refseq_transcript_aliases(store, alias_dir: Path):
    """Build alias TSV for RefSeq transcripts/proteins (NM_*, NP_*, XM_*, XP_*).

    Parses FASTA headers like:
      >NM_001005484.2 Homo sapiens olfactory receptor...

    Extracts the accession.version as the alias.
    """
    sequences = store.list_sequences()
    aliases = {}

    # RefSeq accession patterns
    refseq_pattern = re.compile(r"^(NM_|NP_|NR_|XM_|XP_|XR_)\d+\.\d+")

    for seq in sequences:
        if seq.name:
            # Check if it looks like a RefSeq accession
            match = refseq_pattern.match(seq.name)
            if match:
                aliases[seq.name] = seq.sha512t24u

    if aliases:
        tsv_path = alias_dir / "refseq.tsv"
        with open(tsv_path, "w") as f:
            for alias, digest in sorted(aliases.items()):
                f.write(f"{alias}\t{digest}\n")
        print(f"    Wrote {len(aliases)} aliases to refseq.tsv")


def build_ensembl_aliases(store, alias_dir: Path):
    """Build alias TSV for Ensembl transcripts/proteins (ENST*, ENSP*).

    Parses FASTA headers like:
      >ENST00000456328.2 cdna chromosome:GRCh38:1:...
      >ENSP00000456328.1 pep chromosome:GRCh38:1:...

    Extracts the Ensembl ID with version as the alias.
    """
    sequences = store.list_sequences()
    aliases = {}

    # Ensembl ID patterns
    ensembl_pattern = re.compile(r"^(ENST|ENSP|ENSG)\d+\.\d+")

    for seq in sequences:
        if seq.name:
            # Check if it looks like an Ensembl ID
            match = ensembl_pattern.match(seq.name)
            if match:
                aliases[seq.name] = seq.sha512t24u

    if aliases:
        tsv_path = alias_dir / "ensembl.tsv"
        with open(tsv_path, "w") as f:
            for alias, digest in sorted(aliases.items()):
                f.write(f"{alias}\t{digest}\n")
        print(f"    Wrote {len(aliases)} aliases to ensembl.tsv")


def main():
    if len(sys.argv) < 2:
        print("Usage: build_aliases.py <store_path>", file=sys.stderr)
        sys.exit(1)

    store_path = Path(sys.argv[1])
    if not store_path.exists():
        print(f"Store not found: {store_path}", file=sys.stderr)
        sys.exit(1)

    # Import here to allow script to show help without gtars
    from refget.store import RefgetStore

    print(f"Loading store from {store_path}...")
    store = RefgetStore.on_disk(str(store_path))

    # Create output directories
    alias_dir = Path(__file__).parent / "aliases"
    alias_dir.mkdir(exist_ok=True)

    cache_dir = store_path.parent / ".assembly_reports"
    cache_dir.mkdir(exist_ok=True)

    print("Building assembly aliases from NCBI reports...")
    build_assembly_aliases(store, alias_dir, cache_dir)

    print("Building RefSeq transcript/protein aliases...")
    build_refseq_transcript_aliases(store, alias_dir)

    print("Building Ensembl aliases...")
    build_ensembl_aliases(store, alias_dir)

    print(f"\nAlias TSVs written to: {alias_dir}")
    print("Load into store with: store.load_sequence_aliases(namespace, tsv_path)")


if __name__ == "__main__":
    main()
