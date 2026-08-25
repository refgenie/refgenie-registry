#!/usr/bin/env python3
"""Generate genome YAMLs from a store's sources.csv.

A store's `sources.csv` lists the sequence collections it holds. A genome YAML is
the registry's record OF one of those collections, its name, organism, taxonomy,
assembly accession, and its `build:` block. Most of a store's collections have no
YAML, so they carry no organism or taxonomy anywhere in the registry, and a client
browsing the store sees a digest and nothing else.

This script closes that gap in bulk: it walks a store's sources.csv, skips every
row a genome YAML already covers, and writes one YAML per remaining row at
`build: {store: <slug>, tier: store_only}`, registered and described, no assets
built. It is also how any future hub import lands.

It is IDEMPOTENT: a row already covered (by assembly accession, or by the file
that would be written) is skipped, so a second run writes nothing.

Taxonomy
--------
`organism.taxon_id` is schema-required and sources.csv has only the organism NAME,
so the ids come from NCBI Taxonomy (esearch + esummary) and are cached in
`tools/taxon_ids.json`. That cache is COMMITTED, so a re-run is reproducible and
offline: `--offline` refuses to reach the network and reports any name the cache
does not cover. A name NCBI cannot resolve is reported and its row is SKIPPED --
a genome with no taxon id is a real gap that needs a human, not a blank field.

Usage:
    python tools/sources_to_genomes.py vgp --out-dir genomes/vertebrates
    python tools/sources_to_genomes.py vgp --out-dir genomes/vertebrates --dry-run
    python tools/sources_to_genomes.py vgp --out-dir genomes/vertebrates --offline
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
GENOMES_DIR = REPO / "genomes"
STORES_DIR = REPO / "stores"
TAXON_CACHE = Path(__file__).resolve().parent / "taxon_ids.json"

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
# NCBI asks for <= 3 requests/second without an API key.
NCBI_DELAY = 0.34

NAME_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")
ACCESSION_RE = re.compile(r"^(GC[AF])_(\d{3})(\d{3})(\d{3})\.(\d+)$")

# UCSC mirrors every GenBank/RefSeq assembly as an assembly hub at a path derived
# from the accession. This is the same URL shape the hand-written vertebrate
# records already use.
UCSC_HUB = "https://hgdownload.soe.ucsc.edu/hubs/{prefix}/{a}/{b}/{c}/{acc}/{acc}.fa.gz"


class GenerationError(Exception):
    pass


# --------------------------------------------------------------------------
# naming
# --------------------------------------------------------------------------

def slugify(name: str) -> str:
    """Turn a sources.csv `name` into a schema-legal genome name.

    Genome names must match `^[a-zA-Z0-9_.-]+$` and equal the YAML filename stem,
    but store names are free text: 'mTriInu1 haplotype 2', 'mEubGla1.1.hap2.+ XY'.
    Spaces become underscores (the convention the hand-written records already
    use: `mTriInu1 haplotype 2` -> `mTriInu1_haplotype_2`) and anything still
    illegal is dropped.
    """
    slug = re.sub(r"\s+", "_", name.strip())
    slug = re.sub(r"[^A-Za-z0-9_.-]", "", slug)
    slug = re.sub(r"_+", "_", slug).strip("._-")
    return slug


def hub_url(accession: str) -> str | None:
    """The UCSC assembly-hub FASTA URL for a GCA/GCF accession, or None."""
    match = ACCESSION_RE.match(accession or "")
    if not match:
        return None
    prefix, a, b, c, _version = match.groups()
    return UCSC_HUB.format(prefix=prefix, a=a, b=b, c=c, acc=accession)


# --------------------------------------------------------------------------
# taxonomy
# --------------------------------------------------------------------------

def load_taxon_cache(path: Path = TAXON_CACHE) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_taxon_cache(cache: dict, path: Path = TAXON_CACHE) -> None:
    path.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n")


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode())


def fetch_taxon(name: str) -> dict | None:
    """{taxon_id, common_name} for an organism name, from NCBI Taxonomy.

    Returns None when NCBI resolves the name to zero or several taxa, an
    ambiguous name must not be guessed at.
    """
    query = urllib.parse.urlencode({"db": "taxonomy", "term": name, "retmode": "json"})
    result = _get_json(f"{EUTILS}/esearch.fcgi?{query}").get("esearchresult") or {}
    ids = result.get("idlist") or []
    if len(ids) != 1:
        return None
    taxon_id = int(ids[0])

    time.sleep(NCBI_DELAY)
    query = urllib.parse.urlencode({"db": "taxonomy", "id": taxon_id, "retmode": "json"})
    summary = (_get_json(f"{EUTILS}/esummary.fcgi?{query}").get("result") or {}).get(
        str(taxon_id)
    ) or {}
    common = summary.get("genbankcommonname") or summary.get("commonname") or None
    entry = {"taxon_id": taxon_id}
    if common:
        entry["common_name"] = common
    return entry


def resolve_taxa(names: list, cache: dict, offline: bool) -> list:
    """Fill `cache` for every name. Returns the names that stayed unresolved."""
    missing = [n for n in names if n not in cache]
    if not missing:
        return []
    if offline:
        return missing

    unresolved = []
    for i, name in enumerate(missing, 1):
        try:
            entry = fetch_taxon(name)
        except Exception as exc:  # noqa: BLE001
            print(f"  NCBI lookup failed for {name!r}: {exc}", file=sys.stderr)
            entry = None
        if entry is None:
            unresolved.append(name)
        else:
            cache[name] = entry
        if i % 25 == 0 or i == len(missing):
            print(f"  taxonomy: {i}/{len(missing)} looked up", file=sys.stderr)
            save_taxon_cache(cache)
        time.sleep(NCBI_DELAY)
    save_taxon_cache(cache)
    return unresolved


# --------------------------------------------------------------------------
# existing corpus
# --------------------------------------------------------------------------

def existing_records(genomes_dir: Path = GENOMES_DIR) -> tuple:
    """({name: path}, {accession: name}) across every committed genome YAML."""
    names = {}
    accessions = {}
    for path in sorted(genomes_dir.rglob("*.yaml")):
        data = yaml.safe_load(path.read_text())
        if not isinstance(data, dict) or not data.get("name"):
            continue
        names[data["name"]] = path
        acc = (data.get("assembly") or {}).get("accession")
        if acc:
            accessions[acc] = data["name"]
    return names, accessions


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def render_genome(row: dict, slug: str, store: str, taxon: dict) -> str:
    """One genome YAML, in the same field order the hand-written records use."""
    organism = row["organism"].strip()
    accession = (row.get("accession") or "").strip()
    source = (row.get("source") or "").strip()

    lines = [f"name: {slug}", "description: |"]
    detail = f"{organism} assembly {row['name'].strip()}"
    if accession:
        detail += f" ({accession})"
    if source:
        detail += f", from {source}"
    lines.append(f"  {detail}.")
    lines.append("organism:")
    lines.append(f"  scientific_name: {organism}")
    if taxon.get("common_name"):
        lines.append(f"  common_name: {taxon['common_name']}")
    lines.append(f"  taxon_id: {taxon['taxon_id']}")

    if accession:
        lines.append("assembly:")
        lines.append(f"  source: {'RefSeq' if accession.startswith('GCF') else 'GenBank'}")
        lines.append(f"  accession: {accession}")

    lines.append("fasta:")
    url = hub_url(accession)
    if url:
        lines.append("  sources:")
        lines.append("  - provider: UCSC")
        lines.append(f"    url: {url}")
    lines.append("  checksum:")
    lines.append("    sha256: compute_on_registration")

    lines.append("seqcol:")
    lines.append("  compute: true")

    lines.append("build:")
    lines.append(f"  store: {store}")
    lines.append("  tier: store_only")

    lines.append("metadata:")
    lines.append("  added_by: sources_to_genomes")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

def _rel(path: Path) -> str:
    """Repo-relative display path (out_dir may be given relative or absolute)."""
    path = Path(path).resolve()
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def generate(store: str, out_dir: Path, dry_run: bool, offline: bool) -> int:
    sources_csv = STORES_DIR / store / "sources.csv"
    if not sources_csv.is_file():
        raise GenerationError(f"no such store sources file: {sources_csv}")
    rows = list(csv.DictReader(sources_csv.open(newline="")))

    known_names, known_accessions = existing_records()

    todo = []
    covered_by_accession = []
    covered_by_name = []
    unslugged = []
    for row in rows:
        raw = (row.get("name") or "").strip()
        accession = (row.get("accession") or "").strip()
        if accession and accession in known_accessions:
            covered_by_accession.append((raw, known_accessions[accession]))
            continue
        slug = slugify(raw)
        if not slug or not NAME_RE.match(slug):
            unslugged.append(raw)
            continue
        if slug in known_names:
            covered_by_name.append(raw)
            continue
        todo.append((row, slug))

    collisions = {}
    for row, slug in todo:
        collisions.setdefault(slug, []).append(row["name"])
    duplicated = {s: n for s, n in collisions.items() if len(n) > 1}

    cache = load_taxon_cache()
    unresolved = resolve_taxa(sorted({r["organism"].strip() for r, _ in todo}), cache, offline)

    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    skipped_taxon = []
    for row, slug in todo:
        if slug in duplicated:
            continue
        organism = row["organism"].strip()
        taxon = cache.get(organism)
        if not taxon:
            skipped_taxon.append((slug, organism))
            continue
        text = render_genome(row, slug, store, taxon)
        path = out_dir / f"{slug}.yaml"
        if dry_run:
            print(f"--- {_rel(path)}")
            print(text, end="")
        else:
            path.write_text(text)
        written += 1

    print(f"sources_to_genomes: {len(rows)} rows in stores/{store}/sources.csv")
    print(f"  already covered: {len(covered_by_accession)} by accession, "
          f"{len(covered_by_name)} by name")
    print(f"  {'would write' if dry_run else 'wrote'}: {written} genome YAML(s) "
          f"to {_rel(out_dir)}")

    problems = 0
    for raw, existing in covered_by_accession:
        if slugify(raw) != existing:
            print(f"  NOTE accession match under a different name: sources.csv "
                  f"{raw!r} vs genome {existing!r}")
    if unslugged:
        problems += len(unslugged)
        print(f"  SKIPPED {len(unslugged)} row(s) whose name yields no legal genome "
              f"name: {unslugged}", file=sys.stderr)
    if duplicated:
        problems += len(duplicated)
        print(f"  SKIPPED {len(duplicated)} slug collision(s): {duplicated}",
              file=sys.stderr)
    if unresolved:
        print(f"  {len(unresolved)} organism name(s) unresolved by NCBI Taxonomy"
              f"{' (offline)' if offline else ''}: {unresolved}", file=sys.stderr)
    if skipped_taxon:
        problems += len(skipped_taxon)
        print(f"  SKIPPED {len(skipped_taxon)} row(s) with no taxon id, a real gap, "
              f"not a blank field:", file=sys.stderr)
        for slug, organism in skipped_taxon:
            print(f"    {slug}: {organism}", file=sys.stderr)
    return 1 if problems else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("store", help="store slug (a directory under stores/)")
    parser.add_argument("--out-dir", required=True, type=Path,
                        help="directory to write genome YAMLs into, e.g. genomes/vertebrates")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be written; write nothing")
    parser.add_argument("--offline", action="store_true",
                        help="never reach NCBI; use tools/taxon_ids.json only")
    args = parser.parse_args(argv)
    try:
        return generate(args.store, args.out_dir, args.dry_run, args.offline)
    except GenerationError as exc:
        print(f"sources_to_genomes: ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
