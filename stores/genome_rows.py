#!/usr/bin/env python3
"""Link genome YAMLs to the store rows that hold their sequence.

A genome YAML (`genomes/**/*.yaml`) names a genome; a row in
`stores/<store>/sources.csv` says which FASTA a store ingests. When a row has a
YAML, the row's `name` must equal the YAML's `name` exactly: that string is the
key between the two lists, and the YAML `aliases` carry every other name.

`match_store()` finds each YAML's row, in this order, and stops at the first
rule that gives an answer:

  name    a row whose `name` equals the YAML `name` (the aligned state)
  digest  the YAML `seqcol.digest` equals the row's collection digest
  alias   the row's `name` is one of the YAML `aliases`
  staged  the row's `fasta` is the file pep/config.yaml stages for the genome
  accession  only for a YAML with no `seqcol.digest`: the assembly accession
          matches exactly one row, and no other YAML in the store has it

A row picked by `alias` or `accession` whose digest is known and differs from
the YAML `seqcol.digest` is an error, never a match. A YAML `seqcol.digest`
that no row in the store holds is an error too. Anything ambiguous (two rows
for one YAML, two YAMLs for one row) is reported and left unmatched. The one
exception: when several rows hold the YAML's exact digest (the same genome
staged from several files, e.g. soft-masked and plain), the first of them named
in the YAML `aliases` is the YAML's row; the others stay as they are.

Row digests are optional (`row_digests`, a {row index: digest} map). They come
from a built store's `.row_digests.json` or from a refgenie catalog; without
them only the `name`, `alias` and `accession` rules can apply.
"""
from __future__ import annotations

import csv
import glob
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

STORES_DIR = Path(__file__).resolve().parent
REPO = STORES_DIR.parent
GENOMES_DIR = REPO / "genomes"
ROW_DIGESTS = ".row_digests.json"
PEP_CONFIG = REPO / "pep" / "config.yaml"
# pep/config.yaml and every store's `fasta_root:` root their paths at the same
# env var, written either way; normalizing lets them be compared as text.
FASTA_VAR = ("${REFGETSTORE_FASTA}", "$REFGETSTORE_FASTA")


@dataclass
class Genome:
    name: str
    data: dict
    path: Path

    @property
    def store(self):
        return (self.data.get("build") or {}).get("store")

    @property
    def aliases(self) -> list:
        return [str(a) for a in (self.data.get("aliases") or [])]

    @property
    def digest(self):
        return (self.data.get("seqcol") or {}).get("digest")

    @property
    def accession(self):
        return (self.data.get("assembly") or {}).get("accession")


@dataclass
class StoreMatch:
    matches: dict = field(default_factory=dict)   # genome name -> (row index, rule)
    problems: list = field(default_factory=list)  # (genome name, message), unmatched
    errors: list = field(default_factory=list)    # (genome name, message), data is wrong


def load_genomes(genomes_dir: Path = GENOMES_DIR) -> list:
    """Every genome YAML as a Genome, sorted by name."""
    out = []
    for path in sorted(glob.glob(str(Path(genomes_dir) / "**" / "*.yaml"), recursive=True)):
        with open(path) as fh:
            data = yaml.safe_load(fh)
        if isinstance(data, dict) and data.get("name"):
            out.append(Genome(str(data["name"]), data, Path(path)))
    out.sort(key=lambda g: g.name)
    return out


def read_rows(slug: str, stores_dir: Path = STORES_DIR) -> list:
    path = Path(stores_dir) / slug / "sources.csv"
    if not path.is_file():
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def row_name(row: dict) -> str:
    return (row.get("name") or "").strip()


# The sources.csv columns registered as collection aliases. `name` is unique
# per row; `accession` and `genome_assembly` are often shared (five jungle rows
# are GCA_000001405.15, many are hg38), and an alias must name one collection.
ROW_ALIAS_COLUMNS = ("name", "accession", "genome_assembly")


def row_aliases(rows: list) -> list:
    """[(namespace, value)] per row: every ROW_ALIAS_COLUMNS value that names
    exactly one row of the store. Shared values are skipped, not guessed."""
    counts = {}
    for row in rows:
        for col in ROW_ALIAS_COLUMNS:
            val = (row.get(col) or "").strip()
            if val:
                counts[(col, val)] = counts.get((col, val), 0) + 1
    return [
        [(col, (row.get(col) or "").strip()) for col in ROW_ALIAS_COLUMNS
         if counts.get((col, (row.get(col) or "").strip())) == 1]
        for row in rows
    ]


def row_fasta(row: dict) -> str:
    return " ".join((row.get("fasta") or "").split())


def _normalize_root(path: str) -> str:
    for form in FASTA_VAR:
        if path.startswith(form):
            return "@FASTA@" + path[len(form):]
    return path


def staged_fastas(slug: str, pep_config: Path = PEP_CONFIG,
                  stores_dir: Path = STORES_DIR) -> dict:
    """{genome: fasta path relative to stores/<slug>'s fasta_root} from pep/config.yaml.

    pep/config.yaml's `<genome>_fa` sources name the file the nightly ingests
    for each queued genome. When that file sits under the store's `fasta_root:`,
    the relative part is exactly the store row's `fasta`.
    """
    from store_config import load_pep

    root = load_pep(Path(stores_dir) / slug).get("fasta_root")
    if not root or not Path(pep_config).is_file():
        return {}
    root = _normalize_root(str(root)).rstrip("/") + "/"
    cfg = yaml.safe_load(Path(pep_config).read_text()) or {}
    sources = ((cfg.get("sample_modifiers") or {}).get("derive") or {}).get("sources") or {}
    out = {}
    for key, value in sources.items():
        path = _normalize_root(str(value))
        if key.endswith("_fa") and path.startswith(root):
            out[key[: -len("_fa")]] = path[len(root):]
    return out


def store_row_digests(slug: str, rows: list, base: str | None = None) -> dict:
    """{row index: digest} from $REFGETSTORE_BASE/<slug>/.row_digests.json, if built.

    The file maps each row's `fasta` value to its collection digest; build.py
    writes it. Returns {} when the store was never built on this machine.
    """
    base = base or os.environ.get("REFGETSTORE_BASE")
    if not base:
        return {}
    path = Path(base) / slug / ROW_DIGESTS
    if not path.is_file():
        return {}
    by_fasta = json.loads(path.read_text())
    return {i: by_fasta[row_fasta(r)] for i, r in enumerate(rows) if row_fasta(r) in by_fasta}


def catalog_row_digests(rows: list, catalog: dict) -> dict:
    """{row index: digest} from a {alias name: digest} map (a refgenie catalog)."""
    return {i: catalog[row_name(r)] for i, r in enumerate(rows) if row_name(r) in catalog}


def match_store(genomes: list, rows: list, row_digests: dict | None = None,
                staged: dict | None = None) -> StoreMatch:
    """Match each genome of one store to its row. See the module docstring.

    ``staged`` is {genome name: fasta path relative to the store's fasta_root}
    (see staged_fastas()).
    """
    row_digests = row_digests or {}
    staged = staged or {}
    result = StoreMatch()
    by_name = {}
    for i, row in enumerate(rows):
        by_name.setdefault(row_name(row), []).append(i)
    known_digests = set(row_digests.values())
    accession_count = {}
    for g in genomes:
        if g.accession:
            accession_count[g.accession] = accession_count.get(g.accession, 0) + 1

    for g in genomes:
        exact = by_name.get(g.name, [])
        if len(exact) == 1:
            found = row_digests.get(exact[0])
            if g.digest and found and found != g.digest:
                result.errors.append(
                    (g.name, f"row {g.name!r} has digest {found}, "
                             f"but seqcol.digest is {g.digest}"))
            else:
                result.matches[g.name] = (exact[0], "name")
            continue
        if len(exact) > 1:
            result.problems.append((g.name, f"{len(exact)} rows are named {g.name!r}"))
            continue

        aliased = [i for a in g.aliases for i in by_name.get(a, [])]
        aliased = list(dict.fromkeys(aliased))

        if g.digest and g.digest in known_digests:
            hits = [i for i, d in row_digests.items() if d == g.digest]
            if len(hits) > 1:
                narrowed = [i for i in aliased if i in hits]
                if narrowed:
                    hits = narrowed[:1]
                else:
                    names = sorted(row_name(rows[i]) for i in hits)
                    result.problems.append(
                        (g.name, f"seqcol.digest matches {len(hits)} rows {names}, "
                                 f"none named in aliases"))
                    continue
            result.matches[g.name] = (hits[0], "digest")
            continue

        chosen = rule = None
        if len(aliased) == 1:
            chosen, rule = aliased[0], "alias"
        elif len(aliased) > 1:
            names = [row_name(rows[i]) for i in aliased]
            result.problems.append((g.name, f"aliases name {len(aliased)} rows {names}"))
            continue
        elif staged.get(g.name):
            hits = [i for i, r in enumerate(rows) if row_fasta(r) == staged[g.name]]
            if len(hits) == 1:
                chosen, rule = hits[0], "staged"
            elif hits:
                result.problems.append((g.name, f"{len(hits)} rows ingest {staged[g.name]}"))
                continue
        if chosen is None and not g.digest and g.accession \
                and accession_count.get(g.accession) == 1:
            hits = [i for i, r in enumerate(rows)
                    if (r.get("accession") or "").strip() == g.accession]
            if len(hits) == 1:
                chosen, rule = hits[0], "accession"

        if chosen is None:
            if g.digest and known_digests:
                result.errors.append((g.name, f"seqcol.digest {g.digest} is not in the store"))
            else:
                result.problems.append((g.name, "no row found"))
            continue
        found = row_digests.get(chosen)
        if g.digest and found and found != g.digest:
            result.errors.append(
                (g.name, f"row {row_name(rows[chosen])!r} (by {rule}) has digest {found}, "
                         f"but seqcol.digest is {g.digest}"))
            continue
        result.matches[g.name] = (chosen, rule)

    claimed = {}
    for name, (i, _rule) in result.matches.items():
        claimed.setdefault(i, []).append(name)
    for i, names in claimed.items():
        if len(names) > 1:
            for name in names:
                del result.matches[name]
                result.problems.append(
                    (name, f"row {row_name(rows[i])!r} is also claimed by "
                           f"{sorted(n for n in names if n != name)}"))
    return result
