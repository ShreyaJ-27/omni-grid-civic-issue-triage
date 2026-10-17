"""Unit tests for provider-agnostic LLM client (Issue #12).

All tests run deterministically without network access, live API keys,
Ollama, or downloaded models.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from core.omnigrid.config import Settings
from core.omnigrid.llm import (
    LLMAuthenticationError,
    LLMConfig,
    LLMConnectionError,
    LLMInvalidRequestError,
    LLMProviderError,
    LLMResponseError,
    encode_image,
    get_llm_client,
    redact_secrets,
)
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)
from PIL import Image
from pydantic import BaseModel, Field


class DummySchema(BaseModel):
    name: str
    score: float = Field(ge=0.0, le=1.0)
    flag: bool = True


# -----------------------------------------------------------------------------
# Configuration and Secret Masking Tests
# -----------------------------------------------------------------------------


def test_llm_config_defaults_and_repr_masking() -> None:
    config = LLMConfig(api_key="secret-ollama-key-12345")
    repr_str = repr(config)
    assert "secret-ollama-key-12345" not in repr_str
    assert "***" in repr_str
    assert config.timeout == 30.0
    assert config.max_retries == 1


def test_llm_config_from_settings() -> None:
    settings = Settings(
        llm_base_url="https://api.hosted-provider.com/v1",
        llm_api_key="sk-live-hosted-token-9999",
        llm_text_model="hosted-text-v1",
        llm_vision_model="hosted-vision-v1",
        geofence_meters=50.0,
        cluster_eps_meters=150.0,
        cluster_min_samples=3,
    )
    config = LLMConfig.from_settings(settings, mock=True)
    assert config.base_url == "https://api.hosted-provider.com/v1"
    assert config.api_key == "sk-live-hosted-token-9999"
    assert config.text_model == "hosted-text-v1"
    assert config.vision_model == "hosted-vision-v1"
    assert config.mock is True


def test_secret_redaction() -> None:
    secret = "sk-super-secret-api-token-abc"
    msg = f"Error with key {secret} and Bearer abcdef123456"
    redacted = redact_secrets(msg, secret)
    assert secret not in redacted
    assert "[REDACTED_API_KEY]" in redacted
    assert "Bearer abcdef123456" not in redacted


# -----------------------------------------------------------------------------
# Mock Mode Tests (Deterministic, Offline)
# -----------------------------------------------------------------------------


def test_text_generation_mock_mode() -> None:
    client = get_llm_client(mock=True)

    # Default mock text
    out = client.generate_text("Summarize the pothole report.")
    assert "Mock text completion" in out

    # Enqueued mock text
    client.set_mock_response("Custom mocked summary result.")
    custom_out = client.generate_text("Summarize again.")
    assert custom_out == "Custom mocked summary result."


def test_vision_analysis_mock_mode(tmp_path: Path) -> None:
    img_path = tmp_path / "test.jpg"
    img = Image.new("RGB", (64, 64), color="blue")
    img.save(img_path)

    client = get_llm_client(mock=True)

    # Analyze single image
    res1 = client.analyze_image(img_path, "Describe this defect.")
    assert "Mock vision" in res1

    # Compare two images
    img2_path = tmp_path / "test2.jpg"
    img2 = Image.new("RGB", (64, 64), color="green")
    img2.save(img2_path)

    res2 = client.compare_images(img_path, img2_path, "Compare repair.")
    assert "2 images" in res2


def test_structured_generation_mock_mode(tmp_path: Path) -> None:
    client = get_llm_client(mock=True)

    # Default generated schema
    structured = client.generate_structured("Evaluate metric", DummySchema)
    assert isinstance(structured, DummySchema)
    assert structured.score == 0.90
    assert structured.flag is True

    # Custom dict enqueued
    client.set_mock_response({"name": "Pothole A", "score": 0.75, "flag": False})
    custom = client.generate_structured("Evaluate metric", DummySchema)
    assert custom.name == "Pothole A"
    assert custom.score == 0.75
    assert custom.flag is False

    # Custom JSON string enqueued
    client.set_mock_response(
        '```json\n{"name": "Pothole B", "score": 0.50, "flag": true}\n```'
    )
    from_json = client.generate_structured("Evaluate metric", DummySchema)
    assert from_json.name == "Pothole B"
    assert from_json.score == 0.50


# -----------------------------------------------------------------------------
# Image Encoding and Validation Tests
# -----------------------------------------------------------------------------


def test_encode_image_file_and_resizing(tmp_path: Path) -> None:
    # 1. Valid image within bounds
    img_path = tmp_path / "small.jpg"
    img = Image.new("RGB", (100, 100), color="red")
    img.save(img_path)

    uri = encode_image(img_path)
    assert uri.startswith("data:image/jpeg;base64,")

    # 2. Oversized image resizing
    large_path = tmp_path / "large.jpg"
    large_img = Image.new("RGB", (2000, 1000), color="yellow")
    large_img.save(large_path)

    uri_large = encode_image(large_path, max_dimension=800)
    assert uri_large.startswith("data:image/jpeg;base64,")

    # 3. Non-existent file
    with pytest.raises(LLMInvalidRequestError, match="does not exist"):
        encode_image(tmp_path / "nonexistent.jpg")

    # 4. Corrupted file
    corrupt_path = tmp_path / "corrupt.jpg"
    corrupt_path.write_bytes(b"not an image data")
    with pytest.raises(LLMInvalidRequestError, match="Cannot read image"):
        encode_image(corrupt_path)


def test_invalid_requests() -> None:
    client = get_llm_client(mock=True)

    with pytest.raises(LLMInvalidRequestError, match="Prompt cannot be empty"):
        client.generate_text("   ")

    with pytest.raises(LLMInvalidRequestError, match="At least one image"):
        client.analyze_images([], "Prompt")


# -----------------------------------------------------------------------------
# Retry and Error Handling Tests (with Mocked SDK Calls)
# -----------------------------------------------------------------------------


def test_one_retry_for_transient_timeout() -> None:
    cfg = LLMConfig(api_key="secret-key-xyz", mock=False)
    client = get_llm_client(config=cfg)

    # Mock the internal OpenAI client
    mock_sdk = MagicMock()
    fake_choice = MagicMock()
    fake_choice.message.content = "Success after retry"
    fake_resp = MagicMock(choices=[fake_choice])

    # First call times out, second succeeds
    mock_sdk.chat.completions.create.side_effect = [
        APITimeoutError(request=MagicMock()),
        fake_resp,
    ]
    client._client = mock_sdk

    result = client.generate_text("Hello")
    assert result == "Success after retry"
    assert mock_sdk.chat.completions.create.call_count == 2


def test_transient_failure_exhausts_one_retry() -> None:
    cfg = LLMConfig(api_key="secret-key-xyz", mock=False)
    client = get_llm_client(config=cfg)

    mock_sdk = MagicMock()
    mock_sdk.chat.completions.create.side_effect = [
        APIConnectionError(request=MagicMock()),
        APIConnectionError(request=MagicMock()),
    ]
    client._client = mock_sdk

    with pytest.raises(LLMConnectionError) as exc_info:
        client.generate_text("Hello")

    assert "Connection failed" in str(exc_info.value)
    assert "secret-key-xyz" not in str(exc_info.value)
    # Exactly 2 calls (initial + 1 retry)
    assert mock_sdk.chat.completions.create.call_count == 2


def test_no_retry_for_permanent_auth_failure() -> None:
    cfg = LLMConfig(api_key="bad-auth-key-123", mock=False)
    client = get_llm_client(config=cfg)

    mock_sdk = MagicMock()
    mock_sdk.chat.completions.create.side_effect = AuthenticationError(
        message="Invalid API Key bad-auth-key-123",
        response=MagicMock(status_code=401),
        body=None,
    )
    client._client = mock_sdk

    with pytest.raises(LLMAuthenticationError) as exc_info:
        client.generate_text("Hello")

    assert "Authentication failed" in str(exc_info.value)
    assert "bad-auth-key-123" not in str(exc_info.value)
    assert "[REDACTED_API_KEY]" in str(exc_info.value)
    # Must NOT retry auth failure
    assert mock_sdk.chat.completions.create.call_count == 1


def test_no_retry_for_bad_request() -> None:
    cfg = LLMConfig(mock=False)
    client = get_llm_client(config=cfg)

    mock_sdk = MagicMock()
    mock_sdk.chat.completions.create.side_effect = BadRequestError(
        message="Unknown model requested",
        response=MagicMock(status_code=400),
        body=None,
    )
    client._client = mock_sdk

    with pytest.raises(LLMInvalidRequestError) as exc_info:
        client.generate_text("Hello")

    assert "Invalid request" in str(exc_info.value)
    assert mock_sdk.chat.completions.create.call_count == 1


def test_rate_limit_and_5xx_retries() -> None:
    cfg = LLMConfig(mock=False)
    client = get_llm_client(config=cfg)

    mock_sdk = MagicMock()
    mock_sdk.chat.completions.create.side_effect = [
        RateLimitError(
            message="Rate limit",
            response=MagicMock(status_code=429),
            body=None,
        ),
        InternalServerError(
            message="Server overloaded",
            response=MagicMock(status_code=503),
            body=None,
        ),
    ]
    client._client = mock_sdk

    with pytest.raises(LLMProviderError) as exc_info:
        client.generate_text("Hello")

    assert "Provider internal server error" in str(exc_info.value)
    assert mock_sdk.chat.completions.create.call_count == 2


# -----------------------------------------------------------------------------
# Structured Output Validation and Failure Handling
# -----------------------------------------------------------------------------


def test_malformed_json_response_raises_llm_response_error() -> None:
    cfg = LLMConfig(mock=False)
    client = get_llm_client(config=cfg)

    fake_choice = MagicMock()
    fake_choice.message.content = "Here is your response: NOT JSON AT ALL"
    fake_resp = MagicMock(choices=[fake_choice])

    mock_sdk = MagicMock()
    mock_sdk.chat.completions.create.return_value = fake_resp
    client._client = mock_sdk

    with pytest.raises(LLMResponseError, match="not valid JSON"):
        client.generate_structured("Get schema", DummySchema)


def test_schema_mismatch_raises_llm_response_error() -> None:
    cfg = LLMConfig(mock=False)
    client = get_llm_client(config=cfg)

    fake_choice = MagicMock()
    # Missing required field 'name', and invalid 'score' (> 1.0)
    fake_choice.message.content = '{"score": 99.5, "flag": true}'
    fake_resp = MagicMock(choices=[fake_choice])

    mock_sdk = MagicMock()
    mock_sdk.chat.completions.create.return_value = fake_resp
    client._client = mock_sdk

    with pytest.raises(LLMResponseError, match="failed validation"):
        client.generate_structured("Get schema", DummySchema)


def test_model_selection_for_text_and_vision() -> None:
    cfg = LLMConfig(
        text_model="custom-text-model",
        vision_model="custom-vision-model",
        mock=False,
    )
    client = get_llm_client(config=cfg)

    fake_choice = MagicMock()
    fake_choice.message.content = "Done"
    fake_resp = MagicMock(choices=[fake_choice])

    mock_sdk = MagicMock()
    mock_sdk.chat.completions.create.return_value = fake_resp
    client._client = mock_sdk

    # 1. Text call uses text_model
    client.generate_text("Test prompt")
    call_args = mock_sdk.chat.completions.create.call_args[1]
    assert call_args["model"] == "custom-text-model"

    # 2. Vision call uses vision_model
    img = Image.new("RGB", (10, 10))
    client.analyze_image(img, "Examine image")
    call_args_v = mock_sdk.chat.completions.create.call_args[1]
    assert call_args_v["model"] == "custom-vision-model"

    # 3. Explicit override works
    client.generate_text("Test prompt", model="override-model")
    call_args_o = mock_sdk.chat.completions.create.call_args[1]
    assert call_args_o["model"] == "override-model"
