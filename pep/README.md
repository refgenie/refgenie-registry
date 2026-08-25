# PEP: the nightly build queue

This directory holds the [PEP](http://pep.databio.org) that drives the nightly
Rivanna asset builds.

| File | Role |
|------|------|
| `tiers.yaml` | **Source of truth for what a tier means.** Named asset sets: `store_only`, `sequence_only`, `standard`, `full`. |
| `samples.csv` | **Generated artifact.** One row per `(genome, asset)`. Never hand-edit. |
| `metadata/` | **Generated artifact.** One `<genome>.fhr.json` sidecar per genome. Never hand-edit. |
| `config.yaml` | PEP config: sample modifiers and `derive.sources` (per-genome FASTA/source paths). |

**The queue itself lives in `genomes/**/*.yaml`,** not here. Each genome YAML
carries a `build:` block naming the store that holds its sequence and the tier to
take it to:

```yaml
build:
  store: legacy
  tier: full
  drop: [star_index, tallymer_index]
```

`samples.csv` is generated from those blocks plus `tiers.yaml` by
`build/generate_samples.py`. The Snakefile (`build/Snakefile`) reads the collated
PEP so that `pep.get_sample(genome).asset_group_name` is the list of recipes to
build for that genome.

The queue previously lived in a separate `build_matrix.yaml` that listed
26 of the 128 genomes; the other 102 genome YAMLs were inert, and a genome could
be added to the registry without anything anywhere building or loading it. That
file is gone: one genome, one file, one `build:` block.

## Tiers

`tiers.yaml` defines the ladder. Each tier includes every tier below it:

```yaml
tiers:
  store_only:    []
  sequence_only: [fasta, fasta_index]
  standard:      [fasta, fasta_index, bwa_index, bowtie2_index, hisat2_index,
                  star_index]
  full:          [fasta, fasta_index, bwa_index, bowtie2_index, hisat2_index,
                  star_index, suffixerator_index, tallymer_index]
```

A genome's resolved asset set is: start from `tiers[tier]` (tier order), apply
`add:` (union, in add order), then apply `drop:` (difference). The order is
deterministic, and genomes are emitted sorted by name, so the `samples.csv` diff
is stable.

`store_only` emits **no rows at all**. Its sequence is loaded into its store and
is browsable as a zero-asset genome; nothing is built. That is where most of the
registry sits, and it is the right default for a new genome.

Only pure fasta-derivable (Class 1) assets belong in tiers. Annotation/variant
(Class 2) and transcriptome-chain (Class 3) assets are `add:`-only, because their
availability is about whether a per-genome source file exists, not about cost.

## Editing the queue

1. Edit the genome's `build:` block in `genomes/<organism>/<assembly>.yaml`.
2. Regenerate: `python build/generate_samples.py`.
3. **Review the `pep/samples.csv` diff.** This diff IS the go/no-go gate:
   committing `samples.csv` launches those builds on the next nightly.
4. Commit the genome YAML, `samples.csv` and `pep/metadata/` together.

`build/generate_samples.py --check` verifies `samples.csv` is up to date without
writing (exits non-zero on drift). The nightly driver (`build/run_builds.sh`)
regenerates and runs `git diff --exit-code pep/samples.csv` at startup, so a
hand-edited or stale CSV fails the build loudly. `pep/metadata/` is guarded the
same way.

## Validations

`generate_samples.py` refuses to generate when any check fails:

- **FASTA source**, a genome in a build tier requires a `<genome>_fa` key in
  `config.yaml` `derive.sources`. Without this the rows are emitted happily,
  pointing at a source key that does not exist, and the run dies much later inside
  peppy with a message that never names the genome.
- **Source validation**, any Class-2/3 asset (a recipe with non-empty
  `input_files`) requires a per-genome source key `<genome>_<asset>` in
  `config.yaml` `derive.sources`.
- **Dependency closure**, every asset dependency declared by a recipe's
  `input_assets` must also be in the genome's resolved set (e.g. `tallymer_index`
  needs `suffixerator_index`, `salmon_*` needs `fasta_txome`).

`tools/validate_genome.py` catches the rest on the PR: an unknown store slug (the
error lists the valid ones), an unknown tier, and a typo'd asset name in `add:` or
`drop:`.

## Note: no `# GENERATED` comment in samples.csv

`samples.csv` deliberately carries no leading comment line. peppy reads it with a
bare `pandas.read_csv` (no comment character), so a `#` first line would be
parsed as the header and corrupt the queue. Provenance lives here and in the
genome YAMLs; the `run_builds.sh` guard is what enforces "generated only".
