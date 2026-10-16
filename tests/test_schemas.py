import json
from pathlib import Path

import pytest
from core.omnigrid.schemas import (
    Check,
    Claim,
    Closure,
    Complaint,
    EvidenceMeta,
    Hotspot,
    Media,
    Report,
    Verdict,
)
from pydantic import ValidationError

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples"


def load_sample(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def test_all_shared_models_import_and_construct() -> None:
    media = Media(path="photo.jpg", type="image")
    closure = Closure(
        closed_at="2026-10-15T11:45:00Z",
        media=[media],
        closed_by=None,
    )
    complaint = Complaint(
        id="complaint-test",
        category="other",
        text="Synthetic test complaint",
        lat=12.0,
        lon=77.0,
        created_at="2026-10-14T08:30:00Z",
        status="closed",
        closure=closure,
    )
    evidence = EvidenceMeta(
        complaint_id=complaint.id,
        path="photo.jpg",
        gps=None,
        taken_at=None,
        phash="0123456789abcdef",
        distance_m=None,
    )
    hotspot = Hotspot(
        id="hotspot-test",
        centroid=(12.0, 77.0),
        radius_m=20.0,
        complaint_ids=[complaint.id],
        category_mix={"other": 1},
        severity=0.0,
    )
    check = Check(name="gps_distance", passed=True, detail="Within range.")
    verdict = Verdict(
        complaint_id=complaint.id,
        verdict="verified",
        checks=[check],
        confidence=1.0,
    )
    claim = Claim(text="One complaint was reported.", cites=[complaint.id])
    report = Report(
        hotspots=[hotspot],
        verdicts=[verdict],
        claims=[claim],
        stats={"complaint_count": 1},
    )

    assert complaint.media == []
    assert complaint.closure == closure
    assert report.model_dump_json()
    assert Report.model_validate_json(report.model_dump_json()) == report
    assert evidence.gps is None


@pytest.mark.parametrize(
    ("model", "field", "value"),
    [
        (Complaint, "category", "roadwork"),
        (Media, "type", "audio"),
        (Complaint, "status", "pending"),
        (Verdict, "verdict", "fraud"),
    ],
)
def test_invalid_literal_values_are_rejected(
    model: type, field: str, value: str
) -> None:
    samples = {
        Complaint: load_sample("complaint-open.json"),
        Media: {"path": "photo.jpg", "type": "image"},
        Verdict: load_sample("verdict.json"),
    }
    data = samples[model].copy()
    data[field] = value

    with pytest.raises(ValidationError):
        model.model_validate(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("lat", -90.0),
        ("lat", 90.0),
        ("lon", -180.0),
        ("lon", 180.0),
    ],
)
def test_geographic_coordinate_boundaries_are_valid(field: str, value: float) -> None:
    complaint = load_sample("complaint-open.json")
    complaint[field] = value

    assert Complaint.model_validate(complaint)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("lat", -90.1),
        ("lat", 90.1),
        ("lon", -180.1),
        ("lon", 180.1),
    ],
)
def test_invalid_complaint_coordinates_are_rejected(field: str, value: float) -> None:
    complaint = load_sample("complaint-open.json")
    complaint[field] = value

    with pytest.raises(ValidationError):
        Complaint.model_validate(complaint)


def test_invalid_gps_and_hotspot_centroid_coordinates_are_rejected() -> None:
    evidence = load_sample("evidence-with-gps.json")
    evidence["gps"] = [91.0, 77.0]
    with pytest.raises(ValidationError):
        EvidenceMeta.model_validate(evidence)

    hotspot = load_sample("hotspot.json")
    hotspot["centroid"] = [12.0, 181.0]
    with pytest.raises(ValidationError):
        Hotspot.model_validate(hotspot)


@pytest.mark.parametrize("severity", [0.0, 10.0])
def test_severity_boundaries_are_valid(severity: float) -> None:
    hotspot = load_sample("hotspot.json")
    hotspot["severity"] = severity

    assert Hotspot.model_validate(hotspot).severity == severity


@pytest.mark.parametrize("severity", [-0.1, 10.1])
def test_severity_outside_boundaries_is_rejected(severity: float) -> None:
    hotspot = load_sample("hotspot.json")
    hotspot["severity"] = severity

    with pytest.raises(ValidationError):
        Hotspot.model_validate(hotspot)


@pytest.mark.parametrize("confidence", [0.0, 1.0])
def test_confidence_boundaries_are_valid(confidence: float) -> None:
    verdict = load_sample("verdict.json")
    verdict["confidence"] = confidence

    assert Verdict.model_validate(verdict).confidence == confidence


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_confidence_outside_boundaries_is_rejected(confidence: float) -> None:
    verdict = load_sample("verdict.json")
    verdict["confidence"] = confidence

    with pytest.raises(ValidationError):
        Verdict.model_validate(verdict)


def test_optional_fields_have_contract_defaults() -> None:
    complaint_data = load_sample("complaint-open.json")
    complaint_data.pop("media")
    complaint = Complaint.model_validate(complaint_data)
    another_complaint = Complaint.model_validate(complaint_data)
    check = Check(name="gps_distance", passed=True, detail="Within range.")

    assert complaint.media == []
    assert complaint.closure is None
    assert check.value is None
    complaint.media.append(Media(path="extra.jpg", type="image"))
    assert another_complaint.media == []


def test_nullable_fields_without_defaults_remain_required() -> None:
    evidence = load_sample("evidence-with-gps.json")
    evidence.pop("gps")

    with pytest.raises(ValidationError):
        EvidenceMeta.model_validate(evidence)


@pytest.mark.parametrize(
    ("filename", "model"),
    [
        ("complaint-open.json", Complaint),
        ("complaint-closed.json", Complaint),
        ("evidence-with-gps.json", EvidenceMeta),
        ("evidence-without-gps.json", EvidenceMeta),
        ("hotspot.json", Hotspot),
        ("verdict.json", Verdict),
        ("report.json", Report),
    ],
)
def test_sample_json_round_trips(filename: str, model: type) -> None:
    source = load_sample(filename)

    parsed = model.model_validate(source)

    assert model.model_validate_json(parsed.model_dump_json()) == parsed


def test_report_claim_citations_reference_report_records() -> None:
    report = Report.model_validate(load_sample("report.json"))
    known_ids = {hotspot.id for hotspot in report.hotspots} | {
        hotspot_id
        for hotspot in report.hotspots
        for hotspot_id in hotspot.complaint_ids
    }
    known_ids |= {verdict.complaint_id for verdict in report.verdicts}

    assert all(
        citation in known_ids for claim in report.claims for citation in claim.cites
    )
