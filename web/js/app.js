/**
 * Omni-Grid Civic Issue Triage - Frontend Application
 * Consumes exported Report JSON from web/demo/report.json and renders
 * an interactive Leaflet map, statistics, hotspots, verdicts, and repair checks.
 */

// Embedded fallback sample for when web/demo/report.json is unavailable (e.g. file:// or 404)
const FALLBACK_REPORT = {
  hotspots: [
    {
      id: "hotspot-silk-board",
      centroid: [12.9172, 77.6228],
      radius_m: 65.0,
      complaint_ids: ["complaint-001", "complaint-002", "complaint-003"],
      category_mix: { pothole: 2, waterlogging: 1 },
      severity: 6.8
    },
    {
      id: "hotspot-indiranagar",
      centroid: [12.9784, 77.6408],
      radius_m: 45.0,
      complaint_ids: ["complaint-015", "complaint-017", "complaint-018"],
      category_mix: { streetlight: 2, pothole: 1 },
      severity: 7.2
    }
  ],
  verdicts: [
    {
      complaint_id: "complaint-001",
      verdict: "verified",
      confidence: 0.95,
      checks: [
        { name: "geofence", passed: true, detail: "Closure photo within 12.4m of complaint GPS", value: 12.4 },
        { name: "duplicate_hash", passed: true, detail: "No duplicate pHash match found", value: 0.12 },
        { name: "timestamp_sanity", passed: true, detail: "Closure photo timestamp is chronologically valid", value: null }
      ]
    },
    {
      complaint_id: "complaint-003",
      verdict: "suspect",
      confidence: 0.85,
      checks: [
        { name: "geofence", passed: false, detail: "Closure photo GPS is 23.1 km away from complaint location (>50m)", value: 23120.0 },
        { name: "duplicate_hash", passed: true, detail: "pHash check passed", value: 0.05 },
        { name: "timestamp_sanity", passed: true, detail: "Timestamp check passed", value: null }
      ]
    },
    {
      complaint_id: "complaint-018",
      verdict: "suspect",
      confidence: 0.85,
      checks: [
        { name: "defect_sanity", passed: false, detail: "Streetlight complaint closed with pothole repair photo", value: null },
        { name: "geofence", passed: true, detail: "Geofence check passed", value: 18.0 }
      ]
    }
  ],
  claims: [
    {
      text: "Spatial clustering identified 2 high-density civic hotspots in Bengaluru.",
      cites: ["hotspot-silk-board", "hotspot-indiranagar"]
    },
    {
      text: "Complaint complaint-003 was flagged as suspect due to a 23.1 km geofence mismatch.",
      cites: ["complaint-003"]
    },
    {
      text: "Complaint complaint-001 was verified genuine with valid GPS and timestamp evidence.",
      cites: ["complaint-001"]
    }
  ],
  stats: {
    total_complaints: 6,
    open_complaints: 3,
    closed_complaints: 3,
    hotspot_count: 2,
    verified_count: 1,
    suspect_count: 2,
    inconclusive_count: 0
  },
  complaints: [
    {
      id: "complaint-001",
      category: "pothole",
      text: "Large pothole near Silk Board junction causing vehicle slowdown.",
      lat: 12.9175,
      lon: 77.6231,
      created_at: "2026-10-14T08:30:00Z",
      status: "closed",
      media: [{ path: "demo/media/media_01.jpg", type: "image" }],
      closure: {
        closed_at: "2026-10-15T12:00:00Z",
        media: [{ path: "demo/media/media_06.jpg", type: "image" }],
        closed_by: "ward_contractor_42"
      }
    },
    {
      id: "complaint-002",
      category: "pothole",
      text: "Asphalt crater near service lane.",
      lat: 12.9171,
      lon: 77.6225,
      created_at: "2026-10-14T09:15:00Z",
      status: "open",
      media: [],
      closure: null
    },
    {
      id: "complaint-003",
      category: "waterlogging",
      text: "Deep water accumulation across bus stop.",
      lat: 12.9169,
      lon: 77.6230,
      created_at: "2026-10-14T10:00:00Z",
      status: "closed",
      media: [{ path: "demo/media/media_03.jpg", type: "image" }],
      closure: {
        closed_at: "2026-10-15T14:20:00Z",
        media: [{ path: "demo/media/media_11.jpg", type: "image" }],
        closed_by: "drainage_dept"
      }
    },
    {
      id: "complaint-015",
      category: "streetlight",
      text: "Streetlight fixture flickering and unlit at night on 100ft road.",
      lat: 12.9782,
      lon: 77.6405,
      created_at: "2026-10-14T11:00:00Z",
      status: "closed",
      media: [{ path: "demo/media/media_02.jpg", type: "image" }],
      closure: {
        closed_at: "2026-10-15T16:00:00Z",
        media: [{ path: "demo/media/media_07.jpg", type: "image" }],
        closed_by: "bescom_field"
      }
    },
    {
      id: "complaint-017",
      category: "pothole",
      text: "Damaged road surface opposite metro pillar.",
      lat: 12.9786,
      lon: 77.6410,
      created_at: "2026-10-14T12:00:00Z",
      status: "open",
      media: [],
      closure: null
    },
    {
      id: "complaint-018",
      category: "streetlight",
      text: "Broken lamp pole near pedestrian crossing.",
      lat: 12.9784,
      lon: 77.6408,
      created_at: "2026-10-14T13:30:00Z",
      status: "closed",
      media: [{ path: "demo/media/media_02.jpg", type: "image" }],
      closure: {
        closed_at: "2026-10-15T18:00:00Z",
        media: [{ path: "demo/media/media_13.jpg", type: "image" }],
        closed_by: "contractor_bbmp"
      }
    }
  ]
};

// Application state
const state = {
  report: null,
  isFallback: false,
  map: null,
  mapLayers: {
    hotspots: null,
    complaints: null
  },
  markersMap: new Map(),
  circlesMap: new Map(),
  verdictsMap: new Map(),
  complaintsMap: new Map(),
  selectedComplaintId: null,
  selectedHotspotId: null,
  filterStatus: "all",
  filterCategory: "all",
  filterVerdict: "all",
  searchQuery: ""
};

// DOM Security Helper: safely creates text nodes or sanitized elements
function createElem(tag, classes = [], text = "") {
  const elem = document.createElement(tag);
  if (Array.isArray(classes)) {
    classes.forEach(c => c && elem.classList.add(c));
  } else if (typeof classes === "string" && classes) {
    elem.className = classes;
  }
  if (text !== undefined && text !== null && text !== "") {
    elem.textContent = String(text);
  }
  return elem;
}

function isValidCoord(lat, lon) {
  return (
    typeof lat === "number" &&
    typeof lon === "number" &&
    isFinite(lat) &&
    isFinite(lon) &&
    lat >= -90 &&
    lat <= 90 &&
    lon >= -180 &&
    lon <= 180
  );
}

// Media Path Resolution Helper
function resolveMediaPath(rawPath) {
  if (!rawPath) return "";
  const cleaned = String(rawPath).trim();
  // If already relative to demo/media or media/
  if (cleaned.startsWith("demo/media/") || cleaned.startsWith("media/")) {
    return cleaned;
  }
  // If from synthetic path "data/synthetic/media/media_XX.jpg"
  const filename = cleaned.split("/").pop().split("\\").pop();
  if (filename) {
    return `demo/media/${filename}`;
  }
  return cleaned;
}

// App Initialization
async function initApp() {
  setupTabs();
  setupFilterControls();

  let reportData = null;
  try {
    const res = await fetch("demo/report.json");
    if (!res.ok) {
      throw new Error(`HTTP error ${res.status} reading demo/report.json`);
    }
    reportData = await res.json();
    state.isFallback = false;
  } catch (err) {
    console.warn("Could not load web/demo/report.json; using embedded fallback sample.", err);
    reportData = FALLBACK_REPORT;
    state.isFallback = true;
  }

  loadReport(reportData);
}

function loadReport(data) {
  state.report = data;

  // Build lookups for fast O(1) query
  state.verdictsMap.clear();
  if (Array.isArray(data.verdicts)) {
    data.verdicts.forEach(v => {
      if (v && v.complaint_id) {
        state.verdictsMap.set(String(v.complaint_id), v);
      }
    });
  }

  state.complaintsMap.clear();
  if (Array.isArray(data.complaints)) {
    data.complaints.forEach(c => {
      if (c && c.id) {
        state.complaintsMap.set(String(c.id), c);
      }
    });
  }

  // Update banner notice
  const banner = document.getElementById("banner-notice");
  if (state.isFallback) {
    banner.classList.add("active");
    banner.replaceChildren();
    const txt = createElem("span", [], "Notice: Loaded embedded fallback sample data (web/demo/report.json is not available over file:// or missing).");
    banner.appendChild(txt);
  } else {
    banner.classList.remove("active");
  }

  // Render header stats
  renderHeaderStats();

  // Render Summary Claims
  renderSummaryClaims();

  // Render Hotspots List
  renderHotspotsList();

  // Render Complaints List
  renderComplaintsList();

  // Initialize or update Map
  initOrUpdateMap();
}

function renderHeaderStats() {
  const stats = state.report.stats || {};
  const totalComplaints = stats.total_complaints ?? (state.report.complaints ? state.report.complaints.length : 0);
  const totalHotspots = stats.total_hotspots ?? (state.report.hotspots ? state.report.hotspots.length : 0);
  const suspectCount = stats.suspect_count ?? countVerdicts("suspect");
  const verifiedCount = stats.verified_count ?? countVerdicts("verified");

  setText("stat-total-complaints", totalComplaints);
  setText("stat-total-hotspots", totalHotspots);
  setText("stat-suspect-closures", suspectCount);
  setText("stat-verified-closures", verifiedCount);
}

function countVerdicts(status) {
  let cnt = 0;
  state.verdictsMap.forEach(v => {
    if (v.verdict === status) cnt++;
  });
  return cnt;
}

function setText(id, text) {
  const elem = document.getElementById(id);
  if (elem) elem.textContent = String(text);
}

// ---------------------------------------------------------------------------
// Tabs & Navigation
// ---------------------------------------------------------------------------
function setupTabs() {
  const buttons = document.querySelectorAll(".tab-btn");
  buttons.forEach(btn => {
    btn.addEventListener("click", () => {
      buttons.forEach(b => b.classList.remove("active"));
      btn.classList.add("active");

      const targetId = btn.dataset.tab;
      document.querySelectorAll(".tab-content").forEach(content => {
        content.classList.remove("active");
      });
      const activeContent = document.getElementById(targetId);
      if (activeContent) activeContent.classList.add("active");

      // Invalidate map size in case layout shifted
      if (state.map) {
        setTimeout(() => state.map.invalidateSize(), 150);
      }
    });
  });
}

function switchTab(tabId) {
  const btn = document.querySelector(`.tab-btn[data-tab="${tabId}"]`);
  if (btn) btn.click();
}

// ---------------------------------------------------------------------------
// Summary Claims Tab
// ---------------------------------------------------------------------------
function renderSummaryClaims() {
  const container = document.getElementById("claims-list-container");
  if (!container) return;
  container.replaceChildren();

  const claims = state.report.claims || [];
  if (claims.length === 0) {
    const empty = createElem("div", ["state-message"], "No summary claims available in this report.");
    container.appendChild(empty);
    return;
  }

  claims.forEach(claim => {
    const card = createElem("div", ["claim-card"]);
    const textElem = createElem("div", ["claim-text"], claim.text);
    card.appendChild(textElem);

    if (Array.isArray(claim.cites) && claim.cites.length > 0) {
      const citeContainer = createElem("div", ["citation-list"]);
      claim.cites.forEach(citeId => {
        const pill = createElem("span", ["citation-pill"], `Cite: ${citeId}`);
        pill.addEventListener("click", e => {
          e.stopPropagation();
          handleCitationClick(citeId);
        });
        citeContainer.appendChild(pill);
      });
      card.appendChild(citeContainer);
    }

    container.appendChild(card);
  });
}

function handleCitationClick(citeId) {
  // Check if citeId is a hotspot
  if (state.circlesMap.has(citeId)) {
    const circle = state.circlesMap.get(citeId);
    state.map.setView(circle.getLatLng(), 15);
    circle.openPopup();
    selectHotspot(citeId);
    return;
  }
  // Check if citeId is a complaint
  if (state.complaintsMap.has(citeId)) {
    selectComplaint(citeId);
    if (state.markersMap.has(citeId)) {
      const marker = state.markersMap.get(citeId);
      state.map.setView(marker.getLatLng(), 16);
      marker.openPopup();
    }
  }
}

// ---------------------------------------------------------------------------
// Hotspots Tab
// ---------------------------------------------------------------------------
function renderHotspotsList() {
  const container = document.getElementById("hotspots-list-container");
  if (!container) return;
  container.replaceChildren();

  const hotspots = state.report.hotspots || [];
  if (hotspots.length === 0) {
    const empty = createElem("div", ["state-message"], "No geographic hotspots detected.");
    container.appendChild(empty);
    return;
  }

  hotspots.forEach(h => {
    const card = createElem("div", ["hotspot-card"]);
    card.dataset.hotspotId = h.id;

    // Header
    const header = createElem("div", ["hotspot-header"]);
    const idElem = createElem("span", ["hotspot-id"], h.id);
    const sevTag = createElem("span", ["severity-tag"], `Severity ${Number(h.severity).toFixed(2)} / 10`);

    if (h.severity >= 6.0) sevTag.classList.add("severity-high");
    else if (h.severity >= 4.0) sevTag.classList.add("severity-med");
    else sevTag.classList.add("severity-low");

    header.appendChild(idElem);
    header.appendChild(sevTag);
    card.appendChild(header);

    // Details info
    const info = createElem("div", ["property-grid"]);
    const countItem = createElem("div", ["property-item"]);
    countItem.appendChild(createElem("div", ["prop-name"], "Complaints"));
    countItem.appendChild(createElem("div", ["prop-val"], (h.complaint_ids || []).length));
    info.appendChild(countItem);

    const radiusItem = createElem("div", ["property-item"]);
    radiusItem.appendChild(createElem("div", ["prop-name"], "Cluster Radius"));
    radiusItem.appendChild(createElem("div", ["prop-val"], `${Number(h.radius_m).toFixed(1)} m`));
    info.appendChild(radiusItem);

    card.appendChild(info);

    card.addEventListener("click", () => {
      selectHotspot(h.id);
      if (isValidCoord(h.centroid[0], h.centroid[1]) && state.map) {
        state.map.setView([h.centroid[0], h.centroid[1]], 15);
        if (state.circlesMap.has(h.id)) {
          state.circlesMap.get(h.id).openPopup();
        }
      }
    });

    container.appendChild(card);
  });
}

function selectHotspot(hotspotId) {
  state.selectedHotspotId = hotspotId;
  document.querySelectorAll(".hotspot-card").forEach(c => {
    c.classList.toggle("selected", c.dataset.hotspotId === hotspotId);
  });

  // Filter complaints list to only show complaints in this hotspot
  const hotspot = (state.report.hotspots || []).find(h => h.id === hotspotId);
  if (hotspot && Array.isArray(hotspot.complaint_ids)) {
    renderFilteredComplaints(new Set(hotspot.complaint_ids));
    switchTab("tab-complaints");
  }
}

// ---------------------------------------------------------------------------
// Complaints Tab & Filtering
// ---------------------------------------------------------------------------
function setupFilterControls() {
  const statusSelect = document.getElementById("filter-status");
  const catSelect = document.getElementById("filter-category");
  const verdictSelect = document.getElementById("filter-verdict");
  const searchInput = document.getElementById("search-input");
  const resetBtn = document.getElementById("btn-reset-filters");

  if (statusSelect) {
    statusSelect.addEventListener("change", e => {
      state.filterStatus = e.target.value;
      applyFilters();
    });
  }
  if (catSelect) {
    catSelect.addEventListener("change", e => {
      state.filterCategory = e.target.value;
      applyFilters();
    });
  }
  if (verdictSelect) {
    verdictSelect.addEventListener("change", e => {
      state.filterVerdict = e.target.value;
      applyFilters();
    });
  }
  if (searchInput) {
    searchInput.addEventListener("input", e => {
      state.searchQuery = e.target.value.toLowerCase().trim();
      applyFilters();
    });
  }
  if (resetBtn) {
    resetBtn.addEventListener("click", () => {
      if (statusSelect) statusSelect.value = "all";
      if (catSelect) catSelect.value = "all";
      if (verdictSelect) verdictSelect.value = "all";
      if (searchInput) searchInput.value = "";
      state.filterStatus = "all";
      state.filterCategory = "all";
      state.filterVerdict = "all";
      state.searchQuery = "";
      state.selectedHotspotId = null;
      applyFilters();
    });
  }
}

function applyFilters() {
  renderComplaintsList();
  updateMapMarkersVisibility();
}

function renderComplaintsList() {
  renderFilteredComplaints(null);
}

function renderFilteredComplaints(allowedIdSet = null) {
  const container = document.getElementById("complaints-list-container");
  if (!container) return;
  container.replaceChildren();

  const allComplaints = state.report.complaints || [];
  const filtered = allComplaints.filter(c => {
    if (allowedIdSet && !allowedIdSet.has(c.id)) return false;
    if (state.filterStatus !== "all" && c.status !== state.filterStatus) return false;
    if (state.filterCategory !== "all" && c.category !== state.filterCategory) return false;
    if (state.filterVerdict !== "all") {
      const v = state.verdictsMap.get(c.id);
      if (state.filterVerdict === "none" && v) return false;
      if (state.filterVerdict !== "none" && (!v || v.verdict !== state.filterVerdict)) return false;
    }
    if (state.searchQuery) {
      const textMatch = (c.text || "").toLowerCase().includes(state.searchQuery);
      const idMatch = (c.id || "").toLowerCase().includes(state.searchQuery);
      if (!textMatch && !idMatch) return false;
    }
    return true;
  });

  if (filtered.length === 0) {
    const empty = createElem("div", ["state-message"], "No complaints match current filters.");
    container.appendChild(empty);
    return;
  }

  // Render first 50 complaints for performance if very large list
  const displayLimit = Math.min(filtered.length, 100);
  for (let i = 0; i < displayLimit; i++) {
    const c = filtered[i];
    const item = createElem("div", ["complaint-item"]);
    item.dataset.complaintId = c.id;

    const topRow = createElem("div", ["complaint-item-top"]);
    topRow.appendChild(createElem("span", ["complaint-id"], c.id));

    const badgeGroup = createElem("div", ["badge-group"]);
    const catBadge = createElem("span", ["badge", c.status], `${c.category} • ${c.status}`);
    badgeGroup.appendChild(catBadge);

    const verdict = state.verdictsMap.get(c.id);
    if (verdict) {
      const vBadge = createElem("span", ["badge", verdict.verdict], verdict.verdict);
      badgeGroup.appendChild(vBadge);
    }
    topRow.appendChild(badgeGroup);
    item.appendChild(topRow);

    const textSnippet = (c.text || "").length > 80 ? (c.text || "").substring(0, 77) + "..." : c.text;
    const bodyElem = createElem("div", ["complaint-snippet"], textSnippet);
    item.appendChild(bodyElem);

    item.addEventListener("click", () => {
      selectComplaint(c.id);
    });

    container.appendChild(item);
  }

  if (filtered.length > displayLimit) {
    const note = createElem("div", ["state-message"], `Showing 100 of ${filtered.length} complaints. Refine filters to narrow search.`);
    container.appendChild(note);
  }
}

// ---------------------------------------------------------------------------
// Inspection / Repair Checks View
// ---------------------------------------------------------------------------
function selectComplaint(complaintId) {
  state.selectedComplaintId = complaintId;

  document.querySelectorAll(".complaint-item").forEach(item => {
    item.classList.toggle("selected", item.dataset.complaintId === complaintId);
  });

  const complaint = state.complaintsMap.get(complaintId);
  if (!complaint) return;

  renderInspectionDetail(complaint);
  switchTab("tab-inspection");

  // Center on map marker if available
  if (state.markersMap.has(complaintId) && state.map) {
    const marker = state.markersMap.get(complaintId);
    state.map.setView(marker.getLatLng(), 16);
    marker.openPopup();
  }
}

function renderInspectionDetail(complaint) {
  const container = document.getElementById("inspection-detail-container");
  if (!container) return;
  container.replaceChildren();

  const verdict = state.verdictsMap.get(complaint.id);

  // 1. Complaint Overview Card
  const overviewCard = createElem("div", ["inspection-card"]);
  overviewCard.appendChild(createElem("div", ["inspection-title"], `Complaint: ${complaint.id}`));

  const propGrid = createElem("div", ["property-grid"]);
  addProp(propGrid, "Category", complaint.category);
  addProp(propGrid, "Status", complaint.status);
  addProp(propGrid, "Coordinates", isValidCoord(complaint.lat, complaint.lon) ? `${complaint.lat.toFixed(5)}, ${complaint.lon.toFixed(5)}` : "Unavailable");
  addProp(propGrid, "Filed At", complaint.created_at ? new Date(complaint.created_at).toLocaleString() : "Unknown");
  overviewCard.appendChild(propGrid);

  const textBlock = createElem("div", ["inspection-desc"]);
  textBlock.style.marginTop = "0.75rem";
  textBlock.appendChild(createElem("strong", [], "Description: "));
  textBlock.appendChild(document.createTextNode(complaint.text || "No description provided."));
  overviewCard.appendChild(textBlock);

  container.appendChild(overviewCard);

  // 2. Proof-of-Fix Repair Checks Card
  const checkCard = createElem("div", ["inspection-card"]);
  checkCard.appendChild(createElem("div", ["inspection-title"], "Proof-of-Fix Verification Checks"));

  if (verdict) {
    const vHeader = createElem("div", ["verdict-summary"]);
    vHeader.style.marginBottom = "0.75rem";
    vHeader.appendChild(document.createTextNode("Overall Closure Verdict: "));
    const vBadge = createElem("span", ["badge", verdict.verdict], verdict.verdict);
    vBadge.style.fontSize = "0.85rem";
    vHeader.appendChild(vBadge);
    vHeader.appendChild(document.createTextNode(` (Confidence: ${(verdict.confidence * 100).toFixed(0)}%)`));
    checkCard.appendChild(vHeader);

    // Checks Table
    const table = createElem("table", ["checks-table"]);
    const thead = createElem("thead");
    const trH = createElem("tr");
    trH.appendChild(createElem("th", [], "Check Name"));
    trH.appendChild(createElem("th", [], "Result"));
    trH.appendChild(createElem("th", [], "Measured Value"));
    trH.appendChild(createElem("th", [], "Explanation"));
    thead.appendChild(trH);
    table.appendChild(thead);

    const tbody = createElem("tbody");
    (verdict.checks || []).forEach(chk => {
      const row = createElem("tr");
      row.appendChild(createElem("td", [], chk.name));

      const resTd = createElem("td");
      let statusClass = "inconclusive";
      let statusLabel = "INCONCLUSIVE";
      if (chk.passed === true) {
        statusClass = "verified";
        statusLabel = "PASS";
      } else if (chk.passed === false) {
        statusClass = "suspect";
        statusLabel = "FAIL";
      }
      resTd.appendChild(createElem("span", ["badge", statusClass], statusLabel));
      row.appendChild(resTd);

      const valText = chk.value !== null && chk.value !== undefined ? (typeof chk.value === "number" ? chk.value.toFixed(2) : String(chk.value)) : "—";
      row.appendChild(createElem("td", [], valText));
      row.appendChild(createElem("td", [], chk.detail || ""));
      tbody.appendChild(row);
    });
    table.appendChild(tbody);
    checkCard.appendChild(table);
  } else {
    const noVerdict = createElem("div", ["state-message"], complaint.status === "open" ? "Complaint is currently open; no closure verification checks performed." : "No verification verdict record available for this closed complaint.");
    checkCard.appendChild(noVerdict);
  }
  container.appendChild(checkCard);

  // 3. Evidence Gallery Card
  const galleryCard = createElem("div", ["inspection-card"]);
  galleryCard.appendChild(createElem("div", ["inspection-title"], "Photo Evidence"));

  const gallery = createElem("div", ["evidence-gallery"]);

  // Complaint defect media (initial)
  const initialMedia = (complaint.media && complaint.media[0]) ? complaint.media[0] : null;
  gallery.appendChild(createMediaThumbnail("Initial Complaint Photo", initialMedia));

  // Closure proof media
  const closureMedia = (complaint.closure && complaint.closure.media && complaint.closure.media[0]) ? complaint.closure.media[0] : null;
  gallery.appendChild(createMediaThumbnail("Submitted Repair Photo", closureMedia));

  galleryCard.appendChild(gallery);
  container.appendChild(galleryCard);
}

function addProp(grid, name, val) {
  const item = createElem("div", ["property-item"]);
  item.appendChild(createElem("div", ["prop-name"], name));
  item.appendChild(createElem("div", ["prop-val"], val ?? "—"));
  grid.appendChild(item);
}

function createMediaThumbnail(caption, mediaObj) {
  const container = createElem("div", ["evidence-thumbnail"]);
  const imgBox = createElem("div", ["evidence-img-container"]);

  if (mediaObj && mediaObj.path) {
    const resolvedUrl = resolveMediaPath(mediaObj.path);
    if (mediaObj.type === "video") {
      const video = document.createElement("video");
      video.src = resolvedUrl;
      video.controls = true;
      video.preload = "metadata";
      video.onerror = () => {
        imgBox.replaceChildren();
        imgBox.appendChild(createElem("div", ["evidence-placeholder"], "Evidence video unavailable"));
      };
      imgBox.appendChild(video);
    } else {
      const img = document.createElement("img");
      img.src = resolvedUrl;
      img.alt = caption;
      img.loading = "lazy";
      img.onerror = () => {
        imgBox.replaceChildren();
        imgBox.appendChild(createElem("div", ["evidence-placeholder"], "Evidence photo unavailable"));
      };
      imgBox.appendChild(img);
    }
  } else {
    imgBox.appendChild(createElem("div", ["evidence-placeholder"], "No photo submitted"));
  }

  container.appendChild(imgBox);
  container.appendChild(createElem("div", ["evidence-caption"], caption));
  return container;
}

// ---------------------------------------------------------------------------
// Leaflet Map & Overlays (Issue #16)
// ---------------------------------------------------------------------------
function initOrUpdateMap() {
  const mapElement = document.getElementById("map");
  if (!mapElement) return;

  if (typeof L === "undefined") {
    mapElement.replaceChildren();
    const errMsg = createElem("div", ["state-message"], "Leaflet mapping library could not be loaded. Data remains fully accessible via sidebar panels.");
    mapElement.appendChild(errMsg);
    return;
  }

  // First time initialization
  if (!state.map) {
    try {
      state.map = L.map("map", {
        center: [12.9716, 77.5946], // Default Bengaluru
        zoom: 12,
        zoomControl: true
      });

      const tileLayer = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors',
        maxZoom: 19
      });

      tileLayer.on("tileerror", e => {
        console.warn("Tile request failed; map tiles may be temporarily offline.", e);
      });

      tileLayer.addTo(state.map);

      // Feature layer groups
      state.mapLayers.hotspots = L.featureGroup().addTo(state.map);
      state.mapLayers.complaints = L.featureGroup().addTo(state.map);

      // Layer checkboxes
      const toggleHotspots = document.getElementById("toggle-hotspots");
      const toggleComplaints = document.getElementById("toggle-complaints");

      if (toggleHotspots) {
        toggleHotspots.addEventListener("change", e => {
          if (e.target.checked) state.map.addLayer(state.mapLayers.hotspots);
          else state.map.removeLayer(state.mapLayers.hotspots);
        });
      }
      if (toggleComplaints) {
        toggleComplaints.addEventListener("change", e => {
          if (e.target.checked) state.map.addLayer(state.mapLayers.complaints);
          else state.map.removeLayer(state.mapLayers.complaints);
        });
      }

      window.addEventListener("resize", () => {
        if (state.map) state.map.invalidateSize();
      });
    } catch (exc) {
      console.error("Failed to initialize Leaflet map:", exc);
      mapElement.replaceChildren(createElem("div", ["state-message"], "Error initializing map overlay."));
      return;
    }
  }

  renderMapOverlays();
}

function renderMapOverlays() {
  if (!state.map) return;

  state.mapLayers.hotspots.clearLayers();
  state.mapLayers.complaints.clearLayers();
  state.markersMap.clear();
  state.circlesMap.clear();

  const bounds = L.latLngBounds();
  let hasValidCoords = false;

  // 1. Hotspot Circles
  const hotspots = state.report.hotspots || [];
  hotspots.forEach(h => {
    if (!h.centroid || !isValidCoord(h.centroid[0], h.centroid[1])) return;
    const lat = h.centroid[0];
    const lon = h.centroid[1];
    hasValidCoords = true;
    bounds.extend([lat, lon]);

    let circleColor = "#10b981"; // Low severity
    if (h.severity >= 6.0) circleColor = "#ef4444";
    else if (h.severity >= 4.0) circleColor = "#f59e0b";

    const circle = L.circle([lat, lon], {
      color: circleColor,
      fillColor: circleColor,
      fillOpacity: 0.25,
      weight: 2,
      radius: h.radius_m || 50
    });

    // Hotspot Popup
    const popupContent = createElem("div", ["map-popup"]);
    popupContent.appendChild(createElem("strong", [], h.id));
    popupContent.appendChild(document.createElement("br"));
    popupContent.appendChild(document.createTextNode(`Severity: ${Number(h.severity).toFixed(2)} / 10`));
    popupContent.appendChild(document.createElement("br"));
    popupContent.appendChild(document.createTextNode(`Complaints: ${(h.complaint_ids || []).length}`));
    circle.bindPopup(popupContent);

    circle.on("click", () => {
      selectHotspot(h.id);
    });

    state.mapLayers.hotspots.addLayer(circle);
    state.circlesMap.set(h.id, circle);
  });

  // 2. Complaint Markers
  const complaints = state.report.complaints || [];
  complaints.forEach(c => {
    if (!isValidCoord(c.lat, c.lon)) return;
    hasValidCoords = true;
    bounds.extend([c.lat, c.lon]);

    const verdict = state.verdictsMap.get(c.id);

    // Color code based on status and verdict
    let markerColor = "#38bdf8"; // Open: cyan
    if (c.status === "closed") {
      if (verdict && verdict.verdict === "verified") markerColor = "#10b981"; // Green
      else if (verdict && verdict.verdict === "suspect") markerColor = "#ef4444"; // Red
      else markerColor = "#f59e0b"; // Inconclusive
    }

    const marker = L.circleMarker([c.lat, c.lon], {
      radius: 6,
      fillColor: markerColor,
      color: "#0f172a",
      weight: 1.5,
      opacity: 1,
      fillOpacity: 0.9
    });

    // Popup
    const popupBox = createElem("div", ["map-popup"]);
    popupBox.appendChild(createElem("strong", [], c.id));
    popupBox.appendChild(document.createElement("br"));
    popupBox.appendChild(document.createTextNode(`${c.category} • ${c.status}`));
    if (verdict) {
      popupBox.appendChild(document.createElement("br"));
      popupBox.appendChild(document.createTextNode(`Verdict: ${verdict.verdict}`));
    }
    const inspectBtn = createElem("button", ["tab-btn"], "Inspect Details");
    inspectBtn.style.marginTop = "0.5rem";
    inspectBtn.style.padding = "0.2rem 0.5rem";
    inspectBtn.style.fontSize = "0.75rem";
    inspectBtn.style.border = "1px solid #3b82f6";
    inspectBtn.addEventListener("click", () => {
      selectComplaint(c.id);
    });
    popupBox.appendChild(document.createElement("br"));
    popupBox.appendChild(inspectBtn);

    marker.bindPopup(popupBox);

    marker.on("click", () => {
      selectComplaint(c.id);
    });

    state.mapLayers.complaints.addLayer(marker);
    state.markersMap.set(c.id, marker);
  });

  if (hasValidCoords && bounds.isValid()) {
    state.map.fitBounds(bounds, { padding: [30, 30], maxZoom: 14 });
  }
}

function updateMapMarkersVisibility() {
  const complaints = state.report.complaints || [];
  complaints.forEach(c => {
    const marker = state.markersMap.get(c.id);
    if (!marker) return;

    let visible = true;
    if (state.selectedHotspotId) {
      const hotspot = (state.report.hotspots || []).find(h => h.id === state.selectedHotspotId);
      if (hotspot && !hotspot.complaint_ids.includes(c.id)) visible = false;
    }
    if (visible && state.filterStatus !== "all" && c.status !== state.filterStatus) visible = false;
    if (visible && state.filterCategory !== "all" && c.category !== state.filterCategory) visible = false;
    if (visible && state.filterVerdict !== "all") {
      const v = state.verdictsMap.get(c.id);
      if (state.filterVerdict === "none" && v) visible = false;
      if (state.filterVerdict !== "none" && (!v || v.verdict !== state.filterVerdict)) visible = false;
    }
    if (visible && state.searchQuery) {
      const textMatch = (c.text || "").toLowerCase().includes(state.searchQuery);
      const idMatch = (c.id || "").toLowerCase().includes(state.searchQuery);
      if (!textMatch && !idMatch) visible = false;
    }

    if (visible) {
      if (!state.mapLayers.complaints.hasLayer(marker)) {
        state.mapLayers.complaints.addLayer(marker);
      }
    } else {
      if (state.mapLayers.complaints.hasLayer(marker)) {
        state.mapLayers.complaints.removeLayer(marker);
      }
    }
  });
}

// Start on DOM ready
document.addEventListener("DOMContentLoaded", initApp);
