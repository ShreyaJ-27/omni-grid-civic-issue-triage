"""Phase 5: Evidence extraction - EXIF GPS, pHash, geofence verification."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import imagehash
import piexif
from core.omnigrid.config import get_settings
from core.omnigrid.schemas import Complaint, EvidenceMeta
from PIL import Image, UnidentifiedImageError

EARTH_RADIUS_METERS: float = 6_371_000.0

VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {".mp4", ".mov", ".avi", ".mkv", ".webm", ".wmv", ".flv", ".m4v"}
)

IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif"}
)


def _rational_to_float(val: Any) -> float:
    """Convert an EXIF rational tuple (numerator, denominator) or number to float."""
    if isinstance(val, (int, float)):
        return float(val)
    if hasattr(val, "numerator") and hasattr(val, "denominator"):
        num, den = val.numerator, val.denominator
        return float(num) / float(den) if den != 0 else 0.0
    if isinstance(val, (tuple, list)) and len(val) >= 2:
        num, den = val[0], val[1]
        return float(num) / float(den) if den != 0 else 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def dms_rational_to_decimal(
    dms: Any,
    ref: str | bytes,
) -> float | None:
    """Convert EXIF DMS rational coordinates to signed decimal degrees."""
    if not isinstance(dms, (tuple, list)) or len(dms) < 3:
        return None

    try:
        deg = _rational_to_float(dms[0])
        minutes = _rational_to_float(dms[1])
        seconds = _rational_to_float(dms[2])

        decimal = deg + (minutes / 60.0) + (seconds / 3600.0)
        ref_str = (
            ref.decode("ascii", errors="ignore")
            if isinstance(ref, bytes)
            else str(ref)
        ).strip().upper()

        if ref_str in ("S", "W"):
            decimal = -decimal

        return round(decimal, 6)
    except Exception:
        return None


def extract_exif_gps(
    image_input: str | Path | Image.Image,
) -> tuple[float, float] | None:
    """Extract GPS (latitude, longitude) decimal coordinates from image EXIF.


    Returns None if EXIF GPS data is missing, incomplete, or malformed.
    """
    try:
        gps_dict: dict[Any, Any] = {}

        if isinstance(image_input, (str, Path)):
            path_str = str(image_input)
            try:
                exif_data = piexif.load(path_str)
                gps_dict = exif_data.get("GPS", {})
            except Exception:
                # Fallback to Pillow
                with Image.open(path_str) as img:
                    exif = img.getexif()
                    if exif:
                        gps_ifd = exif.get_ifd(0x8825)
                        gps_dict = dict(gps_ifd) if gps_ifd else {}
        elif isinstance(image_input, Image.Image):
            exif = image_input.getexif()
            if exif:
                gps_ifd = exif.get_ifd(0x8825)
                gps_dict = dict(gps_ifd) if gps_ifd else {}

        if not gps_dict:
            return None

        # GPSLatitude tag: 2, GPSLatitudeRef tag: 1
        # GPSLongitude tag: 4, GPSLongitudeRef tag: 3
        lat_raw = gps_dict.get(piexif.GPSIFD.GPSLatitude) or gps_dict.get(2)
        lat_ref = gps_dict.get(piexif.GPSIFD.GPSLatitudeRef) or gps_dict.get(1, "N")
        lon_raw = gps_dict.get(piexif.GPSIFD.GPSLongitude) or gps_dict.get(4)
        lon_ref = gps_dict.get(piexif.GPSIFD.GPSLongitudeRef) or gps_dict.get(3, "E")

        if lat_raw is None or lon_raw is None:
            return None

        lat = dms_rational_to_decimal(lat_raw, lat_ref)
        lon = dms_rational_to_decimal(lon_raw, lon_ref)

        if lat is None or lon is None:
            return None

        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            return None

        return (lat, lon)
    except Exception:
        return None


def extract_exif_timestamp(
    image_input: str | Path | Image.Image,
) -> datetime | None:
    """Extract image capture timestamp from EXIF and normalize to UTC datetime."""
    try:
        raw_ts: Any = None

        if isinstance(image_input, (str, Path)):
            path_str = str(image_input)
            try:
                exif_data = piexif.load(path_str)
                exif_ifd = exif_data.get("Exif", {})
                zeroth_ifd = exif_data.get("0th", {})
                raw_ts = (
                    exif_ifd.get(piexif.ExifIFD.DateTimeOriginal)
                    or exif_ifd.get(piexif.ExifIFD.DateTimeDigitized)
                    or zeroth_ifd.get(piexif.ImageIFD.DateTime)
                )
            except Exception:
                with Image.open(path_str) as img:
                    exif = img.getexif()
                    if exif:
                        exif_sub = exif.get_ifd(0x8769)
                        raw_ts = (
                            exif_sub.get(0x9003)
                            or exif_sub.get(0x9004)
                            or exif.get(0x0132)
                        )
        elif isinstance(image_input, Image.Image):
            exif = image_input.getexif()
            if exif:
                exif_sub = exif.get_ifd(0x8769)
                raw_ts = (
                    exif_sub.get(0x9003)
                    or exif_sub.get(0x9004)
                    or exif.get(0x0132)
                )

        if not raw_ts:
            return None

        if isinstance(raw_ts, bytes):
            ts_str = raw_ts.decode("ascii", errors="ignore").strip().rstrip("\x00")
        else:
            ts_str = str(raw_ts).strip().rstrip("\x00")

        if not ts_str:
            return None

        # Standard EXIF format: "YYYY:MM:DD HH:MM:SS"
        # Or ISO-8601 variations
        parsed: datetime | None = None
        for fmt in (
            "%Y:%m:%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y:%m:%d %H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S%z",
        ):
            try:
                parsed = datetime.strptime(ts_str, fmt)
                break
            except ValueError:
                continue

        if parsed is None:
            try:
                parsed = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except ValueError:
                return None

        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except Exception:
        return None


def compute_phash(image_input: str | Path | Image.Image) -> str:
    """Compute 16-hex-character perceptual hash (pHash) using Pillow and imagehash."""
    if isinstance(image_input, (str, Path)):
        with Image.open(image_input) as img:
            h = imagehash.phash(img)
            return str(h).lower()
    elif isinstance(image_input, Image.Image):
        h = imagehash.phash(image_input)
        return str(h).lower()
    raise ValueError(f"Unsupported image input type: {type(image_input)}")


def phash_distance(hash1: str, hash2: str) -> int:
    """Compute Hamming distance between two perceptual hash hex strings."""
    h1 = imagehash.hex_to_hash(hash1)
    h2 = imagehash.hex_to_hash(hash2)
    return int(h1 - h2)


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute the Great Circle distance in meters between two coordinates."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    a = min(1.0, max(0.0, a))
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_METERS * c


def compute_distance(
    coords1: tuple[float, float] | None,
    coords2: tuple[float, float] | None,
) -> float | None:
    """Calculate Haversine distance in meters between two coordinates, or None."""
    if coords1 is None or coords2 is None:
        return None
    dist = haversine_distance(coords1[0], coords1[1], coords2[0], coords2[1])
    return round(dist, 2)


def is_within_geofence(
    distance_m: float | None,
    geofence_meters: float | None = None,
) -> bool:
    """Determine whether a distance falls within the geofence threshold."""
    if distance_m is None:
        return False
    threshold = (
        float(geofence_meters)
        if geofence_meters is not None
        else get_settings().GEOFENCE_METERS
    )
    return distance_m <= threshold


def check_geofence(
    evidence: EvidenceMeta,
    geofence_meters: float | None = None,
) -> bool:
    """Check if EvidenceMeta is within geofence."""
    return is_within_geofence(evidence.distance_m, geofence_meters)


def extract_evidence_meta(
    complaint_id: str,
    media_path: str | Path,
    complaint_coords: tuple[float, float] | None = None,
) -> EvidenceMeta:
    """Extract EXIF GPS, capture timestamp, and pHash into an EvidenceMeta record.

    Args:
        complaint_id: The ID of the associated complaint.
        media_path: File system path to the image file.
        complaint_coords: Optional (lat, lon) of the complaint for distance calculation.

    Returns:
        Validated EvidenceMeta instance.

    Raises:
        ValueError: If media_path is a video format or corrupted image file.
        FileNotFoundError: If the media file does not exist.
    """
    path = Path(media_path)

    # 1. Check for video formats
    suffix = path.suffix.lower()
    if suffix in VIDEO_EXTENSIONS:
        raise ValueError(
            f"Video format '{suffix}' is not supported until keyframe extraction "
            f"is implemented: {path.name}"
        )

    # 2. Check file existence
    if not path.is_file():
        raise FileNotFoundError(f"Media file not found: {path}")

    # 3. Open image to verify readability and compute pHash
    try:
        with Image.open(path) as img:
            phash = str(imagehash.phash(img)).lower()
    except UnidentifiedImageError as exc:
        raise ValueError(
            f"Corrupted or unsupported image file: {path.name}"
        ) from exc
    except Exception as exc:
        raise ValueError(
            f"Failed to read image file: {path.name}"
        ) from exc

    # 4. Extract EXIF GPS and timestamp
    gps = extract_exif_gps(path)
    taken_at = extract_exif_timestamp(path)

    # 5. Compute distance if both GPS and complaint coordinates exist
    distance_m = compute_distance(gps, complaint_coords)

    # 6. Construct validated EvidenceMeta
    return EvidenceMeta(
        complaint_id=complaint_id,
        path=str(path).replace("\\", "/"),
        gps=gps,
        taken_at=taken_at,
        phash=phash,
        distance_m=distance_m,
    )


def extract_complaint_evidence(
    complaint: Complaint | dict[str, Any],
    base_dir: Path | str | None = None,
    keyframe_dir: Path | str | None = None,
) -> list[EvidenceMeta]:
    """Extract evidence records for all media in complaint and closure."""
    results: list[EvidenceMeta] = []

    if isinstance(complaint, Complaint):
        cid = complaint.id
        coords = (complaint.lat, complaint.lon)
        media_items = list(complaint.media)
        if complaint.closure and complaint.closure.media:
            media_items.extend(complaint.closure.media)
    elif isinstance(complaint, dict):
        cid = str(complaint.get("id", ""))
        lat = complaint.get("lat")
        lon = complaint.get("lon")
        coords = (
            (float(lat), float(lon))
            if lat is not None and lon is not None
            else None
        )
        media_items = list(complaint.get("media", []))
        closure = complaint.get("closure")
        if isinstance(closure, dict) and "media" in closure:
            media_items.extend(closure.get("media", []))
    else:
        return results

    root_dir = Path(base_dir) if base_dir is not None else Path.cwd()

    for item in media_items:
        mpath = item.path if hasattr(item, "path") else item.get("path")
        if not mpath:
            continue

        resolved_path = Path(mpath)
        if not resolved_path.is_absolute():
            resolved_path = root_dir / resolved_path

        if resolved_path.suffix.lower() in VIDEO_EXTENSIONS:
            try:
                from core.omnigrid.video import extract_video_keyframes

                keyframes = extract_video_keyframes(
                    complaint_id=cid,
                    video_path=resolved_path,
                    complaint_coords=coords,
                    output_dir=keyframe_dir,
                    raise_on_error=False,
                )
                results.extend(kf.evidence_meta for kf in keyframes)
            except Exception:
                continue
            continue


        try:
            meta = extract_evidence_meta(
                complaint_id=cid,
                media_path=resolved_path,
                complaint_coords=coords,
            )
            results.append(meta)
        except ValueError:
            # Skip unreadable or corrupted files during bulk extraction
            continue


    return results
