# Omni-Grid Dashboard

This directory contains the static dashboard served alongside FastAPI in the
hosted demo. The dashboard is publicly accessible without an account and uses
the same-origin API; it does not load an exported report as a fallback.

## Features
- **Live API:** **Run Synthetic Demo** calls `POST /demo`; GeoJSON uploads call `POST /triage`. Both render the validated `Report` returned by the canonical pipeline.
- **Explicit failure states:** Loading, empty, validation, rate-limit, service, and timeout states are shown. API failures never switch to stale JSON.
- **Intentional startup state:** The dashboard shell and map appear immediately with a welcome prompt. Processing begins only after a user runs the demo or uploads GeoJSON; failed requests provide a retry action.
- **Zero-Dependency Core:** Pure vanilla HTML5/ES6 without heavy frameworks.
- **Interactive Leaflet Map:** Renders API-returned complaint markers and hotspot circle overlays with OpenStreetMap tiles.
- **Offline report inspection:** `web/demo/report.json` remains an exporter-produced artifact and is not used by the hosted dashboard.
- **Proof-of-Fix Inspection:** Displays available deterministic verification checks. Uploaded complaints do not include media files, so uploaded media references cannot be inspected.
- **Summary Claims:** Surfaces validated narrative claims with clickable citation pills.

## Directory Structure
```
web/
  index.html            # Primary landing page entry point
  README.md             # Local setup and workflow guide
  css/
    style.css           # Responsive layout and theme
  js/
    app.js              # Map integration, filtering, state, and DOM-safe rendering
  demo/
    report.json         # Real exported report data (hotspots, verdicts, claims, complaints)
    media/              # Local evidence photos for inspection
```

The exported `demo/report.json` and local media are for the separate offline
exporter workflow. The served dashboard does not load these files as a startup
report or as a fallback after an API error.

## Run the hosted-style dashboard locally

Install API and pipeline dependencies and start the combined service from the
repository root:

```powershell
python -m pip install -e ".[api,pipeline]"
$env:LLM_MOCK = "true"
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`, run the synthetic demo, or select a `.geojson`
or `.json` file. The public API runs in deterministic mock mode and accepts at
most 2 MiB and 1,000 complaint features. The displayed data is synthetic and
does not represent live municipal records.

## Refreshing Demo Data

The offline exporter remains available separately to refresh
`web/demo/report.json` and its referenced media:

```bash
python scripts/export_demo_report.py
```

The exporter uses the canonical pipeline in deterministic mock mode by default
and does not contact an LLM service or claim model inference. It emits one
verdict for every closed complaint, including inconclusive results when
evidence is missing. It copies only safely-resolved media referenced by the
report, then validates the serialized output against the shared `Report`
schema and verifies that every media reference resolves.

This script:
1. Ingests `data/synthetic/complaints.geojson`.
2. Extracts available evidence metadata and runs DBSCAN clustering to derive
   `Hotspot` records with explainable severity.
3. Runs deterministic proof-of-fix checks for every closed complaint.
4. Generates citation- and number-validated template claims in mock mode.
5. Saves the validated report to `web/demo/report.json` and copies only its
   approved, referenced media to `web/demo/media/`.
