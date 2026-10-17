"""Comprehensive unit and integration tests for Phase 5 stretch: Video keyframes."""

from __future__ import annotations

import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest
from core.omnigrid.evidence import extract_complaint_evidence
from core.omnigrid.schemas import EvidenceMeta, VideoKeyframe
from core.omnigrid.video import (
    extract_keyframes_from_video,
    extract_video_keyframes,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MEDIA_DIR = REPO_ROOT / "data" / "synthetic" / "media"


def _generate_synthetic_video(
    output_path: Path,
    num_frames: int = 30,
    fps: float = 30.0,
    width: int = 64,
    height: int = 64,
) -> Path:
    """Generate a lightweight synthetic MP4 video with varied frame contents."""
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(output_path), fourcc, fps, (width, height))

    for i in range(num_frames):
        # Vary color and intensity across frames
        color_val = int((i / max(1, num_frames - 1)) * 255)
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:, :] = (color_val, 255 - color_val, (color_val * 2) % 256)
        # Add a moving circle
        cx = int((i / max(1, num_frames - 1)) * (width - 10)) + 5
        cv2.circle(frame, (cx, height // 2), 6, (255, 255, 255), -1)
        writer.write(frame)

    writer.release()
    return output_path


# -----------------------------------------------------------------------------
# 1. Keyframe Extraction Tests
# -----------------------------------------------------------------------------


def test_extract_three_keyframes_written_to_disk(tmp_path: Path) -> None:
    """Exactly three keyframes must be extracted and saved as JPEGs."""
    vid_path = _generate_synthetic_video(tmp_path / "sample_traffic.mp4")
    out_dir = tmp_path / "custom_keyframes"

    keyframes = extract_video_keyframes(
        complaint_id="complaint-vid-01",
        video_path=vid_path,
        complaint_coords=(12.9172, 77.6228),
        output_dir=out_dir,
    )

    assert len(keyframes) == 3

    # Validate output paths and file existence
    expected_dir = out_dir / "sample_traffic"
    for i, kf in enumerate(keyframes):
        assert isinstance(kf, VideoKeyframe)
        assert kf.complaint_id == "complaint-vid-01"
        assert kf.frame_index >= 0
        assert kf.timestamp_sec >= 0.0

        disk_path = Path(kf.keyframe_path)
        assert disk_path.is_file()
        assert disk_path.parent == expected_dir
        assert disk_path.name == f"frame_{i}.jpg"
        assert disk_path.stat().st_size > 0


def test_keyframe_timestamps_in_ascending_order(tmp_path: Path) -> None:
    """Extracted keyframes should cover progression across video duration."""
    vid_path = _generate_synthetic_video(tmp_path / "temporal.mp4", num_frames=60)
    keyframes = extract_video_keyframes(
        complaint_id="c-time",
        video_path=vid_path,
        output_dir=tmp_path / "kf",
    )

    assert len(keyframes) == 3
    t0, t1, t2 = (kf.timestamp_sec for kf in keyframes)
    assert t0 < t1 < t2


def test_raw_keyframes_from_video_helper(tmp_path: Path) -> None:
    """Test lower-level OpenCV frame extraction helper."""
    vid_path = _generate_synthetic_video(tmp_path / "raw.mp4", num_frames=20)
    raw = extract_keyframes_from_video(vid_path, num_keyframes=3)

    assert len(raw) == 3
    for frame_idx, ts, frame_arr in raw:
        assert isinstance(frame_idx, int)
        assert isinstance(ts, float)
        assert isinstance(frame_arr, np.ndarray)
        assert frame_arr.shape == (64, 64, 3)


# -----------------------------------------------------------------------------
# 2. Perceptual Hashing & Evidence Integration Tests
# -----------------------------------------------------------------------------


def test_keyframe_produces_valid_phash(tmp_path: Path) -> None:
    """Each keyframe must produce a valid 16-hex perceptual hash."""
    vid_path = _generate_synthetic_video(tmp_path / "phash_vid.mp4")
    keyframes = extract_video_keyframes(
        complaint_id="c-phash",
        video_path=vid_path,
        output_dir=tmp_path / "kf_phash",
    )

    for kf in keyframes:
        meta = kf.evidence_meta
        assert isinstance(meta, EvidenceMeta)
        assert isinstance(meta.phash, str)
        assert len(meta.phash) == 16
        # Property accessor convenience
        assert kf.phash == meta.phash
        int(meta.phash, 16)  # Must be valid hex


def test_duplicate_videos_produce_matching_keyframes(tmp_path: Path) -> None:
    """Identical videos must produce identical keyframe hashes."""
    vid1 = _generate_synthetic_video(tmp_path / "v1.mp4", num_frames=15)
    vid2 = tmp_path / "v2.mp4"
    vid2.write_bytes(vid1.read_bytes())

    kf1 = extract_video_keyframes("c1", vid1, output_dir=tmp_path / "k1")
    kf2 = extract_video_keyframes("c2", vid2, output_dir=tmp_path / "k2")

    for k_a, k_b in zip(kf1, kf2, strict=True):
        assert k_a.phash == k_b.phash


# -----------------------------------------------------------------------------
# 3. Error Handling & Edge Cases
# -----------------------------------------------------------------------------


def test_zero_byte_video_raises_value_error(tmp_path: Path) -> None:
    """Empty 0-byte video files must raise descriptive ValueError."""
    empty_vid = tmp_path / "empty.mp4"
    empty_vid.write_bytes(b"")

    with pytest.raises(ValueError, match="empty"):
        extract_video_keyframes("c-empty", empty_vid)


def test_corrupted_video_raises_value_error(tmp_path: Path) -> None:
    """Corrupted video header/payload must raise descriptive ValueError."""
    corrupt_vid = tmp_path / "corrupt.mp4"
    corrupt_vid.write_bytes(b"corrupted header bytes that cannot be decoded")

    with pytest.raises(ValueError, match="Failed to open video file"):
        extract_video_keyframes("c-corrupt", corrupt_vid)


def test_nonexistent_video_raises_file_not_found(tmp_path: Path) -> None:
    """Non-existent video path must raise FileNotFoundError."""
    missing = tmp_path / "missing_video.mp4"
    with pytest.raises(FileNotFoundError):
        extract_video_keyframes("c-missing", missing)


def test_unsupported_extensions_raise_value_error(tmp_path: Path) -> None:
    """Non-video files must be rejected with explicit ValueError."""
    for ext in (".txt", ".jpg", ".pdf", ".mp3"):
        bad_file = tmp_path / f"test{ext}"
        bad_file.write_text("dummy")

        with pytest.raises(ValueError, match="Unsupported video format"):
            extract_video_keyframes("c-ext", bad_file)


def test_raise_on_error_false_returns_empty_list(tmp_path: Path) -> None:
    """When raise_on_error is False, errors should return [] safely."""
    empty_vid = tmp_path / "empty.mp4"
    empty_vid.write_bytes(b"")

    res = extract_video_keyframes("c-safe", empty_vid, raise_on_error=False)
    assert res == []


# -----------------------------------------------------------------------------
# 4. Bulk Complaint Extraction & Schema Compliance
# -----------------------------------------------------------------------------


def test_bulk_complaint_extraction_with_video(tmp_path: Path) -> None:
    """Bulk complaint processing extracts keyframes from attached videos."""
    vid_path = _generate_synthetic_video(tmp_path / "citizen_fix.mp4")
    shutil.copy2(MEDIA_DIR / "media_01.jpg", tmp_path / "media_01.jpg")

    complaint_dict = {
        "id": "complaint-vid-bulk",
        "lat": 12.9172,
        "lon": 77.6228,
        "media": [
            {"path": "media_01.jpg", "type": "image"},
            {"path": str(vid_path), "type": "video"},
        ],
    }

    evidence_records = extract_complaint_evidence(
        complaint_dict,
        base_dir=tmp_path,
        keyframe_dir=tmp_path / "keyframes",
    )


    # 1 image + 3 video keyframes = 4 total EvidenceMeta records
    assert len(evidence_records) == 4

    for rec in evidence_records:
        assert isinstance(rec, EvidenceMeta)
        assert rec.complaint_id == "complaint-vid-bulk"
        assert len(rec.phash) == 16


def test_bulk_complaint_extraction_corrupted_video_does_not_crash(
    tmp_path: Path,
) -> None:
    """Corrupted videos in bulk extraction must be skipped without aborting."""
    bad_vid = tmp_path / "broken.mp4"
    bad_vid.write_bytes(b"junk")

    complaint_dict = {
        "id": "complaint-vid-resilience",
        "lat": 12.9172,
        "lon": 77.6228,
        "media": [
            {"path": "data/synthetic/media/media_01.jpg", "type": "image"},
            {"path": str(bad_vid), "type": "video"},
        ],
    }

    # Should safely process the valid image and skip the broken video
    records = extract_complaint_evidence(complaint_dict, base_dir=REPO_ROOT)
    assert len(records) == 1
    assert records[0].complaint_id == "complaint-vid-resilience"


def test_video_keyframe_schema_round_trip(tmp_path: Path) -> None:
    """Ensure VideoKeyframe serializes and deserializes properly."""
    vid_path = _generate_synthetic_video(tmp_path / "schema_test.mp4")
    keyframes = extract_video_keyframes(
        "c-schema",
        vid_path,
        output_dir=tmp_path / "kf_schema",
    )

    kf = keyframes[0]
    data = kf.model_dump()
    reconstructed = VideoKeyframe.model_validate(data)
    assert reconstructed == kf
    assert reconstructed.source_video_path == kf.video_path
