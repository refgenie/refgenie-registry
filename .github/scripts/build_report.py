#!/usr/bin/env python3
"""Run all PR validation checks and emit an artifact for pr-report.yml.

This runs in the UNTRUSTED pull_request context (the fork's code is checked
out), so it must never see secrets and never writes to the PR. It only runs
the validators, collects AI-review input as plain text, and writes:

    <output-dir>/report.json    - machine-readable check results
    <output-dir>/ai_context/    - text inputs for the AI review

pr-report.yml (workflow_run, trusted base context) downloads the artifact,
treats every string in it as untrusted, and does all PR writes.

Checks (invocations identical to the old lint-genomes / lint-recipes /
build-test workflows):
    genome-validation   tools/validate_genome.py --check-fhr
    asset-classes       jsonschema check of asset_classes/*.yaml
    recipe-cross-check  tools/validate_recipe.py --no-url-check recipes/*/recipe.yaml
    recipe-validation   tools/validate_recipe.py <changed recipes>
    build-test          .github/scripts/run-build.sh snakefile
"""

import argparse
import glob
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Cap on the AI-review file-contents blob (bounds prompt-injection blast radius)
MAX_CONTENT_BYTES = 50000
# Cap on per-check output stored in report.json (display only; pr-report
# sanitizes and truncates further before rendering)
MAX_CHECK_OUTPUT = 8000

GENOME_META_PATHS = ("schema/genome.schema.yaml", "schema/fhr.schema.json")


def run(cmd, **kwargs):
    """Run a command, capturing combined stdout+stderr."""
    proc = subprocess.run(
        cmd, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, **kwargs,
    )
    return proc.returncode, proc.stdout


def git_lines(*args):
    code, out = run(["git", *args])
    if code != 0:
        print(f"git {' '.join(args)} failed:\n{out}", file=sys.stderr)
        sys.exit(2)
    return [line for line in out.splitlines() if line.strip()]


def cap(text, limit=MAX_CHECK_OUTPUT):
    if len(text) > limit:
        return text[:limit] + f"\n[TRUNCATED at {limit} chars]"
    return text


def check_asset_classes():
    """Validate asset_classes/*.yaml against schema/asset_class.schema.yaml."""
    import yaml
    from jsonschema import Draft202012Validator

    schema_path = REPO_ROOT / "schema" / "asset_class.schema.yaml"
    if not schema_path.exists():
        return 1, f"ERROR: {schema_path} not found"
    with open(schema_path) as f:
        schema = yaml.safe_load(f)
    validator = Draft202012Validator(schema)

    files = sorted(glob.glob(str(REPO_ROOT / "asset_classes" / "*.yaml")))
    if not files:
        return 0, "No asset_classes/*.yaml files found."

    lines, failed = [], False
    for path in files:
        with open(path) as f:
            data = yaml.safe_load(f)
        rel = str(Path(path).relative_to(REPO_ROOT))
        errors = [e.message for e in validator.iter_errors(data)]
        if errors:
            failed = True
            lines.append(f"FAIL {rel}")
            lines.extend(f"  - {e}" for e in errors)
        else:
            lines.append(f"PASS {rel}")
    return (1 if failed else 0), "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--output-dir", default="pr-report-artifact", type=Path)
    args = parser.parse_args()

    out_dir = args.output_dir
    ctx_dir = out_dir / "ai_context"
    ctx_dir.mkdir(parents=True, exist_ok=True)

    diff_range = f"{args.base_sha}...{args.head_sha}"
    changed = git_lines("diff", "--name-only", "--diff-filter=ACMR", diff_range)
    print(f"Changed files ({len(changed)}):")
    for f in changed:
        print(f"  {f}")

    changed_genomes = [f for f in changed if f.startswith("genomes/") and f.endswith((".yaml", ".yml"))]
    changed_recipes = [f for f in changed if f.startswith("recipes/") and f.endswith("recipe.yaml")]
    genome_meta_changed = any(
        f in GENOME_META_PATHS or f.startswith("tools/") for f in changed
    )
    recipe_paths_changed = any(
        f.startswith(("recipes/", "asset_classes/"))
        or f in ("schema/recipe.schema.yaml", "schema/asset_class.schema.yaml")
        for f in changed
    )
    build_relevant = any(
        f.startswith(("recipes/", "asset_classes/")) or f == "tools/import_recipes.py"
        for f in changed
    )

    checks = []

    def record(name, status, summary, output=""):
        checks.append({
            "name": name, "status": status,
            "summary": summary, "output": cap(output),
        })
        print(f"[{status.upper()}] {name}: {summary}")

    # --- genome validation ------------------------------------------------
    # A schema/tooling change re-lints the whole corpus (a validator change
    # can affect every file); otherwise only the genome files in this PR.
    if genome_meta_changed:
        genome_files = git_lines("ls-files", "genomes/**/*.yaml")
    else:
        genome_files = changed_genomes
    if genome_files:
        code, out = run([sys.executable, "tools/validate_genome.py", "--check-fhr", *genome_files])
        record(
            "genome-validation",
            "pass" if code == 0 else "fail",
            f"{len(genome_files)} genome file(s) validated"
            if code == 0 else "genome validation failed",
            out,
        )
    else:
        record("genome-validation", "skipped", "no genome files changed")

    # --- asset classes + recipe cross-check -------------------------------
    if recipe_paths_changed:
        code, out = check_asset_classes()
        record(
            "asset-classes",
            "pass" if code == 0 else "fail",
            "asset class definitions valid" if code == 0 else "asset class validation failed",
            out,
        )

        all_recipes = sorted(glob.glob(str(REPO_ROOT / "recipes" / "*" / "recipe.yaml")))
        all_recipes = [str(Path(p).relative_to(REPO_ROOT)) for p in all_recipes]
        code, out = run([sys.executable, "tools/validate_recipe.py", "--no-url-check", *all_recipes])
        record(
            "recipe-cross-check",
            "pass" if code == 0 else "fail",
            "all recipes validate against schema and asset classes"
            if code == 0 else "recipe cross-check failed",
            out,
        )
    else:
        record("asset-classes", "skipped", "no recipe/asset-class files changed")
        record("recipe-cross-check", "skipped", "no recipe/asset-class files changed")

    # --- changed-recipe validation (with URL checks) ----------------------
    if changed_recipes:
        code, out = run([sys.executable, "tools/validate_recipe.py", *changed_recipes])
        record(
            "recipe-validation",
            "pass" if code == 0 else "fail",
            f"{len(changed_recipes)} changed recipe(s) validated"
            if code == 0 else "recipe validation failed",
            out,
        )
    else:
        record("recipe-validation", "skipped", "no recipe files changed")

    # --- build test -------------------------------------------------------
    if build_relevant:
        code, out = run([".github/scripts/run-build.sh", "snakefile", "--output", "/tmp/Snakefile"])
        if code == 0:
            rules = 0
            try:
                with open("/tmp/Snakefile") as f:
                    rules = sum(1 for line in f if line.startswith("rule build_"))
            except OSError:
                pass
            record("build-test", "pass",
                   f"registry loaded; Snakefile generated with {rules} build rules", out)
        else:
            record("build-test", "fail", "registry load / Snakefile generation failed", out)
    else:
        record("build-test", "skipped", "no build-relevant files changed")

    overall_pass = all(c["status"] != "fail" for c in checks)

    # --- AI-review context (DATA only; consumed by the trusted reporter) --
    if changed_genomes and changed_recipes:
        contribution_type = "both"
    elif changed_genomes:
        contribution_type = "genome"
    elif changed_recipes:
        contribution_type = "recipe"
    else:
        contribution_type = "unknown"

    content_parts, total = [], 0
    for f in changed:
        if not f.startswith(("genomes/", "recipes/")):
            continue
        path = REPO_ROOT / f
        if not path.is_file():
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        blob = f"--- FILE: {f} ---\n{text}\n\n"
        if total + len(blob) > MAX_CONTENT_BYTES:
            blob = blob[: MAX_CONTENT_BYTES - total]
            content_parts.append(blob)
            content_parts.append(f"\n[TRUNCATED - file contents exceeded {MAX_CONTENT_BYTES} bytes]\n")
            break
        content_parts.append(blob)
        total += len(blob)
    (ctx_dir / "file_contents.txt").write_text("".join(content_parts))

    _, diff = run(["git", "diff", diff_range, "--", "genomes/", "recipes/"])
    (ctx_dir / "diff.txt").write_text(cap(diff, MAX_CONTENT_BYTES))

    existing_genomes = sorted(
        str(p.relative_to(REPO_ROOT))
        for pat in ("*.yaml", "*.yml")
        for p in (REPO_ROOT / "genomes").rglob(pat)
    )[:100] if (REPO_ROOT / "genomes").is_dir() else []
    (ctx_dir / "existing_genomes.txt").write_text("\n".join(existing_genomes) + "\n")

    existing_recipes = sorted(
        str(p.relative_to(REPO_ROOT))
        for p in (REPO_ROOT / "recipes").iterdir() if p.is_dir()
    )[:100] if (REPO_ROOT / "recipes").is_dir() else []
    (ctx_dir / "existing_recipes.txt").write_text("\n".join(existing_recipes) + "\n")

    report = {
        "pr_number": args.pr_number,
        "head_sha": args.head_sha,
        "contribution_type": contribution_type,
        "overall_pass": overall_pass,
        "checks": checks,
        "changed_files": changed,
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    print(f"\nReport written to {out_dir / 'report.json'} (overall_pass={overall_pass})")
    sys.exit(0 if overall_pass else 1)


if __name__ == "__main__":
    main()
