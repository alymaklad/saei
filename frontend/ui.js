// Shared rendering helpers for the Sa'ei pages (load after config.js).
// Kept to presentation only: every number shown comes straight from the API
// response the caller passes in.

// Where a role came from, by the name people know it by.
const SOURCE_NAMES = {
  linkedin: "LinkedIn", greenhouse: "Greenhouse", lever: "Lever", ashby: "Ashby",
  smartrecruiters: "SmartRecruiters", himalayas: "Himalayas", remotive: "Remotive",
  jobicy: "Jobicy", workingnomads: "Working Nomads", weworkremotely: "We Work Remotely",
  remoteok: "RemoteOK", generic: "Job board", wuzzuf: "Wuzzuf", bayt: "Bayt",
  gulftalent: "GulfTalent", tanqeeb: "Tanqeeb", wellfound: "Wellfound", simplyhired: "SimplyHired",
  serpapi: "Google Jobs", watchlist: "Watchlist", link: "Pasted link",
};

function sourceName(source) {
  if (!source) return "";
  return SOURCE_NAMES[source] || source.charAt(0).toUpperCase() + source.slice(1);
}

/** "Company · Source", or just the source when the company isn't known. */
function companyLine(company, source) {
  return [company, sourceName(source)].filter(Boolean).map(escapeHtml).join(' <span class="text-outline-variant">·</span> ');
}

function fmtDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function timeAgo(iso) {
  if (!iso) return "—";
  const secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return "just now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins} min${mins === 1 ? "" : "s"} ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days} day${days === 1 ? "" : "s"} ago`;
  return fmtDate(iso);
}

function pct(score) {
  return score === null || score === undefined ? null : Math.round(score * 100);
}

function initialsOf(name) {
  const words = String(name || "?").trim().split(/\s+/).slice(0, 2);
  return words.map((w) => w[0] || "").join("").toUpperCase() || "?";
}

// A stable tint per company, so the same employer keeps its colour everywhere.
const AVATAR_TINTS = [
  "bg-tertiary-fixed text-on-tertiary-fixed",
  "bg-secondary-fixed text-on-secondary-fixed",
  "bg-primary-fixed text-on-primary-fixed",
  "bg-tertiary-fixed-dim text-on-tertiary-fixed",
  "bg-surface-container-highest text-on-surface",
];

function companyAvatar(company, sizeClass = "sa-avatar") {
  const name = company || "?";
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  const tint = AVATAR_TINTS[h % AVATAR_TINTS.length];
  return `<div class="${sizeClass} ${tint}">${escapeHtml(initialsOf(name).slice(0, 2))}</div>`;
}

function matchChip(score) {
  const p = pct(score);
  if (p === null) return "";
  const cls = p >= 75 ? "sa-chip-teal" : p >= 50 ? "sa-chip-amber" : "sa-chip-error";
  const dot = p >= 75 ? "bg-secondary" : p >= 50 ? "bg-tertiary" : "bg-error";
  return `<span class="${cls}"><span class="h-1.5 w-1.5 rounded-full ${dot}"></span>${p}% Match</span>`;
}

function atsChip(score, tailoredScore) {
  const p = pct(score);
  const t = pct(tailoredScore);
  if (p === null && t === null) return "";
  if (t !== null && p !== null && t !== p) {
    return `<span class="sa-chip">ATS ${p}% <span class="ms text-[12px]">arrow_forward</span> ${t}%</span>`;
  }
  return `<span class="sa-chip">ATS ${t !== null ? t : p}%</span>`;
}

const STATUS_META = {
  pending_review: { label: "Pending review", cls: "sa-chip-amber", icon: "hourglass_top" },
  // orchestrator.py: the CV was below the fit threshold, got rewritten, and
  // the user was notified instead of anything being submitted.
  cv_rewritten_notify_user: { label: "Tailored — notified", cls: "sa-chip-primary", icon: "edit_document" },
  auto_submitted: { label: "Auto-submitted", cls: "sa-chip-teal", icon: "task_alt" },
  // Dry run: what WOULD have been submitted or emailed. Nothing was sent.
  would_apply: { label: "Would have applied (dry run)", cls: "sa-chip", icon: "science" },
  sent: { label: "Emailed", cls: "sa-chip-teal", icon: "outgoing_mail" },
};

function statusChip(status) {
  const m = STATUS_META[status] || { label: (status || "unknown").replace(/_/g, " "), cls: "sa-chip", icon: "radio_button_unchecked" };
  return `<span class="${m.cls}"><span class="ms text-[14px]">${m.icon}</span>${escapeHtml(m.label)}</span>`;
}

// Applications only get cv_path (a server-side path like "cv_output/cv_12.pdf")
// rather than a ready-made download URL -- the tailored-CV file is served
// statically at /files/cv-rewrites/<filename>, so just take the basename.
function cvDownloadUrl(cvPath) {
  if (!cvPath) return null;
  const filename = cvPath.split(/[\\/]/).pop();
  return `/files/cv-rewrites/${filename}`;
}

function hasTailored(a) {
  return a.tailored_ats_score !== null && a.tailored_ats_score !== undefined;
}

function isApplied(a) {
  return a.status === "auto_submitted" || a.status === "sent" || !!a.email_sent;
}

/** Sort a list of applications by one numeric/date key, descending by default.
 *  Rows without a value sort last in BOTH directions -- "no score" isn't a
 *  low score, and burying them under an ascending sort would be just as wrong
 *  as topping it. */
function sortApplicationsBy(apps, key, dir = "desc") {
  return [...apps].sort((a, b) => {
    const x = a[key];
    const y = b[key];
    const xMissing = x === null || x === undefined;
    const yMissing = y === null || y === undefined;
    if (xMissing && yMissing) return 0;
    if (xMissing) return 1;
    if (yMissing) return -1;
    const cmp = key === "date_created" ? new Date(x) - new Date(y) : x - y;
    return dir === "asc" ? cmp : -cmp;
  });
}

/** Tab-style segmented control: calls onChange(value) with the clicked
 *  button's data-filter, and moves the .is-active marker. */
function bindTabs(container, onChange, attr = "filter") {
  container.querySelectorAll(".sa-tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      container.querySelectorAll(".sa-tab").forEach((b) => b.classList.toggle("is-active", b === btn));
      onChange(btn.dataset[attr]);
    });
  });
}

/** Circular score ring (Applications audit panel). */
function scoreRing(score, colorClass, label, sub) {
  const p = pct(score);
  const r = 40;
  const c = 2 * Math.PI * r;
  const off = p === null ? c : c * (1 - p / 100);
  return `
    <div class="flex flex-col items-center text-center">
      <div class="relative h-28 w-28">
        <svg viewBox="0 0 100 100" class="h-28 w-28 -rotate-90">
          <circle cx="50" cy="50" r="${r}" fill="none" stroke="currentColor" stroke-width="9" class="text-surface-container-highest"/>
          <circle cx="50" cy="50" r="${r}" fill="none" stroke="currentColor" stroke-width="9" stroke-linecap="round"
                  stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}" class="${colorClass} transition-all duration-500"/>
        </svg>
        <div class="absolute inset-0 flex flex-col items-center justify-center">
          <span class="text-headline-lg font-bold text-on-surface">${p === null ? "—" : p}<span class="text-label-md">${p === null ? "" : "%"}</span></span>
        </div>
      </div>
      <span class="mt-2 text-label-md font-bold text-on-surface">${escapeHtml(label)}</span>
      <span class="text-label-sm text-on-surface-variant">${escapeHtml(sub)}</span>
    </div>`;
}

/** The ranking agent's match breakdown (agents/ranking_agent.py): one tile
 *  per component with its score, weight and the evidence sentence it was
 *  scored on. */
function matchBreakdownTiles(mb) {
  const entries = Object.values(mb || {}).filter((c) => c && c.label);
  if (!entries.length) return `<p class="sa-empty">No match breakdown recorded for this job.</p>`;
  return entries.map((c) => {
    const p = pct(c.score) ?? 0;
    const good = p >= 70, bad = p < 40;
    const tone = good ? "text-secondary" : bad ? "text-error" : "text-tertiary";
    const bg = bad ? "bg-error-container/40" : "bg-surface-container-low";
    const icon = good ? "check_circle" : bad ? "warning" : "change_circle";
    return `
      <div class="flex flex-col rounded-lg ${bg} p-2.5">
        <div class="mb-1 flex items-center justify-between gap-2 ${tone}">
          <span class="text-label-sm font-semibold">${escapeHtml(c.label)}</span>
          <span class="flex items-center gap-1 text-label-sm font-bold">${p}%<span class="ms text-[16px]">${icon}</span></span>
        </div>
        <span class="text-label-sm text-on-surface-variant">${escapeHtml(c.evidence || "")}</span>
        <span class="mt-1 text-[11px] text-outline">weight ${Math.round((c.weight || 0) * 100)}%</span>
      </div>`;
  }).join("");
}
