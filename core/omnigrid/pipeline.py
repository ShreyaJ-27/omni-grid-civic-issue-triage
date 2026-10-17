"""Canonical end-to-end complaint triage pipeline."""

from __future__ import annotations

import logging
import tempfile
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from .cluster import cluster_complaints
from .damage import DamageSeverityCache, score_damage_severity
from .evidence import (
    IMAGE_EXTENSIONS,
    extract_complaint_evidence,
)
from .ingest import InputError, load_complaints
from .llm import LLMClient
from .schemas import Complaint, Report, Verdict
from .summarize import generate_summary
from .verify import VisionCache, verify_closed_complaints

logger = logging.getLogger(__name__)
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class PipelineError(RuntimeError):
    """A pipeline stage failed without producing a trustworthy complete report."""


def resolve_media_path(media_path: str | Path, media_root: Path | str) -> Path:
    """Resolve a media reference only when its target remains inside media_root."""
    root = Path(media_root).resolve()
    path = Path(media_path)
    try:
        candidate = (path if path.is_absolute() else root / path).resolve()
        candidate.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(
            "Media path is outside the configured media directory"
        ) from exc
    return candidate


def resolve_media_root(
    source: object,
    configured_root: Path | str | None = None,
) -> Path:
    if configured_root is not None:
        return Path(configured_root).resolve()
    if isinstance(source, (str, Path)):
        input_path = Path(source).resolve()
        if input_path == REPOSITORY_ROOT or REPOSITORY_ROOT in input_path.parents:
            return REPOSITORY_ROOT
        return input_path.parent
    return Path.cwd().resolve()


def _media_items(complaint: Complaint) -> list[Any]:
    items: list[Any] = list(complaint.media)
    if complaint.closure is not None:
        items.extend(complaint.closure.media)
    return items


def _vision_eligible(complaints: Sequence[Complaint]) -> int:
    return sum(
        1
        for complaint in complaints
        if complaint.status == "closed"
        and complaint.media
        and complaint.closure
        and complaint.closure.media
        and complaint.media[0].type == "image"
        and complaint.closure.media[0].type == "image"
    )


def _stage_status(skipped: int, total: int) -> str:
    if total == 0:
        return "completed_no_media"
    if skipped:
        return "completed_with_skips"
    return "completed"


def run_pipeline(
    source: object,
    *,
    media_root: Path | str | None = None,
    eps_meters: float | None = None,
    min_samples: int | None = None,
    reference_time: datetime | None = None,
    llm_client: LLMClient | None = None,
    mock: bool | None = None,
    enable_vision: bool = True,
    damage_cache: DamageSeverityCache | None = None,
    vision_cache: VisionCache | None = None,
) -> Report:
    """Run ingest, evidence, clustering, verification, summary, and validation.

    ``source`` may be a JSON/GeoJSON path or already-decoded Complaint data.
    Relative media paths are confined to ``media_root``; by default, repository
    inputs use the repository root and external inputs use their parent directory.
    """
    try:
        complaints = load_complaints(source)
    except InputError:
        raise
    except Exception as exc:
        raise InputError(
            f"could not validate complaint input: {type(exc).__name__}"
        ) from exc

    root = resolve_media_root(source, media_root)
    client = llm_client or LLMClient(mock=mock)
    mock_mode = client.config.mock
    total_media_refs = sum(len(_media_items(complaint)) for complaint in complaints)
    evidence_counts: dict[str, int] = {}

    evidence = []
    try:
        with tempfile.TemporaryDirectory(prefix="omnigrid-keyframes-") as keyframes:
            for complaint in complaints:
                try:
                    evidence.extend(
                        extract_complaint_evidence(
                            complaint,
                            base_dir=root,
                            keyframe_dir=Path(keyframes),
                            diagnostics=evidence_counts,
                        )
                    )
                except Exception as exc:
                    logger.warning(
                        "Evidence extraction failed (%s).", type(exc).__name__
                    )
                    evidence_counts["unexpected_failures"] = (
                        evidence_counts.get("unexpected_failures", 0) + 1
                    )
    except OSError as exc:
        logger.warning("Evidence workspace unavailable (%s).", type(exc).__name__)
        evidence_counts["unexpected_failures"] = (
            evidence_counts.get("unexpected_failures", 0) + 1
        )

    damage_scores: dict[str, float] = {}
    score_attempts = 0
    damage_media_skipped = 0
    eligible_damage_media = [
        (complaint, complaint.media[0])
        for complaint in complaints
        if complaint.media
        and complaint.media[0].type == "image"
        and Path(complaint.media[0].path).suffix.lower() in IMAGE_EXTENSIONS
    ]
    if mock_mode:
        damage_status = "skipped_mock"
    elif not eligible_damage_media:
        damage_status = "skipped_no_eligible_media"
    else:
        for complaint, media in eligible_damage_media:
            try:
                image_path = resolve_media_path(media.path, root)
            except ValueError:
                damage_media_skipped += 1
                continue
            if not image_path.is_file():
                damage_media_skipped += 1
                continue
            score_attempts += 1
            try:
                score = score_damage_severity(
                    image_path,
                    evidence_id=f"damage-{complaint.id}",
                    llm_client=client,
                    cache=damage_cache,
                )
            except Exception as exc:
                logger.warning("Damage scoring failed (%s).", type(exc).__name__)
                score = None
            if score is not None:
                damage_scores[complaint.id] = score.score
        damage_status = (
            "completed"
            if len(damage_scores) == score_attempts
            and damage_media_skipped == 0
            else "completed_with_unavailable_results"
        )

    try:
        effective_reference_time = reference_time or max(
            (complaint.created_at for complaint in complaints),
            default=None,
        )
        hotspots = cluster_complaints(
            complaints,
            eps_meters=eps_meters,
            min_samples=min_samples,
            reference_time=effective_reference_time,
            damage_scores=damage_scores or None,
        )
    except ModuleNotFoundError as exc:
        if exc.name in {"numpy", "sklearn"}:
            raise PipelineError(
                "clustering requires pipeline dependencies; run "
                '`python -m pip install -e ".[pipeline]"`'
            ) from exc
        raise PipelineError(
            f"clustering stage failed ({type(exc).__name__})"
        ) from exc
    except Exception as exc:
        raise PipelineError(
            f"clustering stage failed ({type(exc).__name__}): {exc}"
        ) from exc

    eligible_vision = _vision_eligible(complaints)
    if not enable_vision:
        vision_status = "disabled"
        use_vision = False
    elif mock_mode:
        vision_status = "skipped_mock"
        use_vision = False
    elif eligible_vision == 0:
        vision_status = "skipped_no_comparable_media"
        use_vision = False
    else:
        vision_status = "requested"
        use_vision = True

    verification_status = "completed"
    try:
        verdicts = verify_closed_complaints(
            complaints,
            media_dir=root,
            llm_client=client,
            use_vision=use_vision,
            cache=vision_cache,
            strict_media_paths=True,
        )
    except Exception as exc:
        logger.warning("Closure verification stage failed (%s).", type(exc).__name__)
        verdicts = [
            Verdict(
                complaint_id=complaint.id,
                verdict="inconclusive",
                confidence=0.0,
                checks=[],
            )
            for complaint in complaints
            if complaint.status == "closed"
        ]
        vision_status = "failed"
        verification_status = "failed_inconclusive_fallback"

    verdict_counts = Counter(verdict.verdict for verdict in verdicts)
    vision_checks = [
        check
        for verdict in verdicts
        for check in verdict.checks
        if check.name == "vision_consistency"
    ]
    if use_vision:
        vision_status = (
            "completed"
            if vision_checks
            and all(check.passed is not None for check in vision_checks)
            else "completed_with_inconclusive_checks"
            if vision_checks
            else "completed_no_checks"
        )

    evidence_hash_counts = Counter(meta.phash for meta in evidence)
    try:
        summary_report, summary_diagnostics = generate_summary(
            hotspots=hotspots,
            verdicts=verdicts,
            llm_client=client,
            extra_facts={
                "total_complaints": len(complaints),
                "open_complaints": sum(
                    complaint.status == "open" for complaint in complaints
                ),
                "closed_complaints": sum(
                    complaint.status == "closed" for complaint in complaints
                ),
                "evidence_count": len(evidence),
                "damage_scores_computed": len(damage_scores),
            },
            deterministic_fallback=mock_mode,
        )
    except Exception as exc:
        raise PipelineError(
            f"summary stage failed ({type(exc).__name__}): {exc}"
        ) from exc

    stage_status = {
        "ingest": "completed",
        "evidence": _stage_status(
            evidence_counts.get("skipped", 0)
            + evidence_counts.get("unexpected_failures", 0),
            total_media_refs,
        ),
        "damage_scoring": damage_status,
        "cluster": "completed",
        "verification": verification_status,
        "vision": vision_status,
        "summary": (
            "template_fallback"
            if summary_diagnostics["fallback_triggered"]
            else "completed"
        ),
        "report": "validated",
    }
    stages_completed = ["ingest"]
    if stage_status["evidence"].startswith("completed"):
        stages_completed.append("evidence")
    if stage_status["cluster"] == "completed":
        stages_completed.append("cluster")
    if verification_status == "completed":
        stages_completed.append("verification")
    if stage_status["summary"] in {"completed", "template_fallback"}:
        stages_completed.append("summarize")
    if stage_status["report"] == "validated":
        stages_completed.append("report_validation")
    stages_skipped = [
        stage
        for stage in ("damage_scoring", "vision")
        if stage_status[stage].startswith(("skipped", "disabled"))
    ]
    report_stats = {
        **summary_report.stats,
        "complaint_count": len(complaints),
        "total_complaints": len(complaints),
        "open_complaints": sum(c.status == "open" for c in complaints),
        "closed_complaints": sum(c.status == "closed" for c in complaints),
        "hotspot_count": len(hotspots),
        "total_hotspots": len(hotspots),
        "evidence_count": len(evidence),
        "media_references": total_media_refs,
        "media_references_skipped": evidence_counts.get("skipped", 0),
        "media_skipped_missing": evidence_counts.get("skipped_missing", 0),
        "media_skipped_invalid": evidence_counts.get("skipped_invalid", 0),
        "media_skipped_unsafe": evidence_counts.get("skipped_unsafe", 0),
        "media_evidence_extracted": evidence_counts.get("extracted", 0),
        "evidence_with_gps": sum(meta.gps is not None for meta in evidence),
        "evidence_with_timestamp": sum(meta.taken_at is not None for meta in evidence),
        "unique_evidence_hashes": len(evidence_hash_counts),
        "evidence_extraction_failures": evidence_counts.get(
            "unexpected_failures", 0
        ),
        "duplicate_evidence_hashes": sum(
            count > 1 for count in evidence_hash_counts.values()
        ),
        "damage_scores_computed": len(damage_scores),
        "damage_score_attempts": score_attempts,
        "damage_media_skipped": damage_media_skipped,
        "verdict_counts": {
            name: verdict_counts.get(name, 0)
            for name in ("verified", "suspect", "inconclusive")
        },
        "verified_count": verdict_counts.get("verified", 0),
        "suspect_count": verdict_counts.get("suspect", 0),
        "inconclusive_count": verdict_counts.get("inconclusive", 0),
        "vision_checks": len(vision_checks),
        "vision_checks_passed": sum(check.passed is True for check in vision_checks),
        "vision_checks_failed": sum(check.passed is False for check in vision_checks),
        "vision_checks_inconclusive": sum(
            check.passed is None for check in vision_checks
        ),
        "claims_kept": summary_diagnostics["kept_claim_count"],
        "claims_dropped": summary_diagnostics["dropped_claim_count"],
        "claims_raw": summary_diagnostics["raw_claim_count"],
        "summary_fallback": summary_diagnostics["fallback_triggered"],
        "model_mode": "mock" if mock_mode else "live",
        "model_inference_attempted": not mock_mode,
        "model_inference_used": bool(
            not mock_mode
            and (
                damage_scores
                or any(check.value is not None for check in vision_checks)
                or summary_diagnostics["raw_claim_count"]
            )
        ),
        "stage_status": stage_status,
        "stages_completed": stages_completed,
        "stages_skipped": stages_skipped,
    }
    report = Report(
        hotspots=hotspots,
        verdicts=verdicts,
        claims=summary_report.claims,
        stats=report_stats,
        complaints=complaints,
    )

    complaint_ids = {complaint.id for complaint in complaints}
    if any(
        complaint_id not in complaint_ids
        for hotspot in report.hotspots
        for complaint_id in hotspot.complaint_ids
    ):
        raise PipelineError("report contains a hotspot with an unknown complaint ID")
    if any(verdict.complaint_id not in complaint_ids for verdict in report.verdicts):
        raise PipelineError("report contains a verdict with an unknown complaint ID")
    valid_citations = {
        hotspot.id for hotspot in report.hotspots
    } | {verdict.complaint_id for verdict in report.verdicts}
    if any(
        citation not in valid_citations
        for claim in report.claims
        for citation in claim.cites
    ):
        raise PipelineError("summary contains an unknown citation")
    try:
        return Report.model_validate(report.model_dump(mode="python"))
    except Exception as exc:
        raise PipelineError(
            f"assembled report failed schema validation ({type(exc).__name__})"
        ) from exc
