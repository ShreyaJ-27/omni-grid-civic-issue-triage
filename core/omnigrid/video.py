"""Phase 5 Stretch: Video keyframe extraction and evidence metadata integration.

Supported video formats:
    - MP4 (.mp4)
    - QuickTime / MOV (.mov)
    - Audio Video Interleave (.avi)
    - Matroska (.mkv)

Container and metadata limitations:
    Unlike standalone digital camera JPEG photos, video container formats (e.g.
    MP4/MOV) typically store metadata in container-specific boxes/atoms (such as
    moov/trak/udta) rather than standard EXIF TIFF headers in individual frame
    payloads. Therefore, extracted keyframe images do not carry native EXIF GPS
    coordinates (extract_exif_gps will return None) unless geotags are supplied
    externally. Perceptual hashing (pHash) and visual analysis operate reliably
    on all extracted keyframe images.
"""

from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
from core.omnigrid.evidence import extract_evidence_meta
from core.omnigrid.schemas import VideoKeyframe

SUPPORTED_VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {".mp4", ".mov", ".avi", ".mkv"}
)

DEFAULT_KEYFRAME_DIR: Path = Path("data/synthetic/keyframes")
KEYFRAME_PERCENTAGES: tuple[float, float, float] = (0.15, 0.50, 0.85)


def _compute_keyframe_indices(total_frames: int) -> list[int]:
    """Calculate 3 representative frame indices across the duration."""
    if total_frames <= 0:
        return [0, 0, 0]
    if total_frames == 1:
        return [0, 0, 0]
    if total_frames == 2:
        return [0, 1, 1]

    idx_0 = int(round(total_frames * KEYFRAME_PERCENTAGES[0]))
    idx_1 = int(round(total_frames * KEYFRAME_PERCENTAGES[1]))
    idx_2 = int(round(total_frames * KEYFRAME_PERCENTAGES[2]))

    indices = [
        max(0, min(total_frames - 1, idx_0)),
        max(0, min(total_frames - 1, idx_1)),
        max(0, min(total_frames - 1, idx_2)),
    ]

    # Ensure strictly distinct frame indices when total_frames >= 3
    if len(set(indices)) < 3:
        indices = [0, total_frames // 2, total_frames - 1]

    return indices


def extract_keyframes_from_video(
    video_path: str | Path,
    num_keyframes: int = 3,
) -> list[tuple[int, float, np.ndarray]]:
    """Extract representative frames from a video file using OpenCV.

    Args:
        video_path: Path to the video file.
        num_keyframes: Number of keyframes to extract (default: 3).

    Returns:
        List of tuples: (frame_index, timestamp_sec, frame_bgr_ndarray).

    Raises:
        FileNotFoundError: If the video file does not exist.
        ValueError: If file is zero-byte, unsupported extension, or corrupted.
    """
    path = Path(video_path)

    # 1. Reject non-video extensions
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_VIDEO_EXTENSIONS:
        raise ValueError(
            f"Unsupported video format '{suffix}'. "
            f"Supported formats: {sorted(SUPPORTED_VIDEO_EXTENSIONS)}"
        )

    # 2. Check file existence and non-zero size
    if not path.is_file():
        raise FileNotFoundError(f"Video file not found: {path}")

    if path.stat().st_size == 0:
        raise ValueError(f"Video file is empty (0 bytes): {path}")

    # 3. Open video stream
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(
            f"Failed to open video file (corrupted or invalid format): {path.name}"
        )

    try:
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        fps = fps if (fps and fps > 0 and math.isfinite(fps)) else 30.0

        # Handle streams or codecs reporting invalid frame counts
        if total_frames <= 0:
            frames: list[np.ndarray] = []
            while True:
                ret, frame = cap.read()
                if not ret or frame is None:
                    break
                frames.append(frame)

            if not frames:
                raise ValueError(
                    f"Corrupted video file: no readable frames in {path.name}"
                )

            total_frames = len(frames)
            target_indices = _compute_keyframe_indices(total_frames)

            results: list[tuple[int, float, np.ndarray]] = []
            for idx in target_indices[:num_keyframes]:
                ts = round(idx / fps, 3)
                results.append((idx, ts, frames[idx]))
            return results

        # Seek and extract target keyframes
        target_indices = _compute_keyframe_indices(total_frames)[:num_keyframes]
        results = []

        for idx in target_indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, float(idx))
            ret, frame = cap.read()
            if not ret or frame is None:
                # Fallback: try sequential read from beginning if seek fails
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0.0)
                cur_idx = 0
                found_frame: np.ndarray | None = None
                while cur_idx <= idx:
                    ret_seq, f_seq = cap.read()
                    if not ret_seq or f_seq is None:
                        break
                    if cur_idx == idx:
                        found_frame = f_seq
                    cur_idx += 1

                if found_frame is None:
                    raise ValueError(
                        f"Failed to read frame at index {idx} in {path.name}"
                    )
                frame = found_frame

            pos_msec = cap.get(cv2.CAP_PROP_POS_MSEC)
            timestamp_sec = (
                round(pos_msec / 1000.0, 3)
                if pos_msec and pos_msec > 0
                else round(idx / fps, 3)
            )
            results.append((idx, timestamp_sec, frame))

        return results
    finally:
        cap.release()


def extract_video_keyframes(
    complaint_id: str,
    video_path: str | Path,
    complaint_coords: tuple[float, float] | None = None,
    output_dir: str | Path | None = None,
    raise_on_error: bool = True,
) -> list[VideoKeyframe]:
    """Extract exactly 3 keyframes, write to disk, and return VideoKeyframe records.

    Keyframes are saved to: `<output_dir>/<video_stem>/frame_<idx>.jpg`.

    Args:
        complaint_id: The ID of the associated complaint.
        video_path: File path to the video file (.mp4, .mov, .avi, .mkv).
        complaint_coords: Optional (lat, lon) coordinates of the complaint.
        output_dir: Optional root directory for keyframe images.
        raise_on_error: Whether to raise ValueError on error or return [].

    Returns:
        List of VideoKeyframe schema instances (up to 3).

    Raises:
        ValueError: If video is corrupted, empty, or unsupported format.
        FileNotFoundError: If video file does not exist.
    """
    path = Path(video_path)

    try:
        raw_keyframes = extract_keyframes_from_video(path, num_keyframes=3)
    except (ValueError, FileNotFoundError):
        if raise_on_error:
            raise
        return []
    except Exception as exc:
        if raise_on_error:
            raise ValueError(
                f"Failed to process video file {path.name}: {exc}"
            ) from exc
        return []

    root_out = Path(output_dir) if output_dir is not None else DEFAULT_KEYFRAME_DIR
    target_dir = root_out / path.stem
    target_dir.mkdir(parents=True, exist_ok=True)

    records: list[VideoKeyframe] = []

    for i, (frame_idx, ts_sec, frame) in enumerate(raw_keyframes):
        keyframe_path = target_dir / f"frame_{i}.jpg"
        # Save as standard JPEG
        cv2.imwrite(str(keyframe_path), frame)

        # Feed extracted keyframe into extract_evidence_meta
        meta = extract_evidence_meta(
            complaint_id=complaint_id,
            media_path=keyframe_path,
            complaint_coords=complaint_coords,
        )

        record = VideoKeyframe(
            complaint_id=complaint_id,
            video_path=str(path).replace("\\", "/"),
            frame_index=frame_idx,
            timestamp_sec=ts_sec,
            keyframe_path=str(keyframe_path).replace("\\", "/"),
            evidence_meta=meta,
        )
        records.append(record)

    return records


# Backward-compatible convenience aliases
VideoEvidenceMeta = VideoKeyframe
