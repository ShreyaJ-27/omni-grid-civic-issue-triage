"""Extract a random sample of complaints from the real Bengaluru dataset."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = REPO_ROOT / "data" / "real_bangalore_complaints.geojson"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "real_sample_500.geojson"
DEFAULT_SAMPLE_SIZE = 500
DEFAULT_SEED = 42


def sample_real_dataset(
    input_path: Path | str = DEFAULT_INPUT,
    output_path: Path | str = DEFAULT_OUTPUT,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    seed: int = DEFAULT_SEED,
) -> Path:
    """Extract a reproducible random sample of complaints from GeoJSON."""
    in_path = Path(input_path)
    out_path = Path(output_path)

    if not in_path.is_file():
        raise FileNotFoundError(f"Source GeoJSON not found: {in_path}")

    with in_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        raise ValueError("Input file is not a GeoJSON FeatureCollection")

    features = data.get("features", [])
    if len(features) < sample_size:
        raise ValueError(
            f"Dataset has only {len(features)} features; cannot sample {sample_size}"
        )

    rng = random.Random(seed)
    sampled_features = rng.sample(features, sample_size)

    output_data = {
        "type": "FeatureCollection",
        "features": sampled_features,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract reproducible sample from Bengaluru complaints GeoJSON."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help="Path to full real Bengaluru GeoJSON file",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output path for sampled GeoJSON",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=DEFAULT_SAMPLE_SIZE,
        help="Number of complaints to sample (default: 500)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="Random seed for deterministic sampling (default: 42)",
    )

    args = parser.parse_args()
    dest = sample_real_dataset(
        input_path=args.input,
        output_path=args.output,
        sample_size=args.size,
        seed=args.seed,
    )
    print(f"Successfully sampled {args.size} complaints to {dest}")


if __name__ == "__main__":
    main()
