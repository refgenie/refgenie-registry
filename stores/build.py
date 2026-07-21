#!/usr/bin/env python3
"""Build a RefgetStore from a PEP project.

Each store folder is a PEP with a project_config.yaml pointing at
sources.csv. Store output paths are derived from env vars:

    REFGETSTORE_BASE/<store_name>   (local build path)
    REFGETSTORE_S3/<store_name>     (S3 sync target)

Usage:
    source ../infra/rivanna/env.sh
    python build.py jungle          # Build one store
    python build.py all             # Build all stores
    python build.py jungle --sync   # Build and sync to S3
    python build.py jungle --sync --delete   # ...propagating local removals

Note that this script is ADDITIVE ONLY -- it never removes a collection, so
deleting a row from sources.csv does not drop it from a built store. Use
remove_collections.py for that, then sync with --delete.

Requirements:
    pip install refget gtars peppy
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import peppy

from refget.store import RefgetStore

from fasta_naming import is_url, s3_to_https, cache_name_for, resolve_fasta_token
from store_config import fasta_root as store_fasta_root


SCRIPT_DIR = Path(__file__).parent
PEP_CONFIG = "project_config.yaml"
ALIAS_COLUMNS = {"name", "accession", "genome_assembly"}


def get_store_path(store_name: str) -> Path:
    base = os.environ.get("REFGETSTORE_BASE")
    if not base:
        print("REFGETSTORE_BASE not set. Source env.sh first.", file=sys.stderr)
        sys.exit(1)
    return Path(base) / store_name


def get_s3_path(store_name: str) -> str | None:
    base = os.environ.get("REFGETSTORE_S3")
    if not base:
        return None
    return f"{base.rstrip('/')}/{store_name}"


def download_fasta(url: str, dest_dir: Path, dest_name: str = None) -> str:
    # Many sources (e.g. iGenomes) name every file "genome.fa"; callers pass an
    # explicit dest_name to keep cached files unique.
    filename = dest_name or url.rstrip("/").split("/")[-1]
    fetch_url = s3_to_https(url) if url.startswith("s3://") else url
    dest = dest_dir / filename
    if dest.exists():
        return str(dest)
    print(f"  Downloading {filename}...")
    urllib.request.urlretrieve(fetch_url, dest)
    return str(dest)


def concat_fastas(paths: list[str], dest_dir: Path) -> str:
    """Concatenate multiple FASTA files into one combined file.

    gzip streams are concatenable, so a binary concatenation works for both
    gzipped (.fa.gz/.fna.gz) and plain-text FASTA. The combined filename is
    deterministic so re-runs are cached.
    """
    basenames = [Path(p).name for p in paths]
    # Deterministic combined name: join basenames, preserving the .gz suffix if any.
    is_gz = any(b.endswith(".gz") for b in basenames)
    stems = [b[:-3] if b.endswith(".gz") else b for b in basenames]
    combined_name = "combined_" + "_".join(stems)
    if is_gz and not combined_name.endswith(".gz"):
        combined_name += ".gz"
    dest = dest_dir / combined_name
    if dest.exists():
        return str(dest)
    print(f"  Concatenating {len(paths)} files -> {combined_name}")
    with open(dest, "wb") as out:
        for p in paths:
            with open(p, "rb") as src:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
    return str(dest)


# Cap on per-collection records embedded in the report to keep it small.
MAX_COLLECTION_RECORDS = 5000


def _tool_versions() -> dict:
    """Best-effort tool/version provenance; never raises."""
    versions = {}
    try:
        import gtars
        versions["gtars"] = getattr(gtars, "__version__", "unknown")
    except Exception:
        versions["gtars"] = "unknown"
    try:
        from importlib.metadata import version
        versions["refget"] = version("refget")
    except Exception:
        versions["refget"] = "unknown"
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=SCRIPT_DIR, capture_output=True, text=True, check=True,
        ).stdout.strip()
        versions["build_py_git_rev"] = rev or "unknown"
    except Exception:
        versions["build_py_git_rev"] = "unknown"
    return versions


def write_build_report(
    store_name: str,
    reports_dir: Path,
    sources_path: Path,
    started_at: datetime,
    elapsed: float,
    jobs: int,
    n_rows: int,
    loaded: int,
    skipped: int,
    failed: int,
    stats: dict,
    collections: list[dict],
) -> Path | None:
    """Write <store>_build_report.json into a LOCAL reports dir. Never crashes the build.

    The report is operator provenance (hostname, absolute build paths, tool
    versions, per-run counts) that nothing consumes. It must NOT live inside the
    store directory, because that directory is `aws s3 sync`'d to the PUBLIC
    bucket — build provenance has no reason to be world-readable. It lives in a
    local reports dir next to where builds happen instead.
    """
    ended_at = datetime.now(timezone.utc)
    report = {
        "store": store_name,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "duration_seconds": round(elapsed, 3),
        "jobs": jobs,
        "hostname": socket.gethostname(),
        "sources_csv": str(sources_path),
        "counts": {
            "source_rows": n_rows,
            "loaded": loaded,
            "skipped": skipped,
            "failed": failed,
            "n_collections": stats.get("n_collections") if isinstance(stats, dict) else None,
            "n_sequences": stats.get("n_sequences") if isinstance(stats, dict) else None,
        },
        "tool_versions": _tool_versions(),
    }
    if len(collections) <= MAX_COLLECTION_RECORDS:
        report["collections"] = collections
    else:
        report["collections_omitted"] = len(collections)

    report_path = reports_dir / f"{store_name}_build_report.json"
    try:
        reports_dir.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w") as fh:
            json.dump(report, fh, indent=2)
        return report_path
    except Exception as e:
        print(f"  WARNING: failed to write build report: {e}", file=sys.stderr)
        return None


def build_store(
    store_name: str,
    store_dir: Path,
    sync: bool = False,
    jobs: int = 8,
    delete: bool = False,
):
    start_time = time.monotonic()
    started_at = datetime.now(timezone.utc)
    config_path = store_dir / PEP_CONFIG
    if not config_path.exists():
        print(f"No {PEP_CONFIG} in {store_dir}", file=sys.stderr)
        return False

    project = peppy.Project(str(config_path))
    store_path = get_store_path(store_name)
    s3_path = get_s3_path(store_name)
    samples = project.samples

    # Relative fasta tokens in sources.csv resolve against the store's fasta_root
    # (from project_config.yaml). Absolute tokens / URLs are unaffected.
    fasta_root = store_fasta_root(store_dir)

    # The PEP's sample_table (sources.csv) is the real source of record.
    sources_csv = store_dir / "sources.csv"
    sources_path = sources_csv if sources_csv.exists() else config_path

    print(f"Building {store_name}: {len(samples)} FASTAs -> {store_path} (jobs={jobs})")

    store_path.parent.mkdir(parents=True, exist_ok=True)
    store = RefgetStore.on_disk(str(store_path))

    download_dir = store_path.parent / f".downloads_{store_name}"
    failures = 0

    # --- Phase 1: resolve each row to ONE local FASTA path ---
    # Download URLs (cached) and concatenate space-separated multi-FASTA rows.
    # Each row becomes exactly one collection = one file.
    resolved_rows = []  # list[(row, label, path)]
    for i, sample in enumerate(samples):
        row = sample.to_dict()
        tokens = row["fasta"].strip().split()
        name = row.get("name", "").strip()
        label = name or tokens[-1].split("/")[-1]

        files = []
        failed = False
        for token in tokens:
            if is_url(token):
                download_dir.mkdir(parents=True, exist_ok=True)
                try:
                    files.append(download_fasta(token, download_dir, cache_name_for(token, row)))
                except Exception as e:
                    print(f"  [{i+1}/{len(samples)}] DOWNLOAD FAILED {label}: {e}", file=sys.stderr)
                    failed = True
                    break
            else:
                files.append(resolve_fasta_token(token, fasta_root))
        if failed:
            failures += 1
            continue

        if len(files) == 1:
            path = files[0]
        else:
            download_dir.mkdir(parents=True, exist_ok=True)
            path = concat_fastas(files, download_dir)

        if not Path(path).exists():
            print(f"  [{i+1}/{len(samples)}] FILE NOT FOUND {path}", file=sys.stderr)
            failures += 1
            continue
        resolved_rows.append((row, label, path))

    # --- Phase 2: parallel multi-FASTA ingest (concurrency happens in Rust) ---
    # Deduplicate paths preserving first-occurrence order so the returned
    # results align 1:1 with the unique inputs.
    unique_paths = list(dict.fromkeys(p for _, _, p in resolved_rows))
    successes = 0
    loaded = 0
    skipped = 0
    collection_records = []  # list[{digest, n_sequences, label, was_new}]
    if unique_paths:
        print(f"  Ingesting {len(unique_paths)} FASTAs with {jobs} parallel worker(s)...")
        results = store.add_sequence_collections_from_fastas(unique_paths, jobs=jobs)
        by_path = dict(zip(unique_paths, results))

        # --- Phase 3: register aliases per row from the resolved metadata ---
        for row, label, path in resolved_rows:
            metadata, was_new = by_path[path]
            status = "new" if was_new else "exists"
            print(f"  {status:6s} {metadata.digest[:12]}... {label}")
            successes += 1
            if was_new:
                loaded += 1
            else:
                skipped += 1
            collection_records.append({
                "digest": metadata.digest,
                "n_sequences": getattr(metadata, "n_sequences", None),
                "label": label,
                "was_new": was_new,
            })
            for col in ALIAS_COLUMNS:
                val = row.get(col, "").strip()
                if val:
                    store.add_collection_alias(col, val, metadata.digest)

    stats = store.stats()
    print(f"  Done: {successes} loaded, {failures} failed, {stats}")

    # --- Write a machine-readable build report to a LOCAL reports dir ---
    # NOT into the store dir: the store dir is aws s3 sync'd to the public bucket
    # (see below), and the report is operator provenance nothing consumes. Home:
    # $REFGENIE_BUILD_REPORTS_DIR, else a `_build_reports` sibling of the store
    # dirs (outside the per-store sync path, so it never reaches S3).
    elapsed = time.monotonic() - start_time
    reports_dir_env = os.environ.get("REFGENIE_BUILD_REPORTS_DIR")
    reports_dir = (
        Path(reports_dir_env) if reports_dir_env else store_path.parent / "_build_reports"
    )
    report_path = write_build_report(
        store_name=store_name,
        reports_dir=reports_dir,
        sources_path=sources_path,
        started_at=started_at,
        elapsed=elapsed,
        jobs=jobs,
        n_rows=len(samples),
        loaded=loaded,
        skipped=skipped,
        failed=failures,
        stats=stats if isinstance(stats, dict) else {},
        collections=collection_records,
    )
    if report_path:
        mins, secs = divmod(int(elapsed), 60)
        print(f"  Build report -> {report_path} (duration {mins}m{secs}s)")

    if sync and s3_path:
        # --delete is opt-in: a plain sync is additive, so anything removed from
        # the local store (see remove_collections.py) would linger in the public
        # bucket forever. Pass --delete when the local store has SHRUNK, and only
        # after verifying it locally — until the sync runs, S3 still holds the
        # last good copy and is the rollback.
        cmd = ["aws", "s3", "sync", str(store_path), s3_path]
        if delete:
            cmd.append("--delete")
        print(f"  Syncing to {s3_path}{' (--delete)' if delete else ''}...")
        subprocess.run(cmd, check=True)
        print("  S3 sync complete.")
    elif sync and not s3_path:
        print(f"  REFGETSTORE_S3 not set, skipping sync.", file=sys.stderr)

    return failures == 0


def get_store_dirs() -> list[Path]:
    return sorted(
        d for d in SCRIPT_DIR.iterdir()
        if d.is_dir() and (d / PEP_CONFIG).exists()
    )


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Build RefgetStores from PEP projects.")
    parser.add_argument("store", help="Store name or 'all'")
    parser.add_argument("--sync", action="store_true", help="Sync to S3 after building")
    parser.add_argument(
        "--delete", action="store_true",
        help="Pass --delete to the S3 sync, so objects removed from the local store "
             "(see remove_collections.py) are also removed from the bucket. Requires "
             "--sync. Only use after verifying the local store: until this runs, S3 "
             "holds the last good copy.",
    )
    parser.add_argument(
        "--jobs", "-j", type=int,
        default=int(os.environ.get("SLURM_CPUS_PER_TASK") or 8),
        help="Parallel FASTA-ingest workers (default: $SLURM_CPUS_PER_TASK or 8; 1=serial, 0=auto)",
    )
    args = parser.parse_args()

    if args.delete and not args.sync:
        parser.error("--delete has no effect without --sync")

    if args.store == "all":
        store_dirs = get_store_dirs()
        if not store_dirs:
            print(f"No stores with {PEP_CONFIG} found.", file=sys.stderr)
            sys.exit(1)
        print(f"Building {len(store_dirs)} stores...\n")
        for store_dir in store_dirs:
            build_store(
                store_dir.name, store_dir,
                sync=args.sync, jobs=args.jobs, delete=args.delete,
            )
            print()
    else:
        store_dir = SCRIPT_DIR / args.store
        if not store_dir.exists():
            print(f"Store not found: {store_dir}", file=sys.stderr)
            sys.exit(1)
        build_store(
            args.store, store_dir,
            sync=args.sync, jobs=args.jobs, delete=args.delete,
        )


if __name__ == "__main__":
    main()
