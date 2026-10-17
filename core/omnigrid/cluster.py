"""Phase 3: Spatial clustering and explainable severity scoring."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import numpy as np
from sklearn.cluster import DBSCAN

from .config import get_settings
from .schemas import Complaint, Hotspot

EARTH_RADIUS_METERS: float = 6_371_000.0
RECENCY_HALF_LIFE_HOURS: float = 72.0
COUNT_SATURATION: float = 20.0

WEIGHT_COUNT: float = 0.45
WEIGHT_VLM: float = 0.30
WEIGHT_RECENCY: float = 0.15
WEIGHT_INJURY: float = 0.10

SAFETY_KEYWORDS: tuple[str, ...] = (
    "injury",
    "accident",
    "fallen",
    "hospital",
    "hazard",
    "critical",
)

_SAFETY_REGEX = re.compile(
    r"\b(" + "|".join(SAFETY_KEYWORDS) + r")",
    re.IGNORECASE,
)


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute Great Circle distance in meters between two coordinates.

    Uses the Earth radius constant 6,371,000 meters.
    """

    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    # Numerical safeguard against rounding errors producing a > 1.0
    a = min(1.0, max(0.0, a))
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_METERS * c


def _extract_complaint_id(item: Any) -> str:
    if hasattr(item, "id"):
        return str(item.id)
    if isinstance(item, dict):
        if "id" in item:
            return str(item["id"])
        if "properties" in item and isinstance(item["properties"], dict):
            return str(item["properties"].get("id", ""))
    return ""


def _extract_coordinates(item: Any) -> tuple[float, float] | None:
    lat: Any = None
    lon: Any = None

    if hasattr(item, "lat") and hasattr(item, "lon"):
        lat, lon = item.lat, item.lon
    elif isinstance(item, dict):
        if "lat" in item and "lon" in item:
            lat, lon = item["lat"], item["lon"]
        elif "properties" in item and isinstance(item["properties"], dict):
            props = item["properties"]
            lat, lon = props.get("lat"), props.get("lon")
        elif (
            "geometry" in item
            and isinstance(item["geometry"], dict)
            and item["geometry"].get("type") == "Point"
        ):
            coords = item["geometry"].get("coordinates")
            if (
                coords
                and isinstance(coords, (list, tuple))
                and len(coords) >= 2
            ):
                lon, lat = coords[0], coords[1]

    if lat is None or lon is None:
        return None

    try:
        f_lat = float(lat)
        f_lon = float(lon)
    except (ValueError, TypeError):
        return None

    if not math.isfinite(f_lat) or not math.isfinite(f_lon):
        return None

    if not (-90.0 <= f_lat <= 90.0 and -180.0 <= f_lon <= 180.0):
        return None

    return f_lat, f_lon


def _extract_text(item: Any) -> str:
    if hasattr(item, "text") and isinstance(item.text, str):
        return item.text
    if isinstance(item, dict):
        if "text" in item and isinstance(item["text"], str):
            return item["text"]
        if "properties" in item and isinstance(item["properties"], dict):
            return str(item["properties"].get("text", ""))
    return ""


def _extract_category(item: Any) -> str:
    if hasattr(item, "category") and item.category:
        return str(item.category)
    if isinstance(item, dict):
        if "category" in item and item["category"]:
            return str(item["category"])
        if "properties" in item and isinstance(item["properties"], dict):
            cat = item["properties"].get("category")
            if cat:
                return str(cat)
    return "other"


def _extract_created_at(item: Any) -> datetime | None:
    dt: Any = None
    if hasattr(item, "created_at"):
        dt = item.created_at
    elif isinstance(item, dict):
        if "created_at" in item:
            dt = item["created_at"]
        elif "properties" in item and isinstance(item["properties"], dict):
            dt = item["properties"].get("created_at")

    if dt is None:
        return None

    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)

    if isinstance(dt, str):
        try:
            parsed = datetime.fromisoformat(dt.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except (ValueError, TypeError):
            return None

    return None


def _extract_vlm_damage_score(item: Any) -> float | None:
    candidate_keys = (
        "vlm_damage_score",
        "damage_score",
        "vlm_score",
        "visual_damage_score",
    )

    for key in candidate_keys:
        if hasattr(item, key):
            val = getattr(item, key)
            if val is not None:
                try:
                    s = float(val)
                    if math.isfinite(s):
                        return max(0.0, min(1.0, s))
                except (ValueError, TypeError):
                    pass

    meta = getattr(item, "metadata", None)
    if isinstance(meta, dict):
        for key in candidate_keys:
            if key in meta and meta[key] is not None:
                try:
                    s = float(meta[key])
                    if math.isfinite(s):
                        return max(0.0, min(1.0, s))
                except (ValueError, TypeError):
                    pass

    if isinstance(item, dict):
        for key in candidate_keys:
            if key in item and item[key] is not None:
                try:
                    s = float(item[key])
                    if math.isfinite(s):
                        return max(0.0, min(1.0, s))
                except (ValueError, TypeError):
                    pass
        props = item.get("properties")
        if isinstance(props, dict):
            for key in candidate_keys:
                if key in props and props[key] is not None:
                    try:
                        s = float(props[key])
                        if math.isfinite(s):
                            return max(0.0, min(1.0, s))
                    except (ValueError, TypeError):
                        pass

    return None


def compute_severity_score(
    complaints: Sequence[Any],
    reference_time: datetime | None = None,
    damage_scores: dict[str, float] | None = None,
) -> float:
    """Compute the composite explainable severity score in [0.0, 10.0].

    Formula:
        severity = 10.0 * (
            0.45 * S_count +
            0.30 * S_vlm +
            0.15 * S_recency +
            0.10 * S_injury
        )

    - S_count: log2(1 + n) / log2(1 + 20), saturated at 1.0 for n >= 20.
    - S_vlm: Arithmetic mean of visual damage scores in [0.0, 1.0].
      Explicit fallback: 0.0 if scores are missing or unavailable.
    - S_recency: Exponential decay with 72-hour half-life relative to reference_time.
    - S_injury: Proportion of complaints mentioning safety/injury keywords.

    Clamped to [0.0, 10.0] and rounded to 2 decimal places.
    """
    n = len(complaints)
    if n == 0:
        return 0.0

    # 1. Count Score S_count
    s_count = min(1.0, math.log2(1.0 + n) / math.log2(1.0 + COUNT_SATURATION))
    s_count = max(0.0, min(1.0, s_count))

    # 2. VLM Damage Score S_vlm with explicit fallback to 0.0
    vlm_scores: list[float] = []
    for c in complaints:
        v: float | None = None
        cid = _extract_complaint_id(c)
        if damage_scores is not None and cid in damage_scores:
            cand = damage_scores[cid]
            if cand is not None:
                try:
                    s = float(cand)
                    if math.isfinite(s):
                        v = max(0.0, min(1.0, s))
                except (ValueError, TypeError):
                    pass
        if v is None:
            v = _extract_vlm_damage_score(c)
        if v is not None:
            vlm_scores.append(v)

    if vlm_scores:
        s_vlm = float(np.mean(vlm_scores))
        s_vlm = max(0.0, min(1.0, s_vlm))
    else:
        # Explicit Fallback: never hallucinate or invent a score
        s_vlm = 0.0

    # 3. Recency Score S_recency (72-hour half-life exponential decay)
    if reference_time is None:
        ref_time = datetime.now(UTC)
    elif reference_time.tzinfo is None:
        ref_time = reference_time.replace(tzinfo=UTC)
    else:
        ref_time = reference_time.astimezone(UTC)

    valid_times = [
        t for t in (_extract_created_at(c) for c in complaints) if t is not None
    ]
    if valid_times:
        newest_time = max(valid_times)
        age_hours = (ref_time - newest_time).total_seconds() / 3600.0
        if age_hours <= 0.0:
            s_recency = 1.0
        else:
            s_recency = math.exp(-math.log(2.0) * (age_hours / RECENCY_HALF_LIFE_HOURS))
        s_recency = max(0.0, min(1.0, s_recency))
    else:
        s_recency = 0.0

    # 4. Injury Score S_injury
    injury_count = 0
    for c in complaints:
        text = _extract_text(c)
        if _SAFETY_REGEX.search(text):
            injury_count += 1
    s_injury = injury_count / n
    s_injury = max(0.0, min(1.0, s_injury))

    # Composite weighted severity score in [0.0, 10.0]
    raw_severity = 10.0 * (
        WEIGHT_COUNT * s_count
        + WEIGHT_VLM * s_vlm
        + WEIGHT_RECENCY * s_recency
        + WEIGHT_INJURY * s_injury
    )

    clamped = max(0.0, min(10.0, raw_severity))
    return round(clamped, 2)


def compute_severity_breakdown(
    complaints: Sequence[Any],
    reference_time: datetime | None = None,
    damage_scores: dict[str, float] | None = None,
) -> dict[str, float]:
    """Compute component scores and final severity for detailed explainability."""
    n = len(complaints)
    if n == 0:
        return {
            "count_score": 0.0,
            "vlm_damage_score": 0.0,
            "recency_score": 0.0,
            "injury_score": 0.0,
            "severity": 0.0,
        }

    s_count = max(
        0.0, min(1.0, math.log2(1.0 + n) / math.log2(1.0 + COUNT_SATURATION))
    )

    vlm_scores: list[float] = []
    for c in complaints:
        v: float | None = None
        cid = _extract_complaint_id(c)
        if damage_scores is not None and cid in damage_scores:
            cand = damage_scores[cid]
            if cand is not None:
                try:
                    s = float(cand)
                    if math.isfinite(s):
                        v = max(0.0, min(1.0, s))
                except (ValueError, TypeError):
                    pass
        if v is None:
            v = _extract_vlm_damage_score(c)
        if v is not None:
            vlm_scores.append(v)

    s_vlm = max(0.0, min(1.0, float(np.mean(vlm_scores)))) if vlm_scores else 0.0

    if reference_time is None:
        ref_time = datetime.now(UTC)
    elif reference_time.tzinfo is None:
        ref_time = reference_time.replace(tzinfo=UTC)
    else:
        ref_time = reference_time.astimezone(UTC)

    valid_times = [
        t for t in (_extract_created_at(c) for c in complaints) if t is not None
    ]
    if valid_times:
        newest_time = max(valid_times)
        age_hours = (ref_time - newest_time).total_seconds() / 3600.0
        s_recency = (
            1.0
            if age_hours <= 0.0
            else math.exp(-math.log(2.0) * (age_hours / RECENCY_HALF_LIFE_HOURS))
        )
        s_recency = max(0.0, min(1.0, s_recency))
    else:
        s_recency = 0.0

    injury_count = sum(
        1 for c in complaints if _SAFETY_REGEX.search(_extract_text(c))
    )
    s_injury = max(0.0, min(1.0, injury_count / n))

    severity = round(
        max(
            0.0,
            min(
                10.0,
                10.0
                * (
                    WEIGHT_COUNT * s_count
                    + WEIGHT_VLM * s_vlm
                    + WEIGHT_RECENCY * s_recency
                    + WEIGHT_INJURY * s_injury
                ),
            ),
        ),
        2,
    )

    return {
        "count_score": round(s_count, 4),
        "vlm_damage_score": round(s_vlm, 4),
        "recency_score": round(s_recency, 4),
        "injury_score": round(s_injury, 4),
        "severity": severity,
    }


def cluster_complaints(
    complaints: Sequence[Complaint | dict[str, Any]],
    eps_meters: float | None = None,
    min_samples: int | None = None,
    reference_time: datetime | None = None,
    damage_scores: dict[str, float] | None = None,
) -> list[Hotspot]:
    """Cluster complaints using DBSCAN with haversine metric into explainable Hotspots.

    Args:
        complaints: Collection of Complaint schemas or complaint dictionaries.
        eps_meters: Clustering distance radius in meters (defaults to settings).
        min_samples: Minimum complaints to form a hotspot (defaults to settings).
        reference_time: Optional reference datetime for recency decay.
        damage_scores: Optional mapping of complaint_id -> damage severity
            score in [0.0, 1.0].

    Returns:
        Deterministically sorted list of valid Hotspot schemas.
    """
    settings = get_settings()
    eff_eps_meters = (
        float(eps_meters) if eps_meters is not None else settings.cluster_eps_meters
    )
    eff_min_samples = (
        int(min_samples) if min_samples is not None else settings.cluster_min_samples
    )

    if eff_eps_meters <= 0.0:
        raise ValueError("eps_meters must be a positive number")
    if eff_min_samples <= 0:
        raise ValueError("min_samples must be an integer greater than 0")

    # 1. Safely filter out missing, NaN, or out-of-range coordinates
    valid_complaints: list[Any] = []
    coords_list: list[tuple[float, float]] = []

    for item in complaints:
        coords = _extract_coordinates(item)
        if coords is not None:
            coords_list.append(coords)
            valid_complaints.append(item)

    if len(valid_complaints) < eff_min_samples:
        return []

    # 2. Convert degrees to radians and eps from meters to radians
    coords_arr = np.array(coords_list, dtype=np.float64)
    coords_rad = np.radians(coords_arr)
    eps_rad = eff_eps_meters / EARTH_RADIUS_METERS

    # 3. Fit DBSCAN with haversine metric
    db = DBSCAN(
        eps=eps_rad,
        min_samples=eff_min_samples,
        metric="haversine",
    ).fit(coords_rad)

    labels = db.labels_

    # 4. Group valid clusters (excluding noise label -1)
    unique_labels = sorted(set(labels))
    hotspots: list[Hotspot] = []

    for label in unique_labels:
        if label == -1:
            # Noise point - reject
            continue

        member_indices = np.where(labels == label)[0]
        members = [valid_complaints[i] for i in member_indices]
        member_coords = [coords_list[i] for i in member_indices]

        # Centroid: arithmetic mean of member coordinates
        centroid_lat = float(np.mean([lat for lat, _ in member_coords]))
        centroid_lon = float(np.mean([lon for _, lon in member_coords]))

        # Radius: maximum haversine distance in meters to any member point
        distances = [
            haversine_distance(centroid_lat, centroid_lon, lat, lon)
            for lat, lon in member_coords
        ]
        radius_m = round(float(max(distances)), 2) if distances else 0.0

        # Complaint IDs: deterministically sorted
        complaint_ids = sorted(_extract_complaint_id(m) for m in members)

        # Category mix / counts
        category_counts: dict[str, int] = {}
        for m in members:
            cat = _extract_category(m)
            category_counts[cat] = category_counts.get(cat, 0) + 1

        # Severity score
        severity = compute_severity_score(
            members,
            reference_time=reference_time,
            damage_scores=damage_scores,
        )

        # Deterministic unique identifier
        hotspot_id = f"hotspot-{centroid_lat:.4f}-{centroid_lon:.4f}"

        hotspot = Hotspot(
            id=hotspot_id,
            centroid=(round(centroid_lat, 6), round(centroid_lon, 6)),
            radius_m=radius_m,
            complaint_ids=complaint_ids,
            category_mix=category_counts,
            severity=severity,
        )
        hotspots.append(hotspot)

    # 5. Deterministic sorting: descending by count, then severity, then centroid
    hotspots.sort(

        key=lambda h: (
            -len(h.complaint_ids),
            -h.severity,
            h.centroid[0],
            h.centroid[1],
            h.id,
        )
    )

    return hotspots


# Backward-compatible convenience aliases
cluster = cluster_complaints
find_hotspots = cluster_complaints
