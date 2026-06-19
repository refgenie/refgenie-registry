## New Genome: [assembly name]

**Organism:** [scientific name]
**Assembly source:** [NCBI / Ensembl / UCSC / other]

### Checklist

- [ ] YAML file is at `genomes/<organism>/<assembly>.yaml`
- [ ] Passes schema validation (`python tools/validate_genome.py <file>`)
- [ ] `name` matches the filename (without `.yaml`)
- [ ] `fasta.primary_url` is from an authoritative source
- [ ] `fasta.checksum.sha256` is the hash of the uncompressed FASTA
- [ ] `organism.scientific_name` uses standard binomial nomenclature
- [ ] No alias conflicts with existing genomes

### Notes

<!-- Any additional context for reviewers -->
