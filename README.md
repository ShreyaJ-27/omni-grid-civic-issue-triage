# Omni-Grid development setup

The baseline environment targets Python 3.11. Install the package and its
development tools from the repository root:

```sh
python3.11 -m venv .venv
# Activate .venv, then:
python -m pip install --upgrade pip
make install
```

`make install` installs the core package and the `dev` extra (pytest, Ruff, and
HTTP test utilities). On Windows, use an environment with GNU Make available,
or run the equivalent commands directly:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
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

- `make install` — install the editable package and baseline development tools.
- `make test` — run pytest.
- `make lint` — run Ruff over `core/` and `tests/`.
- `make demo` — run the synthetic complaint triage CLI when its implementation
  and `data/synthetic/complaints.geojson` are available.
- `make export-demo` — export a report through the real CLI export workflow
  when implemented. Neither demo target generates placeholder results.

The demo targets currently report a dependency error because the CLI and
synthetic complaints input have not yet been implemented.

The API and image/clustering dependencies are optional extras and are not
installed by the baseline setup:

```sh
python -m pip install -e ".[api,pipeline]"
```

On Windows, GNU Make is not included with Python; install it separately or run
the equivalent Python commands shown above. CI uses the project commands
directly with Python 3.11 and does not require GNU Make, Ollama, or model
weights.
