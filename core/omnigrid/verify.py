"""Phase 6: Proof-of-Fix deterministic and vision verification checks.

Includes deterministic geofence, duplicate pHash, and timestamp sanity checks,
integrated with vision-model closure consistency verification.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, Field

from .config import get_settings
from .evidence import (
    IMAGE_EXTENSIONS,
    compute_distance,
    compute_phash,
    extract_exif_gps,
    extract_exif_timestamp,
    phash_distance,
)
from .llm import (
    LLMAuthenticationError,
    LLMClient,
    LLMConnectionError,
    LLMError,
    LLMInvalidRequestError,
    LLMProviderError,
    LLMResponseError,
    LLMTimeoutError,
    get_llm_client,
)
from .schemas import Check, Complaint, Verdict

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]

# Map synthetic media filenames to their known defect types for defect sanity
_KNOWN_DEFECT_MAP: dict[str, str] = {
    "media_01.jpg": "pothole",
    "media_02.jpg": "streetlight",
    "media_03.jpg": "waterlogging",
    "media_04.jpg": "other",
    "media_05.jpg": "pothole",
    "media_06.jpg": "pothole",
    "media_07.jpg": "streetlight",
    "media_08.jpg": "waterlogging",
    "media_09.jpg": "other",
    "media_10.jpg": "pothole",
    "media_11.jpg": "pothole",
    "media_12.jpg": "streetlight",
    "media_13.jpg": "pothole",  # Pothole repair photo used for streetlight complaint
    "media_14.jpg": "pothole",
    "media_15.jpg": "pothole",
}

# Defect keywords in contractor or closure notes
_DEFECT_KEYWORDS: dict[str, str] = {
    "asphalt": "pothole",
    "tar": "pothole",
    "crater": "pothole",
    "pothole": "pothole",
    "streetlight": "streetlight",
    "pole": "streetlight",
    "bulb": "streetlight",
    "lamp": "streetlight",
    "drain": "waterlogging",
    "flood": "waterlogging",
    "waterlog": "waterlogging",
    "culvert": "waterlogging",
}

VISION_SYSTEM_PROMPT = (
    "You are an expert civic infrastructure quality auditor. "
    "You compare an initial complaint defect photo with a submitted closure proof "
    "photo. Evaluate whether both photos depict the same physical location and "
    "whether the reported defect appears repaired. Be conservative and objective. "
    "Respond with valid JSON only."
)

VISION_PROMPT_TEMPLATE = (
    "Compare these two civic defect images:\n"
    "Image 1: Initial complaint defect photo.\n"
    "Image 2: Submitted closure / repair proof photo.\n\n"
    "Assess:\n"
    "1. Does Image 2 plausibly depict the same location as Image 1?\n"
    "2. Does the defect visible in Image 1 appear to have been repaired in Image 2?\n"
    "3. Provide your confidence score between 0.0 (uncertain) and 1.0 (certain).\n\n"
    "Respond ONLY with a JSON object in this format:\n"
    "{\n"
    '  "same_location_likely": true/false,\n'
    '  "defect_repaired": true/false,\n'
    '  "confidence": 0.0 to 1.0,\n'
    '  "reasoning": "brief 1-2 sentence explanation"\n'
    "}"
)


# -----------------------------------------------------------------------------
# Structured Model for Vision Response
# -----------------------------------------------------------------------------


class ClosureVisionAssessment(BaseModel):
    """Structured response contract for vision model closure consistency check."""

    same_location_likely: bool
    defect_repaired: bool
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    reasoning: str | None = None


# -----------------------------------------------------------------------------
# Vision Cache
# -----------------------------------------------------------------------------


class VisionCache:
    """Persistent, fault-tolerant cache for vision closure assessments."""

    def __init__(self, cache_path: Path | str | None = None) -> None:
        if cache_path is not None:
            self.path = Path(cache_path)
        else:
            self.path = REPO_ROOT / "data" / "cache" / "vision_cache.json"

    def compute_key(
        self,
        original_bytes: bytes,
        closure_bytes: bytes,
        model_name: str,
        prompt_version: str = "v1",
    ) -> str:
        """Derive a deterministic cache key from image bytes, model, and prompt.

        Notice: Swapping original_bytes and closure_bytes produces a different key
        because comparison order (before vs after) is strictly significant.
        """
        hasher = hashlib.sha256()
        hasher.update(prompt_version.encode("utf-8"))
        hasher.update(b":model:")
        hasher.update(model_name.encode("utf-8"))
        hasher.update(b":orig:")
        hasher.update(original_bytes)
        hasher.update(b":closure:")
        hasher.update(closure_bytes)
        return hasher.hexdigest()

    def get(self, key: str) -> ClosureVisionAssessment | None:
        """Retrieve a cached assessment. Returns None on cache miss or error."""
        if not self.path.is_file():
            return None
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                return None
            entry = data.get(key)
            if entry and isinstance(entry, dict):
                return ClosureVisionAssessment.model_validate(entry)
        except Exception as exc:
            logger.warning("Failed reading vision cache %s: %s", self.path, exc)
            return None
        return None

    def set(self, key: str, assessment: ClosureVisionAssessment) -> None:
        """Persist a valid assessment. Fault-tolerant against I/O errors."""
        try:
            data: dict[str, Any] = {}
            if self.path.is_file():
                try:
                    loaded = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        data = loaded
                except Exception:
                    data = {}

            data[key] = assessment.model_dump()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed writing to vision cache %s: %s", self.path, exc)

    def clear(self) -> None:
        """Clear cache file if it exists."""
        try:
            if self.path.is_file():
                self.path.unlink()
        except Exception:
            pass


# -----------------------------------------------------------------------------
# Path and Property Helpers
# -----------------------------------------------------------------------------


def _get_prop(obj: Any, key: str, default: Any = None) -> Any:
    """Helper to retrieve attributes from either Pydantic models or dicts."""
    if hasattr(obj, key):
        val = getattr(obj, key)
        return val if val is not None else default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return default


def _resolve_media_path(
    media_path: str | Path,
    base_dir: Path | str | None = None,
    strict_paths: bool = False,
) -> Path:
    """Resolve media path against candidate directories."""
    path = Path(media_path)

    if base_dir is not None:
        root = Path(base_dir).resolve()
        try:
            candidate = (path if path.is_absolute() else root / path).resolve()
            candidate.relative_to(root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError(
                "Media path resolves outside the configured media directory"
            ) from exc

        if candidate.is_file():
            return candidate

        if strict_paths:
            return candidate
        by_name = root / path.name
        if by_name.is_file():
            return by_name
        return candidate

    if path.is_file():
        return path

    candidates = [
        Path(base_dir) if base_dir else None,
        REPO_ROOT,
        Path.cwd(),
        REPO_ROOT / "data" / "synthetic" / "media",
    ]

    for cand in candidates:
        if cand is not None:
            full = cand / path
            if full.is_file():
                return full
            by_name = cand / path.name
            if by_name.is_file():
                return by_name

    return path


# -----------------------------------------------------------------------------
# Deterministic Checks
# -----------------------------------------------------------------------------


def check_gps_geofence(
    complaint: Any,
    closure_media_path: Path | str,
    geofence_meters: float | None = None,
    media_dir: Path | str | None = None,
    strict_media_paths: bool = False,
) -> Check:
    """Check 1: Verify closure photo GPS coordinates are within geofence."""
    path = _resolve_media_path(
        closure_media_path,
        base_dir=media_dir,
        strict_paths=strict_media_paths,
    )
    threshold = (
        float(geofence_meters)
        if geofence_meters is not None
        else get_settings().geofence_meters
    )

    complaint_lat = _get_prop(complaint, "lat")
    complaint_lon = _get_prop(complaint, "lon")

    if complaint_lat is None or complaint_lon is None:
        return Check(
            name="gps_geofence",
            passed=None,
            detail="Complaint coordinates missing; geofence check inconclusive.",
            value=None,
        )

    if not path.is_file():
        return Check(
            name="gps_geofence",
            passed=None,
            detail=f"Closure media file not found: {path.name}",
            value=None,
        )

    gps = extract_exif_gps(path)
    if gps is None:
        return Check(
            name="gps_geofence",
            passed=None,
            detail="Closure media missing EXIF GPS metadata; check inconclusive.",
            value=None,
        )

    distance = compute_distance(gps, (float(complaint_lat), float(complaint_lon)))
    if distance is None:
        return Check(
            name="gps_geofence",
            passed=None,
            detail="Unable to compute great-circle distance from closure GPS.",
            value=None,
        )

    if distance <= threshold:
        return Check(
            name="gps_geofence",
            passed=True,
            detail=(
                f"Evidence is within configured geofence "
                f"({distance:.1f}m <= {threshold:.1f}m)."
            ),
            value=distance,
        )

    return Check(
        name="gps_geofence",
        passed=False,
        detail=(
            f"Closure evidence location is {distance:.1f}m away from reported "
            f"complaint (exceeds {threshold:.1f}m geofence). Needs human review."
        ),
        value=distance,
    )


def check_duplicate_evidence(
    complaint: Any,
    closure_media_path: Path | str,
    all_complaints: Sequence[Any] | None = None,
    base_dir: Path | str | None = None,
    strict_media_paths: bool = False,
) -> Check:
    """Check 2: Check for reused or duplicate closure evidence using pHash."""
    try:
        path = _resolve_media_path(
            closure_media_path,
            base_dir=base_dir,
            strict_paths=strict_media_paths,
        )
    except ValueError:
        return Check(
            name="duplicate_evidence",
            passed=None,
            detail="Closure media path is outside the configured media directory.",
            value=None,
        )
    if not path.is_file():
        return Check(
            name="duplicate_evidence",
            passed=None,
            detail="Closure media file not found for duplicate check.",
            value=None,
        )

    try:
        closure_hash = compute_phash(path)
    except Exception:
        return Check(
            name="duplicate_evidence",
            passed=None,
            detail="Could not compute perceptual hash for closure media.",
            value=None,
        )

    complaint_id = _get_prop(complaint, "id", "")

    # 1. Compare against complaint's own initial media (detect recycled ticket photo)
    init_media = _get_prop(complaint, "media", [])
    for m in init_media:
        m_path = _get_prop(m, "path")
        if m_path:
            try:
                p_init = _resolve_media_path(
                    m_path,
                    base_dir=base_dir,
                    strict_paths=strict_media_paths,
                )
            except ValueError:
                logger.warning(
                    "Ignoring initial evidence outside configured media directory."
                )
                continue
            if p_init.is_file():
                try:
                    init_hash = compute_phash(p_init)
                    h_dist = phash_distance(closure_hash, init_hash)
                    if h_dist <= 5:
                        return Check(
                            name="duplicate_evidence",
                            passed=False,
                            detail=(
                                f"Closure photo matches complaint's own initial photo "
                                f"(pHash distance {h_dist} <= 5). Recycled evidence; "
                                f"needs human review."
                            ),
                            value=float(h_dist),
                        )
                except Exception:
                    pass

    # 2. Compare against closure photos of all other closed complaints
    min_dist: int | None = None
    matched_complaint_id: str | None = None

    if all_complaints:
        for other in all_complaints:
            other_id = _get_prop(other, "id", "")
            if other_id == complaint_id:
                continue

            other_status = _get_prop(other, "status", "")
            if other_status != "closed":
                continue

            other_closure = _get_prop(other, "closure")
            if not other_closure:
                continue

            other_media = _get_prop(other_closure, "media", [])
            for om in other_media:
                om_path = _get_prop(om, "path")
                if om_path:
                    try:
                        p_other = _resolve_media_path(
                            om_path,
                            base_dir=base_dir,
                            strict_paths=strict_media_paths,
                        )
                    except ValueError:
                        logger.warning(
                            "Ignoring closure evidence outside configured media "
                            "directory for complaint %s.",
                            other_id,
                        )
                        continue
                    if p_other.is_file() and p_other.resolve() != path.resolve():
                        try:
                            o_hash = compute_phash(p_other)
                            dist = phash_distance(closure_hash, o_hash)
                            if min_dist is None or dist < min_dist:
                                min_dist = dist
                                matched_complaint_id = other_id
                        except Exception:
                            pass

    if min_dist is not None and min_dist <= 5:
        return Check(
            name="duplicate_evidence",
            passed=False,
            detail=(
                f"Closure photo duplicates evidence submitted for "
                f"{matched_complaint_id} (pHash distance {min_dist} <= 5). "
                f"Suspect duplicate submission; needs human review."
            ),
            value=float(min_dist),
        )

    return Check(
        name="duplicate_evidence",
        passed=True,
        detail="Closure evidence perceptual hash is unique across submissions.",
        value=float(min_dist) if min_dist is not None else None,
    )


def check_timestamp_sanity(
    complaint: Any,
    closure_media_path: Path | str,
    media_dir: Path | str | None = None,
    strict_media_paths: bool = False,
) -> Check:
    """Check 3: Verify closure photo capture timestamp is temporally coherent."""
    path = _resolve_media_path(
        closure_media_path,
        base_dir=media_dir,
        strict_paths=strict_media_paths,
    )
    if not path.is_file():
        return Check(
            name="timestamp_sanity",
            passed=None,
            detail="Closure media file not found for timestamp verification.",
            value=None,
        )

    taken_at = extract_exif_timestamp(path)
    if taken_at is None:
        return Check(
            name="timestamp_sanity",
            passed=None,
            detail="Closure media missing EXIF capture timestamp; check inconclusive.",
            value=None,
        )

    created_at_raw = _get_prop(complaint, "created_at")
    if created_at_raw is None:
        return Check(
            name="timestamp_sanity",
            passed=None,
            detail="Complaint creation timestamp missing.",
            value=None,
        )

    if isinstance(created_at_raw, str):
        try:
            created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
        except ValueError:
            return Check(
                name="timestamp_sanity",
                passed=None,
                detail="Invalid complaint created_at format.",
                value=None,
            )
    else:
        created_at = created_at_raw

    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    else:
        created_at = created_at.astimezone(UTC)

    diff_seconds = (taken_at - created_at).total_seconds()

    # If photo was taken more than 60 seconds before complaint creation
    if diff_seconds < -60.0:
        return Check(
            name="timestamp_sanity",
            passed=False,
            detail=(
                f"Closure photo capture time ({taken_at.isoformat()}) precedes "
                f"complaint creation ({created_at.isoformat()}) by "
                f"{abs(diff_seconds):.0f}s. Pre-existing photo; needs human review."
            ),
            value=float(diff_seconds),
        )

    return Check(
        name="timestamp_sanity",
        passed=True,
        detail="Closure photo timestamp is consistent with ticket lifecycle.",
        value=float(diff_seconds),
    )


def check_category_consistency(
    complaint: Any,
    closure_media_path: Path | str | None = None,
    defect_type: str | None = None,
    evidence_available: bool = True,
) -> Check:
    """Check 4: Verify closure defect metadata aligns with complaint category."""
    if not evidence_available:
        return Check(
            name="category_consistency",
            passed=None,
            detail="Closure evidence is missing or unreadable; check inconclusive.",
            value=None,
        )

    category = str(_get_prop(complaint, "category", "")).strip().lower()

    detected_defect = defect_type

    if not detected_defect and closure_media_path:
        fname = Path(closure_media_path).name
        detected_defect = _KNOWN_DEFECT_MAP.get(fname)

    if not detected_defect:
        closure = _get_prop(complaint, "closure")
        if closure:
            closed_by = str(_get_prop(closure, "closed_by", "")).lower()
            for kw, defect in _DEFECT_KEYWORDS.items():
                if kw in closed_by:
                    detected_defect = defect
                    break

    if not detected_defect:
        return Check(
            name="category_consistency",
            passed=True,
            detail="No conflicting defect metadata found on closure submission.",
            value=1.0,
        )

    detected_defect = detected_defect.strip().lower()

    if detected_defect != category:
        return Check(
            name="category_consistency",
            passed=False,
            detail=(
                f"Closure evidence defect '{detected_defect}' does not match "
                f"reported complaint category '{category}'. Needs human review."
            ),
            value=0.0,
        )

    return Check(
        name="category_consistency",
        passed=True,
        detail=f"Closure defect '{detected_defect}' matches complaint category.",
        value=1.0,
    )


# -----------------------------------------------------------------------------
# Vision Consistency Check (Issue #11)
# -----------------------------------------------------------------------------


def check_vision_consistency(
    original_media_path: Path | str,
    closure_media_path: Path | str,
    llm_client: LLMClient | None = None,
    cache: VisionCache | None = None,
    min_confidence: float = 0.60,
    base_dir: Path | str | None = None,
    strict_media_paths: bool = False,
) -> Check:
    """Check 5: Compare original defect and closure images via vision model."""
    try:
        p_orig = _resolve_media_path(
            original_media_path,
            base_dir=base_dir,
            strict_paths=strict_media_paths,
        )
        p_closure = _resolve_media_path(
            closure_media_path,
            base_dir=base_dir,
            strict_paths=strict_media_paths,
        )
    except ValueError:
        return Check(
            name="vision_consistency",
            passed=None,
            detail="Media path is outside the configured media directory.",
            value=None,
        )

    if not p_orig.is_file():
        return Check(
            name="vision_consistency",
            passed=None,
            detail=f"Original defect image not found: {Path(original_media_path).name}",
            value=None,
        )

    if not p_closure.is_file():
        return Check(
            name="vision_consistency",
            passed=None,
            detail=f"Closure image not found: {Path(closure_media_path).name}",
            value=None,
        )

    try:
        orig_bytes = p_orig.read_bytes()
        closure_bytes = p_closure.read_bytes()
    except OSError as exc:
        return Check(
            name="vision_consistency",
            passed=None,
            detail=f"Unable to read image evidence: {exc}",
            value=None,
        )

    client = llm_client if llm_client is not None else get_llm_client()
    vision_cache = cache if cache is not None else VisionCache()

    cache_key = vision_cache.compute_key(
        orig_bytes,
        closure_bytes,
        model_name=client.vision_cache_identity,
    )

    assessment = vision_cache.get(cache_key)

    if assessment is None:
        try:
            assessment = client.compare_images_structured(
                image1=p_orig,
                image2=p_closure,
                prompt=VISION_PROMPT_TEMPLATE,
                response_model=ClosureVisionAssessment,
                system_prompt=VISION_SYSTEM_PROMPT,
            )
        except (
            LLMTimeoutError,
            LLMConnectionError,
            LLMProviderError,
            LLMAuthenticationError,
            LLMInvalidRequestError,
        ) as exc:
            return Check(
                name="vision_consistency",
                passed=None,
                detail=f"Vision check unavailable ({type(exc).__name__}): {exc}",
                value=None,
            )
        except LLMResponseError as exc:
            return Check(
                name="vision_consistency",
                passed=None,
                detail=f"Vision model response invalid: {exc}",
                value=None,
            )
        except LLMError as exc:
            return Check(
                name="vision_consistency",
                passed=None,
                detail=f"Vision model check failed: {exc}",
                value=None,
            )
        except Exception as exc:
            return Check(
                name="vision_consistency",
                passed=None,
                detail=f"Vision check encountered an unexpected error: {exc}",
                value=None,
            )

        # Cache only valid assessments
        vision_cache.set(cache_key, assessment)

    conf = assessment.confidence
    if not math.isfinite(conf) or conf < 0.0 or conf > 1.0:
        return Check(
            name="vision_consistency",
            passed=None,
            detail=f"Invalid confidence value {conf}; check inconclusive.",
            value=None,
        )

    if conf < min_confidence:
        return Check(
            name="vision_consistency",
            passed=None,
            detail=(
                f"Vision model assessment inconclusive due to low confidence "
                f"({conf:.2f} < {min_confidence:.2f})."
            ),
            value=float(conf),
        )

    if not assessment.same_location_likely:
        return Check(
            name="vision_consistency",
            passed=False,
            detail=(
                f"Closure photo is unlikely to depict the same location as original "
                f"complaint (confidence: {conf:.2f}). Needs human review."
            ),
            value=float(conf),
        )

    if not assessment.defect_repaired:
        return Check(
            name="vision_consistency",
            passed=False,
            detail=(
                f"Vision model indicates reported defect was not repaired in closure "
                f"evidence (confidence: {conf:.2f}). Needs human review."
            ),
            value=float(conf),
        )

    return Check(
        name="vision_consistency",
        passed=True,
        detail=(
            f"Vision model confirms defect repaired at matching location "
            f"(confidence: {conf:.2f})."
        ),
        value=float(conf),
    )


# -----------------------------------------------------------------------------
# Proof-of-Fix Verdict Generation
# -----------------------------------------------------------------------------


def verify_closure(
    complaint: Complaint | dict[str, Any],
    all_complaints: Sequence[Complaint | dict[str, Any]] | None = None,
    media_dir: Path | str | None = None,
    geofence_meters: float | None = None,
    llm_client: LLMClient | None = None,
    use_vision: bool = False,
    cache: VisionCache | None = None,
    strict_media_paths: bool = False,
) -> Verdict:
    """Run deterministic and optional vision Proof-of-Fix checks on a complaint."""
    cid = _get_prop(complaint, "id", "unknown")
    status = _get_prop(complaint, "status", "")

    # Non-closed complaints are inconclusive
    if status != "closed":
        return Verdict(
            complaint_id=cid,
            verdict="inconclusive",
            checks=[
                Check(
                    name="status_check",
                    passed=None,
                    detail="Complaint status is not closed.",
                    value=None,
                )
            ],
            confidence=0.0,
        )

    closure = _get_prop(complaint, "closure")
    if not closure:
        return Verdict(
            complaint_id=cid,
            verdict="inconclusive",
            checks=[
                Check(
                    name="closure_metadata",
                    passed=None,
                    detail="Closed complaint lacks closure metadata block.",
                    value=None,
                )
            ],
            confidence=0.20,
        )

    media_list = _get_prop(closure, "media", [])
    if not media_list:
        return Verdict(
            complaint_id=cid,
            verdict="inconclusive",
            checks=[
                Check(
                    name="evidence_present",
                    passed=None,
                    detail="Closed complaint has no closure evidence media attached.",
                    value=None,
                )
            ],
            confidence=0.30,
        )

    # Use first closure media file
    primary_media = media_list[0]
    mpath = _get_prop(primary_media, "path", "")
    media_root = Path(media_dir) if media_dir is not None else REPO_ROOT
    try:
        resolved_path = _resolve_media_path(
            mpath,
            base_dir=media_root,
            strict_paths=strict_media_paths,
        )
    except ValueError:
        logger.warning(
            "Rejected closure media outside configured media directory for %s.", cid
        )
        return Verdict(
            complaint_id=cid,
            verdict="inconclusive",
            checks=[
                Check(
                    name="evidence_path",
                    passed=None,
                    detail=(
                        "Closure media path is outside the configured media "
                        "directory."
                    ),
                    value=None,
                )
            ],
            confidence=0.20,
        )

    if not resolved_path.is_file():
        return Verdict(
            complaint_id=cid,
            verdict="inconclusive",
            checks=[
                Check(
                    name="evidence_path",
                    passed=None,
                    detail=(
                        "Closure media file is missing; verification is "
                        "inconclusive."
                    ),
                    value=None,
                )
            ],
            confidence=0.20,
        )

    evidence_available = False
    if resolved_path.suffix.lower() in IMAGE_EXTENSIONS:
        try:
            compute_phash(resolved_path)
            evidence_available = True
        except Exception:
            logger.warning("Closure image is unreadable; verification is inconclusive.")

    # Execute deterministic checks
    c_geo = check_gps_geofence(
        complaint,
        resolved_path,
        geofence_meters=geofence_meters,
        media_dir=media_root,
        strict_media_paths=strict_media_paths,
    )
    c_dup = check_duplicate_evidence(
        complaint,
        resolved_path,
        all_complaints=all_complaints,
        base_dir=media_root,
        strict_media_paths=strict_media_paths,
    )
    c_time = check_timestamp_sanity(
        complaint,
        resolved_path,
        media_dir=media_root,
        strict_media_paths=strict_media_paths,
    )
    c_cat = check_category_consistency(
        complaint,
        closure_media_path=resolved_path,
        evidence_available=evidence_available,
    )

    deterministic_checks = [c_geo, c_dup, c_time, c_cat]
    checks = list(deterministic_checks)

    # Execute vision check if requested
    if use_vision:
        init_media = _get_prop(complaint, "media", [])
        orig_path = None
        for m in init_media:
            p = _get_prop(m, "path")
            if p:
                orig_path = p
                break

        if orig_path and evidence_available:
            c_vis = check_vision_consistency(
                orig_path,
                resolved_path,
                llm_client=llm_client,
                cache=cache,
                base_dir=media_root,
                strict_media_paths=strict_media_paths,
            )
        else:
            c_vis = Check(
                name="vision_consistency",
                passed=None,
                detail=(
                    "Closure evidence cannot be decoded as an image; vision "
                    "comparison is inconclusive."
                    if not evidence_available
                    else "Complaint lacks initial defect media for vision comparison."
                ),
                value=None,
            )
        checks.append(c_vis)

    # Deterministic checks take strict precedence:
    # Any check failed -> "suspect" immediately
    failed = [c for c in checks if c.passed is False]
    passed = [c for c in checks if c.passed is True]

    if failed:
        verdict = "suspect"
        conf = round(min(1.0, 0.80 + 0.20 * (len(failed) / len(checks))), 2)
    elif all(c.passed is True for c in checks):
        verdict = "verified"
        conf = round(len(passed) / len(checks), 2)
    else:
        # Missing data, low confidence, or inconclusive check without explicit failure
        verdict = "inconclusive"
        conf = round(len(passed) / len(checks), 2)

    return Verdict(
        complaint_id=cid,
        verdict=verdict,
        checks=checks,
        confidence=conf,
    )


def verify_closed_complaints(
    geojson_path_or_dict: Path | str | dict[str, Any] | Sequence[Any],
    media_dir: Path | str | None = None,
    geofence_meters: float | None = None,
    llm_client: LLMClient | None = None,
    use_vision: bool = False,
    cache: VisionCache | None = None,
    strict_media_paths: bool = False,
) -> list[Verdict]:
    """Batch verify all closed complaints within a dataset."""
    complaints: list[Any] = []

    if isinstance(geojson_path_or_dict, (str, Path)):
        p = Path(geojson_path_or_dict)
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("type") == "FeatureCollection":
            complaints = [f["properties"] for f in data.get("features", [])]
        elif isinstance(data, list):
            complaints = data
    elif isinstance(geojson_path_or_dict, dict):
        if geojson_path_or_dict.get("type") == "FeatureCollection":
            features = geojson_path_or_dict.get("features", [])
            complaints = [f["properties"] for f in features]
        else:
            complaints = [geojson_path_or_dict]
    elif isinstance(geojson_path_or_dict, (list, tuple)):
        complaints = list(geojson_path_or_dict)

    closed = [c for c in complaints if _get_prop(c, "status") == "closed"]

    verdicts: list[Verdict] = []
    for c in closed:
        v = verify_closure(
            complaint=c,
            all_complaints=complaints,
            media_dir=media_dir,
            geofence_meters=geofence_meters,
            llm_client=llm_client,
            use_vision=use_vision,
            cache=cache,
            strict_media_paths=strict_media_paths,
        )
        verdicts.append(v)

    return verdicts
