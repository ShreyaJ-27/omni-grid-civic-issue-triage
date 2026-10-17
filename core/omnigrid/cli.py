"""Command-line interface for validation and the complete Omni-Grid pipeline."""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from .ingest import (
    InputError,
)
from .ingest import (
    load_complaints as _load_complaints,
)
from .ingest import (
    read_json as _read_json,
)
from .ingest import (
    validate_model as _validate_model,
)
from .ingest import (
    validate_records as _validate_records,
)
from .schemas import Complaint, EvidenceMeta, Hotspot, Report, Verdict


class MissingDependencyError(RuntimeError):
    """A dependency required by an implemented pipeline stage is unavailable."""


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
        help="run the complete complaint triage pipeline",
        description=(
            "Run ingestion, evidence extraction, DBSCAN clustering, closure "
            "verification, and citation-validated summary generation."
        ),
    )
    triage.add_argument("input", type=Path, help="path to complaint JSON or GeoJSON")
    _add_cluster_options(triage)
    triage.add_argument(
        "--media-root",
        type=Path,
        help="allowed root for relative and absolute evidence paths",
    )
    triage.add_argument(
        "--no-vision",
        action="store_true",
        help="skip provider vision checks while retaining deterministic verification",
    )

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
        from .pipeline import run_pipeline

        return run_pipeline(
            args.input,
            media_root=args.media_root,
            eps_meters=args.eps_meters,
            min_samples=args.min_samples,
            reference_time=args.reference_time,
            enable_vision=not args.no_vision,
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
    except RuntimeError as exc:
        print(f"omnigrid: error: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"omnigrid: error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        detail = exc.strerror or type(exc).__name__
        print(
            f"omnigrid: error: filesystem operation failed: {detail}",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(
            f"omnigrid: unexpected failure ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 1

    print(json.dumps(_json_value(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
