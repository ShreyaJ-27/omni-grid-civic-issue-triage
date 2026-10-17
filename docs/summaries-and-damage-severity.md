# Structured Summaries and Vision Damage Severity

This document details the architecture, validation mechanisms, and interfaces for:
1. **Structured Summary Generation & Citation/Numeric Validation (Issue #13)** in `core/omnigrid/summarize.py`
2. **Vision Damage-Severity Scoring for Complaint Photos (Issue #14)** in `core/omnigrid/damage.py`

---

## 1. Structured Summaries & Validation (`core/omnigrid/summarize.py`)

### Overview
Civic triage reports require machine-readable, concise summaries for municipal officials. To prevent hallucination:
- **No arithmetic by the LLM:** All counts, scores, and metrics are computed beforehand and supplied as JSON facts.
- **Strict Citation Validation:** Every claim must cite only known, valid entity IDs (e.g. `hotspot-...`, complaint IDs). Claims citing unknown IDs are rejected.
- **Strict Numeric Claim Validation:** Standalone numeric figures in each claim (counts, severities, percentages) are verified against the pre-computed facts. Claims asserting unsupported numbers are dropped. Substring matches are avoided to prevent spurious approvals.
- **Retry & Deterministic Fallback:** If the LLM generates malformed JSON or invalid schema, it is retried exactly once. If both attempts fail or all generated claims are rejected by validation, a safe deterministic template summary is produced directly from the facts.

### Interface
```python
from core.omnigrid.summarize import generate_summary

report, diagnostics = generate_summary(
    hotspots=hotspots,
    verdicts=verdicts,
    llm_client=client,       # Provider-agnostic LLM client (mockable)
    extra_facts={"total_complaints": 500},
)

# Output conforms to shared schemas
print(f"Claims kept: {diagnostics['kept_claim_count']}")
print(f"Claims dropped: {diagnostics['dropped_claim_count']}")
print(f"Fallback triggered: {diagnostics['fallback_triggered']}")
```

---

## 2. Vision Damage-Severity Scoring (`core/omnigrid/damage.py`)

### Scale Definition
Visible damage severity is scored on a normalized continuous scale from `0.0` to `1.0`:
- **`0.0` — No visible damage:** Clean infrastructure, cosmetic anomalies without structural damage.
- **`0.25` — Minor damage:** Hairline asphalt cracks, superficial scuffs, minor littering.
- **`0.50` — Moderate damage:** Shallow pothole, partial drain blockage, tilting sign.
- **`0.75` — Severe damage:** Deep pothole, flooded roadway section, damaged electrical enclosure.
- **`1.00` — Critical / Catastrophic damage:** Impassable road crater, active high-voltage hazard, collapsed structure.

### Estimate vs. Objective Fact Disclaimer
> [!IMPORTANT]
> **Advisory Prioritization Only:**
> Vision model damage scores are heuristic estimates intended strictly for operational prioritization and triage.
> They are **not** authoritative civil engineering structural assessments, nor are they definitive legal evidence of fraud or contractor failure.
> Vision scores must never override deterministic verification checks (e.g. GPS geofence breaches or timestamp inconsistencies).

### Caching Architecture
Scores are persistently cached in `data/cache/damage_cache.json` using SHA-256 keys:
$$\text{cache\_key} = \text{SHA256}(\text{prompt\_version} + \text{model\_name} + \text{image\_bytes})$$
- Modifying image bytes or changing the prompt/model version immediately invalidates stale cache entries.
- Cache read/write operations fail safely and will never abort pipeline execution on disk or permission errors.

### Integration with Clustering Severity Formula
In `core/omnigrid/cluster.py`, the explainable composite severity score follows:
$$\text{Severity} = 10 \times \left(0.45 \cdot S_{\text{count}} + 0.30 \cdot S_{\text{vlm}} + 0.15 \cdot S_{\text{recency}} + 0.10 \cdot S_{\text{injury}}\right)$$

Where $S_{\text{vlm}}$ is the arithmetic mean of the visual damage scores for complaints in the cluster.
- When damage scores are unavailable, $S_{\text{vlm}}$ falls back safely to `0.0`.
- The interface accepts an optional `damage_scores: dict[str, float]` mapping in `cluster_complaints()` and `compute_severity_score()`.
