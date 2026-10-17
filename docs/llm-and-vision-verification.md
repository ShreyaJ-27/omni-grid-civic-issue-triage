# Provider-Agnostic LLM Client & Vision-Based Closure Verification

Omni-Grid provides a provider-agnostic LLM client (`core/omnigrid/llm.py`) and a vision-assisted Proof-of-Fix verification module (`core/omnigrid/verify.py`). This architecture allows seamless switching between local open-weight runtimes (such as Ollama) and hosted OpenAI-compatible APIs using configuration alone.

---

## 1. Environment Variables & Safe Configuration

Endpoint, key, model, and geospatial settings are managed through environment
variables or a local `.env` file (copied from `.env.example`). `LLM_MOCK` and
`LLM_TIMEOUT` are process-environment settings only. The `.env` file is ignored
by Git; secrets or live tokens must never be committed to source control.

| Environment Variable | Default | Purpose |
|----------------------|---------|---------|
| `LLM_BASE_URL` | `http://localhost:11434/v1` | Base URL for an OpenAI-compatible endpoint. |
| `LLM_API_KEY` | `ollama` | API authentication key or local placeholder. |
| `LLM_TEXT_MODEL` | `qwen2.5:7b-instruct` | Model identifier used for text generation and structured summaries. |
| `LLM_VISION_MODEL` | `qwen2.5vl:7b` | Model identifier used for image inspection and pair comparison. |
| `LLM_TIMEOUT` | `30.0` seconds | Request timeout. Read from the process environment only. |
| `LLM_MOCK` | `false` | Enables offline mock mode. Read from the process environment only. |

The current configuration loader reads endpoint, key, model, and geospatial
settings from `.env` (then lets process environment variables override them).
`LLM_TIMEOUT` and `LLM_MOCK` are read directly from the process environment;
putting either value in `.env` does not change runtime behavior. Set them in
the shell that starts the CLI, exporter, API, or evaluation command.

---

## 2. Local Ollama Setup

To run locally with open-weight models:

1. Install [Ollama](https://ollama.com).
2. Pull the recommended open-weight text and vision models:
   ```bash
   ollama pull qwen2.5:7b-instruct
   ollama pull qwen2.5vl:7b
   ```
3. Start the Ollama server:
   ```bash
   ollama serve
   ```
4. Verify the endpoint responds:
   ```bash
   curl http://localhost:11434/v1/models
   ```
5. Default `.env.example` settings point directly to Ollama at `http://localhost:11434/v1`.

---

## 3. Hosted OpenAI-Compatible Provider Setup

For a hosted provider, use an endpoint that supports the OpenAI-compatible
chat-completion requests and, if using vision, image input required by the
configured model. Set the provider's documented base URL, model identifiers,
and API key; provider compatibility is not certified by this project:

```bash
export LLM_BASE_URL="https://<provider-base-url>/v1"
export LLM_API_KEY="<your-provider-key>"
export LLM_TEXT_MODEL="<provider-text-model-id>"
export LLM_VISION_MODEL="<provider-vision-model-id>"
```

The client will automatically route completions and vision requests to the hosted URL with appropriate authorization headers.

---

## 4. Text and Vision Model Compatibility

- **Text Operations**: Use `LLM_TEXT_MODEL`. This model is expected to support standard chat completions and structured JSON output.
- **Vision Operations**: Use `LLM_VISION_MODEL`. This model must support OpenAI-compatible image content blocks (`{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}`).
- The client validates and resizes oversized image payloads (default maximum dimension: 1536px) to avoid request payload limits.
- Never assume a text model supports vision; the client explicitly routes vision operations to `vision_model`.

---

## 5. Mock Mode & Offline Testing

For local development and automated CI environments without GPU runtimes or external network access, enable mock mode:

```bash
export LLM_MOCK=true
```

In Python:
```python
from core.omnigrid.llm import get_llm_client

client = get_llm_client(mock=True)
response = client.generate_text("Summarize issue")
```

Mock mode guarantees deterministic outputs, eliminates network latency, and supports custom response injection for test assertions.

---

## 6. Timeout and Retry Behavior

The client implements strict reliability policies:
- **Explicit Timeout**: Set via `LLM_TIMEOUT` (default: 30.0s).
- **At Most One Retry**: Transient errors—such as network connection errors (`APIConnectionError`), timeouts (`APITimeoutError`), rate limits (`RateLimitError`), and provider 5xx errors (`InternalServerError`)—are retried exactly once.
- **No Retries for Permanent Failures**: Authentication failures (401/403) and invalid client requests (400) immediately raise domain exceptions (`LLMAuthenticationError`, `LLMInvalidRequestError`) without retrying.
- **Secret Redaction**: API keys and tokens are automatically redacted from error messages, exceptions, and `__repr__` output.

---

## 7. Vision-Verification Output and Limitations

The vision closure check (`check_vision_consistency`) compares the initial complaint photo with the submitted proof-of-work photo:

```python
from core.omnigrid.verify import check_vision_consistency

check = check_vision_consistency(
    original_media_path="data/synthetic/media/media_01.jpg",
    closure_media_path="data/synthetic/media/media_06.jpg",
)
```

The vision model returns structured JSON:
- `same_location_likely` (bool): Whether both images appear to depict the same location.
- `defect_repaired` (bool): Whether the visible defect appears repaired.
- `confidence` (float between 0.0 and 1.0): Assessment certainty.

**Limitations**:
- The vision check provides an automated estimate, not authoritative geographic proof.
- If confidence is below threshold (default: 0.60) or visual evidence is ambiguous, the check returns `passed=None` (inconclusive).
- Malformed model responses or timeouts result in an inconclusive check rather than crashing the pipeline.

---

## 8. Deterministic-Check Precedence (Mandatory)

**A vision model output must never override a failed deterministic check.**

Proof-of-Fix executes layered checks:
1. `gps_geofence`: Closure photo EXIF coordinates within geofence radius.
2. `duplicate_evidence`: Perceptual hash (pHash) uniqueness against ticket and peer closures.
3. `timestamp_sanity`: Capture timestamp coherent with ticket creation.
4. `category_consistency`: Defect type aligns with complaint category.
5. `vision_consistency`: Vision model location and repair comparison.

If any deterministic check fails (e.g. GPS coordinates are 20km away), the overall verdict **remains `suspect`**, regardless of whether the vision model claims the repair is valid. Cases marked `suspect` indicate a requirement for human review; complaints are never labeled as fraudulent.

---

## 9. Cache Behavior and Invalidation

To prevent expensive, redundant model inference:
- Vision comparisons are cached in `data/cache/vision_cache.json` (configurable).
- The cache key is derived from:
  $$\text{SHA-256}(\text{prompt\_version} + \text{provider/model identity} + \text{image1\_bytes} + \text{image2\_bytes})$$
- Swapping image order changes the key, preserving order-dependent comparisons.
- Altering image bytes, changing the endpoint or vision model, switching between mock and live mode, or updating the prompt version automatically invalidates the cache key.
- Corrupted cache files or unwriteable storage do not crash verification; the system gracefully falls back to direct inference.
- Only valid, schema-conforming assessments are cached.

---

## 10. Privacy and Model Licensing Considerations

- **Privacy**: The code does not add complaint contact fields to model prompts,
  but selected image bytes are sent for vision requests and may themselves
  contain faces, license plates, or other identifying details. Remove or
  anonymize such content where appropriate and do not send sensitive images to
  an external provider without authorization.
- **Processing location**: The default endpoint is local Ollama, but the client can be configured for a hosted OpenAI-compatible provider. Vision requests transmit selected image bytes, and text requests transmit their prompts and computed facts, to the configured endpoint. Confirm authorization and the provider's retention terms before sending non-synthetic or personally identifying content. Mock mode makes no network requests.
- **Media paths**: Bulk evidence extraction confines complaint media to `base_dir` (or the current working directory when omitted). Closure verification confines complaint media to `media_dir` (or the repository root when omitted). Traversal paths, symlinks resolving outside that root, and absolute paths outside it are rejected; rejected closure evidence produces an inconclusive verdict. Pass the trusted media directory when processing a dataset outside the repository.
- **Model Licensing**: Review the selected model's current model card and license, and the provider's terms, before use. This repository does not certify model licenses or hosted-provider terms.
