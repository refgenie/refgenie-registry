#!/usr/bin/env python3
"""The species list, `tools/taxon_ids.json`, and the `species.json` it ships.

The species list is the one place species names are edited. It maps each
species' NCBI scientific name to its taxon id, its common name, and its other
names:

    "Rattus norvegicus": {"taxon_id": 10116, "common_name": "Norway rat",
                          "synonyms": ["brown rat", "rat"]}

It is edited by pull request and feeds two things:

- the FHR fields of store rows that have no genome YAML (`stores/build_fhr.py`
  looks each `sources.csv` organism up here, by key and then by synonym);
- each store's `species.json`, written at the store root next to `rgstore.json`
  with only the species that store holds. FHR has no field for other species
  names, so they ship in this separate file, which gtars ignores.

`species.json` contract (shared with refgenie, which reads it during store sync):

    {"species": [
      {"taxon_id": 10116, "scientific_name": "Rattus norvegicus",
       "common_name": "Norway rat", "synonyms": ["brown rat", "rat"]}
    ]}

`taxon_id` is an int, required, unique in the file. `scientific_name` is
required. `common_name` is optional. `synonyms` is an optional list of other
names. The file lists only that store's species.

Standard library only: `stores/build_fhr.py` imports this module inside the
store-build environment.

Usage:
    python tools/species_list.py check                   # the list, and genome YAML taxa
    python tools/species_list.py store-file <store_dir>  # write species.json from the store's sidecars
    python tools/species_list.py check-file species.json # check a species.json (e.g. from S3)
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

SPECIES_LIST = Path(__file__).resolve().parent / "taxon_ids.json"
REPO = SPECIES_LIST.parent.parent
STORES_DIR = REPO / "stores"
GENOMES_DIR = REPO / "genomes"

# How the list is named in messages people read.
LIST_NAME = "tools/taxon_ids.json"
SPECIES_FILE = "species.json"

ENTRY_KEYS = ("taxon_id", "common_name", "synonyms")
DOC_KEYS = ("taxon_id", "scientific_name", "common_name", "synonyms")
TAXON_URI_RE = re.compile(r"^https://identifiers\.org/taxonomy:(\d+)$")


# --------------------------------------------------------------------------
# reading and writing the list
# --------------------------------------------------------------------------

def load_species_list(path: Path = SPECIES_LIST) -> dict:
    """The species list: {scientific name: {taxon_id, common_name?, synonyms?}}."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_species_list(species: dict, path: Path = SPECIES_LIST) -> None:
    """Write the list sorted by key, so it reads and diffs well in a PR."""
    text = json.dumps(species, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    Path(path).write_text(text, encoding="utf-8")


def by_taxon_id(species: dict) -> dict:
    """{taxon_id: (scientific name, entry)}. Raises ValueError on a repeated id."""
    index: dict = {}
    repeats: dict = {}
    for name, entry in species.items():
        tid = entry.get("taxon_id")
        if tid in index:
            repeats.setdefault(tid, [index[tid][0]]).append(name)
        else:
            index[tid] = (name, entry)
    if repeats:
        listed = "; ".join(f"{tid} ({', '.join(names)})" for tid, names in sorted(repeats.items()))
        raise ValueError(f"taxon id(s) listed more than once in {LIST_NAME}: {listed}")
    return index


def _synonym_owners(name: str, species: dict) -> list:
    """The keys of every entry that carries ``name`` as a synonym, ignoring case."""
    folded = name.casefold()
    return [
        key for key, entry in species.items()
        if any(s.casefold() == folded for s in entry.get("synonyms") or [])
    ]


def resolve_organism(name: str, species: dict):
    """(scientific name, entry) for an organism spelling, or None.

    Tries the exact key, then the key ignoring case, then a synonym ignoring
    case, but only if exactly one entry carries that synonym. An empty name, an
    unknown name, or a synonym two entries share gives None.
    """
    name = (name or "").strip()
    if not name:
        return None
    if name in species:
        return name, species[name]
    folded = name.casefold()
    for key, entry in species.items():
        if key.casefold() == folded:
            return key, entry
    owners = _synonym_owners(name, species)
    if len(owners) == 1:
        return owners[0], species[owners[0]]
    return None


# --------------------------------------------------------------------------
# the name rules
# --------------------------------------------------------------------------

def is_plural_of(name: str, others) -> bool:
    """True when ``name`` is a plain plural (+s or +es) of one of ``others``, ignoring case."""
    folded = name.casefold()
    return any(folded in (o.casefold() + "s", o.casefold() + "es") for o in others if o)


def explain_synonyms(scientific_name: str, common_name: str | None, synonyms) -> tuple:
    """Apply the name rules. Returns (kept, dropped).

    ``dropped`` is a list of (name, the rule it breaks). This is the one place
    the rules live; `clean_synonyms` and the list check both use it. In order:
    strip each name; drop empty names; drop a name equal (ignoring case) to the
    scientific or common name; drop repeats ignoring case (the first one wins);
    then the noise rule: drop a plain plural (+s or +es) of the scientific name,
    the common name, or another kept synonym. Input order is kept otherwise.
    """
    sci = (scientific_name or "").strip()
    common = (common_name or "").strip()
    reserved = {sci.casefold(): "the scientific name", common.casefold(): "the common name"}
    reserved.pop("", None)

    kept: list = []
    dropped: list = []
    seen: set = set()
    for raw in synonyms or []:
        name = raw.strip()
        if not name:
            dropped.append((raw, "is empty"))
            continue
        folded = name.casefold()
        if folded in reserved:
            dropped.append((name, f"equals {reserved[folded]} (ignoring case)"))
            continue
        if folded in seen:
            dropped.append((name, "repeats an earlier synonym (ignoring case)"))
            continue
        seen.add(folded)
        kept.append(name)

    bases = [n for n in (sci, common) if n]
    final = []
    for i, name in enumerate(kept):
        others = bases + kept[:i] + kept[i + 1:]
        plural_of = [o for o in others if is_plural_of(name, [o])]
        if plural_of:
            dropped.append((name, f"is a plain plural of {plural_of[0]!r}"))
        else:
            final.append(name)
    return final, dropped


def clean_synonyms(scientific_name: str, common_name: str | None, synonyms) -> list:
    """``synonyms`` with the name rules applied (see `explain_synonyms`)."""
    return explain_synonyms(scientific_name, common_name, synonyms)[0]


# --------------------------------------------------------------------------
# checking the list
# --------------------------------------------------------------------------

def _is_taxon_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _is_name(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def store_organisms(stores_dir: Path = STORES_DIR) -> dict:
    """{organism spelling: [store, ...]} across every ``stores/*/sources.csv``."""
    spellings: dict = {}
    for path in sorted(Path(stores_dir).glob("*/sources.csv")):
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                organism = (row.get("organism") or "").strip()
                if organism:
                    stores = spellings.setdefault(organism, [])
                    if path.parent.name not in stores:
                        stores.append(path.parent.name)
    return spellings


def _check_entry(key, entry) -> list:
    where = f"{LIST_NAME} {key!r}"
    if not isinstance(key, str) or not key.strip():
        return [f"{LIST_NAME}: an entry key is empty or not a string: {key!r}"]
    problems = []
    if key != key.strip():
        problems.append(f"{where}: the key has surrounding spaces")
    if not isinstance(entry, dict):
        return problems + [f"{where}: the entry must be an object, got {type(entry).__name__}"]
    extra = sorted(set(entry) - set(ENTRY_KEYS))
    if extra:
        problems.append(f"{where}: unknown key(s) {extra}; an entry has only "
                        f"{', '.join(ENTRY_KEYS)}")
    if not _is_taxon_id(entry.get("taxon_id")):
        problems.append(f"{where}: taxon_id must be an integer of at least 1, "
                        f"got {entry.get('taxon_id')!r}")
    common = entry.get("common_name")
    if "common_name" in entry and not _is_name(common):
        problems.append(f"{where}: common_name must be a non-empty string, got {common!r}")
        common = None
    if "synonyms" in entry:
        synonyms = entry["synonyms"]
        if not isinstance(synonyms, list) or not synonyms:
            problems.append(f"{where}: synonyms must be a non-empty list (leave it out "
                            f"when there are none), got {synonyms!r}")
        elif not all(isinstance(s, str) for s in synonyms):
            problems.append(f"{where}: every synonym must be a string, got {synonyms!r}")
        else:
            _kept, dropped = explain_synonyms(key, common, synonyms)
            for name, rule in dropped:
                problems.append(f"{where}: synonym {name!r} {rule}")
            for name in synonyms:
                if name.strip() and name != name.strip():
                    problems.append(f"{where}: synonym {name!r} has surrounding spaces")
    return problems


def check_species_list(species: dict, stores_dir: Path = STORES_DIR) -> list:
    """Every problem with the list, as messages. An empty list means it is valid."""
    if not isinstance(species, dict):
        return [f"{LIST_NAME}: the list must be a JSON object, got {type(species).__name__}"]
    problems = []
    for key, entry in species.items():
        problems += _check_entry(key, entry)

    keys: dict = {}
    for key in species:
        if isinstance(key, str):
            keys.setdefault(key.casefold(), []).append(key)
    for same in keys.values():
        if len(same) > 1:
            problems.append(f"{LIST_NAME}: keys {same} are the same name ignoring case")

    ids: dict = {}
    for key, entry in species.items():
        if isinstance(entry, dict) and _is_taxon_id(entry.get("taxon_id")):
            ids.setdefault(entry["taxon_id"], []).append(key)
    for tid, names in sorted(ids.items()):
        if len(names) > 1:
            problems.append(f"{LIST_NAME}: taxon_id {tid} is listed more than once: {names}")

    valid = {k: e for k, e in species.items()
             if isinstance(e, dict) and isinstance(e.get("synonyms", []), list)
             and all(isinstance(s, str) for s in e.get("synonyms", []))}
    for spelling, stores in sorted(store_organisms(stores_dir).items()):
        if resolve_organism(spelling, valid) is not None:
            continue
        owners = _synonym_owners(spelling, valid)
        csvs = ", ".join(f"stores/{s}/sources.csv" for s in stores)
        if len(owners) > 1:
            problems.append(f"{csvs}: organism {spelling!r} is ambiguous: it is a synonym "
                            f"of both {owners[0]!r} and {owners[1]!r} in {LIST_NAME}")
        else:
            problems.append(f"{csvs}: organism {spelling!r} is not on the species list "
                            f"{LIST_NAME} (as a key or a synonym)")
    return problems


# --------------------------------------------------------------------------
# species.json
# --------------------------------------------------------------------------

def taxon_id_from_fhr(record: dict) -> int | None:
    """The NCBI taxon id an FHR record's ``taxon.uri`` names, or None."""
    taxon = record.get("taxon") if isinstance(record, dict) else None
    uri = taxon.get("uri") if isinstance(taxon, dict) else None
    match = TAXON_URI_RE.match(uri) if isinstance(uri, str) else None
    return int(match.group(1)) if match else None


def species_document(taxon_ids, species: dict) -> dict:
    """The ``species.json`` document for these taxa, sorted by taxon id.

    Raises ValueError naming any taxon id that is not on the list.
    """
    index = by_taxon_id(species)
    wanted = sorted(set(taxon_ids))
    missing = [tid for tid in wanted if tid not in index]
    if missing:
        raise ValueError(f"taxon id(s) not on the species list {LIST_NAME}: {missing}")
    items = []
    for tid in wanted:
        name, entry = index[tid]
        item = {"taxon_id": tid, "scientific_name": name}
        if entry.get("common_name"):
            item["common_name"] = entry["common_name"]
        synonyms = clean_synonyms(name, entry.get("common_name"), entry.get("synonyms"))
        if synonyms:
            item["synonyms"] = synonyms
        items.append(item)
    return {"species": items}


def check_species_document(doc) -> list:
    """Check a ``species.json`` document against the contract. Returns problems.

    Strict on purpose: it checks our own output, and grows when the contract does.
    """
    if not isinstance(doc, dict):
        return [f"the top level must be an object, got {type(doc).__name__}"]
    problems = []
    if "species" not in doc:
        problems.append("the top-level key 'species' is missing")
    extra = sorted(set(doc) - {"species"})
    if extra:
        problems.append(f"unknown top-level key(s) {extra}; the only key is 'species'")
    items = doc.get("species")
    if "species" in doc and not isinstance(items, list):
        problems.append(f"'species' must be a list, got {type(items).__name__}")
    if not isinstance(items, list):
        return problems

    seen_ids: set = set()
    for i, item in enumerate(items):
        where = f"species[{i}]"
        if not isinstance(item, dict):
            problems.append(f"{where} must be an object, got {type(item).__name__}")
            continue
        extra = sorted(set(item) - set(DOC_KEYS))
        if extra:
            problems.append(f"{where}: unknown key(s) {extra}; an item has only "
                            f"{', '.join(DOC_KEYS)}")
        tid = item.get("taxon_id")
        if not _is_taxon_id(tid):
            problems.append(f"{where}: taxon_id must be an integer of at least 1, got {tid!r}")
        elif tid in seen_ids:
            problems.append(f"{where}: taxon_id {tid} appears more than once")
        else:
            seen_ids.add(tid)
        sci = item.get("scientific_name")
        if not _is_name(sci):
            problems.append(f"{where}: scientific_name must be a non-empty string, got {sci!r}")
            sci = None
        common = item.get("common_name")
        if "common_name" in item and not _is_name(common):
            problems.append(f"{where}: common_name must be a non-empty string, got {common!r}")
            common = None
        if "synonyms" in item:
            synonyms = item["synonyms"]
            if not isinstance(synonyms, list) or not synonyms:
                problems.append(f"{where}: synonyms must be a non-empty list, got {synonyms!r}")
                continue
            reserved = {n.casefold(): label for n, label in
                        ((sci, "scientific_name"), (common, "common_name")) if n}
            seen: set = set()
            for name in synonyms:
                if not _is_name(name):
                    problems.append(f"{where}: every synonym must be a non-empty string, "
                                    f"got {name!r}")
                    continue
                folded = name.casefold()
                if folded in reserved:
                    problems.append(f"{where}: synonym {name!r} equals {reserved[folded]} "
                                    f"(ignoring case)")
                elif folded in seen:
                    problems.append(f"{where}: synonym {name!r} is repeated (ignoring case)")
                seen.add(folded)
    return problems


def write_species_file(store_dir: Path, taxon_ids, species: dict) -> bool:
    """Write ``<store_dir>/species.json`` for these taxa, if its text changed.

    Raises ValueError (and writes nothing) if a taxon is not on the list or the
    document fails the contract check. Returns whether it wrote.
    """
    doc = species_document(taxon_ids, species)
    problems = check_species_document(doc)
    if problems:
        raise ValueError(f"{SPECIES_FILE} fails the contract check: " + "; ".join(problems))
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    path = Path(store_dir) / SPECIES_FILE
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def taxa_in_store_dir(store_dir: Path) -> set:
    """The taxon ids a built store's FHR sidecars (``fhr/*.fhr.json``) name."""
    taxa = set()
    no_taxon = 0
    for path in sorted((Path(store_dir) / "fhr").glob("*.fhr.json")):
        tid = taxon_id_from_fhr(json.loads(path.read_text(encoding="utf-8")))
        if tid is None:
            no_taxon += 1
        else:
            taxa.add(tid)
    if no_taxon:
        print(f"NOTE: {no_taxon} FHR sidecar(s) in {store_dir} name no taxon; "
              f"they add nothing to {SPECIES_FILE}.", file=sys.stderr)
    return taxa


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def genome_taxa_problems(species: dict, genomes_dir: Path = GENOMES_DIR) -> list:
    """Every genome YAML whose ``organism.taxon_id`` is not on the list."""
    import yaml  # only the `check` command reads genome YAMLs

    listed = {e.get("taxon_id") for e in species.values() if isinstance(e, dict)}
    problems = []
    for path in sorted(Path(genomes_dir).rglob("*.yaml")):
        organism = ((yaml.safe_load(path.read_text()) or {}).get("organism")) or {}
        tid = organism.get("taxon_id")
        if tid is not None and tid not in listed:
            problems.append(f"{path.relative_to(REPO)}: organism.taxon_id {tid} "
                            f"({organism.get('scientific_name')}) is not on the species "
                            f"list {LIST_NAME}")
    return problems


def _cmd_check(args) -> int:
    species = load_species_list()
    problems = check_species_list(species) + genome_taxa_problems(species)
    for problem in problems:
        print(f"  - {problem}")
    if problems:
        print(f"FAIL {LIST_NAME}: {len(problems)} problem(s)")
        return 1
    print(f"PASS {LIST_NAME}: {len(species)} species; every store organism and "
          f"genome taxon is listed")
    return 0


def _cmd_store_file(args) -> int:
    store_dir = Path(args.store_dir)
    if not store_dir.is_dir():
        print(f"ERROR: no such store directory: {store_dir}", file=sys.stderr)
        return 1
    taxa = taxa_in_store_dir(store_dir)
    if not taxa:
        print(f"{store_dir}: no FHR sidecar names a taxon; {SPECIES_FILE} not written")
        return 0
    try:
        wrote = write_species_file(store_dir, taxa, load_species_list())
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"{store_dir / SPECIES_FILE}: {len(taxa)} species "
          f"({'written' if wrote else 'unchanged'})")
    return 0


def _cmd_check_file(args) -> int:
    try:
        doc = json.loads(Path(args.path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"FAIL {args.path}: cannot read it as JSON: {exc}")
        return 1
    problems = check_species_document(doc)
    for problem in problems:
        print(f"  - {problem}")
    if problems:
        print(f"FAIL {args.path}: {len(problems)} problem(s)")
        return 1
    print(f"PASS {args.path}: {len(doc['species'])} species")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="check the list, and that every genome YAML taxon is on it")
    store_file = sub.add_parser("store-file",
                                help="write species.json for a built store from its sidecars")
    store_file.add_argument("store_dir", help="a built RefgetStore directory")
    check_file = sub.add_parser("check-file", help="check a species.json against the contract")
    check_file.add_argument("path", help="path to a species.json")
    args = parser.parse_args(argv)
    return {"check": _cmd_check, "store-file": _cmd_store_file,
            "check-file": _cmd_check_file}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
