#!/bin/bash
#SBATCH --job-name=verify-plantref
#SBATCH --output=/project/shefflab/brickyard/results_pipeline/refgenie/build_reports/verify_plantref_%j.log
#SBATCH --partition=standard
#SBATCH --account=shefflab
#SBATCH --time=01:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=16G
#
# Gate for the plantref prune: assert the collections that were supposed to
# SURVIVE the removal are still byte-exact, and that Pabies02 landed intact.
#
# This is the check that stands between the prune and the irreversible
# `aws s3 --delete` sync. Orphan-sequence GC is dangerous precisely because it
# can reclaim a sequence still referenced by a collection meant to survive, and
# collection counts cannot see that -- only re-reading a survivor and
# re-totalling its bases can. Do NOT sync to S3 unless this exits 0.
#
# Memory is sized from measured MaxRSS (824 MB on the 2026-07-21 run), not
# guessed. Rivanna bills memory as its own line item.
#
# Usage: sbatch infra/rivanna/verify_plantref.slurm

set -uo pipefail
source ~/code/refgenie-registry/infra/rivanna/env.sh
cd ~/code/refgenie-registry/stores || exit 1

# Survivors of the wheat and barley removals. These three share sequences with
# the collections that were deleted, so they are the ones that would show damage.
EXPECT_wheat_survivor="triticum_aestivum_IWGSC1_1=22:14547261565"
EXPECT_barley_survivor="hordeum_vulgare_IBC_PGSB_v2=10:4834432680"
EXPECT_barley_survivor2="hordeum_vulgare_Ensembl_Genomes_ASM32608v1=19705:4045300851"

# Pabies02, the replacement for the shredded spruce assembly.
#
# NOTE the base total. An earlier run of this check FAILED against
# 894:15935069060 and that expectation was WRONG, not the store. 15,935,069,060
# is the CHROMOSOME-ONLY total -- the 12 records OZ038344.1-OZ038355.1, which is
# all ENA lists for GCA_964035815.1. But fetch_pabies02.slurm deliberately
# assembles the FASTA from those 12 chromosomes PLUS the CAXIVX01 WGS set (882
# unplaced contigs, 1,748,341,117 bp), which is where the 894 sequence count
# comes from in the first place. The pairing of a 894-sequence count with a
# 12-sequence base total was the bug. Full assembly total:
EXPECT_pabies="picea_abies_Pabies02=894:17683410177"

echo "===== verify plantref  $(date) ====="

python -u verify_collections.py plantref \
  --expect "$EXPECT_wheat_survivor" \
  --expect "$EXPECT_barley_survivor" \
  --expect "$EXPECT_barley_survivor2" \
  --expect "$EXPECT_pabies"
rc=$?

echo "  exit $rc  $(date)"
if [ "$rc" -ne 0 ]; then
  echo "VERIFY FAILED -- do NOT sync to S3."
fi
exit $rc
