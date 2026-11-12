# igenomes

AWS iGenomes, pre-built reference genomes used by nf-core and Illumina pipelines.

## Contents

- 31 organisms, 130 builds, 123 whole-genome FASTAs
- Sources: Ensembl, NCBI, UCSC, GATK, Illumina
- S3 bucket: `s3://ngi-igenomes/igenomes/`

## Names

A row's `name` is an alias, so it is a plain name, never the iGenomes path:
`<build>-igenomes-<source>`, such as `sacCer3-igenomes-ucsc`. A build token
that names no organism (`build3.1`, a date, `EF2`, `RTA`) or appears under
several organisms gets the organism folder in front:
`saccharomyces_cerevisiae-build3.1-igenomes-ncbi`. A row with a genome YAML
takes the YAML's name, and a file jungle also holds takes jungle's name. The
iGenomes path goes in the `synonyms` column, so search still finds it. For the
NCBI `build*` folders, `genome_assembly` is the assembly the file really holds.

## Sources

TODO: Parse from [AWS-iGenomes manifest](https://github.com/ewels/AWS-iGenomes/blob/master/ngi-igenomes_file_manifest.txt). Grep for `WholeGenomeFasta/genome.fa` to get FASTA paths.

## References

- [AWS-iGenomes GitHub](https://github.com/ewels/AWS-iGenomes)
- [nf-core igenomes.config](https://github.com/nf-core/rnaseq/blob/master/conf/igenomes.config)
