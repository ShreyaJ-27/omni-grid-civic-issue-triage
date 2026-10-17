# Omni-Grid Web Landing Page & Interactive Map

This directory contains the static web frontend for Omni-Grid civic issue triage.

## Features
- **Data-Driven:** Loads real pipeline output from `demo/report.json` via standard browser `fetch()`.
- **Zero-Dependency Core:** Pure vanilla HTML5/ES6 without heavy frameworks.
- **Interactive Leaflet Map:** Renders complaint markers and hotspot circle overlays with OpenStreetMap tiles.
- **Fail-Safe Fallback:** If `demo/report.json` is missing or inaccessible (e.g. running via `file://`), the page automatically falls back to an embedded sample dataset with a clear notification banner.
- **Proof-of-Fix Inspection:** Displays deterministic verification checks (GPS geofence, duplicate pHash, chronological timestamps, defect consistency) and evidence photo thumbnails.
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

## Running Locally

To serve the web interface locally, run Python's built-in HTTP server from the repository root:

```bash
python -m http.server 8000 --directory web
```

Then open your browser to:
[http://localhost:8000](http://localhost:8000)

*(Note: Browsers block `fetch()` requests on the `file://` protocol due to CORS security policies; serving via a local HTTP server allows loading `demo/report.json` dynamically).*

## Refreshing Demo Data

To regenerate `web/demo/report.json` and copy updated media assets from `data/synthetic/`:

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
