"""validate_sources: names must not name another assembly of the same species."""

from validate_sources import assembly_conflicts


def _row(name, assembly, pep=""):
    return {"name": name, "genome_assembly": assembly, "pep_sample_name": pep}


def test_a_grch37_genome_named_hg38_is_caught():
    # The row that once made `hg38_build37_2_igenomes` name a GRCh37 genome.
    errors = assembly_conflicts(
        [_row("hg38_build37_2_igenomes", "hg19", "GRCh38-igenomes-build37-2-ncbi")]
    )
    assert len(errors) == 2 and "names hg38" in errors[0] and "names GRCh38" in errors[1]


def test_matching_and_other_species_names_pass():
    rows = [
        _row("hg19_build37_2_igenomes", "hg19", "GRCh37-igenomes-build37-2-ncbi"),
        _row("hg38_mm10", "hg38"),  # combined human + mouse
        _row("GRCh38-ena-15", "GRCh38"),
        _row("mm39_primary_ensembl", "mm39", "GRCm39-primary-113-ensembl"),
        _row("sacCer3", "sacCer3"),  # not a known assembly: not checked
    ]
    assert assembly_conflicts(rows) == []
