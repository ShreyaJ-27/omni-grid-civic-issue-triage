"""Phase 8: Structured summary generation with citation and numeric validation.

Generates machine-readable, human-readable civic complaint summaries from
pre-computed facts (counts, severity, distances, etc.) supplied as JSON.
The LLM is never asked to calculate or compute numerical facts.
"""

from __future__ import annotations

import json
import logging
import math
import re
from typing import Any

from core.omnigrid.llm import LLMClient, LLMError, get_llm_client
from core.omnigrid.schemas import Claim, Hotspot, Report, Verdict
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version tag embedded in prompt — changing this invalidates stale caches
# ---------------------------------------------------------------------------
SUMMARY_PROMPT_VERSION = "v1"

# ---------------------------------------------------------------------------
# System prompt: strict instructions around faithfulness and JSON output
# ---------------------------------------------------------------------------

SUMMARY_SYSTEM_PROMPT = (
    "You are an expert civic intelligence analyst preparing structured reports "
    "for local government officials. You summarise provided pre-computed "
    "statistics about civic complaints into clear, concise bullet-point claims. "
    "\n\n"
    "STRICT RULES:\n"
    "1. You MUST NOT calculate, derive, or invent any numerical figures. "
    "All numbers are given to you in the 'facts' JSON. Use them verbatim.\n"
    "2. Each claim MUST cite only IDs that appear in the 'valid_ids' list.\n"
    "3. Claims with no supporting ID should have an empty 'cites' list.\n"
    "4. Respond with ONLY a valid JSON object matching the schema — no prose.\n"
    "\n"
    "Response schema:\n"
    '{"claims": [{"text": "...", "cites": ["id1", "id2"]}]}'
)

# ---------------------------------------------------------------------------
# Structured LLM response model
# ---------------------------------------------------------------------------


class _SummaryResponse(BaseModel):
    claims: list[Claim] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _extract_numbers(text: str, ignore_strings: set[str] | None = None) -> list[float]:
    """Return all standalone numbers found in text, ignoring specified strings."""
    cleaned_text = text
    if ignore_strings:
        for s in ignore_strings:
            if s:
                cleaned_text = cleaned_text.replace(s, " ")
    # Match standalone numeric tokens (integers and floats)
    matches = re.findall(
        r"(?<![a-zA-Z0-9_\-])(\d+(?:\.\d+)?)(?![a-zA-Z0-9_\-])",
        cleaned_text,
    )
    nums: list[float] = []
    for m in matches:
        try:
            val = float(m)
            if math.isfinite(val):
                nums.append(val)
        except ValueError:
            pass
    return nums


def validate_citations(
    claims: list[Claim],
    valid_ids: set[str],
) -> tuple[list[Claim], list[Claim], list[str]]:
    """Filter out claims that reference IDs not in *valid_ids*.

    Returns (kept, dropped, rejection_reasons).
    """
    kept: list[Claim] = []
    dropped: list[Claim] = []
    reasons: list[str] = []
    for claim in claims:
        unknown = [cid for cid in claim.cites if cid not in valid_ids]
        if unknown:
            reason = f"Unknown citation IDs {unknown} in claim: {claim.text[:80]}"
            logger.info("Dropping claim: %s", reason)
            dropped.append(claim)
            reasons.append(reason)
        else:
            kept.append(claim)
    return kept, dropped, reasons


def validate_numeric_claims(
    claims: list[Claim],
    facts: dict[str, Any],
    valid_ids: set[str] | None = None,
) -> tuple[list[Claim], list[Claim], list[str]]:
    """Filter out claims that contain numbers not found in *facts* values.

    A claim is kept if every number it mentions appears somewhere in the
    flattened numeric values of *facts*. Claims with no numbers are always kept.
    Returns (kept, dropped, rejection_reasons).
    """
    fact_numbers: set[float] = set()
    _collect_numbers(facts, fact_numbers)

    ignore_strings: set[str] = set(valid_ids or [])

    kept: list[Claim] = []
    dropped: list[Claim] = []
    reasons: list[str] = []
    for claim in claims:
        # Also ignore any cited IDs for this claim so digits in IDs aren't extracted
        claim_ignore = ignore_strings | set(claim.cites)
        nums = _extract_numbers(claim.text, ignore_strings=claim_ignore)
        if not nums:
            kept.append(claim)
            continue
        unsupported = [n for n in nums if not _number_in_facts(n, fact_numbers)]
        if unsupported:
            reason = (
                f"Unsupported numeric claims {unsupported} in claim: {claim.text[:80]}"
            )
            logger.info("Dropping claim: %s", reason)
            dropped.append(claim)
            reasons.append(reason)
        else:
            kept.append(claim)
    return kept, dropped, reasons


def _collect_numbers(obj: Any, result: set[float]) -> None:
    """Recursively collect all numeric values from a nested structure."""
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        f = float(obj)
        if math.isfinite(f):
            result.add(f)
            # If float in [0.0, 1.0], also include percentage representation e.g. 50.0
            if 0.0 <= f <= 1.0:
                result.add(round(f * 100.0, 4))
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers(v, result)
    elif isinstance(obj, (list, tuple, set)):
        for item in obj:
            _collect_numbers(item, result)


def _number_in_facts(n: float, fact_numbers: set[float]) -> bool:
    """Return True if *n* matches any fact number within a small tolerance."""
    for fn in fact_numbers:
        if abs(fn - n) < 1e-4 or (fn != 0 and abs((fn - n) / fn) < 1e-4):
            return True
    return False


# ---------------------------------------------------------------------------
# Fallback deterministic summary builder
# ---------------------------------------------------------------------------


def _build_fallback_claims(
    hotspots: list[Hotspot],
    verdicts: list[Verdict],
    facts: dict[str, Any],
) -> list[Claim]:
    """Build safe, deterministic claims directly from trusted facts (no LLM)."""
    claims: list[Claim] = []

    total_complaints = facts.get("total_complaints", 0)
    claims.append(
        Claim(
            text=f"There are {int(total_complaints)} civic complaints in the dataset.",
            cites=[],
        )
    )

    total_hotspots = facts.get("total_hotspots", len(hotspots))
    claims.append(
        Claim(
            text=(
                f"{int(total_hotspots)} geographic hotspots were identified by "
                "spatial clustering."
            ),
            cites=[],
        )
    )

    if hotspots:
        top = max(hotspots, key=lambda h: h.severity)
        claims.append(
            Claim(
                text=(
                    f"The highest-severity hotspot ({top.id}) has a severity score "
                    f"of {top.severity:.2f}."
                ),
                cites=[top.id],
            )
        )

    if verdicts:
        suspect_count = facts.get(
            "suspect_count",
            sum(1 for v in verdicts if v.verdict == "suspect"),
        )
        verified_count = facts.get(
            "verified_count",
            sum(1 for v in verdicts if v.verdict == "verified"),
        )
        claims.append(
            Claim(
                text=(
                    f"{int(suspect_count)} closed complaints were flagged as suspect "
                    "by verification checks."
                ),
                cites=[],
            )
        )
        claims.append(
            Claim(
                text=(
                    f"{int(verified_count)} closed complaints were verified as genuine."
                ),
                cites=[],
            )
        )

    return claims


# ---------------------------------------------------------------------------
# Main summary generation entry point
# ---------------------------------------------------------------------------


def generate_summary(
    hotspots: list[Hotspot],
    verdicts: list[Verdict],
    llm_client: LLMClient | None = None,
    extra_facts: dict[str, Any] | None = None,
    mock: bool | None = None,
) -> tuple[Report, dict[str, Any]]:
    """Generate a structured, citation-validated summary Report.

    Facts are derived from the provided hotspots and verdicts and merged with
    any *extra_facts* provided. The LLM is instructed to use only these
    pre-computed values.

    Validation:
    - Citations are checked against known hotspot/verdict IDs.
    - Numbers in claims are checked against the supplied facts dict.
    - One retry is attempted on LLM failure; deterministic fallback is used
      if the retry also fails.

    Returns:
        (report, diagnostics) where diagnostics is a dict with kept/dropped
        claim counts and whether the fallback was triggered.
    """
    client = llm_client or get_llm_client(mock=mock)

    # Build pre-computed facts — LLM must never calculate these
    suspect_ids = [v.complaint_id for v in verdicts if v.verdict == "suspect"]
    verified_ids = [v.complaint_id for v in verdicts if v.verdict == "verified"]
    category_totals: dict[str, int] = {}
    for h in hotspots:
        for cat, cnt in h.category_mix.items():
            category_totals[cat] = category_totals.get(cat, 0) + cnt

    facts: dict[str, Any] = {
        "total_hotspots": len(hotspots),
        "total_verdicts": len(verdicts),
        "suspect_count": len(suspect_ids),
        "verified_count": len(verified_ids),
        "inconclusive_count": sum(1 for v in verdicts if v.verdict == "inconclusive"),
        "category_totals": category_totals,
        "hotspot_severities": {h.id: h.severity for h in hotspots},
        "hotspot_complaint_counts": {h.id: len(h.complaint_ids) for h in hotspots},
    }
    if extra_facts:
        facts.update(extra_facts)

    # total_complaints may come from extra_facts or be computed from hotspots
    if "total_complaints" not in facts:
        all_ids: set[str] = set()
        for h in hotspots:
            all_ids.update(h.complaint_ids)
        facts["total_complaints"] = len(all_ids)

    valid_ids: set[str] = {h.id for h in hotspots} | {v.complaint_id for v in verdicts}

    prompt = (
        f"Summarise the following civic complaint triage results. "
        f"Generate 3 to 6 concise, factual claims in the JSON schema shown. "
        f"Use ONLY the numbers from 'facts'. Cite ONLY IDs from 'valid_ids'.\n\n"
        f"valid_ids: {json.dumps(sorted(valid_ids))}\n\n"
        f"facts: {json.dumps(facts, default=str)}"
    )

    # Try to get LLM claims with one retry
    fallback_triggered = False
    raw_claims: list[Claim] = []
    try:
        response = client.generate_structured(
            prompt=prompt,
            response_model=_SummaryResponse,
            system_prompt=SUMMARY_SYSTEM_PROMPT,
            temperature=0.0,
        )
        raw_claims = response.claims
    except (LLMError, Exception) as exc:
        logger.warning(
            "LLM summary attempt 1 failed (%s: %s); retrying once...",
            type(exc).__name__,
            exc,
        )
        try:
            response = client.generate_structured(
                prompt=prompt,
                response_model=_SummaryResponse,
                system_prompt=SUMMARY_SYSTEM_PROMPT,
                temperature=0.0,
            )
            raw_claims = response.claims
        except (LLMError, Exception) as exc2:
            logger.warning(
                "LLM summary attempt 2 also failed (%s: %s); using fallback.",
                type(exc2).__name__,
                exc2,
            )
            fallback_triggered = True

    if fallback_triggered:
        claims = _build_fallback_claims(hotspots, verdicts, facts)
        diagnostics: dict[str, Any] = {
            "raw_claim_count": 0,
            "kept_claim_count": len(claims),
            "dropped_claim_count": 0,
            "dropped_bad_citation": 0,
            "dropped_bad_numbers": 0,
            "rejection_reasons": ["Provider failure triggered deterministic fallback."],
            "fallback_triggered": True,
        }
        report = Report(
            hotspots=hotspots,
            verdicts=verdicts,
            claims=claims,
            stats={
                **facts,
                "claims_kept": len(claims),
                "claims_dropped": 0,
                "summary_prompt_version": SUMMARY_PROMPT_VERSION,
                "summary_fallback": True,
            },
        )
        return report, diagnostics

    # Validation pass 1: citation check
    kept_citation, dropped_citation, reasons_citation = validate_citations(
        raw_claims, valid_ids
    )

    # Validation pass 2: numeric claim check
    kept_numeric, dropped_numeric, reasons_numeric = validate_numeric_claims(
        kept_citation, facts, valid_ids=valid_ids
    )

    final_claims = kept_numeric
    all_reasons = reasons_citation + reasons_numeric

    if not final_claims:
        # All LLM claims were dropped — use fallback
        logger.warning(
            "All LLM claims were filtered out; falling back to deterministic summary."
        )
        final_claims = _build_fallback_claims(hotspots, verdicts, facts)
        fallback_triggered = True

    diagnostics = {
        "raw_claim_count": len(raw_claims),
        "kept_claim_count": len(final_claims),
        "dropped_claim_count": len(dropped_citation) + len(dropped_numeric),
        "dropped_bad_citation": len(dropped_citation),
        "dropped_bad_numbers": len(dropped_numeric),
        "rejection_reasons": all_reasons,
        "fallback_triggered": fallback_triggered,
    }

    report = Report(
        hotspots=hotspots,
        verdicts=verdicts,
        claims=final_claims,
        stats={
            **facts,
            "claims_kept": len(final_claims),
            "claims_dropped": len(dropped_citation) + len(dropped_numeric),
            "claims_raw": len(raw_claims),
            "summary_prompt_version": SUMMARY_PROMPT_VERSION,
            "summary_fallback": fallback_triggered,
        },
    )
    return report, diagnostics
