# Omni-Grid Civic Issue Triage - Benchmark Evaluation Report

## 1. Run Metadata
- **Evaluation Date**: 2026-10-18 06:24:04 UTC
- **Git Commit Hash**: `9f9d055`
- **Dataset Input**: `complaints.geojson` (500 complaints: 400 open, 100 closed)
- **Media References**: 15 files (15 valid evidence items decoded)
- **Execution Mode**: mock model (100% deterministic & offline reproducible)

---

## 2. Executive Benchmark Results

| Benchmark Category | Metric | Result | Target / Ground Truth | Status |
|--------------------|--------|--------|-----------------------|--------|
| **Hotspot Clustering** | Complaint Recall | **100.0%** (68/68) | 5 planted hotspots (68 complaints) | **PASS** |
| **Hotspot Clustering** | Cluster Purity | **72.1%** (49/68) | Dominant category per cluster | **PASS** |
| **Duplicate Evidence** | Duplicate Recall | **100.0%** (1/1) | 1 planted duplicate pair (2 tickets) | **PASS** |
| **Proof-of-Fix Verifier** | Suspect Precision | **100.0%** (5/5) | 5 flagged, 0 false alarms | **PASS** |
| **Proof-of-Fix Verifier** | Suspect Recall | **100.0%** (5/5) | 5 planted suspicious closures | **PASS** |
| **Proof-of-Fix Verifier** | F1-Score | **1.000** | Balanced quality target >= 0.90 | **PASS** |
| **Summary Citations** | Citation Validity Rate | **100.0%** (1/1) | 100% cited complaint IDs genuinely exist | **PASS** |
| **Pipeline Latency** | End-to-End Latency | **0.46 s** | Sub-minute automated triage | **PASS** |

---

## 3. Pipeline Latency Breakdown

| Pipeline Stage | Wall-Clock Runtime (s) | Description |
|----------------|------------------------|-------------|
| **Ingest** | 0.005 | GeoJSON parsing, schema validation |
| **Evidence Extraction** | 0.065 | EXIF GPS, timestamp, pHash computation |
| **Spatial Clustering** | 0.004 | Haversine DBSCAN & severity scoring |
| **Closure Verification** | 0.133 | Multi-layered deterministic checks |
| **Summary Generation** | 0.000 | Numeric & citation-checked claim synthesis |
| **Total End-to-End** | **0.460** | Full pipeline end-to-end execution |

---

## 4. Discrepancy & Diagnostic Analysis

### A. Verification Classification
- **True Positives (5):** `complaint-003, complaint-017, complaint-018, complaint-030, complaint-045`
- **False Positives:** None (0)
- **False Negatives:** None (0)

### B. Planted Suspicious Closures Ground Truth Audit
| Complaint ID | Planted Violation Type | Verdict | Detection Mechanism |
|--------------|------------------------|---------|---------------------|
| `complaint-003` | Mismatched GPS (>23 km away) | **suspect** | `gps_geofence` failed (23.1 km > 5.0 km threshold) |
| `complaint-017` | Mismatched GPS (>21 km away) | **suspect** | `gps_geofence` failed (21.5 km > 5.0 km threshold) |
| `complaint-018` | Inconsistent Defect (Streetlight closed with pothole) | **suspect** | `category_consistency` failed (streetlight vs pothole) |
| `complaint-030` | Duplicate Evidence (Reused photo with complaint-045) | **suspect** | `duplicate_evidence` failed (pHash distance 0 <= 5) |
| `complaint-045` | Duplicate Evidence (Reused photo with complaint-030) | **suspect** | `duplicate_evidence` failed (pHash distance 0 <= 5) |

### C. Citation Integrity Verification
- **Total Cited IDs**: 1
- **Valid Citations**: 1
- **Invalid / Hallucinated Citations**: 0 (None)

---
*Report generated automatically by `eval.run_eval` benchmark harness.*
