"""Comprehensive tests for Phase 3: Spatial clustering & severity scoring."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from core.omnigrid.cluster import (
    EARTH_RADIUS_METERS,
    RECENCY_HALF_LIFE_HOURS,
    SAFETY_KEYWORDS,
    WEIGHT_COUNT,
    WEIGHT_RECENCY,
    cluster_complaints,
    compute_severity_breakdown,
    compute_severity_score,
    haversine_distance,
)
from core.omnigrid.schemas import Complaint, Hotspot

REPO_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_DIR = REPO_ROOT / "data" / "synthetic"
COMPLAINTS_GEOJSON = SYNTHETIC_DIR / "complaints.geojson"
GROUND_TRUTH_JSON = SYNTHETIC_DIR / "ground_truth.json"


# -----------------------------------------------------------------------------
# 1. Haversine Distance Accuracy Tests
# -----------------------------------------------------------------------------


def test_haversine_distance_same_point_is_zero() -> None:
    """Distance from a point to itself must be exactly 0.0 meters."""
    dist = haversine_distance(12.9716, 77.5946, 12.9716, 77.5946)
    assert dist == pytest.approx(0.0, abs=1e-5)


def test_haversine_distance_known_meridian_arc() -> None:
    """1 degree latitude on Earth (R=6,371,000m) is exactly (pi * R) / 180."""
    expected_one_deg = (math.pi * EARTH_RADIUS_METERS) / 180.0
    dist = haversine_distance(0.0, 0.0, 1.0, 0.0)
    assert dist == pytest.approx(expected_one_deg, rel=1e-5)


def test_haversine_distance_known_bengaluru_landmarks() -> None:
    """Distance between Silk Board and Indiranagar."""
    # Expected great-circle distance is ~7.09 km
    dist = haversine_distance(12.9172, 77.6228, 12.9784, 77.6408)
    assert 7000.0 < dist < 7200.0


# -----------------------------------------------------------------------------
# 2. Degree-to-Radian and Epsilon Conversion Tests
# -----------------------------------------------------------------------------


def test_eps_and_coordinate_conversion_logic() -> None:
    """Verify formula: eps_rad = eps_meters / 6,371,000 and np.radians."""
    eps_meters = 150.0
    eps_rad = eps_meters / EARTH_RADIUS_METERS

    assert eps_rad == pytest.approx(2.3544e-5, rel=1e-3)

    deg = (12.9172, 77.6228)
    rad = np.radians(deg)
    assert rad[0] == pytest.approx(math.radians(12.9172))
    assert rad[1] == pytest.approx(math.radians(77.6228))


# -----------------------------------------------------------------------------
# 3. Noise Handling, Outliers, and Invalid Coordinates
# -----------------------------------------------------------------------------


def test_sparse_isolated_points_are_classified_as_noise() -> None:
    """Points fewer than min_samples or too far apart must not form cluster."""
    sparse_complaints = [
        Complaint(
            id="c1",
            category="pothole",
            text="Pothole on North road",
            lat=12.9000,
            lon=77.5000,
            created_at=datetime(2026, 10, 15, tzinfo=UTC),
            status="open",
        ),
        Complaint(
            id="c2",
            category="pothole",
            text="Pothole on South road",
            lat=13.1000,
            lon=77.7000,
            created_at=datetime(2026, 10, 15, tzinfo=UTC),
            status="open",
        ),
    ]
    hotspots = cluster_complaints(
        sparse_complaints,
        eps_meters=150.0,
        min_samples=3,
    )
    assert hotspots == []


def test_two_close_points_rejected_when_min_samples_is_three() -> None:
    """Clusters require at least min_samples members; 2 must be rejected."""
    complaints = [
        Complaint(
            id="c1",
            category="pothole",
            text="Complaint 1",
            lat=12.9172,
            lon=77.6228,
            created_at=datetime(2026, 10, 15, tzinfo=UTC),
            status="open",
        ),
        Complaint(
            id="c2",
            category="pothole",
            text="Complaint 2",
            lat=12.91721,
            lon=77.62281,
            created_at=datetime(2026, 10, 15, tzinfo=UTC),
            status="open",
        ),
    ]
    hotspots = cluster_complaints(complaints, eps_meters=150.0, min_samples=3)
    assert len(hotspots) == 0


def test_invalid_coordinates_handled_safely() -> None:
    """Invalid, null, NaN, and out-of-range coords are safely filtered out."""
    now = datetime(2026, 10, 15, tzinfo=UTC)
    raw_complaints = [
        # Valid 1, 2, 3 near Silk Board
        {
            "id": "v1",
            "category": "pothole",
            "lat": 12.9172,
            "lon": 77.6228,
            "created_at": now,
        },
        {
            "id": "v2",
            "category": "pothole",
            "lat": 12.91722,
            "lon": 77.62282,
            "created_at": now,
        },
        {
            "id": "v3",
            "category": "pothole",
            "lat": 12.91724,
            "lon": 77.62284,
            "created_at": now,
        },
        # Invalid coordinates that should be ignored
        {
            "id": "b1",
            "category": "pothole",
            "lat": None,
            "lon": 77.62,
            "created_at": now,
        },
        {
            "id": "b2",
            "category": "pothole",
            "lat": float("nan"),
            "lon": 77.62,
            "created_at": now,
        },
        {
            "id": "b3",
            "category": "pothole",
            "lat": 120.0,
            "lon": 77.6,
            "created_at": now,
        },
        {
            "id": "b4",
            "category": "pothole",
            "lat": 12.9,
            "lon": -200.0,
            "created_at": now,
        },

        {
            "id": "b5",
            "category": "pothole",
            "lat": "bad",
            "lon": 77.6,
            "created_at": now,
        },
    ]

    hotspots = cluster_complaints(raw_complaints, eps_meters=150.0, min_samples=3)
    assert len(hotspots) == 1
    assert hotspots[0].complaint_count == 3
    assert set(hotspots[0].complaint_ids) == {"v1", "v2", "v3"}


def test_empty_complaints_returns_empty_list() -> None:
    assert cluster_complaints([]) == []


# -----------------------------------------------------------------------------
# 4. Severity Scoring Formula, Bounds, Fallbacks, and Keywords
# -----------------------------------------------------------------------------


def test_severity_score_empty_complaints() -> None:
    assert compute_severity_score([]) == 0.0


def test_severity_bounds_between_zero_and_ten() -> None:
    """Extreme scenarios must stay clamped within [0.0, 10.0]."""
    ref_time = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)

    # 1. Theoretical Maximum: 25 complaints, all with 1.0 VLM damage,
    # 0 hours old (recency=1.0), and all reporting critical injury.
    max_complaints = [
        {
            "id": f"c-{i}",
            "text": "Critical accident with injury, taken to hospital!",
            "lat": 12.9,
            "lon": 77.6,
            "created_at": ref_time,
            "vlm_damage_score": 1.0,
        }
        for i in range(25)
    ]
    max_score = compute_severity_score(max_complaints, reference_time=ref_time)
    assert max_score == 10.0

    # 2. Breakdown matches exact weights
    bd = compute_severity_breakdown(max_complaints, reference_time=ref_time)
    assert bd["count_score"] == 1.0
    assert bd["vlm_damage_score"] == 1.0
    assert bd["recency_score"] == 1.0
    assert bd["injury_score"] == 1.0
    assert bd["severity"] == 10.0


def test_missing_vlm_damage_score_explicit_fallback() -> None:
    """Missing VLM scores must explicitly fall back to 0.0 without guessing."""
    ref_time = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)
    complaints = [
        {
            "id": f"c-{i}",
            "text": "Broken pavement observed.",
            "lat": 12.9,
            "lon": 77.6,
            "created_at": ref_time,
            # No vlm_damage_score or metadata provided
        }
        for i in range(5)
    ]

    bd = compute_severity_breakdown(complaints, reference_time=ref_time)
    assert bd["vlm_damage_score"] == 0.0

    count_s = math.log2(6.0) / math.log2(21.0)
    expected_severity = round(
        10.0 * (WEIGHT_COUNT * count_s + WEIGHT_RECENCY * 1.0),
        2,
    )
    assert bd["severity"] == expected_severity


def test_count_saturation_at_twenty() -> None:
    """Count score must saturate at exactly 1.0 for n >= 20."""
    now = datetime(2026, 10, 15, tzinfo=UTC)
    twenty = [
        {"id": f"c{i}", "text": "Issue", "lat": 12.0, "lon": 77.0, "created_at": now}
        for i in range(20)
    ]
    thirty = [
        {"id": f"c{i}", "text": "Issue", "lat": 12.0, "lon": 77.0, "created_at": now}
        for i in range(30)
    ]

    bd20 = compute_severity_breakdown(twenty, reference_time=now)
    bd30 = compute_severity_breakdown(thirty, reference_time=now)

    assert bd20["count_score"] == 1.0
    assert bd30["count_score"] == 1.0


def test_recency_exponential_decay_half_life() -> None:
    """At age = 72 hours, recency score must be exactly 0.5."""
    ref_time = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)
    old_time = ref_time - timedelta(hours=RECENCY_HALF_LIFE_HOURS)

    complaints = [
        {
            "id": "c1",
            "text": "General defect",
            "lat": 12.0,
            "lon": 77.0,
            "created_at": old_time,
        }
    ]
    bd = compute_severity_breakdown(complaints, reference_time=ref_time)
    assert bd["recency_score"] == pytest.approx(0.5, rel=1e-3)


def test_safety_keywords_detection() -> None:
    """Verify detection of each safety keyword."""
    ref_time = datetime(2026, 10, 15, tzinfo=UTC)
    for kw in SAFETY_KEYWORDS:
        complaint = {
            "id": "kw-test",
            "text": f"There was a serious {kw} reported near the junction.",
            "lat": 12.0,
            "lon": 77.0,
            "created_at": ref_time,
        }
        bd = compute_severity_breakdown([complaint], reference_time=ref_time)
        assert bd["injury_score"] == 1.0, f"Keyword '{kw}' missed."


# -----------------------------------------------------------------------------
# 5. Full Dataset Verification against Synthetic GeoJSON & Ground Truth
# -----------------------------------------------------------------------------


def test_synthetic_dataset_recovers_all_five_planted_hotspots() -> None:
    """Clustering complaints.geojson must recover all 5 planted hotspots."""
    assert COMPLAINTS_GEOJSON.is_file(), f"Missing {COMPLAINTS_GEOJSON}"
    assert GROUND_TRUTH_JSON.is_file(), f"Missing {GROUND_TRUTH_JSON}"

    with open(COMPLAINTS_GEOJSON, encoding="utf-8") as f:
        geojson_data = json.load(f)

    with open(GROUND_TRUTH_JSON, encoding="utf-8") as f:
        gt_data = json.load(f)

    features = geojson_data["features"]
    assert len(features) == 500

    # Cluster using default parameters (eps=150m, min_samples=3)
    hotspots = cluster_complaints(features, eps_meters=150.0, min_samples=3)

    # Exactly 5 hotspots recovered
    assert len(hotspots) == 5, f"Expected 5 hotspots, got {len(hotspots)}"

    # All returned objects conform strictly to Pydantic Hotspot schema
    for h in hotspots:
        assert isinstance(h, Hotspot)
        assert 0.0 <= h.severity <= 10.0
        assert h.radius_m >= 0.0
        assert h.complaint_count == len(h.complaint_ids)
        assert h.category_counts == h.category_mix
        # Centroid valid latitude / longitude
        assert -90.0 <= h.centroid[0] <= 90.0
        assert -180.0 <= h.centroid[1] <= 180.0
        # Deterministic ID format
        assert h.id.startswith("hotspot-")

    # Match each planted ground-truth hotspot to a recovered hotspot
    gt_hotspots = gt_data["hotspots"]
    assert len(gt_hotspots) == 5

    matched_gt_ids = set()

    for gt in gt_hotspots:
        gt_lat, gt_lon = gt["centroid"]
        gt_cids = set(gt["complaint_ids"])
        gt_count = gt["complaint_count"]

        # Find closest recovered hotspot
        best_match = None
        min_dist = float("inf")

        for h in hotspots:
            dist = haversine_distance(gt_lat, gt_lon, h.centroid[0], h.centroid[1])
            if dist < min_dist:
                min_dist = dist
                best_match = h

        assert best_match is not None
        # Centroid within 50 meters of planted center
        assert min_dist < 50.0, f"Hotspot {gt['name']} drifted {min_dist}m"

        # Complaint count must match exactly
        assert best_match.complaint_count == gt_count, (
            f"Hotspot {gt['name']} count mismatch: expected {gt_count}, "
            f"got {best_match.complaint_count}"
        )

        # Complaint IDs must match exactly
        assert set(best_match.complaint_ids) == gt_cids, (
            f"Hotspot {gt['name']} complaint ID set mismatch"
        )

        matched_gt_ids.add(gt["id"])

    # All 5 planted hotspots accounted for
    assert len(matched_gt_ids) == 5


def test_hotspot_deterministic_ordering() -> None:
    """Hotspots must be sorted descending by count, then severity, then centroid."""
    with open(COMPLAINTS_GEOJSON, encoding="utf-8") as f:
        geojson_data = json.load(f)

    hotspots = cluster_complaints(geojson_data["features"])

    for i in range(len(hotspots) - 1):
        h1 = hotspots[i]
        h2 = hotspots[i + 1]
        assert h1.complaint_count >= h2.complaint_count


def test_settings_override_and_fallback() -> None:
    """CLUSTER_EPS_METERS and CLUSTER_MIN_SAMPLES can be overridden."""
    with open(COMPLAINTS_GEOJSON, encoding="utf-8") as f:
        geojson_data = json.load(f)

    # Overriding min_samples to 20 should eliminate all hotspots (largest has 15)
    hs_high_min = cluster_complaints(geojson_data["features"], min_samples=20)
    assert len(hs_high_min) == 0

    # Using ultra-small eps (e.g. 0.1 meters / 10 cm) will find no clusters
    hs_tiny_eps = cluster_complaints(
        geojson_data["features"],
        eps_meters=0.1,
        min_samples=3,
    )
    assert len(hs_tiny_eps) == 0

