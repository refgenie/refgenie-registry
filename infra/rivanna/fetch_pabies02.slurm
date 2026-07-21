#!/bin/bash
#SBATCH --job-name=fetch-pabies02
#SBATCH --partition=standard
#SBATCH --account=shefflab
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=12:00:00
#SBATCH --output=/project/shefflab/brickyard/results_pipeline/refgenie/build_reports/fetch_pabies02_%j.log

# One-off: stage the Picea abies Pabies02 assembly for the plantref store.
#
# Replaces picea_abies_ConGenIE_v1_0 (10.25M contigs, 12.3 Gbp, median 317 bp),
# which was the single biggest source of bloat in plantref. Pabies02 is
# chromosome-level: 12 chromosomes + 882 unplaced contigs = 894 sequences,
# ~15.9 Gbp -- more genome in four orders of magnitude fewer records.
#
#   ENA assembly GCA_964035815.1, project PRJEB69221
#   Umea Plant Science Centre, PacBio HiFi / hifiasm 0.12, submitted 2024-05-08
#
# Not in NCBI datasets (NCBI still lists Pabies01, 11.3M scaffolds, as the Picea
# reference), so this pulls from ENA directly: the 12 chromosome records
# individually, plus the unplaced contigs as WGS set CAXIVX01.

set -uo pipefail

source ~/code/refgenie-registry/infra/rivanna/env.sh

DEST="$REFGETSTORE_FASTA/plantref/picea_abies/upsc"
# 'upsc' (the data producer), not 'ena' (the portal): the plantref README's rule
# is that the producing authority wins.
mkdir -p "$DEST"
cd "$DEST" || exit 1

echo "===== fetch Pabies02  $(date) ====="
echo "  dest: $DEST"

if [ -f Pabies02.fa.gz ]; then
  echo "  Pabies02.fa.gz already present -- refusing to overwrite. Remove it first."
  exit 1
fi

# --- chromosomes: OZ038344.1 .. OZ038355.1 -------------------------------
# Fetched one at a time; the ENA browser API has no bulk-by-accession endpoint
# for these. Each is up to ~1.7 Gbp, so this is the slow half.
rm -f chromosomes.raw.fa.gz
for acc in OZ038344 OZ038345 OZ038346 OZ038347 OZ038348 OZ038349 \
           OZ038350 OZ038351 OZ038352 OZ038353 OZ038354 OZ038355; do
  echo "  fetching ${acc}.1 ... $(date +%H:%M:%S)"
  # No --retry-all-errors: Rivanna's curl is 7.61.1 and that flag landed in
  # 7.71.0. -f plus --retry still covers transient HTTP 5xx and network errors.
  curl -sSfL --retry 5 --retry-delay 30 \
    "https://www.ebi.ac.uk/ena/browser/api/fasta/${acc}.1?download=false" \
    | gzip -c >> chromosomes.raw.fa.gz
  if [ "${PIPESTATUS[0]}" -ne 0 ]; then
    echo "  FAILED on ${acc}" >&2
    exit 1
  fi
done

# --- unplaced contigs: WGS set CAXIVX01 ----------------------------------
echo "  fetching CAXIVX01 WGS set ... $(date +%H:%M:%S)"
curl -sSfL --retry 5 --retry-delay 30 \
  -o CAXIVX01.raw.fasta.gz \
  https://ftp.ebi.ac.uk/pub/databases/ena/wgs/public/cax/CAXIVX01.fasta.gz || exit 1

# --- normalize headers ---------------------------------------------------
# ENA serves these as
#   >ENA|OZ038344|OZ038344.1 Picea abies genome assembly, chromosome: 01
#   >ENA|CAXIVX010000001|CAXIVX010000001.1 ... contig: PA_chr01_sUL001
# Strip to the bare submitter name (01..12, PA_chr01_sUL001-style).
#
# This is not cosmetic. Sequence digests depend only on sequence content, but the
# COLLECTION digest incorporates the names digest -- header style permanently
# fixes this collection's identity. No other plantref FASTA has a pipe in its
# headers, and pipe-delimited names would also poison
# `build_aliases.py --seq-strategy header_names`, which would register the whole
# string as a sequence alias.
echo "  normalizing headers ... $(date +%H:%M:%S)"
zcat chromosomes.raw.fa.gz CAXIVX01.raw.fasta.gz \
  | sed -E 's/^>.*(chromosome|contig): (.+)$/>\2/' \
  | gzip -c > Pabies02.fa.gz.partial || exit 1
mv Pabies02.fa.gz.partial Pabies02.fa.gz

# --- verify --------------------------------------------------------------
echo "  verifying ... $(date +%H:%M:%S)"
n_seq=$(zcat Pabies02.fa.gz | grep -c '^>')
n_bad=$(zcat Pabies02.fa.gz | grep '^>' | grep -cE '\||  *')
echo "  headers: $n_seq (expect 894)"
echo "  malformed (pipe or space): $n_bad (expect 0)"

if [ "$n_seq" -ne 894 ] || [ "$n_bad" -ne 0 ]; then
  echo "  VERIFICATION FAILED -- leaving raw files in place for inspection" >&2
  mv Pabies02.fa.gz Pabies02.fa.gz.rejected
  exit 1
fi

rm -f chromosomes.raw.fa.gz CAXIVX01.raw.fasta.gz
ls -l Pabies02.fa.gz
echo "ALL DONE $(date)"
