"""Comprehensive unit and integration tests for Phase 6.

Proof-of-Fix deterministic checks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from core.omnigrid.evidence import compute_phash
from core.omnigrid.schemas import Check, Verdict
from core.omnigrid.verify import (
    check_category_consistency,
    check_duplicate_evidence,
    check_gps_geofence,
    check_timestamp_sanity,
    verify_closed_complaints,
    verify_closure,
)
from scripts.generate_sample_media import generate_sample_media

REPO_ROOT = Path(__file__).resolve().parents[1]
MEDIA_DIR = REPO_ROOT / "data" / "synthetic" / "media"
COMPLAINTS_GEOJSON = REPO_ROOT / "data" / "synthetic" / "complaints.geojson"
GROUND_TRUTH_JSON = REPO_ROOT / "data" / "synthetic" / "ground_truth.json"


@pytest.fixture(scope="module", autouse=True)
def ensure_sample_media() -> None:
    """Ensure synthetic media files exist before running verification tests."""
    if not (MEDIA_DIR / "media_15.jpg").is_file():
        generate_sample_media(MEDIA_DIR)


@pytest.fixture(scope="module")
def dataset() -> list[dict[str, Any]]:
    """Load complaints from synthetic GeoJSON file."""
    assert COMPLAINTS_GEOJSON.is_file(), "complaints.geojson must exist"
    data = json.loads(COMPLAINTS_GEOJSON.read_text(encoding="utf-8"))
    return [f["properties"] for f in data["features"]]


@pytest.fixture(scope="module")
def complaints_by_id(dataset: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index complaints by their complaint_id."""
    return {c["id"]: c for c in dataset}


# -----------------------------------------------------------------------------
# 1. Planted Suspicious Closures Detection Tests
# -----------------------------------------------------------------------------


def test_detect_planted_mismatched_gps_complaint_003(
    complaints_by_id: dict[str, dict[str, Any]],
    dataset: list[dict[str, Any]],
) -> None:
    """Verify complaint-003 fails GPS geofence check (~23km in Yelahanka)."""
    complaint = complaints_by_id["complaint-003"]
    verdict = verify_closure(complaint, all_complaints=dataset, media_dir=MEDIA_DIR)

    assert verdict.complaint_id == "complaint-003"
    assert verdict.verdict == "suspect"

    gps_check = next((c for c in verdict.checks if c.name == "gps_geofence"), None)
    assert gps_check is not None
    assert gps_check.passed is False
    assert gps_check.value is not None
    assert gps_check.value > 5000.0  # ~23,000 meters


def test_detect_planted_mismatched_gps_complaint_017(
    complaints_by_id: dict[str, dict[str, Any]],
    dataset: list[dict[str, Any]],
) -> None:
    """Verify complaint-017 fails GPS geofence check (~21km in Anekal)."""
    complaint = complaints_by_id["complaint-017"]
    verdict = verify_closure(complaint, all_complaints=dataset, media_dir=MEDIA_DIR)

    assert verdict.complaint_id == "complaint-017"
    assert verdict.verdict == "suspect"

    gps_check = next((c for c in verdict.checks if c.name == "gps_geofence"), None)
    assert gps_check is not None
    assert gps_check.passed is False
    assert gps_check.value is not None
    assert gps_check.value > 5000.0  # ~21,000 meters


def test_detect_planted_duplicate_evidence_complaint_030(
    complaints_by_id: dict[str, dict[str, Any]],
    dataset: list[dict[str, Any]],
) -> None:
    """Verify complaint-030 fails duplicate evidence check."""
    complaint = complaints_by_id["complaint-030"]
    verdict = verify_closure(complaint, all_complaints=dataset, media_dir=MEDIA_DIR)

    assert verdict.complaint_id == "complaint-030"
    assert verdict.verdict == "suspect"

    dup_check = next(
        (c for c in verdict.checks if c.name == "duplicate_evidence"),
        None,
    )
    assert dup_check is not None
    assert dup_check.passed is False
    assert dup_check.value == 0.0  # Exact match (Hamming distance 0)
    assert "complaint-045" in dup_check.detail


def test_detect_planted_duplicate_evidence_complaint_045(
    complaints_by_id: dict[str, dict[str, Any]],
    dataset: list[dict[str, Any]],
) -> None:
    """Verify complaint-045 fails duplicate evidence check."""
    complaint = complaints_by_id["complaint-045"]
    verdict = verify_closure(complaint, all_complaints=dataset, media_dir=MEDIA_DIR)

    assert verdict.complaint_id == "complaint-045"
    assert verdict.verdict == "suspect"

    dup_check = next(
        (c for c in verdict.checks if c.name == "duplicate_evidence"),
        None,
    )
    assert dup_check is not None
    assert dup_check.passed is False
    assert dup_check.value == 0.0  # Exact match (Hamming distance 0)
    assert "complaint-030" in dup_check.detail


def test_detect_planted_inconsistent_defect_complaint_018(
    complaints_by_id: dict[str, dict[str, Any]],
    dataset: list[dict[str, Any]],
) -> None:
    """Verify complaint-018 fails category consistency check."""
    complaint = complaints_by_id["complaint-018"]
    verdict = verify_closure(complaint, all_complaints=dataset, media_dir=MEDIA_DIR)

    assert verdict.complaint_id == "complaint-018"
    assert verdict.verdict == "suspect"

    cat_check = next(
        (c for c in verdict.checks if c.name == "category_consistency"),
        None,
    )
    assert cat_check is not None
    assert cat_check.passed is False
    assert "streetlight" in cat_check.detail
    assert "pothole" in cat_check.detail


# -----------------------------------------------------------------------------
# 2. Legitimate Closures Verification Tests
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "complaint_id",
    [
        "complaint-001",
        "complaint-015",
        "complaint-028",
        "complaint-043",
        "complaint-055",
    ],
)
def test_legitimate_closures_are_verified(
    complaint_id: str,
    complaints_by_id: dict[str, dict[str, Any]],
    dataset: list[dict[str, Any]],
) -> None:
    """Verify that legitimate closed complaints pass all checks with 'verified'."""
    complaint = complaints_by_id[complaint_id]
    verdict = verify_closure(complaint, all_complaints=dataset, media_dir=MEDIA_DIR)

    assert verdict.complaint_id == complaint_id
    assert verdict.verdict == "verified"
    assert verdict.confidence > 0.8

    # All executed checks must have passed=True
    for c in verdict.checks:
        assert c.passed is True, f"Check {c.name} unexpectedly failed: {c.detail}"


# -----------------------------------------------------------------------------
# 3. Missing EXIF / Inconclusive Cases
# -----------------------------------------------------------------------------


def test_missing_closure_media_is_inconclusive() -> None:
    """Closed complaint without closure media yields 'inconclusive' not 'suspect'."""
    complaint = {
        "id": "complaint-no-media",
        "status": "closed",
        "category": "pothole",
        "latitude": 12.9172,
        "longitude": 77.6228,
        "created_at": "2026-10-01T10:00:00Z",
        "closure": {
            "closed_at": "2026-10-02T10:00:00Z",
            "media": [],
        },
    }
    verdict = verify_closure(complaint, media_dir=MEDIA_DIR)
    assert verdict.complaint_id == "complaint-no-media"
    assert verdict.verdict == "inconclusive"
    assert verdict.confidence <= 0.5


def test_missing_exif_gps_returns_inconclusive_check(tmp_path: Path) -> None:
    """An image with no GPS metadata returns passed=None for gps_geofence."""
    clean_path = tmp_path / "stripped.jpg"
    from PIL import Image

    img = Image.new("RGB", (32, 32), color="green")
    img.save(clean_path, format="JPEG")

    complaint = {"lat": 12.9172, "lon": 77.6228}
    check = check_gps_geofence(
        complaint=complaint,
        closure_media_path=clean_path,
        geofence_meters=50.0,
    )
    assert check.name == "gps_geofence"
    assert check.passed is None
    assert "missing" in check.detail.lower() or "not found" in check.detail.lower()


def test_missing_timestamp_returns_inconclusive_check(tmp_path: Path) -> None:
    """An image with no EXIF timestamp returns passed=None for timestamp_sanity."""
    clean_path = tmp_path / "stripped.jpg"
    from PIL import Image

    img = Image.new("RGB", (32, 32), color="green")
    img.save(clean_path, format="JPEG")

    complaint = {"created_at": "2026-10-01T10:00:00Z"}
    check = check_timestamp_sanity(
        complaint=complaint,
        closure_media_path=clean_path,
    )
    assert check.name == "timestamp_sanity"
    assert check.passed is None


def test_missing_exif_closure_verdict_is_inconclusive(tmp_path: Path) -> None:
    """A closure with missing EXIF returns verdict == 'inconclusive' not suspect."""
    clean_path = tmp_path / "clean_closure.jpg"
    from PIL import Image

    img = Image.new("RGB", (32, 32), color="purple")
    img.save(clean_path, format="JPEG")

    complaint = {
        "id": "complaint-stripped-exif",
        "status": "closed",
        "category": "pothole",
        "latitude": 12.9172,
        "longitude": 77.6228,
        "created_at": "2026-10-01T10:00:00Z",
        "closure": {
            "closed_at": "2026-10-02T10:00:00Z",
            "media": [{"path": str(clean_path)}],
        },
    }

    verdict = verify_closure(complaint, media_dir=tmp_path)
    assert verdict.complaint_id == "complaint-stripped-exif"
    assert verdict.verdict == "inconclusive"


def test_closure_media_outside_media_dir_is_rejected(tmp_path: Path) -> None:
    from PIL import Image

    media_dir = tmp_path / "media"
    media_dir.mkdir()
    outside_image = tmp_path / "outside.jpg"
    Image.new("RGB", (24, 24), color="blue").save(outside_image, format="JPEG")
    complaint = {
        "id": "complaint-outside-media",
        "status": "closed",
        "closure": {
            "closed_at": "2026-10-02T10:00:00Z",
            "media": [{"path": str(outside_image), "type": "image"}],
        },
    }

    verdict = verify_closure(complaint, media_dir=media_dir)

    assert verdict.verdict == "inconclusive"
    assert verdict.checks[0].name == "evidence_path"
    assert verdict.checks[0].passed is None
    assert "outside the configured media directory" in verdict.checks[0].detail


# -----------------------------------------------------------------------------
# 4. Strict Schema Compliance Tests
# -----------------------------------------------------------------------------


def test_schema_compliance_on_check_and_verdict(
    complaints_by_id: dict[str, dict[str, Any]],
    dataset: list[dict[str, Any]],
) -> None:
    """Ensure Check and Verdict models strictly adhere to core.omnigrid.schemas."""
    complaint = complaints_by_id["complaint-001"]
    verdict = verify_closure(complaint, all_complaints=dataset, media_dir=MEDIA_DIR)

    assert isinstance(verdict, Verdict)
    assert isinstance(verdict.complaint_id, str)
    assert verdict.verdict in {"verified", "suspect", "inconclusive"}
    assert isinstance(verdict.confidence, float)
    assert 0.0 <= verdict.confidence <= 1.0
    assert len(verdict.checks) > 0

    for chk in verdict.checks:
        assert isinstance(chk, Check)
        assert isinstance(chk.name, str)
        assert chk.passed in {True, False, None}
        assert isinstance(chk.detail, str)
        assert chk.value is None or isinstance(chk.value, (int, float))

    # Pydantic serialization roundtrip
    dumped = verdict.model_dump()
    restored = Verdict.model_validate(dumped)
    assert restored == verdict


# -----------------------------------------------------------------------------
# 5. Determinism & Consistency Tests
# -----------------------------------------------------------------------------


def test_determinism_identical_runs_produce_identical_verdicts(
    complaints_by_id: dict[str, dict[str, Any]],
    dataset: list[dict[str, Any]],
) -> None:
    """Running verify_closure multiple times produces identical checks and verdicts."""
    complaint = complaints_by_id["complaint-003"]

    v1 = verify_closure(complaint, all_complaints=dataset, media_dir=MEDIA_DIR)
    v2 = verify_closure(complaint, all_complaints=dataset, media_dir=MEDIA_DIR)

    assert v1.verdict == v2.verdict
    assert v1.confidence == v2.confidence
    assert len(v1.checks) == len(v2.checks)
    for c1, c2 in zip(v1.checks, v2.checks, strict=True):
        assert c1.name == c2.name
        assert c1.passed == c2.passed
        assert c1.detail == c2.detail
        assert c1.value == c2.value


# -----------------------------------------------------------------------------
# 6. Duplicate Evidence Recycled Photo Tests
# -----------------------------------------------------------------------------


def test_recycled_initial_photo_fails_duplicate_evidence() -> None:
    """Detects when complaint initial photo is recycled as closure proof."""
    p1 = MEDIA_DIR / "media_01.jpg"
    h1 = compute_phash(p1)
    assert h1 is not None

    complaint = {
        "id": "complaint-recycle-test",
        "status": "closed",
        "category": "pothole",
        "latitude": 12.9172,
        "longitude": 77.6228,
        "media": [{"path": str(p1)}],
        "closure": {
            "media": [{"path": str(p1)}],
            "closed_at": "2026-10-02T10:00:00Z",
        },
    }

    check = check_duplicate_evidence(
        complaint=complaint,
        closure_media_path=p1,
        all_complaints=[],
        base_dir=MEDIA_DIR,
    )

    assert check.passed is False
    assert check.value == 0.0
    assert "recycled" in check.detail.lower()


# -----------------------------------------------------------------------------
# 7. Timestamp Anomaly Tests
# -----------------------------------------------------------------------------


def test_closure_timestamp_prior_to_complaint_fails() -> None:
    """Fails if closure photo was captured prior to complaint creation."""
    p1 = MEDIA_DIR / "media_01.jpg"

    complaint = {"created_at": "2026-10-20T00:00:00Z"}  # Created in future
    check = check_timestamp_sanity(
        complaint=complaint,
        closure_media_path=p1,
    )

    assert check.passed is False
    assert "precedes complaint creation" in check.detail


# -----------------------------------------------------------------------------
# 8. Category Consistency Unit Tests
# -----------------------------------------------------------------------------


def test_category_consistency_unit() -> None:
    """Unit tests for check_category_consistency with defects."""
    # Match
    chk_match = check_category_consistency(
        complaint={"category": "pothole"},
        closure_media_path="media_01.jpg",
    )
    assert chk_match.passed is True

    # Mismatch
    chk_mismatch = check_category_consistency(
        complaint={"category": "streetlight"},
        closure_media_path="media_13.jpg",  # Known pothole patch
        defect_type="pothole",
    )
    assert chk_mismatch.passed is False
    assert "streetlight" in chk_mismatch.detail
    assert "pothole" in chk_mismatch.detail

    # Non-conflicting / no metadata
    chk_no_meta = check_category_consistency(
        complaint={"category": "pothole"},
        closure_media_path="unknown_media.jpg",
    )
    assert chk_no_meta.passed is True


# -----------------------------------------------------------------------------
# 9. Batch Verification Interface Tests
# -----------------------------------------------------------------------------


def test_verify_closed_complaints_batch(dataset: list[dict[str, Any]]) -> None:
    """Batch verify all closed complaints in the synthetic dataset."""
    verdicts = verify_closed_complaints(COMPLAINTS_GEOJSON, media_dir=MEDIA_DIR)

    # In our 500-complaint dataset, exactly 100 are closed
    assert len(verdicts) == 100

    verdict_counts = {
        "verified": sum(1 for v in verdicts if v.verdict == "verified"),
        "suspect": sum(1 for v in verdicts if v.verdict == "suspect"),
        "inconclusive": sum(1 for v in verdicts if v.verdict == "inconclusive"),
    }

    assert verdict_counts["suspect"] == 5
    assert verdict_counts["verified"] == 5
    assert verdict_counts["inconclusive"] == 90

    # The 5 suspect complaints must match ground_truth.json
    ground_truth = json.loads(GROUND_TRUTH_JSON.read_text(encoding="utf-8"))
    expected_suspect_ids = set(ground_truth["suspicious_closure_ids"])

    actual_suspect_ids = {v.complaint_id for v in verdicts if v.verdict == "suspect"}
    assert actual_suspect_ids == expected_suspect_ids
