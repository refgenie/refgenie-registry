#!/usr/bin/env python3
"""Assert the bulker crate the nightly activates defines every command the recipes need.

Why this exists
---------------
The nightly Rivanna build (mobot job ``refgenie-registry-build``) activates ONE
bulker crate, ``databio/refgenie``. If a recipe invokes a command that crate does
not define, bulker does not fail -- the shell simply falls through to whatever
binary happens to exist on the Rivanna host, or to nothing at all. Both failure
modes are quiet:

* Falling through to the HOST is the dangerous one. Every recipe's
  ``custom_seek_keys.version`` expression pipes through ``grep -aoP`` and
  ``awk``. The host's GNU awk and a container's BusyBox awk disagree about
  enough that an expression can change its *answer* rather than error. A version
  expression that returns the wrong string does not crash the build; it renames
  the published asset.
* Falling through to nothing gives an empty version, which (before the
  empty-name guard) produced an asset literally named "".

Until 2026-07 this was papered over by activating two crates side by side
(``databio/lab,databio/refgenie:1.0.0``). That hid a worse problem: bulker
resolves collisions first-listed-wins, so ``databio/lab`` -> ``bulker/biobase``
silently shadowed 10 of refgenie's 16 commands and the refgenie manifest stopped
describing what actually built the assets.

So: one crate, and a check that the one crate is sufficient. Adding a recipe
that uses a new tool now fails here, on the PR, instead of at 3am.

What "required" means
---------------------
Derived mechanically, never hand-maintained:

1. the leading token of every ``command_templates`` entry (per statement --
   entries are shell, so ``;``-separated statements and pipeline stages count)
2. every command referenced in each recipe's ``custom_seek_keys`` expressions
3. the coreutils those version expressions pipe through

...minus shell builtins and the documented host-provided allowlist below.

Usage
-----
    python tools/check_crate_coverage.py                       # fetch from hub
    python tools/check_crate_coverage.py --crate databio/refgenie:1.1.0
    python tools/check_crate_coverage.py --manifest /path/to/refgenie_1.1.0.yaml
    python tools/check_crate_coverage.py --list                # just print the set
"""

from __future__ import annotations

import argparse
import re
import shlex
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
RECIPES_DIR = REPO_ROOT / "recipes"
REGISTRY_URL = "https://hub.bulker.io"
DEFAULT_CRATE = "databio/refgenie:1.1.0"

# Shell syntax that shows up as a leading token but is not a command.
SHELL_BUILTINS = {
    "cd", "if", "then", "else", "elif", "fi", "for", "while", "do", "done",
    "case", "esac", "export", "set", "unset", "echo", "test", "[", "eval",
    "exec", "source", ".", "return", "true", "false", "env", "LC_COLLATE=C",
}

# Commands deliberately NOT in the crate, with the reason. These resolve from
# the Rivanna host. Each entry is a decision, not an oversight -- anything not
# listed here and not in the crate is a hard failure.
# (`cp`, `mkdir`, `cat`, `sort` and friends are NOT here -- bulker/coreutils
# supplies them, and `rm` comes from that crate's `host_commands`.)
HOST_PROVIDED = {
    # POSIX file plumbing not carried by bulker/coreutils.
    "mv": "not in bulker/coreutils; trivial file plumbing, no versioned output",
    "find": "not in bulker/coreutils; selects files for deletion only",
    "file": "single `file ... | grep -q compressed` type probe in fasta_txome",
    # Compression. Not in bulker/coreutils (they are separate upstream
    # packages), and output is byte-identical across implementations.
    "gzip": "not in bulker/coreutils; output format is standardized",
    "gunzip": "not in bulker/coreutils; output format is standardized",
    "unzip": "not in bulker/coreutils; output format is standardized",
    # refgenie's own helper, installed alongside the refgenie binary that is
    # driving the build. It cannot live in a crate: it needs the host's
    # RefgetStore.
    "refgenie-build-fasta": "refgenie helper script; needs host RefgetStore access",
}

# Commands a recipe references that the crate intentionally does not carry.
# Keep in sync with `excluded:` in hub.bulker.io/refgenie_crate_sources.yaml.
EXCLUDED = {
    "cellranger": (
        "Dropped from databio/refgenie: quay.io/xujishu/cellranger tags below "
        "6.0.0 are Docker v1 manifests apptainer cannot convert. The "
        "cellranger_reference recipe is not in pep/samples.csv, so the nightly "
        "never invokes it."
    ),
}

# The coreutils every recipe's version expression pipes through. These are
# required explicitly even though no recipe's *leading* token is one of them,
# because a host/container mismatch here silently changes results.
VERSION_EXPR_COREUTILS = {"grep", "awk", "head", "cut", "sed", "tr"}


# ------------------------------------------------------------------ derivation


def statements(template: str) -> list[str]:
    """Split a command_templates entry into the statements a shell would run.

    Must be quote-aware. Recipes are full of awk programs like
    ``awk '{if($6=="+"){print ...}}'`` whose bodies are stuffed with ``;`` and
    ``|``; a naive split treats ``print`` and ``split(a,`` as commands and the
    check drowns in false positives.
    """
    text = template.replace("\\\n", " ")
    out: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch in ";|\n" or text.startswith("&&", i):
            width = 2 if text.startswith("&&", i) or text.startswith("||", i) else 1
            out.append("".join(buf))
            buf = []
            i += width
            continue
        buf.append(ch)
        i += 1
    out.append("".join(buf))

    # Command substitutions run commands too -- dbnsfp does `rm `find ...``.
    for sub in re.findall(r"`([^`]*)`", text) + re.findall(r"\$\(([^)]*)\)", text):
        out.extend(statements(sub))

    return [s.strip() for s in out if s.strip()]


# Keywords that can prefix a real command within one statement, e.g.
# `if (file ... )`, `else mv ...`. Skipping past them is what finds `file` and
# `mv` in fasta_txome's one-liner conditional.
LEADING_KEYWORDS = {
    "if", "then", "else", "elif", "fi", "do", "done", "while", "for",
    "time", "!", "env", "command", "exec", "nice",
}


def leading_token(statement: str) -> str | None:
    """First real command word of a statement.

    Skips redirections, ``VAR=value`` prefixes and shell keywords, so
    ``else mv a b`` yields ``mv`` rather than being discarded as a builtin.
    """
    statement = re.sub(r"^[><(){}\s]+", "", statement)
    if not statement:
        return None
    try:
        tokens = shlex.split(statement, posix=False)
    except ValueError:
        tokens = statement.split()
    for token in tokens:
        if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*=.*", token):
            continue  # VAR=value prefix
        token = token.strip("`'\"()")
        if not token or token in LEADING_KEYWORDS:
            continue
        # Jinja placeholders are paths/values, not commands.
        if token.startswith("{{"):
            return None
        return token
    return None


def required_commands() -> dict[str, set[str]]:
    """Map command -> set of recipe paths that need it."""
    needed: dict[str, set[str]] = {}

    def note(cmd: str, where: str) -> None:
        if cmd and cmd not in SHELL_BUILTINS:
            needed.setdefault(cmd, set()).add(where)

    for recipe_path in sorted(RECIPES_DIR.glob("*/recipe.yaml")):
        rel = str(recipe_path.relative_to(REPO_ROOT))
        with open(recipe_path) as handle:
            recipe = yaml.safe_load(handle) or {}

        # 1. leading token of every command_templates statement
        for template in recipe.get("command_templates") or []:
            for statement in statements(template):
                note(leading_token(statement), rel)

        # 2. every command in custom_seek_keys expressions
        for expr in (recipe.get("custom_seek_keys") or {}).values():
            if not isinstance(expr, str):
                continue
            expr = expr.split("#", 1)[0]  # strip trailing comment
            for statement in statements(expr):
                note(leading_token(statement), rel)

    # 3. the coreutils the version expressions pipe through
    for cmd in VERSION_EXPR_COREUTILS:
        needed.setdefault(cmd, set()).add("(version expressions)")

    return needed


# ------------------------------------------------------------------ crate load


def fetch(url: str) -> dict:
    # hub.bulker.io sits behind Cloudflare, which 403s urllib's default
    # User-Agent. Send a real one.
    request = urllib.request.Request(
        url, headers={"User-Agent": "refgenie-registry-crate-check/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return yaml.safe_load(response.read())["manifest"]
    except (urllib.error.URLError, KeyError, TypeError) as exc:
        raise SystemExit(f"ERROR: could not load crate manifest {url}: {exc}")


def manifest_url(crate: str) -> str:
    namespace, rest = crate.split("/", 1)
    if ":" in rest:
        name, version = rest.split(":", 1)
        return f"{REGISTRY_URL}/{namespace}/{name}_{version}.yaml"
    return f"{REGISTRY_URL}/{namespace}/{rest}.yaml"


def crate_commands(manifest: dict, seen: set[str] | None = None) -> set[str]:
    """All commands the crate provides, following imports transitively."""
    seen = seen if seen is not None else set()
    provided = {e["command"] for e in manifest.get("commands") or []}
    provided |= set(manifest.get("host_commands") or [])
    for imported in manifest.get("imports") or []:
        if imported in seen:
            continue
        seen.add(imported)
        crate = imported.replace(":default", "")
        if ":" in imported and not imported.endswith(":default"):
            crate = imported
        provided |= crate_commands(fetch(manifest_url(crate)), seen)
    return provided


# ------------------------------------------------------------------------ main


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--crate", default=DEFAULT_CRATE, help="crate to check")
    ap.add_argument("--manifest", help="local manifest file instead of the hub")
    ap.add_argument("--list", action="store_true", help="print required set and exit")
    args = ap.parse_args()

    needed = required_commands()

    if args.list:
        for cmd in sorted(needed, key=str.lower):
            kind = (
                "HOST" if cmd in HOST_PROVIDED
                else "EXCLUDED" if cmd in EXCLUDED
                else "CRATE"
            )
            print(f"{kind:9} {cmd:30} {', '.join(sorted(needed[cmd]))}")
        return 0

    if args.manifest:
        with open(args.manifest) as handle:
            manifest = yaml.safe_load(handle)["manifest"]
        source = args.manifest
    else:
        source = manifest_url(args.crate)
        manifest = fetch(source)

    provided = crate_commands(manifest)
    print(f"crate    : {manifest['name']}:{manifest['version']}  ({source})")
    print(f"provides : {len(provided)} commands (imports resolved)")
    print(f"recipes  : {len(list(RECIPES_DIR.glob('*/recipe.yaml')))} recipes")
    print()

    missing: list[str] = []
    for cmd in sorted(needed, key=str.lower):
        if cmd in provided or cmd in HOST_PROVIDED or cmd in EXCLUDED:
            continue
        missing.append(cmd)

    if missing:
        print("FAIL: recipes reference commands the crate does not define:")
        for cmd in missing:
            print(f"  {cmd}")
            for where in sorted(needed[cmd]):
                print(f"      used by {where}")
        print()
        print(
            "Fix by adding the command to hub.bulker.io/refgenie_crate_sources.yaml\n"
            "(with a `siblings` or `overrides` entry) and cutting a new crate\n"
            "version, OR -- if it really is a host utility -- by adding it to\n"
            "HOST_PROVIDED in this file with a written reason."
        )
        return 1

    # A stale HOST_PROVIDED/EXCLUDED entry is a smaller problem than a missing
    # command, but it still misleads the next reader. Report, do not fail.
    unused = sorted(
        (set(HOST_PROVIDED) | set(EXCLUDED)) - set(needed)
    )
    if unused:
        print("NOTE: allowlist entries no recipe uses any more:")
        for cmd in unused:
            print(f"  {cmd}")
        print()

    print(f"PASS: all {len(needed)} required commands resolve.")
    print(
        f"      {len([c for c in needed if c in provided])} from the crate, "
        f"{len([c for c in needed if c in HOST_PROVIDED])} host-provided, "
        f"{len([c for c in needed if c in EXCLUDED])} excluded."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
