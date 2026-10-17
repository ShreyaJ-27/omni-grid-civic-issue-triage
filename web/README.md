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

The exporter uses deterministic mock mode by default and does not contact an
LLM service. It computes available photo damage scores before hotspot
clustering and emits one verdict for every closed complaint, including
inconclusive results when evidence is missing. The report is validated against
the shared `Report` schema before it is written. Running the exporter with a
hosted model configuration is not supported by this default demo command.

This script:
1. Ingests `data/synthetic/complaints.geojson`.
2. Runs DBSCAN clustering to derive `Hotspot` records.
3. Evaluates proof-of-fix closure verification checks to produce `Verdict` records.
4. Generates structured summary claims and statistics.
5. Saves the validated report to `web/demo/report.json` and copies images to `web/demo/media/`.
