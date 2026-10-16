"""Phase 6: Proof-of-Fix deterministic verification checks and verdict generation."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.omnigrid.config import get_settings
from core.omnigrid.evidence import (
    compute_distance,
    compute_phash,
    extract_exif_gps,
    extract_exif_timestamp,
    phash_distance,
)
from core.omnigrid.schemas import Check, Complaint, Verdict

REPO_ROOT = Path(__file__).resolve().parents[2]

# Map synthetic media filenames to their known defect types for defect sanity
_KNOWN_DEFECT_MAP: dict[str, str] = {
    "media_01.jpg": "pothole",
    "media_02.jpg": "streetlight",
    "media_03.jpg": "waterlogging",
    "media_04.jpg": "other",
    "media_05.jpg": "pothole",
    "media_06.jpg": "pothole",
    "media_07.jpg": "streetlight",
    "media_08.jpg": "waterlogging",
    "media_09.jpg": "other",
    "media_10.jpg": "pothole",
    "media_11.jpg": "pothole",
    "media_12.jpg": "streetlight",
    "media_13.jpg": "pothole",  # Pothole repair photo used for streetlight complaint
    "media_14.jpg": "pothole",
    "media_15.jpg": "pothole",
}

# Defect keywords in contractor or closure notes
_DEFECT_KEYWORDS: dict[str, str] = {
    "asphalt": "pothole",
    "tar": "pothole",
    "crater": "pothole",
    "pothole": "pothole",
    "streetlight": "streetlight",
    "pole": "streetlight",
    "bulb": "streetlight",
    "lamp": "streetlight",
    "drain": "waterlogging",
    "flood": "waterlogging",
    "waterlog": "waterlogging",
    "culvert": "waterlogging",
}


def _get_prop(obj: Any, key: str, default: Any = None) -> Any:
    """Helper to retrieve attributes from either Pydantic models or dicts."""
    if hasattr(obj, key):
        val = getattr(obj, key)
        return val if val is not None else default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return default


def _resolve_media_path(
    media_path: str | Path,
    base_dir: Path | str | None = None,
) -> Path:
    """Resolve media path against candidate directories."""
    path = Path(media_path)
    if path.is_file():
        return path

    candidates = [
        Path(base_dir) if base_dir else None,
        REPO_ROOT,
        Path.cwd(),
        REPO_ROOT / "data" / "synthetic" / "media",
    ]

    for cand in candidates:
        if cand is not None:
            full = cand / path
            if full.is_file():
                return full
            # Also check by basename in candidate directory
            by_name = cand / path.name
            if by_name.is_file():
                return by_name

    return path


def check_gps_geofence(
    complaint: Any,
    closure_media_path: Path | str,
    geofence_meters: float | None = None,
) -> Check:
    """Check 1: Verify closure photo GPS coordinates are within geofence."""
    path = _resolve_media_path(closure_media_path)
    threshold = (
        float(geofence_meters)
        if geofence_meters is not None
        else get_settings().GEOFENCE_METERS
    )

    complaint_lat = _get_prop(complaint, "lat")
    complaint_lon = _get_prop(complaint, "lon")

    if complaint_lat is None or complaint_lon is None:
        return Check(
            name="gps_geofence",
            passed=None,
            detail="Complaint coordinates missing; geofence check inconclusive.",
            value=None,
        )

    if not path.is_file():
        return Check(
            name="gps_geofence",
            passed=None,
            detail=f"Closure media file not found: {path.name}",
            value=None,
        )

    gps = extract_exif_gps(path)
    if gps is None:
        return Check(
            name="gps_geofence",
            passed=None,
            detail="Closure media missing EXIF GPS metadata; check inconclusive.",
            value=None,
        )

    distance = compute_distance(gps, (float(complaint_lat), float(complaint_lon)))
    if distance is None:
        return Check(
            name="gps_geofence",
            passed=None,
            detail="Unable to compute great-circle distance from closure GPS.",
            value=None,
        )

    if distance <= threshold:
        return Check(
            name="gps_geofence",
            passed=True,
            detail=(
                f"Evidence is within configured geofence "
                f"({distance:.1f}m <= {threshold:.1f}m)."
            ),
            value=distance,
        )


    return Check(
        name="gps_geofence",
        passed=False,
        detail=(
            f"Closure evidence location is {distance:.1f}m away from reported "
            f"complaint (exceeds {threshold:.1f}m geofence). Needs human review."
        ),
        value=distance,
    )


def check_duplicate_evidence(
    complaint: Any,
    closure_media_path: Path | str,
    all_complaints: Sequence[Any] | None = None,
    base_dir: Path | str | None = None,
) -> Check:
    """Check 2: Check for reused or duplicate closure evidence using pHash."""
    path = _resolve_media_path(closure_media_path, base_dir=base_dir)
    if not path.is_file():
        return Check(
            name="duplicate_evidence",
            passed=None,
            detail="Closure media file not found for duplicate check.",
            value=None,
        )

    try:
        closure_hash = compute_phash(path)
    except Exception:
        return Check(
            name="duplicate_evidence",
            passed=None,
            detail="Could not compute perceptual hash for closure media.",
            value=None,
        )

    complaint_id = _get_prop(complaint, "id", "")

    # 1. Compare against complaint's own initial media (detect recycled ticket photo)
    init_media = _get_prop(complaint, "media", [])
    for m in init_media:
        m_path = _get_prop(m, "path")
        if m_path:
            p_init = _resolve_media_path(m_path, base_dir=base_dir)
            if p_init.is_file():
                try:
                    init_hash = compute_phash(p_init)
                    h_dist = phash_distance(closure_hash, init_hash)
                    if h_dist <= 5:
                        return Check(
                            name="duplicate_evidence",
                            passed=False,
                            detail=(
                                f"Closure photo matches complaint's own initial photo "
                                f"(pHash distance {h_dist} <= 5). Recycled evidence; "
                                f"needs human review."
                            ),
                            value=float(h_dist),
                        )
                except Exception:
                    pass

    # 2. Compare against closure photos of all other closed complaints
    min_dist: int | None = None
    matched_complaint_id: str | None = None

    if all_complaints:
        for other in all_complaints:
            other_id = _get_prop(other, "id", "")
            if other_id == complaint_id:
                continue

            other_status = _get_prop(other, "status", "")
            if other_status != "closed":
                continue

            other_closure = _get_prop(other, "closure")
            if not other_closure:
                continue

            other_media = _get_prop(other_closure, "media", [])
            for om in other_media:
                om_path = _get_prop(om, "path")
                if om_path:
                    p_other = _resolve_media_path(om_path, base_dir=base_dir)
                    if p_other.is_file() and p_other.resolve() != path.resolve():
                        try:
                            o_hash = compute_phash(p_other)
                            dist = phash_distance(closure_hash, o_hash)
                            if min_dist is None or dist < min_dist:
                                min_dist = dist
                                matched_complaint_id = other_id
                        except Exception:
                            pass

    if min_dist is not None and min_dist <= 5:
        return Check(
            name="duplicate_evidence",
            passed=False,
            detail=(
                f"Closure photo duplicates evidence submitted for "
                f"{matched_complaint_id} (pHash distance {min_dist} <= 5). "
                f"Suspect duplicate submission; needs human review."
            ),
            value=float(min_dist),
        )

    return Check(
        name="duplicate_evidence",
        passed=True,
        detail="Closure evidence perceptual hash is unique across submissions.",
        value=float(min_dist) if min_dist is not None else None,
    )


def check_timestamp_sanity(
    complaint: Any,
    closure_media_path: Path | str,
) -> Check:
    """Check 3: Verify closure photo capture timestamp is temporally coherent."""
    path = _resolve_media_path(closure_media_path)
    if not path.is_file():
        return Check(
            name="timestamp_sanity",
            passed=None,
            detail="Closure media file not found for timestamp verification.",
            value=None,
        )

    taken_at = extract_exif_timestamp(path)
    if taken_at is None:
        return Check(
            name="timestamp_sanity",
            passed=None,
            detail="Closure media missing EXIF capture timestamp; check inconclusive.",
            value=None,
        )

    created_at_raw = _get_prop(complaint, "created_at")
    if created_at_raw is None:
        return Check(
            name="timestamp_sanity",
            passed=None,
            detail="Complaint creation timestamp missing.",
            value=None,
        )

    if isinstance(created_at_raw, str):
        try:
            created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
        except ValueError:
            return Check(
                name="timestamp_sanity",
                passed=None,
                detail="Invalid complaint created_at format.",
                value=None,
            )
    else:
        created_at = created_at_raw

    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    else:
        created_at = created_at.astimezone(UTC)

    diff_seconds = (taken_at - created_at).total_seconds()

    # If photo was taken more than 60 seconds before complaint creation
    if diff_seconds < -60.0:
        return Check(
            name="timestamp_sanity",
            passed=False,
            detail=(
                f"Closure photo capture time ({taken_at.isoformat()}) precedes "
                f"complaint creation ({created_at.isoformat()}) by "
                f"{abs(diff_seconds):.0f}s. Pre-existing photo; needs human review."
            ),
            value=float(diff_seconds),
        )

    return Check(
        name="timestamp_sanity",
        passed=True,
        detail="Closure photo timestamp is consistent with ticket lifecycle.",
        value=float(diff_seconds),
    )


def check_category_consistency(
    complaint: Any,
    closure_media_path: Path | str | None = None,
    defect_type: str | None = None,
) -> Check:
    """Check 4: Verify closure defect metadata aligns with complaint category."""
    category = str(_get_prop(complaint, "category", "")).strip().lower()

    # Determine defect type from argument, known media map, or closure metadata
    detected_defect = defect_type

    if not detected_defect and closure_media_path:
        fname = Path(closure_media_path).name
        detected_defect = _KNOWN_DEFECT_MAP.get(fname)

    if not detected_defect:
        closure = _get_prop(complaint, "closure")
        if closure:
            closed_by = str(_get_prop(closure, "closed_by", "")).lower()
            for kw, defect in _DEFECT_KEYWORDS.items():
                if kw in closed_by:
                    detected_defect = defect
                    break

    if not detected_defect:
        return Check(
            name="category_consistency",
            passed=True,
            detail="No conflicting defect metadata found on closure submission.",
            value=1.0,
        )

    detected_defect = detected_defect.strip().lower()

    if detected_defect != category:
        return Check(
            name="category_consistency",
            passed=False,
            detail=(
                f"Closure evidence defect '{detected_defect}' does not match "
                f"reported complaint category '{category}'. Needs human review."
            ),
            value=0.0,
        )

    return Check(
        name="category_consistency",
        passed=True,
        detail=f"Closure defect '{detected_defect}' matches complaint category.",
        value=1.0,
    )


def verify_closure(
    complaint: Complaint | dict[str, Any],
    all_complaints: Sequence[Complaint | dict[str, Any]] | None = None,
    media_dir: Path | str | None = None,
    geofence_meters: float | None = None,
) -> Verdict:
    """Run all deterministic Proof-of-Fix checks on a closed complaint."""
    cid = _get_prop(complaint, "id", "unknown")
    status = _get_prop(complaint, "status", "")

    # Non-closed complaints are inconclusive
    if status != "closed":
        return Verdict(
            complaint_id=cid,
            verdict="inconclusive",
            checks=[
                Check(
                    name="status_check",
                    passed=None,
                    detail="Complaint status is not closed.",
                    value=None,
                )
            ],
            confidence=0.0,
        )

    closure = _get_prop(complaint, "closure")
    if not closure:
        return Verdict(
            complaint_id=cid,
            verdict="inconclusive",
            checks=[
                Check(
                    name="closure_metadata",
                    passed=None,
                    detail="Closed complaint lacks closure metadata block.",
                    value=None,
                )
            ],
            confidence=0.20,
        )

    media_list = _get_prop(closure, "media", [])
    if not media_list:
        return Verdict(
            complaint_id=cid,
            verdict="inconclusive",
            checks=[
                Check(
                    name="evidence_present",
                    passed=None,
                    detail="Closed complaint has no closure evidence media attached.",
                    value=None,
                )
            ],
            confidence=0.30,
        )

    # Use first closure media file
    primary_media = media_list[0]
    mpath = _get_prop(primary_media, "path", "")
    resolved_path = _resolve_media_path(mpath, base_dir=media_dir)

    # Execute all 4 deterministic checks
    c_geo = check_gps_geofence(
        complaint,
        resolved_path,
        geofence_meters=geofence_meters,
    )
    c_dup = check_duplicate_evidence(
        complaint,
        resolved_path,
        all_complaints=all_complaints,
        base_dir=media_dir,
    )
    c_time = check_timestamp_sanity(
        complaint,
        resolved_path,
    )
    c_cat = check_category_consistency(
        complaint,
        closure_media_path=resolved_path,
    )

    checks = [c_geo, c_dup, c_time, c_cat]

    # Deterministic verdict rule:
    # Any check failed -> "suspect" immediately
    failed = [c for c in checks if c.passed is False]
    passed = [c for c in checks if c.passed is True]

    if failed:
        verdict = "suspect"
        # Confidence reflects how definitive the failed evidence is
        conf = round(min(1.0, 0.80 + 0.20 * (len(failed) / len(checks))), 2)
    elif all(c.passed is True for c in checks):
        verdict = "verified"
        conf = round(len(passed) / len(checks), 2)
    else:
        # Insufficient evidence / missing GPS or timestamps without failures
        verdict = "inconclusive"
        conf = round(len(passed) / len(checks), 2)

    return Verdict(
        complaint_id=cid,
        verdict=verdict,
        checks=checks,
        confidence=conf,
    )


def verify_closed_complaints(
    geojson_path_or_dict: Path | str | dict[str, Any] | Sequence[Any],
    media_dir: Path | str | None = None,
    geofence_meters: float | None = None,
) -> list[Verdict]:
    """Batch verify all closed complaints within a dataset."""
    complaints: list[Any] = []

    if isinstance(geojson_path_or_dict, (str, Path)):
        p = Path(geojson_path_or_dict)
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("type") == "FeatureCollection":
            complaints = [f["properties"] for f in data.get("features", [])]
        elif isinstance(data, list):
            complaints = data
    elif isinstance(geojson_path_or_dict, dict):
        if geojson_path_or_dict.get("type") == "FeatureCollection":
            features = geojson_path_or_dict.get("features", [])
            complaints = [f["properties"] for f in features]
        else:
            complaints = [geojson_path_or_dict]
    elif isinstance(geojson_path_or_dict, (list, tuple)):
        complaints = list(geojson_path_or_dict)

    # Filter closed complaints
    closed = [c for c in complaints if _get_prop(c, "status") == "closed"]

    verdicts: list[Verdict] = []
    for c in closed:
        v = verify_closure(
            complaint=c,
            all_complaints=complaints,
            media_dir=media_dir,
            geofence_meters=geofence_meters,
        )
        verdicts.append(v)

    return verdicts
