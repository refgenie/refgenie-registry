#!/usr/bin/env python3
"""Validate that the FASTA sources referenced by a store actually exist.

Complements validate_sources.py (which checks schema/structure). This script
checks that the *data* each row points at is reachable:

  - Local absolute paths   -> must exist on disk.
  - Relative paths         -> resolved relative to the stores/ directory
                              (where build.py runs), then must exist.
  - URL entries (http/https/ftp) -> reported as remote. Reported as CACHED if a
                              file with the same basename is already present in
                              the store's download cache ($REFGETSTORE_BASE/
                              .downloads_<store>). With --check-urls, http(s)
                              URLs are probed with a HEAD request.

A `fasta` field may contain several space-separated entries (concatenated at
build time); each entry is validated independently.

Usage:
    python validate_files.py vrs                  # one store by name
    python validate_files.py jungle/sources.csv   # or by csv path
    python validate_files.py all                  # every store
    python validate_files.py jungle --check-urls  # also HEAD-probe http(s) URLs

Exit status is non-zero if any local/relative path is missing (or, with
--check-urls, any http(s) URL is unreachable).
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import urllib.request
from pathlib import Path

from fasta_naming import is_url, cache_name_for, resolve_fasta_token
from store_config import fasta_root as store_fasta_root

SCRIPT_DIR = Path(__file__).parent  # the stores/ directory
PEP_CONFIG = "project_config.yaml"


def downloads_dir(store_name: str) -> Path | None:
    base = os.environ.get("REFGETSTORE_BASE")
    if not base:
        return None
    return Path(base) / f".downloads_{store_name}"


def head_ok(url: str, timeout: int = 20) -> bool:
    """Probe an http(s) URL with a HEAD request. ftp:// is not probed."""
    if not url.startswith(("http://", "https://")):
        return True  # ftp: skip active probe; treated as unchecked
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 400
    except Exception:
        # Some servers reject HEAD; fall back to a 1-byte ranged GET.
        try:
            req = urllib.request.Request(url, headers={"Range": "bytes=0-0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return 200 <= resp.status < 400
        except Exception:
            return False


def resolve_local(token: str, fasta_root: str | None = None) -> Path:
    """Resolve a non-URL token to an absolute path the way build.py would.

    Absolute tokens are used as-is; relative tokens are joined onto the store's
    `fasta_root` (from project_config.yaml), falling back to the stores/ dir when
    no fasta_root is configured (legacy behaviour).
    """
    p = Path(token)
    if p.is_absolute():
        return p
    if fasta_root:
        return Path(resolve_fasta_token(token, fasta_root))
    return (SCRIPT_DIR / p).resolve()


def validate_store(store_name: str, csv_path: Path, check_urls: bool) -> dict:
    cache = downloads_dir(store_name)
    fasta_root = store_fasta_root(SCRIPT_DIR / store_name)
    with open(csv_path) as f:
        rows = list(csv.DictReader(f))

    missing = []       # (row_idx, token)
    unreachable = []   # (row_idx, url)
    local_found = 0
    url_cached = 0
    url_uncached = 0   # remote, not in cache, not (or cannot be) probed
    url_total = 0

    for i, row in enumerate(rows, start=1):
        field = (row.get("fasta") or "").strip()
        for token in field.split():
            if is_url(token):
                url_total += 1
                cache_name = cache_name_for(token, row)
                cached = cache is not None and (cache / cache_name).exists()
                if cached:
                    url_cached += 1
                    continue
                if check_urls:
                    if head_ok(token):
                        url_uncached += 1
                    else:
                        unreachable.append((i, token))
                else:
                    url_uncached += 1
            else:
                path = resolve_local(token, fasta_root)
                if path.exists():
                    local_found += 1
                else:
                    missing.append((i, token))

    return {
        "store": store_name,
        "rows": len(rows),
        "local_found": local_found,
        "missing": missing,
        "url_total": url_total,
        "url_cached": url_cached,
        "url_uncached": url_uncached,
        "unreachable": unreachable,
        "cache_dir": str(cache) if cache else None,
    }


def print_report(r: dict, check_urls: bool) -> bool:
    ok = not r["missing"] and not r["unreachable"]
    head = "PASSED" if ok else "FAILED"
    print(f"\n{head}: {r['store']}  ({r['rows']} rows)")
    print(f"  local files found:   {r['local_found']}")
    if r["url_total"]:
        line = f"  remote URLs:         {r['url_total']}  (cached: {r['url_cached']}"
        if check_urls:
            line += f", reachable: {r['url_uncached']}, unreachable: {len(r['unreachable'])}"
        else:
            line += f", uncached: {r['url_uncached']} — not probed"
        line += ")"
        print(line)
        if r["cache_dir"]:
            print(f"  cache dir:           {r['cache_dir']}")
        elif not check_urls:
            print("  (set REFGETSTORE_BASE to report download-cache status)")
    if r["missing"]:
        print(f"  MISSING local paths ({len(r['missing'])}):")
        for idx, tok in r["missing"][:25]:
            print(f"    row {idx}: {tok}")
        if len(r["missing"]) > 25:
            print(f"    ... and {len(r['missing']) - 25} more")
    if r["unreachable"]:
        print(f"  UNREACHABLE URLs ({len(r['unreachable'])}):")
        for idx, tok in r["unreachable"][:25]:
            print(f"    row {idx}: {tok}")
        if len(r["unreachable"]) > 25:
            print(f"    ... and {len(r['unreachable']) - 25} more")
    return ok


def resolve_target(target: str) -> list[tuple[str, Path]]:
    """Return list of (store_name, csv_path) for a target argument."""
    if target == "all":
        out = []
        for d in sorted(SCRIPT_DIR.iterdir()):
            if d.is_dir() and (d / "sources.csv").exists():
                out.append((d.name, d / "sources.csv"))
        return out
    p = Path(target)
    if p.suffix == ".csv":
        # csv path form, e.g. jungle/sources.csv
        store = p.parent.name if p.parent.name else p.stem
        return [(store, p)]
    # store-name form
    csv_path = SCRIPT_DIR / target / "sources.csv"
    return [(target, csv_path)]


def main():
    parser = argparse.ArgumentParser(description="Validate store FASTA sources exist.")
    parser.add_argument("target", help="Store name, sources.csv path, or 'all'")
    parser.add_argument("--check-urls", action="store_true",
                        help="HEAD-probe http(s) URLs that are not already cached")
    args = parser.parse_args()

    targets = resolve_target(args.target)
    if not targets:
        print("No stores found.", file=sys.stderr)
        sys.exit(1)

    all_ok = True
    for store_name, csv_path in targets:
        if not csv_path.exists():
            print(f"\nFAILED: {store_name}  (no sources.csv at {csv_path})")
            all_ok = False
            continue
        r = validate_store(store_name, csv_path, args.check_urls)
        all_ok = print_report(r, args.check_urls) and all_ok

    print()
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
