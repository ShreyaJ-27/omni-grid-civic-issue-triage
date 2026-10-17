"""Phase 9: Vision-based damage severity scoring for civic complaint photos.

Scores the visible damage severity of a complaint photo on a [0.0, 1.0] scale
using the configured vision LLM. Results are cached to disk to avoid redundant
API calls. Falls back to None (not 0.0) if the image is unavailable or the
model call fails, leaving the caller to apply the fallback policy.

Integration with cluster.py:
    The returned float score can be attached to complaint objects via the
    ``vlm_damage_score`` attribute, which ``_extract_vlm_damage_score()`` in
    cluster.py already reads. Alternatively, callers may pass a
    ``damage_scores`` dict to ``compute_severity_score_with_damage()`` to
    inject scores without modifying complaint objects.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator

from .llm import LLMClient, LLMError, get_llm_client

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]

# Bump this string whenever the damage prompt changes to invalidate stale cache
DAMAGE_PROMPT_VERSION = "v1"

DAMAGE_SYSTEM_PROMPT = (
    "You are an expert civil-engineering damage assessor evaluating civic "
    "infrastructure complaint photos submitted by citizens. "
    "You assess the severity of visible damage shown in the photo on a scale from "
    "0.0 (no visible damage) to 1.0 (catastrophic / impassable damage). "
    "Be objective and conservative. Respond with ONLY valid JSON."
)

DAMAGE_PROMPT = (
    "Assess the severity of civic infrastructure damage visible in this "
    "complaint photo.\n\n"
    "Rate on a continuous scale from 0.0 to 1.0:\n"
    "  0.0 — No damage visible\n"
    "  0.25 — Minor cosmetic damage (small crack, slight discoloration)\n"
    "  0.50 — Moderate damage (significant crack, partial blockage)\n"
    "  0.75 — Severe damage (large pothole, major flooding, broken fixture)\n"
    "  1.0  — Critical / catastrophic damage (impassable, safety hazard)\n\n"
    "Respond ONLY with this JSON object:\n"
    '{"score": <float 0.0-1.0>, "reasoning": "<1-2 sentence explanation>"}'
)


# ---------------------------------------------------------------------------
# Structured model for vision response
# ---------------------------------------------------------------------------


class DamageSeverityScore(BaseModel):
    """Structured output from the damage severity vision model."""

    score: Annotated[float, Field(ge=0.0, le=1.0)]
    reasoning: str
    evidence_id: str = ""

    @field_validator("score", mode="before")
    @classmethod
    def _validate_score(cls, v: Any) -> float:
        if isinstance(v, bool):
            raise ValueError("Score cannot be a boolean value.")
        try:
            f = float(v)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Score must be numeric, got {v!r}") from exc
        if not math.isfinite(f):
            raise ValueError(f"Score must be a finite number, got {f}")
        if f < 0.0 or f > 1.0:
            raise ValueError(f"Score must be between 0.0 and 1.0, got {f}")
        return f


# ---------------------------------------------------------------------------
# Damage severity cache
# ---------------------------------------------------------------------------


class DamageSeverityCache:
    """Persistent, fault-tolerant cache for damage severity scores.

    Uses the same design pattern as VisionCache in verify.py.
    Default cache path: <repo_root>/data/cache/damage_cache.json
    """

    def __init__(self, cache_path: Path | str | None = None) -> None:
        if cache_path is not None:
            self.path = Path(cache_path)
        else:
            self.path = REPO_ROOT / "data" / "cache" / "damage_cache.json"

    def compute_key(
        self,
        image_bytes: bytes,
        model_name: str,
        prompt_version: str = DAMAGE_PROMPT_VERSION,
    ) -> str:
        """Derive a deterministic cache key from image bytes, model, and prompt."""
        hasher = hashlib.sha256()
        hasher.update(prompt_version.encode("utf-8"))
        hasher.update(b":model:")
        hasher.update(model_name.encode("utf-8"))
        hasher.update(b":image:")
        hasher.update(image_bytes)
        return hasher.hexdigest()

    def get(self, key: str) -> DamageSeverityScore | None:
        """Retrieve a cached score. Returns None on cache miss or any error."""
        if not self.path.is_file():
            return None
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                return None
            entry = data.get(key)
            if entry and isinstance(entry, dict):
                return DamageSeverityScore.model_validate(entry)
        except Exception as exc:
            logger.warning("Failed reading damage cache %s: %s", self.path, exc)
            return None
        return None

    def set(self, key: str, score: DamageSeverityScore) -> None:
        """Persist a score. Fault-tolerant against I/O errors."""
        try:
            data: dict[str, Any] = {}
            if self.path.is_file():
                try:
                    loaded = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        data = loaded
                except Exception:
                    data = {}

            data[key] = score.model_dump()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed writing to damage cache %s: %s", self.path, exc)

    def clear(self) -> None:
        """Remove the cache file if it exists."""
        try:
            if self.path.is_file():
                self.path.unlink()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Scoring entry point
# ---------------------------------------------------------------------------


def score_damage_severity(
    image_path: str | Path,
    evidence_id: str = "",
    llm_client: LLMClient | None = None,
    cache: DamageSeverityCache | None = None,
    mock: bool | None = None,
) -> DamageSeverityScore | None:
    """Score the damage severity of a single complaint photo.

    Args:
        image_path: Absolute or relative path to the image file.
        evidence_id: Optional identifier attached to the returned score.
        llm_client: LLMClient instance. If None, one is constructed from env.
        cache: DamageSeverityCache instance. If None, a default one is created.
        mock: Override mock mode. If None, respects LLM_MOCK env var.

    Returns:
        DamageSeverityScore with score in [0.0, 1.0], or None on any failure.
        Callers should treat None as "no score available" and apply their own
        fallback (typically 0.0 for the severity formula).
    """
    path = Path(image_path)
    if not path.is_file():
        logger.warning("Damage severity: image not found: %s", path)
        return None

    client = llm_client or get_llm_client(mock=mock)
    score_cache = cache or DamageSeverityCache()

    # Read image bytes once for cache key computation and the LLM call
    try:
        image_bytes = path.read_bytes()
    except OSError as exc:
        logger.warning("Damage severity: cannot read image %s: %s", path, exc)
        return None

    cache_key = score_cache.compute_key(image_bytes, client.vision_cache_identity)

    # Cache hit?
    cached = score_cache.get(cache_key)
    if cached is not None:
        logger.debug("Damage severity cache hit for %s", path.name)
        if evidence_id:
            cached = DamageSeverityScore(
                score=cached.score,
                reasoning=cached.reasoning,
                evidence_id=evidence_id,
            )
        return cached

    # Attempt LLM vision call
    try:
        result = client.generate_structured(
            prompt=DAMAGE_PROMPT,
            response_model=DamageSeverityScore,
            system_prompt=DAMAGE_SYSTEM_PROMPT,
            images=[path],
            temperature=0.0,
        )
        # Attach evidence_id (the model field may be empty from LLM output)
        if evidence_id and not result.evidence_id:
            result = DamageSeverityScore(
                score=result.score,
                reasoning=result.reasoning,
                evidence_id=evidence_id,
            )
    except LLMError as exc:
        logger.warning(
            "Damage severity LLM call failed for %s: %s: %s",
            path.name,
            type(exc).__name__,
            exc,
        )
        return None
    except Exception as exc:
        logger.warning(
            "Damage severity unexpected failure for %s: %s",
            path.name,
            exc,
        )
        return None

    # Persist to cache
    score_cache.set(cache_key, result)
    return result


# ---------------------------------------------------------------------------
# Batch scoring helper
# ---------------------------------------------------------------------------


def score_damage_severity_batch(
    image_paths: list[str | Path],
    evidence_ids: list[str] | None = None,
    llm_client: LLMClient | None = None,
    cache: DamageSeverityCache | None = None,
    mock: bool | None = None,
) -> dict[str, DamageSeverityScore | None]:
    """Score damage severity for a batch of images.

    Args:
        image_paths: List of image file paths.
        evidence_ids: Optional parallel list of evidence IDs.
        llm_client: Shared LLMClient (constructed once for the batch).
        cache: Shared DamageSeverityCache instance.
        mock: Override mock mode.

    Returns:
        Dict mapping str(image_path) -> DamageSeverityScore or None.
    """
    client = llm_client or get_llm_client(mock=mock)
    score_cache = cache or DamageSeverityCache()
    ids = evidence_ids or [""] * len(image_paths)
    results: dict[str, DamageSeverityScore | None] = {}
    for path, eid in zip(image_paths, ids):
        key = str(path)
        results[key] = score_damage_severity(
            path,
            evidence_id=eid,
            llm_client=client,
            cache=score_cache,
        )
    return results


# ---------------------------------------------------------------------------
# Cluster integration helper
# ---------------------------------------------------------------------------


def compute_severity_score_with_damage(
    complaints: list[Any],
    damage_scores: dict[str, float],
    reference_time: Any = None,
) -> float:
    """Compute severity score after injecting external damage scores.

    This is a thin wrapper that temporarily attaches ``vlm_damage_score``
    to complaint dicts (or wraps them) before calling cluster.compute_severity_score,
    so the formula picks up the vision scores without modifying complaint objects.

    Args:
        complaints: List of complaint objects or dicts.
        damage_scores: Mapping of complaint_id -> float score in [0, 1].
        reference_time: Optional reference time for recency decay.

    Returns:
        Composite severity float in [0.0, 10.0].
    """
    from .cluster import compute_severity_score

    if not damage_scores:
        return compute_severity_score(complaints, reference_time=reference_time)

    # Wrap each complaint as a dict with vlm_damage_score injected
    wrapped: list[dict[str, Any]] = []
    for c in complaints:
        if hasattr(c, "model_dump"):
            d = c.model_dump()
        elif isinstance(c, dict):
            d = dict(c)
        else:
            d = {"_original": c}

        # Determine complaint ID
        cid = (
            d.get("id")
            or (getattr(c, "id", None))
            or ""
        )
        if cid in damage_scores:
            d["vlm_damage_score"] = damage_scores[cid]
        wrapped.append(d)

    return compute_severity_score(wrapped, reference_time=reference_time)
