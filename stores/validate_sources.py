#!/usr/bin/env python3
"""Validate a refgetstore sources.csv against sources_schema.json."""

import csv
import json
import re
import sys
from pathlib import Path

import jsonschema

SCHEMA_PATH = Path(__file__).parent / "sources_schema.json"


def load_csv_as_records(csv_path: str) -> list[dict]:
    with open(csv_path) as f:
        return list(csv.DictReader(f))


# Assembly names, each mapped to (species, canonical assembly). A genome's
# names must not name another assembly of the same species: the jungle store
# once filed NCBI36 and GRCh37 FASTAs under hg38, so `hg38_build37_2_igenomes`
# named a GRCh37 genome. Other species' names are fine (`hg38_mm10` is a
# combined human and mouse genome).
ASSEMBLIES = {
    "hg38": ("human", "hg38"), "grch38": ("human", "hg38"),
    "hg19": ("human", "hg19"), "grch37": ("human", "hg19"), "hs37": ("human", "hg19"),
    "hg18": ("human", "hg18"), "ncbi36": ("human", "hg18"),
    "mm39": ("mouse", "mm39"), "grcm39": ("mouse", "mm39"),
    "mm10": ("mouse", "mm10"), "grcm38": ("mouse", "mm10"), "mm38": ("mouse", "mm10"),
    "mm9": ("mouse", "mm9"), "ncbim37": ("mouse", "mm9"), "mm37": ("mouse", "mm9"),
}  # fmt: skip
_ASSEMBLY_TOKEN = re.compile(
    r"(?<![a-z0-9])(" + "|".join(sorted(ASSEMBLIES, key=len, reverse=True)) + r")(?![0-9])",
    re.IGNORECASE,
)


def assembly_conflicts(records: list[dict]) -> list[str]:
    """Rows whose name or PEP sample name names a different assembly of the
    same species than their genome_assembly column."""
    errors = []
    for i, row in enumerate(records):
        declared = ASSEMBLIES.get((row.get("genome_assembly") or "").strip().lower())
        if declared is None:
            continue
        for field in ("name", "pep_sample_name"):
            for token in _ASSEMBLY_TOKEN.findall(row.get(field) or ""):
                species, assembly = ASSEMBLIES[token.lower()]
                if species == declared[0] and assembly != declared[1]:
                    errors.append(
                        f"Row {i + 1}, field '{field}': '{row[field]}' names {token}, but "
                        f"genome_assembly is {row['genome_assembly']}"
                    )
    return errors


def validate(csv_path: str, schema_path: str = None) -> list[str]:
    schema_path = schema_path or str(SCHEMA_PATH)
    with open(schema_path) as f:
        schema = json.load(f)

    records = load_csv_as_records(csv_path)
    if not records:
        return ["Empty CSV file (no data rows)."]

    errors = []
    validator = jsonschema.Draft202012Validator(schema)
    for error in validator.iter_errors(records):
        if error.path:
            row = error.path[0]
            field = error.path[1] if len(error.path) > 1 else "?"
            errors.append(f"Row {row + 1}, field '{field}': {error.message}")
        else:
            errors.append(error.message)

    columns = set(records[0].keys())
    if "fasta" not in columns:
        errors.append("Missing required column: 'fasta'")

    empty_fasta = [i for i, r in enumerate(records) if not r.get("fasta", "").strip()]
    if empty_fasta:
        errors.append(f"Empty 'fasta' values in rows: {[i + 1 for i in empty_fasta[:10]]}")

    errors.extend(assembly_conflicts(records))
    return errors


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <sources.csv> [schema.json]")
        sys.exit(1)

    csv_path = sys.argv[1]
    schema_path = sys.argv[2] if len(sys.argv) > 2 else None
    errors = validate(csv_path, schema_path)

    if errors:
        print(f"FAILED: {csv_path}")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        records = load_csv_as_records(csv_path)
        columns = list(records[0].keys())
        print(f"PASSED: {csv_path}")
        print(f"  Rows: {len(records)}")
        print(f"  Columns: {', '.join(columns)}")


if __name__ == "__main__":
    main()
