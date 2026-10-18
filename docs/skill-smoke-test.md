# Omni-Grid skill smoke test

This guide separates automated CLI/wrapper coverage from a live-agent check.
Automated tests do not prove that an agent discovers or invokes the skill.

## Client and prerequisites

The skill uses the portable Agent Skills `SKILL.md` convention. The open
Skills CLI documents support for Claude Code, Codex, Cursor, OpenCode, and
additional agent targets. This repository has **not** completed an interactive
skill-discovery or invocation test in any coding agent. Python 3.11+, GNU
Make, and the project dependencies are needed for local CLI execution:

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

1. Install from the public repo with the documented Skills CLI, selecting a
   supported agent:

   ```powershell
   npx skills add https://github.com/ShreyaJ-27/omni-grid-civic-issue-triage/tree/main/skill/omni-grid --skill omni-grid
   ```

   For an unmerged branch, run the agent-specific manual setup below from the
   checked-out feature branch instead. The install command is documented by
   the upstream `skills` CLI and points directly at the directory containing
   this `SKILL.md`.
2. Start the selected agent and confirm it discovers the skill. Inspect its
   description; it should distinguish implemented Python evidence and
   verification functions from validator-only CLI commands.
3. Without naming the skill, submit:

   > Analyze these civic complaints, identify hotspots, and check whether the reported closures are supported by evidence. Use the committed synthetic sample at `data/synthetic/complaints.geojson`.

4. Observe the client. Record the client/version, whether the skill was
   discovered, whether the natural-language request triggered it, and the
   exact commands/wrappers actually executed. Discovery alone does not prove
   automatic triggering.
5. For hotspot-only analysis, expected behavior is to run ingestion and
   clustering through `triage` or its wrapper. That CLI output is a partial
   `Report` with empty `verdicts` and `claims`; `stats` lists later stages as
   unavailable. For the committed synthetic sample when closure checks and a
   complete web report are requested, the agent may run
   `python scripts/export_demo_report.py`. That exporter includes deterministic
   closure checks, mock-mode damage scoring, and a validated summary fallback;
   it does not perform a live model call or accept arbitrary input paths.
6. Validate captured output without exposing complaint text unnecessarily:

   ```powershell
   py -3.11 -c "import json; from omnigrid.schemas import Report; data=json.load(open('web/demo/report.json', encoding='utf-8')); Report.model_validate(data); print('Report schema valid')"
   ```

   The demo exporter refreshes its intended tracked artifact and media. Do not
   commit unrelated generated output. When testing CLI output interactively,
   piping the command output to a parser is preferable to storing it.
7. Mark this acceptance test **not verified** until the agent's observable
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

**Manual verification pending.** No interactive agent was used to test skill
discovery, automatic natural-language triggering, command invocation, or JSON
handling during this implementation. The Skills CLI install instructions and
metadata format are documented, but compatibility has not been demonstrated
by a live-agent run.
