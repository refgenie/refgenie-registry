#!/usr/bin/env python3
"""Rebuild every alias of a refgenie-registry RefgetStore from evidence.

An alias is a human-readable key that resolves to exactly one digest within its
namespace. This script is the one way aliases enter a generic store, and each
run replaces the store's whole alias set. It builds two kinds:

1. Our own names: the collection namespaces ``name`` and ``genome_assembly``.
   The registry is the authority for these, so they need no proof, only
   uniqueness. A sources.csv value shared by several rows is skipped, and a
   genome YAML alias that is another row's name is skipped.

2. Other authorities' accessions: the namespaces ``insdc`` and ``refseq``, for
   sequences and for whole assemblies. These are never typed in or guessed.
   Each link is proven against data the authority publishes, and the kind of
   proof is recorded:

   checksum        ENA's MD5 for an INSDC sequence accession equals the MD5 of
                   a sequence in this store.
   ncbi-identical  NCBI's assembly report marks a RefSeq accession identical
                   ("=") to a GenBank accession that is already proven.
   name+length     ENA publishes no checksum (unplaced scaffolds). The
                   collection names the sequence with the accession itself, the
                   length matches NCBI's report, and no sequence in that
                   collection contradicts an ENA checksum.
   content         Assembly accession -> collection. The collection's names are
                   exactly the assembly's accessions (GenBank ones for GCA_,
                   RefSeq ones for GCF_) with NCBI's lengths, and it contradicts
                   no ENA checksum.

   A link without proof is not registered. When the proof points one alias at
   two digests, the alias is withheld. The report lists both cases.

Chromosome names (chr1, 1, ...) are never aliases: they are unique only inside
one assembly, and the store already keeps them as each collection's ``names``.
A derived build (an analysis set, a decoy-augmented or masked FASTA) gets no
accession alias, because its content is not the assembly's.

Authority data is fetched once and kept, keyed by accession, because a
versioned assembly never changes. One folder is shared by all stores:

    $REFGET_ALIAS_SOURCES (default: $REFGETSTORE_BASE/../alias-sources)
      ncbi/<GCA_|GCF_...>_assembly_report.txt   NCBI assembly report
      ena/<GCA_...>.tsv                         ENA accession, version, MD5, length

A run writes a report of what was registered and what was not to the build
reports dir ($REFGENIE_BUILD_REPORTS_DIR, else a ``_build_reports`` sibling of
the store dirs), and moves the store's previous ``aliases/`` folder to
``<reports dir>/aliases_previous/<store>``.

Stores that ship their own alias script (``stores/<store>/build_aliases.py``,
e.g. vrs) are skipped.

Usage:
    source ../infra/rivanna/env.sh
    python build_aliases.py jungle                        # rebuild jungle's aliases
    python build_aliases.py vgp --dry-run --out /tmp/vgp  # compute and report only
    python build_aliases.py jungle --out /tmp/jungle \\
        --store-url https://refgenie.s3.us-east-1.amazonaws.com/refget-store/jungle
        # read-only: compute against a published store

Requirements: refget + gtars (RefgetStore); Python stdlib otherwise.
"""

import argparse
import csv
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from genome_rows import ROW_DIGESTS, load_genomes, row_aliases, row_fasta, row_name

SCRIPT_DIR = Path(__file__).parent
ACCESSION_PATTERN = re.compile(r"(GC[AF]_\d+\.\d+)")
NCBI_FTP_BASE = "https://ftp.ncbi.nlm.nih.gov/genomes/all"
ENA_SEARCH = "https://www.ebi.ac.uk/ena/portal/api/search"
USER_AGENT = "refgenie-registry-alias-builder/2.0"

SEQ, COLL = "sequences", "collections"

# Stronger evidence replaces weaker evidence for the same alias and digest.
EVIDENCE_RANK = {"registry": 3, "checksum": 3, "content": 3,
                 "ncbi-identical": 2, "name+length": 1}

# get_collection() returns VRS-style "SQ."/"ga4gh:SQ." prefixed digests in some
# builds, but the store's alias index is keyed by the bare sha512t24u digest.
_SQ_PREFIX = re.compile(r"^(ga4gh:)?SQ\.")


def normalize_seq_digest(digest):
    return _SQ_PREFIX.sub("", digest) if digest else digest


# ---------------------------------------------------------------------------
# The alias set being built
# ---------------------------------------------------------------------------
class AliasSet:
    """kind -> namespace -> alias -> (digest, evidence), plus what was skipped."""

    def __init__(self):
        self.bindings = {SEQ: defaultdict(dict), COLL: defaultdict(dict)}
        self.withheld = defaultdict(set)    # (kind, ns) -> aliases with two digests
        self.skipped = Counter()            # (kind, ns, reason) -> count
        self.examples = defaultdict(list)   # (kind, ns, reason) -> a few examples
        self.notes = []                     # store-level problems (fetch failures, ...)

    def bind(self, kind, ns, alias, digest, evidence):
        if alias in self.withheld[(kind, ns)]:
            return
        ns_map = self.bindings[kind][ns]
        old = ns_map.get(alias)
        if old is None:
            ns_map[alias] = (digest, evidence)
            return
        old_digest, old_evidence = old
        if old_digest == digest:
            if EVIDENCE_RANK[evidence] > EVIDENCE_RANK[old_evidence]:
                ns_map[alias] = (digest, evidence)
            return
        # One alias, two digests. Keep a strictly stronger proof; otherwise
        # withhold the alias rather than guess.
        if EVIDENCE_RANK[evidence] < EVIDENCE_RANK[old_evidence]:
            self.skip(kind, ns, "weaker evidence disagrees with a proven link", alias)
            return
        if EVIDENCE_RANK[evidence] > EVIDENCE_RANK[old_evidence]:
            ns_map[alias] = (digest, evidence)
            self.skip(kind, ns, "weaker evidence disagrees with a proven link", alias)
            return
        del ns_map[alias]
        self.withheld[(kind, ns)].add(alias)
        self.skip(kind, ns, "withheld: evidence points to two digests",
                  f"{alias} ({old_digest} vs {digest})")

    def get(self, kind, ns, alias):
        hit = self.bindings[kind].get(ns, {}).get(alias)
        return hit[0] if hit else None

    def skip(self, kind, ns, reason, example=None):
        key = (kind, ns, reason)
        self.skipped[key] += 1
        if example and len(self.examples[key]) < 5:
            self.examples[key].append(example)

    def namespaces(self, kind):
        return sorted(ns for ns, m in self.bindings[kind].items() if m)


# ---------------------------------------------------------------------------
# Layer 1: authority data, fetched once and kept
# ---------------------------------------------------------------------------
def _http_get(url, timeout=60, tries=4):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == tries - 1:
                raise
        except (urllib.error.URLError, OSError):
            if attempt == tries - 1:
                raise
        time.sleep(3 * (attempt + 1))


def _write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    tmp.write_bytes(data)
    tmp.replace(path)


def accession_to_ftp_dir(accession):
    m = re.match(r"(GC[AF])_(\d+)\.\d+", accession)
    if not m:
        return None
    prefix, numeric = m.group(1), m.group(2).zfill(9)
    return f"{NCBI_FTP_BASE}/{prefix}/{numeric[0:3]}/{numeric[3:6]}/{numeric[6:9]}/"


def report_url_for_accession(accession):
    """NCBI's assembly_report.txt URL, found by reading the FTP directory listing."""
    dir_url = accession_to_ftp_dir(accession)
    if not dir_url:
        return None
    try:
        html = _http_get(dir_url, timeout=30).decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError):
        return None
    m = re.search(re.escape(accession) + r"_([^/\"]+)/", html)
    if not m:
        return None
    stem = f"{accession}_{m.group(1)}"
    return f"{dir_url}{stem}/{stem}_assembly_report.txt"


def ncbi_report(accession, sources):
    """Path to NCBI's assembly report for `accession`, or None if unavailable."""
    path = sources / "ncbi" / f"{accession}_assembly_report.txt"
    if path.is_file() and path.stat().st_size > 0:
        return path
    url = report_url_for_accession(accession)
    if not url:
        return None
    try:
        _write_atomic(path, _http_get(url))
    except (urllib.error.URLError, OSError) as e:
        print(f"  NCBI report fetch failed for {accession}: {e}", file=sys.stderr)
        return None
    time.sleep(0.3)
    return path


def ena_table(gca, sources):
    """{accession.version: (md5, length)} for every sequence ENA lists under the
    assembly, or None if ENA could not be reached.

    One portal query per assembly. ENA indexes assemblies without the version,
    so the query covers all versions; the result is kept under the versioned
    accession that needed it, which is all that accession will ever need.
    ENA has checksums for chromosome-level records only, not for WGS scaffolds.
    """
    path = sources / "ena" / f"{gca}.tsv"
    if not path.is_file():
        query = urllib.parse.urlencode({
            "result": "sequence",
            "query": f'assembly_accession="{gca.split(".")[0]}"',
            "fields": "accession,sequence_version,sequence_md5,base_count",
            "format": "tsv",
            "limit": "0",
        })
        try:
            _write_atomic(path, _http_get(f"{ENA_SEARCH}?{query}", timeout=300))
        except (urllib.error.URLError, OSError) as e:
            print(f"  ENA fetch failed for {gca}: {e}", file=sys.stderr)
            return None
    table = {}
    with open(path, newline="") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            acc, ver, md5 = r.get("accession"), r.get("sequence_version"), r.get("sequence_md5")
            if acc and ver and md5:
                table[f"{acc}.{ver}"] = (md5.lower(), int(r.get("base_count") or 0))
    return table


def parse_assembly_report(path):
    """(rows, genbank_assembly_accn, refseq_assembly_accn) from an assembly report.

    Each row is {name, role, genbank_accn, relationship, refseq_accn, length}.
    """
    genbank_asm = refseq_asm = ""
    rows = []
    norm = lambda v: "" if v.strip() in ("na", "") else v.strip()
    with open(path, errors="replace") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if line.startswith("#"):
                m = ACCESSION_PATTERN.search(line)
                if m and "GenBank assembly accession:" in line:
                    genbank_asm = m.group(1)
                elif m and "RefSeq assembly accession:" in line:
                    refseq_asm = m.group(1)
                continue
            fields = line.split("\t")
            if len(fields) < 9:
                continue
            length = norm(fields[8])
            rows.append({
                "name": fields[0].strip(),
                "role": fields[1].strip(),
                "genbank_accn": norm(fields[4]),
                "relationship": fields[5].strip(),
                "refseq_accn": norm(fields[6]),
                "length": int(length) if length.isdigit() else None,
            })
    return rows, genbank_asm, refseq_asm


# ---------------------------------------------------------------------------
# Reading the store
# ---------------------------------------------------------------------------
def read_row_digests(store_path):
    """{row fasta: collection digest} that build.py wrote into the store, or {}."""
    path = Path(store_path) / ROW_DIGESTS
    return json.loads(path.read_text()) if path.is_file() else {}


def resolve_collection_digest(store, row, row_digests=None):
    """The collection digest for a sources.csv row, or None.

    build.py's row -> digest map comes first; it still works after a row is
    renamed. Otherwise the store's aliases: the row's `name`, its
    `genome_assembly`, then its accession under `insdc`/`refseq`.
    """
    digest = (row_digests or {}).get(row_fasta(row))
    if digest:
        return digest
    m = ACCESSION_PATTERN.search(row.get("accession") or "")
    lookups = [("name", row_name(row)),
               ("genome_assembly", (row.get("genome_assembly") or "").strip())]
    if m:
        lookups.append((ns_for(m.group(1)), m.group(1)))
    for namespace, value in lookups:
        if not value:
            continue
        try:
            result = store.get_collection_by_alias(namespace, value)
        except Exception:
            result = None
        if result is not None:
            return result.digest
    return None


def _retry(fn, *args, tries=5):
    """Remote stores drop connections now and then; retry a read a few times."""
    for attempt in range(tries):
        try:
            return fn(*args)
        except OSError:
            if attempt == tries - 1:
                raise
            time.sleep(5 * (attempt + 1))


def collection_sequences(store, digest):
    """[(name, length, sha512t24u, md5)] for one collection, in order."""
    coll = _retry(store.get_collection, digest)
    out = []
    for rec in coll.sequences:
        m = rec.metadata
        out.append((m.name, int(m.length), normalize_seq_digest(m.sha512t24u),
                    (m.md5 or "").lower()))
    return out


# ---------------------------------------------------------------------------
# Layer 2: the rules
# ---------------------------------------------------------------------------
def compute_aliases(store, rows, row_digests, genome_aliases, sources):
    """Every alias the store should hold, with its evidence."""
    aset = AliasSet()

    # Each row's collection, and every collection's sequences.
    resolved = []
    for idx, row in enumerate(rows):
        digest = resolve_collection_digest(store, row, row_digests)
        if digest is None:
            aset.notes.append(f"row not found in the store: {row_name(row) or row_fasta(row)}")
            continue
        resolved.append((idx, row, digest))
    seqs = {}
    for _, _, digest in resolved:
        if digest not in seqs:
            seqs[digest] = collection_sequences(store, digest)
    by_md5 = {md5: sha for lst in seqs.values() for _, _, sha, md5 in lst if md5}
    print(f"  {len(resolved)}/{len(rows)} rows resolved, {len(seqs)} collections, "
          f"{len(by_md5)} distinct sequences")

    # Kind 1: our own names.
    unique = row_aliases(rows)
    row_names = {}
    for idx, row, digest in resolved:
        for col, val in unique[idx]:
            aset.bind(COLL, col, val, digest, "registry")
            if col == "name":
                row_names[val] = digest
    for idx, row, digest in resolved:
        for alias in genome_aliases.get(row_name(row), []):
            if row_names.get(alias, digest) != digest:
                aset.skip(COLL, "name", "genome YAML alias is another row's name", alias)
                continue
            aset.bind(COLL, "name", alias, digest, "registry")

    # Kind 2: accessions. Gather authority data for each row naming an assembly.
    claims = []
    ena_all = {}
    for idx, row, digest in resolved:
        m = ACCESSION_PATTERN.search(row.get("accession") or "")
        if not m:
            continue
        acc = m.group(1)
        report = ncbi_report(acc, sources)
        if report is None:
            aset.notes.append(f"{acc}: NCBI assembly report unavailable, no accession aliases")
            continue
        report_rows, genbank_asm, _ = parse_assembly_report(report)
        gca = acc if acc.startswith("GCA_") else genbank_asm
        ena = ena_table(gca, sources) if gca else {}
        if ena is None:
            aset.notes.append(f"{acc}: ENA unavailable, no accession aliases this run")
            continue
        ena_all.update(ena)
        claims.append((row_name(row) or acc, acc, digest, report_rows))

    # Checksum: ENA says accession X has MD5 M, and this store holds M.
    for acc_ver, (md5, _) in ena_all.items():
        sha = by_md5.get(md5)
        if sha:
            aset.bind(SEQ, "insdc", acc_ver, sha, "checksum")

    # A collection that names a sequence with an accession ENA has a checksum
    # for, but holds different content, is not the authority's file.
    contradicting = {}
    for label, acc, digest, _ in claims:
        bad = [n for n, _, _, md5 in seqs[digest] if n in ena_all and ena_all[n][0] != md5]
        if bad:
            contradicting[digest] = bad

    trusted = [c for c in claims if c[2] not in contradicting]
    for label, acc, digest, _ in claims:
        if digest in contradicting:
            bad = contradicting[digest]
            aset.skip(COLL, ns_for(acc), "collection contradicts an ENA checksum",
                      f"{label}: {len(bad)} sequences, e.g. {bad[0]}")

    # Name+length, in a trusted collection, for accessions without a checksum.
    # ENA has no RefSeq checksums at all, and none for GenBank WGS scaffolds.
    for label, acc, digest, report_rows in trusted:
        by_name = {n: (length, sha) for n, length, sha, _ in seqs[digest]}
        for r in report_rows:
            for ns, key in (("insdc", "genbank_accn"), ("refseq", "refseq_accn")):
                accn = r[key]
                if not accn or accn in ena_all or aset.get(SEQ, ns, accn):
                    continue
                hit = by_name.get(accn)
                if hit and hit[0] == r["length"]:
                    aset.bind(SEQ, ns, accn, hit[1], "name+length")

    # NCBI's "=" says a GenBank and a RefSeq accession are the same sequence, so
    # a link proven on one side carries to the other. An ENA checksum outranks
    # it: a GenBank accession whose checksum matches nothing here is not carried.
    for label, acc, digest, report_rows in trusted:
        for r in report_rows:
            gb, rs = r["genbank_accn"], r["refseq_accn"]
            if not (gb and rs and r["relationship"] == "="):
                continue
            g, s = aset.get(SEQ, "insdc", gb), aset.get(SEQ, "refseq", rs)
            if g:
                aset.bind(SEQ, "refseq", rs, g, "ncbi-identical")
            elif s and gb not in ena_all:
                aset.bind(SEQ, "insdc", gb, s, "ncbi-identical")

    # What the trusted reports name but nothing proved, counted once each.
    for ns, key in (("insdc", "genbank_accn"), ("refseq", "refseq_accn")):
        named = {r[key] for _, _, _, rr in trusted for r in rr if r[key]}
        for accn in sorted(named):
            if aset.get(SEQ, ns, accn) or accn in aset.withheld[(SEQ, ns)]:
                continue
            reason = ("ENA checksum matches no sequence in the store" if accn in ena_all
                      else "no proven sequence in the store")
            aset.skip(SEQ, ns, reason, accn)

    # Assembly accession -> collection, when the content is the assembly.
    for label, acc, digest, report_rows in trusted:
        ns = ns_for(acc)
        key = "genbank_accn" if ns == "insdc" else "refseq_accn"
        expected = {r[key]: r["length"] for r in report_rows if r[key]}
        actual = {n: length for n, length, _, _ in seqs[digest]}
        if actual == expected:
            aset.bind(COLL, ns, acc, digest, "content")
            continue
        missing = len(expected.keys() - actual.keys())
        extra = len(actual.keys() - expected.keys())
        differ = sum(1 for n in expected.keys() & actual.keys() if expected[n] != actual[n])
        aset.skip(COLL, ns, "names or lengths differ from the assembly's accessions",
                  f"{label} ({acc}): {missing} missing, {extra} extra, {differ} length differences")

    return aset


def ns_for(accession):
    return "insdc" if accession.startswith("GCA_") else "refseq"


# ---------------------------------------------------------------------------
# Layer 3: output
# ---------------------------------------------------------------------------
def write_maps(aset, out_dir):
    """One TSV per namespace (alias, digest, evidence), for review."""
    for kind in (SEQ, COLL):
        for ns in aset.namespaces(kind):
            path = out_dir / kind / f"{ns}.tsv"
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w") as f:
                for alias, (digest, evidence) in sorted(aset.bindings[kind][ns].items()):
                    f.write(f"{alias}\t{digest}\t{evidence}\n")


def report_text(aset, store_name, store_where):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# Aliases: {store_name}", "",
             f"Built {now} by stores/build_aliases.py from {store_where}.", "",
             "## Registered", "",
             "| Kind | Namespace | Aliases | Evidence |", "|---|---|---:|---|"]
    for kind in (COLL, SEQ):
        for ns in aset.namespaces(kind):
            m = aset.bindings[kind][ns]
            ev = Counter(e for _, e in m.values())
            ev_txt = ", ".join(f"{e} {n:,}" for e, n in ev.most_common())
            lines.append(f"| {kind} | {ns} | {len(m):,} | {ev_txt} |")
    lines += ["", "## Not registered", ""]
    if aset.skipped:
        lines += ["| Kind | Namespace | Reason | Count | Examples |", "|---|---|---|---:|---|"]
        for (kind, ns, reason), n in sorted(aset.skipped.items()):
            ex = "; ".join(aset.examples[(kind, ns, reason)][:3])
            lines.append(f"| {kind} | {ns} | {reason} | {n:,} | {ex} |")
    else:
        lines.append("Nothing.")
    if aset.notes:
        lines += ["", "## Problems", ""] + [f"- {n}" for n in aset.notes]
    return "\n".join(lines) + "\n"


def replace_store_aliases(store, store_path, aset, previous_dir):
    """Swap the store's whole alias set for `aset`, under one store write lock.

    The old aliases/ folder is moved out of the store first. The store rebuilds
    rgstore.json's namespace list and aliases digest from the files on disk at
    every alias commit, so namespaces that are gone simply drop out. The store
    handle still holds the old aliases in memory; force_alias lets the new
    values replace them, which is the point of this step. A clash inside one
    namespace file still errors.
    """
    store.lock_for_batch("build_aliases")
    try:
        aliases_dir = store_path / "aliases"
        if aliases_dir.exists():
            if previous_dir.exists():
                shutil.rmtree(previous_dir)
            previous_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(aliases_dir), str(previous_dir))
            print(f"  previous aliases moved to {previous_dir}")
        if hasattr(store, "set_force_alias"):
            store.set_force_alias(True)
        with tempfile.TemporaryDirectory() as tmp:
            for kind in (COLL, SEQ):
                load = store.load_collection_aliases if kind == COLL else store.load_sequence_aliases
                for ns in aset.namespaces(kind):
                    tsv = os.path.join(tmp, f"{kind}_{ns}.tsv")
                    with open(tsv, "w") as f:
                        for alias, (digest, _) in sorted(aset.bindings[kind][ns].items()):
                            f.write(f"{alias}\t{digest}\n")
                    n = load(ns, tsv)
                    print(f"  {kind}/{ns}: {n:,} loaded")
    finally:
        if hasattr(store, "set_force_alias"):
            store.set_force_alias(False)
        store.release_batch_lock()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def read_sources(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("store", help="Store name (a stores/<store> dir)")
    parser.add_argument("--store-path", help="Built store path (default $REFGETSTORE_BASE/<store>)")
    parser.add_argument("--store-url", help="Read a published store instead (implies --dry-run)")
    parser.add_argument("--sources-dir", help="Authority data cache "
                        "(default $REFGET_ALIAS_SOURCES, else $REFGETSTORE_BASE/../alias-sources)")
    parser.add_argument("--out", help="Also write the alias maps and report here")
    parser.add_argument("--dry-run", action="store_true", help="Compute and report; do not touch the store")
    args = parser.parse_args()

    store_name = args.store
    if (SCRIPT_DIR / store_name / "build_aliases.py").is_file():
        print(f"{store_name} ships its own stores/{store_name}/build_aliases.py; skipping.")
        return
    sources_path = SCRIPT_DIR / store_name / "sources.csv"
    if not sources_path.is_file():
        sys.exit(f"sources.csv not found: {sources_path}")
    out_dir = Path(args.out) if args.out else None

    from refget.store import RefgetStore

    base = os.environ.get("REFGETSTORE_BASE")
    if args.store_url:
        args.dry_run = True
        if not out_dir:
            sys.exit("--store-url needs --out")
        cache = out_dir / "store_cache"
        cache.mkdir(parents=True, exist_ok=True)
        store = RefgetStore.open_remote(str(cache), args.store_url)
        with urllib.request.urlopen(f"{args.store_url.rstrip('/')}/{ROW_DIGESTS}") as r:
            row_digests = json.load(r)
        store_path, where = None, args.store_url
    else:
        if args.store_path:
            store_path = Path(args.store_path)
        elif base:
            store_path = Path(base) / store_name
        else:
            sys.exit("REFGETSTORE_BASE not set and --store-path not given.")
        if not store_path.exists():
            sys.exit(f"Store not found: {store_path}")
        store = RefgetStore.on_disk(str(store_path))
        row_digests = read_row_digests(store_path)
        where = str(store_path)
    if hasattr(store, "set_quiet"):
        store.set_quiet(True)

    if args.sources_dir:
        sources = Path(args.sources_dir)
    elif os.environ.get("REFGET_ALIAS_SOURCES"):
        sources = Path(os.environ["REFGET_ALIAS_SOURCES"])
    elif base:
        sources = Path(base).parent / "alias-sources"
    elif out_dir:
        sources = out_dir / "alias-sources"
    else:
        sys.exit("No authority data folder: set REFGET_ALIAS_SOURCES or pass --sources-dir.")

    rows = read_sources(sources_path)
    genome_aliases = {g.name: g.aliases for g in load_genomes()
                      if g.store == store_name and g.aliases}
    print(f"Store:    {where}")
    print(f"Sources:  {sources_path} ({len(rows)} rows)")
    print(f"Evidence: {sources}")

    aset = compute_aliases(store, rows, row_digests, genome_aliases, sources)
    report = report_text(aset, store_name, where)
    print()
    print(report)

    if out_dir:
        write_maps(aset, out_dir)
        (out_dir / "report.md").write_text(report)
        print(f"Maps and report written to {out_dir}")
    if args.dry_run:
        print("[DRY RUN] store not changed.")
        return

    reports_env = os.environ.get("REFGENIE_BUILD_REPORTS_DIR")
    reports_dir = Path(reports_env) if reports_env else store_path.parent / "_build_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / f"aliases_{store_name}.md").write_text(report)

    print("Replacing aliases...")
    replace_store_aliases(store, store_path, aset, reports_dir / "aliases_previous" / store_name)
    print(f"Done. Store stats: {store.stats()}")


if __name__ == "__main__":
    main()
