# Optional FastAPI service

The HTTP service is a thin, unauthenticated local interface to the same
`omnigrid.pipeline.run_pipeline` implementation used by the CLI. The CLI remains
the standard local entry point; installing or running the API is optional.

## Install and configure

From the repository root, install the API together with the development and
pipeline dependencies:

```powershell
python -m pip install -e ".[dev,pipeline,api]"
```

Configuration uses the existing centralized settings and `.env` loading. Copy
`.env.example` to the ignored `.env` file only if you need to customize it.
`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_TEXT_MODEL`, `LLM_VISION_MODEL`,
`LLM_TIMEOUT`, `GEOFENCE_METERS`, `CLUSTER_EPS_METERS`, and
`CLUSTER_MIN_SAMPLES` have the same meaning as for the CLI. Credentials stay
on the server and must never be sent in an upload or returned by the API.

For deterministic, offline operation, enable the pipeline's existing mock
mode. No provider, key, or model download is needed:

```powershell
$env:LLM_MOCK = "true"
```

Start the server bound to localhost:

```powershell
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Stop it with `Ctrl+C`. The API runs synchronous pipeline work in a worker
thread, so it does not block the async request event loop. LLM provider calls
use the shared `LLM_TIMEOUT` setting (30 seconds by default) and existing
client retry behavior; the API does not add an overall request deadline.

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
When providers are unavailable, only the pipeline's existing deterministic
verification and validated template-summary fallback apply; the API does not
invent model evidence or verification outcomes.

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

## Security and deployment limits

This is an unauthenticated local/demo API, not a public production service.
There is no permissive CORS configuration. Keep the listener on localhost.
Public or shared deployment requires additional hardening outside this issue,
including authentication and authorization, TLS and trusted proxy setup,
rate/concurrency controls, operational request deadlines, provider quotas,
privacy/data-retention review, and deployment-specific monitoring. Do not
expose the service to a network without explicitly configuring and reviewing
those controls.

An opt-in live-model smoke test may be performed only after configuring a
trusted compatible provider and server-side credentials in `.env` or process
environment variables. Automated tests use mock mode and do not require live
credentials or external services.
