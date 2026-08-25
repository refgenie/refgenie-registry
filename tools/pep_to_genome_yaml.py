#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["peprs>=0.2.4", "typer>=0.12", "pyyaml>=6", "jsonschema>=4.20", "requests>=2.31"]
# ///
"""Convert a PEP into refgenie-registry genome YAML files.

One PEP sample == one genome assembly. This reads a PEP (from PEPHub or a local
config), validates it against the eido input schema, and writes one
``genomes/<folder>/<sample_name>.yaml`` per sample in the shape defined by
``schema/genome.schema.yaml`` (reference: ``genomes/human/hg38.yaml``). All
samples land in a single folder, named after the PEP unless ``--folder`` says
otherwise.

Every emitted genome carries a ``build:`` block, which the registry requires:
``store`` says which store holds the sequence and must name a real directory
under ``stores/``; ``tier`` says how far to take the genome. Bulk imports are
``store_only`` (loaded into the store, browsable, no assets built), so that is
the default.

The shebang runs this through ``uv``, which installs the dependencies above into
a throwaway environment, so it works without setting anything up:

    tools/pep_to_genome_yaml.py convert <pep> --store vgp --added-by <user> --dry-run
    tools/pep_to_genome_yaml.py inspect <pep>

`convert` validates the input PEP against the eido schema (via peprs.eido) and
validates every generated document BEFORE writing anything, so a failed run
leaves no files behind and a separate validate command isn't needed.
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import typer
import yaml

# tools/ is this file's dir; make sibling modules importable when run via `uv run`.
_TOOLS_DIR = Path(__file__).resolve().parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

_REPO = _TOOLS_DIR.parent
DEFAULT_SCHEMA = _REPO / "schema" / "pep_genome_input.schema.json"
DEFAULT_OUT = _REPO / "genomes"

app = typer.Typer(add_completion=False, help=__doc__)


# --------------------------------------------------------------------------- #
# PEP loading + schema loading
# --------------------------------------------------------------------------- #
def load_pep(ref: str):
    """Load a PEP from a local config path or a PEPHub registry path."""
    import peprs

    p = Path(ref)
    if p.exists():
        return peprs.Project(str(p))
    return peprs.Project.from_pephub(ref)


def load_input_schema(schema_arg: str):
    """Return something ``peprs.eido.validate_project`` accepts: a path or a dict.

    Accepts a local file path, an http(s) URL to a JSON schema, or a PEPHub
    schema registry path ``namespace/name:version`` (default version 0.1.0).
    """
    if schema_arg.startswith(("http://", "https://")):
        import requests

        return requests.get(schema_arg, timeout=60).json()
    p = Path(schema_arg)
    if p.exists():
        return str(p)
    if "/" in schema_arg:  # pephub schema registry: namespace/name[:version]
        import requests

        reg, _, ver = schema_arg.partition(":")
        ver = ver or "0.1.0"
        url = (
            f"https://pephub-api.databio.org/api/v1/schemas/{reg}"
            f"/versions/{ver}/file?format=json"
        )
        return requests.get(url, timeout=60).json()
    raise typer.BadParameter(f"Cannot locate schema: {schema_arg!r}")


# --------------------------------------------------------------------------- #
# Field helpers
# --------------------------------------------------------------------------- #
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Tiers a bulk import may assign, from pep/tiers.yaml. store_only is the default:
# an imported assembly gets its sequence loaded and becomes browsable, and
# building assets for it is a separate, deliberate decision.
TIERS = ("store_only", "sequence_only", "standard", "full")


def store_slugs() -> list[str]:
    """Names of the stores this repo defines, i.e. the legal build.store values."""
    return sorted(
        d.name for d in (_REPO / "stores").iterdir()
        if d.is_dir() and (d / "project_config.yaml").is_file()
    )


def slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", str(s).strip().lower())
    return re.sub(r"_+", "_", s).strip("_")


def val(row: dict, key: str) -> Optional[str]:
    """Stripped string value, or None if absent/blank/NaN."""
    v = row.get(key)
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.lower() == "nan":
        return None
    return s


def int_val(row: dict, key: str) -> Optional[int]:
    s = val(row, key)
    if s is None:
        return None
    return int(float(s))  # tolerate "9606", 9606, 9606.0


def infer_provider(url: str, explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit
    host = urlparse(url).netloc.lower()
    if "ucsc" in host or "hgdownload" in host:
        return "UCSC"
    if "ncbi" in host:
        return "NCBI"
    if "ebi.ac.uk" in host or "ensembl" in host:
        return "Ensembl"
    return None


def infer_source(accession: Optional[str], explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit
    if not accession:
        return None
    if accession.startswith("GCF_"):
        return "RefSeq"
    if accession.startswith("GCA_"):
        return "GenBank"
    return None


# --------------------------------------------------------------------------- #
# YAML dumper: ordered keys + literal block for `description`
# --------------------------------------------------------------------------- #
class LiteralStr(str):
    pass


class _GenomeDumper(yaml.SafeDumper):
    pass


_GenomeDumper.add_representer(
    LiteralStr,
    lambda d, data: d.represent_scalar("tag:yaml.org,2002:str", data, style="|"),
)


def dump_yaml(doc: dict) -> str:
    return yaml.dump(
        doc,
        Dumper=_GenomeDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=4096,
    )


# --------------------------------------------------------------------------- #
# The mapping: sample row -> (folder, name, genome doc). Pure, no I/O.
# --------------------------------------------------------------------------- #
def sample_to_genome(
    row: dict,
    store: str,
    tier: str = "store_only",
    added_by: Optional[str] = None,
    folder: Optional[str] = None,
    added: Optional[str] = None,
) -> tuple[str, str, dict]:
    """Map one PEP sample row to (folder, name, genome document).

    Pure: no I/O, no clock unless ``added`` is omitted. ``store`` and ``tier``
    become the required ``build:`` block. ``added`` overrides the date stamped
    into ``metadata.added``, which is what makes re-running byte-reproducible.
    """
    name = val(row, "sample_name")
    if not name:
        raise ValueError("missing sample_name")
    if not NAME_RE.match(name):
        raise ValueError(f"name {name!r} violates ^[A-Za-z0-9_.-]+$")

    species = val(row, "species_name")
    if not species:
        raise ValueError("missing species_name")
    taxon_id = int_val(row, "taxon_id")
    if taxon_id is None or taxon_id < 1:
        raise ValueError(f"missing/invalid taxon_id: {row.get('taxon_id')!r}")
    description = val(row, "description")
    if not description:
        raise ValueError("missing description")
    fasta_url = val(row, "fasta_url")
    if not fasta_url:
        raise ValueError("missing fasta_url")

    # Every sample from one PEP lands in one folder: the PEP name by default,
    # or whatever --folder says.
    if not folder:
        raise ValueError("no output folder given")
    folder = slug(folder)

    if store not in store_slugs():
        raise ValueError(
            f"build.store {store!r} is not a store in this repo "
            f"(choose one of: {', '.join(store_slugs())})"
        )
    if tier not in TIERS:
        raise ValueError(f"build.tier {tier!r} is not a tier (choose one of: {', '.join(TIERS)})")

    doc: dict = {"name": name}

    genome_name = val(row, "genome_name")
    aliases = [a for a in [genome_name] if a and a != name]
    if aliases:
        doc["aliases"] = aliases

    doc["description"] = LiteralStr(description + "\n")

    organism = {"scientific_name": species}
    common = val(row, "common_name")
    if common:
        organism["common_name"] = common
    organism["taxon_id"] = taxon_id
    doc["organism"] = organism

    accession = val(row, "ncbi_id")
    assembly = {}
    source = infer_source(accession, val(row, "assembly_source"))
    if source:
        assembly["source"] = source
    if accession:
        assembly["accession"] = accession
    level = val(row, "assembly_level")
    if level:
        assembly["level"] = level
    if assembly:
        doc["assembly"] = assembly

    masking = val(row, "masking")
    if masking:
        doc["masking"] = masking

    source_entry = {}
    provider = infer_provider(fasta_url, val(row, "fasta_provider"))
    if provider:
        source_entry["provider"] = provider
    source_entry["url"] = fasta_url
    checksum = {}
    md5 = val(row, "md5")
    if md5:
        checksum["md5"] = md5
    checksum["sha256"] = val(row, "sha256") or "compute_on_registration"
    doc["fasta"] = {"sources": [source_entry], "checksum": checksum}

    digest = val(row, "seqcol_digest")
    doc["seqcol"] = {"digest": digest} if digest else {"compute": True}

    fhr = {}
    assembly_date = val(row, "assembly_date")
    if assembly_date and ISO_DATE_RE.match(assembly_date):
        fhr["date_created"] = assembly_date
    identifiers = []
    bioproject = val(row, "BioProject")
    if bioproject:
        identifiers.append(f"bioproject:{bioproject}")
    biosample = val(row, "id_id")
    if biosample and biosample.upper().startswith("SAM"):
        identifiers.append(f"biosample:{biosample}")
    if identifiers:
        fhr["identifier"] = identifiers
    if fhr:
        doc["fhr"] = fhr

    doc["build"] = {"store": store, "tier": tier}

    metadata = {"added": added or date.today().isoformat()}
    if added_by:
        metadata["added_by"] = added_by
    doc["metadata"] = metadata

    return folder, name, doc


# --------------------------------------------------------------------------- #
# Validation of emitted docs (reuses tools/validate_genome.py)
# --------------------------------------------------------------------------- #
def validate_doc_inmemory(doc: dict) -> list[str]:
    import genome_to_fhr
    import validate_genome as vg

    data = genome_to_fhr.normalize_dates(dict(doc))
    schema = vg.load_schema()
    errs = []
    errs += vg.validate_schema(data, schema)
    errs += vg.check_required_fields(data)
    errs += vg.check_taxon(data)
    errs += vg.check_checksum_format(data)
    errs += vg.check_masking(data)
    errs += vg.check_fhr_block(data)
    errs += vg.check_fhr_export(data)
    return errs


def validate_file(path: Path, check_urls: bool) -> list[str]:
    import validate_genome as vg

    return vg.validate_genome(path, vg.load_schema(), check_urls=check_urls, check_fhr=True)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
@app.command()
def convert(
    pep: str = typer.Argument(..., help="PEPHub registry path or local PEP config path"),
    store: str = typer.Option(..., help="build.store for every genome; must name a stores/ directory"),
    tier: str = typer.Option("store_only", help=f"build.tier for every genome ({'|'.join(TIERS)})"),
    out: Path = typer.Option(DEFAULT_OUT, help="Output root for genomes/<folder>/<name>.yaml"),
    schema: str = typer.Option(str(DEFAULT_SCHEMA), help="eido input schema: path, URL, or namespace/name:version"),
    only: Optional[str] = typer.Option(None, help="Comma-separated sample names to restrict to"),
    folder: Optional[str] = typer.Option(None, help="Single output folder for ALL genomes (default: the PEP name)"),
    added_by: Optional[str] = typer.Option(None, help="Value for metadata.added_by"),
    added: Optional[str] = typer.Option(None, help="Value for metadata.added (YYYY-MM-DD; default today). Set it to make a run reproducible."),
    overwrite: bool = typer.Option(False, help="Overwrite existing files (default: skip)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print target path + YAML, write nothing"),
    validate: bool = typer.Option(True, help="Validate the input PEP (eido) and every generated document"),
    url_check: bool = typer.Option(False, help="Also verify FASTA URLs when validating output (slow)"),
):
    """Convert every PEP sample into a genome YAML file.

    Nothing is written until every sample has been mapped AND validated. A run
    that fails validation leaves the working tree untouched, so a partial import
    can never be committed by accident.
    """
    if store not in store_slugs():
        raise typer.BadParameter(
            f"{store!r} is not a store in this repo. Choose one of: {', '.join(store_slugs())}"
        )
    if tier not in TIERS:
        raise typer.BadParameter(f"{tier!r} is not a tier. Choose one of: {', '.join(TIERS)}")
    if added and not ISO_DATE_RE.match(added):
        raise typer.BadParameter(f"--added must be YYYY-MM-DD, got {added!r}")

    project = load_pep(pep)
    typer.echo(f"Loaded PEP: {project.name}  ({len(project)} samples)")

    if validate:
        from peprs.eido import EidoValidationError, validate_project

        try:
            validate_project(project, load_input_schema(schema))
            typer.echo(f"Input PEP passed eido validation ({schema}).")
        except EidoValidationError as e:
            typer.secho("Input PEP FAILED eido validation:", fg="red")
            for cat, errs in e.errors_by_type.items():
                for err in errs[:5]:
                    typer.echo(f"  [{cat}] {err.get('message', err)}")
            raise typer.Exit(1)

    # All genomes go into ONE folder named after the PEP (override with --folder).
    target_folder = slug(folder) if folder else slug(project.name or "pep")
    typer.echo(f"Writing all genomes into: {out}/{target_folder}/  (store={store}, tier={tier})")

    wanted = {s.strip() for s in only.split(",")} if only else None

    # --- pass 1: map and validate everything, writing nothing ----------------
    planned: list[tuple[Path, str, str]] = []  # (dest, name, text)
    failed = 0
    for s in project.samples:
        row = s.to_dict()
        name0 = row.get("sample_name", "?")
        if wanted is not None and name0 not in wanted:
            continue
        try:
            _, name, doc = sample_to_genome(
                row, store=store, tier=tier, added_by=added_by,
                folder=target_folder, added=added,
            )
        except Exception as exc:  # noqa: BLE001 - isolate one bad sample
            failed += 1
            typer.secho(f"  FAIL  {name0}: {exc}", fg="red")
            continue

        if validate:
            verrs = validate_doc_inmemory(doc)
            if verrs:
                failed += 1
                typer.secho(f"  INVALID {name}:", fg="red")
                for e in verrs:
                    typer.echo(f"    - {e}")
                continue

        planned.append((out / target_folder / f"{name}.yaml", name, dump_yaml(doc)))

    if failed:
        typer.secho(
            f"\n{failed} sample(s) failed; nothing was written. Fix the PEP and re-run.",
            fg="red",
        )
        raise typer.Exit(1)

    if dry_run:
        for dest, name, text in planned:
            typer.echo(f"\n# --- {dest} ---")
            typer.echo(text.rstrip())
        typer.echo(f"\nSummary: {len(planned)} ok, 0 skipped, 0 failed (dry run, nothing written)")
        return

    # --- pass 2: write ------------------------------------------------------
    written = skipped = 0
    for dest, name, text in planned:
        if dest.exists() and not overwrite:
            skipped += 1
            typer.echo(f"  skip  {dest} (exists)")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
        written += 1
        typer.echo(f"  write {dest}")

    if validate and url_check:
        for dest, name, _ in planned:
            if dest.exists():
                for e in validate_file(dest, check_urls=True):
                    typer.secho(f"  URL  {dest}: {e}", fg="red")
                    failed += 1

    typer.echo(f"\nSummary: {written} written, {skipped} skipped, {failed} failed")
    if failed:
        raise typer.Exit(1)



@app.command()
def inspect(
    pep: str = typer.Argument(..., help="PEPHub registry path or local PEP config path"),
    rows: int = typer.Option(3, help="How many sample rows to print"),
):
    """Load a PEP and print its size, columns, and a few rows."""
    project = load_pep(pep)
    samples = list(project.samples)
    typer.echo(f"name: {project.name}")
    typer.echo(f"samples: {len(samples)}")
    if samples:
        cols = list(samples[0].to_dict().keys())
        typer.echo(f"columns: {cols}")
        for s in samples[:rows]:
            typer.echo(f"  {s.to_dict()}")


if __name__ == "__main__":
    app()
