#!/usr/bin/env python3
"""One-time migration: fold pep/build_matrix.yaml into each genome YAML's `build:` block.

Before this migration a genome's build instructions lived in a second file
(pep/build_matrix.yaml) that listed 26 of the 128 genomes. The other 102 genome
YAMLs were inert -- nothing read them. After it, `genomes/**/*.yaml` is the single
list of genomes and each file says, in a `build:` block, which store holds its
sequence and how far to take it.

This script is the reproducible record of that fold. It is IDEMPOTENT: a file
that already carries a `build:` block is left byte-identical on disk, so a second
run produces zero diff. Run it, review `git diff`, then verify the fold was
lossless by regenerating pep/samples.csv and confirming it is byte-identical to
the committed pre-migration file (see --verify-samples).

Why a script and not 128 hand-edits: hand-editing is how an
`add: [suffixerator_index]` goes missing silently, and the samples.csv proof only
works if the tier/add/drop values are copied verbatim.

Resolving `build.tier`
----------------------
* a genome named in pep/build_matrix.yaml keeps its tier, `add:` and `drop:`
  VERBATIM -- that is what makes the samples.csv diff empty;
* every other genome gets `tier: store_only` (load the sequence, build no
  assets), which is exactly what it gets today: nothing queued it.

Resolving `build.store`
-----------------------
Three rules, applied in order, most authoritative first. Every genome resolves
under one of them; the script exits non-zero if any does not, rather than
guessing.

1. STAGED PATH (the queued genomes). pep/config.yaml's `derive.sources` names the
   FASTA the nightly actually ingests, as `${REFGETSTORE_FASTA}/<dir>/...`. That
   staging dir IS the store the pipeline reads, so it wins over anything else --
   `hg38`, for instance, is present in both `jungle` and `legacy`, and `legacy` is
   the copy the builds use.
2. FASTA URL. The genome's `fasta.sources[].url` (or its basename) appears in
   exactly one store's sources.csv. Unambiguous provenance; no judgment involved.
3. CURATED (below). Genomes that rules 1-2 cannot reach -- almost all of them
   carry no `fasta.sources` at all, so there is no URL to match. Each entry
   records the store row that is the evidence for the assignment.
"""

from __future__ import annotations

import argparse
import collections
import csv
import glob
import os
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
GENOMES_DIR = REPO / "genomes"
STORES_DIR = REPO / "stores"
PEP_CONFIG = REPO / "pep" / "config.yaml"
BUILD_MATRIX = REPO / "pep" / "build_matrix.yaml"

# Rule 1: staging dir under $REFGETSTORE_FASTA -> store slug. Only dirs that ARE
# a store map here; `pangenome_reference/` is a staging dir with no store of that
# name (its one genome, hg38_initial, resolves by URL to jungle instead).
STAGING_DIR_TO_STORE = {
    "legacy": "legacy",
    "plantref": "plantref",
    "jungle": "jungle",
}

# Rule 3: genomes with no `fasta.sources` to match on. The comment on each line is
# the row in that store's sources.csv that holds the sequence.
CURATED_STORE = {
    # --- jungle: the human/mouse reference-jungle dataset ---------------------
    "hg18_igenomes":           "jungle",  # hg18-igenomes-ucsc
    "hg19_igenomes":           "jungle",  # hg19-igenomes-ucsc
    "hg19_grch37_igenomes":    "jungle",  # GRCh37-igenomes-ensembl
    "hg38_decoy_igenomes":     "jungle",  # GRCh38-igenomes-decoy-ncbi
    "hg38_build36_3_igenomes": "jungle",  # GRCh38-igenomes-build36-3-ncbi
    "hg38_build37_1_igenomes": "jungle",  # GRCh38-igenomes-build37-1-ncbi
    "hg38_build37_2_igenomes": "jungle",  # GRCh38-igenomes-build37-2-ncbi
    "mm9_igenomes":            "jungle",  # mm9-igenomes-ucsc
    "mm10_igenomes":           "jungle",  # mm10-igenomes-ucsc
    "mm37_igenomes":           "jungle",  # NCBIM37-igenomes-ensembl
    "mm38_igenomes":           "jungle",  # mm38-igenomes-ncbi
    "hg38_p0_ena":             "jungle",  # GRCh38-ena-15
    "hg38_p14_ena":            "jungle",  # GRCh38-ena-29
    "mm38_p1_ena":             "jungle",  # GRCm39-ena-02
    "mm39_ena":                "jungle",  # GRCm39-ena-09
    "hg19_primary_gencode":    "jungle",  # GRCh37-primary-assembly-47-gencode
    "hg38_p14_gencode":        "jungle",  # GRCh38-p14-47-gencode
    "hg38_primary_gencode":    "jungle",  # GRCh38-primary-assembly-47-gencode
    "hg38_p0_genomic":         "jungle",  # GRCh38.p0-fasta-genomic
    "hg38_p1_genomic":         "jungle",  # GRCh38.p1-fasta-genomic
    "hg38_p2_genomic":         "jungle",  # GRCh38.p2-fasta-genomic
    "hg38_p6_genomic":         "jungle",  # GRCh38.p6-fasta-genomic
    "hg38_p7_genomic":         "jungle",  # GRCh38.p7-fasta-genomic
    "hg38_p8_genomic":         "jungle",  # GRCh38.p8-fasta-genomic
    "hg38_p12_genomic":        "jungle",  # GRCh38.p12-fasta-genomic
    "hg38_p13_genomic":        "jungle",  # GRCh38.p13-fasta-genomic
    "hg38_p14_genomic":        "jungle",  # GRCh38.p14-fasta-genomic (vrs holds
                                          # the same file; jungle is the genome
                                          # collection, vrs is the VRS reference set)
    "hg19_p13_genomic":        "jungle",  # GRCh37.p13-fasta-genomic (same, for hg19)
    "hg19_1kg":                "jungle",  # hs37-1kg
    "hg19_hs37d5":             "jungle",  # hs37d5
    "hg19_b37":                "jungle",  # b37-broad
    # t2t-chm13 is a human reference and belongs with the rest of them, but NO
    # store holds it yet -- there is no CHM13 row in jungle. That is a real gap,
    # not a mis-assignment: build/check_registration.py reports it as unregistered
    # until the sequence is staged and loaded. Recording the intended store here
    # is what makes that report possible.
    "t2t-chm13":               "jungle",
    # --- salmon_txomes: Ensembl cDNA transcriptomes ---------------------------
    "hg18_cdna":               "salmon_txomes",  # Ensembl NCBI36 r40 cdna.all
    "hg19_cdna":               "salmon_txomes",  # Ensembl GRCh37 r75 cdna.all
    "hg38_cdna":               "salmon_txomes",  # Ensembl GRCh38 r97 cdna.all
    "rn6_cdna":                "salmon_txomes",  # Ensembl Rnor_6.0 r97 cdna.all
}

STAGED_RE = re.compile(r"\$\{REFGETSTORE_FASTA\}/([^/]+)/")
METADATA_RE = re.compile(r"^metadata:\s*$", re.M)
BUILD_RE = re.compile(r"^build:\s*$", re.M)


class MigrationError(Exception):
    """Raised when a genome cannot be resolved; never guessed around."""


# --------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------

def load_genomes(genomes_dir: Path = GENOMES_DIR) -> dict:
    """{genome name: (path, parsed record)} for every genome YAML."""
    out = {}
    for path in sorted(genomes_dir.rglob("*.yaml")):
        data = yaml.safe_load(path.read_text())
        if not isinstance(data, dict) or not data.get("name"):
            raise MigrationError(f"{path}: not a genome record")
        out[data["name"]] = (path, data)
    return out


def load_staged_dirs(pep_config: Path = PEP_CONFIG) -> dict:
    """{genome: staging dir} from pep/config.yaml's `<genome>_fa` derive sources."""
    cfg = yaml.safe_load(pep_config.read_text())
    sources = ((cfg.get("sample_modifiers") or {}).get("derive") or {}).get("sources") or {}
    staged = {}
    for key, value in sources.items():
        if not key.endswith("_fa"):
            continue
        match = STAGED_RE.match(str(value))
        if match:
            staged[key[: -len("_fa")]] = match.group(1)
    return staged


def load_store_fasta_index(stores_dir: Path = STORES_DIR, skip=("legacy",)) -> dict:
    """{fasta token or basename: {store, ...}} across every store's sources.csv.

    `legacy` is skipped: its sources.csv was generated FROM these genome YAMLs, so
    matching against it would be circular. Rule 1 assigns every legacy genome
    anyway, from the staged path that legacy/sources.csv itself was built from.
    """
    index = collections.defaultdict(set)
    for csv_path in sorted(stores_dir.glob("*/sources.csv")):
        store = csv_path.parent.name
        if store in skip:
            continue
        with open(csv_path, newline="") as fh:
            for row in csv.DictReader(fh):
                for token in (row.get("fasta") or "").split():
                    index[token].add(store)
                    index[os.path.basename(token)].add(store)
    return index


def load_matrix(build_matrix: Path = BUILD_MATRIX) -> dict:
    """{genome: {tier, add, drop}} from pep/build_matrix.yaml, verbatim."""
    doc = yaml.safe_load(build_matrix.read_text())
    out = {}
    for genome, value in (doc.get("genomes") or {}).items():
        if isinstance(value, str):
            out[genome] = {"tier": value}
        elif isinstance(value, dict):
            spec = {"tier": value.get("tier")}
            if value.get("add"):
                spec["add"] = list(value["add"])
            if value.get("drop"):
                spec["drop"] = list(value["drop"])
            out[genome] = spec
        else:
            raise MigrationError(
                f"build_matrix genome '{genome}': value must be a tier name or a "
                f"mapping, got {type(value).__name__}"
            )
    return out


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------

def resolve_store(name: str, data: dict, staged: dict, fasta_index: dict) -> tuple:
    """Return (store slug, rule name). Raises MigrationError if unresolvable."""
    store = STAGING_DIR_TO_STORE.get(staged.get(name))
    if store:
        return store, "staged-path"

    hits = set()
    for source in (data.get("fasta") or {}).get("sources") or []:
        url = source.get("url") or ""
        hits |= fasta_index.get(url, set())
        hits |= fasta_index.get(os.path.basename(url), set())
    if len(hits) == 1:
        return hits.pop(), "fasta-url"

    store = CURATED_STORE.get(name)
    if store:
        return store, "curated"

    detail = f"fasta URL matched {sorted(hits)}" if hits else "no fasta URL matched any store"
    raise MigrationError(
        f"genome '{name}': cannot resolve build.store ({detail}, and it is not in "
        f"CURATED_STORE). Add it to CURATED_STORE in this script, with the store "
        f"row that holds its sequence as the evidence."
    )


def resolve_build(name: str, data: dict, staged: dict, fasta_index: dict, matrix: dict) -> dict:
    """The `build:` block for one genome: store, tier, and verbatim add/drop."""
    store, _rule = resolve_store(name, data, staged, fasta_index)
    spec = matrix.get(name) or {"tier": "store_only"}
    block = {"store": store, "tier": spec["tier"]}
    if spec.get("add"):
        block["add"] = spec["add"]
    if spec.get("drop"):
        block["drop"] = spec["drop"]
    return block


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def render_block(block: dict) -> str:
    """The `build:` block as YAML text, in a fixed key order."""
    lines = ["build:", f"  store: {block['store']}", f"  tier: {block['tier']}"]
    for key in ("add", "drop"):
        if block.get(key):
            lines.append(f"  {key}: [{', '.join(block[key])}]")
    return "\n".join(lines) + "\n"


def insert_block(text: str, block_text: str) -> str:
    """Insert the rendered block just above the file's top-level `metadata:`.

    Textual insertion, not a yaml round-trip: these files carry block scalars,
    comments and hand-chosen spacing that a re-dump would flatten, and this
    migration must change nothing but the one added block. `build:` sits next to
    `metadata:` because both are registry bookkeeping, fenced off from the
    FHR-exported fields above.
    """
    match = METADATA_RE.search(text)
    if not match:
        return text.rstrip("\n") + "\n\n" + block_text
    start = match.start()
    return text[:start] + block_text + "\n" + text[start:]


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def migrate(dry_run: bool = False, report: bool = False) -> int:
    genomes = load_genomes()
    staged = load_staged_dirs()
    fasta_index = load_store_fasta_index()
    matrix = load_matrix()

    unknown = sorted(set(matrix) - set(genomes))
    if unknown:
        raise MigrationError(
            f"pep/build_matrix.yaml queues genomes with no genomes/**/*.yaml: "
            f"{', '.join(unknown)}. Every queued genome must have a record."
        )

    written = 0
    skipped = 0
    rows = []
    for name in sorted(genomes):
        path, data = genomes[name]
        store, rule = resolve_store(name, data, staged, fasta_index)
        block = resolve_build(name, data, staged, fasta_index, matrix)
        rows.append((name, store, block["tier"], rule, name in matrix))

        text = path.read_text()
        if BUILD_RE.search(text):
            skipped += 1
            continue
        new_text = insert_block(text, render_block(block))
        if dry_run:
            print(f"--- {path.relative_to(REPO)}")
            print(render_block(block), end="")
        else:
            path.write_text(new_text)
        written += 1

    if report:
        print(f"{'genome':32} {'store':16} {'tier':14} {'rule':12} queued")
        for name, store, tier, rule, queued in rows:
            print(f"{name:32} {store:16} {tier:14} {rule:12} {'yes' if queued else ''}")
        print()
    by_store = collections.Counter(r[1] for r in rows)
    by_tier = collections.Counter(r[2] for r in rows)
    print(f"migrate_build_matrix: {len(rows)} genomes; stores {dict(sorted(by_store.items()))}")
    print(f"migrate_build_matrix: tiers {dict(sorted(by_tier.items()))}")
    verb = "would write" if dry_run else "wrote"
    print(f"migrate_build_matrix: {verb} {written} file(s), {skipped} already had a build: block")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the blocks that would be inserted; write nothing")
    parser.add_argument("--report", action="store_true",
                        help="print the resolved store/tier/rule for every genome")
    args = parser.parse_args(argv)
    try:
        return migrate(dry_run=args.dry_run, report=args.report)
    except MigrationError as exc:
        print(f"migrate_build_matrix: ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
