from pathlib import Path

import pytest

from omnigrid.config import Settings


def test_default_settings() -> None:
    settings = Settings.from_env(environ={}, dotenv_path=None)

    assert settings.llm_base_url == "http://localhost:11434/v1"
    assert settings.llm_api_key == "ollama"
    assert settings.llm_text_model == "qwen2.5:7b-instruct"
    assert settings.llm_vision_model == "qwen2.5vl:7b"
    assert settings.geofence_meters == 50.0
    assert settings.cluster_eps_meters == 150.0
    assert settings.cluster_min_samples == 3


def test_environment_values_override_defaults() -> None:
    settings = Settings.from_env(
        environ={
            "LLM_BASE_URL": "http://localhost:1234/v1",
            "LLM_API_KEY": "local-placeholder",
            "LLM_TEXT_MODEL": "text-test",
            "LLM_VISION_MODEL": "vision-test",
            "GEOFENCE_METERS": "25.5",
            "CLUSTER_EPS_METERS": "80",
            "CLUSTER_MIN_SAMPLES": "5",
        },
        dotenv_path=None,
    )

    assert settings.llm_base_url == "http://localhost:1234/v1"
    assert settings.llm_api_key == "local-placeholder"
    assert settings.llm_text_model == "text-test"
    assert settings.llm_vision_model == "vision-test"
    assert settings.geofence_meters == 25.5
    assert settings.cluster_eps_meters == 80.0
    assert settings.cluster_min_samples == 5


def test_process_environment_overrides_dotenv_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("GEOFENCE_METERS=99\nCLUSTER_MIN_SAMPLES=6\n", encoding="utf-8")

    for name in (
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "LLM_TEXT_MODEL",
        "LLM_VISION_MODEL",
        "GEOFENCE_METERS",
        "CLUSTER_EPS_METERS",
        "CLUSTER_MIN_SAMPLES",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GEOFENCE_METERS", "12.5")

    settings = Settings.from_env(dotenv_path=env_file)

    assert settings.geofence_meters == 12.5
    assert settings.cluster_min_samples == 6


def test_numeric_settings_use_numeric_types() -> None:
    settings = Settings.from_env(environ={}, dotenv_path=None)

    assert isinstance(settings.geofence_meters, float)
    assert isinstance(settings.cluster_eps_meters, float)
    assert isinstance(settings.cluster_min_samples, int)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("GEOFENCE_METERS", "not-a-number"),
        ("GEOFENCE_METERS", "0"),
        ("GEOFENCE_METERS", "-1"),
        ("GEOFENCE_METERS", "nan"),
        ("CLUSTER_EPS_METERS", "inf"),
        ("CLUSTER_EPS_METERS", "-5"),
        ("CLUSTER_MIN_SAMPLES", "1.5"),
        ("CLUSTER_MIN_SAMPLES", "0"),
        ("CLUSTER_MIN_SAMPLES", "-2"),
    ],
)
def test_invalid_numeric_settings_raise_clear_errors(name: str, value: str) -> None:
    with pytest.raises(ValueError, match=name) as error:
        Settings.from_env(environ={name: value}, dotenv_path=None)

    assert name in str(error.value)


def test_configuration_errors_never_expose_secret_values() -> None:
    secret = "not-a-real-secret-value"

    with pytest.raises(ValueError) as error:
        Settings.from_env(
            environ={
                "LLM_API_KEY": secret,
                "CLUSTER_EPS_METERS": "invalid-number",
            },
            dotenv_path=None,
        )

    assert secret not in str(error.value)
    assert "invalid-number" not in str(error.value)


def test_blank_optional_model_settings_are_disabled() -> None:
    settings = Settings.from_env(
        environ={
            "LLM_BASE_URL": "",
            "LLM_API_KEY": " ",
            "LLM_TEXT_MODEL": "",
            "LLM_VISION_MODEL": " ",
        },
        dotenv_path=None,
    )

    assert settings.llm_base_url is None
    assert settings.llm_api_key is None
    assert settings.llm_text_model is None
    assert settings.llm_vision_model is None
