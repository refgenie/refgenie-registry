#!/usr/bin/env python3
"""Single source of truth for collision-free FASTA cache naming.

Stdlib-only (no pandas/peppy/refget) so it can be imported by build.py,
download_fastas.py, and validate_files.py without drift.

A store's `sources.csv` `fasta` column holds URLs/paths; many sources share a
basename (e.g. every iGenomes file is `genome.fa`, and a single Ensembl cDNA
basename is referenced by 80 different release URLs). Naming cached files by
basename alone silently overwrites distinct sources. `cache_name_for` builds a
collision-free name from the row's disambiguating columns.
"""

from __future__ import annotations

# Public-bucket regions for s3:// sources we know how to fetch over HTTPS.
S3_BUCKET_REGION = {"ngi-igenomes": "eu-west-1"}


def is_url(s: str) -> bool:
    return s.startswith(("http://", "https://", "ftp://", "s3://"))


def s3_to_https(url: str) -> str:
    """Rewrite an s3:// URL to its public virtual-hosted HTTPS endpoint.

    Avoids needing the aws CLI / credentials for public buckets like
    ngi-igenomes (AWS iGenomes). Region is eu-west-1 for ngi-igenomes,
    else us-east-1.
    """
    bucket, _, key = url[len("s3://"):].partition("/")
    region = S3_BUCKET_REGION.get(bucket, "us-east-1")
    return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"


def _slug(s: str) -> str:
    """Collapse whitespace runs to `_` and replace `/` with `_`."""
    return "_".join(s.strip().split()).replace("/", "_")


def cache_name_for(url: str, row: dict) -> str:
    """Collision-free local cache filename for one URL/path token.

    1. basename = url.rstrip('/').split('/')[-1]
    2. disambiguator: row['name'] if non-empty, else
       '_'.join(row[k] for k in (source,organism,version,genome_assembly) if set)
    3. slug collapses whitespace to '_' and replaces '/' with '_'
    4. filename = slug(disambig) + '__' + basename  (or just basename if no disambig)
    """
    basename = url.rstrip("/").split("/")[-1]

    name = str(row.get("name", "") or "").strip()
    if name:
        disambig = name
    else:
        parts = []
        for k in ("source", "organism", "version", "genome_assembly"):
            v = str(row.get(k, "") or "").strip()
            if v:
                parts.append(v)
        disambig = "_".join(parts)

    if disambig:
        return _slug(disambig) + "__" + basename
    return basename
