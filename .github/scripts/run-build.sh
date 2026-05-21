#!/usr/bin/env bash
# Build runner for refgenie-registry assets.
#
# Subcommands:
#   setup     Install tools declared in a recipe
#   download  Fetch genome FASTA, verify checksum
#   init      Initialize refgenie and register genome
#   execute   Register recipe and run the build
#   validate  Check output patterns exist
#   upload    Upload build artifacts to cloud storage
#
# Usage:
#   run-build.sh setup    --recipe-path <path>
#   run-build.sh download --genome <path> --workdir <dir>
#   run-build.sh init     --genome <path> --workdir <dir>
#   run-build.sh execute  --genome <path> --recipe-path <path> --workdir <dir>
#   run-build.sh validate --recipe-path <path> --workdir <dir> --genome-name <name>
#   run-build.sh upload   --workdir <dir> --genome-name <name> --recipe <name> \
#                         --provider <s3|r2> --bucket <bucket> --endpoint <url>

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log() { echo "==> $*"; }
err() { echo "ERROR: $*" >&2; exit 1; }

parse_yaml_field() {
    # Simple YAML field reader (top-level string fields only)
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
# Subcommand: setup
# ---------------------------------------------------------------------------

cmd_setup() {
    local recipe_path=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --recipe-path) recipe_path="$2"; shift 2 ;;
            *) err "setup: unknown option $1" ;;
        esac
    done
    [[ -n "$recipe_path" ]] || err "setup: --recipe-path required"
    [[ -f "$recipe_path" ]] || err "setup: recipe not found: $recipe_path"

    log "Installing tools from $recipe_path"

    python3 - "$recipe_path" <<'PYEOF'
import yaml, subprocess, sys

with open(sys.argv[1]) as f:
    recipe = yaml.safe_load(f)

tools = recipe.get("requires", {}).get("tools", [])
if not tools:
    print("No tools to install.")
    sys.exit(0)

for tool in tools:
    name = tool["name"]
    source = tool.get("source", "bioconda")
    version = tool.get("version", "")

    if source in ("bioconda", "conda-forge"):
        pkg = name
        # Strip version operator for conda install (conda handles constraints differently)
        cmd = ["mamba", "install", "-y", "-c", source, pkg]
        print(f"Installing {name} from {source}...")
        result = subprocess.run(cmd, capture_output=False)
        if result.returncode != 0:
            # Fallback to conda
            cmd[0] = "conda"
            result = subprocess.run(cmd, capture_output=False)
            if result.returncode != 0:
                print(f"WARNING: Failed to install {name} from {source}")
    elif source == "pip":
        cmd = ["pip", "install", name]
        print(f"Installing {name} via pip...")
        subprocess.run(cmd, check=True)
    elif source == "apt":
        cmd = ["apt-get", "install", "-y", name]
        print(f"Installing {name} via apt...")
        subprocess.run(cmd, check=True)
    else:
        print(f"WARNING: Unknown source '{source}' for tool '{name}', skipping.")
PYEOF
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
# Subcommand: init
# ---------------------------------------------------------------------------

cmd_init() {
    local genome_path="" workdir=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --genome)  genome_path="$2"; shift 2 ;;
            --workdir) workdir="$2"; shift 2 ;;
            *) err "init: unknown option $1" ;;
        esac
    done
    [[ -n "$genome_path" ]] || err "init: --genome required"
    [[ -n "$workdir" ]]     || err "init: --workdir required"

    local genome_name
    genome_name=$(parse_yaml_field "$genome_path" "name")

    log "Initializing refgenie in $workdir"
    mkdir -p "$workdir/output"

    # Initialize refgenie config
    export REFGENIE="$workdir/refgenie_config.yaml"
    if command -v refgenie &>/dev/null; then
        refgenie init -c "$REFGENIE"
    else
        # Create minimal config manually
        cat > "$REFGENIE" <<EOF
config_version: 0.4
genome_folder: $workdir/output
genomes: {}
EOF
    fi

    log "Refgenie initialized with config at $REFGENIE"
}

# ---------------------------------------------------------------------------
# Subcommand: execute
# ---------------------------------------------------------------------------

cmd_execute() {
    local genome_path="" recipe_path="" workdir=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --genome)      genome_path="$2"; shift 2 ;;
            --recipe-path) recipe_path="$2"; shift 2 ;;
            --workdir)     workdir="$2"; shift 2 ;;
            *) err "execute: unknown option $1" ;;
        esac
    done
    [[ -n "$genome_path" ]]  || err "execute: --genome required"
    [[ -n "$recipe_path" ]]  || err "execute: --recipe-path required"
    [[ -n "$workdir" ]]      || err "execute: --workdir required"

    local genome_name recipe_name
    genome_name=$(parse_yaml_field "$genome_path" "name")
    recipe_name=$(parse_yaml_field "$recipe_path" "name")

    local fasta="$workdir/fasta/${genome_name}.fa"
    local output_dir="$workdir/output/${genome_name}/${recipe_name}"
    mkdir -p "$output_dir"

    log "Building $recipe_name for $genome_name"

    # Run setup commands if defined
    local setup_cmd
    setup_cmd=$(parse_yaml_field "$recipe_path" "build.setup")
    if [[ -n "$setup_cmd" ]]; then
        log "Running setup..."
        bash -c "$setup_cmd"
    fi

    # Build the command with variable substitution
    local build_cmd
    build_cmd=$(python3 -c "
import yaml
with open('$recipe_path') as f:
    d = yaml.safe_load(f)
cmd = d.get('build', {}).get('command', '')
cmd = cmd.replace('{fasta}', '$fasta')
cmd = cmd.replace('{output_dir}', '$output_dir')
cmd = cmd.replace('{genome}', '$genome_name')
cmd = cmd.replace('{threads}', '${THREADS:-2}')
# Handle {fasta_url} for the fasta recipe
import yaml as y2
with open('$genome_path') as f2:
    gd = y2.safe_load(f2)
fasta_url = gd.get('fasta', {}).get('primary_url', '')
cmd = cmd.replace('{fasta_url}', fasta_url)
print(cmd)
")

    log "Executing build command..."
    echo "$build_cmd"
    bash -c "$build_cmd"

    log "Build completed: $output_dir"
}

# ---------------------------------------------------------------------------
# Subcommand: validate
# ---------------------------------------------------------------------------

cmd_validate() {
    local recipe_path="" workdir="" genome_name=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --recipe-path) recipe_path="$2"; shift 2 ;;
            --workdir)     workdir="$2"; shift 2 ;;
            --genome-name) genome_name="$2"; shift 2 ;;
            *) err "validate: unknown option $1" ;;
        esac
    done
    [[ -n "$recipe_path" ]] || err "validate: --recipe-path required"
    [[ -n "$workdir" ]]     || err "validate: --workdir required"
    [[ -n "$genome_name" ]] || err "validate: --genome-name required"

    local recipe_name
    recipe_name=$(parse_yaml_field "$recipe_path" "name")
    local output_dir="$workdir/output/${genome_name}/${recipe_name}"

    log "Validating outputs in $output_dir"

    # Check output patterns
    local patterns
    patterns=$(python3 -c "
import yaml
with open('$recipe_path') as f:
    d = yaml.safe_load(f)
for o in d.get('outputs', []):
    print(o.get('pattern', ''))
")

    local missing=0
    while IFS= read -r pattern; do
        [[ -n "$pattern" ]] || continue
        local count
        count=$(find "$output_dir" -name "$pattern" 2>/dev/null | wc -l)
        if [[ "$count" -eq 0 ]]; then
            echo "MISSING: No files matching pattern: $pattern"
            missing=1
        else
            echo "OK: Found $count file(s) matching $pattern"
        fi
    done <<< "$patterns"

    if [[ "$missing" -eq 1 ]]; then
        err "validate: missing output files"
    fi

    # Run test commands if defined
    local test_cmds
    test_cmds=$(python3 -c "
import yaml
with open('$recipe_path') as f:
    d = yaml.safe_load(f)
for cmd in d.get('test', {}).get('commands', []):
    cmd = cmd.replace('{output_dir}', '$output_dir')
    cmd = cmd.replace('{genome}', '$genome_name')
    print(cmd)
" 2>/dev/null) || true

    if [[ -n "$test_cmds" ]]; then
        log "Running validation tests..."
        while IFS= read -r tcmd; do
            [[ -n "$tcmd" ]] || continue
            if ! bash -c "$tcmd" 2>&1; then
                err "validate: test failed: $tcmd"
            fi
        done <<< "$test_cmds"
    fi

    log "Validation passed."
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
    setup)    cmd_setup "$@" ;;
    download) cmd_download "$@" ;;
    init)     cmd_init "$@" ;;
    execute)  cmd_execute "$@" ;;
    validate) cmd_validate "$@" ;;
    upload)   cmd_upload "$@" ;;
    *)
        echo "Usage: $0 <setup|download|init|execute|validate|upload> [options]"
        echo ""
        echo "Subcommands:"
        echo "  setup     Install tools declared in a recipe"
        echo "  download  Fetch genome FASTA, verify checksum"
        echo "  init      Initialize refgenie and register genome"
        echo "  execute   Register recipe and run the build"
        echo "  validate  Check output patterns exist"
        echo "  upload    Upload build artifacts to cloud storage"
        exit 1
        ;;
esac
