"""Generate synthetic civic media images with embedded EXIF GPS tags.

Creates 40 small JPEG images in data/synthetic/media/ for Bengaluru coordinates,
including intentional GPS offsets (>5 km) and duplicate evidence files.
"""

from __future__ import annotations

import random
import shutil
import sys
from pathlib import Path

import piexif
from PIL import Image, ImageDraw

# Ensure project root is in sys.path
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_MEDIA_DIR = REPO_ROOT / "data" / "synthetic" / "media"
EXPECTED_MEDIA_COUNT = 40


def decimal_to_dms_rational(
    deg_float: float,
) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    """Convert decimal degrees to EXIF DMS rational format ((d,1), (m,1), (s,10000))."""
    deg_abs = abs(deg_float)
    degrees = int(deg_abs)
    remainder_minutes = (deg_abs - degrees) * 60.0
    minutes = int(remainder_minutes)
    remainder_seconds = (remainder_minutes - minutes) * 60.0
    seconds_scaled = int(round(remainder_seconds * 10000.0))
    return ((degrees, 1), (minutes, 1), (seconds_scaled, 10000))


def dms_rational_to_decimal(
    dms: tuple[tuple[int, int], tuple[int, int], tuple[int, int]],
    ref: str,
) -> float:
    """Convert EXIF DMS rational format to decimal degrees."""
    degrees = dms[0][0] / dms[0][1]
    minutes = dms[1][0] / dms[1][1]
    seconds = dms[2][0] / dms[2][1]
    decimal = degrees + (minutes / 60.0) + (seconds / 3600.0)
    if ref.upper() in ("S", "W"):
        decimal = -decimal
    return round(decimal, 6)


def create_exif_bytes(
    lat: float, lon: float, timestamp: str = "2026:10:14 10:00:00"
) -> bytes:
    """Build EXIF header bytes containing valid GPS and camera metadata."""
    lat_ref = "N" if lat >= 0 else "S"
    lon_ref = "E" if lon >= 0 else "W"

    gps_ifd = {
        piexif.GPSIFD.GPSVersionID: (2, 3, 0, 0),
        piexif.GPSIFD.GPSLatitudeRef: lat_ref,
        piexif.GPSIFD.GPSLatitude: decimal_to_dms_rational(lat),
        piexif.GPSIFD.GPSLongitudeRef: lon_ref,
        piexif.GPSIFD.GPSLongitude: decimal_to_dms_rational(lon),
        piexif.GPSIFD.GPSAltitudeRef: 0,
        piexif.GPSIFD.GPSAltitude: (920, 1),
    }

    zeroth_ifd = {
        piexif.ImageIFD.Make: "OmniGrid Synthetic",
        piexif.ImageIFD.Model: "CivicSense Camera",
        piexif.ImageIFD.Software: "Pillow/piexif",
        piexif.ImageIFD.DateTime: timestamp,
    }

    exif_ifd = {
        piexif.ExifIFD.DateTimeOriginal: timestamp,
        piexif.ExifIFD.DateTimeDigitized: timestamp,
    }

    exif_dict = {
        "0th": zeroth_ifd,
        "Exif": exif_ifd,
        "GPS": gps_ifd,
        "1st": {},
        "thumbnail": None,
    }
    return piexif.dump(exif_dict)


def extract_exif_gps(image_path: Path | str) -> tuple[float, float] | None:
    """Read EXIF GPS latitude and longitude from a JPEG file."""
    try:
        exif_dict = piexif.load(str(image_path))
        gps = exif_dict.get("GPS", {})
        if (
            not gps
            or piexif.GPSIFD.GPSLatitude not in gps
            or piexif.GPSIFD.GPSLongitude not in gps
        ):
            return None

        lat_ref = gps.get(piexif.GPSIFD.GPSLatitudeRef, b"N")
        lon_ref = gps.get(piexif.GPSIFD.GPSLongitudeRef, b"E")
        if isinstance(lat_ref, bytes):
            lat_ref = lat_ref.decode("ascii", errors="ignore")
        if isinstance(lon_ref, bytes):
            lon_ref = lon_ref.decode("ascii", errors="ignore")

        lat = dms_rational_to_decimal(gps[piexif.GPSIFD.GPSLatitude], lat_ref)
        lon = dms_rational_to_decimal(gps[piexif.GPSIFD.GPSLongitude], lon_ref)
        return (lat, lon)
    except Exception:
        return None


def draw_sample_image(
    category: str,
    label: str,
    seed: int,
    is_repair: bool = False,
) -> Image.Image:
    """Generate a 128x128 synthetic image with varied visual features."""
    rng = random.Random(seed)
    img = Image.new("RGB", (128, 128), color=(240, 240, 240))
    draw = ImageDraw.Draw(img)

    # Base background theme
    if category == "pothole":
        base_color = (100, 100, 105) if not is_repair else (60, 60, 65)
        draw.rectangle([0, 0, 128, 128], fill=base_color)
        if not is_repair:
            # Jagged pothole cavity
            draw.ellipse([34, 34, 94, 84], fill=(30, 30, 35), outline=(20, 20, 25))
            draw.ellipse([44, 44, 84, 74], fill=(15, 15, 20))
        else:
            # Smooth asphalt repair patch with yellow border lines
            draw.rectangle(
                [25, 25, 103, 103], fill=(40, 40, 45), outline=(255, 215, 0)
            )
    elif category == "streetlight":
        draw.rectangle([0, 0, 128, 128], fill=(20, 25, 40))
        # Lamp post
        draw.rectangle([60, 30, 68, 128], fill=(160, 160, 170))
        if not is_repair:
            # Dark unlit bulb
            draw.ellipse([54, 18, 74, 38], fill=(50, 55, 65), outline=(80, 85, 95))
        else:
            # Glowing bright yellow light
            draw.ellipse(
                [44, 8, 84, 48], fill=(255, 240, 120), outline=(255, 255, 200)
            )
            draw.ellipse([54, 18, 74, 38], fill=(255, 255, 255))
    elif category == "waterlogging":
        water_bg = (45, 75, 110) if not is_repair else (110, 115, 120)
        draw.rectangle([0, 0, 128, 128], fill=water_bg)
        if not is_repair:
            # Water ripples
            for y in range(30, 100, 15):
                draw.arc(
                    [15, y, 113, y + 20],
                    start=0,
                    end=180,
                    fill=(120, 180, 230),
                    width=2,
                )
        else:
            # Cleared drain grate
            draw.rectangle(
                [30, 30, 98, 98], fill=(50, 50, 50), outline=(180, 180, 180)
            )
            for x in range(36, 96, 10):
                draw.line([(x, 30), (x, 98)], fill=(180, 180, 180), width=2)
    else:  # other / civic
        other_bg = (130, 120, 110) if not is_repair else (80, 130, 80)
        draw.rectangle([0, 0, 128, 128], fill=other_bg)
        # Abstract shapes
        for _ in range(6):
            rx1, ry1 = rng.randint(10, 80), rng.randint(20, 80)
            rx2, ry2 = rx1 + rng.randint(15, 40), ry1 + rng.randint(15, 40)
            rand_color = (
                rng.randint(60, 200),
                rng.randint(60, 200),
                rng.randint(60, 200),
            )
            draw.rectangle([rx1, ry1, rx2, ry2], fill=rand_color)

    # Seed-based noise blocks to ensure unique cryptographic and perceptual hashes
    for _ in range(8):
        nx = rng.randint(5, 115)
        ny = rng.randint(5, 115)
        ncolor = (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
        draw.rectangle([nx, ny, nx + 4, ny + 4], fill=ncolor)

    # Label footer
    draw.rectangle([0, 110, 128, 128], fill=(0, 0, 0))
    draw.text((6, 113), label[:20], fill=(255, 255, 255))

    return img


# Metadata definitions for the 15 images
# Coordinates correspond to Bengaluru landmarks:
# Silk Board: ~12.9172, 77.6228
# Indiranagar 100ft Rd: ~12.9784, 77.6408
# Domlur Flyover: ~12.9609, 77.6387
# Jayanagar 4th Block: ~12.9304, 77.5833
# Hebbal: ~13.0354, 77.5970
#
# Offsets (>5 km away):
# media_11.jpg: Silk Board closure, EXIF at Yelahanka (13.1250, 77.6300) -> ~23.1 km
# media_12.jpg: Indiranagar closure, EXIF at Anekal (12.7850, 77.6500) -> ~21.5 km
#
# Duplicates:
# media_14.jpg and media_15.jpg: Exact byte duplicate files under different filenames

MEDIA_SPECIFICATIONS = [
    # 1-5: Open complaint evidence photos (citizen submitted)
    {
        "filename": "media_01.jpg",
        "category": "pothole",
        "label": "SB-POTHOLE-OPEN",
        "lat": 12.9172,
        "lon": 77.6228,
        "is_repair": False,
        "seed": 101,
        "role": "open_complaint",
    },
    {
        "filename": "media_02.jpg",
        "category": "streetlight",
        "label": "IN-LIGHT-DARK",
        "lat": 12.9784,
        "lon": 77.6408,
        "is_repair": False,
        "seed": 102,
        "role": "open_complaint",
    },
    {
        "filename": "media_03.jpg",
        "category": "waterlogging",
        "label": "DM-FLOOD-OPEN",
        "lat": 12.9609,
        "lon": 77.6387,
        "is_repair": False,
        "seed": 103,
        "role": "open_complaint",
    },
    {
        "filename": "media_04.jpg",
        "category": "other",
        "label": "JN-SIDEWALK-BROKEN",
        "lat": 12.9304,
        "lon": 77.5833,
        "is_repair": False,
        "seed": 104,
        "role": "open_complaint",
    },
    {
        "filename": "media_05.jpg",
        "category": "pothole",
        "label": "HB-POTHOLE-CRATER",
        "lat": 13.0354,
        "lon": 77.5970,
        "is_repair": False,
        "seed": 105,
        "role": "open_complaint",
    },
    # 6-10: Legitimate closure repair photos (matching location & category)
    {
        "filename": "media_06.jpg",
        "category": "pothole",
        "label": "SB-POTHOLE-FIXED",
        "lat": 12.9173,
        "lon": 77.6229,
        "is_repair": True,
        "seed": 106,
        "role": "verified_closure",
    },
    {
        "filename": "media_07.jpg",
        "category": "streetlight",
        "label": "IN-LIGHT-REPAIRED",
        "lat": 12.9785,
        "lon": 77.6409,
        "is_repair": True,
        "seed": 107,
        "role": "verified_closure",
    },
    {
        "filename": "media_08.jpg",
        "category": "waterlogging",
        "label": "DM-DRAIN-CLEARED",
        "lat": 12.9610,
        "lon": 77.6388,
        "is_repair": True,
        "seed": 108,
        "role": "verified_closure",
    },
    {
        "filename": "media_09.jpg",
        "category": "other",
        "label": "JN-SIDEWALK-CLEAN",
        "lat": 12.9305,
        "lon": 77.5834,
        "is_repair": True,
        "seed": 109,
        "role": "verified_closure",
    },
    {
        "filename": "media_10.jpg",
        "category": "pothole",
        "label": "HB-POTHOLE-ASPHALT",
        "lat": 13.0355,
        "lon": 77.5971,
        "is_repair": True,
        "seed": 110,
        "role": "verified_closure",
    },
    # 11-12: Mismatched GPS closure photos (>5 km away from complaint)
    {
        "filename": "media_11.jpg",
        "category": "pothole",
        "label": "GPS-MISMATCH-YEL",
        "lat": 13.1250,  # Yelahanka, >23 km from Silk Board
        "lon": 77.6300,
        "is_repair": True,
        "seed": 111,
        "role": "mismatched_gps_closure",
    },
    {
        "filename": "media_12.jpg",
        "category": "streetlight",
        "label": "GPS-MISMATCH-ANK",
        "lat": 12.7850,  # Anekal, >21 km from Indiranagar
        "lon": 77.6500,
        "is_repair": True,
        "seed": 112,
        "role": "mismatched_gps_closure",
    },
    # 13: Inconsistent defect closure photo (shows pothole repair for streetlight)
    {
        "filename": "media_13.jpg",
        "category": "pothole",  # Pothole repair image
        "label": "DEFECT-POTHOLE-PATCH",
        "lat": 12.9784,  # Indiranagar coordinates
        "lon": 77.6408,
        "is_repair": True,
        "seed": 113,
        "role": "inconsistent_defect_closure",
    },
    # 14-15: Exact duplicates under different filenames
    {
        "filename": "media_14.jpg",
        "category": "pothole",
        "label": "DUP-EVIDENCE-SRC",
        "lat": 12.9609,
        "lon": 77.6387,
        "is_repair": True,
        "seed": 114,
        "role": "duplicate_evidence_a",
    },
    {
        "filename": "media_15.jpg",
        "role": "duplicate_evidence_b",
        "copy_of": "media_14.jpg",
    },
]

# Supplemental images broaden the synthetic sample while keeping each asset
# deterministic and geographically plausible.
_SUPPLEMENTAL_ANCHORS = [
    (12.9172, 77.6228),
    (12.9784, 77.6408),
    (12.9609, 77.6387),
    (12.9304, 77.5833),
    (13.0354, 77.5970),
]
_SUPPLEMENTAL_CATEGORIES = ["pothole", "waterlogging", "streetlight", "other"]
for _index in range(16, EXPECTED_MEDIA_COUNT + 1):
    _anchor_lat, _anchor_lon = _SUPPLEMENTAL_ANCHORS[(_index - 16) % 5]
    _offset = ((_index - 16) // 5 + 1) * 0.00002
    MEDIA_SPECIFICATIONS.append(
        {
            "filename": f"media_{_index:02d}.jpg",
            "category": _SUPPLEMENTAL_CATEGORIES[(_index - 16) % 4],
            "label": f"SYNTHETIC-{_index:02d}",
            "lat": _anchor_lat + _offset,
            "lon": _anchor_lon + _offset,
            "is_repair": False,
            "seed": 100 + _index,
            "role": "supplemental",
        }
    )


def generate_sample_media(output_dir: Path | str = DEFAULT_MEDIA_DIR) -> list[Path]:
    """Generate 40 JPEG images with EXIF GPS tags into output_dir."""
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    generated_files: list[Path] = []

    for spec in MEDIA_SPECIFICATIONS:
        filename = spec["filename"]
        filepath = target_dir / filename

        if "copy_of" in spec:
            source_file = target_dir / spec["copy_of"]
            shutil.copyfile(source_file, filepath)
            generated_files.append(filepath)
            continue

        img = draw_sample_image(
            category=spec["category"],
            label=spec["label"],
            seed=spec["seed"],
            is_repair=spec["is_repair"],
        )
        exif_bytes = create_exif_bytes(
            lat=spec["lat"],
            lon=spec["lon"],
            timestamp="2026:10:14 11:30:00",
        )
        img.save(filepath, format="JPEG", quality=90, exif=exif_bytes)
        generated_files.append(filepath)

    return generated_files


def main() -> None:
    media_paths = generate_sample_media(DEFAULT_MEDIA_DIR)
    print(f"Generated {len(media_paths)} images in {DEFAULT_MEDIA_DIR}")
    for path in media_paths:
        gps = extract_exif_gps(path)
        print(f" - {path.name}: GPS={gps}")


if __name__ == "__main__":
    main()
