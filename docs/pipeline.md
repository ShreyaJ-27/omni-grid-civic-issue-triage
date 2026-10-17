# End-to-end triage pipeline

`core/omnigrid/pipeline.py` exposes `run_pipeline(source, ...)`, the shared
implementation used by `omnigrid triage` and the web demo exporter. `source`
can be a path to a Complaint JSON object/array, a GeoJSON Feature, a GeoJSON
FeatureCollection, or already-decoded complaint data.

```powershell
$env:LLM_MOCK = "true"
py -3.11 -m omnigrid.cli triage data\synthetic\complaints.geojson
make export-demo
```

The CLI writes only the schema-valid `Report` JSON to stdout. Diagnostics go to
stderr and invalid complaint input exits non-zero. Relative media paths from a
repository input resolve under the repository root; external JSON inputs use
their parent directory unless `--media-root` is supplied. Absolute paths and
symlinks must remain inside that media root. Missing, unreadable, unsupported,
and unsafe media are skipped and counted; no evidence is inferred from
unreadable files.

The pipeline validates complaints, extracts image/video evidence metadata,
clusters complaints using the shared explainable severity implementation,
applies deterministic closure checks before optional vision checks, validates
summary citations and numbers, and validates the assembled `Report`. Failed
deterministic checks remain suspect even if a vision assessment passes. Missing
or ambiguous evidence yields an inconclusive result. A live provider failure
does not invent a successful check or score.

Set `LLM_MOCK=true` for CI and offline demos. In this mode visual inference is
skipped, damage scores are not fabricated, and summaries use deterministic
template claims; report statistics explicitly label mock mode and the template
fallback. In live mode, provider/model use depends on configured services and
credentials. No live model behavior is implied by mock-mode output.

`Report.stats` includes complaint/hotspot/evidence totals, skipped media
categories, damage-scoring and vision status, verdict categories, claim
kept/dropped counts, model mode, and per-stage status. `stages_skipped`
distinguishes intentionally skipped mock or vision work; `stage_status`
distinguishes completed stages, fallbacks, and failures.

The exporter writes `web/demo/report.json` and copies only referenced media
whose resolved paths are within the allowed media root. It validates the
exported report and verifies every remaining media reference points to a
copied asset. The frontend consumes the same Report contract.
