"""Tests for pep/metadata/*.fhr.json generation from the genome YAMLs.

Run: pytest build/test_generate_genome_metadata.py
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "build"))

import generate_genome_metadata as ggm  # noqa: E402


def test_queued_genome_without_a_yaml_gets_a_minimal_genome_record(tmp_path):
    samples = tmp_path / "samples.csv"
    samples.write_text("sample_name,genome_name\nnoyaml_fasta,noyaml\n")
    genomes = tmp_path / "genomes"
    genomes.mkdir()
    out, missing = ggm.generate(samples_csv=str(samples), genomes_dir=str(genomes))
    assert missing == ["noyaml"]
    assert json.loads(out["noyaml"]) == {"genome": "noyaml"}
