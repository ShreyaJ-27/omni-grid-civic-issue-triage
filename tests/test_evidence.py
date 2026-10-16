"""Comprehensive unit and integration tests for Phase 5: Evidence extraction."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from core.omnigrid.evidence import (
    compute_distance,
    compute_phash,
    dms_rational_to_decimal,
    extract_complaint_evidence,
    extract_evidence_meta,
    extract_exif_gps,
    extract_exif_timestamp,
    is_within_geofence,
    phash_distance,
)
from core.omnigrid.schemas import EvidenceMeta
from PIL import Image
from scripts.generate_sample_media import (
    generate_sample_media,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MEDIA_DIR = REPO_ROOT / "data" / "synthetic" / "media"
COMPLAINTS_GEOJSON = REPO_ROOT / "data" / "synthetic" / "complaints.geojson"


@pytest.fixture(scope="module", autouse=True)
def ensure_sample_media() -> None:
    """Ensure synthetic media files exist before running evidence tests."""
    if not (MEDIA_DIR / "media_15.jpg").is_file():
        generate_sample_media(MEDIA_DIR)


# -----------------------------------------------------------------------------
# 1. EXIF GPS Extraction Tests
# -----------------------------------------------------------------------------


def test_exif_gps_extraction_silk_board_media_01() -> None:
    """Verify media_01.jpg GPS coordinates match Silk Board specifications."""
    p1 = MEDIA_DIR / "media_01.jpg"
    assert p1.is_file()

    gps = extract_exif_gps(p1)
    assert gps is not None
    lat, lon = gps
    assert lat == pytest.approx(12.9172, abs=1e-4)
    assert lon == pytest.approx(77.6228, abs=1e-4)


def test_exif_gps_extraction_indiranagar_media_02() -> None:
    """Verify media_02.jpg GPS coordinates match Indiranagar specifications."""
    p2 = MEDIA_DIR / "media_02.jpg"
    gps = extract_exif_gps(p2)
    assert gps is not None
    lat, lon = gps
    assert lat == pytest.approx(12.9784, abs=1e-4)
    assert lon == pytest.approx(77.6408, abs=1e-4)


def test_exif_gps_extraction_jayanagar_media_04() -> None:
    """Verify media_04.jpg GPS coordinates match Jayanagar specifications."""
    p4 = MEDIA_DIR / "media_04.jpg"
    gps = extract_exif_gps(p4)
    assert gps is not None
    lat, lon = gps
    assert lat == pytest.approx(12.9304, abs=1e-4)
    assert lon == pytest.approx(77.5833, abs=1e-4)


def test_dms_rational_to_decimal_conversion() -> None:
    """Test DMS rational conversion handles North, South, East, West correctly."""
    # 12 deg, 55 min, 1.92 sec N -> 12.9172
    dms_lat = ((12, 1), (55, 1), (19200, 10000))
    lat_n = dms_rational_to_decimal(dms_lat, "N")
    assert lat_n == pytest.approx(12.9172, abs=1e-4)

    # South must be negative
    lat_s = dms_rational_to_decimal(dms_lat, "S")
    assert lat_s == pytest.approx(-12.9172, abs=1e-4)

    # West must be negative
    dms_lon = ((77, 1), (37, 1), (220800, 10000))
    lon_w = dms_rational_to_decimal(dms_lon, "W")
    assert lon_w == pytest.approx(-77.6228, abs=1e-4)

    # Malformed inputs
    assert dms_rational_to_decimal([], "N") is None
    assert dms_rational_to_decimal(((1, 0), (1, 1), (1, 1)), "N") is not None


# -----------------------------------------------------------------------------
# 2. Timestamp Extraction & UTC Normalization Tests
# -----------------------------------------------------------------------------


def test_exif_timestamp_extraction_and_utc_normalization() -> None:
    """Verify timestamp extraction normalizes to timezone-aware UTC datetime."""
    p1 = MEDIA_DIR / "media_01.jpg"
    dt = extract_exif_timestamp(p1)

    assert dt is not None
    assert isinstance(dt, datetime)
    assert dt.tzinfo == UTC
    assert dt.year == 2026
    assert dt.month == 10
    assert dt.day == 14


# -----------------------------------------------------------------------------
# 3. Perceptual Hashing & Hamming Distance Tests
# -----------------------------------------------------------------------------


def test_phash_generation_hex_string() -> None:
    """Verify pHash returns a 16-character hexadecimal string."""
    p1 = MEDIA_DIR / "media_01.jpg"
    h = compute_phash(p1)

    assert isinstance(h, str)
    assert len(h) == 16
    int(h, 16)  # Validates valid hexadecimal


def test_phash_duplicate_images_zero_distance() -> None:
    """Exact duplicate evidence media_14 and media_15 must have 0 Hamming distance."""
    p14 = MEDIA_DIR / "media_14.jpg"
    p15 = MEDIA_DIR / "media_15.jpg"

    h14 = compute_phash(p14)
    h15 = compute_phash(p15)

    assert h14 == h15
    dist = phash_distance(h14, h15)
    assert dist == 0


def test_phash_different_images_non_zero_distance() -> None:
    """Distinct images should have positive Hamming distance."""
    p1 = MEDIA_DIR / "media_01.jpg"
    p2 = MEDIA_DIR / "media_02.jpg"

    h1 = compute_phash(p1)
    h2 = compute_phash(p2)

    assert phash_distance(h1, h2) > 0


# -----------------------------------------------------------------------------
# 4. Geofencing & Distance Verification Tests
# -----------------------------------------------------------------------------


def test_distance_calculation_known_landmarks() -> None:
    """Distance between Silk Board and Indiranagar must be ~7.09 km."""
    silk_board = (12.9172, 77.6228)
    indiranagar = (12.9784, 77.6408)

    dist = compute_distance(silk_board, indiranagar)
    assert dist is not None
    assert 7000.0 < dist < 7200.0


def test_geofence_verification_valid_repair_within_threshold() -> None:
    """media_06.jpg repair was taken ~15m from Silk Board complaint (<= 50m)."""
    p6 = MEDIA_DIR / "media_06.jpg"
    silk_board = (12.9172, 77.6228)

    meta = extract_evidence_meta("complaint-001", p6, complaint_coords=silk_board)
    assert meta.distance_m is not None
    assert meta.distance_m < 25.0
    assert is_within_geofence(meta.distance_m) is True
    assert meta.is_within_geofence is True


def test_geofence_verification_offset_repair_outside_threshold() -> None:
    """media_11.jpg has GPS at Yelahanka, >23 km from Silk Board (> 50m)."""
    p11 = MEDIA_DIR / "media_11.jpg"
    silk_board = (12.9172, 77.6228)

    meta = extract_evidence_meta("complaint-003", p11, complaint_coords=silk_board)
    assert meta.distance_m is not None
    assert meta.distance_m > 20000.0  # > 20 km
    assert is_within_geofence(meta.distance_m) is False
    assert meta.is_within_geofence is False


def test_geofence_configurable_setting_override() -> None:
    """Verify geofence distance respects custom threshold parameter."""
    dist = 45.0  # within default 50m

    # Default settings (50m)
    assert is_within_geofence(dist) is True

    # Tightened geofence (30m) -> False
    assert is_within_geofence(dist, geofence_meters=30.0) is False

    # Loosened geofence (100m) -> True
    assert is_within_geofence(dist, geofence_meters=100.0) is True


def test_geofence_none_distance_returns_false() -> None:
    """If distance cannot be computed, geofence check must return False."""
    assert is_within_geofence(None) is False


# -----------------------------------------------------------------------------
# 5. Error Handling & Edge Cases
# -----------------------------------------------------------------------------


def test_image_with_stripped_exif(tmp_path: Path) -> None:
    """Images lacking EXIF metadata must parse safely with None GPS and timestamp."""
    clean_img_path = tmp_path / "stripped.jpg"
    img = Image.new("RGB", (64, 64), color="blue")
    img.save(clean_img_path, format="JPEG")

    meta = extract_evidence_meta(
        complaint_id="c-stripped",
        media_path=clean_img_path,
        complaint_coords=(12.9, 77.6),
    )

    assert meta.complaint_id == "c-stripped"
    assert meta.gps is None
    assert meta.taken_at is None
    assert meta.distance_m is None
    assert isinstance(meta.phash, str)
    assert len(meta.phash) == 16
    assert meta.is_within_geofence is False


def test_corrupted_image_raises_value_error(tmp_path: Path) -> None:
    """Corrupted files that cannot be decoded must raise ValueError."""
    bad_file = tmp_path / "corrupted.jpg"
    bad_file.write_bytes(b"not a valid jpeg or png header byte content")

    with pytest.raises(ValueError, match="Corrupted or unsupported"):
        extract_evidence_meta("c-bad", bad_file)


def test_unsupported_video_formats_raise_value_error(tmp_path: Path) -> None:
    """Video files (.mp4, .mov, .avi) must raise a descriptive ValueError."""
    for ext in (".mp4", ".mov", ".avi", ".mkv"):
        vid_file = tmp_path / f"test{ext}"
        vid_file.write_bytes(b"\x00\x00\x00\x18ftypmp42")

        with pytest.raises(ValueError, match="not supported until keyframe extraction"):
            extract_evidence_meta("c-vid", vid_file)


def test_nonexistent_file_raises_file_not_found(tmp_path: Path) -> None:
    """Missing file paths must raise FileNotFoundError."""
    missing = tmp_path / "does_not_exist.jpg"
    with pytest.raises(FileNotFoundError):
        extract_evidence_meta("c-missing", missing)


# -----------------------------------------------------------------------------
# 6. Schema Compliance & Bulk Ingestion Tests
# -----------------------------------------------------------------------------


def test_evidence_meta_schema_compliance() -> None:
    """Ensure returned objects conform strictly to EvidenceMeta schema."""
    p1 = MEDIA_DIR / "media_01.jpg"
    meta = extract_evidence_meta(
        "c-schema-test",
        p1,
        complaint_coords=(12.9172, 77.6228),
    )

    assert isinstance(meta, EvidenceMeta)

    dumped = meta.model_dump()
    reconstructed = EvidenceMeta.model_validate(dumped)
    assert reconstructed == meta


def test_extract_complaint_evidence_bulk() -> None:
    """Extract evidence for a complaint with media attachments."""
    complaint_dict = {
        "id": "complaint-001",
        "lat": 12.9172,
        "lon": 77.6228,
        "media": [{"path": "data/synthetic/media/media_01.jpg", "type": "image"}],
        "closure": {
            "closed_at": "2026-10-14T12:00:00Z",
            "media": [
                {"path": "data/synthetic/media/media_06.jpg", "type": "image"}
            ],
            "closed_by": "crew",
        },
    }

    records = extract_complaint_evidence(complaint_dict, base_dir=REPO_ROOT)
    assert len(records) == 2

    # Open evidence
    assert records[0].complaint_id == "complaint-001"
    assert records[0].distance_m is not None
    assert records[0].distance_m < 5.0  # At complaint point

    # Closure repair evidence
    assert records[1].complaint_id == "complaint-001"
    assert records[1].distance_m is not None
    assert records[1].distance_m < 25.0
    assert records[1].is_within_geofence is True
