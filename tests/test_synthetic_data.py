"""Tests for synthetic data generation and sample media (Phase 2)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import imagehash
import numpy as np
import pytest
from core.omnigrid.schemas import Complaint
from PIL import Image
from scripts.generate_sample_media import (
    DEFAULT_MEDIA_DIR,
    EXPECTED_MEDIA_COUNT,
    extract_exif_gps,
)
from scripts.generate_synthetic import (
    generate_synthetic_data,
    haversine_m,
)
from sklearn.cluster import DBSCAN

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def synthetic_files(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[list[Path], Path, Path]:
    """Build JSON fixtures in temporary storage without touching tracked data."""
    media_paths = sorted(DEFAULT_MEDIA_DIR.glob("media_*.jpg"))
    _, geojson_data, ground_truth_data = generate_synthetic_data()
    output_dir = tmp_path_factory.mktemp("synthetic-data")
    geojson_path = output_dir / "complaints.geojson"
    ground_truth_path = output_dir / "ground_truth.json"
    geojson_path.write_text(json.dumps(geojson_data, indent=2), encoding="utf-8")
    ground_truth_path.write_text(
        json.dumps(ground_truth_data, indent=2), encoding="utf-8"
    )
    return media_paths, geojson_path, ground_truth_path


def test_media_count_and_existence(synthetic_files: tuple) -> None:
    media_paths, _, _ = synthetic_files
    assert len(media_paths) == EXPECTED_MEDIA_COUNT
    for p in media_paths:
        assert p.is_file()
        assert p.stat().st_size > 0


def test_media_exif_gps_tags_present(synthetic_files: tuple) -> None:
    media_paths, _, _ = synthetic_files
    for p in media_paths:
        gps = extract_exif_gps(p)
        assert gps is not None
        lat, lon = gps
        # Latitude and longitude within realistic bounds
        assert -90.0 <= lat <= 90.0
        assert -180.0 <= lon <= 180.0


def test_media_offset_gps_greater_than_5km(synthetic_files: tuple) -> None:
    # media_11 is offset from Silk Board (12.9172, 77.6228)
    gps_11 = extract_exif_gps(DEFAULT_MEDIA_DIR / "media_11.jpg")
    assert gps_11 is not None
    dist_11 = haversine_m(gps_11[0], gps_11[1], 12.9172, 77.6228)
    assert dist_11 > 5000.0  # > 5 km

    # media_12 is offset from Indiranagar (12.9784, 77.6408)
    gps_12 = extract_exif_gps(DEFAULT_MEDIA_DIR / "media_12.jpg")
    assert gps_12 is not None
    dist_12 = haversine_m(gps_12[0], gps_12[1], 12.9784, 77.6408)
    assert dist_12 > 5000.0  # > 5 km


def test_media_duplicate_images_identical_hash_different_filenames(
    synthetic_files: tuple,
) -> None:
    p14 = DEFAULT_MEDIA_DIR / "media_14.jpg"
    p15 = DEFAULT_MEDIA_DIR / "media_15.jpg"
    assert p14.name != p15.name

    bytes14 = p14.read_bytes()
    bytes15 = p15.read_bytes()
    assert hashlib.sha256(bytes14).hexdigest() == hashlib.sha256(bytes15).hexdigest()

    h14 = imagehash.phash(Image.open(p14))
    h15 = imagehash.phash(Image.open(p15))
    assert h14 - h15 == 0  # 0 Hamming distance


def test_complaints_geojson_feature_collection(synthetic_files: tuple) -> None:
    _, geojson_path, _ = synthetic_files
    data = json.loads(geojson_path.read_text(encoding="utf-8"))

    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) == 500

    open_count = 0
    closed_count = 0

    for feat in data["features"]:
        assert feat["type"] == "Feature"
        assert feat["geometry"]["type"] == "Point"
        lon, lat = feat["geometry"]["coordinates"]

        # Validate against Pydantic schema
        complaint = Complaint.model_validate(feat["properties"])
        assert complaint.id == feat["id"]
        assert complaint.lat == lat
        assert complaint.lon == lon

        # Coordinates within Bengaluru bounding box
        assert 12.78 <= lat <= 13.15
        assert 77.45 <= lon <= 77.75

        if complaint.status == "open":
            open_count += 1
            assert complaint.closure is None
        else:
            closed_count += 1
            assert complaint.closure is not None
            assert complaint.closure.closed_at >= complaint.created_at

    assert open_count == 400
    assert closed_count == 100


def test_ground_truth_structure_and_disclaimer(synthetic_files: tuple) -> None:
    _, _, ground_truth_path = synthetic_files
    gt = json.loads(ground_truth_path.read_text(encoding="utf-8"))

    meta = gt["metadata"]
    assert meta["seed"] == 42
    assert meta["disclaimer"] == "synthetic_only_no_real_citizen_data"
    assert meta["total_complaints"] == 500
    assert meta["open_complaints"] == 400
    assert meta["closed_complaints"] == 100
    assert meta["planted_hotspots_count"] == 5
    assert meta["planted_suspicious_closures_count"] == 5
    assert "generated_at" not in meta

    assert len(gt["hotspots"]) == 5
    assert len(gt["suspicious_closure_ids"]) == 5


def test_planted_hotspots_tightly_clustered(synthetic_files: tuple) -> None:
    _, geojson_path, ground_truth_path = synthetic_files
    geojson_data = json.loads(geojson_path.read_text(encoding="utf-8"))
    gt = json.loads(ground_truth_path.read_text(encoding="utf-8"))

    complaint_map = {f["id"]: f["properties"] for f in geojson_data["features"]}

    expected_names = {
        "Silk Board",
        "Indiranagar 100ft Rd",
        "Domlur Flyover",
        "Jayanagar 4th Block",
        "Hebbal",
    }
    actual_names = {h["name"] for h in gt["hotspots"]}
    assert actual_names == expected_names

    for h in gt["hotspots"]:
        count = h["complaint_count"]
        assert 12 <= count <= 15
        assert len(h["complaint_ids"]) == count

        clat, clon = h["centroid"]
        for cid in h["complaint_ids"]:
            props = complaint_map[cid]
            dist = haversine_m(props["lat"], props["lon"], clat, clon)
            assert dist <= 50.0  # Tightly clustered within 50m


def test_dbscan_recovers_planted_hotspots(synthetic_files: tuple) -> None:
    _, geojson_path, _ = synthetic_files
    geojson_data = json.loads(geojson_path.read_text(encoding="utf-8"))

    coords = [
        (f["properties"]["lat"], f["properties"]["lon"])
        for f in geojson_data["features"]
    ]
    coords_rad = np.radians(np.array(coords))
    eps_rad = 150.0 / 6371000.0  # 150 meters in radians

    db = DBSCAN(eps=eps_rad, min_samples=3, metric="haversine").fit(coords_rad)
    labels = db.labels_

    n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
    assert n_clusters == 5  # Exactly 5 hotspots recovered


def test_generator_is_reproducible_for_a_fixed_seed() -> None:
    first = generate_synthetic_data(seed=123)
    second = generate_synthetic_data(seed=123)
    assert first == second
    assert first[2]["metadata"]["seed"] == 123
    assert generate_synthetic_data(seed=124)[1] != first[1]


def test_planted_suspicious_closures(synthetic_files: tuple) -> None:
    _, geojson_path, ground_truth_path = synthetic_files
    geojson_data = json.loads(geojson_path.read_text(encoding="utf-8"))
    gt = json.loads(ground_truth_path.read_text(encoding="utf-8"))

    complaint_map = {f["id"]: f["properties"] for f in geojson_data["features"]}
    suspicious = gt["suspicious_closures"]

    # 1. Mismatched GPS: exactly 2
    assert len(suspicious["mismatched_gps"]) == 2
    for item in suspicious["mismatched_gps"]:
        props = complaint_map[item["complaint_id"]]
        assert props["status"] == "closed"
        assert len(props["closure"]["media"]) >= 1
        closure_media_path = REPO_ROOT / props["closure"]["media"][0]["path"]
        exif_gps = extract_exif_gps(closure_media_path)
        assert exif_gps is not None
        dist = haversine_m(props["lat"], props["lon"], exif_gps[0], exif_gps[1])
        assert dist > 5000.0  # > 5 km

    # 2. Duplicate Evidence: exactly 2
    assert len(suspicious["duplicate_evidence"]) == 2
    dup_ids = [d["complaint_id"] for d in suspicious["duplicate_evidence"]]
    assert len(set(dup_ids)) == 2
    c1 = complaint_map[dup_ids[0]]
    c2 = complaint_map[dup_ids[1]]
    p1 = REPO_ROOT / c1["closure"]["media"][0]["path"]
    p2 = REPO_ROOT / c2["closure"]["media"][0]["path"]
    assert p1.name != p2.name  # Different filenames
    assert p1.read_bytes() == p2.read_bytes()  # Exact duplicate content

    # 3. Inconsistent Defect: exactly 1
    assert len(suspicious["inconsistent_defect"]) == 1
    inc_item = suspicious["inconsistent_defect"][0]
    c_inc = complaint_map[inc_item["complaint_id"]]
    assert c_inc["category"] == "streetlight"
    assert "media_13.jpg" in c_inc["closure"]["media"][0]["path"]
