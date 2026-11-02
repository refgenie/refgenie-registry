#!/usr/bin/env python3
"""Give each store row the same name as its genome YAML.

A genome YAML and the store row that holds its sequence must share one name:
the row's `name` equals the YAML `name`, and any other name lives in the YAML
`aliases`. This finds every row whose name does not match yet (rules in
stores/genome_rows.py), prints the renames, and with --apply writes them:

  * stores/<store>/sources.csv: the row's `name` becomes the YAML name; no other
    field or column changes.
  * the genome YAML: the old row name is added to `aliases` (by inserting one
    line, so the rest of the file is untouched).

Rows with no YAML are left alone. Anything ambiguous or contradictory is
reported and not renamed, and makes the exit status non-zero.

Row digests let the `digest` rule work. Pass --catalog with a refgenie catalog
(e.g. the public publish_catalog.sqlite, whose alias table maps names to
digests), or set REFGETSTORE_BASE so built stores' .row_digests.json are read.

Usage:
    python tools/align_store_names.py --catalog publish_catalog.sqlite
    python tools/align_store_names.py --catalog publish_catalog.sqlite --apply
    python tools/align_store_names.py --store vgp
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sqlite3
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "stores"))
from genome_rows import (  # noqa: E402
    STORES_DIR,
    catalog_row_digests,
    load_genomes,
    match_store,
    read_rows,
    row_name,
    staged_fastas,
    store_row_digests,
)


def read_catalog(path: Path) -> dict:
    """{alias name: genome digest} from a refgenie catalog sqlite file."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return dict(con.execute("SELECT name, genome_digest FROM alias"))
    finally:
        con.close()


def yaml_scalar(value: str) -> str:
    """`value` as a YAML scalar that loads back as the same string."""
    try:
        if yaml.safe_load(value) == value and ": " not in value and " #" not in value:
            return value
    except yaml.YAMLError:
        pass
    return "'" + value.replace("'", "''") + "'"


def add_alias(text: str, alias: str) -> str:
    """Return YAML `text` with `alias` appended to its top-level `aliases` list.

    Edits by inserting a line (not a YAML round-trip) so nothing else moves. A
    file with no `aliases` gets a new block right after its `name:` line.
    """
    lines = text.splitlines(keepends=True)
    item = yaml_scalar(alias)
    start = next((i for i, l in enumerate(lines) if re.match(r"aliases:\s*$", l)), None)
    if start is None:
        at = next(i for i, l in enumerate(lines) if l.startswith("name:")) + 1
        lines[at:at] = ["aliases:\n", f"  - {item}\n"]
        return "".join(lines)
    end = start + 1
    indent = "  "
    while end < len(lines):
        m = re.match(r"(\s*)- ", lines[end])
        if not m:
            break
        indent = m.group(1)
        end += 1
    if not lines[end - 1].endswith("\n"):
        lines[end - 1] += "\n"
    lines.insert(end, f"{indent}- {item}\n")
    return "".join(lines)


def rename_rows(slug: str, renames: dict, stores_dir: Path = STORES_DIR) -> None:
    """Rewrite stores/<slug>/sources.csv with row `name`s replaced ({index: new})."""
    path = Path(stores_dir) / slug / "sources.csv"
    raw = path.read_text()
    newline = "\r\n" if "\r\n" in raw else "\n"
    reader = csv.DictReader(io.StringIO(raw, newline=""))
    fields = reader.fieldnames
    rows = list(reader)
    for i, new in renames.items():
        rows[i]["name"] = new
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fields, lineterminator=newline)
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(out.getvalue())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--catalog", type=Path,
                        help="refgenie catalog sqlite, for row digests")
    parser.add_argument("--store", action="append",
                        help="only this store (repeatable); default every store with YAMLs")
    parser.add_argument("--apply", action="store_true", help="write the renames")
    args = parser.parse_args(argv)

    catalog = read_catalog(args.catalog) if args.catalog else {}
    genomes = load_genomes()
    by_store = {}
    for g in genomes:
        if g.store:
            by_store.setdefault(g.store, []).append(g)
    slugs = args.store or sorted(by_store)

    bad = 0
    total = 0
    print(f"{'store':14} {'rule':9} {'row name (old)':45} {'YAML name (new)':40} digest")
    for slug in slugs:
        rows = read_rows(slug)
        digests = store_row_digests(slug, rows) or catalog_row_digests(rows, catalog)
        if rows and "name" not in rows[0]:
            print(f"{slug:14} SKIPPED   sources.csv has no `name` column", file=sys.stderr)
            bad += len(by_store.get(slug, []))
            continue
        found = match_store(by_store.get(slug, []), rows, digests, staged_fastas(slug))
        named = {g.name: g for g in by_store.get(slug, [])}
        renames = {}
        for name, (i, rule) in sorted(found.matches.items()):
            old = row_name(rows[i])
            if old == name:
                continue
            renames[i] = name
            print(f"{slug:14} {rule:9} {old:45} {name:40} {digests.get(i, '-')}")
        for name, msg in found.errors:
            bad += 1
            print(f"{slug:14} ERROR     {name}: {msg}", file=sys.stderr)
        for name, msg in found.problems:
            bad += 1
            print(f"{slug:14} UNMATCHED {name}: {msg}", file=sys.stderr)
        total += len(renames)

        if args.apply and renames:
            for i, new in renames.items():
                old = row_name(rows[i])
                g = named[new]
                if old not in g.aliases:
                    g.path.write_text(add_alias(g.path.read_text(), old))
                    assert old in (yaml.safe_load(g.path.read_text()).get("aliases") or [])
            rename_rows(slug, renames)

    print(f"\nalign_store_names: {total} row(s) {'renamed' if args.apply else 'to rename'}, "
          f"{bad} genome(s) unmatched or in error")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
