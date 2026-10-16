"""Command-line interface for validating and running available Omni-Grid stages."""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .schemas import Complaint, EvidenceMeta, Hotspot, Report, Verdict

ModelT = TypeVar("ModelT", bound=BaseModel)


class InputError(ValueError):
    """An expected input or contract validation error."""


class MissingDependencyError(RuntimeError):
    """A dependency required by an implemented pipeline stage is unavailable."""


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise InputError(f"input file not found: {path}") from exc
    except IsADirectoryError as exc:
        raise InputError(f"input path is a directory, not a file: {path}") from exc
    except UnicodeDecodeError as exc:
        raise InputError(f"input is not valid UTF-8 JSON: {path}") from exc
    except json.JSONDecodeError as exc:
        raise InputError(
            f"invalid JSON in {path} at line {exc.lineno}, column {exc.colno}: "
            f"{exc.msg}"
        ) from exc
    except OSError as exc:
        raise InputError(f"cannot read input file {path}: {exc.strerror}") from exc


def _validation_details(error: ValidationError) -> str:
    details = []
    for item in error.errors(include_input=False, include_context=False):
        location = ".".join(str(part) for part in item["loc"]) or "input"
        details.append(f"{location}: {item['msg']}")
    return "; ".join(details)


def _validate_model(model: type[ModelT], data: object, label: str) -> ModelT:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise InputError(
            f"{label} does not match {model.__name__}: {_validation_details(exc)}"
        ) from exc


def _validate_records(model: type[ModelT], data: object, label: str) -> list[ModelT]:
    records = data if isinstance(data, list) else [data]
    if not records:
        return []

    validated = []
    for index, record in enumerate(records):
        validated.append(_validate_model(model, record, f"{label}[{index}]"))
    return validated


def _feature_properties(feature: object, index: int) -> dict[str, object]:
    label = f"features[{index}]"
    if not isinstance(feature, dict) or feature.get("type") != "Feature":
        raise InputError(f"{label} must be a GeoJSON Feature")

    properties = feature.get("properties")
    if not isinstance(properties, dict):
        raise InputError(f"{label}.properties must be an object")

    geometry = feature.get("geometry")
    if not isinstance(geometry, dict) or geometry.get("type") != "Point":
        raise InputError(f"{label}.geometry must be a Point")

    coordinates = geometry.get("coordinates")
    if (
        not isinstance(coordinates, list)
        or len(coordinates) < 2
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in coordinates[:2]
        )
    ):
        raise InputError(
            f"{label}.geometry.coordinates must contain numeric longitude "
            "and latitude"
        )

    longitude, latitude = coordinates[:2]
    complaint_data = dict(properties)
    if "id" not in complaint_data and feature.get("id") is not None:
        complaint_data["id"] = feature["id"]

    for key, coordinate in (("lat", latitude), ("lon", longitude)):
        if key not in complaint_data:
            complaint_data[key] = coordinate
            continue
        try:
            property_coordinate = float(complaint_data[key])
        except (TypeError, ValueError):
            continue
        if not math.isclose(property_coordinate, coordinate, rel_tol=0, abs_tol=1e-9):
            raise InputError(
                f"{label}.properties.{key} conflicts with GeoJSON geometry"
            )

    return complaint_data


def _load_complaints(path: Path) -> list[Complaint]:
    data = _read_json(path)
    if isinstance(data, dict) and data.get("type") == "FeatureCollection":
        features = data.get("features")
        if not isinstance(features, list):
            raise InputError("GeoJSON FeatureCollection.features must be an array")
        complaints = []
        for index, feature in enumerate(features):
            properties = _feature_properties(feature, index)
            complaints.append(
                _validate_model(
                    Complaint,
                    properties,
                    f"features[{index}].properties",
                )
            )
        return complaints

    if isinstance(data, dict) and data.get("type") == "Feature":
        properties = _feature_properties(data, 0)
        return [_validate_model(Complaint, properties, "feature.properties")]

    return _validate_records(Complaint, data, "complaints")


def _parse_positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "must be a finite number greater than 0"
        ) from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than 0")
    return parsed


def _parse_positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer greater than 0") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be an integer greater than 0")
    return parsed


def _parse_reference_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "must be an ISO-8601 timestamp with a timezone"
        ) from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError(
            "must be an ISO-8601 timestamp with a timezone"
        )
    return parsed.astimezone(UTC)


def _add_cluster_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--eps-meters",
        type=_parse_positive_float,
        help="maximum DBSCAN neighborhood radius in meters",
    )
    parser.add_argument(
        "--min-samples",
        type=_parse_positive_int,
        help="minimum complaints required to form a hotspot",
    )
    parser.add_argument(
        "--reference-time",
        type=_parse_reference_time,
        help="timezone-aware ISO-8601 time for deterministic recency scoring",
    )


def _cluster(
    complaints: list[Complaint],
    args: argparse.Namespace,
) -> list[Hotspot]:
    try:
        from .cluster import cluster_complaints
    except ModuleNotFoundError as exc:
        if exc.name in {"numpy", "sklearn"}:
            raise MissingDependencyError(
                "clustering requires the pipeline dependencies; run "
                "`python -m pip install -e \".[pipeline]\"`"
            ) from exc
        raise

    return cluster_complaints(
        complaints,
        eps_meters=args.eps_meters,
        min_samples=args.min_samples,
        reference_time=args.reference_time,
    )


def _json_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="omnigrid",
        description="Validate Omni-Grid data and run the implemented triage stages.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser(
        "ingest",
        help="load and validate complaint JSON or a GeoJSON FeatureCollection",
        description=(
            "Read a Complaint JSON object/array or GeoJSON Feature/FeatureCollection. "
            "Output is a JSON array of shared Complaint records."
        ),
    )
    ingest.add_argument("input", type=Path, help="path to complaint JSON or GeoJSON")

    evidence = subparsers.add_parser(
        "evidence",
        help="validate existing EvidenceMeta JSON (does not extract media evidence)",
        description=(
            "Validate one EvidenceMeta JSON object or an array against the shared "
            "schema. EXIF/pHash extraction is not implemented."
        ),
    )
    evidence.add_argument("input", type=Path, help="path to EvidenceMeta JSON")

    cluster = subparsers.add_parser(
        "cluster",
        help="cluster validated complaints into hotspots",
        description=(
            "Load Complaint JSON or GeoJSON and run the implemented deterministic "
            "DBSCAN hotspot clustering stage. Output is a JSON array of Hotspot."
        ),
    )
    cluster.add_argument("input", type=Path, help="path to complaint JSON or GeoJSON")
    _add_cluster_options(cluster)

    verify = subparsers.add_parser(
        "verify",
        help="validate an existing Verdict JSON (does not evaluate closure evidence)",
        description=(
            "Validate a precomputed Verdict JSON object or array against the shared "
            "schema. Proof-of-fix checks are not implemented."
        ),
    )
    verify.add_argument("input", type=Path, help="path to Verdict JSON")

    summarize = subparsers.add_parser(
        "summarize",
        help="validate an existing Report JSON (does not generate a summary)",
        description=(
            "Validate an existing Report JSON against the shared schema. Narrative "
            "generation and citation validation are not implemented."
        ),
    )
    summarize.add_argument("input", type=Path, help="path to Report JSON")

    triage = subparsers.add_parser(
        "triage",
        help="ingest complaints and run the available clustering stage",
        description=(
            "Run ingestion and DBSCAN clustering, returning a partial shared Report. "
            "Evidence extraction, closure verification, and summarization are not run."
        ),
    )
    triage.add_argument("input", type=Path, help="path to complaint JSON or GeoJSON")
    _add_cluster_options(triage)

    return parser


def _execute(args: argparse.Namespace) -> object:
    if args.command == "ingest":
        return _load_complaints(args.input)
    if args.command == "evidence":
        return _validate_records(EvidenceMeta, _read_json(args.input), "evidence")
    if args.command == "cluster":
        return _cluster(_load_complaints(args.input), args)
    if args.command == "verify":
        return _validate_records(Verdict, _read_json(args.input), "verdicts")
    if args.command == "summarize":
        return _validate_model(Report, _read_json(args.input), "report")
    if args.command == "triage":
        complaints = _load_complaints(args.input)
        hotspots = _cluster(complaints, args)
        return Report(
            hotspots=hotspots,
            verdicts=[],
            claims=[],
            stats={
                "complaint_count": len(complaints),
                "hotspot_count": len(hotspots),
                "stages_completed": ["ingest", "cluster"],
                "stages_unavailable": ["evidence", "verify", "summarize"],
            },
        )
    raise InputError(f"unsupported command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        result = _execute(args)
    except InputError as exc:
        print(f"omnigrid: error: {exc}", file=sys.stderr)
        return 2
    except MissingDependencyError as exc:
        print(f"omnigrid: error: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"omnigrid: error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(_json_value(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
