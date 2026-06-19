#!/usr/bin/env python3
"""Post-build alias registration for refgenie-registry RefgetStores.

This is a *post-build* step: it runs after ``build.py`` has ingested all the
FASTAs for a store and registered the collection-level aliases from the
``ALIAS_COLUMNS`` ({name, accession, genome_assembly}) in ``sources.csv``.

It does NOT re-ingest any FASTA. Each ``sources.csv`` row is resolved to an
already-built collection digest by looking up the collection aliases that
``build.py`` wrote (``name`` -> ``accession`` -> ``genome_assembly``). It then
registers two further kinds of aliases:

1. Accession collection aliases (``insdc`` / ``refseq`` namespaces) derived
   from a GenBank/RefSeq assembly accession (GCA_/GCF_) in the ``accession``
   column. ``build.py`` puts the raw accession under the generic ``accession``
   namespace; this also files it under the canonical INSDC/RefSeq namespace so
   it can be looked up the same way as the VRS store.

2. Sequence-level aliases, via two strategies (per store config):

   a. ``header_names`` -- register each collection's FASTA header name (from
      level2 ``names``) as a sequence alias under a namespace. Useful when the
      headers already are the desired identifier (UCSC ``chr1``, Ensembl ``1``,
      NCBI ``NC_000001.11``). Cheap, needs no network.

   b. ``assembly_report`` -- download/parse the NCBI ``assembly_report.txt``
      for each row's GCA/GCF accession (or an explicit ``assembly_report``
      column), build the RefSeq<->GenBank<->UCSC<->name mapping, match it to
      the collection's sequences by name+length, and register every alias form
      (``refseq`` / ``insdc`` / ``ucsc``) pointing at the matched sequence
      digest. This is the richest option and mirrors the legacy
      ``backfill_sequence_aliases.py`` from refget/analysis.

Per-store behaviour is read from the ``aliasing:`` block in each store's
``project_config.yaml`` (and is overridable on the command line). A store with
no ``aliasing:`` block gets collection aliases only. Output is written into the
store in place.

Usage:
    source ../infra/rivanna/env.sh
    python build_aliases.py jungle                 # use the store's aliasing: config
    python build_aliases.py vgp --dry-run
    python build_aliases.py vgp --store-path /tmp/test_store
    python build_aliases.py jungle --seq-strategy header_names

Requirements: refget + gtars (RefgetStore) and pyyaml; Python stdlib otherwise.
"""

import argparse
import csv
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

import yaml

SCRIPT_DIR = Path(__file__).parent
PEP_CONFIG = "project_config.yaml"
ACCESSION_PATTERN = re.compile(r"(GC[AF]_\d+\.\d+)")
NCBI_FTP_BASE = "https://ftp.ncbi.nlm.nih.gov/genomes/all"
# get_collection_level2() returns VRS-style "SQ."/"ga4gh:SQ." prefixed sequence
# digests, but the store's sequence-alias index is keyed by the bare
# sha512t24u digest (matching list_sequences()[].sha512t24u). Strip the prefix
# before registering sequence aliases or lookups silently miss.
_SQ_PREFIX = re.compile(r"^(ga4gh:)?SQ\.")


def normalize_seq_digest(digest):
    return _SQ_PREFIX.sub("", digest) if digest else digest


# ---------------------------------------------------------------------------
# Per-store configuration
# ---------------------------------------------------------------------------
# Each store declares how its sequence aliases are built in the `aliasing:`
# block of its stores/<store>/project_config.yaml. Recognized keys:
#   seq_strategy:
#     "none"            - collection aliases only (default when no block)
#     "header_names"    - register FASTA header names as sequence aliases
#     "assembly_report" - parse NCBI assembly reports for cross-accession aliases
#   header_namespace:     namespace under which header-name aliases are filed
#   header_namespace_col: column whose value names the namespace (per authority)
#   assembly_report_when_accession: also pull assembly-report aliases for rows
#                                    that carry a GCA/GCF accession
#
# (vrs is deliberately absent: it ships its own stores/vrs/build_aliases.py with
# VRS-specific namespace logic, so it has no `aliasing:` block here.)
def load_aliasing_config(store_name):
    """Read the `aliasing:` block from stores/<store>/project_config.yaml.

    Returns a config dict for build_aliases(), defaulting to collection-aliases
    only ({"seq_strategy": "none"}) when the store declares no `aliasing:` block.
    """
    default = {"seq_strategy": "none"}
    config_path = SCRIPT_DIR / store_name / PEP_CONFIG
    if not config_path.exists():
        return dict(default)
    try:
        with open(config_path) as f:
            pep = yaml.safe_load(f) or {}
    except Exception as e:
        print(f"WARNING: could not read {config_path}: {e}", file=sys.stderr)
        return dict(default)
    aliasing = pep.get("aliasing")
    if not isinstance(aliasing, dict):
        return dict(default)
    return {**default, **aliasing}


# ---------------------------------------------------------------------------
# Collection digest resolution (read-only against the built store)
# ---------------------------------------------------------------------------
def resolve_collection_digest(store, row):
    """Return the collection digest for a sources.csv row, or None.

    Uses the collection aliases that build.py already registered. Tries the
    most-specific identifier first. Also tries the canonical insdc/refseq
    namespaces keyed by the accession value, so already-aliased stores (e.g.
    the legacy vgp backfill, which only filed insdc/refseq) still resolve.
    """
    for namespace in ("name", "accession", "genome_assembly"):
        val = (row.get(namespace) or "").strip()
        if not val:
            continue
        result = store.get_collection_by_alias(namespace, val)
        if result is not None:
            return result.digest

    acc = (row.get("accession") or "").strip()
    m = ACCESSION_PATTERN.search(acc) if acc else None
    if m:
        acc = m.group(1)
        for namespace in ("insdc", "refseq"):
            result = store.get_collection_by_alias(namespace, acc)
            if result is not None:
                return result.digest
    return None


# ---------------------------------------------------------------------------
# Assembly report download + parse (adapted from analysis build_ncbi_alias_table)
# ---------------------------------------------------------------------------
def accession_to_ftp_dir(accession):
    m = re.match(r"(GC[AF])_(\d+)\.\d+", accession)
    if not m:
        return None
    prefix, numeric = m.group(1), m.group(2).zfill(9)
    d1, d2, d3 = numeric[0:3], numeric[3:6], numeric[6:9]
    return f"{NCBI_FTP_BASE}/{prefix}/{d1}/{d2}/{d3}/"


def lookup_assembly_name_from_ftp(accession):
    """Scrape the NCBI FTP listing for the assembly-name subdirectory."""
    dir_url = accession_to_ftp_dir(accession)
    if not dir_url:
        return None
    try:
        req = urllib.request.Request(
            dir_url, headers={"User-Agent": "refget-alias-builder/1.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        m = re.search(re.escape(accession) + r"_([^/\"]+)/", html)
        if m:
            return m.group(1)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        pass
    return None


def report_url_for_accession(accession):
    """Construct the assembly_report.txt URL for a GCA/GCF accession."""
    dir_url = accession_to_ftp_dir(accession)
    if not dir_url:
        return None
    asm = lookup_assembly_name_from_ftp(accession)
    if not asm:
        return None
    stem = f"{accession}_{asm}"
    return f"{dir_url}{stem}/{stem}_assembly_report.txt"


def fetch_assembly_report(accession, report_hint, cache_dir, sleep_sec=0.3):
    """Return a local path to the assembly report, or None.

    report_hint may be an explicit URL/path from an ``assembly_report`` column.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{accession or 'report'}_assembly_report.txt"
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return str(cache_path)

    url = None
    if report_hint:
        if os.path.exists(report_hint):
            return report_hint
        url = report_hint
    elif accession:
        url = report_url_for_accession(accession)
    if not url:
        return None

    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "refget-alias-builder/1.0"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        cache_path.write_bytes(data)
        time.sleep(sleep_sec)
        return str(cache_path)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        print(f"    report fetch failed for {accession}: {e}", file=sys.stderr)
        return None


def parse_assembly_report(path):
    """Parse an assembly_report.txt into per-sequence and assembly-level dicts.

    Returns (rows, genbank_assembly_accn, refseq_assembly_accn) where each row
    is {sequence_name, sequence_length, refseq_accn, genbank_accn, ucsc_name}.
    """
    genbank_asm = refseq_asm = ""
    rows = []
    with open(path, errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("#"):
                if "GenBank assembly accession:" in line:
                    m = ACCESSION_PATTERN.search(line)
                    if m:
                        genbank_asm = m.group(1)
                elif "RefSeq assembly accession:" in line:
                    m = ACCESSION_PATTERN.search(line)
                    if m:
                        refseq_asm = m.group(1)
                continue
            fields = line.split("\t")
            if len(fields) < 9:
                continue
            norm = lambda v: "" if v.strip() == "na" else v.strip()
            rows.append(
                {
                    "sequence_name": fields[0].strip(),
                    "genbank_accn": norm(fields[4]) if len(fields) > 4 else "",
                    "refseq_accn": norm(fields[6]) if len(fields) > 6 else "",
                    "sequence_length": norm(fields[8]) if len(fields) > 8 else "",
                    "ucsc_name": norm(fields[9]) if len(fields) > 9 else "",
                }
            )
    return rows, genbank_asm, refseq_asm


# ---------------------------------------------------------------------------
# Sequence-name -> (digest, length) lookup for one collection
# ---------------------------------------------------------------------------
def name_to_info_for_collection(store, coll_digest):
    level2 = store.get_collection_level2(coll_digest)
    names = level2.get("names", [])
    lengths = level2.get("lengths", [])
    sequences = level2.get("sequences", [])
    return {n: (normalize_seq_digest(s), int(l)) for n, l, s in zip(names, lengths, sequences)}


# ---------------------------------------------------------------------------
# Main alias accumulation
# ---------------------------------------------------------------------------
def build_aliases(store, rows, config, cache_dir, dry_run=False):
    """Accumulate collection + sequence aliases and load them into the store."""
    seq_aliases = defaultdict(list)   # namespace -> [(alias, seq_digest)]
    coll_aliases = defaultdict(list)  # namespace -> [(alias, coll_digest)]

    seq_strategy = config.get("seq_strategy", "none")
    header_ns = config.get("header_namespace")
    header_ns_col = config.get("header_namespace_col")
    report_when_acc = config.get("assembly_report_when_accession", False)

    n_resolved = n_unresolved = 0
    n_seq_matched = n_seq_unmatched = 0

    for i, row in enumerate(rows, 1):
        label = (row.get("name") or row.get("accession") or row.get("fasta", "")).strip()
        coll_digest = resolve_collection_digest(store, row)
        if coll_digest is None:
            n_unresolved += 1
            print(f"  [{i}/{len(rows)}] UNRESOLVED {label}", file=sys.stderr)
            continue
        n_resolved += 1

        accession = (row.get("accession") or "").strip()
        acc_match = ACCESSION_PATTERN.search(accession) if accession else None

        # 1. Canonical collection accession aliases.
        if acc_match:
            acc = acc_match.group(1)
            ns = "refseq" if acc.startswith("GCF_") else "insdc"
            coll_aliases[ns].append((acc, coll_digest))

        # Decide whether this row gets assembly-report sequence aliases.
        report_hint = (row.get("assembly_report") or "").strip()
        use_report = seq_strategy == "assembly_report" or (
            report_when_acc and (acc_match or report_hint)
        )

        if use_report and (acc_match or report_hint):
            report_acc = acc_match.group(1) if acc_match else None
            report_path = fetch_assembly_report(report_acc, report_hint, cache_dir)
            if report_path:
                n_seq_matched, n_seq_unmatched = _aliases_from_report(
                    store, coll_digest, report_path, seq_aliases, coll_aliases,
                    n_seq_matched, n_seq_unmatched,
                )
                print(f"  [{i}/{len(rows)}] report {label}")
                continue

        # Fall back to / primary: header-name passthrough.
        if seq_strategy == "header_names" or (use_report and not (acc_match or report_hint)):
            ns = header_ns or "sequence"
            if header_ns_col:
                ns = (row.get(header_ns_col) or ns).strip() or ns
            level2 = store.get_collection_level2(coll_digest)
            for name, seq_digest in zip(level2.get("names", []), level2.get("sequences", [])):
                if name:
                    seq_aliases[ns].append((name, normalize_seq_digest(seq_digest)))
            print(f"  [{i}/{len(rows)}] header_names[{ns}] {label}")
        else:
            print(f"  [{i}/{len(rows)}] coll-only {label}")

    n_seq = sum(len(v) for v in seq_aliases.values())
    n_coll = sum(len(v) for v in coll_aliases.values())
    print(
        f"\nResolved {n_resolved} collections ({n_unresolved} unresolved); "
        f"seq matched {n_seq_matched}, unmatched {n_seq_unmatched}"
    )
    print(f"Aliases to register: {n_coll} collection, {n_seq} sequence")

    if dry_run:
        print("\n[DRY RUN] not registering. Sample:")
        for ns, pairs in list(coll_aliases.items()):
            print(f"  collections/{ns}: {len(pairs)} (e.g. {pairs[:2]})")
        for ns, pairs in list(seq_aliases.items()):
            print(f"  sequences/{ns}: {len(pairs)} (e.g. {pairs[:2]})")
        return

    _load(store, seq_aliases, coll_aliases)
    print(f"\nDone. Store stats: {store.stats()}")


def _aliases_from_report(store, coll_digest, report_path, seq_aliases,
                         coll_aliases, n_matched, n_unmatched):
    rows, genbank_asm, refseq_asm = parse_assembly_report(report_path)
    if refseq_asm:
        coll_aliases["refseq"].append((refseq_asm, coll_digest))
    if genbank_asm:
        coll_aliases["insdc"].append((genbank_asm, coll_digest))

    name_to_info = name_to_info_for_collection(store, coll_digest)
    for r in rows:
        seq_len = int(r["sequence_length"]) if r["sequence_length"] else None
        seq_digest = None
        for cand in (r["sequence_name"], r["refseq_accn"], r["genbank_accn"], r["ucsc_name"]):
            if cand and cand in name_to_info:
                sd, sl = name_to_info[cand]
                if seq_len is None or sl == seq_len:
                    seq_digest = sd
                    break
        if seq_digest is None:
            n_unmatched += 1
            continue
        n_matched += 1
        if r["refseq_accn"]:
            seq_aliases["refseq"].append((r["refseq_accn"], seq_digest))
        if r["genbank_accn"]:
            seq_aliases["insdc"].append((r["genbank_accn"], seq_digest))
        if r["ucsc_name"]:
            seq_aliases["ucsc"].append((r["ucsc_name"], seq_digest))
    return n_matched, n_unmatched


def _load(store, seq_aliases, coll_aliases):
    print("\nRegistering aliases...")
    with tempfile.TemporaryDirectory() as tmp:
        for ns, pairs in seq_aliases.items():
            if not pairs:
                continue
            tsv = os.path.join(tmp, f"seq_{ns}.tsv")
            with open(tsv, "w") as f:
                for alias, digest in pairs:
                    f.write(f"{alias}\t{digest}\n")
            n = store.load_sequence_aliases(ns, tsv)
            print(f"  sequences/{ns}: {n} loaded")
        for ns, pairs in coll_aliases.items():
            if not pairs:
                continue
            tsv = os.path.join(tmp, f"coll_{ns}.tsv")
            with open(tsv, "w") as f:
                for alias, digest in pairs:
                    f.write(f"{alias}\t{digest}\n")
            n = store.load_collection_aliases(ns, tsv)
            print(f"  collections/{ns}: {n} loaded")


def read_sources(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("store", help="Store name (a stores/<store> dir) or 'all'")
    parser.add_argument("--store-path", help="Override built-store path (default $REFGETSTORE_BASE/<store>)")
    parser.add_argument("--sources", help="Override sources.csv path")
    parser.add_argument("--seq-strategy", choices=["none", "header_names", "assembly_report"],
                        help="Override the per-store sequence-alias strategy")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from refget.store import RefgetStore

    store_name = args.store
    config = load_aliasing_config(store_name)
    if args.seq_strategy:
        config["seq_strategy"] = args.seq_strategy

    sources_path = Path(args.sources) if args.sources else SCRIPT_DIR / store_name / "sources.csv"
    if not sources_path.exists():
        print(f"sources.csv not found: {sources_path}", file=sys.stderr)
        sys.exit(1)

    if args.store_path:
        store_path = Path(args.store_path)
    else:
        base = os.environ.get("REFGETSTORE_BASE")
        if not base:
            print("REFGETSTORE_BASE not set and --store-path not given.", file=sys.stderr)
            sys.exit(1)
        store_path = Path(base) / store_name
    if not store_path.exists():
        print(f"Store not found: {store_path}", file=sys.stderr)
        sys.exit(1)

    rows = read_sources(sources_path)
    print(f"Store:    {store_path}")
    print(f"Sources:  {sources_path} ({len(rows)} rows)")
    print(f"Config:   {config}")
    print(f"Dry run:  {args.dry_run}\n")

    store = RefgetStore.on_disk(str(store_path))
    if hasattr(store, "set_quiet"):
        store.set_quiet(True)

    cache_dir = store_path.parent / f".assembly_reports_{store_name}"
    build_aliases(store, rows, config, cache_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
