// Search page: the role query and filters, job-board management, running a
// search, and the best recent matches.

const SITE_TYPE_LABEL = {
  greenhouse: "Greenhouse · API",
  lever: "Lever · API",
  ashby: "Ashby · API",
  smartrecruiters: "SmartRecruiters · API",
  generic: "Career page · scraped",
};
// Rows of these types are company boards read through an API; every other
// search_sites row is a scraped job board or careers page.
const API_SITE_TYPES = new Set(["greenhouse", "lever", "ashby", "smartrecruiters"]);

const positionInput = document.getElementById("position-input");
const seniorityInput = document.getElementById("seniority-input");
const maxAgeInput = document.getElementById("max-age-input");
const maxResultsInput = document.getElementById("max-results-input");
const positionMessage = document.getElementById("position-message");

// ---- filters ------------------------------------------------------------------

async function loadPosition() {
  try {
    const cfg = await getJSON("/api/search/config");
    positionInput.value = cfg.position_query || "";
    // Seniority options come from the backend (agents/search_agent.py's
    // SENIORITY_LABELS) so the control can't drift from what the filter knows.
    (cfg.seniority_levels || []).forEach(({ value, label }) => {
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = label;
      seniorityInput.appendChild(opt);
    });
    seniorityInput.value = cfg.seniority_level || "";
    maxAgeInput.value = cfg.max_age_days ? String(cfg.max_age_days) : "";
    maxResultsInput.value = cfg.max_results_per_site ? String(cfg.max_results_per_site) : "";
  } catch (e) {
    positionMessage.textContent = "Could not reach the backend API.";
    positionMessage.className = "save-message save-error mt-space-sm block";
  }
}

function saveFilters() {
  return postJSON("/api/search/config", {
    position_query: positionInput.value.trim(),
    seniority_level: seniorityInput.value,
    max_age_days: maxAgeInput.value ? parseInt(maxAgeInput.value, 10) : null,
    max_results_per_site: maxResultsInput.value ? parseInt(maxResultsInput.value, 10) : null,
  });
}

// ---- sources ------------------------------------------------------------------

const sitesBody = document.getElementById("sites-body");
const siteMessage = document.getElementById("site-message");

function sourceTile({ label, url, sub, action, dim = false }) {
  return `
    <div class="flex items-center justify-between gap-2 rounded-lg bg-surface-container-low px-space-sm py-2 ${dim ? "opacity-60" : ""}">
      <div class="min-w-0">
        <a href="${escapeHtml(url)}" target="_blank" rel="noopener" title="${escapeHtml(url)}"
           class="block truncate text-label-md text-on-surface hover:text-primary">${escapeHtml(label)}</a>
        <span class="text-label-sm normal-case tracking-normal text-on-surface-variant">${escapeHtml(sub)}</span>
      </div>
      ${action}
    </div>`;
}

const removeBtn = (attrs) => `
  <button type="button" class="sa-icon-btn" ${attrs} aria-label="Remove source" title="Remove">
    <span class="ms text-[18px]">close</span>
  </button>`;

function siteTile(site) {
  return sourceTile({
    label: site.label || site.identifier || site.url,
    url: site.url,
    sub: SITE_TYPE_LABEL[site.site_type] || "Job board · scraped",
    action: removeBtn(`data-remove-site="${site.id}"`),
  });
}

// A known board that isn't searched yet: shown dimmed, one click adds it.
function availableBoardTile(board) {
  return sourceTile({
    label: board.label,
    url: board.url,
    sub: `${board.region} · not searched`,
    dim: true,
    action: `
      <button type="button" class="inline-flex flex-shrink-0 items-center gap-1 rounded-lg px-2 py-1 text-label-md text-primary transition-colors hover:bg-surface-container" data-add-board="${escapeHtml(board.key)}" title="Search ${escapeHtml(board.label)} too">
        <span class="ms text-[18px]">add</span>Add
      </button>`,
  });
}

function feedTile(feed) {
  return sourceTile({
    label: feed.label,
    url: feed.url,
    sub: feed.enabled ? "Remote feed · API" : "Off · skipped in searches",
    dim: !feed.enabled,
    action: `
      <button type="button" class="inline-flex h-8 w-8 items-center justify-center rounded-lg text-on-surface-variant transition-colors hover:bg-surface-container hover:text-on-surface" data-feed="${feed.key}" data-enabled="${feed.enabled}"
              aria-pressed="${feed.enabled}" title="${feed.enabled ? "Switch off" : "Switch on"}">
        <span class="ms text-[22px] ${feed.enabled ? "text-primary" : ""}">${feed.enabled ? "toggle_on" : "toggle_off"}</span>
      </button>`,
  });
}

function envBoardTile(board) {
  return sourceTile({
    label: board.label,
    url: board.url,
    sub: `${board.kind} · API`,
    action: removeBtn(`data-env-key="${board.env_key}" data-value="${escapeHtml(board.value)}"`),
  });
}

function linkedinChips(locations, googleJobs) {
  const chips = locations.map((loc) => `
    <span class="flex items-center gap-1 rounded-full bg-surface-container-low py-1 pl-3 pr-1 text-label-md text-on-surface">
      <span class="ms text-[16px] text-primary">group</span>LinkedIn · ${escapeHtml(loc)}
      <button type="button" class="sa-icon-btn h-6 w-6" data-linkedin="${escapeHtml(loc)}" aria-label="Stop searching ${escapeHtml(loc)}" title="Remove">
        <span class="ms text-[16px]">close</span>
      </button>
    </span>`).join("");
  const google = googleJobs
    ? `<span class="flex items-center gap-1 rounded-full bg-surface-container-low px-3 py-1 text-label-md text-on-surface"><span class="ms text-[16px] text-primary">travel_explore</span>Google Jobs · SerpAPI</span>`
    : `<a href="settings.html" class="flex items-center gap-1 rounded-full bg-surface-container-low px-3 py-1 text-label-md text-on-surface-variant opacity-60 hover:opacity-100"><span class="ms text-[16px]">travel_explore</span>Google Jobs · off (add a SerpAPI key)</a>`;
  return `${chips}
    <form id="linkedin-add" class="flex items-center gap-1">
      <input type="text" id="linkedin-input" class="sa-input w-44 py-1.5" placeholder="Add a location…" aria-label="LinkedIn location">
      <button type="submit" class="inline-flex h-8 w-8 items-center justify-center rounded-lg text-on-surface-variant transition-colors hover:bg-surface-container hover:text-on-surface" title="Add location"><span class="ms text-[18px]">add</span></button>
    </form>
    ${google}`;
}

function showSiteError(err, fallback) {
  siteMessage.textContent = err.message || fallback;
  siteMessage.className = "save-message save-error mt-2 block";
}

// Wires a click handler to every button matching `selector`; the handler's
// request runs with the button disabled, then the whole list reloads.
function onEach(selector, handler, fallback) {
  document.querySelectorAll(selector).forEach((btn) => {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await handler(btn);
        loadSites();
      } catch (err) {
        showSiteError(err, fallback);
        btn.disabled = false;
      }
    });
  });
}

async function loadSites() {
  try {
    const [sites, builtin] = await Promise.all([
      getJSON("/api/search/sites"),
      getJSON("/api/search/builtin-sources"),
    ]);
    const apiSites = sites.filter((s) => API_SITE_TYPES.has(s.site_type));
    const scraped = sites.filter((s) => !API_SITE_TYPES.has(s.site_type));
    const feedsOn = builtin.feeds.filter((f) => f.enabled).length;
    const boardCount = builtin.boards.length + apiSites.length;

    const parts = [
      `${feedsOn} remote feed${feedsOn === 1 ? "" : "s"}`,
      `${boardCount} company board${boardCount === 1 ? "" : "s"}`,
      `${scraped.length} job board${scraped.length === 1 ? "" : "s"}`,
    ];
    if (builtin.linkedin.length) parts.push(`LinkedIn (${builtin.linkedin.join(", ")})`);
    if (builtin.google_jobs) parts.push("Google Jobs");
    document.getElementById("sources-summary").textContent = parts.join(" · ");

    document.getElementById("feeds-body").innerHTML = builtin.feeds.map(feedTile).join("");
    document.getElementById("linkedin-body").innerHTML = linkedinChips(builtin.linkedin, builtin.google_jobs);
    document.getElementById("boards-body").innerHTML = boardCount
      ? builtin.boards.map(envBoardTile).join("") + apiSites.map(siteTile).join("")
      : `<p class="sa-empty col-span-3">No company boards yet. Paste a board URL below.</p>`;
    const available = builtin.available_boards || [];
    sitesBody.innerHTML = scraped.length || available.length
      ? scraped.map(siteTile).join("") + available.map(availableBoardTile).join("")
      : `<p class="sa-empty col-span-3">No job boards or career pages added.</p>`;

    onEach("[data-add-board]", (btn) => postJSON("/api/search/builtin-sources/add-board",
      { key: btn.dataset.addBoard }), "Could not add that board.");
    onEach("[data-remove-site]", (btn) => deleteJSON(`/api/search/sites/${btn.dataset.removeSite}`),
      "Could not remove source.");
    onEach("[data-feed]", (btn) => postJSON(`/api/search/builtin-sources/feeds/${btn.dataset.feed}`,
      { enabled: btn.dataset.enabled !== "true" }), "Could not change that feed.");
    onEach("[data-env-key]", (btn) => postJSON("/api/search/builtin-sources/remove-board",
      { env_key: btn.dataset.envKey, value: btn.dataset.value }), "Could not remove source.");
    onEach("[data-linkedin]", (btn) => postJSON("/api/search/builtin-sources/remove-linkedin",
      { env_key: "LINKEDIN_LOCATIONS", value: btn.dataset.linkedin }), "Could not remove location.");

    document.getElementById("linkedin-add").addEventListener("submit", async (e) => {
      e.preventDefault();
      const value = document.getElementById("linkedin-input").value.trim();
      if (!value) return;
      try {
        await postJSON("/api/search/builtin-sources/add-linkedin", { env_key: "LINKEDIN_LOCATIONS", value });
        loadSites();
      } catch (err) {
        showSiteError(err, "Could not add location.");
      }
    });
  } catch (e) {
    document.getElementById("sources-summary").textContent = "could not reach the backend API";
  }
}

document.getElementById("manage-sources").addEventListener("click", () => {
  const panel = document.getElementById("sources-panel");
  panel.hidden = !panel.hidden;
});

document.getElementById("add-site-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("site-url-input");
  const url = input.value.trim();
  if (!url) return;
  siteMessage.textContent = "Adding…";
  siteMessage.className = "save-message mt-2 block";
  try {
    await postJSON("/api/search/sites", { url });
    input.value = "";
    siteMessage.textContent = "Added.";
    siteMessage.className = "save-message save-success mt-2 block";
    loadSites();
  } catch (err) {
    siteMessage.textContent = err.message || "Could not add source.";
    siteMessage.className = "save-message save-error mt-2 block";
  }
});

// ---- run ----------------------------------------------------------------------
// The run is one blocking request, so there are no real per-stage progress
// events. While it's in flight the bar is indeterminate; the step cards only
// get numbers once the result is back.

const RUN_STEPS = [
  { key: "find", label: "Finding opportunities" },
  { key: "match", label: "Matching against profile" },
  { key: "prepare", label: "Preparing your results" },
];

function paintRun(state, detail = {}) {
  const panel = document.getElementById("run-panel");
  panel.hidden = false;
  const bar = document.getElementById("run-bar");
  const chip = document.getElementById("run-chip");
  const title = document.getElementById("run-title");
  const stopBtn = document.getElementById("stop-btn");
  stopBtn.classList.toggle("hidden", state !== "running");
  stopBtn.disabled = false;
  document.getElementById("stop-label").textContent = "Stop search";
  if (state === "running") {
    title.innerHTML = "Sa'ei is searching across your active sources&hellip;";
    chip.innerHTML = `<span class="h-1.5 w-1.5 animate-pulse rounded-full bg-tertiary"></span>Running`;
    bar.style.width = "60%";
    bar.classList.add("animate-pulse");
  } else {
    const look = {
      failed: { title: "The search finished with problems", dot: "bg-error", chip: "Needs attention" },
      partial: { title: "Search complete · a few jobs left for later", dot: "bg-tertiary", chip: "Partly done" },
      done: { title: "Search complete", dot: "bg-secondary", chip: "Done" },
      stopped: { title: "Search stopped", dot: "bg-outline", chip: "Stopped" },
    }[state];
    title.textContent = look.title;
    chip.innerHTML = `<span class="h-1.5 w-1.5 rounded-full ${look.dot}"></span>${look.chip}`;
    bar.style.width = "100%";
    bar.classList.remove("animate-pulse");
  }
  document.getElementById("run-steps").innerHTML = RUN_STEPS.map((s) => {
    const text = detail[s.key];
    const done = state !== "running";
    return `
      <div class="flex items-start gap-2 rounded-lg ${done ? "bg-surface-container-lowest/70" : "bg-surface-container-lowest"} p-space-sm">
        <span class="ms mt-0.5 text-[18px] ${done ? (s.key === "prepare" && state !== "done" ? (state === "failed" ? "text-error" : state === "stopped" ? "text-on-surface-variant" : "text-tertiary") : "text-primary") : "animate-pulse text-on-surface-variant"}">${done ? "check_circle" : "radio_button_unchecked"}</span>
        <div>
          <p class="text-label-md font-semibold text-on-surface">${s.label}</p>
          <p class="text-body-sm text-on-surface-variant">${escapeHtml(text || (done ? "Done" : "In progress"))}</p>
        </div>
      </div>`;
  }).join("");
}

// The run sets aside jobs the LLM cut short (usage limit spent, unreadable
// answer) and processes them first next time -- result.queued. result.errors
// holds only the jobs that failed for some other reason. Both are explained in
// plain words; the raw messages stay behind "Details" for debugging.
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

function runNoteLines(result) {
  const queued = result.queued || [];
  const errors = result.errors || [];
  const sourceErrors = result.source_errors || [];
  const limit = queued.filter((q) => q.reason === "limit").length;
  const stoppedJobs = queued.filter((q) => q.reason === "stopped").length;
  const unreadable = queued.length - limit - stoppedJobs;
  const daily = /per day|\bTPD\b|daily/i.test(result.limit_error || "");
  const lines = [];
  if (result.stopped) {
    lines.push(stoppedJobs
      ? `You stopped the search. ${plural(stoppedJobs, "job was", "jobs were")} found but not processed yet; `
        + `${stoppedJobs === 1 ? "it's" : "they're"} saved, and the next search processes ${stoppedJobs === 1 ? "it" : "them"} first.`
      : "You stopped the search before any new jobs were ranked. The next search will find them again.");
  }
  if (result.resumed) {
    lines.push(`Finished ${plural(result.resumed, "job", "jobs")} left over from an earlier search.`);
  }
  if (limit) {
    lines.push(`${plural(limit, "job is", "jobs are")} saved for later because the AI model's usage limit ran out`
      + (daily ? " for today" : "") + ". "
      + `${limit === 1 ? "It'll" : "They'll"} be processed first on your next search`
      + (daily ? " once the limit resets, or right away if you switch to another model in Settings." : "."));
  }
  if (unreadable) {
    lines.push(`${plural(unreadable, "job", "jobs")} couldn't be read by the model this time. `
      + `${unreadable === 1 ? "It's" : "They're"} saved and will be tried again first on your next search.`);
  }
  if (errors.length) {
    lines.push(`${plural(errors.length, "job", "jobs")} failed for another reason. See the details below.`);
  }
  if (sourceErrors.length) {
    const names = [...new Set(sourceErrors.map((x) => x.source))].join(", ");
    lines.push(`Couldn't reach ${names} this time, so ${sourceErrors.length === 1 ? "it was" : "they were"} skipped.`);
  }
  return lines;
}

function rawErrorDetails(result) {
  const groups = new Map();
  (result.errors || []).forEach((e) => {
    const msg = e.error || "Unknown error";
    const key = msg.replace(/\d+(\.\d+)?/g, "#");  // per-request numbers vary
    if (!groups.has(key)) groups.set(key, { count: 0, sample: msg });
    groups.get(key).count += 1;
  });
  const items = [...groups.values()].map(({ sample, count }) => (count > 1 ? `${sample} (×${count})` : sample));
  if (result.limit_error) items.unshift(`Usage limit: ${result.limit_error}`);
  (result.source_errors || []).forEach((x) => items.push(`${x.source}${x.identifier ? ` (${x.identifier})` : ""}: ${x.error}`));
  return items;
}

function showRunNotes(result) {
  const lines = runNoteLines(result);
  const details = rawErrorDetails(result);
  searchMessage.className = "mt-space-md flex items-start gap-2 rounded-lg bg-tertiary-fixed/40 px-space-md py-space-sm text-body-sm text-on-surface";
  searchMessage.innerHTML = `
    <span class="ms mt-0.5 text-[18px] text-tertiary">info</span>
    <div class="min-w-0">
      ${lines.map((l) => `<p>${escapeHtml(l)}</p>`).join("")}
      ${details.length ? `
      <details class="mt-1 text-on-surface-variant">
        <summary class="cursor-pointer text-label-md">Details</summary>
        <ul class="mt-1 list-disc space-y-1 break-words pl-5 text-body-sm">${details.map((d) => `<li>${escapeHtml(d)}</li>`).join("")}</ul>
      </details>` : ""}
    </div>`;
}

const searchBtn = document.getElementById("search-btn");
const searchMessage = document.getElementById("search-message");

// Stop: the run ends after the step in progress (a job being scored finishes
// first), so the button says so rather than pretending it's instant.
document.getElementById("stop-btn").addEventListener("click", async () => {
  const btn = document.getElementById("stop-btn");
  btn.disabled = true;
  document.getElementById("stop-label").textContent = "Stopping…";
  document.getElementById("run-title").textContent = "Stopping after the current step…";
  try {
    await postJSON("/api/search/stop", {});
  } catch (err) {
    btn.disabled = false;
    document.getElementById("stop-label").textContent = "Stop search";
    searchMessage.className = "mt-space-md flex items-start gap-2 text-body-sm text-error";
    searchMessage.innerHTML = `<span class="ms text-[16px]">error</span><span>${escapeHtml(err.message || "Could not stop the search.")}</span>`;
  }
});

// Before any search: say so when an earlier run left jobs waiting.
async function loadPending() {
  try {
    const p = await getJSON("/api/search/pending");
    if (!p.count || searchBtn.disabled) return;
    searchMessage.className = "mt-space-md flex items-start gap-2 text-body-sm text-on-surface-variant";
    searchMessage.innerHTML = `<span class="ms text-[16px]">schedule</span><span>${escapeHtml(
      `${plural(p.count, "job is", "jobs are")} waiting from an earlier search`
      + (p.limit ? " that hit the AI model's usage limit" : "")
      + ". The next search processes them first.")}</span>`;
  } catch (e) { /* the status pill already reports an unreachable backend */ }
}

document.getElementById("position-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  searchBtn.disabled = true;
  positionMessage.textContent = "";
  try {
    // Search uses what's saved, so save the filters as typed first.
    await saveFilters();
  } catch (err) {
    positionMessage.textContent = err.message || "Could not save the filters.";
    positionMessage.className = "save-message save-error mt-space-sm block";
    searchBtn.disabled = false;
    return;
  }
  try {
    await postJSON("/api/search/run", {});
    await followRun();
  } catch (err) {
    showRunFailure(err.message || "Search failed.");
    searchBtn.disabled = false;
  }
});

function showRunning() {
  searchBtn.disabled = true;
  paintRun("running");
  searchMessage.innerHTML = `<span class="ms text-[16px]">info</span><span>Takes a few minutes — one model call per new job found. You can leave this page; the search keeps going.</span>`;
  searchMessage.className = "mt-space-md flex items-start gap-2 text-body-sm text-on-surface-variant";
}

function showRunFailure(message) {
  paintRun("failed", { prepare: "The search request failed" });
  searchMessage.innerHTML = `<span class="ms text-[16px]">error</span><span>${escapeHtml(message)}</span>`;
  searchMessage.className = "mt-space-md flex items-start gap-2 text-body-sm text-error";
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// The run happens server-side (a single request would be cut off by any proxy
// long before a search finishes); this polls until it's over, then paints the
// outcome. Brief network blips are retried rather than reported as a failure.
async function followRun() {
  showRunning();
  let misses = 0;
  for (;;) {
    await sleep(3000);
    let status;
    try {
      status = await getJSON("/api/search/status");
      misses = 0;
    } catch (err) {
      if (++misses >= 5) throw new Error("Lost contact with the backend while the search was running.");
      continue;
    }
    if (status.running) continue;
    searchBtn.disabled = false;
    if (status.error) showRunFailure(status.error);
    else if (status.result) showResult(status.result);
    else paintRun("done", {});   // a scheduled run finished; its report is emailed
    loadSnapshots();
    return;
  }
}

function showResult(result) {
  const sourceErrors = result.source_errors || [];
  const errors = result.errors || [];
  const queued = result.queued || [];
  const state = result.stopped ? "stopped"
    : errors.length ? "failed" : queued.length || sourceErrors.length ? "partial" : "done";
  const ready = result.processed.length;
  const prepare = [`${ready} ready`];
  if (queued.length) prepare.push(`${queued.length} saved for the next search`);
  if (result.stopped && !queued.length) prepare.push("stopped before processing");
  if (errors.length) prepare.push(`${errors.length} failed`);
  paintRun(state, {
    find: `${result.found} role${result.found === 1 ? "" : "s"} found${sourceErrors.length ? ` · ${sourceErrors.length} source(s) unreachable` : ""}`,
    match: `${result.new_after_dedup ?? ready} new after deduplication`,
    prepare: prepare.join(" · "),
  });
  if (state !== "done" || result.resumed) {
    showRunNotes(result);
  } else {
    searchMessage.innerHTML = `<span class="ms text-[16px]">check</span><span>Finished. New roles are ranked below and on the Applications page.</span>`;
  }
}

// Coming back to this page while a search is still going: show it and follow it.
async function resumeRunningSearch() {
  try {
    const status = await getJSON("/api/search/status");
    if (status.running) await followRun();
  } catch (err) {
    if (searchBtn.disabled) {
      showRunFailure(err.message || "Search failed.");
      searchBtn.disabled = false;
    }
  }
}

// ---- snapshots --------------------------------------------------------------------

const SOURCE_ICON = {
  greenhouse: "eco", generic: "language", weworkremotely: "public", lever: "work", ashby: "work",
  smartrecruiters: "work", linkedin: "group", himalayas: "public", remotive: "public", jobicy: "public",
  workingnomads: "public", remoteok: "public",
};

function snapshotCard(a) {
  const p = pct(a.match_score);
  const tone = p === null ? "text-on-surface-variant" : p >= 75 ? "text-primary" : "text-tertiary";
  return `
    <a href="applications.html?id=${a.id}" class="flex items-center gap-space-md rounded-xl bg-surface-container-lowest p-space-md shadow-soft transition-shadow hover:shadow-md">
      <span class="flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-lg bg-surface-container text-primary"><span class="ms">${SOURCE_ICON[a.source] || "work"}</span></span>
      <div class="min-w-0 flex-1">
        <p class="truncate text-headline-sm text-on-surface">${escapeHtml(a.title || "Untitled role")}${a.company ? ` <span class="text-label-md text-on-surface-variant">· ${escapeHtml(a.company)}</span>` : ""}</p>
        <p class="flex flex-wrap items-center gap-x-2 text-body-sm text-on-surface-variant">
          <span class="flex items-center gap-0.5"><span class="ms text-[16px]">travel_explore</span>${escapeHtml(sourceName(a.source) || "—")}</span>
          <span>·</span><span>${timeAgo(a.date_created)}</span>
          ${a.ats_score != null ? `<span>·</span><span class="font-mono text-label-sm normal-case tracking-normal">ATS ${pct(hasTailored(a) ? a.tailored_ats_score : a.ats_score)}%</span>` : ""}
        </p>
      </div>
      <div class="text-right">
        <p class="text-headline-md ${tone}">${p === null ? "—" : p + "%"}</p>
        <p class="text-label-md font-semibold text-on-surface-variant">Match</p>
      </div>
      <span class="flex h-8 w-8 items-center justify-center rounded-lg bg-surface-container text-on-surface-variant"><span class="ms text-[18px]">chevron_right</span></span>
    </a>`;
}

async function loadSnapshots() {
  const el = document.getElementById("snapshots");
  try {
    const apps = await getJSON("/api/applications?limit=50");
    const best = sortApplicationsBy(apps, "match_score").slice(0, 5);
    const chip = document.getElementById("snap-chip"); if (chip) chip.remove();
    el.innerHTML = best.length ? best.map(snapshotCard).join("")
      : `<p class="sa-empty">Nothing found yet — run a search.</p>`;
  } catch (e) {
    el.innerHTML = `<p class="sa-empty">Could not reach the backend API.</p>`;
  }
}

loadPosition();
loadSites();
loadSnapshots();
loadPending();
resumeRunningSearch();
