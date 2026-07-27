# vgp

VGP (Vertebrate Genomes Project) vertebrate genomes. Originally split from the jungle store.

## Sources

605 genome assemblies, the full set listed by the UCSC VGP assembly hub
(<https://hgdownload.soe.ucsc.edu/hubs/VGP/>), which is where the original
download list came from. The set is VGP and affiliated efforts (Bat1K, Wellcome
Sanger Institute, etc.), so it includes a handful of assemblies that GenArk
classifies under non-vertebrate clades -- that is expected; the VGP hub listing,
not a clade filter, defines membership here.

FASTAs were fetched from UCSC GenArk hub URLs
(`https://hgdownload.soe.ucsc.edu/hubs/GC[AF]/.../<accession>.fa.gz`) via
`wget -i files.txt`, and are cached at `$REFGETSTORE_FASTA/vgp/fasta/`.

Provenance for each row comes from the sample table stored beside those FASTAs,
`$REFGETSTORE_FASTA/vgp/databio_vertebrates_refgenie_default/sample_table.csv`,
which carries the tolID, scientific name, NCBI accession, BioProject, assembly
date and clade for every assembly.

## History

Until 2026-07-27 this file declared only 531 of the 605 assemblies we hold, and
attributed the shortfall to 80 collections that "lack FHR metadata" and were
"likely duplicates or alternate haplotypes". That explanation was wrong on both
counts: the 74 undeclared assemblies are all distinct accessions, none is a
re-version of an assembly already declared, and their provenance was never
missing -- it was in the sample table beside the FASTAs the whole time. How the
531-row file came to be built that way was not determined; both it and this
README arrived in the repo's initial commit with no intervening history.
`sources.csv` now declares all 605.
