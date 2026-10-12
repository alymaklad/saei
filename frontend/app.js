// Home page. Shared helpers live in config.js (API access, escapeHtml, the
// status pill) and ui.js (chips, avatars, dates, sorting).

function paintGreeting(user) {
  const h = new Date().getHours();
  const part = h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
  const first = user && user.name ? user.name.trim().split(/\s+/)[0] : "";
  document.getElementById("greeting").textContent = first ? `${part}, ${first}.` : `${part}.`;
}
paintGreeting(window.saeiUser);
document.addEventListener("saei:user", (e) => paintGreeting(e.detail));

let stats = null;
let apps = [];
let gaps = [];

function average(values) {
  const v = values.filter((x) => x !== null && x !== undefined);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
}

// ---- trajectory ---------------------------------------------------------------

function paintTrajectory() {
  if (!stats) return;
  const tailored = apps.filter(hasTailored).length;
  const submitted = stats.auto_submitted + stats.emails_sent;
  const strong = apps.filter((a) => (a.match_score || 0) >= 0.75).length;

  const steps = [
    { label: "Search", sub: `${stats.total_jobs} opportunities`, done: stats.total_jobs > 0 },
    { label: "Match", sub: `${strong} strong match${strong === 1 ? "" : "es"}`, done: stats.total_applications > 0 },
    { label: "Tailor", sub: `${tailored} customized CV${tailored === 1 ? "" : "s"}`, done: tailored > 0 },
    { label: "Apply", sub: stats.pending_review ? `${stats.pending_review} ready to submit` : `${submitted} submitted`, done: submitted > 0 },
    { label: "Grow", sub: `${gaps.length} skill insight${gaps.length === 1 ? "" : "s"}`, done: false },
  ];
  // Where the caravan is waiting on you: drafts to review first, otherwise
  // the first stage that hasn't produced anything yet.
  let here = stats.pending_review ? 3 : steps.findIndex((s) => !s.done);
  if (here === -1) here = steps.length - 1;

  document.getElementById("trajectory-stage").textContent =
    `Stage ${here + 1} of ${steps.length} · ${steps[here].label}`;
  document.getElementById("trajectory").innerHTML = steps.map((s, i) => {
    let node;
    if (i === here) {
      node = `<div class="relative flex h-10 w-10 items-center justify-center rounded-lg bg-primary-container text-on-primary shadow-md">
                <span class="ms ms-fill text-[20px]">directions_walk</span>
                <span class="absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full bg-tertiary ring-2 ring-surface-container-lowest"></span>
              </div>`;
    } else if (i < here) {
      node = `<div class="flex h-9 w-9 items-center justify-center rounded-lg bg-surface-container text-secondary"><span class="ms text-[18px]">check</span></div>`;
    } else {
      node = `<div class="flex h-9 w-9 items-center justify-center rounded-lg bg-surface-container-low"><span class="h-1.5 w-1.5 rounded-full bg-primary-fixed-dim"></span></div>`;
    }
    const label = i === here ? "text-primary font-semibold" : i < here ? "text-on-surface font-semibold" : "text-on-surface-variant";
    const sub = i === here ? "text-primary" : "text-on-surface-variant";
    return `
      <div class="flex flex-col items-center text-center">
        <div class="mb-2 flex h-10 items-center">${node}</div>
        <span class="text-label-md ${label}">${s.label}</span>
        <span class="text-body-sm ${sub}">${escapeHtml(s.sub)}</span>
      </div>`;
  }).join("");
}

// ---- stats ---------------------------------------------------------------------------

// The numbers, said once, as the line under the greeting.
function paintStats() {
  if (!apps.length) return;
  const weekAgo = Date.now() - 7 * 24 * 3600 * 1000;
  const fresh = apps.filter((a) => a.date_created && new Date(a.date_created).getTime() >= weekAgo).length;
  const m = pct(average(apps.map((a) => a.match_score)));
  const parts = [`${fresh} new role${fresh === 1 ? "" : "s"} this week`];
  if (m !== null) parts.push(`an average match of ${m}%`);
  document.getElementById("home-summary").textContent = `${parts.join(", ")}.`;
}

// ---- recent applications -----------------------------------------------------------

const STATUS_WORDS = {
  pending_review: "Ready to apply",
  cv_rewritten_notify_user: "Tailored",
  auto_submitted: "Submitted",
  would_apply: "Would have applied",
  sent: "Emailed",
};

function recentRow(a) {
  const p = pct(a.match_score);
  return `
    <div class="flex items-center gap-space-md px-space-md py-space-md">
      <div class="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg bg-surface-container text-label-md font-semibold text-on-surface">${escapeHtml(initialsOf(a.company || a.title).slice(0, 2))}</div>
      <div class="min-w-0 flex-1">
        <p class="truncate text-label-md font-semibold text-on-surface">${escapeHtml(a.title || "Untitled role")}</p>
        <p class="truncate text-body-sm text-on-surface-variant">${companyLine(a.company, a.source)}</p>
      </div>
      ${p !== null ? `<span class="rounded-full bg-surface-container px-2.5 py-0.5 text-label-sm text-secondary">${p}% Match</span>` : ""}
      <span class="w-32 text-right text-body-sm text-on-surface">${escapeHtml(STATUS_WORDS[a.status] || (a.status || "").replace(/_/g, " "))}</span>
      <a href="applications.html?id=${a.id}" class="flex items-center gap-0.5 text-label-md text-primary hover:underline">View <span class="ms text-[18px]">arrow_forward</span></a>
    </div>`;
}

function paintRecent() {
  const el = document.getElementById("recent-apps");
  const recent = sortApplicationsBy(apps, "date_created").slice(0, 4);
  el.innerHTML = recent.length ? recent.map(recentRow).join("")
    : `<p class="sa-empty">No applications yet — run a search to find your first roles.</p>`;
}

function paintInsight() {
  if (!gaps.length) return;
  const top = gaps[0];
  document.getElementById("insight-text").textContent =
    `${top.skill} was missing from ${top.count} of the jobs you matched — the most common gap right now.`;
  const box = document.getElementById("insight");
  box.classList.remove("hidden");
  box.classList.add("flex");
}

// ---- load ---------------------------------------------------------------------------

async function load() {
  const [s, a, g] = await Promise.allSettled([
    getJSON("/api/stats"),
    getJSON("/api/applications?limit=200"),
    getJSON("/api/skill-gaps?limit=10"),
  ]);
  if (s.status === "fulfilled") stats = s.value;
  if (a.status === "fulfilled") apps = a.value;
  if (g.status === "fulfilled") gaps = g.value;

  if (a.status === "rejected") {
    document.getElementById("recent-apps").innerHTML = `<p class="sa-empty">Could not reach the backend API.</p>`;
  } else {
    paintRecent();
  }
  paintStats();
  paintTrajectory();
  paintInsight();
}

load();
