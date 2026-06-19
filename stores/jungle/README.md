# jungle

The reference genome jungle paper dataset. Originally built by the `refgetstore-build` pipeline (in `analysis/refgetstore-build/`).

## Sources

`sources.csv` contains 96 FASTA entries extracted from the [PEPHub project](https://pephub-api.databio.org/api/v1/projects/donaldcampbelljr/human_mouse_fasta_brickyard/samples?tag=default) that was used to curate the brickyard FASTA collection. Covers 60 Homo sapiens and 36 Mus musculus assemblies from 10 sources (UCSC, Ensembl, iGenomes, NCBI, GENCODE, refgenie, ENA, DDBJ, misc, Broad).

Note: The full jungle store may contain additional FASTAs discovered by the `refgetstore-build` inventory walker (`src/01_inventory/inventory_genomes.py`) that were not in the PEP. To get a complete inventory, run the walker on the brickyard filesystem (`$BRICK_ROOT`).
