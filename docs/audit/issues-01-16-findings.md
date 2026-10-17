# Issues #1-16 Audit Findings

## Executive summary

The audit found and repaired real defects in media-path confinement, evidence
extraction failure handling, model-cache identity, synthetic-data
reproducibility, demo report completeness, sample-media coverage, and Makefile
targets. No confirmed Critical or High severity issue remains in the audited
scope.

**Verdict:** the deterministic and mock-mode synthetic demo is locally
reproducible and passes the checks recorded below. This is not a claim that a
live agent, hosted model, local model server, or deployed service was tested.
Issue #7 still lacks its required live-agent smoke test; live model/provider
compatibility remains unverified. Issue #17 is open and remains the next
required implementation step; Issue #18 is explicitly deferred until #17 is
complete.

The audit branch was created from `f59fae1ab1805d26c685a7b7f2765d1c5244c8aa`,
which matched `origin/main` when checked. A pre-existing user edit to
`data/synthetic/ground_truth.json` (`metadata.generated_at`) was preserved and
excluded from audit changes.

## Severity-ranked findings

### Medium

#### M1. Complaint media paths could escape the selected media directory — fixed

- **Evidence:** before the fix, bulk evidence extraction joined relative paths
  to `base_dir` without checking the resolved destination; verification's
  resolver accepted an existing path before applying any media-root
  restriction. Absolute paths and `..` traversal in complaint media could
  therefore read files outside the selected dataset. With vision enabled and
  a hosted endpoint configured, those selected image bytes could be sent to
  that endpoint.
- **Impact:** processing an untrusted complaint record could make the local
  process inspect an unrelated local image; vision-enabled processing could
  transmit it to the configured service.
- **Root cause:** path resolution did not enforce a containment boundary or
  account for symlinks.
- **Fix:** bulk evidence extraction now resolves paths and rejects paths
  outside `base_dir` (defaulting to the current working directory).
  Verification resolves complaint media under `media_dir` (defaulting to the
  repository root), rejects paths escaping that root, and returns an
  inconclusive `evidence_path` check rather than reading an external file.
  Symlink targets are checked after resolution.
- **Regression tests:** `tests/test_evidence.py::test_bulk_evidence_rejects_paths_outside_base_dir`
  and `tests/test_verify.py::test_closure_media_outside_media_dir_is_rejected`.
- **Verification:** both regressions pass; the complete suite passes. The
  media-root behavior is documented in
  `docs/llm-and-vision-verification.md`.

#### M2. Exported reports omitted most closed complaints and ignored damage scores in clustering — fixed

- **Evidence:** the baseline exporter produced only 10 verdicts for a dataset
  with 100 closed complaints. It also clustered before calculating the
  available damage scores, so those scores were not supplied to hotspot
  severity calculation.
- **Impact:** reports omitted 90 closed complaints from verification output
  and hotspot severity failed to use an implemented input. The web report
  therefore did not represent the full set of closures.
- **Root cause:** the exporter filtered verification to complaints with
  closure media and ran clustering before damage scoring.
- **Fix:** the exporter scores eligible complaint photos before clustering,
  passes scores into clustering, and emits a verdict for every closed
  complaint; closures with missing evidence are inconclusive. It validates
  the serialized report, refreshes the demo media, and uses a fixed reference
  time for deterministic output.
- **Regression test:** `tests/test_web.py::test_export_script_generates_valid_report_and_media`.
- **Verification:** final report validates as `Report` and contains 500
  complaints, 5 hotspots, 100 verdicts (5 suspect, 5 verified, 90
  inconclusive), 5 damage scores, 5 fallback claims, and 40 copied images.
  All 15 referenced media paths resolve; no missing paths were found.
  Repeated test exports are byte-identical.

#### M3. Vision cache entries could cross provider/mode boundaries and retain the wrong evidence ID — fixed

- **Evidence:** damage-score cache keys used only the vision model name, not
  endpoint or mock/live mode. On a cache hit, an existing cached
  `evidence_id` could be returned for a different requested evidence item.
  Closure-vision cache keys also omitted provider identity.
- **Impact:** cached assessments could be reused across endpoints or between
  deterministic mock output and live inference, and score provenance could
  identify the wrong evidence record.
- **Root cause:** cache identity was narrower than the inference
  configuration, and cached score identity was treated as reusable data.
- **Fix:** `LLMClient.vision_cache_identity` includes endpoint, model and
  mock/live mode; both vision caches use it. Damage-score cache hits attach
  the caller's requested evidence ID.
- **Regression tests:** cache identity and evidence-ID tests in
  `tests/test_damage_severity.py` and `tests/test_vision_verify.py`.
- **Verification:** focused cache tests and the complete suite pass. Cache
  keys store the identity only as hashed input; credentials are not part of
  the identity.

#### M4. Missing media could abort bulk evidence extraction — fixed

- **Evidence:** a direct baseline call to `extract_complaint_evidence()` with
  a nonexistent image raised `FileNotFoundError` and prevented processing
  the rest of the complaint's media.
- **Impact:** one missing or malformed attachment could abort bulk evidence
  processing for an otherwise usable complaint dataset.
- **Root cause:** bulk extraction did not consistently handle expected
  filesystem and media-format failures.
- **Fix:** bulk extraction logs a warning and skips missing, unreadable or
  malformed image/video inputs. Single-file extraction continues to raise a
  clear error to its direct caller.
- **Regression tests:** `tests/test_evidence.py::test_bulk_evidence_skips_missing_and_malformed_images_with_diagnostics`
  and video failure tests in `tests/test_video.py`.
- **Verification:** focused evidence/video/verification selection passed 72
  tests; the full suite passed.

### Low

#### L1. Synthetic generation was not reproducible and its test fixture rewrote tracked data — fixed

- **Evidence:** the generator added a wall-clock `generated_at` value, and
  the synthetic-data fixture wrote generated files directly into tracked
  repository paths. A pytest run changed the user's `ground_truth.json`.
- **Impact:** repeated generation differed despite identical complaint data,
  and routine tests dirtied a tracked file.
- **Root cause:** generation metadata was time-dependent and test output
  shared paths with checked-in fixtures.
- **Fix:** generation accepts a seed and omits the variable timestamp from
  newly generated ground truth; tests write generated JSON under pytest
  temporary storage.
- **Regression test:** `tests/test_synthetic_data.py::test_generator_is_reproducible_for_a_fixed_seed`.
- **Verification:** the test passes. The original user timestamp edit was
  restored and remains the only diff in `data/synthetic/ground_truth.json`;
  that file is excluded from the audit commit.

#### L2. The committed sample media set was short of its approximately-40-image requirement — fixed

- **Evidence:** the repository initially contained 15 synthetic JPEGs, below
  Issue #4's approximately 40-image acceptance criterion.
- **Impact:** the evidence set underrepresented the documented test sample.
- **Root cause:** media generation and the checked-in expected count stopped
  at 15.
- **Fix:** the deterministic generator now creates 40 images, retaining
  existing assets and adding `media_16.jpg` through `media_40.jpg`.
- **Regression test:** `tests/test_synthetic_data.py::test_media_count_and_existence`
  and EXIF/offset/duplicate checks in the same module.
- **Verification:** all 40 files exist, have EXIF GPS, and the test suite
  validates planted offsets and duplicate hashes.

#### L3. Make targets omitted script linting and did not perform report export — fixed

- **Evidence:** the original `lint` target omitted `scripts/`, and
  `export-demo` did not invoke the report exporter.
- **Impact:** CI/local quality checks could miss script errors, and the
  documented command could not refresh the web report.
- **Root cause:** Make targets did not cover all maintained Python code or
  the documented export workflow.
- **Fix:** `lint` now includes `scripts/`; `export-demo` runs
  `scripts/export_demo_report.py`.
- **Regression/verification:** `make lint` and `make export-demo` both
  succeeded. The final export completed in 6,417.4 ms in mock mode.

## Remaining gaps and risks

- **Issue #7:** the required real-agent skill discovery and automatic
  natural-language trigger test remains unperformed. Wrapper tests do not
  prove agent behavior.
- **Live models/providers:** no Ollama service, hosted endpoint, API
  credential, or model download was used. LLM behavior, retries and
  validation were exercised offline with mocks only. No model performance or
  calibration claim is made.
- **Issue #16 map tiles:** the local browser rendered the data-driven page,
  controls and complaint details; the final OpenStreetMap tile requests were
  aborted during a page reload. External tile-service rendering is therefore
  not claimed as verified.
- **Security/dependency scanning:** no SCA/CVE scanner was run. The project
  uses version ranges rather than a committed lockfile; the fresh Python
  3.11.9 installation succeeded, but future dependency resolution is not
  guaranteed to be byte-for-byte reproducible.
- **Historical CI:** GitHub Actions runs on the early main merges for PRs
  #22-#25 failed. Available logs show Ruff import-order failures on #23/#24
  and `ModuleNotFoundError: imagehash` during test collection on #25; #22's
  failed run had no failed-job logs available. Later main merge runs #26-#32
  succeeded, including two successful checks on PR #32's merge. The audit
  commit `695ffbb` also passed GitHub Actions run #35 (Python 3.11 install,
  lint and tests).
- **Issue #18:** no FastAPI service, upload endpoint, request-size limit, or
  API integration test was added or evaluated as a feature. This remains
  deferred as requested.

## Exact local verification

Environment: Windows, Python **3.11.9**, Ruff **0.17.1**, pytest **9.1.1**.
A new virtual environment was created under the system temporary directory;
`python -m pip install -e ".[dev,pipeline]"` completed successfully.

| Command / check | Result |
|---|---|
| User-provided initial baseline: `python -m ruff check core tests scripts` | Exit 0; “All checks passed!” |
| User-provided initial baseline: `python -m pytest -q` | 226 passed in 49.57 s in the supplied output; independently reproduced before edits with Python 3.11.9: 226 passed in 34.53 s. |
| User-provided baseline: `python -m omnigrid.cli --help` | Exit 0. |
| User-provided baseline: `python -m omnigrid.cli triage data/synthetic/complaints.geojson` | Exit 0; 500 complaints and 5 hotspots; stages completed were `ingest` and `cluster`, with later stages explicitly unavailable. |
| Fresh environment: `python -m pip install -e ".[dev,pipeline]"` | Exit 0. |
| Fresh environment: `python -m ruff check core tests scripts` | Exit 0; all checks passed. |
| Fresh environment: `python -m pytest -q` after all fixes | **233 passed in 36.95 s; exit 0.** |
| Focused evidence/video/verification tests: `python -m pytest -q tests\test_evidence.py tests\test_video.py tests\test_verify.py tests\test_vision_verify.py` | **72 passed in 6.52 s; exit 0.** |
| `make lint` | Exit 0; Ruff checked `core tests scripts`. |
| Fresh environment: `make test` | **233 passed in 38.58 s; exit 0.** |
| `make demo` | Exit 0; CLI emitted 500 complaint count and 5 hotspots; intentionally partial `triage` report. |
| `python -m omnigrid.cli --help` in fresh environment | Exit 0; all six subcommands listed. |
| `python -m omnigrid.cli ingest does-not-exist.json` | Exit 2; no success JSON; diagnostic `input file not found`. |
| `make export-demo` | Exit 0; **6,417.4 ms** measured with PowerShell `Measure-Command`; 500 complaints, 5 hotspots, 100 verdicts, 5 damage scores, 5 fallback claims and 40 copied images. |
| `git diff --check` | Exit 0; no whitespace errors. |
| Pylance syntax checks on modified evidence/verification source and tests | No syntax errors; `verify.py` Pylance diagnostics empty. |
| Local browser smoke test at `http://localhost:8765/` | Report fetch HTTP 200; 500 complaints, 5 hotspots, 100 verdicts; complaint detail showed verification checks and repair evidence. Aborted the report fetch and verified the embedded fallback (6 complaints, 2 hotspots, 2 suspect, 1 verified). At 390 px viewport, document width was 375 px. |

The local exporter used deterministic mock mode; it did not call a model
service. These local results are separate from CI. GitHub Actions run #35 for
audit commit `695ffbb` passed the `quality` job, including dependency
installation, lint and tests. Current GitHub `main` evidence is the successful
Actions run for merged PR #32 (run `39008040429`, two successful `quality`
checks).

## Issue #17 limitations to address next

Issue #17 is open and requires a general, executable pipeline; no Issue #17
implementation was started in this audit branch. `omnigrid triage` still runs
only ingestion and clustering and marks evidence, verification and summary as
unavailable. `make export-demo` composes existing stages only for the fixed
synthetic sample; it does not accept arbitrary complaint input and is not a
replacement for the general pipeline.

Issue #17 must integrate ingestion, evidence extraction, clustering,
verification and citation-validated summaries using the shared schemas;
produce a complete validated `Report` with actionable errors and claims
kept/dropped diagnostics; retain deterministic/template fallback when models
are unavailable; and test the full workflow. The fixed-sample mock export
measured below 10 seconds, but that does not verify the issue's general
pipeline acceptance.

**Go/no-go:** Issues #17 and #18 remain blocked until this audit PR is reviewed
and merged. Issue #18 depends on the completed Issue #17 pipeline.
