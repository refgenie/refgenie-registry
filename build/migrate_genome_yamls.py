#!/usr/bin/env python3
"""Migrate genome YAML files to the FHR-aligned registry schema.

This is the reproducible, idempotent record of the genome-metadata migration to
the schema defined in ``schema/genome.schema.yaml`` (validated by
``tools/validate_genome.py`` and exported by ``tools/genome_to_fhr.py``).

Design note — what "migration" means here
------------------------------------------
The FHR-aligned schema keeps ``organism.taxon_id`` as an **integer** and the
exporter (``tools/genome_to_fhr.py``) *derives* the FHR ``taxon.uri`` as
``https://identifiers.org/taxonomy:{taxon_id}`` at export time. The URI is NOT
stored in the YAML. Because of that decision, the existing corpus is already in
the target integer-taxon shape, so this script is, for the current 97 files,
essentially a verifier: it round-trips each file, applies the normalization
rules below, and rewrites ONLY files that genuinely need a change. Compliant
files are left byte-identical on disk (nothing is written for them), so a second
run produces zero diff.

Normalization rules applied (only when a file actually needs them)
------------------------------------------------------------------
1. Multi-organism / hybrid ``scientific_name`` (contains " / "): the schema's
   ``organism`` block holds a single taxon object, so we resolve to the PRIMARY
   taxon — the component whose NCBI id equals the file's existing ``taxon_id``
   (or the first component if none matches, setting ``taxon_id`` to match) — set
   ``scientific_name`` to that primary, and record every organism (with NCBI
   taxon ids) in the ``description`` as a provenance note. This keeps the FHR
   export self-consistent (``taxon.name`` and ``taxon.uri`` agree) instead of
   pairing a two-species name with a single-species URI.
2. Missing/invalid ``taxon_id`` recoverable from ``scientific_name`` via the
   NCBI lookup table below: fill it. (Defensive; 0 files in the current corpus.)
3. Crammed / malformed ``description`` carrying literal ``key: value`` organism
   lines: strip those lines, collapse to one trimmed sentence, ensure it ends in
   a period. (Defensive; 0 files in the current corpus.)

Anything that cannot be resolved confidently (a multi-species or missing-id file
whose organism is not in the lookup table) is classified ``needs-attention`` and
the script exits non-zero — the gap is surfaced, never silently guessed.

Usage
-----
    python build/migrate_genome_yamls.py                 # rewrite in place
    python build/migrate_genome_yamls.py --dry-run       # print diffs, write nothing
    python build/migrate_genome_yamls.py --report        # per-file classification
    python build/migrate_genome_yamls.py genomes/human/hg38.yaml   # specific files
"""

from __future__ import annotations

import argparse
import difflib
import io
import re
import sys
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import LiteralScalarString

GENOMES_DIR = Path(__file__).resolve().parent.parent / "genomes"

# Authoritative scientific_name -> NCBI taxon id lookup. Covers every organism
# present in the corpus plus yeast, for the defensive fill/hybrid-resolution
# paths. The integer already in each file is trusted first; this table is only a
# fallback for missing ids and for resolving hybrid components.
NCBI_TAXA = {
    "Homo sapiens": 9606,
    "Mus musculus": 10090,
    "Rattus norvegicus": 10116,
    "Drosophila melanogaster": 7227,
    "Saccharomyces cerevisiae": 4932,
    "Enterobacteria phage T7": 10760,
}

# Common names, used to phrase the hybrid provenance note when available.
_HYBRID_MARKER = "Dual-species hybrid"

# Detects organism metadata accidentally embedded as text inside description.
_CRAMMED_RE = re.compile(
    r"^\s*(scientific_name|taxon_id|common_name|organism)\s*:", re.MULTILINE
)


def _make_yaml() -> YAML:
    """Round-trip YAML configured to match the corpus's hand-written style."""
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096  # never line-wrap long scalars
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def _valid_taxon(tid) -> bool:
    return isinstance(tid, int) and not isinstance(tid, bool) and tid >= 1


def _clean_description(text: str) -> str:
    """Collapse a description to one trimmed sentence, dropping any embedded
    ``key: value`` organism lines. Ensures it ends with a period."""
    lines = []
    for line in str(text).splitlines():
        if _CRAMMED_RE.match(line):
            continue
        stripped = line.strip()
        if stripped:
            lines.append(stripped)
    joined = " ".join(lines).strip()
    if joined and not joined.endswith((".", "!", "?")):
        joined += "."
    return joined


def _resolve_hybrid(scientific_name: str, taxon_id):
    """Resolve a multi-organism ``scientific_name`` (" / " joined) to a primary
    component + ordered species list. Returns (primary_name, primary_taxon,
    species) where species is a list of (name, taxon_id_or_None) for all
    components. Raises KeyError if a component cannot be resolved."""
    components = [c.strip() for c in scientific_name.split("/") if c.strip()]
    species = []
    for name in components:
        species.append((name, NCBI_TAXA.get(name)))

    # Primary = the component whose id matches the file's existing taxon_id.
    primary = None
    if _valid_taxon(taxon_id):
        for name, tid in species:
            if tid == taxon_id:
                primary = (name, tid)
                break
    if primary is None:
        # Fall back to the first component; require it be resolvable.
        name, tid = species[0]
        if tid is None:
            raise KeyError(name)
        primary = (name, tid)

    # Reorder species so the primary comes first, secondaries follow.
    ordered = [primary] + [s for s in species if s != primary]
    for name, tid in ordered:
        if tid is None:
            raise KeyError(name)
    return primary[0], primary[1], ordered


def _hybrid_note(species) -> str:
    """Build the provenance note recording every organism in a hybrid."""
    primary_name, primary_tid = species[0]
    secondaries = species[1:]
    sec_str = ", ".join(
        f"{name} (NCBI taxon {tid})" for name, tid in secondaries
    )
    return (
        f"{_HYBRID_MARKER}; primary organism {primary_name} "
        f"(NCBI taxon {primary_tid}), also contains {sec_str}."
    )


def transform(data) -> tuple[bool, str, list[str]]:
    """Apply normalization rules in place on a ruamel-loaded mapping.

    Returns (changed, status, notes). status is one of:
      already-fhr  -- no change needed (compliant)
      migrated     -- a change was applied
      needs-attention -- could not resolve confidently (caller exits non-zero)
    """
    notes: list[str] = []
    changed = False
    status = "already-fhr"

    organism = data.get("organism")
    if not isinstance(organism, dict):
        return False, "needs-attention", ["organism block missing or not a mapping"]

    sci = organism.get("scientific_name")
    tid = organism.get("taxon_id")

    # Rule 3 (defensive): crammed description.
    desc = data.get("description")
    if isinstance(desc, str) and _CRAMMED_RE.search(desc):
        cleaned = _clean_description(desc)
        if cleaned and cleaned != desc.strip():
            data["description"] = LiteralScalarString(cleaned + "\n")
            changed = True
            status = "migrated"
            notes.append("normalized crammed description")

    # Rule 1: multi-organism / hybrid scientific_name.
    if isinstance(sci, str) and "/" in sci:
        try:
            primary_name, primary_tid, species = _resolve_hybrid(sci, tid)
        except KeyError as exc:
            return False, "needs-attention", [
                f"unresolved hybrid component (not in NCBI lookup): {exc.args[0]!r}"
            ]
        organism["scientific_name"] = primary_name
        if organism.get("taxon_id") != primary_tid:
            organism["taxon_id"] = primary_tid
        # Record all organisms in the description as a provenance note.
        cur = data.get("description")
        base = str(cur).strip() if cur is not None else ""
        if _HYBRID_MARKER not in base:
            note = _hybrid_note(species)
            new_desc = f"{base} {note}".strip() if base else note
            data["description"] = LiteralScalarString(new_desc + "\n")
        changed = True
        status = "migrated"
        notes.append(
            f"resolved hybrid -> primary {primary_name} (taxon {primary_tid}); "
            f"recorded {len(species) - 1} secondary organism(s) in description"
        )
        sci = primary_name
        tid = primary_tid

    # Rule 2 (defensive): fill a missing/invalid taxon_id from the lookup table.
    if not _valid_taxon(organism.get("taxon_id")):
        if isinstance(sci, str) and sci in NCBI_TAXA:
            organism["taxon_id"] = NCBI_TAXA[sci]
            changed = True
            status = "migrated"
            notes.append(f"filled taxon_id={NCBI_TAXA[sci]} from scientific_name")
        else:
            return False, "needs-attention", [
                f"missing/invalid taxon_id and scientific_name {sci!r} "
                f"not in NCBI lookup table"
            ]

    return changed, status, notes


def process_file(path: Path, write: bool, dry_run: bool):
    """Process one file. Returns (status, notes, diff_or_None)."""
    yaml = _make_yaml()
    original = path.read_text()
    data = yaml.load(io.StringIO(original))
    if not isinstance(data, dict):
        return "needs-attention", ["not a YAML mapping"], None

    changed, status, notes = transform(data)
    if not changed:
        return status, notes, None

    buf = io.StringIO()
    yaml.dump(data, buf)
    new_text = buf.getvalue()

    if new_text == original:
        # Transform decided nothing observable changed; treat as compliant.
        return "already-fhr", notes, None

    diff = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=str(path),
            tofile=str(path),
        )
    )
    if write and not dry_run:
        path.write_text(new_text)
    return status, notes, diff


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate genome YAMLs to the FHR-aligned schema (idempotent)."
    )
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        help="Genome YAML files (default: all genomes/**/*.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the unified diff for each file that would change; write nothing.",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Print a per-file classification (migrated/already-fhr/needs-attention).",
    )
    args = parser.parse_args()

    files = args.files or sorted(GENOMES_DIR.rglob("*.yaml"))
    if not files:
        print("No genome YAML files found.", file=sys.stderr)
        sys.exit(1)

    write = not args.dry_run
    counts = {"migrated": 0, "already-fhr": 0, "needs-attention": 0}
    attention: list[Path] = []

    for path in files:
        if not path.exists():
            print(f"SKIP {path} (file not found)")
            continue
        status, notes, diff = process_file(path, write=write, dry_run=args.dry_run)
        counts[status] = counts.get(status, 0) + 1
        if status == "needs-attention":
            attention.append(path)

        if args.report:
            note_str = f" ({'; '.join(notes)})" if notes else ""
            print(f"{status.upper():16} {path}{note_str}")
        elif status == "migrated":
            verb = "would migrate" if args.dry_run else "migrated"
            print(f"{verb.upper()} {path}: {'; '.join(notes)}")
            if args.dry_run and diff:
                print(diff)
        elif status == "needs-attention":
            print(f"NEEDS-ATTENTION {path}: {'; '.join(notes)}")

    print(
        f"\nSummary: {counts['migrated']} migrated, "
        f"{counts['already-fhr']} already-compliant, "
        f"{counts['needs-attention']} needs-attention "
        f"({sum(counts.values())} total)"
    )
    if attention:
        print("Files needing attention:", file=sys.stderr)
        for p in attention:
            print(f"  - {p}", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
