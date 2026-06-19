#!/usr/bin/env python3
"""Validate a refgetstore sources.csv against sources_schema.json."""

import csv
import json
import sys
from pathlib import Path

import jsonschema

SCHEMA_PATH = Path(__file__).parent / "sources_schema.json"


def load_csv_as_records(csv_path: str) -> list[dict]:
    with open(csv_path) as f:
        return list(csv.DictReader(f))


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
