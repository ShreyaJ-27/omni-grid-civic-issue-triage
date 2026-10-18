# FastAPI service and live dashboard

The HTTP service serves the dashboard and API from one origin. It is
unauthenticated by design for the synthetic hackathon demo; do not treat it as
a municipal production service. Both public routes call the canonical
`omnigrid.pipeline.run_pipeline` in deterministic mock mode. They never send
uploads or images to an inference provider.

## Install and configure

From the repository root, install the API together with the development and
pipeline dependencies:

```powershell
python -m pip install -e ".[dev,pipeline,api]"
```

Configuration uses the existing centralized settings and `.env` loading. Copy
`.env.example` to the ignored `.env` file only if you need to customize it.
The variables read by application code are listed and traced in
[docs/deployment.md](deployment.md). No provider key is required for this
API: public requests explicitly use mock mode.

Start the server bound to localhost:

```powershell
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Stop it with `Ctrl+C`. The API runs synchronous pipeline work in a worker thread and limits
simultaneous pipeline runs to two. Since it uses mock mode, it makes no LLM
provider calls.

## `GET /health`

Returns process health only. It does not contact LLM or vision providers:

```json
{"status": "ok", "service": "omnigrid-api"}
```

## `POST /triage`

Send exactly one `multipart/form-data` file field named `file`. The filename
must end in `.geojson` or `.json`; accepted media types are
`application/geo+json`, `application/json`, and `application/octet-stream`.
The content must be UTF-8 GeoJSON containing a `Feature` or a non-empty
`FeatureCollection` of Point features. Each feature's properties must satisfy
the shared `Complaint` schema. Coordinates are GeoJSON `[longitude, latitude]`
and are checked against valid geographic ranges. The maximum file size is
2 MiB; the complete multipart body is independently capped at 2 MiB plus 64
KiB.

Example:

```powershell
curl.exe -X POST http://127.0.0.1:8000/triage `
  -F "file=@data/synthetic/complaints.geojson;type=application/geo+json"
```

The response is the shared `Report` schema: `hotspots`, `verdicts`, `claims`,
`stats`, and `complaints`. It is validated by the pipeline before serialization.
`stats.model_mode`, `stats.summary_fallback`, `stats.stage_status`, and related
pipeline statistics describe whether inference ran, was skipped, or fell back.
The public API explicitly uses deterministic mock mode: no damage scoring or
vision inference is claimed, and the summary is generated using the
pipeline's mock-mode template.

Complaint media is not uploaded by this endpoint. Relative media references
are constrained to an empty per-request temporary directory, so the service
cannot read arbitrary server files; references to unavailable media are handled
and counted by the canonical pipeline. Absolute and parent-traversal media
paths and filenames containing path components are rejected. Temporary
directories are unique per request and cleaned up after success or failure.
Multipart clients may normalize drive-prefixed names before the application
receives them; uploaded filenames are never used as server paths.

### Errors

| Status | Meaning |
| --- | --- |
| `400` | Invalid multipart request, empty file, or unsafe filename |
| `413` | File or complete request exceeds its size limit |
| `415` | Unsupported file extension or media type |
| `422` | Malformed GeoJSON, unsupported geometry, invalid coordinates, or complaint-schema failure |
| `500` | Unexpected server-side failure; response omits exception details |

The endpoint does not log uploaded content or personal complaint text. Server
diagnostics include an opaque request identifier and exception type only.

## `POST /demo`

The dashboard's **Run Demo** action calls this endpoint. It loads
`data/synthetic/complaints.geojson` from the checked-in repository and runs
the same canonical pipeline in mock mode. It accepts no request body and no
caller-supplied file path. The response is the actual schema-validated `Report`
generated for that request; it is not `web/demo/report.json`.

## Dashboard and public-service limits

`GET /` serves `web/index.html` and the frontend assets from the same origin.
The dashboard displays only API responses. A failed API request clears any
previously displayed report and shows a timeout, validation, busy, or
unavailable message rather than falling back to the offline export.

Both pipeline routes share a two-request concurrency cap and a process-local
limit of 30 requests per client address per minute. The upload path also caps
request size, file size, and feature count. The process-local limiter is
best-effort and is not a substitute for provider/platform DDoS controls; it
can behave differently behind reverse proxies or with multiple workers.

## Security and deployment limits

The Render configuration is for a public synthetic demo, not unrestricted
production use. No permissive CORS configuration is enabled, and credentials
or privileged filesystem operations are not exposed. Uploads cannot access
server media paths; no uploaded media is persisted. Free-tier instances may
cold-start, restart, and lose ephemeral files. Do not submit sensitive
personal data. Production municipal use would require a separate privacy,
availability, identity/access, observability, and operational security review.

Real inference remains available to local Python/CLI workflows when a
compatible provider is explicitly configured. The public HTTP demo remains
mock-only to bound costs and avoid sending public uploads to a third party.
