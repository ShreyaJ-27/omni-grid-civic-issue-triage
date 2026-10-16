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
- `make lint` — run Ruff over `core/` and `tests/`.
- `make demo` — run the implemented ingest-and-cluster portion of the triage
  CLI on `data/synthetic/complaints.geojson`. The JSON report explicitly lists
  evidence extraction, closure verification, and narrative summarization as
  unavailable until those pipeline stages are implemented.
- `make export-demo` — report an error until report export is implemented.

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

The available implementation validates complaint, evidence, verdict, and
report contracts, and runs complaint clustering. Media evidence extraction,
closure verification, report generation, and citation checking are not
implemented. `triage` therefore emits a partial shared `Report` that explicitly
lists completed and unavailable stages; it does not fabricate findings. See
`skill/omni-grid/SKILL.md` for agent instructions and
`docs/skill-smoke-test.md` for installation and the manual live-agent test.

On Windows, GNU Make is not included with Python; install it separately or run
the equivalent Python commands shown above. CI uses the project commands
directly with Python 3.11 and does not require GNU Make, Ollama, or model
weights.
