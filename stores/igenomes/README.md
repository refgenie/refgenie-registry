# igenomes

AWS iGenomes — pre-built reference genomes used by nf-core and Illumina pipelines.

## Contents

- 31 organisms, 130 builds, 123 whole-genome FASTAs
- Sources: Ensembl, NCBI, UCSC, GATK, Illumina
- S3 bucket: `s3://ngi-igenomes/igenomes/`

## Sources

TODO: Parse from [AWS-iGenomes manifest](https://github.com/ewels/AWS-iGenomes/blob/master/ngi-igenomes_file_manifest.txt). Grep for `WholeGenomeFasta/genome.fa` to get FASTA paths.

## References

- [AWS-iGenomes GitHub](https://github.com/ewels/AWS-iGenomes)
- [nf-core igenomes.config](https://github.com/nf-core/rnaseq/blob/master/conf/igenomes.config)
