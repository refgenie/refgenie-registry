# legacy store

The genomes served by the legacy `refgenomes.databio.org` refgenie server.

These are the 19 collections the old server hosted, and they are a coherent set:
one server's published reference genomes, addressed by the short names
(`hg38`, `mm10`, `human_alu`, ...) that thousands of pipeline configs still use.
`tools/digest_map.json` maps each one's legacy hex refgenie digest to its GA4GH
seqcol digest.

Several of them (`hg18`, `hg19`, `hg38`, `hg38_noalt_decoy`) are also present in
other stores under provider-specific names, and `rCRSd` is also in `decoys`. That
is fine -- a sequence collection is content-addressed, so the same digest can be
held by more than one store. What this store adds is the legacy *identity*: the
name the old server published it under.

## Sources

`sources.csv` points at `$REFGETSTORE_FASTA/legacy/<name>.fa.gz`, the staged
clean-extension symlinks to the legacy server's own FASTA files (targets under
`/project/shefflab/www/refgenie_refgenomes.databio.org/`). Those same files are
what `pep/config.yaml` derives each queued genome's `fasta_file_path` from, so
the store and the asset builds ingest byte-identical input.

## Registry link

Every genome YAML in `genomes/` whose `build.store` is `legacy` has its sequence
here. `build/sync_stores.py` verifies that link.
