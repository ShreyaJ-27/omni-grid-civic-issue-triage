# Omni-Grid skill smoke test

This guide separates automated CLI/wrapper coverage from a live-agent check.
Automated tests do not prove that an agent discovers or invokes the skill.

## Client and prerequisites

The skill uses the Agent Skills `SKILL.md` convention and is compatible with
Claude Code. The documented example below uses Claude Code **2.1.283** on
Windows; discover the installed version with `claude --version`. Install
Python 3.11+, GNU Make, and the project dependencies first:

```powershell
make install
py -3.11 -m omnigrid.cli triage data\synthetic\complaints.geojson
```

Claude Code's project skill discovery path is `.claude/skills/<skill-name>/`.
For a local, untracked smoke test from the repository root, register this
checkout's skill using a directory junction:

```powershell
New-Item -ItemType Directory -Force .claude\skills | Out-Null
New-Item -ItemType Junction -Path .claude\skills\omni-grid -Target (Resolve-Path skill\omni-grid)
```

If the junction already exists, inspect its target before replacing it. Do not
commit the local `.claude` registration. Alternatively, copy the skill
directory to Claude Code's documented personal skills directory
`$HOME\.claude\skills\omni-grid`. Remove or update the local registration after
testing as appropriate.

## Manual live-agent smoke test

1. Start Claude Code with the repository as the working directory and confirm
   `/omni-grid` appears as an available skill. Inspect its displayed
   description; it should mention civic complaints, supported triage steps,
   and the unfinished evidence/verification/summary stages. If it is missing,
   check that the client is using the intended checkout and that the
   `SKILL.md` file is directly inside `.claude\skills\omni-grid`.
2. Without naming the skill, submit:

   > Analyze these civic complaints, identify hotspots, and check whether the reported closures are supported by evidence. Use the committed synthetic sample at `data/synthetic/complaints.geojson`.

3. Observe the client. Record the client/version, whether the skill was
   discovered, whether the natural-language request triggered it, and the
   exact commands/wrappers actually executed. Discovery alone does not prove
   automatic triggering.
4. Expected behavior with the current implementation is to run ingestion and
   clustering, typically through `triage` or its wrapper. The output is a JSON
   `Report` with `hotspots`, empty `verdicts` and `claims`, and `stats` listing
   completed stages (`ingest`, `cluster`) and unavailable stages
   (`evidence`, `verify`, `summarize`). The agent must explicitly state that
   closure evidence cannot yet be assessed; it must not claim closures are
   verified or suspect without an implemented check.
5. Validate captured CLI JSON without exposing complaint text unnecessarily:

   ```powershell
   py -3.11 -c "import json; from omnigrid.schemas import Report; data=json.load(open('report.json', encoding='utf-8')); Report.model_validate(data); print('Report schema valid')"
   ```

   Save CLI JSON to `report.json` only if it is an intended local output; the
   repository ignores generated output directories, not arbitrary filenames.
   Do not commit generated data. When testing interactively, piping the
   command output to a parser is preferable to storing it.
6. Mark this acceptance test **not verified** until the agent's observable
   behavior confirms the automatic trigger, command invocation, and validated
   output. Record any dependency errors or corrections below in your own test
   notes; do not represent mocked tests as a live-agent result.

## Automated checks and troubleshooting

The repository's `tests/test_cli.py` and `tests/test_skill_smoke.py` cover
command registration/help, shared schema output, malformed and missing input,
wrapper stdout/stderr/exit-code forwarding, and skill metadata. They run
offline and do not use an LLM, model download, Ollama, or credentials. Run them
with:

```powershell
py -3.11 -m pytest -q tests\test_cli.py tests\test_skill_smoke.py
```

Common issues:

- **Missing NumPy/scikit-learn/Pillow/ImageHash/OpenCV:** rerun `make install`
  to install the `pipeline` extra. No model service is required for ingestion
  or clustering.
- **Input not found:** paths passed to a wrapper are interpreted relative to
  the caller's current directory. Use a repository-relative path from the
  repository root or an absolute path.
- **Malformed GeoJSON/schema error:** verify a `FeatureCollection` has Point
  geometries and each feature's properties satisfy the shared `Complaint`
  model. Geometry is `[longitude, latitude]`; disagreement with `lat`/`lon`
  properties is rejected.
- **Permission denied:** confirm the client can read the repository and
  execute the selected Python interpreter. Do not grant repository or account
  access to the agent.
- **No skill trigger:** confirm the location and frontmatter, start a fresh
  client session, and check skill availability before retrying the natural
  language prompt. A manually invoked `/omni-grid` is not evidence of automatic
  triggering.
- **Non-JSON output:** use only stdout for machine output; diagnostics are on
  stderr. Do not wrap or reinterpret an error message as a successful report.

## Live-agent result

**Manual verification pending.** Claude Code 2.1.283 was available in the
development environment, but this repository change did not start an
interactive agent session or make an API call. Therefore skill discovery,
automatic natural-language triggering, the live commands invoked, and agent
JSON handling have not been observed.
