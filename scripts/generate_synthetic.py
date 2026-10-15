"""Generate synthetic Bengaluru civic issue complaints and ground truth answer key.

Produces 500 complaints across Bengaluru with:
- 5 planted geographic hotspots (12-15 complaints within 20-50m)
- Status split: 400 open, 100 closed
- Exactly 5 planted suspicious closures for O3 verification:
  * 2 Mismatched GPS (>5 km offset)
  * 2 Duplicate Evidence (identical image hash)
  * 1 Inconsistent Defect (streetlight complaint with pothole repair closure)
- Exports valid FeatureCollection to data/synthetic/complaints.geojson
- Exports answer key to data/synthetic/ground_truth.json
"""

from __future__ import annotations

import json
import math
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Ensure project root and core are in sys.path
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
CORE_DIR = REPO_ROOT / "core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from core.omnigrid.schemas import Closure, Complaint, Media  # noqa: E402
from scripts.generate_sample_media import (  # noqa: E402
    DEFAULT_MEDIA_DIR,
    generate_sample_media,
)

DATA_SYNTHETIC_DIR = REPO_ROOT / "data" / "synthetic"
COMPLAINTS_GEOJSON_PATH = DATA_SYNTHETIC_DIR / "complaints.geojson"
GROUND_TRUTH_JSON_PATH = DATA_SYNTHETIC_DIR / "ground_truth.json"

RANDOM_SEED = 42

# 5 distinct geographic hotspots with coordinates and counts
HOTSPOT_SPECS = [
    {
        "id": "hotspot-silk-board",
        "name": "Silk Board",
        "centroid": (12.9172, 77.6228),
        "count": 14,
        "primary_category": "pothole",
    },
    {
        "id": "hotspot-indiranagar-100ft-rd",
        "name": "Indiranagar 100ft Rd",
        "centroid": (12.9784, 77.6408),
        "count": 13,
        "primary_category": "streetlight",
    },
    {
        "id": "hotspot-domlur-flyover",
        "name": "Domlur Flyover",
        "centroid": (12.9609, 77.6387),
        "count": 15,
        "primary_category": "waterlogging",
    },
    {
        "id": "hotspot-jayanagar-4th-block",
        "name": "Jayanagar 4th Block",
        "centroid": (12.9304, 77.5833),
        "count": 12,
        "primary_category": "other",
    },
    {
        "id": "hotspot-hebbal",
        "name": "Hebbal",
        "centroid": (13.0354, 77.5970),
        "count": 14,
        "primary_category": "pothole",
    },
]

TEXT_TEMPLATES: dict[str, list[str]] = {
    "pothole": [
        "Deep crater on main carriageway near {loc} causing traffic hazard.",
        "Hazardous road depression and loose asphalt near {loc}.",
        "Multiple potholes along the bus lane near {loc} slowing commuter traffic.",
        "Severe road surface erosion creating dangerous bump near {loc}.",
        "Large pothole near {loc} posing danger to two-wheelers and autos.",
    ],
    "waterlogging": [
        "Severe stormwater accumulation under the {loc} underpass after rain.",
        "Blocked roadside drain causing waterlogging near {loc}.",
        "Stagnant rainwater pool blocking the pedestrian crossing near {loc}.",
        "Drainage overflow onto the service road near {loc}.",
        "Water pooling across traffic lanes near {loc} due to clogged culvert.",
    ],
    "streetlight": [
        "Streetlights out for consecutive nights along {loc}, pitch dark road.",
        "Flickering street lamp pole near {loc} causing poor night visibility.",
        "Group of dark streetlight poles near {loc} creating safety concern.",
        "Streetlight fixture non-functional on arterial road near {loc}.",
        "Complete lighting outage on the main stretch near {loc}.",
    ],
    "other": [
        "Overflowing waste container spilled onto the road near {loc}.",
        "Broken granite sidewalk slabs tripping pedestrians near {loc}.",
        "Fallen tree branches obstructing traffic lane near {loc}.",
        "Damaged median barrier protruding into active lane near {loc}.",
        "Construction rubble left on the pedestrian walkway near {loc}.",
    ],
}

LOCALITY_NAMES = [
    "Silk Board Junction",
    "Indiranagar 100ft Road",
    "Domlur Flyover",
    "Jayanagar 4th Block",
    "Hebbal Flyover",
    "Koramangala 5th Block",
    "Whitefield Main Road",
    "Malleshwaram 8th Cross",
    "BTM Layout 2nd Stage",
    "HSR Layout Sector 1",
    "Banashankari 2nd Stage",
    "MG Road Metro Station",
    "Ulsoor Lake Road",
    "Basavanagudi Gandhi Bazaar",
    "Rajajinagar 1st Block",
]


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate Great Circle distance in meters between two coordinates."""
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    return r * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def generate_synthetic_data() -> (
    tuple[list[Complaint], dict[str, Any], dict[str, Any]]
):
    """Generate 500 validated complaints, GeoJSON FeatureCollection, and answer key."""
    rng = random.Random(RANDOM_SEED)

    # Ensure media files exist
    if not (DEFAULT_MEDIA_DIR / "media_15.jpg").is_file():
        generate_sample_media(DEFAULT_MEDIA_DIR)

    complaints: list[Complaint] = []
    hotspot_records: list[dict[str, Any]] = []

    # Designate the 5 suspicious closure IDs cleanly within their respective locations
    suspicious_ids = {
        "mismatched_gps_1": "complaint-003",  # Silk Board hotspot
        "mismatched_gps_2": "complaint-017",  # Indiranagar hotspot
        "duplicate_evidence_1": "complaint-030",  # Domlur hotspot
        "duplicate_evidence_2": "complaint-045",  # Jayanagar hotspot
        "inconsistent_defect": "complaint-018",  # Indiranagar hotspot
    }

    complaint_counter = 1
    base_time = datetime(2026, 10, 10, 8, 0, 0, tzinfo=UTC)

    # 1. Generate Hotspot complaints (68 total)
    for h_spec in HOTSPOT_SPECS:
        h_id = h_spec["id"]
        h_name = h_spec["name"]
        clat, clon = h_spec["centroid"]
        h_count = h_spec["count"]
        p_cat = h_spec["primary_category"]

        h_complaint_ids: list[str] = []
        category_counts: dict[str, int] = {
            "pothole": 0,
            "waterlogging": 0,
            "streetlight": 0,
            "other": 0,
        }

        # First 3 complaints in Silk Board, Domlur, Jayanagar, Hebbal are closed.
        # In Indiranagar, 4 are closed because it holds both
        # mismatched_gps_2 and inconsistent_defect.
        closed_threshold = 4 if h_name == "Indiranagar 100ft Rd" else 3

        for i in range(h_count):
            cid = f"complaint-{complaint_counter:03d}"
            complaint_counter += 1
            h_complaint_ids.append(cid)

            # 8 to 45m tight clustering around centroid
            radius = rng.uniform(8.0, 45.0)
            theta = rng.uniform(0, 2.0 * math.pi)
            dlat = (radius * math.cos(theta)) / 111000.0
            dlon = (radius * math.sin(theta)) / (
                111000.0 * math.cos(math.radians(clat))
            )
            lat = round(clat + dlat, 6)
            lon = round(clon + dlon, 6)

            # Assign category
            if cid == suspicious_ids["inconsistent_defect"]:
                category = "streetlight"
            elif rng.random() < 0.70:
                category = p_cat
            else:
                other_cats = [
                    c
                    for c in ["pothole", "waterlogging", "streetlight", "other"]
                    if c != p_cat
                ]
                category = rng.choice(other_cats)
            category_counts[category] += 1

            text = rng.choice(TEXT_TEMPLATES[category]).format(loc=h_name)
            created_at = base_time + timedelta(
                days=rng.randint(0, 3),
                hours=rng.randint(0, 23),
                minutes=rng.randint(0, 59),
            )

            is_closed = i < closed_threshold
            status = "closed" if is_closed else "open"
            closure = None

            if is_closed:
                closed_at = created_at + timedelta(
                    hours=rng.randint(4, 36), minutes=rng.randint(0, 59)
                )
                closure = Closure(
                    closed_at=closed_at,
                    media=[],
                    closed_by=f"synthetic-bbmp-crew-h{h_spec['name'][:2].lower()}",
                )

            comp = Complaint(
                id=cid,
                category=category,
                text=text,
                lat=lat,
                lon=lon,
                created_at=created_at,
                status=status,
                media=[],
                closure=closure,
            )
            complaints.append(comp)

        hotspot_records.append(
            {
                "id": h_id,
                "name": h_name,
                "centroid": [clat, clon],
                "radius_m": 45.0,
                "complaint_count": h_count,
                "complaint_ids": h_complaint_ids,
                "category_mix": category_counts,
            }
        )

    # 2. Generate Background complaints (432 total)
    bg_points: list[tuple[float, float]] = []
    while len(bg_points) < (500 - len(complaints)):
        lat = rng.uniform(12.90, 13.05)
        lon = rng.uniform(77.50, 77.70)

        # Ensure >= 300m away from all hotspot centroids
        too_close_hotspot = any(
            haversine_m(lat, lon, spec["centroid"][0], spec["centroid"][1]) < 300.0
            for spec in HOTSPOT_SPECS
        )
        if too_close_hotspot:
            continue

        # Prevent spurious DBSCAN clusters: at most 1 neighbor within 160m
        close_neighbors = sum(
            1
            for blat, blon in bg_points
            if haversine_m(lat, lon, blat, blon) < 160.0
        )
        if close_neighbors < 2:
            bg_points.append((round(lat, 6), round(lon, 6)))

    # Hotspots had 16 closed. We need 100 total closed, so 84 background closed.
    closed_indices = set(rng.sample(range(len(bg_points)), 84))

    for idx, (lat, lon) in enumerate(bg_points):
        cid = f"complaint-{complaint_counter:03d}"
        complaint_counter += 1

        category = rng.choice(["pothole", "waterlogging", "streetlight", "other"])
        loc = rng.choice(LOCALITY_NAMES)
        text = rng.choice(TEXT_TEMPLATES[category]).format(loc=loc)
        created_at = base_time + timedelta(
            days=rng.randint(0, 4),
            hours=rng.randint(0, 23),
            minutes=rng.randint(0, 59),
        )

        is_closed = idx in closed_indices
        status = "closed" if is_closed else "open"
        closure = None
        if is_closed:
            closed_at = created_at + timedelta(
                hours=rng.randint(2, 48), minutes=rng.randint(0, 59)
            )
            closure = Closure(
                closed_at=closed_at,
                media=[],
                closed_by="synthetic-triage-officer",
            )

        comp = Complaint(
            id=cid,
            category=category,
            text=text,
            lat=lat,
            lon=lon,
            created_at=created_at,
            status=status,
            media=[],
            closure=closure,
        )
        complaints.append(comp)

    # 3. Configure 5 Legitimate Closures with matching photo evidence
    verified_closure_assignments = [
        ("complaint-001", "data/synthetic/media/media_06.jpg", "pothole"),
        ("complaint-015", "data/synthetic/media/media_07.jpg", "streetlight"),
        ("complaint-028", "data/synthetic/media/media_08.jpg", "waterlogging"),
        ("complaint-043", "data/synthetic/media/media_09.jpg", "other"),
        ("complaint-055", "data/synthetic/media/media_10.jpg", "pothole"),
    ]
    for cid, media_path, cat in verified_closure_assignments:
        c = next(x for x in complaints if x.id == cid)
        c.status = "closed"
        c.category = cat  # type: ignore[assignment]
        closed_at = c.created_at + timedelta(hours=6)
        c.closure = Closure(
            closed_at=closed_at,
            media=[Media(path=media_path, type="image")],
            closed_by="verified-maintenance-crew",
        )

    # 4. Configure Citizen Evidence on 5 Open Complaints
    open_evidence_assignments = [
        ("complaint-005", "data/synthetic/media/media_01.jpg", "pothole"),
        ("complaint-020", "data/synthetic/media/media_02.jpg", "streetlight"),
        ("complaint-032", "data/synthetic/media/media_03.jpg", "waterlogging"),
        ("complaint-047", "data/synthetic/media/media_04.jpg", "other"),
        ("complaint-059", "data/synthetic/media/media_05.jpg", "pothole"),
    ]
    for cid, media_path, cat in open_evidence_assignments:
        c = next(x for x in complaints if x.id == cid)
        c.status = "open"
        c.category = cat  # type: ignore[assignment]
        c.closure = None
        c.media = [Media(path=media_path, type="image")]

    # 5. Plant Exactly 5 Suspicious Closures for O3 Verification
    # (A) Mismatched GPS 1: Silk Board complaint with closure EXIF at Yelahanka (>23 km)
    c_m1 = next(x for x in complaints if x.id == suspicious_ids["mismatched_gps_1"])
    c_m1.status = "closed"
    c_m1.category = "pothole"
    c_m1.text = "Deep road depression near Silk Board flyover entrance."
    c_m1.closure = Closure(
        closed_at=c_m1.created_at + timedelta(hours=8),
        media=[Media(path="data/synthetic/media/media_11.jpg", type="image")],
        closed_by="synthetic-crew-sb",
    )

    # (B) Mismatched GPS 2: Indiranagar complaint with closure EXIF at Anekal (>21 km)
    c_m2 = next(x for x in complaints if x.id == suspicious_ids["mismatched_gps_2"])
    c_m2.status = "closed"
    c_m2.category = "streetlight"
    c_m2.text = "Dark streetlight along Indiranagar 100ft road corridor."
    c_m2.closure = Closure(
        closed_at=c_m2.created_at + timedelta(hours=12),
        media=[Media(path="data/synthetic/media/media_12.jpg", type="image")],
        closed_by="synthetic-crew-in",
    )

    # (C) Duplicate Evidence 1: Domlur complaint
    c_d1 = next(x for x in complaints if x.id == suspicious_ids["duplicate_evidence_1"])
    c_d1.status = "closed"
    c_d1.category = "pothole"
    c_d1.text = "Pothole hazard on Domlur inner ring road approach."
    c_d1.closure = Closure(
        closed_at=c_d1.created_at + timedelta(hours=10),
        media=[Media(path="data/synthetic/media/media_14.jpg", type="image")],
        closed_by="synthetic-patch-crew-a",
    )

    # (D) Duplicate Evidence 2: Jayanagar complaint reusing duplicate image hash
    c_d2 = next(x for x in complaints if x.id == suspicious_ids["duplicate_evidence_2"])
    c_d2.status = "closed"
    c_d2.category = "pothole"
    c_d2.text = "Road crater in front of Jayanagar 4th Block complex."
    c_d2.closure = Closure(
        closed_at=c_d2.created_at + timedelta(hours=11),
        media=[Media(path="data/synthetic/media/media_15.jpg", type="image")],
        closed_by="synthetic-patch-crew-b",
    )

    # (E) Inconsistent Defect: Streetlight complaint closed with pothole repair photo
    c_inc = next(x for x in complaints if x.id == suspicious_ids["inconsistent_defect"])
    c_inc.status = "closed"
    c_inc.category = "streetlight"
    c_inc.text = "Streetlight unlit on 100ft road creating hazardous dark stretch."
    c_inc.closure = Closure(
        closed_at=c_inc.created_at + timedelta(hours=14),
        media=[Media(path="data/synthetic/media/media_13.jpg", type="image")],
        closed_by="synthetic-asphalt-contractor",
    )

    # 6. Validate every record using Complaint.model_validate()
    validated_complaints: list[Complaint] = []
    features: list[dict[str, Any]] = []

    for c in complaints:
        dumped = c.model_dump(mode="json")
        validated = Complaint.model_validate(dumped)
        validated_complaints.append(validated)

        features.append(
            {
                "type": "Feature",
                "id": validated.id,
                "geometry": {
                    "type": "Point",
                    "coordinates": [validated.lon, validated.lat],
                },
                "properties": dumped,
            }
        )

    geojson_data: dict[str, Any] = {
        "type": "FeatureCollection",
        "features": features,
    }

    open_count = sum(1 for c in validated_complaints if c.status == "open")
    closed_count = sum(1 for c in validated_complaints if c.status == "closed")

    # Build ground truth answer key
    ground_truth_data: dict[str, Any] = {
        "metadata": {
            "seed": RANDOM_SEED,
            "disclaimer": "synthetic_only_no_real_citizen_data",
            "generated_at": datetime.now(UTC).isoformat(),
            "total_complaints": len(validated_complaints),
            "open_complaints": open_count,
            "closed_complaints": closed_count,
            "planted_hotspots_count": len(hotspot_records),
            "planted_suspicious_closures_count": 5,
        },
        "hotspots": hotspot_records,
        "suspicious_closures": {
            "mismatched_gps": [
                {
                    "complaint_id": suspicious_ids["mismatched_gps_1"],
                    "type": "mismatched_gps",
                    "description": (
                        "Closure image EXIF GPS is ~23.1 km away from "
                        "complaint location (>5 km)"
                    ),
                    "complaint_coords": [c_m1.lat, c_m1.lon],
                    "closure_media_path": "data/synthetic/media/media_11.jpg",
                    "exif_gps_coords": [13.1250, 77.6300],
                    "distance_km": 23.12,
                },
                {
                    "complaint_id": suspicious_ids["mismatched_gps_2"],
                    "type": "mismatched_gps",
                    "description": (
                        "Closure image EXIF GPS is ~21.5 km away from "
                        "complaint location (>5 km)"
                    ),
                    "complaint_coords": [c_m2.lat, c_m2.lon],
                    "closure_media_path": "data/synthetic/media/media_12.jpg",
                    "exif_gps_coords": [12.7850, 77.6500],
                    "distance_km": 21.53,
                },
            ],
            "duplicate_evidence": [
                {
                    "complaint_id": suspicious_ids["duplicate_evidence_1"],
                    "type": "duplicate_evidence",
                    "description": (
                        "Closure evidence image is identical duplicate "
                        "of complaint-045"
                    ),
                    "closure_media_path": "data/synthetic/media/media_14.jpg",
                    "duplicate_with_complaint_id": suspicious_ids[
                        "duplicate_evidence_2"
                    ],
                    "duplicate_with_media_path": (
                        "data/synthetic/media/media_15.jpg"
                    ),
                },
                {
                    "complaint_id": suspicious_ids["duplicate_evidence_2"],
                    "type": "duplicate_evidence",
                    "description": (
                        "Closure evidence image is identical duplicate "
                        "of complaint-030"
                    ),
                    "closure_media_path": "data/synthetic/media/media_15.jpg",
                    "duplicate_with_complaint_id": suspicious_ids[
                        "duplicate_evidence_1"
                    ],
                    "duplicate_with_media_path": (
                        "data/synthetic/media/media_14.jpg"
                    ),
                },
            ],
            "inconsistent_defect": [
                {
                    "complaint_id": suspicious_ids["inconsistent_defect"],
                    "type": "inconsistent_defect",
                    "description": (
                        "Streetlight complaint closed with pothole repair "
                        "image evidence"
                    ),
                    "complaint_category": "streetlight",
                    "closure_defect": "pothole",
                    "closure_media_path": "data/synthetic/media/media_13.jpg",
                }
            ],
        },
        "suspicious_closure_ids": [
            suspicious_ids["mismatched_gps_1"],
            suspicious_ids["mismatched_gps_2"],
            suspicious_ids["duplicate_evidence_1"],
            suspicious_ids["duplicate_evidence_2"],
            suspicious_ids["inconsistent_defect"],
        ],
    }

    return validated_complaints, geojson_data, ground_truth_data


def main() -> None:
    DATA_SYNTHETIC_DIR.mkdir(parents=True, exist_ok=True)
    complaints, geojson_data, ground_truth_data = generate_synthetic_data()

    # Write complaints.geojson
    with open(COMPLAINTS_GEOJSON_PATH, "w", encoding="utf-8") as f:
        json.dump(geojson_data, f, indent=2)

    # Write ground_truth.json
    with open(GROUND_TRUTH_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(ground_truth_data, f, indent=2)

    open_count = ground_truth_data["metadata"]["open_complaints"]
    closed_count = ground_truth_data["metadata"]["closed_complaints"]
    print(
        f"Generated {len(complaints)} complaints "
        f"({open_count} open, {closed_count} closed)"
    )
    print(f" - Exported GeoJSON: {COMPLAINTS_GEOJSON_PATH}")
    print(f" - Exported Ground Truth: {GROUND_TRUTH_JSON_PATH}")


if __name__ == "__main__":
    main()
