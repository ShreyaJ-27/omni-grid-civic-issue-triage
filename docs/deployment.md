# Render deployment and environment handoff

This prepares a public synthetic demo. It does not claim a Render service has
been created or deployed. The dashboard and API use one origin and require no
account or provider key. Public API routes call the canonical pipeline in
deterministic mock mode.

## Environment-variable inventory

The following application variables were traced in `core/omnigrid/config.py`
and `core/omnigrid/llm.py`. Optional inference settings are used by local CLI
and Python-library execution; the public HTTP API explicitly runs in mock mode.

### Required for the public synthetic demo

| Variable | Required? / demo use | Purpose and safe value | Secret / where to get it | Missing or invalid behavior / startup |
| --- | --- | --- | --- | --- |
| None | No application variable is required for the synthetic demo. | Render Blueprint sets `LLM_MOCK=true` as a defensive default, but the public API explicitly selects mock mode. | No secret or third-party account is needed. | The app starts without provider credentials. The demo runs the checked-in data and deterministic checks/templates. |

### Optional inference-provider variables

| Variable | Required? / demo use | Purpose and safe example | Secret / acquisition | Missing or invalid behavior / startup |
| --- | --- | --- | --- | --- |
| `LLM_BASE_URL` | Optional; not used for public API inference. | OpenAI-compatible API base URL; e.g. `https://provider.example/v1` (placeholder only). Read from `.env` or process environment. | Not normally secret; obtain the exact endpoint from the provider's official API documentation. | Defaults to `http://localhost:11434/v1`; a live local CLI call fails if no provider is listening. App starts. |
| `LLM_API_KEY` | Optional; not used by mock API. | Provider credential; defaults to the non-secret local placeholder `ollama`. | Secret when it is an actual credential. Create it in the selected provider's official developer dashboard; do not paste it into chat or commit it. | Live provider calls may fail authentication; app starts. |
| `LLM_TEXT_MODEL` | Optional; not used by mock API. | Text model identifier; default `qwen2.5:7b-instruct`. | Not secret. Use a model identifier supported by the configured provider. | Live requests may be rejected or fall back to a validated template; app starts. |
| `LLM_VISION_MODEL` | Optional; not used by mock API. | Vision model identifier; default `qwen2.5vl:7b`. | Not secret. Use a provider model that accepts image input. | Vision stages may fail/skip; app starts. |
| `LLM_MOCK` | Optional; set to `true` by Render config. | Process-environment-only switch; `1`, `true`, `yes`, or `on` enables mock mode. | Not secret; set manually only to change non-API pipeline defaults. | Missing/other values disable mock mode for local CLI/library flows. API routes still explicitly choose mock mode. App starts. |
| `LLM_TIMEOUT` | Optional; not used in API mock mode. | Provider request timeout in seconds; safe example `30`. Process-environment-only. | Not secret. Choose according to provider and request limits. | Missing uses the library default (30 seconds); unparsable values also use that default. App starts. |

No specific external inference provider or model was live-tested or certified.
No external model-provider API key is needed for the default public synthetic
demo. If optional live inference is configured locally, follow the chosen
provider's current official dashboard instructions and terms; there are no
provider-specific free-tier claims in this project.

### Optional tuning variables

| Variable | Required? / demo use | Purpose and default | Secret / how to set | Missing or invalid behavior / startup |
| --- | --- | --- | --- | --- |
| `GEOFENCE_METERS` | Optional; public API uses the default. | Closure-photo distance threshold; `50`. `.env` or process environment. | Not secret. Set a positive finite number. | Missing uses default; invalid or non-positive values raise `ValueError` when settings load. App process can start, but a pipeline request can fail. |
| `CLUSTER_EPS_METERS` | Optional; public API uses the default. | DBSCAN neighborhood radius; `150`. `.env` or process environment. | Not secret. Set a positive finite number. | Missing uses default; invalid or non-positive values raise `ValueError` when settings load. |
| `CLUSTER_MIN_SAMPLES` | Optional; public API uses the default. | Minimum complaint count per cluster; `3`. `.env` or process environment. | Not secret. Set a positive integer. | Missing uses default; invalid or non-positive values raise `ValueError` when settings load. |

### Render-managed values

| Name | Purpose | Action |
| --- | --- | --- |
| `PORT` | Assigned by Render and passed to Uvicorn by the start command. | Do not add or hard-code it in the Render environment. |
| `PYTHON_VERSION` | Render build runtime selection; Blueprint pins `3.11.11`. | Keep the Blueprint value unless intentionally changing the Python runtime. It is not an app setting. |

Render environment variables are entered in the Web Service's **Environment**
tab when needed. Do not add any key for the default demo. If you later enable
live provider inference in a separate trusted environment, enter the actual
provider key as a secret environment variable there; do not enable it on the
public API without a separate cost and privacy review.

## Render Blueprint settings

The checked-in [`render.yaml`](../render.yaml) specifies:

- Runtime: Python
- Build: `python -m pip install -e ".[api,pipeline]"`
- Start: `uvicorn api.main:app --host 0.0.0.0 --port $PORT`
- Health check: `/health`
- Mock mode: enabled
- Plan: Free

The dependency extras are defined in `pyproject.toml`; the project name does
not need to be guessed for pip because the editable install targets the checked
out repository.

## Manual deployment steps

1. Review and merge the implementation PR yourself. Confirm its target is
   `main` and its checks are green before continuing.
2. Open [dashboard.render.com](https://dashboard.render.com/) and sign in or
   create a Render account.
3. Choose **New +** → **Blueprint** and connect the GitHub account/repository
   `ShreyaJ-27/omni-grid-civic-issue-triage`. Alternatively create a **Web
   Service** and enter the settings below manually.
4. Select the merged `main` branch. If creating a Web Service manually, select
   **Python** runtime and use the build, start, and health-check values above.
   Choose the Free plan if available for your account.
5. Do not add inference keys. Keep `LLM_MOCK=true`; the Blueprint sets it.
   No signing key, password, database, or other secret is required.
6. Save/create the service and allow the first build to finish. The first
   build may take longer while dependencies install. Do not consider it
   deployed successfully until checks below pass.
7. Copy the HTTPS URL shown on the Render service page. That is the actual
   public URL; no URL is prefilled in this repository.
8. Open `<your-render-url>/health`; expect
   `{"status":"ok","service":"omnigrid-api"}`.
9. Open the base URL, choose **Run Demo**, and wait up to three
   minutes for a Free-tier cold start. Confirm the dashboard shows a 500
   complaint synthetic report and mock-mode label.
10. For an upload check, use `data/synthetic/complaints.geojson` locally and
    choose it in the dashboard. Confirm the report updates from that API
    response. Never upload real personal complaint data for this demo.
11. Check that Leaflet assets and OpenStreetMap tiles load; those external
    browser requests need network access. Confirm filters, hotspot selection,
    claims, and inspection evidence render.

## Troubleshooting and limitations

- **Build fails:** inspect Render build logs and confirm the service checks out
  the merged branch and uses Python 3.11+. Native pipeline packages are
  installed through the declared `pipeline` extra.
- **Startup fails:** confirm the start command uses `api.main:app`, binds
  `0.0.0.0`, and uses `$PORT`; inspect startup logs for import/config errors.
- **Health passes but demo fails:** inspect the service logs and retry once.
  The dashboard shows API errors and never substitutes the offline report.
- **Cold start / request timeout:** Free instances may spin down; first
  requests can take up to a few minutes. The browser allows 180 seconds and
  tells the user to retry if it expires.
- **Provider failure:** the public API intentionally does not call a provider.
  Local real-inference issues require an explicitly configured compatible
  endpoint, key, and model. Provider/model combinations have not been live
  verified here.
- **Free tier limitations:** instance availability, CPU, memory, request
  duration, and ephemeral filesystem are platform-controlled and can change.
  Uploads are not saved by the app, and in-memory rate-limit state resets on
  restart and is per process. If the free service cannot meet the desired
  latency or capacity, use a suitable paid instance or a separately reviewed
  deployment architecture.

## Judge demo

1. Open the HTTPS URL from the Render service page; there is no login or signup.
2. Click **Run Demo** and wait for the API response.
3. Inspect report stats, hotspot clusters, summary claims/citations, and the
   **Inspection & Checks** panel. The report is generated by the pipeline in
   deterministic mock mode; it is synthetic, not live municipal data.
4. Optionally upload supported GeoJSON (UTF-8 Feature/FeatureCollection with
   Point features, max 2 MiB and 1,000 features).
5. Install the portable skill after its PR is merged with:

   ```powershell
   npx skills add https://github.com/ShreyaJ-27/omni-grid-civic-issue-triage/tree/main/skill/omni-grid --skill omni-grid
   ```

   The installer will ask which supported agent to configure. Invoke it with a
   checked-in synthetic input or a trusted local file. Agent-specific automatic
   discovery was not tested as part of this implementation.
