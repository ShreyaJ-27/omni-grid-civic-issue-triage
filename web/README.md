# Omni-Grid Civic Field Desk

This directory contains the civic field desk dashboard served alongside FastAPI in the
hosted application. The dashboard is publicly accessible without authentication, integrates
directly with the same-origin live API, and adheres to strict transparency and verifiable-evidence
standards.

## Core Architecture & Features

### 1. Civic Field Desk Interface
- **Civic Design Palette:** Warm cream background, deep forest green accents, refined typography, and high-contrast status tags.
- **Persistent Low-Light Toggle:** One-click "Night view" toggle saved to `localStorage` (`data-theme="night"`).
- **Tabbed Civic Navigation:** Overview, City Map, Issues, Hotspots, Proof of Fix, Briefings & Reports, and Data Ingestion.
- **Interactive Summary Cards:** Keyboard-accessible summary cards with filter shortcuts:
  - *Issues Tracked* (`#stat-total-complaints`) → resets filters or launches demo.
  - *Active Hotspots* (`#stat-total-hotspots`) → jumps to hotspot panel.
  - *Needs a Second Look* (`#stat-suspect-closures`) → filters suspect closures.
  - *Proof Checked* (`#stat-verified-closures`) → filters verified closures.
- **Responsive Layout:** Adaptive desktop split-screen (Leaflet map + triage panels) with graceful mobile column stacking.

### 2. Live API Connection & Failure Resilience
- **Real Sample & Synthetic Demos:** Dedicated controls for the sampled Bengaluru demo (`POST /demo`) and synthetic scenarios (`POST /demo/synthetic`).
- **Preserved Report on Failure:** If an API request encounters an error after a report is already loaded, the dashboard preserves and continues displaying the last successful report while surfacing an explicit banner with a **Retry** action. It never silently reverts to stale local JSON.
- **Explicit States:** Loading spinner with cold-start timeout notices (up to 3 minutes), empty states, rate-limit warnings (429), payload size limit alerts (413), and inline error details.

### 3. Preview-First Civic Data Ingestion
- **Multi-Format Support:** Ingestion preview for CSV, JSON arrays / wrappers, GeoJSON Point features, and structured PDF complaint tables.
- **Editable CSV Field Mapping:** Visual column mapper with auto-detection for `id`, `category`, `status`, `text`, `created_at`, `lat`, and `lon`.
- **Validation & Limitations:** Row-by-row validation counts, missing-coordinate warnings, and explicit notice that uploaded records do not include media files (media references are noted, not uploaded).
- **PDF Tables & OCR Prerequisites:** Conservative tabular extraction for digital PDFs with required review consent. Scanned-PDF OCR is not supported in the browser interface; prerequisites for CLI OCR (`python-poppler`, `pytesseract`, `Tesseract`) are documented clearly.
- **Normalization to Shared Model:** Validated records are packaged into a standard GeoJSON FeatureCollection and triaged via `POST /triage`.

### 4. AI Closure Auditor Simulator
- **Interactive Scenarios:**
  1. *Geofence Spoofing:* Closure photo location GPS coordinates located 1.3 km away from complaint site.
  2. *Defect-class Mismatch:* Waterlogging complaint closed with a dry road photograph showing no drainage intervention.
  3. *Reused Evidence / Perceptual-Hash Match:* Closure photo hash matches an earlier complaint closure in the dataset.
  4. *Genuine Repair:* Verified repair meeting location, timestamp, category match, and hash uniqueness criteria.
- **Deterministic Checks & Guidance:** Displays individual verification checks (GPS geofence, timestamp sequence, defect class match, perceptual hash), measured values, overall verdict, confidence score, and human-review guidance.
- **Accurate Perceptual-Hash Disclaimer:** Explains that the perceptual-hash check compares image fingerprints against other records within the dataset; it does not perform public web or stock-photo searches.

### 5. Accessibility & Utility Controls
- **Speech Synthesis Briefing:** Integrated browser speech synthesis (`window.speechSynthesis`) to listen to city briefings with accessible play/stop controls.
- **JSON Report Export:** Download current active report as formatted JSON.
- **Keyboard Navigation:** Full arrow-key and Enter/Space navigation for hotspot cards, complaint lists, and modal dialogs with focus trapping.

---

## Directory Structure
```
web/
  index.html            # Primary civic field desk landing page
  README.md             # Architecture, feature documentation, and run guide
  css/
    style.css           # Civic field desk stylesheet (light & night mode tokens)
  js/
    app.js              # State management, API calls, map, ingestion, auditor
  demo/
    report.json         # Offline export artifact (used for standalone CLI verification)
    media/              # Offline reference media
```

---

## Local Development & Running

### Starting the Live API & Field Desk
From the repository root:

```powershell
python -m pip install -e ".[api,pipeline]"
$env:LLM_MOCK = "true"
$env:PYTHONPATH = "."
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000` in any modern web browser.

### Running Test Suite
Execute the web acceptance and regression tests:

```powershell
$env:PYTHONPATH = "."
pytest tests/test_web.py -v
```

Execute the full project test suite:

```powershell
$env:PYTHONPATH = "."
pytest
```

Run code formatting and lint checks:

```powershell
ruff check .
```

---

## Render Deployment

The dashboard and FastAPI service are configured for automated deployment to Render via `render.yaml`:

- **Runtime:** Python 3.11
- **Build Command:** `python -m pip install -e ".[api,pipeline]"`
- **Start Command:** `uvicorn api.main:app --host 0.0.0.0 --port $PORT`
- **Health Check:** `GET /health`
- **Static Assets:** Mounted directly from `web/` at `/` with `html=True`.
