# Contributing to Omni-Grid

Thanks for considering a contribution. This project is open to useful,
well-scoped work from new and experienced contributors. Please read the
[Code of Conduct](CODE_OF_CONDUCT.md) and [SECURITY.md](SECURITY.md) first.

## Find or discuss the work

1. Search the [open issues](https://github.com/ShreyaJ-27/omni-grid-civic-issue-triage/issues)
   and recent pull requests for related work before starting.
2. For a bug, include a minimal reproduction that uses synthetic or otherwise
   safe data. For a larger feature or architecture change, open an issue or
   comment on the relevant issue first to agree on scope.
3. Do not assume an issue is assigned to you or that an unassigned issue is
   available. Ask in the issue before doing substantial work, and avoid
   duplicate implementations.
4. Keep changes within the issue's scope. Do not add credentials, real
   complaint records, private media, or unrelated generated output.

## Fork and branch workflow

1. Fork the repository in your GitHub account.
2. Clone your fork and add the project repository as `upstream`:

   ```sh
   git clone https://github.com/<your-account>/omni-grid-civic-issue-triage.git
   cd omni-grid-civic-issue-triage
   git remote add upstream https://github.com/ShreyaJ-27/omni-grid-civic-issue-triage.git
   git fetch upstream
   git switch -c <type>/<short-description> upstream/main
   ```

3. Make focused changes on your branch and push that branch to your fork.
   Do not push directly to the project `main` branch. Do not request or
   change collaborator permissions; no coding agent or service account needs
   repository access to make a contribution.
4. Open a pull request from your fork, link the relevant issue, explain the
   behavior or documentation change, and include test results. Use a closing
   keyword only when the pull request fully resolves that issue.

Use small, descriptive commits and avoid unrelated formatting churn. Do not
rewrite shared history. Project maintainers review and decide whether to merge
contributions; opening a pull request does not guarantee acceptance.

## Python setup and checks

Python 3.11 is the repository's CI baseline and the package declares Python
3.11 or newer. Run these commands from the repository root.

```sh
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,pipeline]"
python -m pytest
python -m ruff check api core tests scripts eval
```

On Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev,pipeline]"
python -m pytest
python -m ruff check api core tests scripts eval
```

GNU Make is optional and is not included with Python on Windows. If GNU Make
is installed, `make install`, `make test`, `make lint`, and `make eval` run the
corresponding project commands. The direct Python commands above work without
Make. For API changes, install all needed extras together:

```sh
python -m pip install -e ".[dev,pipeline,api]"
```

Tests and the default evaluation run do not require an LLM service, model
download, or live API key. To run the CLI offline, set `LLM_MOCK=true` in the
process environment before launching it (for example,
`$env:LLM_MOCK = "true"` in PowerShell). In the current implementation,
`LLM_MOCK` and `LLM_TIMEOUT` are process-environment settings and are not
loaded from `.env`.

## Code, tests, and documentation

- Follow the existing Python style and use the shared Pydantic schemas and
  canonical pipeline rather than duplicating validation or triage logic.
- Add or update focused tests for behavior changes. Keep tests deterministic,
  offline, and independent of hosted services where possible.
- Use synthetic fixtures. Do not add real citizen reports, photos, videos,
  exact addresses, names, phone numbers, license plates, or other identifying
  content to tests, examples, logs, or generated reports.
- If regenerating synthetic fixtures, note that
  `python scripts/generate_synthetic.py` overwrites the checked-in GeoJSON and
  ground-truth files and may recreate generated media. Review generated diffs
  carefully and do not include unrelated data changes.
- Update the README and relevant guides when commands, schemas, dependencies,
  configuration, or behavior change. Keep Mermaid diagrams in sync with the
  actual data flow.
- Only include media or other third-party material when you have the rights
  to distribute it and have recorded its source and license. Do not include
  credentials, private keys, access tokens, or API secrets. Keep local
  credentials in the ignored `.env` file or process environment.

## Pull-request review

Explain why the change is needed, summarize its scope, list verification
commands and results, and note known limitations. Reviewers may request
changes or additional tests. Maintainers retain merge authority and will
prioritize correctness, privacy, maintainability, and alignment with the
project's actual implementation.

Hacktoberfest contributions should be genuine improvements that meet these
same standards. Do not submit whitespace-only or trivial changes, artificial
pull requests, commit spam, or requests to bypass review. No event eligibility
or acceptance outcome is promised.