# Omni-Grid: Implementation Plan v2 (build plan)

Repo: `ShreyaJ-27/omni-grid-civic-issue-triage` | Track 2, problem statement **O3: Trustworthy and Verifiable AI** | Team of 3 | Build window 13 to 18 Oct 2026

> **Historical planning document:** the dates, owners, goals, and technology
> options below record the original hackathon plan, not current delivery
> status. Proposed items such as CSV/ward-polygon ingestion and hosting are
> not claims that those features were implemented. For verified capabilities,
> commands, and limitations, use the current [README](README.md) and linked
> component documentation.

Original goal: phases 1 to 10 working locally, followed by deployment, polish,
and rehearsal.

---

## 1. Tech stack (and why it fits this hackathon)

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.11 | Everything in the pipeline is Python; agent skills call scripts |
| Packaging / tasks | `pyproject.toml`, `Makefile`, `ruff`, `pytest` | Already scaffolded; CI in `.github/workflows` |
| Data | `pandas`, `geopandas`, `shapely` | GeoJSON ingest, geofence checks, ward polygons |
| Clustering | `scikit-learn` DBSCAN with `metric="haversine"` (coords in radians, eps = meters / 6371000) | Correct for lat/lon; HDBSCAN optional upgrade |
| Images | `Pillow`, `exifread` or `piexif`, `imagehash` (pHash) | EXIF GPS, duplicate/near-duplicate detection, no model needed |
| Video (stretch) | `opencv-python-headless` | Extract 3 keyframes, treat them as images |
| Schemas | `pydantic` v2 | One contract shared by all three members; enforces structured model output |
| Open-weight text LLM | **Qwen2.5 7B Instruct** (Apache 2.0) as default; Llama 3.1 8B or Mistral 7B as swap-ins | Strong instruction following and JSON output at 7B; clean license |
| Open-weight vision model | **Qwen2.5-VL 7B** (or 3B on weak laptops); alternatives: Llama 3.2 Vision, Gemma 3 vision | The original pitch named Llama 3 8B / Mistral 7B, which are text-only. Photo evidence needs a vision model |
| Model runtime | **Ollama** locally (OpenAI-compatible API at `localhost:11434/v1`) | Zero-cost, offline, easy for judges to reproduce |
| Hosted fallback | Any OpenAI-compatible host serving open-weight models (Hugging Face Inference Providers, Groq, OpenRouter, Together) | Free public URL cannot run a 7B model; switch with env vars only |
| LLM client | `openai` Python SDK pointed at `LLM_BASE_URL` | One code path for local and hosted |
| Verifiability layer | Custom citation + number validator (see 4.3) | This is what earns O3 |
| Skill packaging | `skill/omni-grid/SKILL.md` + `scripts/` | Agent Skills format; check agentskills.io for current frontmatter rules |
| Optional API | `FastAPI` + `uvicorn` in `api/` | Only if time allows; landing page works from pre-computed JSON |
| Landing page / demo | Static `web/index.html` (already built) + Leaflet for the real map | Host free on GitHub Pages or Netlify |
| CI | GitHub Actions: `ruff` + `pytest` | Visible quality signal |

**Before pulling models:** confirm the exact tags at ollama.com/library and read each model card's license. Model names and hosting availability change often. Keep the model name in `.env` so a swap is one line.

`.env.example`
```
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_TEXT_MODEL=qwen2.5:7b-instruct
LLM_VISION_MODEL=qwen2.5vl:7b
GEOFENCE_METERS=50
CLUSTER_EPS_METERS=150
CLUSTER_MIN_SAMPLES=3
```

---

## 2. Repo layout (matches your scaffold)

```
core/omnigrid/
  schemas.py      # pydantic models: the shared contract
  ingest.py       # load + validate GeoJSON/CSV
  evidence.py     # EXIF GPS, pHash, geofence, video keyframes
  cluster.py      # DBSCAN + severity
  verify.py       # Proof-of-Fix checks -> Verdict
  llm.py          # provider-agnostic client (text + vision)
  summarize.py    # structured summary + citation validator
  pipeline.py     # ingest -> evidence -> cluster -> verify -> summarize -> Report
  cli.py          # `omnigrid <command>`: JSON on stdout
skill/omni-grid/
  SKILL.md
  scripts/*.py    # thin wrappers that call cli commands
api/              # optional FastAPI
data/synthetic/   # generated complaints + media + ground_truth.json
web/              # landing page + demo JSON
eval/             # metrics script and results.md
tests/
```

---

## 3. Shared contracts (Lead writes these first, everyone codes against them)

```python
class Media(BaseModel):  path: str; type: Literal["image","video"]
class Closure(BaseModel): closed_at: datetime; media: list[Media]; closed_by: str | None
class Complaint(BaseModel):
    id: str; category: Literal["pothole","waterlogging","streetlight","other"]
    text: str; lat: float; lon: float; created_at: datetime
    status: Literal["open","closed"]; media: list[Media] = []; closure: Closure | None = None
class EvidenceMeta(BaseModel):
    complaint_id: str; path: str; gps: tuple[float,float] | None
    taken_at: datetime | None; phash: str; distance_m: float | None
class Hotspot(BaseModel):
    id: str; centroid: tuple[float,float]; radius_m: float; complaint_ids: list[str]
    category_mix: dict[str,int]; severity: float   # 0 to 10
class Check(BaseModel): name: str; passed: bool; detail: str; value: float | None = None
class Verdict(BaseModel):
    complaint_id: str; verdict: Literal["verified","suspect","inconclusive"]
    checks: list[Check]; confidence: float
class Claim(BaseModel): text: str; cites: list[str]   # complaint or hotspot IDs
class Report(BaseModel):
    hotspots: list[Hotspot]; verdicts: list[Verdict]; claims: list[Claim]; stats: dict
```

**Severity (0 to 10), keep it simple and explainable:**
`0.45*count_score + 0.30*vlm_damage_score + 0.15*recency_score + 0.10*injury_mention`, each part scaled 0 to 1, count_score = min(1, log2(1+n)/log2(1+20)). Document the formula in `references/severity.md`.

---

## 4. Trust design (the O3 story)

1. **Numbers from code only.** The LLM receives computed facts as JSON and may not invent counts, distances or scores.
2. **Structured output.** The model must return `list[Claim]` JSON validated by pydantic. Retry once on invalid output, then fall back to a template summary.
3. **Citation validator.** After generation: every `cites` ID must exist in the data; every number in a claim must appear in the facts JSON; otherwise the claim is dropped and logged. Report the "claims kept / dropped" count.
4. **Layered closure checks, cheapest first:** GPS distance, duplicate hash, then vision-model consistency. The vision model never overrides a failed deterministic check.
5. **"Suspect," never "fraud."** Output includes the evidence for a human to review.
6. **Measured accuracy.** `eval/` reports cluster purity, duplicate recall, suspect-closure precision/recall on the planted ground truth, and citation-validity rate.
7. **Privacy.** All processing local by default; no PII in prompts; ticket text only.

---

## 5. Phases and owners

Dependencies: Phase 1 first (about 45 min), then B, C and A work in parallel on mocks of the shared contract.

| Phase | Task | Owner | Depends on | Target (today) |
|---|---|---|---|---|
| 1 | Schemas, config, CI, Makefile, task board | **A (Lead)** | none | Hour 0 to 1 |
| 2 | Synthetic Bengaluru dataset + planted ground truth + sample media with EXIF | **B** | 1 | Hour 1 to 3 |
| 3 | Clustering + severity | **B** | 1, 2 | Hour 3 to 4 |
| 4 | Skill packaging: `SKILL.md`, CLI, script wrappers | **A** | 1 | Hour 1 to 4 |
| 5 | Evidence pipeline: EXIF, pHash, geofence | **B** | 2 | Hour 4 to 6 |
| 6 | Proof-of-Fix: deterministic checks (B) + vision consistency (C) | **B + C** | 5, 7 | Hour 6 to 8 |
| 7 | LLM layer: provider client, structured summary, citation validator, vision severity | **C** | 1 | Hour 1 to 6 |
| 8 | Landing page to `/web`, real map with evidence thumbnails, demo JSON export | **C** | 3, 7 | Hour 6 to 9 |
| 9 | Pipeline + agent integration test (+ optional FastAPI) | **A** | 3, 5, 6, 7 | Hour 6 to 9 |
| 10 | Eval harness, README, architecture diagram | **A + B** | 6, 9 | Hour 9 to 11 |
| 11 | Deployment (GitHub Pages/Netlify landing; optional API) | **A** | 8, 9 | Tomorrow AM |
| 12 | Demo script, video fallback, slides, submission | **All** | 11 | Tomorrow PM to Sun |

**Load:** A = phases 1, 4, 9, 11 + half of 10; B = 2, 3, 5 + half of 6; C = 7, 8 + half of 6. If someone finishes early, they take stretch items (video keyframes, Kannada/Hindi complaint text, PDF export).

---

## 6. Definition of done (per phase)

- Code merged to `main` through a PR with one review.
- Unit tests pass in CI.
- Function documented with a one-line example in `docs/` or docstring.
- Output matches the schema in Section 3.

---

## 7. Demo checklist (what the build must support)

- [ ] `omnigrid triage data/synthetic/complaints.geojson` prints a Report JSON in under 10 s (excluding model time)
- [ ] An agent runs the skill from one natural-language prompt
- [ ] 5 hotspots recovered from planted ground truth
- [ ] 3 planted fake closures flagged, 0 or 1 false flags
- [ ] Every claim in the summary passes the citation validator
- [ ] Landing page loads real exported JSON and shows evidence on the map
- [ ] Eval table in README

## 8. Risks

| Risk | Plan B |
|---|---|
| Vision model too slow or weak on laptops | Use the 3B variant, analyse 1 image per ticket, cache results in `data/cache/` |
| Ollama setup trouble | Switch `LLM_BASE_URL` to a hosted open-weight API |
| Contract changes mid-day | Only the Lead changes `schemas.py`; announce in group |
| Merge conflicts | One owner per file (Section 2 assigns them by phase) |
| Time runs short | Cut order: video, API, multilingual, PDF. Never cut Proof-of-Fix or the live page |
