"""End-to-end tests for the canonical complaint triage pipeline."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from core.omnigrid.damage import DamageSeverityCache, DamageSeverityScore
from core.omnigrid.evidence import haversine_distance
from core.omnigrid.ingest import InputError
from core.omnigrid.llm import LLMResponseError, LLMTimeoutError
from core.omnigrid.pipeline import resolve_media_path, run_pipeline
from core.omnigrid.schemas import (
    Claim,
    Closure,
    Complaint,
    Media,
    Report,
)
from core.omnigrid.summarize import _SummaryResponse
from core.omnigrid.verify import ClosureVisionAssessment, VisionCache
from PIL import Image

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_INPUT = REPOSITORY_ROOT / "data" / "synthetic" / "complaints.geojson"
GROUND_TRUTH = REPOSITORY_ROOT / "data" / "synthetic" / "ground_truth.json"


@pytest.fixture(scope="module")
def synthetic_report() -> Report:
    return run_pipeline(
        SYNTHETIC_INPUT,
        media_root=REPOSITORY_ROOT,
        mock=True,
    )


def _complaint(
    complaint_id: str,
    *,
    status: str = "open",
    media: list[Media] | None = None,
    closure_media: list[Media] | None = None,
) -> Complaint:
    closure = (
        Closure(
            closed_at=datetime(2026, 10, 2, tzinfo=UTC),
            media=closure_media or [],
            closed_by="synthetic-test",
        )
        if status == "closed"
        else None
    )
    return Complaint(
        id=complaint_id,
        category="pothole",
        text="Synthetic test complaint",
        lat=12.9,
        lon=77.6,
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
        status=status,  # type: ignore[arg-type]
        media=media or [],
        closure=closure,
    )


def _image(path: Path, color: str = "blue") -> Path:
    Image.new("RGB", (64, 64), color=color).save(path, format="JPEG")
    return path


class _FakeLiveClient:
    config = SimpleNamespace(mock=False)
    vision_cache_identity = "test-live-provider/model"

    def __init__(
        self,
        *,
        claims: list[Claim] | None = None,
        fail_summary: bool = False,
        vision_error: Exception | None = None,
    ) -> None:
        self.claims = claims or []
        self.fail_summary = fail_summary
        self.vision_error = vision_error
        self.summary_attempts = 0
        self.vision_attempts = 0

    def generate_structured(
        self,
        *,
        response_model: type,
        **_: Any,
    ) -> Any:
        if response_model is DamageSeverityScore:
            return DamageSeverityScore(score=0.4, reasoning="Synthetic test score")
        if response_model is _SummaryResponse:
            self.summary_attempts += 1
            if self.fail_summary:
                raise LLMResponseError("synthetic invalid response")
            return response_model(claims=self.claims)
        raise AssertionError(f"Unexpected response schema: {response_model}")

    def compare_images_structured(self, **_: Any) -> ClosureVisionAssessment:
        self.vision_attempts += 1
        if self.vision_error is not None:
            raise self.vision_error
        return ClosureVisionAssessment(
            same_location_likely=True,
            defect_repaired=True,
            confidence=0.99,
            reasoning="Synthetic test assessment",
        )


def test_synthetic_pipeline_builds_complete_schema_valid_report(
    synthetic_report: Report,
) -> None:
    report = Report.model_validate(synthetic_report.model_dump(mode="python"))

    assert len(report.complaints) == 500
    assert len(report.hotspots) == 5
    assert len(report.verdicts) == 100
    assert report.claims
    assert report.stats["stage_status"]["report"] == "validated"
    assert report.stats["model_mode"] == "mock"
    assert report.stats["model_inference_used"] is False
    assert report.stats["summary_fallback"] is True
    assert report.stats["claims_kept"] == len(report.claims)
    assert report.stats["claims_dropped"] == 0
    complaint_ids = {complaint.id for complaint in report.complaints}
    assert all(
        complaint_id in complaint_ids
        for hotspot in report.hotspots
        for complaint_id in hotspot.complaint_ids
    )
    assert all(
        verdict.complaint_id in complaint_ids for verdict in report.verdicts
    )


def test_pipeline_recovers_five_planted_hotspots_within_50m(
    synthetic_report: Report,
) -> None:
    truth = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    actual_by_complaints = {
        frozenset(hotspot.complaint_ids): hotspot
        for hotspot in synthetic_report.hotspots
    }

    assert len(actual_by_complaints) == 5
    for expected in truth["hotspots"]:
        actual = actual_by_complaints[frozenset(expected["complaint_ids"])]
        distance = haversine_distance(
            actual.centroid[0],
            actual.centroid[1],
            expected["centroid"][0],
            expected["centroid"][1],
        )
        assert distance < 50
        assert len(actual.complaint_ids) == expected["complaint_count"]


def test_pipeline_flags_planted_fake_closures_deterministically(
    synthetic_report: Report,
) -> None:
    planted = {
        "complaint-003",
        "complaint-017",
        "complaint-030",
        "complaint-045",
        "complaint-018",
    }
    verdicts = {verdict.complaint_id: verdict for verdict in synthetic_report.verdicts}

    assert all(verdicts[complaint_id].verdict == "suspect" for complaint_id in planted)
    assert synthetic_report.stats["suspect_count"] == 5
    assert synthetic_report.stats["verified_count"] == 5
    assert synthetic_report.stats["inconclusive_count"] == 90


def test_media_failures_are_counted_and_do_not_hide_other_evidence(
    tmp_path: Path,
) -> None:
    root = tmp_path / "media-root"
    root.mkdir()
    valid_image = _image(root / "valid.jpg")
    malformed_image = root / "broken.jpg"
    malformed_image.write_bytes(b"not an image")
    (root / "directory.jpg").mkdir()
    outside_image = _image(tmp_path / "outside.jpg", color="red")
    complaint = _complaint(
        "media-cases",
        media=[
            Media(path="valid.jpg", type="image"),
            Media(path="missing.jpg", type="image"),
            Media(path="broken.jpg", type="image"),
            Media(path="directory.jpg", type="image"),
            Media(path=str(outside_image), type="image"),
        ],
    )

    report = run_pipeline([complaint], media_root=root, mock=True)

    assert report.stats["evidence_count"] == 1
    assert report.stats["media_references"] == 5
    assert report.stats["media_references_skipped"] == 4
    assert report.stats["media_skipped_missing"] == 2
    assert report.stats["media_skipped_invalid"] == 1
    assert report.stats["media_skipped_unsafe"] == 1
    assert report.stats["stage_status"]["evidence"] == "completed_with_skips"
    assert valid_image.is_file()
    assert report.stats["media_evidence_extracted"] == 1


def test_missing_exif_and_duplicate_evidence_are_inconclusive_or_suspect(
    tmp_path: Path,
) -> None:
    root = tmp_path / "media"
    root.mkdir()
    duplicated = _image(root / "initial.jpg")
    closure = root / "closure.jpg"
    closure.write_bytes(duplicated.read_bytes())
    complaint = _complaint(
        "duplicate-case",
        status="closed",
        media=[Media(path="initial.jpg", type="image")],
        closure_media=[Media(path="closure.jpg", type="image")],
    )

    report = run_pipeline(
        [complaint],
        media_root=root,
        mock=True,
    )

    assert report.stats["evidence_count"] == 2
    assert report.stats["evidence_with_gps"] == 0
    assert report.stats["evidence_with_timestamp"] == 0
    assert report.stats["vision_checks"] == 0
    verdict = report.verdicts[0]
    assert verdict.verdict == "suspect"
    duplicate_check = next(
        check for check in verdict.checks if check.name == "duplicate_evidence"
    )
    assert duplicate_check.passed is False
    assert any(check.passed is None for check in verdict.checks)


def test_unsafe_media_resolution_rejects_traversal(tmp_path: Path) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    outside = tmp_path / "outside.jpg"
    _image(outside)

    with pytest.raises(ValueError, match="outside"):
        resolve_media_path("../outside.jpg", root)
    with pytest.raises(ValueError, match="outside"):
        resolve_media_path(outside, root)


def test_missing_nested_media_does_not_fall_back_to_same_basename(
    tmp_path: Path,
) -> None:
    root = tmp_path / "media"
    root.mkdir()
    _image(root / "closure.jpg")
    complaint = _complaint(
        "ambiguous-media",
        status="closed",
        closure_media=[Media(path="nested/closure.jpg", type="image")],
    )

    report = run_pipeline([complaint], media_root=root, mock=True)

    assert report.stats["evidence_count"] == 0
    assert report.verdicts[0].verdict == "inconclusive"
    assert report.verdicts[0].checks[0].name == "evidence_path"


def test_unreadable_closure_cannot_be_flagged_from_filename_alone(
    tmp_path: Path,
) -> None:
    root = tmp_path / "media"
    root.mkdir()
    (root / "media_13.jpg").write_bytes(b"not an image")
    complaint = _complaint(
        "unreadable-closure",
        status="closed",
        closure_media=[Media(path="media_13.jpg", type="image")],
    )
    complaint.category = "streetlight"

    report = run_pipeline([complaint], media_root=root, mock=True)

    assert report.stats["media_skipped_invalid"] == 1
    assert report.verdicts[0].verdict == "inconclusive"
    category_check = next(
        check
        for check in report.verdicts[0].checks
        if check.name == "category_consistency"
    )
    assert category_check.passed is None


def test_deterministic_failure_remains_suspect_when_vision_passes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "media"
    root.mkdir()
    initial = _image(root / "initial.jpg")
    closure_path = root / "closure.jpg"
    closure_path.write_bytes(initial.read_bytes())
    complaint = _complaint(
        "precedence-case",
        status="closed",
        media=[Media(path="initial.jpg", type="image")],
        closure_media=[Media(path="closure.jpg", type="image")],
    )
    client = _FakeLiveClient(
        claims=[Claim(text="There is 1 complaint.", cites=[])]
    )

    report = run_pipeline(
        [complaint],
        media_root=root,
        llm_client=client,  # type: ignore[arg-type]
        damage_cache=DamageSeverityCache(tmp_path / "damage-cache.json"),
        vision_cache=VisionCache(tmp_path / "vision-cache.json"),
    )

    verdict = report.verdicts[0]
    assert verdict.verdict == "suspect"
    assert next(
        check for check in verdict.checks if check.name == "duplicate_evidence"
    ).passed is False
    assert next(
        check for check in verdict.checks if check.name == "vision_consistency"
    ).passed is True
    assert client.vision_attempts == 1


def test_timeout_and_invalid_claims_are_reported_with_truthful_fallback(
    tmp_path: Path,
) -> None:
    root = tmp_path / "media"
    root.mkdir()
    initial = _image(root / "initial.jpg")
    closure_path = _image(root / "closure.jpg", color="green")
    complaint = _complaint(
        "provider-failure",
        status="closed",
        media=[Media(path="initial.jpg", type="image")],
        closure_media=[Media(path="closure.jpg", type="image")],
    )
    client = _FakeLiveClient(
        fail_summary=True,
        vision_error=LLMTimeoutError("synthetic timeout"),
    )

    report = run_pipeline(
        [complaint],
        media_root=root,
        llm_client=client,  # type: ignore[arg-type]
        damage_cache=DamageSeverityCache(tmp_path / "damage-cache.json"),
        vision_cache=VisionCache(tmp_path / "vision-cache.json"),
    )

    assert report.stats["model_mode"] == "live"
    assert report.stats["model_inference_used"] is True
    assert report.stats["vision_checks_inconclusive"] == 1
    assert report.stats["summary_fallback"] is True
    assert report.stats["claims_kept"] == len(report.claims) > 0
    assert client.summary_attempts == 2
    assert client.vision_attempts == 1
    assert initial.is_file()
    assert closure_path.is_file()


def test_citation_and_number_filtering_report_kept_and_dropped_counts(
    tmp_path: Path,
) -> None:
    root = tmp_path / "media"
    root.mkdir()
    client = _FakeLiveClient(
        claims=[
            Claim(text="There is 1 complaint.", cites=[]),
            Claim(text="There are 999 complaints.", cites=[]),
            Claim(text="A supported claim.", cites=["unknown-id"]),
        ]
    )

    report = run_pipeline(
        [_complaint("claims-case")],
        media_root=root,
        llm_client=client,  # type: ignore[arg-type]
    )

    assert report.stats["claims_raw"] == 3
    assert report.stats["claims_kept"] == 1
    assert report.stats["claims_dropped"] == 2
    assert report.claims[0].text == "There is 1 complaint."


def test_invalid_complaint_input_fails_before_report_assembly(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="does not match Complaint"):
        run_pipeline(
            [{"id": "bad", "category": "not-a-category", "text": "private"}],
            media_root=tmp_path,
            mock=True,
        )
