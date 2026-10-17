"""Tests for Phase 8: Structured summary generation and citation/numeric validation.

Covers all 10 acceptance criteria for Issue #13.
"""

from __future__ import annotations

from typing import Any

import pytest
from core.omnigrid.llm import LLMClient, LLMResponseError, LLMTimeoutError
from core.omnigrid.schemas import Check, Claim, Hotspot, Report, Verdict
from core.omnigrid.summarize import (
    _SummaryResponse,
    generate_summary,
    validate_citations,
    validate_numeric_claims,
)


@pytest.fixture
def sample_hotspots() -> list[Hotspot]:
    return [
        Hotspot(
            id="hotspot-12.3400-56.7800",
            centroid=(12.34, 56.78),
            radius_m=45.2,
            complaint_ids=["c-001", "c-002", "c-003"],
            category_mix={"pothole": 2, "streetlight": 1},
            severity=7.50,
        ),
        Hotspot(
            id="hotspot-12.3500-56.7900",
            centroid=(12.35, 56.79),
            radius_m=20.0,
            complaint_ids=["c-004", "c-005"],
            category_mix={"waterlogging": 2},
            severity=4.20,
        ),
    ]


@pytest.fixture
def sample_verdicts() -> list[Verdict]:
    return [
        Verdict(
            complaint_id="c-001",
            verdict="verified",
            confidence=0.95,
            checks=[Check(name="geofence", passed=True, detail="Within 10m")],
        ),
        Verdict(
            complaint_id="c-002",
            verdict="suspect",
            confidence=0.85,
            checks=[
                Check(
                    name="phash_duplicate",
                    passed=False,
                    detail="Duplicate image",
                )
            ],
        ),
    ]


@pytest.fixture
def sample_facts() -> dict[str, Any]:
    return {
        "total_complaints": 5,
        "total_hotspots": 2,
        "total_verdicts": 2,
        "suspect_count": 1,
        "verified_count": 1,
        "hotspot_severities": {
            "hotspot-12.3400-56.7800": 7.50,
            "hotspot-12.3500-56.7900": 4.20,
        },
        "hotspot_complaint_counts": {
            "hotspot-12.3400-56.7800": 3,
            "hotspot-12.3500-56.7900": 2,
        },
    }


# ---------------------------------------------------------------------------
# Acceptance Criterion 1: Valid claims with known citations & numbers retained
# ---------------------------------------------------------------------------


def test_valid_claims_retained(sample_hotspots, sample_verdicts, sample_facts):
    valid_ids = {"hotspot-12.3400-56.7800", "c-001"}
    claims = [
        Claim(
            text="Hotspot hotspot-12.3400-56.7800 has 3 complaints with severity 7.50.",
            cites=["hotspot-12.3400-56.7800"],
        ),
        Claim(
            text="Complaint c-001 was verified genuine.",
            cites=["c-001"],
        ),
    ]

    kept_cites, dropped_cites, _ = validate_citations(claims, valid_ids)
    assert len(kept_cites) == 2
    assert len(dropped_cites) == 0

    kept_nums, dropped_nums, _ = validate_numeric_claims(
        kept_cites, sample_facts, valid_ids
    )
    assert len(kept_nums) == 2
    assert len(dropped_nums) == 0


# ---------------------------------------------------------------------------
# Acceptance Criterion 2: Unknown citation IDs cause claims to be rejected
# ---------------------------------------------------------------------------


def test_unknown_citation_ids_rejected():
    valid_ids = {"hotspot-1", "complaint-100"}
    claims = [
        Claim(text="Hotspot 1 has complaints.", cites=["hotspot-1"]),
        Claim(text="Hotspot fake has complaints.", cites=["hotspot-fake-999"]),
        Claim(text="Both hotspots mentioned.", cites=["hotspot-1", "fabricated-id"]),
    ]

    kept, dropped, reasons = validate_citations(claims, valid_ids)
    assert len(kept) == 1
    assert kept[0].cites == ["hotspot-1"]
    assert len(dropped) == 2
    assert any("hotspot-fake-999" in r for r in reasons)
    assert any("fabricated-id" in r for r in reasons)


# ---------------------------------------------------------------------------
# Acceptance Criterion 3: Fabricated counts, percentages, distances, scores rejected
# ---------------------------------------------------------------------------


def test_unsupported_numerical_claims_rejected(sample_facts):
    valid_ids = {"hotspot-1"}
    # Facts has total_complaints=5, severity=7.50
    claims = [
        Claim(text="There are 5 total complaints.", cites=[]),  # supported
        Claim(text="There are 999 total complaints.", cites=[]),  # fabricated count
        Claim(text="Hotspot severity is 9.99.", cites=[]),  # fabricated score
        Claim(text="Damage occurred over 5000 meters.", cites=[]),
        Claim(text="99% of complaints are potholes.", cites=[]),
    ]

    kept, dropped, reasons = validate_numeric_claims(claims, sample_facts, valid_ids)
    assert len(kept) == 1
    assert kept[0].text == "There are 5 total complaints."
    assert len(dropped) == 4
    assert len(reasons) == 4


def test_avoid_naive_substring_matching():
    """Ensure that 2 is not accepted just because 25 or 12.34 is in facts."""
    facts = {"total": 25, "ratio": 12.34}
    claims = [
        Claim(text="There are 2 complaints.", cites=[]),  # 2 is not 25 or 12.34
    ]
    kept, dropped, reasons = validate_numeric_claims(claims, facts)
    assert len(kept) == 0
    assert len(dropped) == 1


# ---------------------------------------------------------------------------
# Acceptance Criterion 4: A malformed first response triggers exactly one retry
# ---------------------------------------------------------------------------


def test_malformed_first_response_retries_once(sample_hotspots, sample_verdicts):
    client = LLMClient(mock=True)

    # Response 1: malformed (raises LLMResponseError)
    # Response 2 (retry): valid
    client.set_mock_response(LLMResponseError("Malformed JSON"))
    client.set_mock_response(
        _SummaryResponse(
            claims=[
                Claim(
                    text="Spatial clustering identified 2 hotspots.",
                    cites=[],
                )
            ]
        )
    )

    report, diagnostics = generate_summary(
        sample_hotspots, sample_verdicts, llm_client=client
    )

    assert not diagnostics["fallback_triggered"]
    assert diagnostics["kept_claim_count"] == 1
    assert report.claims[0].text == "Spatial clustering identified 2 hotspots."


# ---------------------------------------------------------------------------
# Acceptance Criterion 5: A malformed second response triggers fallback
# ---------------------------------------------------------------------------


def test_malformed_second_response_triggers_fallback(sample_hotspots, sample_verdicts):
    client = LLMClient(mock=True)

    # Response 1 & 2 both fail
    client.set_mock_response(LLMResponseError("Malformed JSON 1"))
    client.set_mock_response(LLMResponseError("Malformed JSON 2"))

    report, diagnostics = generate_summary(
        sample_hotspots, sample_verdicts, llm_client=client
    )

    assert diagnostics["fallback_triggered"]
    assert report.stats["summary_fallback"] is True
    assert len(report.claims) > 0
    # Every fallback claim conforms to Claim schema
    for c in report.claims:
        assert isinstance(c, Claim)
        assert isinstance(c.text, str)
        assert isinstance(c.cites, list)


# ---------------------------------------------------------------------------
# Acceptance Criterion 6: Provider failure triggers deterministic fallback
# ---------------------------------------------------------------------------


def test_provider_failure_triggers_fallback(sample_hotspots, sample_verdicts):
    client = LLMClient(mock=True)

    client.set_mock_response(LLMTimeoutError("Request timed out"))
    client.set_mock_response(LLMTimeoutError("Request timed out retry"))

    report, diagnostics = generate_summary(
        sample_hotspots, sample_verdicts, llm_client=client
    )

    assert diagnostics["fallback_triggered"]
    assert report.stats["summary_fallback"] is True
    assert len(report.claims) > 0


# ---------------------------------------------------------------------------
# Acceptance Criterion 7: Invalid claims never appear in the final report
# ---------------------------------------------------------------------------


def test_invalid_claims_never_in_final_report(sample_hotspots, sample_verdicts):
    client = LLMClient(mock=True)

    # Supply 1 valid claim and 2 invalid claims (1 bad citation, 1 bad number)
    valid_id = sample_hotspots[0].id
    client.set_mock_response(
        _SummaryResponse(
            claims=[
                Claim(
                    text="Hotspot is severe.",
                    cites=[valid_id],
                ),
                Claim(
                    text="Fake hotspot cited.",
                    cites=["non_existent_hotspot_id_12345"],
                ),
                Claim(
                    text="There were 99999 complaints reported.",
                    cites=[],
                ),
            ]
        )
    )

    report, diagnostics = generate_summary(
        sample_hotspots, sample_verdicts, llm_client=client
    )

    assert not diagnostics["fallback_triggered"]
    assert diagnostics["dropped_bad_citation"] == 1
    assert diagnostics["dropped_bad_numbers"] == 1
    assert len(report.claims) == 1
    assert report.claims[0].cites == [valid_id]
    # Verify non-existent ID does not appear anywhere
    for c in report.claims:
        assert "non_existent_hotspot_id_12345" not in c.cites
        assert "99999" not in c.text


def test_all_invalid_claims_triggers_safe_fallback(sample_hotspots, sample_verdicts):
    """When all model claims are dropped by validation, fallback is triggered."""
    client = LLMClient(mock=True)

    client.set_mock_response(
        _SummaryResponse(
            claims=[
                Claim(text="There were 999 complaints.", cites=["fake-id"]),
            ]
        )
    )

    report, diagnostics = generate_summary(
        sample_hotspots, sample_verdicts, llm_client=client
    )

    assert diagnostics["fallback_triggered"]
    assert len(report.claims) > 0
    # Fallback claims must not contain fake-id or 999
    for c in report.claims:
        assert "fake-id" not in c.cites
        assert "999" not in c.text


# ---------------------------------------------------------------------------
# Acceptance Criterion 8: Kept/dropped claim counts correct and in stats
# ---------------------------------------------------------------------------


def test_kept_dropped_counts_in_diagnostics_and_stats(sample_hotspots, sample_verdicts):
    client = LLMClient(mock=True)

    valid_id = sample_hotspots[0].id
    client.set_mock_response(
        _SummaryResponse(
            claims=[
                Claim(text="Hotspot valid.", cites=[valid_id]),
                Claim(text="Bad cite.", cites=["bogus-id"]),
                Claim(text="Bad number 888.", cites=[]),
            ]
        )
    )

    report, diagnostics = generate_summary(
        sample_hotspots, sample_verdicts, llm_client=client
    )

    assert diagnostics["raw_claim_count"] == 3
    assert diagnostics["kept_claim_count"] == 1
    assert diagnostics["dropped_claim_count"] == 2
    assert diagnostics["dropped_bad_citation"] == 1
    assert diagnostics["dropped_bad_numbers"] == 1

    assert report.stats["claims_kept"] == 1
    assert report.stats["claims_dropped"] == 2
    assert report.stats["claims_raw"] == 3


# ---------------------------------------------------------------------------
# Acceptance Criterion 9: Final output validates against shared Claim and Report schemas
# ---------------------------------------------------------------------------


def test_output_schema_validation(sample_hotspots, sample_verdicts):
    client = LLMClient(mock=True)
    report, _ = generate_summary(sample_hotspots, sample_verdicts, llm_client=client)

    assert isinstance(report, Report)
    # Check that Report dump and re-validation works cleanly
    dumped = report.model_dump(mode="json")
    validated = Report.model_validate(dumped)
    assert len(validated.claims) == len(report.claims)
    for c in validated.claims:
        assert isinstance(c, Claim)


# ---------------------------------------------------------------------------
# Acceptance Criterion 10: Empty or incomplete input facts handled safely
# ---------------------------------------------------------------------------


def test_empty_facts_handled_safely():
    client = LLMClient(mock=True)
    report, diagnostics = generate_summary(
        hotspots=[],
        verdicts=[],
        llm_client=client,
        extra_facts={},
    )

    assert isinstance(report, Report)
    assert len(report.claims) > 0
    # No invented numbers: total_complaints should be 0
    complaint_claims = [
        c for c in report.claims if "complaints in the dataset" in c.text
    ]
    assert len(complaint_claims) == 1
    assert "0 civic complaints" in complaint_claims[0].text
    # No crashed calculations or exceptions
