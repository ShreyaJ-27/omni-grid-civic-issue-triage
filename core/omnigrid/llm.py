"""Provider-agnostic LLM client for text and vision operations using OpenAI SDK.

Supports local Ollama through its OpenAI-compatible endpoint as default,
as well as compatible hosted OpenAI-compatible providers via environment configuration.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, TypeVar

from PIL import Image
from pydantic import BaseModel, ValidationError

try:
    from openai import (
        APIConnectionError,
        APITimeoutError,
        AuthenticationError,
        BadRequestError,
        InternalServerError,
        OpenAI,
        PermissionDeniedError,
        RateLimitError,
    )
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore[assignment, misc]
    APIConnectionError = Exception  # type: ignore[assignment, misc]
    APITimeoutError = Exception  # type: ignore[assignment, misc]
    AuthenticationError = Exception  # type: ignore[assignment, misc]
    BadRequestError = Exception  # type: ignore[assignment, misc]
    InternalServerError = Exception  # type: ignore[assignment, misc]
    PermissionDeniedError = Exception  # type: ignore[assignment, misc]
    RateLimitError = Exception  # type: ignore[assignment, misc]

from core.omnigrid.config import Settings, get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

DEFAULT_TIMEOUT = 30.0
MAX_IMAGE_DIMENSION = 1536


# -----------------------------------------------------------------------------
# Exceptions
# -----------------------------------------------------------------------------


class LLMError(Exception):
    """Base exception for all LLM client errors."""


class LLMConnectionError(LLMError):
    """Network connection failure to the LLM provider."""


class LLMTimeoutError(LLMError):
    """Request to the LLM provider timed out."""


class LLMAuthenticationError(LLMError):
    """Authentication or permission error with the LLM provider."""


class LLMInvalidRequestError(LLMError):
    """Client-side request or input validation error."""


class LLMProviderError(LLMError):
    """Unexpected provider-side or server failure (e.g. rate limit, 5xx)."""


class LLMResponseError(LLMError):
    """Response validation, decoding, or schema error."""


# -----------------------------------------------------------------------------
# Secret Redaction Helper
# -----------------------------------------------------------------------------


def redact_secrets(text: str, secret: str | None = None) -> str:
    """Redact API keys or sensitive authorization tokens from text/logs."""
    if not text:
        return text
    result = text
    if secret and len(secret) >= 4:
        result = result.replace(secret, "[REDACTED_API_KEY]")
    result = re.sub(r"(Bearer\s+)[A-Za-z0-9_\-\.]{6,}", r"\1[REDACTED]", result)
    result = re.sub(r"(sk-[A-Za-z0-9_\-]{8,})", r"[REDACTED_KEY]", result)
    return result


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class LLMConfig:
    """Runtime configuration for OpenAI-compatible LLM endpoints."""

    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"
    text_model: str = "qwen2.5:7b-instruct"
    vision_model: str = "qwen2.5vl:7b"
    timeout: float = DEFAULT_TIMEOUT
    max_retries: int = 1
    mock: bool = False

    def __repr__(self) -> str:
        masked_key = "***" if self.api_key else "None"
        return (
            f"LLMConfig(base_url={self.base_url!r}, api_key={masked_key!r}, "
            f"text_model={self.text_model!r}, vision_model={self.vision_model!r}, "
            f"timeout={self.timeout}, max_retries={self.max_retries}, mock={self.mock})"
        )

    @classmethod
    def from_settings(
        cls,
        settings: Settings | None = None,
        mock: bool | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> LLMConfig:
        """Construct LLMConfig from centralized Settings."""
        cfg = settings or get_settings()

        mock_flag = mock
        if mock_flag is None:
            mock_env = os.getenv("LLM_MOCK", "").strip().lower()
            mock_flag = mock_env in ("1", "true", "yes", "on")

        req_timeout = timeout
        if req_timeout is None:
            env_timeout = os.getenv("LLM_TIMEOUT")
            if env_timeout:
                try:
                    req_timeout = float(env_timeout)
                except ValueError:
                    req_timeout = DEFAULT_TIMEOUT
            else:
                req_timeout = DEFAULT_TIMEOUT

        retries = max_retries if max_retries is not None else 1

        return cls(
            base_url=cfg.llm_base_url or "http://localhost:11434/v1",
            api_key=cfg.llm_api_key or "ollama",
            text_model=cfg.llm_text_model or "qwen2.5:7b-instruct",
            vision_model=cfg.llm_vision_model or "qwen2.5vl:7b",
            timeout=req_timeout,
            max_retries=retries,
            mock=mock_flag,
        )


# -----------------------------------------------------------------------------
# Image Processing Helper
# -----------------------------------------------------------------------------


def encode_image(
    image_input: str | Path | bytes | Image.Image,
    max_dimension: int = MAX_IMAGE_DIMENSION,
) -> str:
    """Validate, downscale if needed, and base64-encode an image for vision calls.

    Returns a data URI string: 'data:image/jpeg;base64,...'.
    """
    img: Image.Image
    if isinstance(image_input, (str, Path)):
        path = Path(image_input)
        if not path.is_file():
            raise LLMInvalidRequestError(f"Image file does not exist: {path}")
        try:
            img = Image.open(path)
            img.load()
        except Exception as exc:
            raise LLMInvalidRequestError(
                f"Cannot read image file {path}: {exc}"
            ) from exc
    elif isinstance(image_input, bytes):
        try:
            img = Image.open(BytesIO(image_input))
            img.load()
        except Exception as exc:
            raise LLMInvalidRequestError(f"Cannot read image bytes: {exc}") from exc
    elif isinstance(image_input, Image.Image):
        img = image_input
    else:
        raise LLMInvalidRequestError(
            f"Unsupported image input type: {type(image_input)}"
        )

    # Downscale if exceeding maximum dimensions while preserving aspect ratio
    if img.width > max_dimension or img.height > max_dimension:
        img = img.copy()
        img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)

    # Convert to RGB if needed (handling alpha channels and palette images)
    if img.mode != "RGB":
        img = img.convert("RGB")

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=85)
    b64_data = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{b64_data}"


def _extract_json_substring(text: str) -> str:
    """Extract a JSON object substring from raw text or markdown fences."""
    stripped = text.strip()
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    if match:
        return match.group(1).strip()

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        return stripped[start : end + 1].strip()

    return stripped


# -----------------------------------------------------------------------------
# LLM Client
# -----------------------------------------------------------------------------


class LLMClient:
    """Provider-agnostic client supporting text, vision, and structured responses."""

    def __init__(
        self,
        config: LLMConfig | Settings | None = None,
        mock: bool | None = None,
        mock_handler: Callable[[str, list[str] | None], Any] | None = None,
    ) -> None:
        if isinstance(config, Settings):
            self.config = LLMConfig.from_settings(config, mock=mock)
        elif isinstance(config, LLMConfig):
            if mock is not None:
                self.config = LLMConfig(
                    base_url=config.base_url,
                    api_key=config.api_key,
                    text_model=config.text_model,
                    vision_model=config.vision_model,
                    timeout=config.timeout,
                    max_retries=config.max_retries,
                    mock=mock,
                )
            else:
                self.config = config
        else:
            self.config = LLMConfig.from_settings(mock=mock)

        self._mock_handler = mock_handler
        self._mock_responses: list[Any] = []
        self._client: Any = None

        if not self.config.mock:
            if OpenAI is None:  # pragma: no cover
                raise LLMProviderError(
                    "openai package is required for live LLMClient operations."
                )
            self._client = OpenAI(
                base_url=self.config.base_url,
                api_key=self.config.api_key or "ollama",
                timeout=self.config.timeout,
                max_retries=0,  # Handled explicitly by this client
            )

    @property
    def vision_cache_identity(self) -> str:
        """Identify the endpoint, model, and mock/live mode for cached scores."""
        identity = (
            f"{self.config.base_url.rstrip('/')}/{self.config.vision_model}"
        )
        return f"mock:{identity}" if self.config.mock else identity

    def set_mock_response(self, response: Any) -> None:
        """Enqueue a mock response for deterministic testing."""
        self._mock_responses.append(response)

    def _execute_with_retry(self, operation: Callable[[], Any]) -> Any:
        """Execute an API call with at most one retry for transient errors."""
        max_attempts = 1 + (1 if self.config.max_retries > 0 else 0)
        attempts = 0

        while attempts < max_attempts:
            attempts += 1
            try:
                return operation()
            except (AuthenticationError, PermissionDeniedError) as exc:
                msg = redact_secrets(str(exc), self.config.api_key)
                raise LLMAuthenticationError(f"Authentication failed: {msg}") from None
            except BadRequestError as exc:
                msg = redact_secrets(str(exc), self.config.api_key)
                raise LLMInvalidRequestError(f"Invalid request: {msg}") from None
            except APITimeoutError as exc:
                if attempts >= max_attempts:
                    msg = redact_secrets(str(exc), self.config.api_key)
                    raise LLMTimeoutError(
                        f"Request timed out after {self.config.timeout}s: {msg}"
                    ) from None
                logger.warning("Transient timeout during LLM call; retrying once...")
            except APIConnectionError as exc:
                if attempts >= max_attempts:
                    msg = redact_secrets(str(exc), self.config.api_key)
                    raise LLMConnectionError(
                        f"Connection failed to {self.config.base_url}: {msg}"
                    ) from None
                logger.warning(
                    "Transient connection error during LLM call; retrying once..."
                )
            except RateLimitError as exc:
                if attempts >= max_attempts:
                    msg = redact_secrets(str(exc), self.config.api_key)
                    raise LLMProviderError(f"Rate limit exceeded: {msg}") from None
                logger.warning("Rate limit reached; retrying once...")
            except InternalServerError as exc:
                if attempts >= max_attempts:
                    msg = redact_secrets(str(exc), self.config.api_key)
                    raise LLMProviderError(
                        f"Provider internal server error (5xx): {msg}"
                    ) from None
                logger.warning("Provider 5xx error; retrying once...")
            except LLMError:
                raise
            except Exception as exc:
                msg = redact_secrets(str(exc), self.config.api_key)
                raise LLMError(f"LLM operation failed: {msg}") from None

    # -------------------------------------------------------------------------
    # Text Generation
    # -------------------------------------------------------------------------

    def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> str:
        """Generate a text completion using the configured text model."""
        if not prompt or not prompt.strip():
            raise LLMInvalidRequestError("Prompt cannot be empty.")

        target_model = model or self.config.text_model

        if self.config.mock:
            if self._mock_responses:
                resp = self._mock_responses.pop(0)
                if isinstance(resp, Exception):
                    raise resp
                return str(resp)
            if self._mock_handler is not None:
                return str(self._mock_handler(prompt, None))
            return f"Mock text completion for: {prompt[:30]}..."

        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        def call() -> Any:
            kwargs: dict[str, Any] = {
                "model": target_model,
                "messages": messages,
                "temperature": temperature,
            }
            if max_tokens is not None:
                kwargs["max_tokens"] = max_tokens
            return self._client.chat.completions.create(**kwargs)

        response = self._execute_with_retry(call)
        try:
            choice = response.choices[0]
            content = choice.message.content
            return content.strip() if content else ""
        except (AttributeError, IndexError) as exc:
            raise LLMResponseError(
                f"Malformed response format from provider: {response}"
            ) from exc

    # -------------------------------------------------------------------------
    # Image / Vision Operations
    # -------------------------------------------------------------------------

    def analyze_image(
        self,
        image: str | Path | bytes | Image.Image,
        prompt: str,
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
    ) -> str:
        """Analyze a single image with the configured vision model."""
        return self.analyze_images(
            images=[image],
            prompt=prompt,
            system_prompt=system_prompt,
            model=model,
            temperature=temperature,
        )

    def analyze_images(
        self,
        images: list[str | Path | bytes | Image.Image],
        prompt: str,
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
    ) -> str:
        """Analyze one or more images with the configured vision model."""
        if not images:
            raise LLMInvalidRequestError(
                "At least one image is required for vision analysis."
            )
        if not prompt or not prompt.strip():
            raise LLMInvalidRequestError("Prompt cannot be empty for vision analysis.")

        target_model = model or self.config.vision_model

        encoded_images = [encode_image(img) for img in images]

        if self.config.mock:
            if self._mock_responses:
                resp = self._mock_responses.pop(0)
                if isinstance(resp, Exception):
                    raise resp
                return str(resp)
            if self._mock_handler is not None:
                return str(self._mock_handler(prompt, encoded_images))
            return f"Mock vision ({len(images)} images) for: {prompt[:30]}..."

        user_content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for enc in encoded_images:
            user_content.append({"type": "image_url", "image_url": {"url": enc}})

        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_content})

        def call() -> Any:
            return self._client.chat.completions.create(
                model=target_model,
                messages=messages,
                temperature=temperature,
            )

        response = self._execute_with_retry(call)
        try:
            choice = response.choices[0]
            content = choice.message.content
            return content.strip() if content else ""
        except (AttributeError, IndexError) as exc:
            raise LLMResponseError(
                f"Malformed vision response from provider: {response}"
            ) from exc

    def compare_images(
        self,
        image1: str | Path | bytes | Image.Image,
        image2: str | Path | bytes | Image.Image,
        prompt: str,
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
    ) -> str:
        """Compare two images (e.g. before/after) using the vision model."""
        return self.analyze_images(
            images=[image1, image2],
            prompt=prompt,
            system_prompt=system_prompt,
            model=model,
            temperature=temperature,
        )

    # -------------------------------------------------------------------------
    # Structured JSON Generation
    # -------------------------------------------------------------------------

    def generate_structured(
        self,
        prompt: str,
        response_model: type[T],
        system_prompt: str | None = None,
        images: list[str | Path | bytes | Image.Image] | None = None,
        model: str | None = None,
        temperature: float = 0.0,
    ) -> T:
        """Generate structured JSON validated against a Pydantic model."""
        target_model = model or (
            self.config.vision_model if images else self.config.text_model
        )

        encoded_images = [encode_image(img) for img in images] if images else []

        if self.config.mock:
            if self._mock_responses:
                resp = self._mock_responses.pop(0)
                if isinstance(resp, Exception):
                    raise resp
                if isinstance(resp, response_model):
                    return resp
                if isinstance(resp, dict):
                    try:
                        return response_model.model_validate(resp)
                    except ValidationError as exc:
                        raise LLMResponseError(
                            f"Mock response failed schema validation: {exc}"
                        ) from exc
                if isinstance(resp, str):
                    try:
                        parsed = json.loads(_extract_json_substring(resp))
                        return response_model.model_validate(parsed)
                    except (json.JSONDecodeError, ValidationError) as exc:
                        raise LLMResponseError(
                            f"Mock string response invalid for "
                            f"{response_model.__name__}: {exc}"
                        ) from exc

            if self._mock_handler is not None:
                handler_result = self._mock_handler(prompt, encoded_images or None)
                if isinstance(handler_result, response_model):
                    return handler_result
                if isinstance(handler_result, dict):
                    return response_model.model_validate(handler_result)

            return self._build_default_mock_model(response_model)

        user_content: Any
        if encoded_images:
            user_content = [{"type": "text", "text": prompt}]
            for enc in encoded_images:
                user_content.append({"type": "image_url", "image_url": {"url": enc}})
        else:
            user_content = prompt

        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_content})

        def call_json_mode() -> Any:
            try:
                return self._client.chat.completions.create(
                    model=target_model,
                    messages=messages,
                    temperature=temperature,
                    response_format={"type": "json_object"},
                )
            except BadRequestError:
                logger.info(
                    "Provider does not support response_format='json_object'; "
                    "falling back to standard completion."
                )
                return self._client.chat.completions.create(
                    model=target_model,
                    messages=messages,
                    temperature=temperature,
                )

        response = self._execute_with_retry(call_json_mode)
        try:
            choice = response.choices[0]
            raw_text = choice.message.content or ""
        except (AttributeError, IndexError) as exc:
            raise LLMResponseError(f"Malformed provider response: {response}") from exc

        json_str = _extract_json_substring(raw_text)
        try:
            parsed_data = json.loads(json_str)
        except json.JSONDecodeError as exc:
            raise LLMResponseError(
                f"Response content is not valid JSON: {raw_text[:200]}"
            ) from exc

        if not isinstance(parsed_data, dict):
            raise LLMResponseError(
                f"Expected JSON object for {response_model.__name__}, "
                f"got {type(parsed_data).__name__}"
            )

        try:
            return response_model.model_validate(parsed_data)
        except ValidationError as exc:
            raise LLMResponseError(
                f"Structured response failed validation for "
                f"{response_model.__name__}: {exc}"
            ) from exc

    def compare_images_structured(
        self,
        image1: str | Path | bytes | Image.Image,
        image2: str | Path | bytes | Image.Image,
        prompt: str,
        response_model: type[T],
        system_prompt: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
    ) -> T:
        """Compare two images and return a validated Pydantic model."""
        return self.generate_structured(
            prompt=prompt,
            response_model=response_model,
            system_prompt=system_prompt,
            images=[image1, image2],
            model=model,
            temperature=temperature,
        )

    def _build_default_mock_model(self, model_cls: type[T]) -> T:
        """Generate a sensible, deterministic mock model instance."""
        data: dict[str, Any] = {}
        for name, field in model_cls.model_fields.items():
            if field.default is not None and not repr(field.default).startswith(
                "PydanticUndefined"
            ):
                data[name] = field.default
            elif field.annotation is bool:
                data[name] = True
            elif field.annotation in (float, float | None):
                data[name] = 0.90
            elif field.annotation in (int, int | None):
                data[name] = 1
            elif field.annotation in (str, str | None):
                data[name] = "Mock value"
            else:
                data[name] = None
        try:
            return model_cls.model_validate(data)
        except ValidationError:
            return model_cls.model_validate({})


# -----------------------------------------------------------------------------
# Convenience Factory
# -----------------------------------------------------------------------------


def get_llm_client(
    config: LLMConfig | Settings | None = None,
    mock: bool | None = None,
) -> LLMClient:
    """Retrieve an initialized LLMClient instance."""
    return LLMClient(config=config, mock=mock)
