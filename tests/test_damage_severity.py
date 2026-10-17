"""Tests for Phase 9: Vision damage-severity scoring for complaint photos.

Covers all 10 acceptance criteria for Issue #14.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from core.omnigrid.cluster import (
    cluster_complaints,
    compute_severity_breakdown,
    compute_severity_score,
)
from core.omnigrid.damage import (
    DamageSeverityCache,
    DamageSeverityScore,
    compute_severity_score_with_damage,
    score_damage_severity,
    score_damage_severity_batch,
)
from core.omnigrid.llm import LLMClient, LLMConnectionError, LLMTimeoutError
from PIL import Image
from pydantic import ValidationError


@pytest.fixture
def tmp_image(tmp_path: Path) -> Path:
    """Create a temporary valid test image."""
    img_path = tmp_path / "test_defect.jpg"
    img = Image.new("RGB", (64, 64), color=(180, 50, 50))
    img.save(img_path, format="JPEG")
    return img_path


@pytest.fixture
def tmp_cache(tmp_path: Path) -> DamageSeverityCache:
    """Create an isolated DamageSeverityCache instance."""
    return DamageSeverityCache(cache_path=tmp_path / "test_damage_cache.json")


# ---------------------------------------------------------------------------
# Acceptance Criterion 1: Valid scores and concise explanations are accepted
# ---------------------------------------------------------------------------


def test_valid_damage_score_accepted(tmp_image, tmp_cache):
    client = LLMClient(mock=True)
    client.set_mock_response(
        DamageSeverityScore(
            score=0.75,
            reasoning="Deep pothole extending across traffic lane.",
            evidence_id="ev-101",
        )
    )

    result = score_damage_severity(
        tmp_image,
        evidence_id="ev-101",
        llm_client=client,
        cache=tmp_cache,
    )

    assert result is not None
    assert result.score == 0.75
    assert "traffic lane" in result.reasoning
    assert result.evidence_id == "ev-101"


def test_cached_score_uses_requested_evidence_id(tmp_image, tmp_cache):
    first_client = LLMClient(mock=True)
    first_client.set_mock_response(
        DamageSeverityScore(
            score=0.75,
            reasoning="Visible defect",
            evidence_id="first-evidence",
        )
    )
    first = score_damage_severity(
        tmp_image,
        evidence_id="first-evidence",
        llm_client=first_client,
        cache=tmp_cache,
    )
    second = score_damage_severity(
        tmp_image,
        evidence_id="second-evidence",
        llm_client=LLMClient(mock=True),
        cache=tmp_cache,
    )

    assert first is not None and second is not None
    assert second.score == first.score
    assert second.evidence_id == "second-evidence"
    mock_key = tmp_cache.compute_key(
        tmp_image.read_bytes(),
        first_client.vision_cache_identity,
    )
    live_key = tmp_cache.compute_key(
        tmp_image.read_bytes(),
        first_client.vision_cache_identity.removeprefix("mock:"),
    )
    assert tmp_cache.get(mock_key) is not None
    assert tmp_cache.get(live_key) is None


# ---------------------------------------------------------------------------
# Acceptance Criterion 2: Out of range, nonnumeric, NaN, infinity rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "invalid_score",
    [-0.1, 1.1, -100.0, 5.0, float("nan"), float("inf"), float("-inf"), "not_a_number"],
)
def test_invalid_scores_rejected(invalid_score):
    with pytest.raises(ValidationError):
        DamageSeverityScore(score=invalid_score, reasoning="Should fail")


def test_boolean_score_rejected():
    with pytest.raises(ValidationError):
        DamageSeverityScore(score=True, reasoning="Booleans not allowed")


def test_out_of_range_llm_response_returns_none(tmp_image, tmp_cache):
    client = LLMClient(mock=True)
    # LLM returns a dictionary with out-of-range score
    client.set_mock_response({"score": 1.5, "reasoning": "Too high"})

    result = score_damage_severity(tmp_image, llm_client=client, cache=tmp_cache)
    assert result is None


# ---------------------------------------------------------------------------
# Acceptance Criterion 3: Missing or unreadable photos do not receive scores
# ---------------------------------------------------------------------------


def test_missing_photo_returns_none(tmp_cache):
    client = LLMClient(mock=True)
    non_existent = Path("/non/existent/path/photo_999.jpg")
    result = score_damage_severity(non_existent, llm_client=client, cache=tmp_cache)
    assert result is None


def test_unreadable_photo_returns_none(tmp_path, tmp_cache):
    client = LLMClient(mock=True)
    corrupted_file = tmp_path / "corrupt.jpg"
    corrupted_file.write_bytes(b"NOT_A_VALID_IMAGE_DATA")

    result = score_damage_severity(corrupted_file, llm_client=client, cache=tmp_cache)
    assert result is None


# ---------------------------------------------------------------------------
# Acceptance Criterion 4: Provider failures are handled safely
# ---------------------------------------------------------------------------


def test_provider_timeout_handled_safely(tmp_image, tmp_cache):
    client = LLMClient(mock=True)
    client.set_mock_response(LLMTimeoutError("Vision model timed out"))

    result = score_damage_severity(tmp_image, llm_client=client, cache=tmp_cache)
    assert result is None


def test_provider_connection_error_handled_safely(tmp_image, tmp_cache):
    client = LLMClient(mock=True)
    client.set_mock_response(LLMConnectionError("Ollama connection failed"))

    result = score_damage_severity(tmp_image, llm_client=client, cache=tmp_cache)
    assert result is None


# ---------------------------------------------------------------------------
# Acceptance Criterion 5: Repeated requests reuse valid cached results
# ---------------------------------------------------------------------------


def test_cache_hit_avoids_duplicate_inference(tmp_image, tmp_cache):
    client = LLMClient(mock=True)
    # Only enqueue ONE response
    client.set_mock_response(
        DamageSeverityScore(
            score=0.60,
            reasoning="Moderate waterlogging.",
            evidence_id="ev-first",
        )
    )

    # First call: cache miss -> queries mock client
    res1 = score_damage_severity(tmp_image, llm_client=client, cache=tmp_cache)
    assert res1 is not None
    assert res1.score == 0.60

    # Second call: cache hit -> uses cache without calling client
    # (client has no responses left)
    res2 = score_damage_severity(tmp_image, llm_client=client, cache=tmp_cache)
    assert res2 is not None
    assert res2.score == 0.60
    assert res2.reasoning == res1.reasoning


# ---------------------------------------------------------------------------
# Acceptance Criterion 6: Changed image or prompt/model invalidates cache
# ---------------------------------------------------------------------------


def test_changed_image_content_invalidates_cache(tmp_path, tmp_cache):
    client = LLMClient(mock=True)
    client.set_mock_response(
        DamageSeverityScore(score=0.20, reasoning="Image 1 minor")
    )
    client.set_mock_response(
        DamageSeverityScore(score=0.80, reasoning="Image 2 severe")
    )

    img1 = tmp_path / "img1.jpg"
    Image.new("RGB", (32, 32), color=(0, 255, 0)).save(img1, format="JPEG")

    img2 = tmp_path / "img2.jpg"
    Image.new("RGB", (32, 32), color=(255, 0, 0)).save(img2, format="JPEG")

    res1 = score_damage_severity(img1, llm_client=client, cache=tmp_cache)
    res2 = score_damage_severity(img2, llm_client=client, cache=tmp_cache)

    assert res1.score == 0.20
    assert res2.score == 0.80


def test_changed_prompt_version_invalidates_cache(tmp_cache):
    b = b"dummy_image_bytes"
    key_v1 = tmp_cache.compute_key(b, "qwen2.5vl:7b", prompt_version="v1")
    key_v2 = tmp_cache.compute_key(b, "qwen2.5vl:7b", prompt_version="v2")
    assert key_v1 != key_v2


# ---------------------------------------------------------------------------
# Acceptance Criterion 7: Cache corruption or write failure does not crash
# ---------------------------------------------------------------------------


def test_corrupted_cache_file_handled_safely(tmp_path):
    corrupt_cache_path = tmp_path / "corrupt_cache.json"
    corrupt_cache_path.write_text("{ this is not valid JSON !!!", encoding="utf-8")

    cache = DamageSeverityCache(cache_path=corrupt_cache_path)
    # Reading corrupt cache should return None rather than raising an exception
    assert cache.get("any_key") is None


def test_cache_write_failure_does_not_crash(tmp_image, monkeypatch):
    client = LLMClient(mock=True)
    client.set_mock_response(DamageSeverityScore(score=0.50, reasoning="Fair"))

    cache = DamageSeverityCache()

    def bad_write(*args, **kwargs):
        raise OSError("Disk full simulation")

    monkeypatch.setattr(Path, "write_text", bad_write)

    # Should succeed without raising exception
    result = score_damage_severity(tmp_image, llm_client=client, cache=cache)
    assert result is not None
    assert result.score == 0.50


# ---------------------------------------------------------------------------
# Acceptance Criterion 8: Stable evidence identifiers are used consistently
# ---------------------------------------------------------------------------


def test_stable_evidence_id_preserved(tmp_image, tmp_cache):
    client = LLMClient(mock=True)
    client.set_mock_response(
        DamageSeverityScore(score=0.45, reasoning="Cracked curb", evidence_id="")
    )

    result = score_damage_severity(
        tmp_image,
        evidence_id="photo-complaint-042",
        llm_client=client,
        cache=tmp_cache,
    )
    assert result.evidence_id == "photo-complaint-042"

    # Repeated cached call with evidence_id preserved
    cached = score_damage_severity(
        tmp_image,
        evidence_id="photo-complaint-042",
        llm_client=client,
        cache=tmp_cache,
    )
    assert cached.evidence_id == "photo-complaint-042"


def test_batch_scoring(tmp_path, tmp_cache):
    client = LLMClient(mock=True)
    client.set_mock_response(DamageSeverityScore(score=0.3, reasoning="R1"))
    client.set_mock_response(DamageSeverityScore(score=0.7, reasoning="R2"))

    p1 = tmp_path / "p1.jpg"
    p2 = tmp_path / "p2.jpg"
    Image.new("RGB", (16, 16), color=(1, 1, 1)).save(p1, format="JPEG")
    Image.new("RGB", (16, 16), color=(2, 2, 2)).save(p2, format="JPEG")

    batch = score_damage_severity_batch(
        [p1, p2],
        evidence_ids=["e1", "e2"],
        llm_client=client,
        cache=tmp_cache,
    )
    assert len(batch) == 2
    assert batch[str(p1)].score == 0.3
    assert batch[str(p1)].evidence_id == "e1"
    assert batch[str(p2)].score == 0.7
    assert batch[str(p2)].evidence_id == "e2"


# ---------------------------------------------------------------------------
# Acceptance Criterion 9: Clustering consumes available scores through interface
# ---------------------------------------------------------------------------


def test_clustering_applies_damage_scores_formula():
    """Verify damage scores integrate into the 0.30*vlm_damage_score weight."""
    complaints = [
        {"id": "c-1", "lat": 12.9716, "lon": 77.5946, "text": "Pothole on street"},
        {"id": "c-2", "lat": 12.9717, "lon": 77.5947, "text": "Another pothole"},
    ]

    # Without damage scores -> vlm_damage_score is 0.0
    bd_zero = compute_severity_breakdown(complaints)
    assert bd_zero["vlm_damage_score"] == 0.0

    # With damage scores 0.80 and 0.60 -> mean is 0.70
    damage_scores = {"c-1": 0.80, "c-2": 0.60}
    bd_scored = compute_severity_breakdown(complaints, damage_scores=damage_scores)
    assert bd_scored["vlm_damage_score"] == 0.70

    # Severity formula verification:
    # diff in severity should be exactly 10.0 * 0.30 * 0.70 = 2.10
    expected_diff = round(10.0 * 0.30 * 0.70, 2)
    actual_diff = round(bd_scored["severity"] - bd_zero["severity"], 2)
    assert actual_diff == expected_diff

    # Also test cluster_complaints interface directly
    hotspots = cluster_complaints(
        complaints,
        eps_meters=500.0,
        min_samples=2,
        damage_scores=damage_scores,
    )
    assert len(hotspots) == 1
    assert hotspots[0].severity == bd_scored["severity"]


def test_compute_severity_score_with_damage_helper():
    complaints = [
        {"id": "c-1", "lat": 12.0, "lon": 77.0, "text": "pothole"},
    ]
    score = compute_severity_score_with_damage(complaints, damage_scores={"c-1": 0.90})
    assert score > 0.0


# ---------------------------------------------------------------------------
# Acceptance Criterion 10: Missing scores preserve safe backward-compatible behavior
# ---------------------------------------------------------------------------


def test_missing_damage_scores_backward_compatible():
    complaints = [
        {"id": "c-1", "lat": 12.9716, "lon": 77.5946, "text": "Pothole on street"},
        {"id": "c-2", "lat": 12.9717, "lon": 77.5947, "text": "Another pothole"},
    ]

    # Calling with damage_scores=None or omitted behaves identically
    sev_default = compute_severity_score(complaints)
    sev_none = compute_severity_score(complaints, damage_scores=None)
    sev_empty = compute_severity_score(complaints, damage_scores={})

    assert sev_default == sev_none == sev_empty

    # Partial scores: only c-1 has score, c-2 is missing
    # Formula uses available scores (mean of [0.80] = 0.80)
    bd_partial = compute_severity_breakdown(complaints, damage_scores={"c-1": 0.80})
    assert bd_partial["vlm_damage_score"] == 0.80
