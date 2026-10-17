"""Offline CLI integration tests; these are not a live-agent smoke test."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from omnigrid.schemas import (
    Complaint,
    EvidenceMeta,
    Hotspot,
    Report,
    Verdict,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SAMPLES = REPOSITORY_ROOT / "data" / "samples"
SYNTHETIC = REPOSITORY_ROOT / "data" / "synthetic" / "complaints.geojson"
COMMANDS = ("ingest", "evidence", "cluster", "verify", "summarize", "triage")


def run_cli(
    *args: str,
    cwd: Path = REPOSITORY_ROOT,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["LLM_MOCK"] = "true"
    environment["PYTHONPATH"] = str(REPOSITORY_ROOT / "core") + os.pathsep + (
        environment.get("PYTHONPATH", "")
    )
    return subprocess.run(
        [sys.executable, "-m", "omnigrid.cli", *args],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("command", COMMANDS)
def test_commands_have_help(command: str) -> None:
    result = run_cli(command, "--help")

    assert result.returncode == 0
    assert "usage:" in result.stdout.lower()
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("command", "input_path", "schema"),
    [
        ("ingest", str(SAMPLES / "complaint-open.json"), Complaint),
        ("evidence", str(SAMPLES / "evidence-with-gps.json"), EvidenceMeta),
        ("verify", str(SAMPLES / "verdict.json"), Verdict),
        ("summarize", str(SAMPLES / "report.json"), Report),
    ],
)
def test_schema_validation_commands_emit_valid_json(
    command: str,
    input_path: str,
    schema: type,
) -> None:
    result = run_cli(command, input_path)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    output = json.loads(result.stdout)
    if isinstance(output, list):
        assert output
        for record in output:
            schema.model_validate(record)
    else:
        schema.model_validate(output)


def test_ingest_converts_synthetic_geojson_to_complaints() -> None:
    result = run_cli("ingest", str(SYNTHETIC))

    assert result.returncode == 0, result.stderr
    complaints = json.loads(result.stdout)
    assert len(complaints) == 500
    assert all(Complaint.model_validate(item) for item in complaints)


def test_cluster_returns_shared_hotspot_records() -> None:
    result = run_cli(
        "cluster",
        str(SYNTHETIC),
        "--eps-meters",
        "150",
        "--min-samples",
        "3",
        "--reference-time",
        "2026-10-16T00:00:00Z",
    )

    assert result.returncode == 0, result.stderr
    hotspots = json.loads(result.stdout)
    assert hotspots
    assert all(Hotspot.model_validate(item) for item in hotspots)


def test_triage_returns_complete_shared_report() -> None:
    result = run_cli("triage", str(SYNTHETIC))

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    report = Report.model_validate_json(result.stdout)
    assert len(report.complaints) == 500
    assert len(report.hotspots) == 5
    assert len(report.verdicts) == 100
    assert report.claims
    assert report.stats["stages_completed"] == [
        "ingest",
        "evidence",
        "cluster",
        "verification",
        "summarize",
        "report_validation",
    ]
    assert report.stats["stages_skipped"] == ["damage_scoring", "vision"]
    assert report.stats["model_mode"] == "mock"
    assert report.stats["model_inference_used"] is False


def test_triage_works_from_another_working_directory(tmp_path: Path) -> None:
    result = run_cli("triage", str(SYNTHETIC), cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    report = Report.model_validate_json(result.stdout)
    assert len(report.complaints) == 500
    assert len(report.hotspots) == 5
    assert report.stats["stage_status"]["report"] == "validated"


@pytest.mark.parametrize(
    ("input_path", "message"),
    [
        ("missing-input.json", "input file not found"),
    ],
)
def test_missing_input_fails_without_success_output(
    input_path: str,
    message: str,
) -> None:
    result = run_cli("ingest", input_path)

    assert result.returncode == 2
    assert result.stdout == ""
    assert message in result.stderr
    assert "Traceback" not in result.stderr


def test_malformed_json_fails_without_success_output(tmp_path: Path) -> None:
    malformed = tmp_path / "broken.json"
    malformed.write_text('{"features": ]', encoding="utf-8")

    result = run_cli("ingest", str(malformed))

    assert result.returncode == 2
    assert result.stdout == ""
    assert "invalid JSON" in result.stderr
    assert "Traceback" not in result.stderr


def test_schema_error_is_reported_without_echoing_complaint_text(
    tmp_path: Path,
) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text(
        json.dumps({"id": "x", "category": "unknown", "text": "private"}),
        encoding="utf-8",
    )

    result = run_cli("ingest", str(invalid))

    assert result.returncode == 2
    assert result.stdout == ""
    assert "does not match Complaint" in result.stderr
    assert "private" not in result.stderr
    assert "Traceback" not in result.stderr


def test_evidence_command_rejects_missing_metadata_fields(tmp_path: Path) -> None:
    media_metadata = tmp_path / "evidence.json"
    media_metadata.write_text("{}", encoding="utf-8")

    result = run_cli("evidence", str(media_metadata))

    assert result.returncode == 2
    assert result.stdout == ""
    assert "does not match EvidenceMeta" in result.stderr
