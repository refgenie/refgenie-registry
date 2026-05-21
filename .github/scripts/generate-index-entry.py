#!/usr/bin/env python3
"""Generate an index entry YAML file for a completed build.

Creates a YAML file under index/<genome>/<recipe>.yaml with build metadata,
storage information, file listings, and access URLs. Also updates the
index/manifest.yaml file.
"""

import argparse
import hashlib
import os
import sys
from datetime import datetime, timezone

import yaml


def parse_args():
    parser = argparse.ArgumentParser(description="Generate index entry for a build")
    parser.add_argument("--genome", required=True, help="Genome name")
    parser.add_argument("--recipe", required=True, help="Recipe name")
    parser.add_argument("--recipe-path", required=True, help="Path to recipe YAML")
    parser.add_argument("--workdir", required=True, help="Build working directory")
    parser.add_argument(
        "--storage-provider", default="s3", help="Storage provider (s3, r2)"
    )
    parser.add_argument("--storage-bucket", required=True, help="Storage bucket name")
    parser.add_argument(
        "--storage-endpoint", default="", help="Storage endpoint URL (for R2/MinIO)"
    )
    parser.add_argument(
        "--output", default="", help="Output path (default: index/<genome>/<recipe>.yaml)"
    )
    return parser.parse_args()


def file_sha256(path):
    """Compute SHA-256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def get_file_list(output_dir):
    """Walk output directory and collect file info."""
    files = []
    if not os.path.isdir(output_dir):
        return files
    for root, dirs, filenames in os.walk(output_dir):
        for fname in sorted(filenames):
            fpath = os.path.join(root, fname)
            rel_path = os.path.relpath(fpath, output_dir)
            size = os.path.getsize(fpath)
            checksum = file_sha256(fpath)
            files.append(
                {
                    "name": rel_path,
                    "size": size,
                    "checksum": f"sha256:{checksum}",
                }
            )
    return files


def build_access_urls(genome, recipe, bucket, endpoint, provider):
    """Generate access URLs for the built asset."""
    s3_path = f"s3://{bucket}/{genome}/{recipe}/"
    urls = {"s3": s3_path}

    if provider == "r2" and endpoint:
        # Cloudflare R2 public URL
        # Typically: https://<account>.r2.cloudflarestorage.com/<bucket>/<path>
        http_url = f"{endpoint.rstrip('/')}/{bucket}/{genome}/{recipe}/"
        urls["http"] = http_url
    elif provider == "s3":
        http_url = f"https://{bucket}.s3.amazonaws.com/{genome}/{recipe}/"
        urls["http"] = http_url

    return urls


def update_manifest(index_dir, genome, recipe, entry):
    """Update the manifest.yaml with this entry."""
    manifest_path = os.path.join(index_dir, "manifest.yaml")

    if os.path.isfile(manifest_path):
        with open(manifest_path) as f:
            manifest = yaml.safe_load(f) or {}
    else:
        manifest = {}

    if "assets" not in manifest:
        manifest["assets"] = {}

    genome_assets = manifest["assets"].get(genome, {})
    genome_assets[recipe] = {
        "status": entry.get("build", {}).get("status", "success"),
        "recipe_version": entry.get("recipe_version", ""),
        "built": entry.get("build", {}).get("timestamp", ""),
        "files": len(entry.get("files", [])),
    }
    manifest["assets"][genome] = genome_assets

    # Update totals
    total = sum(
        len(recipes) for recipes in manifest["assets"].values()
    )
    manifest["total_assets"] = total
    manifest["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with open(manifest_path, "w") as f:
        yaml.dump(manifest, f, default_flow_style=False, sort_keys=False)

    print(f"Updated manifest: {manifest_path} (total_assets: {total})")


def main():
    args = parse_args()

    # Read recipe metadata
    with open(args.recipe_path) as f:
        recipe_data = yaml.safe_load(f)

    recipe_version = recipe_data.get("version", "unknown")
    recipe_sha = file_sha256(args.recipe_path)

    # Collect output files
    output_dir = os.path.join(
        args.workdir, "output", args.genome, args.recipe
    )
    files = get_file_list(output_dir)

    # Build access URLs
    access_urls = build_access_urls(
        args.genome,
        args.recipe,
        args.storage_bucket,
        args.storage_endpoint,
        args.storage_provider,
    )

    # Build the index entry
    entry = {
        "genome": args.genome,
        "asset": args.recipe,
        "recipe_version": recipe_version,
        "recipe_sha": recipe_sha,
        "build": {
            "status": "success",
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "builder": "github-actions",
            "run_id": os.environ.get("GITHUB_RUN_ID", "local"),
        },
        "storage": {
            "provider": args.storage_provider,
            "bucket": args.storage_bucket,
            "path": f"{args.genome}/{args.recipe}/",
        },
        "files": files,
        "access": access_urls,
    }

    if args.storage_endpoint:
        entry["storage"]["endpoint"] = args.storage_endpoint

    # Determine output path
    if args.output:
        output_path = args.output
    else:
        output_path = os.path.join("index", args.genome, f"{args.recipe}.yaml")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w") as f:
        yaml.dump(entry, f, default_flow_style=False, sort_keys=False)

    print(f"Generated index entry: {output_path}")

    # Update manifest
    index_dir = os.path.dirname(os.path.dirname(output_path))
    if os.path.basename(index_dir) != "index":
        index_dir = "index"
    update_manifest(index_dir, args.genome, args.recipe, entry)


if __name__ == "__main__":
    main()
