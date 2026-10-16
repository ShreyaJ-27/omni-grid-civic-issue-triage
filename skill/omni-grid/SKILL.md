---
name: omni-grid
description: Use for civic-complaint GeoJSON ingestion, hotspot analysis, validating Omni-Grid evidence/verdict/report JSON, or running the available triage steps. Trigger on requests to analyze civic complaints, find geographic clusters, inspect closure evidence, or use the Omni-Grid CLI; clearly disclose that evidence extraction, closure verification, and narrative summarization are not implemented.
license: MIT
compatibility: Python 3.11+ with this repository's installed dependencies.
metadata:
  version: "0.2.0"
---

# Omni-Grid civic issue triage

Use the repository CLI and shared Pydantic v2 schemas as the source of truth.
Do not recreate ingestion, clustering, schema validation, or severity logic in
agent code.

## When to use

Use this skill when a user wants to analyze civic complaints, identify
geographic hotspots, validate Omni-Grid data, or understand whether reported
closures are supported by evidence.

## Prerequisites and inputs

- Run commands from the repository root after installing dependencies with
  `make install`. This installs the project, development tools, and the
  `pipeline` extra used by clustering and media tests.
- Use Python 3.11 or newer. The packaged command is `omnigrid`; the module form
  `python -m omnigrid.cli` and scripts in `skill/omni-grid/scripts/` are also
  supported.
- Ask for the complaint file path if the user did not provide one. Do not
  invent filenames, media paths, complaint data, or missing evidence. Use
  `data/synthetic/complaints.geojson` only when the user asks to use the
  committed synthetic sample.
- Complaint input is a JSON `Complaint`, an array of `Complaint` records, a
  GeoJSON `Feature`, or a GeoJSON `FeatureCollection` of `Point` features.
  Each feature's properties must satisfy the shared `Complaint` model. Point
  coordinates are `[longitude, latitude]`; conflicting property coordinates
  are rejected.
- Evidence, verdict, and report validator inputs must already contain records
  matching `EvidenceMeta`, `Verdict`, or `Report`. Sample records under
  `data/samples/` are synthetic and their media paths are illustrative.

## Available commands and contracts

Successful commands emit only JSON on stdout. Errors and diagnostics go to
stderr and exit non-zero. The CLI validates input with the shared contracts in
`core/omnigrid/schemas.py`.

| Command | Input and behavior | Successful JSON output |
| --- | --- | --- |
| `ingest INPUT` | Parse a complaint JSON file or GeoJSON, convert Point features to `Complaint`, and validate all records. | Array of `Complaint` |
| `evidence INPUT` | Validate existing evidence metadata only. It does **not** read photos, extract EXIF, compute pHash, or calculate geofence distance. | Array of `EvidenceMeta` |
| `cluster INPUT [options]` | Ingest complaints and run the implemented DBSCAN/haversine clustering and explainable severity scoring. | Array of `Hotspot` |
| `verify INPUT` | Validate existing verdict records only. It does **not** evaluate closure evidence or make a verification decision. | Array of `Verdict` |
| `summarize INPUT` | Validate an existing report only. It does **not** generate narrative claims or check citations. | `Report` object |
| `triage INPUT [options]` | Run ingestion and clustering only. It returns a partial report and names unavailable stages in `stats`. | Partial `Report` object |

`cluster` and `triage` accept `--eps-meters`, `--min-samples`, and
`--reference-time` (timezone-aware ISO-8601). The default cluster settings come
from the existing configuration. Use `--help` on the command or a subcommand
for the full usage.

Evidence extraction, proof-of-fix checks, LLM/vision calls, report generation,
and citation validation have not been implemented in this repository. Do not
describe their contract validators as performing those operations. A partial
`triage` report intentionally has empty `verdicts` and `claims`; it does not
pretend to have verified closures. If a user needs one of the missing stages,
explain the limitation rather than inferring a result or returning fabricated
scores.

## Workflow

1. Confirm the input path and whether the user wants the committed synthetic
   sample. If data or paths are missing, ask instead of guessing.
2. Read the relevant command's `--help` if the input shape or options are
   unclear.
3. Run the wrapper or CLI command with an argument list, keeping stdout as
   JSON. Do not parse stderr as a result.
4. For hotspot analysis, use `cluster` or `triage`. Report that the current
   triage result covers ingestion and clustering only.
5. If asked to assess closure evidence, state that this repository cannot yet
   extract or evaluate it. `evidence` and `verify` currently validate
   precomputed records only.
6. Treat deterministic code checks as authoritative over any model opinion.
   This version has no LLM or vision-model stage. Future model opinions must
   never override deterministic evidence checks.
7. Describe a flagged case as **suspect** or **inconclusive**, never as fraud.
   Any future suspect closure finding requires human review. Do not identify
   people in media or repeat complaint PII unnecessarily.

## Examples

From the repository root in PowerShell, after `make install`:

```powershell
py -3.11 -m omnigrid.cli ingest data\synthetic\complaints.geojson
py -3.11 -m omnigrid.cli cluster data\synthetic\complaints.geojson --eps-meters 150 --min-samples 3 --reference-time 2026-10-16T00:00:00Z
py -3.11 -m omnigrid.cli triage data\synthetic\complaints.geojson
py -3.11 -m omnigrid.cli evidence data\samples\evidence-with-gps.json
py -3.11 -m omnigrid.cli verify data\samples\verdict.json
py -3.11 -m omnigrid.cli summarize data\samples\report.json
```

The three last commands validate existing sample contracts; they do not
extract evidence, run proof-of-fix checks, or generate a report. The same
commands can be invoked through `skill/omni-grid/scripts/` wrappers, for
example:

```powershell
py -3.11 skill\omni-grid\scripts\triage.py data\synthetic\complaints.geojson
```

Wrappers locate the checkout from their own path, so they also work when
invoked by absolute path from another working directory. See
`docs/skill-smoke-test.md` for setup and the manual live-agent procedure.
