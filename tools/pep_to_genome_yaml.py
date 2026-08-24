# /// script
# requires-python = ">=3.10"
# dependencies = ["peprs>=0.2.4", "typer>=0.12", "pyyaml>=6", "jsonschema>=4.20", "requests>=2.31"]
# ///
"""Convert a PEP into refgenie-registry genome YAML files.

One PEP sample == one genome assembly. This reads a PEP (from PEPHub or a local
config), validates it against the eido input schema, and writes one
``genomes/<organism>/<assembly>.yaml`` per sample in the shape defined by
``schema/genome.schema.yaml`` (reference: ``genomes/human/hg38.yaml``).

Run it with uv (peprs needs Python >=3.10; the deps above auto-install):

    uv run tools/pep_to_genome_yaml.py convert databio/refgenie_new_registry_vertebrates:default \
        --added-by <github_user> --dry-run
    uv run tools/pep_to_genome_yaml.py inspect databio/refgenie_new_registry_vertebrates:default

`convert` validates the input PEP against the eido schema (via peprs.eido) before
writing anything, so a separate validate command isn't needed. See
pep_to_ref_yaml.md for the full design.
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

# Coarse common-name folders already used by the registry, so well-known species
# land next to existing files. Everything else falls back to slug(species_name).
COMMON_NAME_FOLDER = {
    "homo sapiens": "human",
    "mus musculus": "mouse",
    "rattus norvegicus": "rat",
    "drosophila melanogaster": "fly",
    "caenorhabditis elegans": "worm",
    "saccharomyces cerevisiae": "yeast",
    "schizosaccharomyces pombe": "yeast",
}


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


def resolve_folder(row: dict) -> str:
    """genomes/<folder>: explicit override -> common name -> map -> species slug."""
    gf = val(row, "genome_folder")
    if gf:
        return slug(gf)
    cn = val(row, "common_name")
    if cn:
        return slug(cn)
    sp = val(row, "species_name")
    if sp and sp.lower() in COMMON_NAME_FOLDER:
        return COMMON_NAME_FOLDER[sp.lower()]
    if sp:
        return slug(sp)
    raise ValueError("cannot resolve folder: no genome_folder/common_name/species_name")


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
    row: dict, added_by: Optional[str] = None, folder: Optional[str] = None
) -> tuple[str, str, dict]:
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

    # A single output folder (e.g. the PEP name) when given; otherwise fall back
    # to per-organism resolution.
    folder = slug(folder) if folder else resolve_folder(row)

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

    metadata = {"added": date.today().isoformat()}
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
    out: Path = typer.Option(DEFAULT_OUT, help="Output root for genomes/<folder>/<name>.yaml"),
    schema: str = typer.Option(str(DEFAULT_SCHEMA), help="eido input schema: path, URL, or namespace/name:version"),
    only: Optional[str] = typer.Option(None, help="Comma-separated sample names to restrict to"),
    folder: Optional[str] = typer.Option(None, help="Single output folder for ALL genomes (default: the PEP name)"),
    added_by: Optional[str] = typer.Option(None, help="Value for metadata.added_by"),
    overwrite: bool = typer.Option(False, help="Overwrite existing files (default: skip)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print target path + YAML, write nothing"),
    validate: bool = typer.Option(True, help="Validate the input PEP (eido) and each output file"),
    url_check: bool = typer.Option(False, help="Also verify FASTA URLs when validating output (slow)"),
):
    """Convert every PEP sample into a genome YAML file."""
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
    typer.echo(f"Writing all genomes into: {out}/{target_folder}/")

    wanted = {s.strip() for s in only.split(",")} if only else None
    written = skipped = failed = 0
    for s in project.samples:
        row = s.to_dict()
        name0 = row.get("sample_name", "?")
        if wanted is not None and name0 not in wanted:
            continue
        try:
            _, name, doc = sample_to_genome(row, added_by=added_by, folder=target_folder)
        except Exception as exc:  # noqa: BLE001 — isolate one bad sample
            failed += 1
            typer.secho(f"  FAIL  {name0}: {exc}", fg="red")
            continue

        dest = out / target_folder / f"{name}.yaml"
        text = dump_yaml(doc)

        if dry_run:
            typer.echo(f"\n# --- {dest.relative_to(out.parent) if out.parent in dest.parents else dest} ---")
            typer.echo(text.rstrip())
            if validate:
                verrs = validate_doc_inmemory(doc)
                if verrs:
                    failed += 1
                    typer.secho(f"  INVALID {name}:", fg="red")
                    for e in verrs:
                        typer.echo(f"    - {e}")
                else:
                    written += 1
            else:
                written += 1
            continue

        if dest.exists() and not overwrite:
            skipped += 1
            typer.echo(f"  skip  {dest} (exists)")
            continue

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
        if validate:
            verrs = validate_file(dest, check_urls=url_check)
            if verrs:
                failed += 1
                typer.secho(f"  INVALID {dest}:", fg="red")
                for e in verrs:
                    typer.echo(f"    - {e}")
                continue
        written += 1
        typer.echo(f"  write {dest}")

    typer.echo(f"\nSummary: {written} {'ok' if dry_run else 'written'}, {skipped} skipped, {failed} failed")
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
