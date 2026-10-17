# Omni-Grid development setup

The baseline environment targets Python 3.11. Install the package and its
development tools from the repository root:

```sh
python3.11 -m venv .venv
# Activate .venv, then:
python -m pip install --upgrade pip
make install
```

`make install` installs the core package, the `dev` extra (pytest, Ruff, and
HTTP test utilities), and the `pipeline` extra needed by clustering, media
tests, and the CLI. On Windows, use an environment with GNU Make available,
or run the equivalent commands directly:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev,pipeline]"
```

Copy `.env.example` to `.env` to customize local settings. `.env` is ignored by
Git; only the safe example defaults belong in version control. The example
`LLM_API_KEY=ollama` is a local placeholder, not a hosted API credential.
Configuration is loaded from `.env` when present, then overridden by process
environment variables. Blank LLM endpoint, key, or model settings disable that
optional setting. Geofence and clustering numeric values must be finite and
greater than zero (minimum sample count must be a positive integer); invalid
values raise an error without echoing the supplied value.

## Project commands

- `make install` — install the editable package, development tools, and
  deterministic clustering/media dependencies.
- `make test` — run pytest.
- `make lint` — run Ruff over `core/`, `tests/`, and `scripts/`.
- `make demo` — run the CLI's ingestion-and-clustering-only `triage` command on
  `data/synthetic/complaints.geojson`. Its partial report lists later stages as
  unavailable because the CLI does not orchestrate the full Python pipeline.
- `make export-demo` — run the synthetic report exporter in offline/mock mode,
  including clustering, deterministic closure checks, damage scoring, and
  structured summaries, then refresh `web/demo/report.json` and its media.

The API dependencies remain optional. The `pipeline` extra is installed by
`make install` because clustering and the repository's existing tests require
NumPy, scikit-learn, Pillow, ImageHash, OpenCV, and piexif:

```sh
python -m pip install -e ".[api]"
```

## CLI and Agent Skill

The installed `omnigrid` command and `python -m omnigrid.cli` support
`ingest`, `evidence`, `cluster`, `verify`, `summarize`, and `triage`. For
example:

```powershell
py -3.11 -m omnigrid.cli triage data\synthetic\complaints.geojson
```

The CLI's `evidence`, `verify`, and `summarize` commands validate existing
contract JSON; they do not run those stages. The Python library implements
media evidence extraction, deterministic closure verification, optional vision
checks, damage scoring, and citation-validated summaries. The `triage` CLI
currently runs ingestion and clustering only and returns a partial `Report`.
The synthetic exporter composes the implemented stages for the committed demo;
it is not a general-purpose CLI pipeline for arbitrary complaint files. See
`skill/omni-grid/SKILL.md` for agent instructions and
`docs/skill-smoke-test.md` for installation and the manual live-agent test.

On Windows, GNU Make is not included with Python; install it separately or run
the equivalent Python commands shown above. CI uses the project commands
directly with Python 3.11 and does not require GNU Make, Ollama, or model
weights.

## LLM Client & Vision Verification

Omni-Grid provides a provider-agnostic LLM client (`core/omnigrid/llm.py`) and
vision-assisted closure verification (`core/omnigrid/verify.py`):

- **Provider-agnostic**: Point to local Ollama (`http://localhost:11434/v1`) or any
  OpenAI-compatible hosted provider with `LLM_BASE_URL` and `LLM_API_KEY`.
- **Mock mode**: Run deterministically and offline without API keys or models using
  `LLM_MOCK=true`.
- **Proof-of-Fix precedence**: A vision-model consistency check evaluates whether
  a defect is repaired at the matching location, but **never overrides a failed
  deterministic check** (GPS geofence, duplicate pHash, or timestamp sanity).
- **Persistent caching**: Vision inference results are hashed and cached in
  `data/cache/vision_cache.json` to prevent duplicate inference.

See `docs/llm-and-vision-verification.md` for complete configuration, model
recommendations, licensing, and timeout/retry details.

## Issues #1-16 Audit

The implementation traceability matrix and verified findings are available in
[`docs/audit/issues-01-16-matrix.md`](docs/audit/issues-01-16-matrix.md) and
[`docs/audit/issues-01-16-findings.md`](docs/audit/issues-01-16-findings.md).
The audit records untested live-agent/model behavior and keeps Issues #17 and
#18 deferred.
