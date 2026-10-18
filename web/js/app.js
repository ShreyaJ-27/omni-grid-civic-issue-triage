/**
 * Omni-Grid Civic Field Desk — Frontend Application
 *
 * Architecture:
 * - Pure vanilla ES6+ (no frameworks)
 * - DOM-safe: all text via textContent, never innerHTML for user data
 * - API-first: renders only reports returned by the live API
 * - On failure: preserves last successful report, shows explicit error + retry
 * - Features: night theme, speech synthesis briefing, JSON export,
 *             AI Auditor simulator, civic data ingestion preview,
 *             keyboard-accessible map/panel selection
 */

"use strict";

// ---------------------------------------------------------------------------
// Application State
// ---------------------------------------------------------------------------
const state = {
  report: null,
  lastSuccessfulReport: null,   // preserved across failed requests
  requestInProgress: false,
  lastRequest: null,            // { endpoint, file } for retry
  map: null,
  mapLayers: { hotspots: null, complaints: null },
  markersMap: new Map(),
  circlesMap: new Map(),
  verdictsMap: new Map(),
  complaintsMap: new Map(),
  selectedComplaintId: null,
  selectedHotspotId: null,
  filterStatus: "all",
  filterCategory: "all",
  filterVerdict: "all",
  searchQuery: "",
  // UI state
  currentPage: "overview",
  currentPanelTab: "panel-briefing",
  nightMode: false,
  speechSynth: null,
  speechUtterance: null,
  isSpeaking: false,
  // Auditor
  selectedScenario: null,
  // Ingestion
  ingestParsed: null,    // { records, format, warnings }
};

// ---------------------------------------------------------------------------
// DOM Helpers
// ---------------------------------------------------------------------------
function createElem(tag, classes = [], text = "") {
  const el = document.createElement(tag);
  if (Array.isArray(classes)) {
    classes.forEach(c => c && el.classList.add(c));
  } else if (typeof classes === "string" && classes) {
    el.className = classes;
  }
  if (text !== undefined && text !== null && text !== "") {
    el.textContent = String(text);
  }
  return el;
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = String(text);
}

function show(el) { if (el) el.style.display = ""; }
function hide(el) { if (el) el.style.display = "none"; }
function showEl(id) { const el = document.getElementById(id); if (el) el.style.display = ""; }
function hideEl(id) { const el = document.getElementById(id); if (el) el.style.display = "none"; }

// ---------------------------------------------------------------------------
// Coordinate Validation
// ---------------------------------------------------------------------------
function isValidCoord(lat, lon) {
  return (
    typeof lat === "number" && typeof lon === "number" &&
    isFinite(lat) && isFinite(lon) &&
    lat >= -90 && lat <= 90 &&
    lon >= -180 && lon <= 180
  );
}

// ---------------------------------------------------------------------------
// Media Path Resolution
// ---------------------------------------------------------------------------
function resolveMediaPath(rawPath) {
  if (!rawPath) return "";
  const cleaned = String(rawPath).trim();
  if (cleaned.startsWith("demo/media/")) return cleaned;
  if (
    state.report &&
    state.report.stats.api_demo_media === true &&
    cleaned.startsWith("data/synthetic/media/")
  ) {
    const filename = cleaned.split("/").pop().split("\\").pop();
    if (/^[a-zA-Z0-9._-]+$/.test(filename)) return `demo/media/${filename}`;
  }
  return "";
}

// ---------------------------------------------------------------------------
// Night/Light Theme
// ---------------------------------------------------------------------------
function initTheme() {
  // Persist across sessions
  const saved = localStorage.getItem("og-theme");
  if (saved === "night") applyTheme(true);

  const btn = document.getElementById("btn-night-view");
  if (btn) {
    btn.addEventListener("click", () => applyTheme(!state.nightMode));
  }
}

function applyTheme(night) {
  state.nightMode = night;
  document.documentElement.setAttribute("data-theme", night ? "night" : "");
  localStorage.setItem("og-theme", night ? "night" : "light");
  const btn = document.getElementById("btn-night-view");
  if (btn) {
    btn.textContent = night ? "Day view" : "Night view";
    btn.setAttribute("aria-pressed", String(night));
  }
}

// ---------------------------------------------------------------------------
// Page Navigation (primary tabs)
// ---------------------------------------------------------------------------
function initPageNav() {
  document.querySelectorAll("[data-page]").forEach(el => {
    el.addEventListener("click", e => {
      const page = e.currentTarget.dataset.page;
      if (page) switchPage(page);
    });
  });
}

function switchPage(pageId) {
  state.currentPage = pageId;

  // Deactivate all nav tabs
  document.querySelectorAll(".nav-tab, .nav-tab-ingestion").forEach(t => {
    t.classList.remove("active");
    t.setAttribute("aria-selected", "false");
  });
  // Activate target tab
  const activeTab = document.querySelector(`[data-page="${pageId}"]`);
  if (activeTab) {
    activeTab.classList.add("active");
    activeTab.setAttribute("aria-selected", "true");
  }

  // Deactivate all pages
  document.querySelectorAll(".tab-page").forEach(p => p.classList.remove("active"));
  // Activate target page
  const page = document.getElementById(`page-${pageId}`);
  if (page) page.classList.add("active");

  // Invalidate map size on tab switch
  if (state.map) setTimeout(() => state.map.invalidateSize(), 150);

  // Sync full-page issue list when switching to Issues tab
  if (pageId === "issues") syncIssuesPageList();
  if (pageId === "hotspots") syncHotspotsPageList();
  if (pageId === "briefings") syncBriefingsPage();
  if (pageId === "proofoffix") syncProofPage();
}

// ---------------------------------------------------------------------------
// Panel Sub-tabs (right panel)
// ---------------------------------------------------------------------------
function initPanelTabs() {
  document.querySelectorAll(".panel-tab").forEach(btn => {
    btn.addEventListener("click", () => {
      const panelId = btn.dataset.panel;
      if (panelId) switchPanel(panelId);
    });
    btn.addEventListener("keydown", e => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        btn.click();
      }
    });
  });
}

function switchPanel(panelId) {
  state.currentPanelTab = panelId;
  document.querySelectorAll(".panel-tab").forEach(t => {
    const active = t.dataset.panel === panelId;
    t.classList.toggle("active", active);
    t.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll(".panel-content").forEach(p => {
    p.classList.toggle("active", p.id === panelId);
  });
  if (state.map) setTimeout(() => state.map.invalidateSize(), 150);
}

// ---------------------------------------------------------------------------
// API Status & Banner
// ---------------------------------------------------------------------------
function setApiStatus(message, status = "idle") {
  const el = document.getElementById("api-status");
  if (el) {
    el.textContent = String(message);
    el.dataset.state = status;
  }

  const banner = document.getElementById("banner-notice");
  if (!banner) return;

  const isError = status === "error";
  banner.classList.toggle("active", isError);
  banner.replaceChildren();

  if (isError) {
    const msgSpan = createElem("span", [], message);
    banner.appendChild(msgSpan);

    if (state.lastRequest) {
      const retryBtn = createElem("button", ["retry-btn"], "Retry");
      retryBtn.type = "button";
      retryBtn.addEventListener("click", () => {
        requestReport(state.lastRequest.endpoint, state.lastRequest.file, retryBtn);
      });
      banner.appendChild(retryBtn);
    }
  }
}

// ---------------------------------------------------------------------------
// Report Validation
// ---------------------------------------------------------------------------
function validateReport(data) {
  if (
    !data || typeof data !== "object" ||
    !Array.isArray(data.complaints) ||
    !Array.isArray(data.hotspots) ||
    !Array.isArray(data.verdicts) ||
    !Array.isArray(data.claims) ||
    !data.stats || typeof data.stats !== "object"
  ) {
    throw new Error("The API response does not match the Omni-Grid Report schema.");
  }
  for (const c of data.complaints) {
    if (!c || typeof c.id !== "string" || !isValidCoord(c.lat, c.lon)) {
      throw new Error("The API returned an invalid complaint in its Report.");
    }
  }
  return data;
}

function apiErrorMessage(error) {
  if (!(error instanceof Error)) return "The API request failed unexpectedly. Please retry.";
  if (error.name === "AbortError")
    return "The request timed out while the service was starting or processing. Please retry.";
  if (error.status === 413) return "The GeoJSON request is too large; the limit is 2 MiB.";
  if (error.status === 422) return `GeoJSON validation failed: ${error.message}`;
  if (error.status === 429) return "Demo request limit reached. Wait a minute and retry.";
  if (error.status === 503) return "The service is busy or waking from a cold start. Please retry.";
  if (error.status >= 500) return "The API is unavailable or encountered an error. Please retry.";
  if (error instanceof TypeError)
    return "Could not reach the API. The service may be starting; wait briefly and retry.";
  return error.message || "The API request failed.";
}

// ---------------------------------------------------------------------------
// API Request
// ---------------------------------------------------------------------------
async function requestReport(endpoint, file, triggerButton = null) {
  if (state.requestInProgress) return;
  state.requestInProgress = true;
  state.lastRequest = { endpoint, file };

  // Disable all action buttons
  const actionBtns = document.querySelectorAll(
    "#btn-run-demo, #btn-run-demo-synthetic, .btn-run-real, .btn-synthetic"
  );
  const fileInput = document.getElementById("geojson-upload");
  actionBtns.forEach(b => { b.disabled = true; });
  if (fileInput) fileInput.disabled = true;

  if (triggerButton) {
    triggerButton.disabled = true;
    triggerButton.setAttribute("aria-busy", "true");
    triggerButton.classList.add("is-loading");
  }

  // Show loading state
  const claimsContainer = document.getElementById("claims-list-container");
  if (claimsContainer) {
    claimsContainer.replaceChildren(
      createLoadingMessage("Processing complaints. The service may take up to three minutes to wake and complete processing.")
    );
  }
  setApiStatus("Processing your dataset. A free service may take up to three minutes to wake and complete processing.", "loading");

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 180000);

  try {
    const options = { method: "POST", signal: controller.signal };

    if (endpoint === "/demo" || endpoint === "/demo/real" || endpoint === "/demo/synthetic") {
      options.method = "POST";
    }

    if (file) {
      if (file.size > 2 * 1024 * 1024) {
        const err = new Error("Selected file exceeds the 2 MiB limit.");
        err.status = 413;
        throw err;
      }
      const form = new FormData();
      form.append("file", file);
      options.body = form;
    }

    const response = await fetch(endpoint, options);

    let payload;
    try { payload = await response.json(); }
    catch { throw new Error(`API returned an unreadable response (HTTP ${response.status}).`); }

    if (!response.ok) {
      const err = new Error(
        payload && typeof payload.detail === "string"
          ? payload.detail
          : `HTTP ${response.status}`
      );
      err.status = response.status;
      throw err;
    }

    const report = validateReport(payload);
    loadReport(report);
    state.lastSuccessfulReport = report;  // preserve on success

    if (report.complaints.length === 0) {
      setApiStatus("The API returned a valid Report with no complaints.", "empty");
    } else {
      const mode = report.stats.model_mode === "mock"
        ? "Deterministic mock mode"
        : "Live mode";
      setApiStatus(
        `Report received from the API: ${report.complaints.length} complaints. ${mode}; inspect checks and evidence below.`,
        "success"
      );
      // Update footer demo mode indicator
      setText("footer-demo-mode",
        endpoint.includes("synthetic") ? "SYNTHETIC SCENARIOS" : "REAL SAMPLE"
      );
    }

  } catch (error) {
    // Preserve last successful report — do NOT clear it
    setApiStatus(apiErrorMessage(error), "error");
    // Only clear the claims loading state if no successful report is available
    if (!state.lastSuccessfulReport) {
      if (claimsContainer) {
        claimsContainer.replaceChildren(createElem("div", ["state-message"],
          "Request failed. Check the banner above and retry."));
      }
    } else {
      // Restore previous report display
      loadReport(state.lastSuccessfulReport);
    }

  } finally {
    clearTimeout(timeout);
    state.requestInProgress = false;
    actionBtns.forEach(b => { b.disabled = false; });
    actionBtns.forEach(b => {
      b.removeAttribute("aria-busy");
      b.classList.remove("is-loading");
    });
    if (triggerButton) {
      triggerButton.disabled = false;
      triggerButton.removeAttribute("aria-busy");
      triggerButton.classList.remove("is-loading");
    }
    if (fileInput) {
      fileInput.disabled = false;
      fileInput.value = "";
    }
  }
}

function createLoadingMessage(message) {
  const wrapper = createElem("div", ["state-message", "loading-message"]);
  wrapper.setAttribute("role", "status");
  wrapper.appendChild(createElem("div", ["state-spinner"]));
  wrapper.appendChild(createElem("p", [], message));
  return wrapper;
}

// ---------------------------------------------------------------------------
// Load Report (orchestrates all renders)
// ---------------------------------------------------------------------------
function loadReport(data) {
  state.report = data;

  // Rebuild lookup maps
  state.verdictsMap.clear();
  (data.verdicts || []).forEach(v => {
    if (v && v.complaint_id) state.verdictsMap.set(String(v.complaint_id), v);
  });
  state.complaintsMap.clear();
  (data.complaints || []).forEach(c => {
    if (c && c.id) state.complaintsMap.set(String(c.id), c);
  });

  renderHeaderStats();
  renderSummaryClaims();
  renderHotspotsList();
  renderComplaintsList();
  renderInspectionEmptyState();
  initOrUpdateMap();
  updateTabCounts();
}

// ---------------------------------------------------------------------------
// Header Stats
// ---------------------------------------------------------------------------
function renderHeaderStats() {
  const stats = state.report.stats || {};
  const total    = stats.total_complaints ?? state.report.complaints.length;
  const hotspots = stats.total_hotspots   ?? state.report.hotspots.length;
  const suspect  = stats.suspect_count    ?? countVerdicts("suspect");
  const verified = stats.verified_count   ?? countVerdicts("verified");

  setText("stat-total-complaints",  total);
  setText("stat-total-hotspots",    hotspots);
  setText("stat-suspect-closures",  suspect);
  setText("stat-verified-closures", verified);
  updateMapCountBadge();
}

function countVerdicts(verdict) {
  let n = 0;
  state.verdictsMap.forEach(v => { if (v.verdict === verdict) n++; });
  return n;
}

function updateMapCountBadge() {
  const badge = document.getElementById("map-count-badge");
  if (!badge) return;
  const visible = state.report
    ? getFilteredComplaints().length
    : 0;
  badge.textContent = `${visible} on map`;
}

function updateTabCounts() {
  if (!state.report) return;
  const claims   = (state.report.claims || []).length;
  const hotspots = (state.report.hotspots || []).length;
  const issues   = (state.report.complaints || []).length;

  setText("tab-count-briefing", claims);
  setText("tab-count-hotspots", hotspots);
  setText("tab-count-issues",   issues);
}

// ---------------------------------------------------------------------------
// Summary Cards — quick-filter shortcuts
// ---------------------------------------------------------------------------
function initSummaryCards() {
  document.querySelectorAll(".summary-card[data-filter-action]").forEach(card => {
    card.addEventListener("click", () => handleCardFilter(card.dataset.filterAction));
    card.addEventListener("keydown", e => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        handleCardFilter(card.dataset.filterAction);
      }
    });
  });
}

function handleCardFilter(action) {
  if (!state.report) {
    // No report yet — trigger synthetic demo on total-issues click
    if (action === "all") {
      requestReport("/demo/synthetic", null, document.getElementById("btn-run-demo-synthetic"));
    }
    return;
  }
  // Clear previous highlight
  document.querySelectorAll(".summary-card").forEach(c => c.classList.remove("active-filter"));
  const card = document.querySelector(`.summary-card[data-filter-action="${action}"]`);
  if (card) card.classList.add("active-filter");

  if (action === "all") {
    resetFilters();
  } else if (action === "hotspots") {
    switchPage("overview");
    switchPanel("panel-hotspots");
  } else if (action === "suspect") {
    setFilter("verdict", "suspect");
    switchPanel("panel-issues");
  } else if (action === "verified") {
    setFilter("verdict", "verified");
    switchPanel("panel-issues");
  }
}

function setFilter(type, value) {
  if (type === "status") {
    state.filterStatus = value;
    const el = document.getElementById("filter-status");
    if (el) el.value = value;
  } else if (type === "category") {
    state.filterCategory = value;
    const el = document.getElementById("filter-category");
    if (el) el.value = value;
  } else if (type === "verdict") {
    state.filterVerdict = value;
    const el = document.getElementById("filter-verdict");
    if (el) el.value = value;
  }
  applyFilters();
}

// ---------------------------------------------------------------------------
// Filter Controls Setup
// ---------------------------------------------------------------------------
function setupFilterControls() {
  const statusSel  = document.getElementById("filter-status");
  const catSel     = document.getElementById("filter-category");
  const verdictSel = document.getElementById("filter-verdict");
  const search     = document.getElementById("search-input");
  const resetBtn   = document.getElementById("btn-reset-filters");
  const fitBtn     = document.getElementById("btn-fit-map");
  const fitBtnFull = document.getElementById("btn-fit-map-full");
  const fullscreenBtn = document.getElementById("btn-fullscreen-map");

  if (statusSel)  statusSel.addEventListener("change",  e => { state.filterStatus   = e.target.value; applyFilters(); });
  if (catSel)     catSel.addEventListener("change",     e => { state.filterCategory = e.target.value; applyFilters(); });
  if (verdictSel) verdictSel.addEventListener("change", e => { state.filterVerdict  = e.target.value; applyFilters(); });
  if (search)     search.addEventListener("input",      e => { state.searchQuery    = e.target.value.toLowerCase().trim(); applyFilters(); });
  if (resetBtn)   resetBtn.addEventListener("click",    () => resetFilters());
  if (fitBtn)     fitBtn.addEventListener("click",      () => fitMapToBounds());
  if (fitBtnFull) fitBtnFull.addEventListener("click",  () => fitMapToBounds());
  if (fullscreenBtn) fullscreenBtn.addEventListener("click", () => switchPage("citymap"));
}

function resetFilters() {
  state.filterStatus   = "all";
  state.filterCategory = "all";
  state.filterVerdict  = "all";
  state.searchQuery    = "";
  state.selectedHotspotId = null;

  ["filter-status", "filter-category", "filter-verdict"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = "all";
  });
  const search = document.getElementById("search-input");
  if (search) search.value = "";

  // Clear active-filter highlight from cards
  document.querySelectorAll(".summary-card").forEach(c => c.classList.remove("active-filter"));

  applyFilters();
}

function applyFilters() {
  renderComplaintsList();
  updateMapMarkersVisibility();
  updateMapCountBadge();
  syncIssuesPageList();
}

// ---------------------------------------------------------------------------
// Get Filtered Complaints
// ---------------------------------------------------------------------------
function getFilteredComplaints(allowedIdSet = null) {
  if (!state.report) return [];
  return (state.report.complaints || []).filter(c => {
    if (allowedIdSet && !allowedIdSet.has(c.id)) return false;
    if (state.filterStatus !== "all"   && c.status   !== state.filterStatus)   return false;
    if (state.filterCategory !== "all" && c.category !== state.filterCategory) return false;
    if (state.filterVerdict !== "all") {
      const v = state.verdictsMap.get(c.id);
      if (state.filterVerdict === "none" && v) return false;
      if (state.filterVerdict !== "none" && (!v || v.verdict !== state.filterVerdict)) return false;
    }
    if (state.searchQuery) {
      const textMatch = (c.text || "").toLowerCase().includes(state.searchQuery);
      const idMatch   = (c.id || "").toLowerCase().includes(state.searchQuery);
      if (!textMatch && !idMatch) return false;
    }
    return true;
  });
}

// ---------------------------------------------------------------------------
// Summary Claims (Briefing Panel)
// ---------------------------------------------------------------------------
function renderSummaryClaims() {
  const container = document.getElementById("claims-list-container");
  if (!container) return;
  container.replaceChildren();

  if (!state.report) {
    const welcome = createElem("section", ["welcome-state"]);
    welcome.setAttribute("aria-labelledby", "welcome-heading");
    welcome.appendChild(Object.assign(createElem("div", ["welcome-icon"], "⌖"), { ariaHidden: "true" }));
    const h2 = createElem("h2", [], "Ready to analyze civic complaints?");
    h2.id = "welcome-heading";
    welcome.appendChild(h2);
    welcome.appendChild(createElem("p", [],
      "Omni-Grid processes complaint data, identifies geographic hotspots, and helps review evidence and closure claims."
    ));
    const demoBtn = createElem("button", ["btn", "btn-primary"], "Run Synthetic Demo");
    demoBtn.type = "button";
    demoBtn.style.marginTop = "0.25rem";
    demoBtn.dataset.runDemo = "";
    welcome.appendChild(demoBtn);
    container.appendChild(welcome);
    return;
  }

  const claims = state.report.claims || [];
  if (claims.length === 0) {
    container.appendChild(createElem("div", ["state-message"], "No summary claims available in this report."));
    return;
  }

  const list = createElem("div", ["claims-list"]);
  claims.forEach(claim => {
    const item = createElem("div", ["claim-item"]);
    const row  = createElem("div", ["claim-accent-bar"]);
    const bar  = createElem("div", ["claim-bar"]);
    const text = createElem("div", ["claim-text"], claim.text);
    row.appendChild(bar);
    row.appendChild(text);
    item.appendChild(row);

    if (Array.isArray(claim.cites) && claim.cites.length > 0) {
      const citeList = createElem("div", ["citation-list"]);
      claim.cites.forEach(citeId => {
        const pill = createElem("button", ["citation-pill"], `Cite: ${citeId}`);
        pill.type = "button";
        pill.setAttribute("aria-label", `Jump to ${citeId}`);
        pill.addEventListener("click", e => {
          e.stopPropagation();
          handleCitationClick(citeId);
        });
        citeList.appendChild(pill);
      });
      item.appendChild(citeList);
    }
    list.appendChild(item);
  });
  container.appendChild(list);
}

function handleCitationClick(citeId) {
  if (state.circlesMap.has(citeId)) {
    const circle = state.circlesMap.get(citeId);
    state.map.setView(circle.getLatLng(), 15);
    circle.openPopup();
    selectHotspot(citeId);
    switchPanel("panel-hotspots");
    return;
  }
  if (state.complaintsMap.has(citeId)) {
    selectComplaint(citeId);
    if (state.markersMap.has(citeId) && state.map) {
      state.map.setView(state.markersMap.get(citeId).getLatLng(), 16);
      state.markersMap.get(citeId).openPopup();
    }
  }
}

// ---------------------------------------------------------------------------
// Hotspots Panel
// ---------------------------------------------------------------------------
function renderHotspotsList() {
  const container = document.getElementById("hotspots-list-container");
  if (!container) return;
  container.replaceChildren();

  if (!state.report) {
    container.appendChild(createElem("div", ["state-message"],
      "Hotspot analysis will appear after a dataset is processed."));
    return;
  }

  const hotspots = state.report.hotspots || [];
  if (hotspots.length === 0) {
    container.appendChild(createElem("div", ["state-message"], "No geographic hotspots detected."));
    return;
  }

  hotspots.forEach((h, idx) => {
    const card = createElem("div", ["hotspot-card"]);
    card.dataset.hotspotId = h.id;
    card.setAttribute("tabindex", "0");
    card.setAttribute("role", "button");
    card.setAttribute("aria-label", `Hotspot ${h.id}, severity ${Number(h.severity).toFixed(2)}`);

    const header = createElem("div", ["hotspot-header"]);
    header.appendChild(createElem("span", ["hotspot-id"], h.id));

    const sevTag = createElem("span", ["severity-tag"],
      `Severity ${Number(h.severity).toFixed(2)} / 10`);
    if (h.severity >= 6.0)      sevTag.classList.add("severity-high");
    else if (h.severity >= 4.0) sevTag.classList.add("severity-med");
    else                        sevTag.classList.add("severity-low");
    header.appendChild(sevTag);
    card.appendChild(header);

    const statsDiv = createElem("div", ["hotspot-stats"]);
    const countStat = createElem("span", []);
    countStat.textContent = "";
    const countStrong = createElem("strong", [], String((h.complaint_ids || []).length));
    countStat.appendChild(countStrong);
    countStat.appendChild(document.createTextNode(" complaints"));
    statsDiv.appendChild(countStat);
    const radiusStat = createElem("span", []);
    const radStrong = createElem("strong", [], `${Number(h.radius_m).toFixed(0)} m`);
    radiusStat.appendChild(radStrong);
    radiusStat.appendChild(document.createTextNode(" radius"));
    statsDiv.appendChild(radiusStat);
    card.appendChild(statsDiv);

    const clickHandler = () => {
      selectHotspot(h.id);
      if (isValidCoord(h.centroid[0], h.centroid[1]) && state.map) {
        state.map.setView([h.centroid[0], h.centroid[1]], 15);
        const circle = state.circlesMap.get(h.id);
        if (circle) circle.openPopup();
      }
    };
    card.addEventListener("click", clickHandler);
    card.addEventListener("keydown", e => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); clickHandler(); }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        const cards = [...document.querySelectorAll(".hotspot-card")];
        const next = cards[idx + 1];
        if (next) next.focus();
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        const cards = [...document.querySelectorAll(".hotspot-card")];
        const prev = cards[idx - 1];
        if (prev) prev.focus();
      }
    });

    container.appendChild(card);
  });
}

function selectHotspot(hotspotId) {
  state.selectedHotspotId = hotspotId;
  document.querySelectorAll(".hotspot-card").forEach(c => {
    const active = c.dataset.hotspotId === hotspotId;
    c.classList.toggle("selected", active);
    c.setAttribute("aria-selected", String(active));
  });

  const hotspot = (state.report.hotspots || []).find(h => h.id === hotspotId);
  if (hotspot && Array.isArray(hotspot.complaint_ids)) {
    renderFilteredComplaintsById(new Set(hotspot.complaint_ids));
    switchPanel("panel-issues");
  }
}

// ---------------------------------------------------------------------------
// Complaints / Issues Panel
// ---------------------------------------------------------------------------
function renderComplaintsList() {
  renderFilteredComplaintsById(null);
}

function renderFilteredComplaintsById(allowedIdSet = null) {
  const container = document.getElementById("complaints-list-container");
  if (!container) return;
  container.replaceChildren();

  if (!state.report) {
    container.appendChild(createElem("div", ["state-message"],
      "Complaints will appear here after a dataset is processed."));
    return;
  }

  const filtered = getFilteredComplaints(allowedIdSet);
  renderComplaintItems(container, filtered);
}

function renderComplaintItems(container, complaints, limit = 100) {
  if (complaints.length === 0) {
    const msg = state.report && state.report.complaints.length === 0
      ? "No complaints are available in this report."
      : "No complaints match current filters.";
    container.appendChild(createElem("div", ["state-message"], msg));
    return;
  }

  const shown = Math.min(complaints.length, limit);
  complaints.slice(0, shown).forEach((c, idx) => {
    const item = createElem("div", ["complaint-item"]);
    item.dataset.complaintId = c.id;
    item.setAttribute("tabindex", "0");
    item.setAttribute("role", "button");
    item.setAttribute("aria-label", `Complaint ${c.id}: ${c.category} ${c.status}`);

    const topRow = createElem("div", ["complaint-item-top"]);
    topRow.appendChild(createElem("span", ["complaint-id"], c.id));

    const badgeGroup = createElem("div", ["badge-group"]);
    badgeGroup.appendChild(createElem("span", ["badge", c.status], `${c.category} · ${c.status}`));

    const verdict = state.verdictsMap.get(c.id);
    if (verdict) {
      badgeGroup.appendChild(createElem("span", ["badge", verdict.verdict], verdict.verdict));
    }
    topRow.appendChild(badgeGroup);
    item.appendChild(topRow);

    const snippet = (c.text || "").length > 80
      ? (c.text || "").substring(0, 77) + "…"
      : (c.text || "");
    item.appendChild(createElem("div", ["complaint-snippet"], snippet));

    const clickHandler = () => selectComplaint(c.id);
    item.addEventListener("click", clickHandler);
    item.addEventListener("keydown", e => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); clickHandler(); }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        const items = [...container.querySelectorAll(".complaint-item")];
        const next = items[idx + 1];
        if (next) next.focus();
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        const items = [...container.querySelectorAll(".complaint-item")];
        const prev = items[idx - 1];
        if (prev) prev.focus();
      }
    });

    container.appendChild(item);
  });

  if (complaints.length > limit) {
    container.appendChild(createElem("div", ["state-message"],
      `Showing ${limit} of ${complaints.length} complaints. Refine filters to narrow.`));
  }
}

// ---------------------------------------------------------------------------
// Select Complaint
// ---------------------------------------------------------------------------
function selectComplaint(complaintId) {
  state.selectedComplaintId = complaintId;

  document.querySelectorAll(".complaint-item").forEach(item => {
    const active = item.dataset.complaintId === complaintId;
    item.classList.toggle("selected", active);
    item.setAttribute("aria-selected", String(active));
  });

  const complaint = state.complaintsMap.get(complaintId);
  if (!complaint) return;

  renderInspectionDetail(complaint);
  switchPanel("panel-proof");
  syncProofPage();

  if (state.markersMap.has(complaintId) && state.map) {
    const marker = state.markersMap.get(complaintId);
    state.map.setView(marker.getLatLng(), 16);
    marker.openPopup();
  }
}

// ---------------------------------------------------------------------------
// Inspection / Proof-of-Fix Panel
// ---------------------------------------------------------------------------
function renderInspectionEmptyState() {
  const container = document.getElementById("inspection-detail-container");
  if (!container) return;
  const msg = state.report
    ? "Select a complaint on the map or from the Issues panel to view its verification checks."
    : "Verification results will appear here after a dataset is processed.";
  container.replaceChildren(createElem("div", ["state-message"], msg));
}

function renderInspectionDetail(complaint) {
  const container = document.getElementById("inspection-detail-container");
  if (!container) return;
  container.replaceChildren();

  const verdict = state.verdictsMap.get(complaint.id);

  // 1. Complaint overview card
  const overviewCard = createElem("div", ["inspection-card"]);
  overviewCard.appendChild(createElem("div", ["inspection-title"], `Complaint: ${complaint.id}`));
  const grid = createElem("div", ["property-grid"]);
  addProp(grid, "Category",    complaint.category);
  addProp(grid, "Status",      complaint.status);
  addProp(grid, "Coordinates",
    isValidCoord(complaint.lat, complaint.lon)
      ? `${complaint.lat.toFixed(5)}, ${complaint.lon.toFixed(5)}`
      : "Unavailable"
  );
  addProp(grid, "Filed at",
    complaint.created_at
      ? new Date(complaint.created_at).toLocaleString()
      : "Unknown"
  );
  overviewCard.appendChild(grid);

  const textBlock = createElem("div", []);
  textBlock.style.marginTop = "0.65rem";
  textBlock.style.fontSize  = "0.85rem";
  const strong = createElem("strong", [], "Description: ");
  textBlock.appendChild(strong);
  textBlock.appendChild(document.createTextNode(complaint.text || "No description provided."));
  overviewCard.appendChild(textBlock);
  container.appendChild(overviewCard);

  // 2. Proof-of-fix checks card
  const checkCard = createElem("div", ["inspection-card"]);
  checkCard.appendChild(createElem("div", ["inspection-title"], "Proof-of-Fix Verification Checks"));

  if (verdict) {
    const vRow = createElem("div", ["verdict-summary"]);
    vRow.appendChild(document.createTextNode("Overall Verdict: "));
    const vBadge = createElem("span", ["badge", verdict.verdict], verdict.verdict);
    vRow.appendChild(vBadge);
    vRow.appendChild(document.createTextNode(` (Confidence: ${(verdict.confidence * 100).toFixed(0)}%)`));
    checkCard.appendChild(vRow);

    const table = createElem("table", ["checks-table"]);
    table.setAttribute("aria-label", `Verification checks for ${complaint.id}`);
    const thead = createElem("thead");
    const trh = createElem("tr");
    ["Check Name", "Result", "Measured Value", "Explanation"].forEach(h => {
      trh.appendChild(createElem("th", [], h));
    });
    thead.appendChild(trh);
    table.appendChild(thead);

    const tbody = createElem("tbody");
    (verdict.checks || []).forEach(chk => {
      const tr = createElem("tr");
      tr.appendChild(createElem("td", [], chk.name));

      const resTd = createElem("td");
      let cls = "inconclusive", lbl = "INCONCLUSIVE";
      if (chk.passed === true)  { cls = "verified"; lbl = "PASS"; }
      if (chk.passed === false) { cls = "suspect";  lbl = "FAIL"; }
      resTd.appendChild(createElem("span", ["badge", cls], lbl));
      tr.appendChild(resTd);

      const val = chk.value !== null && chk.value !== undefined
        ? (typeof chk.value === "number" ? chk.value.toFixed(2) : String(chk.value))
        : "—";
      tr.appendChild(createElem("td", [], val));
      tr.appendChild(createElem("td", [], chk.detail || ""));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    checkCard.appendChild(table);
  } else {
    const msg = complaint.status === "open"
      ? "Complaint is currently open; no closure verification checks performed."
      : "No verification verdict record available for this closed complaint.";
    checkCard.appendChild(createElem("div", ["state-message"], msg));
  }
  container.appendChild(checkCard);

  // 3. Evidence gallery
  const galleryCard = createElem("div", ["inspection-card"]);
  galleryCard.appendChild(createElem("div", ["inspection-title"], "Photo Evidence"));
  const gallery = createElem("div", ["evidence-gallery"]);

  const initialMedia = (complaint.media && complaint.media[0]) ? complaint.media[0] : null;
  gallery.appendChild(createMediaThumbnail("Initial Complaint Photo", initialMedia));

  const closureMedia = (complaint.closure && complaint.closure.media && complaint.closure.media[0])
    ? complaint.closure.media[0] : null;
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
  const thumb = createElem("div", ["evidence-thumbnail"]);
  const imgBox = createElem("div", ["evidence-img-container"]);

  if (mediaObj && mediaObj.path) {
    const url = resolveMediaPath(mediaObj.path);
    if (mediaObj.type === "video") {
      const video = document.createElement("video");
      video.src = url;
      video.controls = true;
      video.preload = "metadata";
      video.onerror = () => {
        imgBox.replaceChildren(createElem("div", ["evidence-placeholder"], "Evidence video unavailable"));
      };
      imgBox.appendChild(video);
    } else {
      const img = document.createElement("img");
      img.src = url;
      img.alt = caption;
      img.loading = "lazy";
      img.onerror = () => {
        imgBox.replaceChildren(createElem("div", ["evidence-placeholder"], "Evidence photo unavailable"));
      };
      imgBox.appendChild(img);
    }
  } else {
    imgBox.appendChild(createElem("div", ["evidence-placeholder"], "No photo submitted"));
  }

  thumb.appendChild(imgBox);
  thumb.appendChild(createElem("div", ["evidence-caption"], caption));
  return thumb;
}

// ---------------------------------------------------------------------------
// Full-page tab sync helpers
// ---------------------------------------------------------------------------
function syncIssuesPageList() {
  const container = document.getElementById("issues-page-list");
  if (!container) return;
  container.replaceChildren();
  const complaints = getFilteredComplaints();
  renderComplaintItems(container, complaints);
}

function syncHotspotsPageList() {
  const container = document.getElementById("hotspots-page-list");
  if (!container) return;
  container.replaceChildren();
  if (!state.report) {
    container.appendChild(createElem("div", ["state-message"],
      "Hotspot analysis will appear after a dataset is processed."));
    return;
  }
  // Reuse the hotspot rendering logic but into a different container
  const hotspots = state.report.hotspots || [];
  if (hotspots.length === 0) {
    container.appendChild(createElem("div", ["state-message"], "No geographic hotspots detected."));
    return;
  }
  hotspots.forEach(h => {
    const card = createElem("div", ["hotspot-card"]);
    card.setAttribute("tabindex", "0");
    card.setAttribute("role", "button");
    const header = createElem("div", ["hotspot-header"]);
    header.appendChild(createElem("span", ["hotspot-id"], h.id));
    const sev = createElem("span", ["severity-tag"], `Sev ${Number(h.severity).toFixed(2)}`);
    if (h.severity >= 6) sev.classList.add("severity-high");
    else if (h.severity >= 4) sev.classList.add("severity-med");
    else sev.classList.add("severity-low");
    header.appendChild(sev);
    card.appendChild(header);
    container.appendChild(card);
  });
}

function syncBriefingsPage() {
  const container = document.getElementById("briefings-claims-container");
  if (!container) return;
  container.replaceChildren();
  if (!state.report || !state.report.claims.length) {
    container.appendChild(createElem("div", ["state-message"],
      "Briefing claims will appear after a dataset is processed."));
    return;
  }
  state.report.claims.forEach(claim => {
    const item = createElem("div", ["claim-item"]);
    const row = createElem("div", ["claim-accent-bar"]);
    row.appendChild(createElem("div", ["claim-bar"]));
    row.appendChild(createElem("div", ["claim-text"], claim.text));
    item.appendChild(row);
    container.appendChild(item);
  });
}

function syncProofPage() {
  const container = document.getElementById("proof-page-container");
  if (!container) return;
  if (!state.selectedComplaintId) {
    container.replaceChildren(createElem("div", ["state-message"],
      "Select a complaint from the Issues tab or map to view its verification checks."));
    return;
  }
  const complaint = state.complaintsMap.get(state.selectedComplaintId);
  if (!complaint) return;
  // Render proof content into this container too
  container.replaceChildren();
  const verdict = state.verdictsMap.get(complaint.id);
  if (verdict) {
    const card = createElem("div", ["inspection-card"]);
    card.appendChild(createElem("div", ["inspection-title"], `Proof of Fix: ${complaint.id}`));
    const vRow = createElem("div", ["verdict-summary"]);
    vRow.appendChild(document.createTextNode("Verdict: "));
    vRow.appendChild(createElem("span", ["badge", verdict.verdict], verdict.verdict));
    card.appendChild(vRow);
    container.appendChild(card);
  } else {
    container.appendChild(createElem("div", ["state-message"],
      `No verdict available for complaint ${state.selectedComplaintId}.`));
  }
}

// ---------------------------------------------------------------------------
// Leaflet Map
// ---------------------------------------------------------------------------
function initOrUpdateMap() {
  const mapEl = document.getElementById("map");
  if (!mapEl) return;

  if (typeof L === "undefined") {
    mapEl.replaceChildren(createElem("div", ["state-message"],
      "Leaflet mapping library could not be loaded. Data remains accessible via sidebar panels."));
    return;
  }

  if (!state.map) {
    try {
      state.map = L.map("map", {
        center: [12.9716, 77.5946],
        zoom: 12,
        zoomControl: true,
      });

      const tiles = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution:
          '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors',
        maxZoom: 19,
      });
      tiles.on("tileerror", e => console.warn("Map tile failed:", e));
      tiles.addTo(state.map);

      state.mapLayers.hotspots   = L.featureGroup().addTo(state.map);
      state.mapLayers.complaints = L.featureGroup().addTo(state.map);

      const toggleHotspots   = document.getElementById("toggle-hotspots");
      const toggleComplaints = document.getElementById("toggle-complaints");
      if (toggleHotspots) {
        toggleHotspots.addEventListener("change", e => {
          e.target.checked
            ? state.map.addLayer(state.mapLayers.hotspots)
            : state.map.removeLayer(state.mapLayers.hotspots);
        });
      }
      if (toggleComplaints) {
        toggleComplaints.addEventListener("change", e => {
          e.target.checked
            ? state.map.addLayer(state.mapLayers.complaints)
            : state.map.removeLayer(state.mapLayers.complaints);
        });
      }

      window.addEventListener("resize", () => state.map && state.map.invalidateSize());
    } catch (err) {
      console.error("Map init failed:", err);
      mapEl.replaceChildren(createElem("div", ["state-message"], "Error initializing map overlay."));
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
  let hasCoords = false;

  // Hotspot circles
  (state.report?.hotspots || []).forEach(h => {
    if (!h.centroid || !isValidCoord(h.centroid[0], h.centroid[1])) return;
    hasCoords = true;
    bounds.extend([h.centroid[0], h.centroid[1]]);

    let color = "#16a34a";
    if (h.severity >= 6.0)      color = "#dc2626";
    else if (h.severity >= 4.0) color = "#d97706";

    const circle = L.circle([h.centroid[0], h.centroid[1]], {
      color, fillColor: color, fillOpacity: 0.2, weight: 2,
      radius: h.radius_m || 50,
    });

    const popup = createElem("div", []);
    const title = createElem("div", ["map-popup-title"], h.id);
    const detail = createElem("div", ["map-popup-detail"]);
    detail.textContent = `Severity: ${Number(h.severity).toFixed(2)} / 10 · ${(h.complaint_ids || []).length} complaints`;
    popup.appendChild(title);
    popup.appendChild(detail);
    circle.bindPopup(popup);

    circle.on("click", () => {
      selectHotspot(h.id);
      switchPanel("panel-hotspots");
    });

    state.mapLayers.hotspots.addLayer(circle);
    state.circlesMap.set(h.id, circle);
  });

  // Complaint markers
  (state.report?.complaints || []).forEach(c => {
    if (!isValidCoord(c.lat, c.lon)) return;
    hasCoords = true;
    bounds.extend([c.lat, c.lon]);

    const verdict = state.verdictsMap.get(c.id);
    let color = "#0284c7";  // open: blue
    if (c.status === "closed") {
      if (verdict?.verdict === "verified")     color = "#16a34a";
      else if (verdict?.verdict === "suspect") color = "#dc2626";
      else                                     color = "#d97706";
    }

    const marker = L.circleMarker([c.lat, c.lon], {
      radius: 5.5, fillColor: color, color: "#ffffff",
      weight: 1.5, opacity: 1, fillOpacity: 0.9,
    });

    const popup = createElem("div", []);
    const title = createElem("div", ["map-popup-title"], c.id);
    const detail = createElem("div", ["map-popup-detail"]);
    detail.textContent = `${c.category} · ${c.status}`;
    if (verdict) {
      const vSpan = createElem("span", ["badge", verdict.verdict], verdict.verdict);
      vSpan.style.display = "inline-flex";
      vSpan.style.marginTop = "0.3rem";
      detail.appendChild(document.createElement("br"));
      detail.appendChild(document.createTextNode("Verdict: "));
      detail.appendChild(vSpan);
    }
    const inspectBtn = createElem("button", ["map-popup-inspect"], "Inspect Details");
    inspectBtn.type = "button";
    inspectBtn.addEventListener("click", () => selectComplaint(c.id));
    popup.appendChild(title);
    popup.appendChild(detail);
    popup.appendChild(document.createElement("br"));
    popup.appendChild(inspectBtn);
    marker.bindPopup(popup);

    marker.on("click", () => selectComplaint(c.id));

    state.mapLayers.complaints.addLayer(marker);
    state.markersMap.set(c.id, marker);
  });

  if (hasCoords && bounds.isValid()) {
    state.map.fitBounds(bounds, { padding: [32, 32], maxZoom: 14 });
  }

  updateMapCountBadge();
}

function fitMapToBounds() {
  if (!state.map) return;
  if (state.mapLayers.complaints && state.mapLayers.hotspots) {
    const combined = L.featureGroup([
      state.mapLayers.complaints,
      state.mapLayers.hotspots,
    ]);
    const bounds = combined.getBounds();
    if (bounds.isValid()) {
      state.map.fitBounds(bounds, { padding: [32, 32], maxZoom: 14 });
    }
  } else {
    state.map.setView([12.9716, 77.5946], 12);
  }
}

function updateMapMarkersVisibility() {
  (state.report?.complaints || []).forEach(c => {
    const marker = state.markersMap.get(c.id);
    if (!marker) return;

    let visible = true;
    if (state.selectedHotspotId) {
      const h = (state.report?.hotspots || []).find(x => x.id === state.selectedHotspotId);
      if (h && !h.complaint_ids.includes(c.id)) visible = false;
    }
    if (visible && state.filterStatus   !== "all" && c.status   !== state.filterStatus)   visible = false;
    if (visible && state.filterCategory !== "all" && c.category !== state.filterCategory) visible = false;
    if (visible && state.filterVerdict  !== "all") {
      const v = state.verdictsMap.get(c.id);
      if (state.filterVerdict === "none" && v) visible = false;
      if (state.filterVerdict !== "none" && (!v || v.verdict !== state.filterVerdict)) visible = false;
    }
    if (visible && state.searchQuery) {
      const tm = (c.text || "").toLowerCase().includes(state.searchQuery);
      const im = (c.id   || "").toLowerCase().includes(state.searchQuery);
      if (!tm && !im) visible = false;
    }

    if (visible) {
      if (!state.mapLayers.complaints.hasLayer(marker)) state.mapLayers.complaints.addLayer(marker);
    } else {
      if (state.mapLayers.complaints.hasLayer(marker)) state.mapLayers.complaints.removeLayer(marker);
    }
  });
}

// ---------------------------------------------------------------------------
// Speech Synthesis Briefing
// ---------------------------------------------------------------------------
function initSpeech() {
  if (!("speechSynthesis" in window)) return;
  state.speechSynth = window.speechSynthesis;

  ["btn-listen-briefing", "btn-listen-briefing-full"].forEach(id => {
    const btn = document.getElementById(id);
    if (!btn) return;
    btn.addEventListener("click", () => toggleSpeech(btn));
  });
}

function toggleSpeech(btn) {
  if (!state.speechSynth) {
    btn.textContent = "🔇 Speech not available in this browser.";
    return;
  }
  if (state.isSpeaking) {
    state.speechSynth.cancel();
    state.isSpeaking = false;
    updateSpeechButtons(false);
    return;
  }

  // Build briefing text
  if (!state.report) {
    const utt = new SpeechSynthesisUtterance("No report loaded. Run a demo or upload data to generate a briefing.");
    state.speechSynth.speak(utt);
    return;
  }

  const claims  = (state.report.claims  || []).map(c => c.text).join(". ");
  const stats   = state.report.stats || {};
  const total   = stats.total_complaints ?? state.report.complaints.length;
  const hotspot = stats.total_hotspots   ?? state.report.hotspots.length;
  const text    = `City Briefing for Bengaluru. ${total} complaints in the dataset. ${hotspot} geographic hotspots identified. ${claims}`;

  const utt = new SpeechSynthesisUtterance(text);
  utt.rate  = 0.95;
  utt.pitch = 1.0;
  utt.onstart = () => { state.isSpeaking = true;  updateSpeechButtons(true); };
  utt.onend   = () => { state.isSpeaking = false; updateSpeechButtons(false); };
  utt.onerror = () => { state.isSpeaking = false; updateSpeechButtons(false); };
  state.speechSynth.speak(utt);
}

function updateSpeechButtons(speaking) {
  ["btn-listen-briefing", "btn-listen-briefing-full"].forEach(id => {
    const btn = document.getElementById(id);
    if (!btn) return;
    btn.textContent = speaking ? "⏹ Stop briefing" : "🔊 Listen to briefing";
    btn.classList.toggle("is-speaking", speaking);
    btn.setAttribute("aria-pressed", String(speaking));
  });
}

// ---------------------------------------------------------------------------
// JSON Export
// ---------------------------------------------------------------------------
function initJsonExport() {
  ["btn-download-json", "btn-download-json-full"].forEach(id => {
    const btn = document.getElementById(id);
    if (!btn) return;
    btn.addEventListener("click", downloadReportJson);
  });
}

function downloadReportJson() {
  if (!state.report) {
    alert("No report loaded. Run a demo or upload data first.");
    return;
  }
  const json = JSON.stringify(state.report, null, 2);
  const blob = new Blob([json], { type: "application/json" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = `omni-grid-report-${new Date().toISOString().slice(0, 10)}.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// AI Auditor Modal
// ---------------------------------------------------------------------------
const AUDITOR_SCENARIOS = {
  geofence: {
    title: "Geofence Spoofing",
    before: "Defect: pothole on 5th Cross Road (lat 12.971, lon 77.594)",
    after:  "Closure photo GPS: lat 12.981, lon 77.601 — 1.3 km away from complaint",
    checks: [
      { name: "GPS geofence",       passed: false,  value: "1310 m",    detail: "Closure photo taken 1.3 km from complaint; threshold 200 m." },
      { name: "Timestamp sequence", passed: true,   value: "36 h",      detail: "Closure filed after complaint; plausible window." },
      { name: "Defect class match", passed: true,   value: "pothole",   detail: "Defect class in closure matches complaint category." },
      { name: "Perceptual hash",    passed: true,   value: "unique",    detail: "Image hash not found in other closure records in this dataset." },
    ],
    verdict: "suspect", confidence: 0.91,
    guidance: "Closure location is inconsistent with the complaint site. Field verification required before accepting this closure. Do not mark verified without physical inspection.",
  },
  mismatch: {
    title: "Defect-class Mismatch",
    before: "Complaint: waterlogging on Outer Ring Road (status: closed)",
    after:  "Closure photo description: road surface with no visible water or flooding",
    checks: [
      { name: "GPS geofence",       passed: true,   value: "48 m",      detail: "Closure photo within acceptable radius." },
      { name: "Timestamp sequence", passed: true,   value: "12 h",      detail: "Closure filed within 12 hours of complaint." },
      { name: "Defect class match", passed: false,  value: "mismatch",  detail: "Complaint category is waterlogging; closure photo shows dry road surface — no evidence of water removal or drainage work." },
      { name: "Perceptual hash",    passed: true,   value: "unique",    detail: "Image hash not found in other closure records in this dataset." },
    ],
    verdict: "suspect", confidence: 0.87,
    guidance: "The closure photo does not match the reported defect category. Either the wrong photo was submitted, or the wrong complaint was closed. Human review required.",
  },
  hash: {
    title: "Reused Evidence / Hash Match",
    before: "Complaint: pothole on BTM Layout (complaint-042)",
    after:  "Closure photo hash matches complaint-017 (filed 8 days earlier, different location)",
    checks: [
      { name: "GPS geofence",       passed: true,   value: "92 m",      detail: "Closure photo within acceptable radius of complaint." },
      { name: "Timestamp sequence", passed: true,   value: "2 days",    detail: "Closure filed two days after complaint." },
      { name: "Defect class match", passed: true,   value: "pothole",   detail: "Defect class matches complaint category." },
      { name: "Perceptual hash",    passed: false,  value: "duplicate", detail: "Image hash matches a closure photo already submitted for complaint-017 at a different location within this dataset. Possible reuse of evidence." },
    ],
    verdict: "suspect", confidence: 0.94,
    guidance: "The closure photo appears to have been used for a different complaint earlier in this dataset. This may indicate evidence reuse. Field verification required. Note: hash check compares images within this dataset only — not a web or stock-photo search.",
  },
  genuine: {
    title: "Genuine Repair",
    before: "Complaint: pothole on Jayanagar 4th Block (status: closed)",
    after:  "Closure photo shows repaired road surface with fresh asphalt fill",
    checks: [
      { name: "GPS geofence",       passed: true,   value: "55 m",      detail: "Closure photo within 55 m of complaint; threshold 200 m." },
      { name: "Timestamp sequence", passed: true,   value: "18 h",      detail: "Closure filed within expected repair window." },
      { name: "Defect class match", passed: true,   value: "pothole",   detail: "Closure shows road patching consistent with pothole repair." },
      { name: "Perceptual hash",    passed: true,   value: "unique",    detail: "Image hash not found in other closure records in this dataset." },
    ],
    verdict: "verified", confidence: 0.96,
    guidance: "All deterministic checks pass. Closure appears to be a genuine repair at the correct location with matching evidence. No immediate human review required, but periodic field spot-checks are recommended.",
  },
};

function initAuditorModal() {
  const openBtn  = document.getElementById("btn-open-auditor");
  const closeBtn = document.getElementById("btn-close-auditor");
  const runBtn   = document.getElementById("btn-run-audit");
  const overlay  = document.getElementById("auditor-modal");
  const panel    = document.getElementById("auditor-modal-panel");

  if (openBtn)  openBtn.addEventListener("click",  () => openAuditor());
  if (closeBtn) closeBtn.addEventListener("click", () => closeAuditor());

  // Scenario selection
  document.querySelectorAll(".scenario-card[data-scenario]").forEach(card => {
    card.addEventListener("click", () => selectScenario(card.dataset.scenario));
    card.addEventListener("keydown", e => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectScenario(card.dataset.scenario); }
    });
  });

  if (runBtn) runBtn.addEventListener("click", () => runAudit());

  // Close on overlay background click
  if (overlay) {
    overlay.addEventListener("click", e => {
      if (e.target === overlay) closeAuditor();
    });
  }

  // Close on Escape
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && overlay && !overlay.classList.contains("hidden")) closeAuditor();
  });
}

function openAuditor() {
  const overlay = document.getElementById("auditor-modal");
  const panel   = document.getElementById("auditor-modal-panel");
  if (!overlay) return;
  overlay.classList.remove("hidden");
  // Focus trap
  if (panel) panel.focus();
  document.body.style.overflow = "hidden";
}

function closeAuditor() {
  const overlay = document.getElementById("auditor-modal");
  if (!overlay) return;
  overlay.classList.add("hidden");
  document.body.style.overflow = "";
  // Return focus
  const openBtn = document.getElementById("btn-open-auditor");
  if (openBtn) openBtn.focus();
}

function selectScenario(id) {
  state.selectedScenario = id;
  document.querySelectorAll(".scenario-card").forEach(c => {
    const active = c.dataset.scenario === id;
    c.classList.toggle("selected", active);
    c.setAttribute("aria-selected", String(active));
  });
  const runBtn = document.getElementById("btn-run-audit");
  const hint   = document.getElementById("audit-run-hint");
  if (runBtn) runBtn.disabled = false;
  if (hint)   hint.textContent = `Ready to run: ${AUDITOR_SCENARIOS[id]?.title || id}`;

  // Hide previous results
  const results = document.getElementById("auditor-results");
  if (results) results.classList.remove("visible");
}

function runAudit() {
  const id = state.selectedScenario;
  if (!id) return;
  const scenario = AUDITOR_SCENARIOS[id];
  if (!scenario) return;

  const runBtn = document.getElementById("btn-run-audit");
  const hint   = document.getElementById("audit-run-hint");
  if (runBtn) { runBtn.disabled = true; runBtn.textContent = "Running…"; }

  // Simulate a brief processing delay for realism
  setTimeout(() => {
    if (runBtn) { runBtn.disabled = false; runBtn.textContent = "Run Audit Pipeline"; }
    if (hint)   hint.textContent = "Audit complete.";
    renderAuditResults(scenario);
  }, 600);
}

function renderAuditResults(scenario) {
  const results = document.getElementById("auditor-results");
  if (!results) return;

  // Evidence placeholders
  const beforeImg = document.getElementById("auditor-before-img");
  const afterImg  = document.getElementById("auditor-after-img");
  if (beforeImg) beforeImg.textContent = scenario.before;
  if (afterImg)  afterImg.textContent  = scenario.after;

  // Checks table
  const tbody = document.getElementById("auditor-checks-tbody");
  if (tbody) {
    tbody.replaceChildren();
    scenario.checks.forEach(chk => {
      const tr = createElem("tr");
      tr.appendChild(createElem("td", [], chk.name));
      const resTd = createElem("td");
      let cls = "inconclusive", lbl = "INCONCLUSIVE";
      if (chk.passed === true)  { cls = "verified"; lbl = "PASS"; }
      if (chk.passed === false) { cls = "suspect";  lbl = "FAIL"; }
      resTd.appendChild(createElem("span", ["badge", cls], lbl));
      tr.appendChild(resTd);
      tr.appendChild(createElem("td", [], String(chk.value ?? "—")));
      tr.appendChild(createElem("td", [], chk.detail || ""));
      tbody.appendChild(tr);
    });
  }

  // Verdict
  const verdictBadge = document.getElementById("auditor-verdict-badge");
  const confidence   = document.getElementById("auditor-confidence");
  if (verdictBadge) {
    verdictBadge.textContent = scenario.verdict;
    verdictBadge.className = `badge ${scenario.verdict}`;
  }
  if (confidence) confidence.textContent = `Confidence: ${(scenario.confidence * 100).toFixed(0)}%`;

  // Guidance
  const reviewText = document.getElementById("auditor-review-text");
  if (reviewText) reviewText.textContent = ` ${scenario.guidance}`;

  results.classList.add("visible");
}

// ---------------------------------------------------------------------------
// Civic Data Ingestion (browser-side preview)
// ---------------------------------------------------------------------------
function initIngestion() {
  const dropZone  = document.getElementById("ingestion-drop-zone");
  const fileInput = document.getElementById("ingestion-file-input");
  const proceedBtn = document.getElementById("btn-ingest-proceed");
  const cancelBtn  = document.getElementById("btn-ingest-cancel");

  if (dropZone) {
    dropZone.addEventListener("dragover", e => {
      e.preventDefault();
      dropZone.classList.add("drag-over");
    });
    dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
    dropZone.addEventListener("drop", e => {
      e.preventDefault();
      dropZone.classList.remove("drag-over");
      const file = e.dataTransfer.files[0];
      if (file) processIngestFile(file);
    });
  }

  if (fileInput) {
    fileInput.addEventListener("change", e => {
      const file = e.target.files[0];
      if (file) processIngestFile(file);
    });
  }

  if (proceedBtn) {
    proceedBtn.addEventListener("click", () => {
      if (!state.ingestParsed || !state.ingestParsed.geojson) return;
      // Submit as file upload to /triage
      const json = JSON.stringify(state.ingestParsed.geojson);
      const blob = new Blob([json], { type: "application/json" });
      const file = new File([blob], "ingested-data.geojson", { type: "application/json" });
      hideIngestionPreview();
      switchPage("overview");
      requestReport("/triage", file, proceedBtn);
    });
  }

  if (cancelBtn) {
    cancelBtn.addEventListener("click", () => {
      state.ingestParsed = null;
      hideIngestionPreview();
    });
  }
}

function processIngestFile(file) {
  if (file.size > 2 * 1024 * 1024) {
    showIngestionError("File exceeds the 2 MB limit. Please reduce the file size.");
    return;
  }

  const ext = file.name.split(".").pop().toLowerCase();
  const reader = new FileReader();

  reader.onload = e => {
    const content = e.target.result;
    try {
      if (ext === "geojson" || ext === "json") {
        handleIngestionJson(content, file.name);
      } else if (ext === "csv") {
        handleIngestionCsv(content, file.name);
      } else if (ext === "pdf") {
        handleIngestionPdf(content, file.name);
      } else {
        showIngestionError(`Unsupported file type: .${ext}. Please upload CSV, JSON, GeoJSON, or PDF.`);
      }
    } catch (err) {
      showIngestionError(`Could not parse file: ${err.message}`);
    }
  };

  reader.onerror = () => showIngestionError("Could not read file.");
  reader.readAsText(file);
}

function handleIngestionJson(content, filename) {
  let data;
  try {
    data = JSON.parse(content);
  } catch (e) {
    showIngestionError("Invalid JSON. Please check the file format.");
    return;
  }

  let records = [];
  let format = "json-array";
  let notices = [];
  let geojson = null;

  if (Array.isArray(data)) {
    records = data;
    format = "json-array";
    notices.push({ type: "info", msg: "JSON array detected. Each object will be mapped to a complaint record." });
  } else if (data && data.type === "FeatureCollection" && Array.isArray(data.features)) {
    format = "geojson-fc";
    geojson = data;
    const pointFeatures = data.features.filter(f => f.geometry && f.geometry.type === "Point");
    const nonPoint = data.features.length - pointFeatures.length;
    records = pointFeatures.map(f => ({ ...f.properties, lat: f.geometry.coordinates[1], lon: f.geometry.coordinates[0] }));
    if (nonPoint > 0) {
      notices.push({ type: "warning", msg: `${nonPoint} non-Point feature(s) skipped. Only GeoJSON Point features are supported.` });
    }
    notices.push({ type: "info", msg: "GeoJSON FeatureCollection detected. Point features will be ingested as complaint records." });
  } else if (data && data.type === "Feature" && data.geometry && data.geometry.type === "Point") {
    format = "geojson-feature";
    records = [{ ...data.properties, lat: data.geometry.coordinates[1], lon: data.geometry.coordinates[0] }];
    geojson = { type: "FeatureCollection", features: [data] };
    notices.push({ type: "info", msg: "Single GeoJSON Point Feature detected." });
  } else if (data && typeof data === "object" && !Array.isArray(data)) {
    // Possible JSON wrapper
    const keys = Object.keys(data);
    const arrKey = keys.find(k => Array.isArray(data[k]));
    if (arrKey) {
      records = data[arrKey];
      format = "json-wrapper";
      notices.push({ type: "info", msg: `JSON wrapper detected. Using array field "${arrKey}".` });
    } else {
      records = [data];
      format = "json-object";
      notices.push({ type: "info", msg: "Single JSON object detected. Treating as one complaint record." });
    }
  }

  // Validate records
  const { valid, invalid, missingCoords, validationRows } = validateIngestRecords(records);

  // Build GeoJSON for submission if not already
  if (!geojson && valid.length > 0) {
    geojson = {
      type: "FeatureCollection",
      features: valid.map(r => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [r.lon ?? r.longitude ?? 0, r.lat ?? r.latitude ?? 0] },
        properties: r,
      })),
    };
  }

  state.ingestParsed = { records, format, valid, invalid, geojson, notices };
  showIngestionPreview(records.length, valid.length, invalid.length, missingCoords, validationRows, notices, "geojson");
}

function handleIngestionCsv(content, filename) {
  const lines = content.trim().split(/\r?\n/);
  if (lines.length < 2) {
    showIngestionError("CSV file appears to be empty or has no data rows.");
    return;
  }

  const headers = lines[0].split(",").map(h => h.trim().replace(/^"(.*)"$/, "$1"));
  const rows = lines.slice(1).map(line => {
    const vals = parseCsvLine(line);
    const obj = {};
    headers.forEach((h, i) => { obj[h] = (vals[i] || "").trim(); });
    return obj;
  });

  // Show CSV mapping UI
  renderCsvMapping(headers, rows);

  const { valid, invalid, missingCoords, validationRows } = validateIngestRecords(rows, headers);
  const notices = [
    { type: "info", msg: "CSV detected. Review and edit the field mapping below before proceeding." },
    { type: "warning", msg: "CSV files do not include geographic coordinates unless lat/lon columns are present. Records missing coordinates will be skipped." },
  ];

  state.ingestParsed = { records: rows, format: "csv", valid, invalid, geojson: null, notices };
  showIngestionPreview(rows.length, valid.length, invalid.length, missingCoords, validationRows, notices, "csv");
}

function handleIngestionPdf(content, filename) {
  const notices = [
    { type: "warning", msg: "PDF ingestion is limited to embedded-text tables. Scanned PDFs require OCR which is not available in this browser preview." },
  ];

  // Attempt basic text extraction (limited in browser)
  const notices2 = [
    ...notices,
    { type: "info", msg: "No complaint rows could be automatically extracted from this PDF. Please export the data as CSV or JSON for ingestion, or use the CLI: omnigrid ingest <file.pdf>" },
  ];

  // Show PDF consent + OCR prereqs notice
  showIngestionPreview(0, 0, 0, 0, [], notices2, "pdf");
  showEl("pdf-section");

  state.ingestParsed = { records: [], format: "pdf", valid: [], invalid: [], geojson: null, notices: notices2 };
  const proceedBtn = document.getElementById("btn-ingest-proceed");
  if (proceedBtn) proceedBtn.disabled = true;
  const hint = document.getElementById("ingestion-proceed-hint");
  if (hint) hint.textContent = "PDF data could not be extracted. Export as CSV or JSON to proceed.";
}

function parseCsvLine(line) {
  const result = [];
  let inQuote = false;
  let cur = "";
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"' && (i === 0 || line[i-1] !== "\\")) {
      inQuote = !inQuote;
    } else if (ch === "," && !inQuote) {
      result.push(cur); cur = "";
    } else {
      cur += ch;
    }
  }
  result.push(cur);
  return result;
}

const OMNI_GRID_FIELDS = [
  { key: "id",         label: "ID",          required: true },
  { key: "category",   label: "Category",    required: true },
  { key: "status",     label: "Status",      required: true },
  { key: "text",       label: "Description", required: true },
  { key: "created_at", label: "Created At",  required: true },
  { key: "lat",        label: "Latitude",    required: false },
  { key: "lon",        label: "Longitude",   required: false },
];

function renderCsvMapping(headers, sampleRows) {
  showEl("csv-mapping-section");
  const tbody = document.getElementById("csv-mapping-tbody");
  if (!tbody) return;
  tbody.replaceChildren();

  const sample = sampleRows[0] || {};

  headers.forEach(col => {
    const tr = createElem("tr");
    tr.appendChild(createElem("td", [], col));

    const selTd = createElem("td");
    const sel = document.createElement("select");
    sel.setAttribute("aria-label", `Map CSV column "${col}" to Omni-Grid field`);

    const blankOpt = createElem("option", [], "— skip —");
    blankOpt.value = "";
    sel.appendChild(blankOpt);

    OMNI_GRID_FIELDS.forEach(f => {
      const opt = createElem("option", [], `${f.label}${f.required ? " *" : ""}`);
      opt.value = f.key;
      // Auto-suggest based on name similarity
      const colLower = col.toLowerCase().replace(/[^a-z]/g, "");
      const keyLower = f.key.toLowerCase().replace(/[^a-z]/g, "");
      if (colLower === keyLower || colLower.includes(keyLower) || keyLower.includes(colLower)) {
        opt.selected = true;
      }
      sel.appendChild(opt);
    });

    selTd.appendChild(sel);
    tr.appendChild(selTd);

    const reqField = OMNI_GRID_FIELDS.find(f => {
      const cL = col.toLowerCase().replace(/[^a-z]/g, "");
      const kL = f.key.toLowerCase().replace(/[^a-z]/g, "");
      return cL === kL || cL.includes(kL) || kL.includes(cL);
    });
    tr.appendChild(createElem("td", [], reqField?.required ? "Yes *" : ""));
    tr.appendChild(createElem("td", [], String(sample[col] || "").substring(0, 40)));
    tbody.appendChild(tr);
  });
}

function validateIngestRecords(records, headers = null) {
  const valid = [];
  const invalid = [];
  let missingCoords = 0;
  const validationRows = records.slice(0, 20).map((r, i) => {
    const issues = [];
    if (!r.id && !r.ID && !r.complaint_id) issues.push("Missing ID");
    if (!r.category && !r.Category && !r.type) issues.push("Missing category");
    if (!r.status && !r.Status) issues.push("Missing status");

    const lat = parseFloat(r.lat ?? r.latitude ?? r.Lat ?? r.Latitude);
    const lon = parseFloat(r.lon ?? r.longitude ?? r.Lon ?? r.Longitude ?? r.lng);
    const hasCoords = isValidCoord(lat, lon);
    if (!hasCoords) { issues.push("Missing/invalid coordinates"); missingCoords++; }

    if (issues.length === 0) {
      valid.push(r);
      return { row: i + 1, status: "pass", issues: [] };
    } else {
      invalid.push(r);
      return { row: i + 1, status: issues.includes("Missing/invalid coordinates") ? "warn" : "fail", issues };
    }
  });

  // Count remaining
  records.slice(20).forEach(r => {
    const lat = parseFloat(r.lat ?? r.latitude ?? r.Lat ?? r.Latitude);
    const lon = parseFloat(r.lon ?? r.longitude ?? r.Lon ?? r.Longitude ?? r.lng);
    if (!isValidCoord(lat, lon)) { missingCoords++; invalid.push(r); }
    else valid.push(r);
  });

  return { valid, invalid, missingCoords, validationRows };
}

function showIngestionPreview(total, validCount, invalidCount, missingCoords, validationRows, notices, format) {
  const section = document.getElementById("ingestion-preview-section");
  if (section) section.style.display = "";

  setText("preview-total-rows",  total);
  setText("preview-valid-rows",  validCount);
  setText("preview-invalid-rows", invalidCount);
  setText("preview-coord-missing", missingCoords);

  // Notices
  const noticesContainer = document.getElementById("ingestion-format-notices");
  if (noticesContainer) {
    noticesContainer.replaceChildren();
    noticesContainer.style.marginTop = "0.75rem";
    (notices || []).forEach(n => {
      const div = createElem("div", ["ingestion-notice", n.type], n.msg);
      noticesContainer.appendChild(div);
    });
  }

  // Show format-specific sections
  hideEl("csv-mapping-section");
  hideEl("geojson-notice");
  hideEl("pdf-section");

  if (format === "csv")    showEl("csv-mapping-section");
  if (format === "geojson") showEl("geojson-notice");
  if (format === "pdf")    showEl("pdf-section");

  // Validation rows
  const validationWrapper = document.getElementById("validation-rows-wrapper");
  const validationList    = document.getElementById("validation-row-list");
  if (validationRows && validationRows.length > 0 && validationList) {
    if (validationWrapper) validationWrapper.style.display = "";
    validationList.replaceChildren();
    validationRows.forEach(vr => {
      const row = createElem("div", ["validation-row"]);
      row.setAttribute("role", "listitem");
      const icon = createElem("span", ["val-icon", vr.status]);
      icon.textContent = vr.status === "pass" ? "✓" : vr.status === "warn" ? "⚠" : "✗";
      row.appendChild(icon);
      const txt = createElem("span", [],
        `Row ${vr.row}: ${vr.issues.length === 0 ? "Valid" : vr.issues.join(", ")}`);
      row.appendChild(txt);
      validationList.appendChild(row);
    });
  } else if (validationWrapper) {
    validationWrapper.style.display = "none";
  }

  // Media limitation notice
  const mediaNotice = document.getElementById("media-limitation-notice");
  if (mediaNotice) mediaNotice.style.display = format !== "pdf" ? "" : "none";

  // Enable proceed if there are valid records
  const proceedBtn = document.getElementById("btn-ingest-proceed");
  const proceedHint = document.getElementById("ingestion-proceed-hint");
  if (proceedBtn) {
    const canProceed = validCount > 0 && format !== "pdf";
    proceedBtn.disabled = !canProceed;
    if (proceedHint) {
      if (canProceed) {
        proceedHint.textContent = `${validCount} valid records ready. Click 'Process & Replace Report' to run triage.`;
      } else if (format === "pdf") {
        proceedHint.textContent = "PDF data could not be extracted automatically. Export as CSV or JSON.";
      } else {
        proceedHint.textContent = "No valid records found. Check field mapping and file format.";
      }
    }
  }
}

function hideIngestionPreview() {
  const section = document.getElementById("ingestion-preview-section");
  if (section) section.style.display = "none";
  const noticesContainer = document.getElementById("ingestion-format-notices");
  if (noticesContainer) noticesContainer.replaceChildren();
  hideEl("csv-mapping-section");
  hideEl("geojson-notice");
  hideEl("pdf-section");
  hideEl("validation-rows-wrapper");
  hideEl("media-limitation-notice");
}

function showIngestionError(message) {
  const noticesContainer = document.getElementById("ingestion-format-notices");
  if (noticesContainer) {
    noticesContainer.replaceChildren();
    noticesContainer.style.marginTop = "0.75rem";
    noticesContainer.appendChild(createElem("div", ["ingestion-notice", "error"], message));
  }
}

// ---------------------------------------------------------------------------
// Clear Report (reset to empty state)
// ---------------------------------------------------------------------------
function clearReport() {
  state.report = null;
  state.verdictsMap.clear();
  state.complaintsMap.clear();
  state.markersMap.clear();
  state.circlesMap.clear();
  state.selectedComplaintId = null;
  state.selectedHotspotId   = null;

  ["stat-total-complaints","stat-total-hotspots","stat-suspect-closures","stat-verified-closures"].forEach(id => setText(id, "—"));
  ["tab-count-briefing","tab-count-hotspots","tab-count-issues"].forEach(id => setText(id, ""));
  setText("map-count-badge", "—");

  if (state.mapLayers.hotspots)   state.mapLayers.hotspots.clearLayers();
  if (state.mapLayers.complaints) state.mapLayers.complaints.clearLayers();
  if (state.map) state.map.setView([12.9716, 77.5946], 12);

  renderSummaryClaims();
  renderHotspotsList();
  renderComplaintsList();
  renderInspectionEmptyState();
}

// ---------------------------------------------------------------------------
// App Initialization
// ---------------------------------------------------------------------------
function initApp() {
  initTheme();
  initPageNav();
  initPanelTabs();
  initSummaryCards();
  setupFilterControls();
  initAuditorModal();
  initIngestion();
  initSpeech();
  initJsonExport();
  initOrUpdateMap();

  // Wire up demo buttons
  const btnReal = document.getElementById("btn-run-demo");
  if (btnReal) {
    btnReal.addEventListener("click", event => {
      requestReport("/demo", null, event.currentTarget);
    });
  }

  const btnSynthetic = document.getElementById("btn-run-demo-synthetic");
  if (btnSynthetic) {
    btnSynthetic.addEventListener("click", event => {
      requestReport("/demo/synthetic", null, event.currentTarget);
    });
  }

  // Welcome state "Run Synthetic Demo" button delegation
  document.getElementById("claims-list-container")?.addEventListener("click", event => {
    const demoButton = event.target.closest("[data-run-demo]");
    if (demoButton) requestReport("/demo/synthetic", null, demoButton);
  });

  // GeoJSON upload on main upload control
  const geojsonUpload = document.getElementById("geojson-upload");
  if (geojsonUpload) {
    geojsonUpload.addEventListener("change", e => {
      const file = e.target.files[0];
      if (file) requestReport("/triage", file);
    });
  }

  // Full-page tab panel links (↗ link in panel tabs goes to ingestion page)
  document.querySelectorAll(".panel-tab-link[data-page]").forEach(link => {
    link.addEventListener("click", e => {
      e.preventDefault();
      switchPage(link.dataset.page);
    });
  });

  // Footer page navigation links
  document.querySelectorAll(".footer-link[data-page]").forEach(link => {
    link.addEventListener("click", () => switchPage(link.dataset.page));
  });

  // Briefings page listen/download wiring
  const listenFull  = document.getElementById("btn-listen-briefing-full");
  const downloadFull = document.getElementById("btn-download-json-full");
  if (listenFull)   listenFull.addEventListener("click",   () => toggleSpeech(listenFull));
  if (downloadFull) downloadFull.addEventListener("click", () => downloadReportJson());

  setApiStatus(
    "Choose a demo dataset above to run through the live API, or upload a supported file.",
    "idle"
  );
}

// Start application on DOM ready
document.addEventListener("DOMContentLoaded", initApp);
