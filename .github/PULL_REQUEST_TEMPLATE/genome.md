## New Genome: [assembly name]

**Organism:** [scientific name]
**Assembly source:** [NCBI / Ensembl / UCSC / other]

### Checklist

- [ ] YAML file is at `genomes/<organism>/<assembly>.yaml`
- [ ] Passes schema validation (`python tools/validate_genome.py <file>`)
- [ ] `name` matches the filename (without `.yaml`)
- [ ] `description` is present
- [ ] At least one `fasta.sources[].url` is from an authoritative source
- [ ] `fasta.checksum.sha256` is the hash of the uncompressed FASTA (or the
      `compute_on_registration` sentinel)
- [ ] `organism.scientific_name` uses standard binomial nomenclature
- [ ] `organism.taxon_id` (NCBI Taxonomy ID) is present
- [ ] No alias conflicts with existing genomes
- [ ] (optional) FHR provenance added under the `fhr:` block (license, DOI,
      authors with ORCIDs, ...)

### Notes

<!-- Any additional context for reviewers -->
