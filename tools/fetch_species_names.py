#!/usr/bin/env python3
"""Fill the species list from NCBI Taxonomy: common names, other names, missing species.

The species list, `tools/taxon_ids.json` (see `tools/species_list.py`), maps
each species' NCBI scientific name to `{taxon_id, common_name, synonyms}`. It is
COMMITTED and edited by pull request; builds never call NCBI. This script is the
one-time (and occasional) bulk refresh, and its result is reviewed like any
other list edit.

What it does:

1. Collects every `organism` spelling in `stores/*/sources.csv` that the list
   cannot resolve (`species_list.resolve_organism`), and resolves each one
   through `MANUAL_TAXA` first, then NCBI esearch (a single hit only).
2. Fetches the NCBI Taxonomy record (efetch, 200 ids per POST, `NCBI_DELAY`
   between requests) of every taxon it needs: the list's, every genome YAML's
   `organism.taxon_id`, and the newly resolved ones. From each record it reads
   `ScientificName`, `GenbankCommonName`, `CommonName`, `Synonym` and
   `GenbankSynonym`, nothing else.
3. Rebuilds each entry. The key becomes NCBI's current scientific name (a
   re-keyed entry keeps its old key as a synonym). The taxon id never changes
   (a merged id is reported, because genome YAMLs use it). A set common name is
   kept; an empty one is filled from NCBI. Synonyms are, in order: NCBI common
   names not used as the common name, NCBI synonyms, the old key, every store
   spelling of the taxon, then any existing synonym NCBI did not return (a hand
   addition). A name with a four-digit year (leaked authority text) is dropped,
   and the rest goes through `species_list.clean_synonyms`.

It prints a summary, and exits 1 if any store spelling is still unresolved: add
it to `MANUAL_TAXA` and rerun.

Usage:
    python tools/fetch_species_names.py --dry-run   # print the summary; write nothing
    python tools/fetch_species_names.py             # update tools/taxon_ids.json
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import yaml

from sources_to_genomes import EUTILS, NCBI_DELAY, fetch_taxon
from species_list import (
    GENOMES_DIR,
    LIST_NAME,
    explain_synonyms,
    load_species_list,
    resolve_organism,
    save_species_list,
    store_organisms,
)

BATCH = 200

# A year in a name means authority text leaked in ("Mus musculus Linnaeus, 1758").
YEAR_RE = re.compile(r"\b(1[5-9]\d\d|20\d\d)\b")

# Store spellings that esearch cannot resolve to one taxon, or resolves to the
# wrong one. Each points at the most specific NCBI taxon the label names (the
# strain when it names a strain). The comment names the NCBI Taxonomy record
# checked (its ScientificName as efetch returns it) and the store row it serves.
MANUAL_TAXA = {
    # Sinsheimervirus phiX174 (formerly Escherichia phage phiX174); igenomes PhiX/Illumina/RTA,
    # PhiX/NCBI/1993-04-28
    "PhiX": 10847,
    # Lambdavirus lambda (formerly Escherichia phage lambda); igenomes
    # Enterobacteriophage_lambda/NCBI/1993-04-28
    "Enterobacteriophage lambda": 10710,
    # Escherichia coli str. K-12 substr. DH10B; igenomes Escherichia_coli_K_12_DH10B/*
    "Escherichia coli K 12 DH10B": 316385,
    # Escherichia coli str. K-12 substr. MG1655; igenomes Escherichia_coli_K_12_MG1655/NCBI/2001-10-15
    "Escherichia coli K 12 MG1655": 511145,
    # Bacillus subtilis subsp. subtilis str. 168; igenomes Bacillus_subtilis_168/Ensembl/EB2
    "Bacillus subtilis 168": 224308,
    # Bacillus cereus ATCC 10987; igenomes Bacillus_cereus_ATCC_10987/NCBI/2004-02-13
    "Bacillus cereus ATCC 10987": 222523,
    # Staphylococcus aureus subsp. aureus NCTC 8325; igenomes Staphylococcus_aureus_NCTC_8325/NCBI/2006-02-13
    "Staphylococcus aureus NCTC 8325": 93061,
    # Sorangium cellulosum So ce56; igenomes Sorangium_cellulosum_So_ce_56/NCBI/2007-11-27
    "Sorangium cellulosum So ce 56": 448385,
    # Cereibacter sphaeroides 2.4.1; igenomes Rhodobacter_sphaeroides_2.4.1/NCBI/2005-10-07
    "Rhodobacter sphaeroides 2.4.1": 272943,
    # Pseudomonas aeruginosa PAO1; igenomes Pseudomonas_aeruginosa_PAO1/NCBI/2000-09-13
    "Pseudomonas aeruginosa PAO1": 208964,
    # Mycobacterium tuberculosis H37Rv; igenomes Mycobacterium_tuberculosis_H37RV/*
    "Mycobacterium tuberculosis H37RV": 83332,
    # Saccharomyces cerevisiae S288C; plantref saccharomyces_cerevisiae_ENSEMBL_release_81
    "Saccharomyces cerevisiae strain S288C": 559292,
    # Oryza sativa Japonica Group; igenomes Oryza_sativa_japonica/Ensembl/IRGSP-1.0, .../MSU6
    "Oryza sativa japonica": 39947,
    # Phytophthora sojae (the CSV misspells the genus); plantref phytophtora_sojae_ENSEMBL_protist_release_28
    "Phytophtora sojae": 67593,
    # Asterochloris sp. Cgr/DA1pho; plantref asterochloris_sp_v2_0_JGI_7_45_13
    "Asterochloris sp. Cgr DA1pho": 763042,
    # Picochlorum costavermella (NCBI files strain RCC4223 under it); plantref picochlorum_rcc4223_ORCAE
    "Picochlorum RCC4223": 2009235,
    # Zoysia japonica (NCBI has no taxon for the subspecies; the assembly is cultivar
    # Nagirizaki); plantref zoysia_japonica_Zoysia_Genome_Database_v1_1
    "Zoysia japonica subsp. nagirizaki": 309978,
    # Helicosporidium sp. ATCC 50920, the strain of the one Helicosporidium nuclear genome
    # (esearch picks the generic "Helicosporidium sp.", 1907511); plantref
    # helicosporidium_sp_Illinois_University_1_0
    "Helicosporidium sp.": 1291522,
}


# --------------------------------------------------------------------------
# NCBI
# --------------------------------------------------------------------------

def _names(taxon, path: str) -> list:
    return [n.text.strip() for n in taxon.findall(path) if n.text and n.text.strip()]


def fetch_names(taxon_ids: list) -> dict:
    """{taxon_id: record} from NCBI Taxonomy efetch.

    A record is {taxon_id, scientific_name, common_names, synonyms}: NCBI's own
    TaxId, its ScientificName, GenbankCommonName then every CommonName, and
    every Synonym and GenbankSynonym. A merged taxon comes back under its new
    id, with the requested id among its AkaTaxIds; it is filed under every id it
    answers to.
    """
    records = {}
    for start in range(0, len(taxon_ids), BATCH):
        if start:
            time.sleep(NCBI_DELAY)
        batch = taxon_ids[start : start + BATCH]
        body = urllib.parse.urlencode(
            {"db": "taxonomy", "id": ",".join(map(str, batch)), "retmode": "xml"}
        ).encode()
        with urllib.request.urlopen(f"{EUTILS}/efetch.fcgi", data=body, timeout=60) as resp:
            root = ET.fromstring(resp.read())
        for taxon in root.findall("Taxon"):
            record = {
                "taxon_id": int(taxon.findtext("TaxId")),
                "scientific_name": taxon.findtext("ScientificName").strip(),
                "common_names": _names(taxon, "OtherNames/GenbankCommonName")
                + _names(taxon, "OtherNames/CommonName"),
                "synonyms": _names(taxon, "OtherNames/Synonym")
                + _names(taxon, "OtherNames/GenbankSynonym"),
            }
            for tid in [record["taxon_id"]] + [int(a.text) for a in taxon.findall("AkaTaxIds/TaxId")]:
                records[tid] = record
        print(f"  efetch: {min(start + BATCH, len(taxon_ids))}/{len(taxon_ids)} ids",
              file=sys.stderr)
    return records


def yaml_taxa() -> dict:
    """{taxon_id: scientific_name} across every genome YAML."""
    taxa = {}
    for path in sorted(GENOMES_DIR.rglob("*.yaml")):
        organism = (yaml.safe_load(path.read_text()) or {}).get("organism") or {}
        if organism.get("taxon_id"):
            taxa.setdefault(organism["taxon_id"], organism.get("scientific_name"))
    return taxa


def resolve_spellings(spellings: list) -> tuple:
    """({spelling: (taxon_id, how)}, [unresolved]) through MANUAL_TAXA, then esearch."""
    resolved, unresolved = {}, []
    for i, spelling in enumerate(spellings, 1):
        if spelling in MANUAL_TAXA:
            resolved[spelling] = (MANUAL_TAXA[spelling], "manual")
            continue
        try:
            found = fetch_taxon(spelling)
        except Exception as exc:  # noqa: BLE001
            print(f"  NCBI lookup failed for {spelling!r}: {exc}", file=sys.stderr)
            found = None
        if found is None:
            unresolved.append(spelling)
        else:
            resolved[spelling] = (found["taxon_id"], "esearch")
        if i % 25 == 0 or i == len(spellings):
            print(f"  esearch: {i}/{len(spellings)} spellings", file=sys.stderr)
        time.sleep(NCBI_DELAY)
    return resolved, unresolved


# --------------------------------------------------------------------------
# building entries
# --------------------------------------------------------------------------

class Summary:
    def __init__(self):
        self.added = []          # (key, taxon_id)
        self.rekeyed = []        # (old, new)
        self.filled = []         # (key, common_name)
        self.syn_added = 0
        self.syn_removed = 0
        self.syn_entries = 0
        self.plural = []         # (key, name, rule)
        self.year = []           # (key, name)
        self.merged = []         # (key, list id, NCBI id)
        self.conflicts = []      # message
        self.unanswered = []     # (key, taxon_id)
        self.spellings = []      # (spelling, taxon_id, how, NCBI name)
        self.unresolved = []     # spelling


def build_synonyms(key, common_name, record, old_key, spellings, existing, summary) -> list:
    """The synonyms of one entry, in the documented order, with the rules applied."""
    names = [n for n in record["common_names"] if n != common_name]
    names += record["synonyms"]
    if old_key:
        names.append(old_key)
    names += spellings
    names += existing
    kept = []
    for name in names:
        if YEAR_RE.search(name):
            summary.year.append((key, name))
        else:
            kept.append(name)
    final, dropped = explain_synonyms(key, common_name, kept)
    summary.plural += [(key, name, rule) for name, rule in dropped if "plural" in rule]
    return final


def make_entry(key, taxon_id, common_name, synonyms) -> dict:
    entry = {"taxon_id": taxon_id}
    if common_name:
        entry["common_name"] = common_name
    if synonyms:
        entry["synonyms"] = synonyms
    return entry


def rebuild(species: dict, records: dict, spellings_by_taxon: dict, new_taxa: list,
            summary: Summary) -> dict:
    """The new list: every existing entry rebuilt, plus an entry for each new taxon."""
    claimed = {}  # casefolded key -> taxon id
    out = {}

    def claim(key, taxon_id):
        holder = claimed.get(key.casefold())
        if holder is not None and holder != taxon_id:
            return False
        claimed[key.casefold()] = taxon_id
        return True

    staying, moving = [], []
    for key, entry in species.items():
        record = records.get(entry["taxon_id"])
        if record is None:
            summary.unanswered.append((key, entry["taxon_id"]))
            staying.append((key, key, entry, None))
        elif record["scientific_name"] == key:
            staying.append((key, key, entry, record))
        else:
            moving.append((key, record["scientific_name"], entry, record))

    for key, _new, entry, _record in staying:
        claim(key, entry["taxon_id"])
    placed = list(staying)
    for key, new, entry, record in sorted(moving, key=lambda m: m[0]):
        if claim(new, entry["taxon_id"]):
            placed.append((key, new, entry, record))
            summary.rekeyed.append((key, new))
        else:
            summary.conflicts.append(
                f"{key!r} (taxon {entry['taxon_id']}): NCBI calls it {new!r}, which the "
                f"list already uses for taxon {claimed[new.casefold()]}; left under its old key")
            if not claim(key, entry["taxon_id"]):
                raise SystemExit(f"cannot place {key!r}: both {new!r} and its old key are taken")
            placed.append((key, key, entry, record))

    for old, key, entry, record in placed:
        tid = entry["taxon_id"]
        if record is None:
            out[key] = dict(entry)
            continue
        if record["taxon_id"] != tid:
            summary.merged.append((key, tid, record["taxon_id"]))
        common = entry.get("common_name")
        if not common and record["common_names"]:
            common = record["common_names"][0]
            summary.filled.append((key, common))
        before = entry.get("synonyms") or []
        synonyms = build_synonyms(key, common, record, old if old != key else None,
                                  spellings_by_taxon.get(tid, []), before, summary)
        added = {s.casefold() for s in synonyms} - {s.casefold() for s in before}
        removed = {s.casefold() for s in before} - {s.casefold() for s in synonyms}
        summary.syn_added += len(added)
        summary.syn_removed += len(removed)
        summary.syn_entries += bool(added or removed)
        out[key] = make_entry(key, tid, common, synonyms)

    for tid in sorted(new_taxa):
        record = records.get(tid)
        if record is None:
            summary.unanswered.append(("(new)", tid))
            continue
        key = record["scientific_name"]
        if not claim(key, tid):
            summary.conflicts.append(
                f"new taxon {tid}: NCBI calls it {key!r}, which the list already uses for "
                f"taxon {claimed[key.casefold()]}; not added")
            continue
        if record["taxon_id"] != tid:
            summary.merged.append((key, tid, record["taxon_id"]))
        common = record["common_names"][0] if record["common_names"] else None
        synonyms = build_synonyms(key, common, record, None, spellings_by_taxon.get(tid, []),
                                  [], summary)
        out[key] = make_entry(key, tid, common, synonyms)
        summary.added.append((key, tid))
        summary.syn_added += len(synonyms)
    return out


def print_summary(before: dict, after: dict, s: Summary, dry_run: bool) -> None:
    print(f"fetch_species_names: {len(before)} -> {len(after)} species"
          f"{' (dry run: nothing written)' if dry_run else ''}")

    def section(title, items, fmt):
        print(f"  {title} ({len(items)})" + (":" if items else ""))
        for item in items:
            print(f"    {fmt(item)}")

    section("added", s.added, lambda a: f"{a[0]} ({a[1]})")
    section("re-keyed", s.rekeyed, lambda r: f"{r[0]} -> {r[1]}")
    section("common names filled", s.filled, lambda f: f"{f[0]}: {f[1]}")
    print(f"  synonyms: {s.syn_added} added, {s.syn_removed} removed, "
          f"across {s.syn_entries} existing entries (plus the new ones)")
    section("dropped by the plural rule", s.plural, lambda p: f"{p[0]}: {p[1]!r} {p[2]}")
    section("dropped by the year rule", s.year, lambda y: f"{y[0]}: {y[1]!r}")
    section("merged ids (kept; genome YAMLs use them)", s.merged,
            lambda m: f"{m[0]}: list {m[1]} -> NCBI {m[2]}")
    section("conflicts", s.conflicts, str)
    section("NCBI returned no record", s.unanswered, lambda u: f"{u[0]} ({u[1]})")
    section("store spellings resolved", s.spellings,
            lambda r: f"{r[0]!r} -> {r[1]} {r[3]} [{r[2]}]")
    section("store spellings still unresolved (add to MANUAL_TAXA)", s.unresolved, repr)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the summary; write nothing")
    args = parser.parse_args(argv)

    species = load_species_list()
    summary = Summary()

    spellings = store_organisms()
    on_list = {s: resolve_organism(s, species) for s in spellings}
    missing = sorted(s for s, hit in on_list.items() if hit is None)
    print(f"  {len(missing)} store spelling(s) not on the list; resolving", file=sys.stderr)
    resolved, summary.unresolved = resolve_spellings(missing)

    spelling_taxa = {s: hit[1]["taxon_id"] for s, hit in on_list.items() if hit is not None}
    spelling_taxa.update({s: tid for s, (tid, _how) in resolved.items()})
    spellings_by_taxon = {}
    for spelling in sorted(spelling_taxa):
        spellings_by_taxon.setdefault(spelling_taxa[spelling], []).append(spelling)

    listed = {entry["taxon_id"] for entry in species.values()}
    from_yaml = set(yaml_taxa())
    new_taxa = (from_yaml | {tid for tid, _how in resolved.values()}) - listed

    time.sleep(NCBI_DELAY)
    records = fetch_names(sorted(listed | from_yaml | new_taxa))

    for spelling, (tid, how) in sorted(resolved.items()):
        name = records[tid]["scientific_name"] if tid in records else "(no NCBI record)"
        summary.spellings.append((spelling, tid, how, name))

    after = rebuild(species, records, spellings_by_taxon, new_taxa, summary)
    print_summary(species, after, summary, args.dry_run)
    if not args.dry_run:
        save_species_list(after)
        print(f"  wrote {LIST_NAME}")
    return 1 if summary.unresolved else 0


if __name__ == "__main__":
    raise SystemExit(main())
