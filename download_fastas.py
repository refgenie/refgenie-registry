#!/usr/bin/env python3
"""Download FASTAs from a sources.csv file, splitting space-separated URLs.

Cached files are named with the collision-free contract in
stores/fasta_naming.py (a single source of truth shared with build.py and
validate_files.py), so different releases that share a basename (e.g. 80
Ensembl releases of Homo_sapiens.GRCh38.cdna.all.fa.gz) no longer overwrite
each other in the cache.
"""
import csv
import os
import sys
import urllib.request

# fasta_naming.py lives in stores/ (next to build.py). This script runs from
# the repo root, so add stores/ to the import path.
_STORES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stores")
if _STORES_DIR not in sys.path:
    sys.path.insert(0, _STORES_DIR)

from fasta_naming import is_url, s3_to_https, cache_name_for

sources_csv = sys.argv[1]
download_dir = sys.argv[2]

os.makedirs(download_dir, exist_ok=True)

with open(sources_csv) as f:
    reader = csv.DictReader(f)
    rows = list(reader)

total = len(rows)
downloaded = 0
skipped = 0
failed = 0

for i, row in enumerate(rows):
    fasta_field = row["fasta"].strip()
    urls = fasta_field.split()
    for url in urls:
        if not is_url(url):
            continue
        filename = cache_name_for(url, row)
        fetch_url = s3_to_https(url) if url.startswith("s3://") else url
        dest = os.path.join(download_dir, filename)
        if os.path.exists(dest):
            print(f"  [{i+1}/{total}] exists  {filename}")
            skipped += 1
            continue
        try:
            print(f"  [{i+1}/{total}] downloading {filename}...", flush=True)
            urllib.request.urlretrieve(fetch_url, dest)
            print(f"  [{i+1}/{total}] done    {filename}")
            downloaded += 1
        except Exception as e:
            print(f"  [{i+1}/{total}] FAILED  {filename}: {e}", file=sys.stderr)
            failed += 1

print(f"\nSummary: {downloaded} downloaded, {skipped} already existed, {failed} failed")
