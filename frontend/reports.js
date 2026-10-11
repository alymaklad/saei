// Reports page: headline numbers, a plain-language summary built from real
// data, skill gaps, where roles come from, and every dispatch -- emailed reports
// reports and application emails -- in one stream.

let apps = [];
let gaps = [];
let stats = null;
let dispatches = [];
let dispatchFilter = "all";
const PREVIEW = 5;
let expanded = false;

// ---- stats + momentum -------------------------------------------------------

function listJoin(items) {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

function paintStats() {
  const sources = new Set(apps.map((a) => a.source).filter(Boolean));
  if (stats) {
    document.getElementById("stat-jobs").textContent = stats.total_jobs;
    document.getElementById("stat-queue").textContent = stats.pending_review;
  }
  document.getElementById("stat-jobs-sub").textContent = sources.size
    ? `Found across ${sources.size} source${sources.size === 1 ? "" : "s"}` : "";
  document.getElementById("stat-high").textContent = apps.filter((a) => (a.match_score || 0) >= 0.8).length;
  document.getElementById("stat-gaps").textContent = gaps.length;
  document.getElementById("stat-gaps-sub").textContent = gaps.length ? listJoin(gaps.slice(0, 2).map((g) => g.skill)) : "None recurring yet";
}

function paintMomentum() {
  const el = document.getElementById("momentum");
  if (!apps.length) {
    el.textContent = "No roles yet. Once a search has run, this summarises where your strongest matches are and which skills would open the most doors.";
    return;
  }
  // Built from the data on this page -- every name and number below is real.
  const best = sortApplicationsBy(apps, "match_score").filter((a) => a.match_score != null).slice(0, 3);
  const titles = [...new Set(best.map((a) => a.title).filter(Boolean))];
  const strong = apps.filter((a) => (a.match_score || 0) >= 0.75).length;
  let html = titles.length
    ? `Your strongest opportunities were roles like ${listJoin(titles.map((t) => `<strong class="text-on-surface">${escapeHtml(t)}</strong>`))}`
      + ` — ${strong} of ${apps.length} matched at 75% or better.`
    : `${apps.length} roles found so far.`;
  if (gaps.length) {
    const g = gaps[0];
    const share = Math.round((g.count / apps.length) * 100);
    html += ` Demonstrating <strong class="text-on-surface">${escapeHtml(g.skill)}</strong> would credit a requirement in`
      + ` <span class="text-primary">${g.count} saved role${g.count === 1 ? "" : "s"} (${share}%)</span>`
      + `${gaps[1] ? `, and ${escapeHtml(gaps[1].skill)} another ${gaps[1].count}` : ""}.`;
  }
  el.innerHTML = html;
}

// ---- news digest ----------------------------------------------------------------

async function loadNews() {
  const box = document.getElementById("news-content");
  const toggle = document.getElementById("digest-toggle");
  try {
    const digests = await getJSON("/api/news?limit=1");
    if (!digests.length) {
      toggle.hidden = true;
      document.getElementById("digest-date").textContent = "No news digest yet — the weekly run hasn't happened.";
      return;
    }
    const d = digests[0];
    box.textContent = d.content;
    document.getElementById("digest-date").textContent =
      `${d.field ? d.field + " · " : ""}${d.date_created ? fmtDate(d.date_created) : ""}`;
    toggle.addEventListener("click", () => box.classList.toggle("hidden"));
  } catch (e) {
    toggle.hidden = true;
  }
}

// ---- skill gaps ---------------------------------------------------------------------

function paintGaps() {
  const list = document.getElementById("skill-gaps-list");
  document.getElementById("gaps-chip").textContent = gaps.length ? `${gaps.length} key signals detected` : "";
  if (!gaps.length) {
    list.innerHTML = `<li class="sa-empty">No recurring gaps yet.</li>`;
    return;
  }
  const base = apps.length || gaps[0].count;
  const tones = ["bg-primary", "bg-primary-container", "bg-secondary", "bg-secondary-fixed-dim"];
  list.innerHTML = gaps.slice(0, 6).map((g, i) => {
    const share = Math.min(100, Math.round((g.count / base) * 100));
    return `
      <li>
        <div class="mb-1.5 flex items-center justify-between gap-space-md">
          <span class="flex items-center gap-2 text-label-md text-on-surface">${escapeHtml(g.skill)}
            ${i === 0 ? `<span class="rounded bg-tertiary-fixed px-1.5 py-0.5 text-label-sm normal-case tracking-normal text-on-tertiary-fixed-variant">Priority gap</span>` : ""}</span>
          <span class="font-mono text-body-sm text-on-surface-variant">${g.count} role${g.count === 1 ? "" : "s"} (${share}%)</span>
        </div>
        <div class="sa-progress"><div class="${tones[Math.min(i, tones.length - 1)]}" style="width:${share}%"></div></div>
      </li>`;
  }).join("");
}

// ---- sources ring ---------------------------------------------------------------------

function paintSources() {
  const counts = {};
  apps.forEach((a) => { const s = a.source || "other"; counts[s] = (counts[s] || 0) + 1; });
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const total = apps.length;
  const text = document.getElementById("source-text");
  const ring = document.getElementById("source-ring");
  if (!total) { text.textContent = "No roles yet."; ring.innerHTML = ""; return; }
  text.textContent = entries.map(([s, n]) => `${s} (${Math.round((n / total) * 100)}%)`).join(" · ");
  const colors = ["#9c3e1e", "#6a5d43", "#d7c4a5", "#bc5633", "#8a726b"];
  const r = 15.9, c = 2 * Math.PI * r;
  let offset = 0;
  ring.innerHTML = `<circle cx="21" cy="21" r="${r}" fill="none" stroke="#ece8e1" stroke-width="4"/>` + entries.map(([, n], i) => {
    const len = (n / total) * c;
    const seg = `<circle cx="21" cy="21" r="${r}" fill="none" stroke="${colors[i % colors.length]}" stroke-width="4"
      stroke-dasharray="${Math.max(0, len - 0.6).toFixed(2)} ${(c - len + 0.6).toFixed(2)}" stroke-dashoffset="${(-offset).toFixed(2)}"/>`;
    offset += len;
    return seg;
  }).join("");
}

// ---- dispatches -----------------------------------------------------------------------

function dispatchItem(d) {
  const failed = d.status === "failed";
  const state = failed ? "Failed" : d.dry_run ? "Logged (dry run)" : "Delivered";
  return `
    <article class="px-space-lg py-space-md">
      <div class="mb-1 flex items-center justify-between gap-space-sm">
        <span class="flex items-center gap-1.5 text-label-sm uppercase tracking-wider ${d.kind === "email" ? "text-secondary" : "text-primary"}">
          <span class="h-1.5 w-1.5 rounded-full ${failed ? "bg-error" : d.kind === "email" ? "bg-secondary" : "bg-primary"}"></span>${escapeHtml(d.label)}
        </span>
        <span class="font-mono text-body-sm ${failed ? "text-error" : "text-on-surface-variant"}">${state} ${d.at ? fmtDate(d.at) : ""}</span>
      </div>
      <h3 class="text-headline-sm text-on-surface">${escapeHtml(d.title)}</h3>
      ${d.sub ? `<p class="text-body-sm text-on-surface-variant">${escapeHtml(d.sub)}</p>` : ""}
      ${d.error ? `<p class="mt-1 text-body-sm text-error">${escapeHtml(d.error)}</p>` : ""}
      ${d.content ? `
        <details class="group mt-space-sm">
          <summary class="flex cursor-pointer list-none items-center gap-1 text-label-md text-on-surface-variant hover:text-primary">
            <span class="ms text-[16px]">visibility</span>Review transmission</summary>
          <pre class="mt-space-sm max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-surface-container-low p-space-md font-sans text-body-sm text-on-surface-variant">${escapeHtml(d.content)}</pre>
        </details>` : ""}
    </article>`;
}

function renderDispatches() {
  const el = document.getElementById("reports-list");
  const more = document.getElementById("reports-more");
  const list = dispatches.filter((d) => dispatchFilter === "all" || d.kind === dispatchFilter);
  if (!list.length) {
    el.innerHTML = `<p class="sa-empty">${dispatchFilter === "email" ? "No application emails sent yet." : dispatchFilter === "telegram" ? "No reports yet — the scheduler emails them at 20:00 and on Mondays." : "Nothing dispatched yet."}</p>`;
    more.hidden = true;
    return;
  }
  const shown = expanded ? list : list.slice(0, PREVIEW);
  el.innerHTML = shown.map(dispatchItem).join("");
  more.hidden = shown.length >= list.length;
  more.textContent = `Show older (${list.length - shown.length} more) →`;
}

document.getElementById("reports-more").addEventListener("click", () => { expanded = true; renderDispatches(); });
bindTabs(document.getElementById("dispatch-tabs"), (f) => { dispatchFilter = f; expanded = false; renderDispatches(); });

// ---- load ------------------------------------------------------------------------------

async function load() {
  const [s, a, g, r, e] = await Promise.allSettled([
    getJSON("/api/stats"),
    getJSON("/api/applications?limit=1000"),
    getJSON("/api/skill-gaps?limit=12"),
    getJSON("/api/reports?limit=50"),
    getJSON("/api/emails?limit=100"),
  ]);
  if (s.status === "fulfilled") stats = s.value;
  if (a.status === "fulfilled") apps = a.value;
  if (g.status === "fulfilled") gaps = g.value;

  const reports = r.status === "fulfilled" ? r.value : [];
  const emails = e.status === "fulfilled" ? e.value : [];
  dispatches = [
    ...reports.map((x) => ({
      kind: "telegram", at: x.sent_at, status: x.status, dry_run: x.dry_run,
      label: x.report_type === "daily" ? "Application summary" : "News digest",
      title: x.report_type === "daily" ? "Daily application summary" : "Weekly news digest",
      sub: "Report email", content: x.content, error: x.error_message,
    })),
    ...emails.map((x) => ({
      kind: "email", at: x.sent_at, status: x.status, dry_run: x.dry_run,
      label: "Application email",
      title: x.subject,
      sub: `To ${x.to_email}${x.job_title ? ` · ${x.job_title}${x.company ? " at " + x.company : ""}` : ""}`,
      content: "", error: x.error_message,
    })),
  ].sort((x, y) => new Date(y.at || 0) - new Date(x.at || 0));

  if (a.status === "rejected") {
    document.getElementById("momentum").textContent = "Could not reach the backend API.";
  } else {
    paintMomentum();
  }
  paintStats();
  paintGaps();
  paintSources();
  if (r.status === "rejected" && e.status === "rejected") {
    document.getElementById("reports-list").innerHTML = `<p class="sa-empty">Could not reach the backend API.</p>`;
  } else {
    renderDispatches();
  }
}

load();
loadNews();
