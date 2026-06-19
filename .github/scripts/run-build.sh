#!/usr/bin/env bash
# Build runner for refgenie-registry assets.
#
# refgenie IS the build system. The registry stores refgenie-native recipes and
# asset classes; refgenie loads them directly (tools/import_recipes.py), then
# `refgenie generate snakefile` renders a Snakemake workflow whose rules build
# each asset via `refgenie1 build {genome}/{asset}:{tag}` inside the recipe's
# container. There is NO conda/mamba/bioconda tool-install path here -- recipes
# are not built with conda.
#
# Subcommands:
#   load      Load the registry (asset classes + native recipes) into a
#             refgenie DB and assert it loads cleanly.
#   snakefile Load the registry and generate the build Snakefile, asserting it
#             is non-empty and contains build rules.
#   download  Fetch genome FASTA, verify checksum.
#   upload    Upload build artifacts to cloud storage.
#
# Usage:
#   run-build.sh load      [--registry-root <dir>]
#   run-build.sh snakefile [--registry-root <dir>] [--output <path>]
#   run-build.sh download  --genome <path> --workdir <dir>
#   run-build.sh upload    --workdir <dir> --genome-name <name> --recipe <name> \
#                          --provider <s3|r2> --bucket <bucket> --endpoint <url>

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log() { echo "==> $*"; }
err() { echo "ERROR: $*" >&2; exit 1; }

parse_yaml_field() {
    # Simple YAML field reader (top-level / dotted string fields only)
    python3 -c "
import yaml, sys
with open('$1') as f:
    d = yaml.safe_load(f)
keys = '$2'.split('.')
val = d
for k in keys:
    if isinstance(val, dict):
        val = val.get(k)
    else:
        val = None
        break
print(val if val is not None else '')
"
}

# ---------------------------------------------------------------------------
# Subcommand: load
#
# Load the entire registry (asset classes + refgenie-native recipes) into an
# in-memory refgenie DB. Proves the registry is consumable by the real builder.
# ---------------------------------------------------------------------------

cmd_load() {
    local registry_root="$REPO_ROOT"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --registry-root) registry_root="$2"; shift 2 ;;
            *) err "load: unknown option $1" ;;
        esac
    done

    log "Loading registry from $registry_root into refgenie"
    python3 "$registry_root/tools/import_recipes.py" --registry-root "$registry_root"
    log "Registry loaded."
}

# ---------------------------------------------------------------------------
# Subcommand: snakefile
#
# Load the registry and render the build Snakefile via refgenie's real build
# entrypoint (populate_snakefile_template / `refgenie generate snakefile`).
# ---------------------------------------------------------------------------

cmd_snakefile() {
    local registry_root="$REPO_ROOT"
    local output="/tmp/Snakefile"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --registry-root) registry_root="$2"; shift 2 ;;
            --output)        output="$2"; shift 2 ;;
            *) err "snakefile: unknown option $1" ;;
        esac
    done

    log "Loading registry and generating Snakefile -> $output"
    python3 "$registry_root/tools/import_recipes.py" \
        --registry-root "$registry_root" \
        --snakefile "$output"

    [[ -s "$output" ]] || err "snakefile: generated Snakefile is empty"
    grep -q "^rule build_" "$output" || err "snakefile: no 'rule build_*' rules generated"
    grep -q "refgenie1 build" "$output" || err "snakefile: rules do not invoke 'refgenie1 build'"

    local rule_count
    rule_count=$(grep -c "^rule build_" "$output")
    log "Snakefile OK: $rule_count build rules, invokes 'refgenie1 build'."
}

# ---------------------------------------------------------------------------
# Subcommand: download
# ---------------------------------------------------------------------------

cmd_download() {
    local genome_path="" workdir=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --genome)  genome_path="$2"; shift 2 ;;
            --workdir) workdir="$2"; shift 2 ;;
            *) err "download: unknown option $1" ;;
        esac
    done
    [[ -n "$genome_path" ]] || err "download: --genome required"
    [[ -n "$workdir" ]]     || err "download: --workdir required"
    [[ -f "$genome_path" ]] || err "download: genome not found: $genome_path"

    mkdir -p "$workdir/fasta"

    local genome_name
    genome_name=$(parse_yaml_field "$genome_path" "name")
    local primary_url
    primary_url=$(parse_yaml_field "$genome_path" "fasta.primary_url")

    [[ -n "$primary_url" ]] || err "download: no fasta.primary_url in $genome_path"

    log "Downloading FASTA for $genome_name from $primary_url"
    local fasta_gz="$workdir/fasta/${genome_name}.fa.gz"
    local fasta="$workdir/fasta/${genome_name}.fa"

    if curl -fSL -o "$fasta_gz" "$primary_url"; then
        log "Decompressing FASTA..."
        gunzip -f "$fasta_gz"
    else
        # Try mirrors
        log "Primary URL failed, trying mirrors..."
        local mirrors
        mirrors=$(python3 -c "
import yaml
with open('$genome_path') as f:
    d = yaml.safe_load(f)
for m in d.get('fasta', {}).get('mirrors', []):
    print(m)
")
        local success=false
        while IFS= read -r mirror; do
            [[ -n "$mirror" ]] || continue
            log "Trying mirror: $mirror"
            if curl -fSL -o "$fasta_gz" "$mirror"; then
                gunzip -f "$fasta_gz"
                success=true
                break
            fi
        done <<< "$mirrors"
        [[ "$success" == "true" ]] || err "download: all download URLs failed"
    fi

    # Verify the FASTA file looks valid
    [[ -f "$fasta" ]] || err "download: FASTA file not found after download"
    head -1 "$fasta" | grep -q "^>" || err "download: FASTA file does not start with >"

    local checksum_expected
    checksum_expected=$(parse_yaml_field "$genome_path" "fasta.checksum.sha256")
    if [[ -n "$checksum_expected" && "$checksum_expected" != "compute_on_registration" ]]; then
        log "Verifying SHA-256 checksum..."
        local actual_sha
        actual_sha=$(sha256sum "$fasta" | cut -d' ' -f1)
        if [[ "$actual_sha" != "$checksum_expected" ]]; then
            err "download: checksum mismatch (expected $checksum_expected, got $actual_sha)"
        fi
        log "Checksum verified."
    fi

    log "FASTA downloaded: $fasta"
    echo "$fasta"
}

# ---------------------------------------------------------------------------
# Subcommand: upload
# ---------------------------------------------------------------------------

cmd_upload() {
    local workdir="" genome_name="" recipe="" provider="" bucket="" endpoint=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --workdir)      workdir="$2"; shift 2 ;;
            --genome-name)  genome_name="$2"; shift 2 ;;
            --recipe)       recipe="$2"; shift 2 ;;
            --provider)     provider="$2"; shift 2 ;;
            --bucket)       bucket="$2"; shift 2 ;;
            --endpoint)     endpoint="$2"; shift 2 ;;
            *) err "upload: unknown option $1" ;;
        esac
    done
    [[ -n "$workdir" ]]     || err "upload: --workdir required"
    [[ -n "$genome_name" ]] || err "upload: --genome-name required"
    [[ -n "$recipe" ]]      || err "upload: --recipe required"
    [[ -n "$provider" ]]    || err "upload: --provider required"
    [[ -n "$bucket" ]]      || err "upload: --bucket required"

    local source_dir="$workdir/output/${genome_name}/${recipe}"
    local s3_path="s3://${bucket}/${genome_name}/${recipe}/"

    log "Uploading $source_dir to $s3_path"

    local aws_args=("s3" "cp" "--recursive" "$source_dir" "$s3_path")
    if [[ -n "$endpoint" ]]; then
        aws_args=("--endpoint-url" "$endpoint" "${aws_args[@]}")
    fi

    aws "${aws_args[@]}"

    log "Upload complete."
}

# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

SUBCOMMAND="${1:-}"
shift || true

case "$SUBCOMMAND" in
    load)      cmd_load "$@" ;;
    snakefile) cmd_snakefile "$@" ;;
    download)  cmd_download "$@" ;;
    upload)    cmd_upload "$@" ;;
    *)
        echo "Usage: $0 <load|snakefile|download|upload> [options]"
        echo ""
        echo "Subcommands:"
        echo "  load      Load the registry (asset classes + native recipes) into refgenie"
        echo "  snakefile Load the registry and generate the build Snakefile"
        echo "  download  Fetch genome FASTA, verify checksum"
        echo "  upload    Upload build artifacts to cloud storage"
        exit 1
        ;;
esac
