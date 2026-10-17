"""Comprehensive tests for Issue #11: vision-based closure consistency check.

Covers positive/negative assessments, location uncertainty, low confidence,
malformed responses, timeouts, cache hits/misses, swapping images, corruption,
and deterministic check precedence.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from core.omnigrid.llm import (
    LLMResponseError,
    LLMTimeoutError,
    get_llm_client,
)
from core.omnigrid.verify import (
    ClosureVisionAssessment,
    VisionCache,
    check_vision_consistency,
    verify_closed_complaints,
    verify_closure,
)
from PIL import Image


def _make_dummy_image(
    path: Path,
    color: str = "red",
    size: tuple[int, int] = (64, 64),
) -> Path:
    """Helper to create a solid-color test JPEG image."""
    img = Image.new("RGB", size, color=color)
    img.save(path, format="JPEG")
    return path


# -----------------------------------------------------------------------------
# Unit Tests for check_vision_consistency
# -----------------------------------------------------------------------------


def test_positive_vision_assessment(tmp_path: Path) -> None:
    img1 = _make_dummy_image(tmp_path / "orig.jpg", color="blue")
    img2 = _make_dummy_image(tmp_path / "closure.jpg", color="green")
    cache = VisionCache(cache_path=tmp_path / "cache.json")

    client = get_llm_client(mock=True)
    client.set_mock_response(
        ClosureVisionAssessment(
            same_location_likely=True,
            defect_repaired=True,
            confidence=0.92,
            reasoning="Pothole was filled with fresh asphalt.",
        )
    )

    check = check_vision_consistency(img1, img2, llm_client=client, cache=cache)
    assert check.name == "vision_consistency"
    assert check.passed is True
    assert check.value == 0.92
    assert "confirms defect repaired" in check.detail


def test_negative_repair_assessment(tmp_path: Path) -> None:
    img1 = _make_dummy_image(tmp_path / "orig.jpg", color="red")
    img2 = _make_dummy_image(tmp_path / "closure.jpg", color="red")
    cache = VisionCache(cache_path=tmp_path / "cache.json")

    client = get_llm_client(mock=True)
    client.set_mock_response(
        ClosureVisionAssessment(
            same_location_likely=True,
            defect_repaired=False,
            confidence=0.88,
            reasoning="Pothole crater is still open and unpaved.",
        )
    )

    check = check_vision_consistency(img1, img2, llm_client=client, cache=cache)
    assert check.name == "vision_consistency"
    assert check.passed is False
    assert check.value == 0.88
    assert "was not repaired" in check.detail
    assert "Needs human review." in check.detail


def test_different_location_uncertainty(tmp_path: Path) -> None:
    img1 = _make_dummy_image(tmp_path / "orig.jpg", color="yellow")
    img2 = _make_dummy_image(tmp_path / "closure.jpg", color="purple")
    cache = VisionCache(cache_path=tmp_path / "cache.json")

    client = get_llm_client(mock=True)
    client.set_mock_response(
        ClosureVisionAssessment(
            same_location_likely=False,
            defect_repaired=True,
            confidence=0.82,
            reasoning="Background architecture and road texture do not match.",
        )
    )

    check = check_vision_consistency(img1, img2, llm_client=client, cache=cache)
    assert check.name == "vision_consistency"
    assert check.passed is False
    assert check.value == 0.82
    assert "unlikely to depict the same location" in check.detail
    assert "Needs human review." in check.detail


def test_low_confidence_is_inconclusive(tmp_path: Path) -> None:
    img1 = _make_dummy_image(tmp_path / "orig.jpg")
    img2 = _make_dummy_image(tmp_path / "closure.jpg")
    cache = VisionCache(cache_path=tmp_path / "cache.json")

    client = get_llm_client(mock=True)
    client.set_mock_response(
        ClosureVisionAssessment(
            same_location_likely=True,
            defect_repaired=True,
            confidence=0.45,  # Below default min_confidence=0.60
            reasoning="Image is blurry; uncertain if defect was repaired.",
        )
    )

    check = check_vision_consistency(img1, img2, llm_client=client, cache=cache)
    assert check.name == "vision_consistency"
    assert check.passed is None
    assert check.value == 0.45
    assert "low confidence" in check.detail


def test_malformed_response_handling(tmp_path: Path) -> None:
    img1 = _make_dummy_image(tmp_path / "orig.jpg")
    img2 = _make_dummy_image(tmp_path / "closure.jpg")
    cache = VisionCache(cache_path=tmp_path / "cache.json")

    client = get_llm_client(mock=True)
    client.set_mock_response(LLMResponseError("Invalid JSON structure"))

    check = check_vision_consistency(img1, img2, llm_client=client, cache=cache)
    assert check.name == "vision_consistency"
    assert check.passed is None
    assert "invalid" in check.detail.lower()


def test_missing_or_unreadable_images(tmp_path: Path) -> None:
    valid_img = _make_dummy_image(tmp_path / "valid.jpg")
    missing_img = tmp_path / "does_not_exist.jpg"

    check1 = check_vision_consistency(missing_img, valid_img)
    assert check1.passed is None
    assert "not found" in check1.detail.lower()

    check2 = check_vision_consistency(valid_img, missing_img)
    assert check2.passed is None
    assert "not found" in check2.detail.lower()


def test_timeout_and_provider_failure(tmp_path: Path) -> None:
    img1 = _make_dummy_image(tmp_path / "orig.jpg")
    img2 = _make_dummy_image(tmp_path / "closure.jpg")

    client = get_llm_client(mock=True)
    client.set_mock_response(LLMTimeoutError("Model timed out after 30s"))

    check = check_vision_consistency(img1, img2, llm_client=client)
    assert check.name == "vision_consistency"
    assert check.passed is None
    assert "unavailable" in check.detail.lower()


# -----------------------------------------------------------------------------
# Caching Tests
# -----------------------------------------------------------------------------


def test_cache_hit_and_miss(tmp_path: Path) -> None:
    img1 = _make_dummy_image(tmp_path / "orig.jpg", color="black")
    img2 = _make_dummy_image(tmp_path / "closure.jpg", color="white")
    cache = VisionCache(cache_path=tmp_path / "cache.json")

    client = MagicMock()
    client.config.vision_model = "test-vision-model"
    client.compare_images_structured.return_value = ClosureVisionAssessment(
        same_location_likely=True,
        defect_repaired=True,
        confidence=0.95,
        reasoning="Repaired.",
    )

    # 1. First call: Cache miss -> invokes client
    check1 = check_vision_consistency(img1, img2, llm_client=client, cache=cache)
    assert check1.passed is True
    assert client.compare_images_structured.call_count == 1

    # 2. Second call: Cache hit -> does NOT invoke client again
    check2 = check_vision_consistency(img1, img2, llm_client=client, cache=cache)
    assert check2.passed is True
    assert client.compare_images_structured.call_count == 1


def test_cache_key_order_and_invalidation(tmp_path: Path) -> None:
    img_a = _make_dummy_image(tmp_path / "a.jpg", color="red")
    img_b = _make_dummy_image(tmp_path / "b.jpg", color="blue")
    cache = VisionCache(cache_path=tmp_path / "cache.json")

    bytes_a = img_a.read_bytes()
    bytes_b = img_b.read_bytes()

    # Swapping order produces different cache keys
    key_ab = cache.compute_key(bytes_a, bytes_b, model_name="model-v1")
    key_ba = cache.compute_key(bytes_b, bytes_a, model_name="model-v1")
    assert key_ab != key_ba

    # Changing model name invalidates cache
    key_model2 = cache.compute_key(bytes_a, bytes_b, model_name="model-v2")
    assert key_ab != key_model2


def test_cache_corruption_tolerance(tmp_path: Path) -> None:
    cache_path = tmp_path / "corrupt_cache.json"
    cache_path.write_text("{this is not valid json content!!!", encoding="utf-8")
    cache = VisionCache(cache_path=cache_path)

    # Getting from corrupted cache does not crash, returns None
    assert cache.get("any_key") is None

    # check_vision_consistency works despite corrupt cache
    img1 = _make_dummy_image(tmp_path / "orig.jpg")
    img2 = _make_dummy_image(tmp_path / "closure.jpg")
    client = get_llm_client(mock=True)

    check = check_vision_consistency(img1, img2, llm_client=client, cache=cache)
    assert check.name == "vision_consistency"
    assert check.passed is True


# -----------------------------------------------------------------------------
# Precedence and Pipeline Integration Tests
# -----------------------------------------------------------------------------


def test_deterministic_failure_cannot_be_overridden_by_positive_vision(
    tmp_path: Path,
) -> None:
    """A positive vision check must NEVER override a failed deterministic check."""
    from scripts.generate_sample_media import generate_sample_media

    media_dir = tmp_path / "media"
    generate_sample_media(media_dir)

    # complaint-003 has mismatched GPS (fails gps_geofence by ~23km)
    complaints_file = Path("data/synthetic/complaints.geojson")
    data = json.loads(complaints_file.read_text(encoding="utf-8"))
    c003 = next(
        f["properties"]
        for f in data["features"]
        if f["properties"]["id"] == "complaint-003"
    )

    c003_with_media = dict(c003)
    c003_with_media["media"] = [
        {"path": str(media_dir / "media_01.jpg"), "type": "image"}
    ]

    client = get_llm_client(mock=True)
    client.set_mock_response(
        ClosureVisionAssessment(
            same_location_likely=True,
            defect_repaired=True,
            confidence=0.99,
            reasoning="Defect looks completely repaired.",
        )
    )

    verdict = verify_closure(
        c003_with_media,
        media_dir=media_dir,
        llm_client=client,
        use_vision=True,
        cache=VisionCache(cache_path=tmp_path / "c003_cache.json"),
    )

    # Strict requirement: verdict MUST remain "suspect" because GPS geofence failed!
    assert verdict.verdict == "suspect"

    gps_check = next(c for c in verdict.checks if c.name == "gps_geofence")
    assert gps_check.passed is False

    vis_check = next(c for c in verdict.checks if c.name == "vision_consistency")
    assert vis_check.passed is True


def test_vision_failure_triggers_suspect_verdict(tmp_path: Path) -> None:
    """If checks pass but vision detects unrepaired defect, verdict is suspect."""
    from scripts.generate_sample_media import generate_sample_media

    media_dir = tmp_path / "media"
    generate_sample_media(media_dir)

    # complaint-001 is a legitimate closure
    complaints_file = Path("data/synthetic/complaints.geojson")
    data = json.loads(complaints_file.read_text(encoding="utf-8"))
    c001 = next(
        f["properties"]
        for f in data["features"]
        if f["properties"]["id"] == "complaint-001"
    )

    c001_with_media = dict(c001)
    c001_with_media["media"] = [
        {"path": str(media_dir / "media_01.jpg"), "type": "image"}
    ]

    client = get_llm_client(mock=True)
    client.set_mock_response(
        ClosureVisionAssessment(
            same_location_likely=True,
            defect_repaired=False,
            confidence=0.90,
            reasoning="Pothole still present.",
        )
    )

    verdict = verify_closure(
        c001_with_media,
        media_dir=media_dir,
        llm_client=client,
        use_vision=True,
        cache=VisionCache(cache_path=tmp_path / "fail_cache.json"),
    )

    assert verdict.verdict == "suspect"
    vis_check = next(c for c in verdict.checks if c.name == "vision_consistency")
    assert vis_check.passed is False


def test_vision_inconclusive_yields_inconclusive_verdict(tmp_path: Path) -> None:
    """If checks pass but vision is inconclusive, verdict is inconclusive."""
    from scripts.generate_sample_media import generate_sample_media

    media_dir = tmp_path / "media"
    generate_sample_media(media_dir)

    complaints_file = Path("data/synthetic/complaints.geojson")
    data = json.loads(complaints_file.read_text(encoding="utf-8"))
    c001 = next(
        f["properties"]
        for f in data["features"]
        if f["properties"]["id"] == "complaint-001"
    )

    c001_with_media = dict(c001)
    c001_with_media["media"] = [
        {"path": str(media_dir / "media_01.jpg"), "type": "image"}
    ]

    client = get_llm_client(mock=True)
    client.set_mock_response(
        ClosureVisionAssessment(
            same_location_likely=True,
            defect_repaired=True,
            confidence=0.40,
            reasoning="Uncertain.",
        )
    )

    verdict = verify_closure(
        c001_with_media,
        media_dir=media_dir,
        llm_client=client,
        use_vision=True,
        cache=VisionCache(cache_path=tmp_path / "inconc_cache.json"),
    )

    assert verdict.verdict == "inconclusive"
    vis_check = next(c for c in verdict.checks if c.name == "vision_consistency")
    assert vis_check.passed is None


def test_all_passed_yields_verified_verdict(tmp_path: Path) -> None:
    """When deterministic and vision checks all pass, verdict is verified."""
    from scripts.generate_sample_media import generate_sample_media

    media_dir = tmp_path / "media"
    generate_sample_media(media_dir)

    complaints_file = Path("data/synthetic/complaints.geojson")
    data = json.loads(complaints_file.read_text(encoding="utf-8"))
    c001 = next(
        f["properties"]
        for f in data["features"]
        if f["properties"]["id"] == "complaint-001"
    )

    c001_with_media = dict(c001)
    c001_with_media["media"] = [
        {"path": str(media_dir / "media_01.jpg"), "type": "image"}
    ]

    client = get_llm_client(mock=True)
    client.set_mock_response(
        ClosureVisionAssessment(
            same_location_likely=True,
            defect_repaired=True,
            confidence=0.95,
            reasoning="Pothole completely repaired.",
        )
    )

    verdict = verify_closure(
        c001_with_media,
        media_dir=media_dir,
        llm_client=client,
        use_vision=True,
        cache=VisionCache(cache_path=tmp_path / "pass_cache.json"),
    )

    assert verdict.verdict == "verified"
    assert verdict.confidence > 0.8
    vis_check = next(c for c in verdict.checks if c.name == "vision_consistency")
    assert vis_check.passed is True


def test_batch_verify_with_vision(tmp_path: Path) -> None:
    from scripts.generate_sample_media import generate_sample_media

    media_dir = tmp_path / "media"
    generate_sample_media(media_dir)

    complaints_file = Path("data/synthetic/complaints.geojson")
    client = get_llm_client(mock=True)

    verdicts = verify_closed_complaints(
        complaints_file,
        media_dir=media_dir,
        llm_client=client,
        use_vision=True,
        cache=VisionCache(cache_path=tmp_path / "batch_cache.json"),
    )

    assert len(verdicts) == 100
    # For complaints that have closure media, vision check is run
    verdicts_with_media = [
        v for v in verdicts if any(c.name == "gps_geofence" for c in v.checks)
    ]
    assert len(verdicts_with_media) == 10
    for v in verdicts_with_media:
        vis_check = next((c for c in v.checks if c.name == "vision_consistency"), None)
        assert vis_check is not None
