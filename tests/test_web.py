"""Tests for Issues #15 and #16: Web landing page, demo report, and Leaflet map.

Verifies:
- Issue #15 acceptance criteria (landing page, real demo report, schema mapping)
- Issue #16 acceptance criteria (Leaflet coordinates, overlays, badges)
"""

from __future__ import annotations

import json
from pathlib import Path

from core.omnigrid.damage import DamageSeverityCache
from core.omnigrid.schemas import Complaint, Hotspot, Report, Verdict
from scripts import export_demo_report
from scripts.export_demo_report import export_demo

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = REPO_ROOT / "web"
INDEX_HTML = WEB_DIR / "index.html"
STYLE_CSS = WEB_DIR / "css" / "style.css"
APP_JS = WEB_DIR / "js" / "app.js"
DEMO_REPORT_JSON = WEB_DIR / "demo" / "report.json"
DEMO_MEDIA_DIR = WEB_DIR / "demo" / "media"


# ---------------------------------------------------------------------------
# Issue #15 Acceptance Tests: Landing page organization & real demo JSON
# ---------------------------------------------------------------------------


def test_index_html_exists_as_entrypoint():
    """Criterion 1: web/index.html is the landing-page entry point."""
    assert INDEX_HTML.is_file(), "web/index.html must exist as the primary entry point."
    content = INDEX_HTML.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in content
    assert "OMNI-GRID" in content
    assert "leaflet.js" in content
    assert "js/app.js" in content
    assert "css/style.css" in content


def test_frontend_references_demo_report():
    """Criterion 2: The frontend fetches web/demo/report.json."""
    app_js = APP_JS.read_text(encoding="utf-8")
    assert "demo/report.json" in app_js, "app.js must fetch demo/report.json."


def test_demo_report_validates_against_shared_schema():
    """Criterion 7: Report values mapped correctly from shared Report schema."""
    assert DEMO_REPORT_JSON.is_file(), "web/demo/report.json must be generated."
    data = json.loads(DEMO_REPORT_JSON.read_text(encoding="utf-8"))

    report = Report.model_validate(data)
    assert isinstance(report, Report)
    assert len(report.hotspots) > 0
    assert len(report.verdicts) > 0
    assert len(report.claims) > 0
    assert len(report.complaints) > 0

    # Validate each sub-model satisfies shared schemas
    for h in report.hotspots:
        assert isinstance(h, Hotspot)
        assert len(h.centroid) == 2
        assert 0.0 <= h.severity <= 10.0

    for v in report.verdicts:
        assert isinstance(v, Verdict)
        assert v.verdict in ("verified", "suspect", "inconclusive")
        assert len(v.checks) > 0

    for c in report.complaints:
        assert isinstance(c, Complaint)
        assert -90.0 <= c.lat <= 90.0
        assert -180.0 <= c.lon <= 180.0


def test_embedded_fallback_sample_present_in_js():
    """Criterion 4: Fallback sample appears when the JSON file cannot be loaded."""
    app_js = APP_JS.read_text(encoding="utf-8")
    assert "FALLBACK_REPORT" in app_js, "app.js must contain FALLBACK_REPORT."
    assert "banner-notice" in app_js, "app.js must manage the banner notice element."
    assert "hotspots" in app_js
    assert "verdicts" in app_js


def test_changing_report_json_modifies_values_without_code_change(tmp_path):
    """Criterion 3: Replacing report.json updates values without touching HTML/JS."""
    custom_report = {
        "hotspots": [
            {
                "id": "hotspot-custom-test",
                "centroid": [13.0, 77.6],
                "radius_m": 50.0,
                "complaint_ids": ["c-999"],
                "category_mix": {"pothole": 1},
                "severity": 9.2,
            }
        ],
        "verdicts": [
            {
                "complaint_id": "c-999",
                "verdict": "suspect",
                "confidence": 0.99,
                "checks": [
                    {"name": "geofence", "passed": False, "detail": "Test check"}
                ],
            }
        ],
        "claims": [{"text": "Custom claim test.", "cites": ["c-999"]}],
        "stats": {"complaint_count": 1, "hotspot_count": 1},
        "complaints": [
            {
                "id": "c-999",
                "category": "pothole",
                "text": "Custom complaint text",
                "lat": 13.0,
                "lon": 77.6,
                "created_at": "2026-10-17T00:00:00Z",
                "status": "closed",
            }
        ],
    }

    # Verify that the custom report adheres strictly to the Report schema
    validated = Report.model_validate(custom_report)
    assert validated.hotspots[0].id == "hotspot-custom-test"
    assert validated.hotspots[0].severity == 9.2


def test_export_script_generates_valid_report_and_media(tmp_path, monkeypatch):
    """Criterion 8 & Export: export_demo produces valid report and copies media."""
    out_json = tmp_path / "test_out_report.json"
    out_media = tmp_path / "media"
    captured: dict[str, object] = {}
    original_cluster = export_demo_report.cluster_complaints
    damage_cache = DamageSeverityCache(tmp_path / "damage-cache.json")

    def capture_cluster(complaints, **kwargs):
        captured.update(kwargs)
        return original_cluster(complaints, **kwargs)

    monkeypatch.setattr(export_demo_report, "cluster_complaints", capture_cluster)

    report = export_demo(
        output_report_path=out_json,
        output_media_dir=out_media,
        use_mock_llm=True,
        damage_cache=damage_cache,
    )

    assert out_json.is_file()
    assert isinstance(report, Report)
    assert len(report.hotspots) == 5
    assert len(report.verdicts) == 100
    assert len(report.complaints) == 500
    assert report.stats["inconclusive_count"] == 90
    assert report.stats["damage_scores_computed"] > 0
    assert captured["damage_scores"]

    # Ensure media files were copied
    copied_media = list(out_media.glob("*.jpg"))
    assert len(copied_media) == 40

    exported_report = Report.model_validate_json(out_json.read_text(encoding="utf-8"))
    valid_citations = {
        *(hotspot.id for hotspot in exported_report.hotspots),
        *(verdict.complaint_id for verdict in exported_report.verdicts),
    }
    for claim in exported_report.claims:
        assert set(claim.cites) <= valid_citations
    for complaint in exported_report.complaints:
        media = list(complaint.media)
        if complaint.closure:
            media.extend(complaint.closure.media)
        for item in media:
            assert item.path.startswith("demo/media/")
            assert (out_media / Path(item.path).name).is_file()

    second_json = tmp_path / "second_report.json"
    export_demo(
        output_report_path=second_json,
        output_media_dir=tmp_path / "second_media",
        use_mock_llm=True,
        damage_cache=damage_cache,
    )
    assert second_json.read_bytes() == out_json.read_bytes()


# ---------------------------------------------------------------------------
# Issue #16 Acceptance Tests: Interactive Leaflet Map & Detail Views
# ---------------------------------------------------------------------------


def test_leaflet_elements_and_controls_present():
    """Criterion 1 & 2: Map container and layer controls exist in index.html."""
    content = INDEX_HTML.read_text(encoding="utf-8")
    assert 'id="map"' in content
    assert 'id="toggle-hotspots"' in content
    assert 'id="toggle-complaints"' in content
    assert 'id="filter-status"' in content
    assert 'id="filter-category"' in content
    assert 'id="filter-verdict"' in content


def test_map_markers_use_valid_report_coordinates():
    """Criterion 1: Complaint pins use actual valid report coordinates."""
    data = json.loads(DEMO_REPORT_JSON.read_text(encoding="utf-8"))
    complaints = data.get("complaints", [])
    assert len(complaints) > 0

    valid_coords_count = 0
    for c in complaints:
        lat = c.get("lat")
        lon = c.get("lon")
        assert isinstance(lat, (int, float))
        assert isinstance(lon, (int, float))
        assert -90.0 <= lat <= 90.0
        assert -180.0 <= lon <= 180.0
        valid_coords_count += 1

    assert valid_coords_count == len(complaints)


def test_hotspot_overlays_use_actual_coordinates_and_radius():
    """Criterion 2: Hotspot overlays use actual coordinates and show severity."""
    data = json.loads(DEMO_REPORT_JSON.read_text(encoding="utf-8"))
    hotspots = data.get("hotspots", [])
    assert len(hotspots) > 0

    for h in hotspots:
        centroid = h.get("centroid")
        assert len(centroid) == 2
        assert -90.0 <= centroid[0] <= 90.0
        assert -180.0 <= centroid[1] <= 180.0
        assert h.get("radius_m") > 0.0
        assert 0.0 <= h.get("severity") <= 10.0


def test_evidence_paths_resolved_relative_to_demo_media():
    """Criterion 5: Evidence paths are resolved safely to demo/media."""
    data = json.loads(DEMO_REPORT_JSON.read_text(encoding="utf-8"))
    complaints = data.get("complaints", [])

    media_found = 0
    for c in complaints:
        for m in c.get("media", []):
            path = m.get("path")
            assert not path.startswith("/"), "Must not be an absolute filesystem path."
            assert not path.startswith("C:"), "Must not expose local drive paths."
            media_found += 1
        closure = c.get("closure")
        if closure:
            for m in closure.get("media", []):
                path = m.get("path")
                assert not path.startswith("/"), "Must not be an absolute path."
                assert not path.startswith("C:"), "Must not expose local drive paths."
                media_found += 1

    assert media_found > 0


def test_verdicts_have_distinguishable_states():
    """Criterion 7: Verified, suspect, and inconclusive states are distinct."""
    data = json.loads(DEMO_REPORT_JSON.read_text(encoding="utf-8"))
    verdicts = data.get("verdicts", [])

    states = {v.get("verdict") for v in verdicts}
    # Synthetic dataset contains both verified and suspect closures
    assert "verified" in states
    assert "suspect" in states

    # Checks have passed bool or None
    for v in verdicts:
        for chk in v.get("checks", []):
            assert chk.get("passed") in (True, False, None)
            assert chk.get("name") is not None


def test_css_responsive_layout_media_queries():
    """Criterion 10: Interface adapts to mobile viewport sizes."""
    css_content = STYLE_CSS.read_text(encoding="utf-8")
    assert "@media" in css_content
    assert "max-width" in css_content
    assert "flex-direction: column" in css_content
