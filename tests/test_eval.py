"""Comprehensive unit and integration tests for Phase 10 evaluation harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from core.omnigrid.schemas import Check, Claim, Hotspot, Verdict
from eval.run_eval import (
    calculate_citation_metrics,
    calculate_clustering_metrics,
    calculate_duplicate_evidence_metrics,
    calculate_verification_metrics,
    extract_verdict_duplicate_pairs,
    generate_markdown_report,
    get_git_commit_hash,
    main,
    run_evaluation,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_DATA = REPO_ROOT / "data" / "synthetic" / "complaints.geojson"
GROUND_TRUTH = REPO_ROOT / "data" / "synthetic" / "ground_truth.json"


# -----------------------------------------------------------------------------
# 1. Hotspot Clustering Metric Tests
# -----------------------------------------------------------------------------


def test_clustering_metrics_perfect_recovery() -> None:
    detected = [
        Hotspot(
            id="hotspot-1",
            centroid=(12.9, 77.6),
            radius_m=30.0,
            complaint_ids=["c1", "c2", "c3"],
            category_mix={"pothole": 3},
            severity=5.0,
        ),
        Hotspot(
            id="hotspot-2",
            centroid=(12.8, 77.5),
            radius_m=25.0,
            complaint_ids=["c4", "c5"],
            category_mix={"streetlight": 2},
            severity=4.0,
        ),
    ]
    gt = [
        {"id": "gt-1", "complaint_ids": ["c1", "c2", "c3"]},
        {"id": "gt-2", "complaint_ids": ["c4", "c5"]},
    ]

    metrics = calculate_clustering_metrics(detected, gt)
    assert metrics["recall"] == 1.0
    assert metrics["purity"] == 1.0
    assert metrics["recovered_gt_complaints_count"] == 5
    assert metrics["gt_hotspot_complaints_count"] == 5


def test_clustering_metrics_mixed_purity() -> None:
    detected = [
        Hotspot(
            id="hotspot-1",
            centroid=(12.9, 77.6),
            radius_m=30.0,
            complaint_ids=["c1", "c2", "c3", "c4"],
            category_mix={"pothole": 3, "streetlight": 1},
            severity=5.0,
        ),
        Hotspot(
            id="hotspot-2",
            centroid=(12.8, 77.5),
            radius_m=25.0,
            complaint_ids=["c5", "c6"],
            category_mix={"waterlogging": 1, "other": 1},
            severity=4.0,
        ),
    ]
    gt = [
        {"id": "gt-1", "complaint_ids": ["c1", "c2", "c3", "c4", "c5", "c6"]},
    ]

    metrics = calculate_clustering_metrics(detected, gt)
    # Total points: 4 + 2 = 6. Max matches: 3 (pothole) + 1 (waterlogging) = 4.
    # Purity = 4 / 6 = 0.6667
    assert metrics["purity"] == pytest.approx(4.0 / 6.0, abs=1e-4)
    assert metrics["recall"] == 1.0


def test_clustering_metrics_partial_recall() -> None:
    detected = [
        Hotspot(
            id="hotspot-1",
            centroid=(12.9, 77.6),
            radius_m=30.0,
            complaint_ids=["c1", "c2"],
            category_mix={"pothole": 2},
            severity=5.0,
        ),
    ]
    gt = [
        {"id": "gt-1", "complaint_ids": ["c1", "c2", "c3", "c4"]},
    ]

    metrics = calculate_clustering_metrics(detected, gt)
    assert metrics["recall"] == 0.5
    assert metrics["recovered_gt_complaints_count"] == 2
    assert metrics["gt_hotspot_complaints_count"] == 4


def test_clustering_metrics_empty_edge_cases() -> None:
    # Both empty
    empty_both = calculate_clustering_metrics([], [])
    assert empty_both["recall"] == 1.0
    assert empty_both["purity"] == 0.0

    # Empty detected, non-empty GT
    empty_pred = calculate_clustering_metrics(
        [], [{"complaint_ids": ["c1", "c2"]}]
    )
    assert empty_pred["recall"] == 0.0
    assert empty_pred["purity"] == 0.0

    # Non-empty detected, empty GT
    single = [
        Hotspot(
            id="hotspot-1",
            centroid=(12.9, 77.6),
            radius_m=10.0,
            complaint_ids=["c1"],
            category_mix={"pothole": 1},
            severity=3.0,
        )
    ]
    empty_gt = calculate_clustering_metrics(single, [])
    assert empty_gt["recall"] == 0.0
    assert empty_gt["purity"] == 1.0


# -----------------------------------------------------------------------------
# 2. Duplicate Evidence Metric Tests
# -----------------------------------------------------------------------------


def test_duplicate_evidence_metrics_exact_match() -> None:
    detected = {("c1", "c2"), ("c3", "c4")}
    gt = [
        {"complaint_id": "c1", "duplicate_with_complaint_id": "c2"},
        {"complaint_id": "c3", "duplicate_with_complaint_id": "c4"},
    ]
    metrics = calculate_duplicate_evidence_metrics(detected, gt)
    assert metrics["recall"] == 1.0
    assert metrics["matched_gt_pairs_count"] == 2
    assert metrics["gt_pairs_count"] == 2


def test_duplicate_evidence_metrics_reverse_tuple_normalization() -> None:
    # Reverse order of IDs in detected should still match GT
    detected = {("c2", "c1")}
    gt = [{"complaint_id": "c1", "duplicate_with_complaint_id": "c2"}]
    metrics = calculate_duplicate_evidence_metrics(detected, gt)
    assert metrics["recall"] == 1.0
    assert metrics["matched_gt_pairs_count"] == 1


def test_duplicate_evidence_metrics_partial_and_zero_match() -> None:
    detected = {("c1", "c2")}
    gt = [
        {"complaint_id": "c1", "duplicate_with_complaint_id": "c2"},
        {"complaint_id": "c3", "duplicate_with_complaint_id": "c4"},
    ]
    metrics = calculate_duplicate_evidence_metrics(detected, gt)
    assert metrics["recall"] == 0.5
    assert metrics["matched_gt_pairs_count"] == 1

    # Completely disjoint
    metrics_zero = calculate_duplicate_evidence_metrics({("c8", "c9")}, gt)
    assert metrics_zero["recall"] == 0.0
    assert metrics_zero["matched_gt_pairs_count"] == 0


def test_duplicate_evidence_metrics_empty_cases() -> None:
    assert calculate_duplicate_evidence_metrics([], [])["recall"] == 1.0
    assert (
        calculate_duplicate_evidence_metrics(
            [], [{"complaint_id": "c1", "duplicate_with_complaint_id": "c2"}]
        )["recall"]
        == 0.0
    )


def test_extract_verdict_duplicate_pairs() -> None:
    verdicts = [
        Verdict(
            complaint_id="complaint-030",
            verdict="suspect",
            confidence=0.9,
            checks=[
                Check(
                    name="duplicate_evidence",
                    passed=False,
                    detail="Duplicates evidence for complaint-045",
                )
            ],
        ),
        Verdict(
            complaint_id="complaint-045",
            verdict="suspect",
            confidence=0.9,
            checks=[
                Check(
                    name="duplicate_evidence",
                    passed=False,
                    detail="Duplicates evidence for complaint-030",
                )
            ],
        ),
        Verdict(
            complaint_id="complaint-001",
            verdict="verified",
            confidence=1.0,
            checks=[
                Check(
                    name="duplicate_evidence",
                    passed=True,
                    detail="Evidence is unique",
                )
            ],
        ),
    ]
    pairs = extract_verdict_duplicate_pairs(verdicts)
    assert pairs == {("complaint-030", "complaint-045")}


# -----------------------------------------------------------------------------
# 3. Suspicious-Closure Verification Metric Tests
# -----------------------------------------------------------------------------


def test_verification_metrics_perfect_scores() -> None:
    verdicts = [
        Verdict(complaint_id=f"c{i}", verdict="suspect", confidence=0.95, checks=[])
        for i in range(1, 6)
    ] + [
        Verdict(
            complaint_id=f"c{i}", verdict="verified", confidence=0.95, checks=[]
        )
        for i in range(6, 20)
    ]
    target_ids = [f"c{i}" for i in range(1, 6)]

    metrics = calculate_verification_metrics(verdicts, target_ids)
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0
    assert metrics["f1"] == 1.0
    assert metrics["false_positives"] == []
    assert metrics["false_negatives"] == []


def test_verification_metrics_with_fp_and_fn() -> None:
    # Flagged: c1, c2, c3, c4 (c4 is FP)
    # Targets: c1, c2, c3, c5 (c5 is FN)
    verdicts = [
        Verdict(complaint_id="c1", verdict="suspect", confidence=0.9, checks=[]),
        Verdict(complaint_id="c2", verdict="suspect", confidence=0.9, checks=[]),
        Verdict(complaint_id="c3", verdict="suspect", confidence=0.9, checks=[]),
        Verdict(complaint_id="c4", verdict="suspect", confidence=0.9, checks=[]),
        Verdict(
            complaint_id="c5", verdict="verified", confidence=0.9, checks=[]
        ),
    ]
    targets = ["c1", "c2", "c3", "c5"]

    metrics = calculate_verification_metrics(verdicts, targets)
    # TP = 3, FP = 1 (c4), FN = 1 (c5)
    # Precision = 3 / 4 = 0.75
    # Recall = 3 / 4 = 0.75
    # F1 = 0.75
    assert metrics["precision"] == 0.75
    assert metrics["recall"] == 0.75
    assert metrics["f1"] == 0.75
    assert metrics["false_positives"] == ["c4"]
    assert metrics["false_negatives"] == ["c5"]


def test_verification_metrics_empty_edge_cases() -> None:
    # No suspects flagged
    metrics_no_suspects = calculate_verification_metrics([], ["c1", "c2"])
    assert metrics_no_suspects["precision"] == 0.0
    assert metrics_no_suspects["recall"] == 0.0
    assert metrics_no_suspects["f1"] == 0.0

    # No targets
    metrics_no_targets = calculate_verification_metrics(
        [
            Verdict(
                complaint_id="c1", verdict="suspect", confidence=0.9, checks=[]
            )
        ],
        [],
    )
    assert metrics_no_targets["precision"] == 0.0
    assert metrics_no_targets["recall"] == 0.0
    assert metrics_no_targets["f1"] == 0.0


# -----------------------------------------------------------------------------
# 4. Citation Integrity Metric Tests
# -----------------------------------------------------------------------------


def test_citation_metrics_all_valid() -> None:
    claims = [
        Claim(
            text="Complaint complaint-001 has severe damage.",
            cites=["complaint-001"],
        ),
        Claim(
            text="Issues complaint-002 and complaint-003 fixed.",
            cites=["complaint-002", "complaint-003"],
        ),
    ]
    valid_cids = {"complaint-001", "complaint-002", "complaint-003"}

    metrics = calculate_citation_metrics(claims, valid_cids)
    assert metrics["validity_rate"] == 1.0
    assert metrics["invalid_citations"] == []
    assert metrics["valid_citations_count"] == 3


def test_citation_metrics_with_invalid_ids() -> None:
    claims = [
        Claim(
            text="Complaint complaint-999 is hallucinated.",
            cites=["complaint-999"],
        ),
        Claim(
            text="Complaint complaint-001 is real.",
            cites=["complaint-001"],
        ),
    ]
    valid_cids = {"complaint-001"}

    metrics = calculate_citation_metrics(claims, valid_cids)
    assert metrics["validity_rate"] == 0.5
    assert metrics["invalid_citations"] == ["complaint-999"]
    assert metrics["valid_citations_count"] == 1


def test_citation_metrics_empty_claims() -> None:
    metrics = calculate_citation_metrics([], {"c1"})
    assert metrics["validity_rate"] == 1.0
    assert metrics["total_citations"] == 0


# -----------------------------------------------------------------------------
# 5. Full Evaluation Pipeline & CLI Export Integration Tests
# -----------------------------------------------------------------------------


def test_git_commit_hash() -> None:
    commit = get_git_commit_hash(REPO_ROOT)
    assert isinstance(commit, str)
    assert len(commit) > 0


def test_run_evaluation_on_synthetic_data() -> None:
    results = run_evaluation(
        data_path=SYNTHETIC_DATA,
        ground_truth_path=GROUND_TRUTH,
        mock=True,
    )

    assert "metadata" in results
    assert "dataset_summary" in results
    assert "timings_seconds" in results
    assert "clustering" in results
    assert "duplicate_evidence" in results
    assert "verification" in results
    assert "citations" in results

    # Verify expected benchmark targets on planted synthetic dataset
    assert results["clustering"]["recall"] == 1.0
    assert results["clustering"]["purity"] > 0.5
    assert results["duplicate_evidence"]["recall"] == 1.0
    assert results["verification"]["precision"] == 1.0
    assert results["verification"]["recall"] == 1.0
    assert results["verification"]["f1"] == 1.0
    assert results["citations"]["validity_rate"] == 1.0
    assert results["diagnostics"]["false_positives"] == []
    assert results["diagnostics"]["false_negatives"] == []


def test_generate_markdown_report_structure() -> None:
    results = run_evaluation(
        data_path=SYNTHETIC_DATA,
        ground_truth_path=GROUND_TRUTH,
        mock=True,
    )
    md = generate_markdown_report(results)

    # Validate mandatory report sections and headers
    assert "# Omni-Grid Civic Issue Triage - Benchmark Evaluation Report" in md
    assert "## 1. Run Metadata" in md
    assert "## 2. Executive Benchmark Results" in md
    assert "## 3. Pipeline Latency Breakdown" in md
    assert "## 4. Discrepancy & Diagnostic Analysis" in md
    assert "| **Hotspot Clustering** |" in md
    assert "| **Duplicate Evidence** |" in md
    assert "| **Proof-of-Fix Verifier** |" in md
    assert "| **Summary Citations** |" in md
    assert "complaint-003" in md
    assert "complaint-017" in md
    assert "complaint-018" in md
    assert "complaint-030" in md
    assert "complaint-045" in md


def test_cli_export(tmp_path: Path) -> None:
    out_file = tmp_path / "results_test.md"
    exit_code = main(
        [
            "--data",
            str(SYNTHETIC_DATA),
            "--ground-truth",
            str(GROUND_TRUTH),
            "--output",
            str(out_file),
        ]
    )

    assert exit_code == 0
    assert out_file.is_file()
    content = out_file.read_text(encoding="utf-8")
    assert len(content) > 500
    assert "Executive Benchmark Results" in content


def test_cli_json_flag(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(
        [
            "--data",
            str(SYNTHETIC_DATA),
            "--ground-truth",
            str(GROUND_TRUTH),
            "--json",
        ]
    )
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Evaluation report written successfully" in captured.out
    # Find JSON payload in output
    start = captured.out.find("{")
    assert start != -1
    parsed = json.loads(captured.out[start:])
    assert "clustering" in parsed
    assert "verification" in parsed
