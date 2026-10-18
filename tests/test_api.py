"""Offline tests for the optional FastAPI service."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from api import main as api_main
from fastapi.testclient import TestClient

from omnigrid import pipeline as pipeline_module
from omnigrid.cli import _execute
from omnigrid.llm import LLMConnectionError
from omnigrid.schemas import Report

GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-122.4, 37.7]},
            "properties": {
                "id": "api-fixture-1",
                "category": "pothole",
                "text": "Synthetic test complaint",
                "created_at": "2026-10-17T12:00:00Z",
                "status": "open",
            },
        }
    ],
}


def _upload(
    data: object = GEOJSON,
    *,
    filename: str = "complaints.geojson",
    content_type: str = "application/geo+json",
) -> dict[str, tuple[str, bytes, str]]:
    content = data if isinstance(data, bytes) else json.dumps(data).encode("utf-8")
    return {"file": (filename, content, content_type)}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("LLM_MOCK", "true")
    api_main._rate_limit_buckets.clear()
    return TestClient(api_main.app)


def test_health_reports_only_api_process_health(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "omnigrid-api"}
    assert "provider" not in response.json()
    assert "api_key" not in response.json()


def test_dashboard_is_served_from_the_api_origin(client: TestClient) -> None:
    response = client.get("/")
    script_response = client.get("/js/app.js")

    assert response.status_code == 200
    assert 'id="btn-run-demo"' in response.text
    assert "Run Synthetic Demo" in response.text
    assert script_response.status_code == 200
    assert 'requestReport("/demo", null)' in script_response.text


def test_demo_endpoint_runs_checked_in_fixture_through_canonical_pipeline(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_MOCK", "false")
    response = client.post("/demo")

    assert response.status_code == 200
    report = Report.model_validate(response.json())
    assert len(report.complaints) == 500
    assert len(report.hotspots) == 5
    assert report.stats["model_mode"] == "mock"
    assert report.stats["model_inference_used"] is False
    assert report.stats["api_demo_media"] is True


def test_valid_upload_returns_schema_valid_report(client: TestClient) -> None:
    response = client.post("/triage", files=_upload())

    assert response.status_code == 200
    report = Report.model_validate(response.json())
    assert report.complaints[0].id == "api-fixture-1"
    assert report.stats["model_mode"] == "mock"
    assert report.stats["summary_fallback"] is True


def test_api_report_matches_cli_output_for_same_fixture(
    client: TestClient,
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "fixture.geojson"
    input_path.write_text(json.dumps(GEOJSON), encoding="utf-8")
    cli_report = _execute(
        SimpleNamespace(
            command="triage",
            input=input_path,
            eps_meters=None,
            min_samples=None,
            reference_time=None,
            media_root=None,
            no_vision=False,
        )
    )

    response = client.post("/triage", files=_upload())

    assert response.status_code == 200
    assert response.json() == cli_report.model_dump(mode="json")


def test_missing_upload_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/triage",
        content=b"--empty-boundary--\r\n",
        headers={"content-type": "multipart/form-data; boundary=empty-boundary"},
    )

    assert response.status_code == 422
    assert "file" in response.json()["detail"]


def test_empty_upload_is_rejected(client: TestClient) -> None:
    response = client.post("/triage", files=_upload(b""))

    assert response.status_code == 400
    assert response.json()["detail"] == "The uploaded file is empty."


def test_utf8_bom_geojson_is_accepted(client: TestClient) -> None:
    content = b"\xef\xbb\xbf" + json.dumps(GEOJSON).encode("utf-8")

    response = client.post("/triage", files=_upload(content))

    assert response.status_code == 200
    assert Report.model_validate(response.json()).complaints[0].id == "api-fixture-1"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"{not json", "valid UTF-8 GeoJSON"),
        ({"type": "NotGeoJSON"}, "GeoJSON Feature"),
        (
            {
                **GEOJSON,
                "features": [
                    {
                        **GEOJSON["features"][0],
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[0, 0], [1, 1], [0, 0]]],
                        },
                    }
                ],
            },
            "must be a Point",
        ),
        (
            {
                **GEOJSON,
                "features": [
                    {
                        **GEOJSON["features"][0],
                        "properties": {
                            **GEOJSON["features"][0]["properties"],
                            "category": "unsupported",
                        },
                    }
                ],
            },
            "category",
        ),
        (
            {
                **GEOJSON,
                "features": [
                    {
                        **GEOJSON["features"][0],
                        "geometry": {
                            "type": "Point",
                            "coordinates": [-122.4, 95],
                        },
                    }
                ],
            },
            "lat",
        ),
    ],
)
def test_malformed_or_invalid_geojson_is_rejected(
    client: TestClient,
    data: object,
    message: str,
) -> None:
    response = client.post("/triage", files=_upload(data))

    assert response.status_code == 422
    assert message.lower() in response.json()["detail"].lower()


def test_empty_feature_collection_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/triage",
        files=_upload({"type": "FeatureCollection", "features": []}),
    )

    assert response.status_code == 422
    assert "at least one" in response.json()["detail"]


def test_feature_collection_limit_is_enforced(client: TestClient) -> None:
    data = {
        "type": "FeatureCollection",
        "features": [GEOJSON["features"][0]] * (api_main.MAX_FEATURES + 1),
    }

    response = client.post("/triage", files=_upload(data))

    assert response.status_code == 413
    assert "cannot contain more than" in response.json()["detail"]


def test_pipeline_concurrency_saturation_returns_service_unavailable(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api_main, "_pipeline_semaphore", asyncio.Semaphore(0))

    response = client.post("/triage", files=_upload())

    assert response.status_code == 503


def test_rate_limit_returns_429(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = Report(
        hotspots=[],
        verdicts=[],
        claims=[],
        stats={"model_mode": "mock"},
        complaints=[],
    )
    monkeypatch.setattr(api_main, "_run_pipeline", lambda *_, **__: report)
    monkeypatch.setattr(api_main, "RATE_LIMIT_REQUESTS", 1)

    assert client.post("/demo").status_code == 200
    response = client.post("/demo")

    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"

def test_unsupported_media_type_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/triage",
        files=_upload(content_type="image/png"),
    )

    assert response.status_code == 415


def test_file_and_request_size_limits_return_413(client: TestClient) -> None:
    oversized_file = b" " * (api_main.MAX_UPLOAD_BYTES + 1)
    file_response = client.post("/triage", files=_upload(oversized_file))
    request_response = client.post(
        "/triage",
        content=b"x" * (api_main.MAX_REQUEST_BYTES + 1),
        headers={"content-type": "multipart/form-data; boundary=too-large"},
    )

    assert file_response.status_code == 413
    assert request_response.status_code == 413


@pytest.mark.parametrize(
    "filename",
    ["../outside.geojson", r"..\..\outside.geojson"],
)
def test_suspicious_filename_components_are_rejected(
    client: TestClient,
    filename: str,
) -> None:
    response = client.post("/triage", files=_upload(filename=filename))

    assert response.status_code == 400
    assert "path components" in response.json()["detail"]


def test_windows_drive_filename_is_never_used_as_a_server_path(
    client: TestClient,
) -> None:
    response = client.post(
        "/triage",
        files=_upload(filename=r"C:\outside.geojson"),
    )

    assert response.status_code == 200
    assert Report.model_validate(response.json()).complaints[0].id == "api-fixture-1"


def test_media_path_traversal_is_rejected(client: TestClient) -> None:
    data = json.loads(json.dumps(GEOJSON))
    data["features"][0]["properties"]["media"] = [
        {"path": "../../private.txt", "type": "image"}
    ]

    response = client.post("/triage", files=_upload(data))

    assert response.status_code == 422
    assert "Media paths must be relative" in response.json()["detail"]


def test_pipeline_provider_failure_uses_canonical_fallback(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnavailableClient:
        config = SimpleNamespace(mock=False)

        def generate_structured(self, **_: Any) -> Any:
            raise LLMConnectionError("provider is unavailable")

    def run_with_unavailable_provider(
        source: object,
        **kwargs: Any,
    ) -> Report:
        kwargs["llm_client"] = UnavailableClient()
        return pipeline_module.run_pipeline(source, **kwargs)

    monkeypatch.setattr(api_main, "run_pipeline", run_with_unavailable_provider)

    response = client.post("/triage", files=_upload())

    assert response.status_code == 200
    report = Report.model_validate(response.json())
    assert report.stats["model_mode"] == "live"
    assert report.stats["summary_fallback"] is True
    assert report.stats["stage_status"]["summary"] == "template_fallback"


def test_unexpected_pipeline_error_is_safe_and_cleans_media_directory(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    seen_root: list[Path] = []
    private_message = "PRIVATE complaint text and /server/private/path"

    def fail_pipeline(
        source: object,
        *,
        media_root: str,
        mock: bool,
    ) -> Report:
        assert source == GEOJSON
        assert mock is True
        seen_root.append(Path(media_root))
        raise RuntimeError(private_message)

    monkeypatch.setattr(api_main, "run_pipeline", fail_pipeline)
    caplog.set_level(logging.ERROR, logger=api_main.__name__)

    response = client.post("/triage", files=_upload())

    assert response.status_code == 500
    assert response.json() == {
        "detail": "Triage failed unexpectedly. Try again later."
    }
    assert private_message not in response.text
    assert private_message not in caplog.text
    assert len(seen_root) == 1
    assert not seen_root[0].exists()


def test_concurrent_requests_use_distinct_temporary_directories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    barrier = threading.Barrier(2)
    roots: list[Path] = []
    lock = threading.Lock()
    report = Report(
        hotspots=[],
        verdicts=[],
        claims=[],
        stats={"model_mode": "mock"},
        complaints=[],
    )

    def fake_pipeline(
        source: object,
        *,
        media_root: str,
        mock: bool,
    ) -> Report:
        assert source == GEOJSON
        assert mock is True
        path = Path(media_root)
        assert path.is_dir()
        with lock:
            roots.append(path)
        barrier.wait(timeout=5)
        return report

    monkeypatch.setenv("LLM_MOCK", "true")
    monkeypatch.setattr(api_main, "run_pipeline", fake_pipeline)

    def post_upload() -> int:
        with TestClient(api_main.app) as client:
            response = client.post("/triage", files=_upload())
        return response.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(lambda _: post_upload(), range(2)))

    assert statuses == [200, 200]
    assert len(roots) == 2
    assert roots[0] != roots[1]
    assert all(not root.exists() for root in roots)
