// Shared across every page -- include this <script> before any page-specific
// script. The API serves this dashboard itself (http://localhost:8001/, or the
// deployed address), so by default it's the page's own origin. Opened from the
// local port-5500 static server or as a file, it points at the local API.
// Set window.JOB_AGENT_API_BASE to host the dashboard somewhere else.
const API_BASE = window.JOB_AGENT_API_BASE
  ?? ((location.protocol === "file:" || location.port === "5500") ? "http://localhost:8001" : "");

async function getJSON(path) {
  const res = await fetch(`${API_BASE}${path}`);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

async function postJSON(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `${path} -> ${res.status}`);
  return data;
}

async function deleteJSON(path) {
  const res = await fetch(`${API_BASE}${path}`, { method: "DELETE" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `${path} -> ${res.status}`);
  return data;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

// The status pill lives in the shared nav header on every page.
// The pill shows the last known status straight away (kept for the browser
// session), then refreshes it -- instead of flashing "checking status..." on
// every page.
const NAV_STATUS_KEY = "saei.navStatus";

function paintNavStatus(pill, text, cls) {
  pill.textContent = text;
  pill.className = cls;
}

function paintCachedNavStatus() {
  const pill = document.getElementById("status-pill");
  if (!pill) return;
  try {
    const cached = JSON.parse(sessionStorage.getItem(NAV_STATUS_KEY) || "null");
    if (cached) paintNavStatus(pill, cached.text, cached.cls);
  } catch (e) { /* private mode: keep the placeholder */ }
}

async function loadNavStatus() {
  const pill = document.getElementById("status-pill");
  if (!pill) return;
  let text, cls;
  try {
    const status = await getJSON("/api/status");
    [text, cls] = status.dry_run
      ? ["Dry run · nothing is sent", "pill pill-warn"]
      : ["Autonomous mode · Active", "pill pill-ok"];
  } catch (e) {
    [text, cls] = ["Backend unreachable", "pill pill-muted"];
  }
  paintNavStatus(pill, text, cls);
  try { sessionStorage.setItem(NAV_STATUS_KEY, JSON.stringify({ text, cls })); } catch (e) { /* private mode */ }
}

paintCachedNavStatus();  // config.js loads right after shell.js has built the header
document.addEventListener("DOMContentLoaded", loadNavStatus);
