"""Load and validate complaint JSON and GeoJSON against shared schemas."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .schemas import Complaint

ModelT = TypeVar("ModelT", bound=BaseModel)


class InputError(ValueError):
    """An expected input or contract validation error."""


def read_json(path: Path) -> object:
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


def validation_details(error: ValidationError) -> str:
    details = []
    for item in error.errors(include_input=False, include_context=False):
        location = ".".join(str(part) for part in item["loc"]) or "input"
        details.append(f"{location}: {item['msg']}")
    return "; ".join(details)


def validate_model(model: type[ModelT], data: object, label: str) -> ModelT:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise InputError(
            f"{label} does not match {model.__name__}: {validation_details(exc)}"
        ) from exc


def validate_records(
    model: type[ModelT],
    data: object,
    label: str,
) -> list[ModelT]:
    records = data if isinstance(data, list) else [data]
    if not records:
        return []
    return [
        validate_model(model, record, f"{label}[{index}]")
        for index, record in enumerate(records)
    ]


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
    if "id" not in complaint_data:
        if feature.get("id") is not None:
            complaint_data["id"] = feature["id"]
        elif "complaint_id" in complaint_data:
            complaint_data["id"] = complaint_data["complaint_id"]

    if "text" not in complaint_data and "description" in complaint_data:
        complaint_data["text"] = complaint_data["description"]

    if "media" not in complaint_data and "evidence" in complaint_data:
        complaint_data["media"] = complaint_data["evidence"]

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


def load_complaints(source: object) -> list[Complaint]:
    """Load complaints from a path, JSON value, or GeoJSON feature collection."""
    data = read_json(Path(source)) if isinstance(source, (str, Path)) else source

    if isinstance(data, dict) and data.get("type") == "FeatureCollection":
        features = data.get("features")
        if not isinstance(features, list):
            raise InputError("GeoJSON FeatureCollection.features must be an array")
        complaints = [
            validate_model(
                Complaint,
                _feature_properties(feature, index),
                f"features[{index}].properties",
            )
            for index, feature in enumerate(features)
        ]
    elif isinstance(data, dict) and data.get("type") == "Feature":
        properties = _feature_properties(data, 0)
        complaints = [validate_model(Complaint, properties, "feature.properties")]
    else:
        complaints = validate_records(Complaint, data, "complaints")

    ids = [complaint.id for complaint in complaints]
    if len(ids) != len(set(ids)):
        raise InputError("complaint IDs must be unique")
    return complaints
