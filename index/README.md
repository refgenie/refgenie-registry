# index/

Auto-generated asset index. **Do not edit by hand.**

`manifest.yaml` is a roll-up of every built asset, regenerated from the
`index/<genome>/<recipe>.yaml` entries. It is produced and committed by CI —
the [Regenerate Manifest](../.github/workflows/regenerate-manifest.yaml)
workflow (weekly cron + `workflow_dispatch`), which runs
[`.github/scripts/regenerate-manifest.py`](../.github/scripts/regenerate-manifest.py).

The script preserves the existing `updated` timestamp when `assets` /
`total_assets` are unchanged, so quiet weeks produce no commit. Manual edits
here will be overwritten on the next regeneration.

To regenerate locally:

```bash
python3 .github/scripts/regenerate-manifest.py --index-dir index
```
