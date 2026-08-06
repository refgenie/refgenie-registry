# PEP: the nightly build queue

This directory holds the [PEP](http://pep.databio.org) that drives the nightly
Rivanna asset builds.

| File | Role |
|------|------|
| `tiers.yaml` | **Source of truth.** Per-genome asset assignments, expressed as tiers plus optional `add:`/`drop:` overrides. |
| `samples.csv` | **Generated artifact.** One row per `(genome, asset)`. Never hand-edit. |
| `config.yaml` | PEP config: sample modifiers and `derive.sources` (per-genome FASTA/source paths). |

`samples.csv` is generated from `tiers.yaml` by `build/generate_samples.py`.
The Snakefile (`build/Snakefile`) reads the collated PEP so that
`pep.get_sample(genome).asset_group_name` is the list of recipes to build for
that genome.

## Tiers

`tiers.yaml` defines named asset sets and assigns each genome one:

```yaml
tiers:
  full:          [fasta, fasta_index, bwa_index, bowtie2_index, hisat2_index,
                  star_index, suffixerator_index, tallymer_index]
  standard:      [fasta, fasta_index, bwa_index, bowtie2_index, hisat2_index,
                  star_index]
  sequence_only: [fasta, fasta_index]

genomes:
  mm39: full                      # bare string when the tier is the whole story
  hg38:                           # mapping only when overriding
    tier: full
    add: [ensembl_gtf, dbsnp]     # niche / annotation assets, per-genome
    drop: [tallymer_index]
```

A genome's resolved asset set is: start from `tiers[tier]` (tier order), apply
`add:` (union, in add order), then apply `drop:` (difference). The order is
deterministic so the `samples.csv` diff is stable.

Only pure fasta-derivable (Class 1) assets belong in tiers. Annotation/variant
(Class 2) and transcriptome-chain (Class 3) assets are `add:`-only, because their
availability is about whether a per-genome source file exists, not about cost.

## Editing the queue

1. Edit `pep/tiers.yaml`.
2. Regenerate: `python build/generate_samples.py`.
3. **Review the `pep/samples.csv` diff.** This diff IS the go/no-go gate:
   committing `samples.csv` launches those builds on the next nightly.
4. Commit both `tiers.yaml` and `samples.csv` together.

`build/generate_samples.py --check` verifies `samples.csv` is up to date without
writing (exits non-zero on drift). The nightly driver (`build/run_builds.sh`)
regenerates and runs `git diff --exit-code pep/samples.csv` at startup, so a
hand-edited or stale CSV fails the build loudly.

## Validations

`generate_samples.py` refuses to generate when either check fails:

- **Source validation** — any Class-2/3 asset (a recipe with non-empty
  `input_files`) requires a per-genome source key `<genome>_<asset>` in
  `config.yaml` `derive.sources`.
- **Dependency closure** — every asset dependency declared by a recipe's
  `input_assets` must also be in the genome's resolved set (e.g. `tallymer_index`
  needs `suffixerator_index`, `salmon_*` needs `fasta_txome`).

## Note: no `# GENERATED` comment in samples.csv

`samples.csv` deliberately carries no leading comment line. peppy reads it with a
bare `pandas.read_csv` (no comment character), so a `#` first line would be
parsed as the header and corrupt the queue. Provenance lives here and in
`tiers.yaml`; the `run_builds.sh` guard is what enforces "generated only".
