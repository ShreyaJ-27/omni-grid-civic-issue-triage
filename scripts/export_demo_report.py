"""Export the canonical pipeline report for the web demo."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.omnigrid.damage import DamageSeverityCache  # noqa: E402
from core.omnigrid.evidence import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS  # noqa: E402
from core.omnigrid.ingest import InputError  # noqa: E402
from core.omnigrid.pipeline import (  # noqa: E402
    PipelineError,
    resolve_media_path,
    resolve_media_root,
    run_pipeline,
)
from core.omnigrid.schemas import Complaint, Media, Report  # noqa: E402

logger = logging.getLogger(__name__)

SYNTHETIC_DIR = REPO_ROOT / "data" / "synthetic"
COMPLAINTS_GEOJSON = SYNTHETIC_DIR / "complaints.geojson"
WEB_DEMO_DIR = REPO_ROOT / "web" / "demo"
WEB_MEDIA_DIR = WEB_DEMO_DIR / "media"
EXPORT_REPORT_PATH = WEB_DEMO_DIR / "report.json"


def _media_entries(complaint: Complaint) -> list[Media]:
    entries = list(complaint.media)
    if complaint.closure is not None:
        entries.extend(complaint.closure.media)
    return entries


def _export_media(
    report: Report,
    media_root: Path,
    output_media_dir: Path,
) -> int:
    output_media_dir.mkdir(parents=True, exist_ok=True)
    output_root = output_media_dir.resolve()
    copied: dict[str, Path] = {}
    dropped = 0

    for complaint in report.complaints:
        valid_media: list[Media] = []
        valid_closure_media: list[Media] = []
        closure_media_ids = (
            {id(item) for item in complaint.closure.media}
            if complaint.closure is not None
            else set()
        )

        for media in _media_entries(complaint):
            allowed_extensions = (
                IMAGE_EXTENSIONS if media.type == "image" else VIDEO_EXTENSIONS
            )
            try:
                source_path = resolve_media_path(media.path, media_root)
                if (
                    not source_path.is_file()
                    or source_path.suffix.lower() not in allowed_extensions
                ):
                    raise ValueError("media is missing or has an unsupported type")

                output_name = source_path.name
                destination = output_media_dir / output_name
                resolved_destination = destination.resolve()
                resolved_destination.relative_to(output_root)
                if destination.name in copied:
                    if (
                        copied[destination.name].read_bytes()
                        != source_path.read_bytes()
                    ):
                        raise ValueError("different media files share an output name")
                else:
                    shutil.copy2(source_path, destination)
                    copied[destination.name] = source_path
                media.path = f"demo/media/{output_name}"
                if id(media) in closure_media_ids:
                    valid_closure_media.append(media)
                else:
                    valid_media.append(media)
            except (OSError, RuntimeError, ValueError):
                dropped += 1
                logger.warning(
                    "Omitting an unavailable or unsafe media reference from export."
                )

        complaint.media = valid_media
        if complaint.closure is not None:
            complaint.closure.media = valid_closure_media

    return dropped


def export_demo(
    input_geojson: Path | str = COMPLAINTS_GEOJSON,
    output_report_path: Path | str = EXPORT_REPORT_PATH,
    output_media_dir: Path | str = WEB_MEDIA_DIR,
    use_mock_llm: bool = True,
    damage_cache: DamageSeverityCache | None = None,
    media_root: Path | str | None = None,
) -> Report:
    """Run the shared pipeline and export only media referenced by its report."""
    input_path = Path(input_geojson).resolve()
    report_path = Path(output_report_path).resolve()
    media_path = Path(output_media_dir).resolve()
    if input_path == report_path:
        raise ValueError("report output must not overwrite the complaint input")

    source_media_root = resolve_media_root(input_path, media_root)
    report = run_pipeline(
        input_path,
        media_root=source_media_root,
        mock=use_mock_llm,
        damage_cache=damage_cache,
    )
    dropped_media = _export_media(report, source_media_root, media_path)
    report.stats["exported_media_count"] = len(
        {
            media.path
            for complaint in report.complaints
            for media in _media_entries(complaint)
        }
    )
    report.stats["export_media_references_dropped"] = dropped_media
    validated = Report.model_validate(report.model_dump(mode="python"))

    for complaint in validated.complaints:
        for media in _media_entries(complaint):
            asset = (media_path / Path(media.path).name).resolve()
            asset.relative_to(media_path)
            if not asset.is_file():
                raise RuntimeError(
                    "export validation failed: referenced media asset is missing"
                )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    serialized: dict[str, Any] = validated.model_dump(mode="json")
    report_path.write_text(
        json.dumps(serialized, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Exported report to %s", report_path)
    return validated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the canonical pipeline and export a validated web report."
    )
    parser.add_argument("--input", type=Path, default=COMPLAINTS_GEOJSON)
    parser.add_argument("--output-report", type=Path, default=EXPORT_REPORT_PATH)
    parser.add_argument("--output-media-dir", type=Path, default=WEB_MEDIA_DIR)
    parser.add_argument("--media-root", type=Path)
    parser.add_argument(
        "--live-model",
        action="store_true",
        help="enable configured live model calls instead of deterministic mock mode",
    )
    args = parser.parse_args(argv)
    try:
        export_demo(
            input_geojson=args.input,
            output_report_path=args.output_report,
            output_media_dir=args.output_media_dir,
            use_mock_llm=not args.live_model,
            media_root=args.media_root,
        )
    except (InputError, PipelineError, OSError, ValueError, RuntimeError) as exc:
        print(f"export-demo: error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(
            f"export-demo: unexpected failure ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    raise SystemExit(main())
