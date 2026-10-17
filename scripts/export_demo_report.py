"""Export full Omni-Grid demo report and media assets to web/demo/.

Runs the pipeline stages on data/synthetic/complaints.geojson:
1. Ingests synthetic complaints
2. Clusters complaints into Hotspots with DBSCAN
3. Runs proof-of-fix verification checks (geofence, pHash, timestamp)
4. Scores vision damage severity for available photos
5. Generates structured summary claims and statistics
6. Saves the final validated Report to web/demo/report.json
7. Copies sample media to web/demo/media/ for local browser viewing
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.omnigrid.cli import _load_complaints  # noqa: E402
from core.omnigrid.cluster import cluster_complaints  # noqa: E402
from core.omnigrid.damage import (  # noqa: E402
    DamageSeverityCache,
    score_damage_severity,
)
from core.omnigrid.llm import LLMClient  # noqa: E402
from core.omnigrid.schemas import Complaint, Report, Verdict  # noqa: E402
from core.omnigrid.summarize import generate_summary  # noqa: E402
from core.omnigrid.verify import verify_closed_complaints  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SYNTHETIC_DIR = REPO_ROOT / "data" / "synthetic"
COMPLAINTS_GEOJSON = SYNTHETIC_DIR / "complaints.geojson"
SYNTHETIC_MEDIA_DIR = SYNTHETIC_DIR / "media"

WEB_DEMO_DIR = REPO_ROOT / "web" / "demo"
WEB_MEDIA_DIR = WEB_DEMO_DIR / "media"
EXPORT_REPORT_PATH = WEB_DEMO_DIR / "report.json"


def export_demo(
    input_geojson: Path = COMPLAINTS_GEOJSON,
    output_report_path: Path = EXPORT_REPORT_PATH,
    output_media_dir: Path = WEB_MEDIA_DIR,
    use_mock_llm: bool = True,
    damage_cache: DamageSeverityCache | None = None,
) -> Report:
    """Generate and export the complete demo Report and copy media assets."""
    logger.info("Loading complaints from %s...", input_geojson)
    complaints: list[Complaint] = _load_complaints(input_geojson)
    logger.info("Loaded %d complaints.", len(complaints))

    # 1. Score available complaint photos before clustering so scores contribute
    # to the documented hotspot severity formula.
    logger.info("Scoring damage severity for available media...")
    llm_client = LLMClient(mock=use_mock_llm)
    damage_scores: dict[str, float] = {}
    for c in complaints:
        if c.media:
            first_media = c.media[0]
            media_file = REPO_ROOT / first_media.path
            if media_file.is_file():
                score_obj = score_damage_severity(
                    image_path=media_file,
                    evidence_id=f"damage-{c.id}",
                    llm_client=llm_client,
                    cache=damage_cache,
                )
                if score_obj is not None:
                    damage_scores[c.id] = score_obj.score
    logger.info("Computed %d damage severity scores.", len(damage_scores))

    # 2. Spatial clustering consumes any available visual damage scores.
    logger.info("Running spatial DBSCAN clustering...")
    reference_time = max((c.created_at for c in complaints), default=None)
    hotspots = cluster_complaints(
        complaints,
        reference_time=reference_time,
        damage_scores=damage_scores,
    )
    logger.info("Identified %d hotspots.", len(hotspots))

    # 3. Verify every closed complaint, including cases with missing evidence.
    logger.info(
        "Verifying closed complaints with proof-of-fix evidence..."
    )
    verdicts: list[Verdict] = verify_closed_complaints(
        complaints,
        use_vision=False,  # deterministic checks first
    )
    logger.info("Produced %d verdicts.", len(verdicts))

    # 4. Generate structured summary
    logger.info("Synthesizing structured summary claims...")
    extra_facts = {
        "total_complaints": len(complaints),
        "open_complaints": sum(1 for c in complaints if c.status == "open"),
        "closed_complaints": sum(1 for c in complaints if c.status == "closed"),
        "damage_scores_computed": len(damage_scores),
    }
    report, _diagnostics = generate_summary(
        hotspots=hotspots,
        verdicts=verdicts,
        extra_facts=extra_facts,
        mock=use_mock_llm,
    )

    # 5. Attach complaints list to report for rich map visualization
    report.complaints = complaints

    # 6. Prepare web/demo directory and media
    output_report_path.parent.mkdir(parents=True, exist_ok=True)
    output_media_dir.mkdir(parents=True, exist_ok=True)

    if SYNTHETIC_MEDIA_DIR.is_dir():
        logger.info("Copying media assets to %s...", output_media_dir)
        for media_file in SYNTHETIC_MEDIA_DIR.glob("*.jpg"):
            dest_file = output_media_dir / media_file.name
            shutil.copy2(media_file, dest_file)

    # 7. Write exported report JSON
    report_dict: dict[str, Any] = report.model_dump(mode="json")
    # Normalize media paths for web usage: replace synthetic path with demo/media/
    if "complaints" in report_dict:
        for c in report_dict["complaints"]:
            for m in c.get("media", []):
                p = m.get("path", "")
                filename = Path(p).name
                if (
                    "media_" in filename
                    and (SYNTHETIC_MEDIA_DIR / filename).is_file()
                ):
                    m["path"] = f"demo/media/{filename}"
            closure = c.get("closure")
            if closure:
                for m in closure.get("media", []):
                    p = m.get("path", "")
                    filename = Path(p).name
                    if (
                        "media_" in filename
                        and (SYNTHETIC_MEDIA_DIR / filename).is_file()
                    ):
                        m["path"] = f"demo/media/{filename}"

    Report.model_validate(report_dict)
    output_report_path.write_text(
        json.dumps(report_dict, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Exported demo report successfully to %s", output_report_path)
    return report


if __name__ == "__main__":
    export_demo()
