"""Environment-backed runtime configuration for Omni-Grid."""

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

_DEFAULTS = {
    "LLM_BASE_URL": "http://localhost:11434/v1",
    "LLM_API_KEY": "ollama",
    "LLM_TEXT_MODEL": "qwen2.5:7b-instruct",
    "LLM_VISION_MODEL": "qwen2.5vl:7b",
    "GEOFENCE_METERS": "50",
    "CLUSTER_EPS_METERS": "150",
    "CLUSTER_MIN_SAMPLES": "3",
}


def _optional_string(values: Mapping[str, str], key: str) -> str | None:
    value = values.get(key, _DEFAULTS[key]).strip()
    return value or None


def _positive_float(values: Mapping[str, str], key: str) -> float:
    try:
        value = float(values.get(key, _DEFAULTS[key]))
    except ValueError:
        raise ValueError(f"{key} must be a finite number greater than 0") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{key} must be a finite number greater than 0")
    return value


def _positive_int(values: Mapping[str, str], key: str) -> int:
    try:
        value = int(values.get(key, _DEFAULTS[key]))
    except ValueError:
        raise ValueError(f"{key} must be an integer greater than 0") from None
    if value <= 0:
        raise ValueError(f"{key} must be an integer greater than 0")
    return value


@dataclass(frozen=True)
class Settings:
    llm_base_url: str | None
    llm_api_key: str | None
    llm_text_model: str | None
    llm_vision_model: str | None
    geofence_meters: float
    cluster_eps_meters: float
    cluster_min_samples: int

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        dotenv_path: str | Path | None = ".env",
    ) -> "Settings":
        """Load .env defaults, overridden by process or explicitly supplied values."""
        file_values = (
            dotenv_values(dotenv_path=dotenv_path, encoding="utf-8")
            if dotenv_path is not None
            else {}
        )
        values = {key: value for key, value in file_values.items() if value is not None}
        values.update(os.environ if environ is None else environ)

        return cls(
            llm_base_url=_optional_string(values, "LLM_BASE_URL"),
            llm_api_key=_optional_string(values, "LLM_API_KEY"),
            llm_text_model=_optional_string(values, "LLM_TEXT_MODEL"),
            llm_vision_model=_optional_string(values, "LLM_VISION_MODEL"),
            geofence_meters=_positive_float(values, "GEOFENCE_METERS"),
            cluster_eps_meters=_positive_float(values, "CLUSTER_EPS_METERS"),
            cluster_min_samples=_positive_int(values, "CLUSTER_MIN_SAMPLES"),
        )


def get_settings(
    environ: Mapping[str, str] | None = None,
    dotenv_path: str | Path | None = ".env",
) -> Settings:
    """Retrieve runtime settings from environment or default .env."""
    return Settings.from_env(environ=environ, dotenv_path=dotenv_path)

