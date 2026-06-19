# infra/rivanna/

Rivanna (UVA HPC) execution layer for building and publishing RefgetStores.
**Not needed to use the registry** — this is the operator-side machinery that
runs the `stores/` build pipeline on the cluster. It is isolated here so the
rest of the repo reads cleanly for external contributors.

| File | Role |
|------|------|
| `env.sh` | Exports `REFGETSTORE_BASE` (Rivanna brickyard) and `REFGETSTORE_S3`. Source before building. |
| `build_store.slurm` | SLURM job (8 CPU / 32 GB) that runs `stores/build.py <store>`. |
| `download_igenomes.slurm` | SLURM job to stage iGenomes FASTAs into the download cache. |
| `download_salmon_txomes.slurm` | SLURM job to stage Salmon transcriptome FASTAs. |
| `sync_to_s3.slurm` | SLURM job that `aws s3 sync`s built stores to S3 (`refgenie` profile). |
| `yoke.toml` | Yoke session config (`remote = "riva"`) for driving the above from a laptop. |

## Assumptions

These scripts are specific to the `shefflab` allocation on Rivanna and assume:

- The repo is checked out at `~/code/refgenie-registry` (SLURM scripts `cd` there by absolute path).
- A Python venv at `~/envs/refgetstore-analysis` with `refget`/`gtars` installed.
- The Lmod stack `gcc/11.4.0 openmpi/4.1.4 python/3.11.4`.
- An `~/.aws` `refgenie` profile (authenticates as `RefgenieDataBot`) for S3 writes.

## Usage

```bash
# Build a store (submit from the repo root):
sbatch --job-name=build-vgp infra/rivanna/build_store.slurm vgp

# Or drive it from a laptop via yoke (run from this directory):
cd infra/rivanna
yoke -c 'sbatch build_store.slurm vgp' -s refgenie-registry
```

The build/validation tooling these jobs invoke lives in [`../../stores/`](../../stores/);
see [`stores/README.md`](../../stores/README.md) for the build/alias/validate workflow.
