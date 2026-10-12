// Reports page: one reading column. The opening says the headline numbers as
// a sentence; below it, the skills that would help most, where roles come
// from, this week's news, and everything Sa'ei sent. Every name and number on
// the page comes straight from the API.

let apps = [];
let gaps = [];
let stats = null;
let dispatches = [];
let dispatchFilter = "all";
const PREVIEW = 6;
let expanded = false;

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function listJoin(items) {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

// ---- opening ----------------------------------------------------------------

function paintOpening() {
  const lede = document.getElementById("lede");
  const summary = document.getElementById("momentum");
  const found = apps.length;
  if (!found) {
    lede.textContent = "No roles yet.";
    summary.textContent = "Run a search and this report fills in: your strongest matches, the skills that would open the most doors, and everything Sa'ei sends you.";
    return;
  }
  const high = apps.filter((a) => (a.match_score || 0) >= 0.8).length;
  const best = Math.max(0, ...apps.map((a) => a.match_score || 0));
  const queue = stats ? stats.pending_review : 0;
  const parts = [
    `<b>${plural(found, "role")}</b> found.`,
    high ? `<b>${high}</b> matched 80% or better.` : `Your best match is <b>${Math.round(best * 100)}%</b>.`,
  ];
  if (queue) parts.push(`<b class="rp-hot">${queue}</b> ${queue === 1 ? "is" : "are"} drafted and waiting for you.`);
  lede.innerHTML = parts.join(" ");

  const top = sortApplicationsBy(apps, "match_score").filter((a) => a.match_score != null).slice(0, 3);
  const titles = [...new Set(top.map((a) => a.title).filter(Boolean))];
  let html = titles.length
    ? `Your strongest matches are roles like ${listJoin(titles.map((t) => `<strong>${escapeHtml(t)}</strong>`))}.`
    : "";
  if (gaps.length) {
    const g = gaps[0];
    html += ` Showing <strong>${escapeHtml(g.skill)}</strong> in your experience would meet a requirement in ${plural(g.count, "role")}`
      + `${gaps[1] ? `, and ${escapeHtml(gaps[1].skill)} in another ${gaps[1].count}` : ""}.`;
  }
  summary.innerHTML = html.trim();
}

// ---- skills ------------------------------------------------------------------

function paintGaps() {
  const list = document.getElementById("skill-gaps-list");
  if (!gaps.length) {
    list.innerHTML = `<li class="sa-empty">Nothing recurring yet. Skills appear here once several roles ask for the same thing.</li>`;
    return;
  }
  const base = apps.length || gaps[0].count;
  const top = gaps[0].count;
  list.innerHTML = gaps.slice(0, 6).map((g) => {
    const share = Math.round((g.count / base) * 100);
    const width = Math.max(3, Math.round((g.count / top) * 100));   // longest bar = most-asked skill
    return `
      <li class="rp-bar">
        <span class="rp-bar-name" title="${escapeHtml(g.skill)}">${escapeHtml(g.skill)}</span>
        <span class="rp-bar-track" aria-hidden="true"><span class="rp-bar-fill" style="display:block;width:${width}%"></span></span>
        <span class="rp-bar-count"><b>${plural(g.count, "role")}</b> · ${share}%</span>
      </li>`;
  }).join("");
}

// ---- sources ---------------------------------------------------------------------

const SOURCE_NAMES = {
  linkedin: "LinkedIn", greenhouse: "Greenhouse", lever: "Lever", ashby: "Ashby",
  smartrecruiters: "SmartRecruiters", himalayas: "Himalayas", remotive: "Remotive",
  jobicy: "Jobicy", workingnomads: "Working Nomads", weworkremotely: "We Work Remotely",
  remoteok: "RemoteOK", generic: "Job boards & career pages", wuzzuf: "Wuzzuf", bayt: "Bayt",
  gulftalent: "GulfTalent", tanqeeb: "Tanqeeb", wellfound: "Wellfound", simplyhired: "SimplyHired",
  serpapi: "Google Jobs", watchlist: "Watchlist",
};
const SOURCE_COLORS = ["#9c3e1e", "#0a6a64", "#a06908", "#bc5633", "#86d4cc", "#8a726b", "#ddc0b8"];

function paintSources() {
  const counts = {};
  apps.forEach((a) => { const s = a.source || "other"; counts[s] = (counts[s] || 0) + 1; });
  let entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const total = apps.length;
  const bar = document.getElementById("source-bar");
  const legend = document.getElementById("source-legend");
  if (!total) {
    document.getElementById("source-total").textContent = "No roles yet.";
    bar.hidden = true;
    return;
  }
  // Six named sources at most; the rest share one "Other" slice.
  if (entries.length > 7) {
    const rest = entries.slice(6).reduce((n, [, c]) => n + c, 0);
    entries = [...entries.slice(0, 6), ["other", rest]];
  }
  document.getElementById("source-total").textContent =
    `${plural(total, "role")} from ${plural(Object.keys(counts).length, "source")}.`;
  const name = (s) => SOURCE_NAMES[s] || (s === "other" ? "Other" : s);
  bar.innerHTML = entries.map(([s, n], i) =>
    `<span style="width:${(n / total) * 100}%;background:${SOURCE_COLORS[i % SOURCE_COLORS.length]}" title="${escapeHtml(name(s))}: ${n}"></span>`).join("");
  legend.innerHTML = entries.map(([s, n], i) => `
    <li><i style="background:${SOURCE_COLORS[i % SOURCE_COLORS.length]}"></i>${escapeHtml(name(s))} <b>${Math.round((n / total) * 100)}%</b></li>`).join("");
}

// ---- news: the latest digest, read into points, sources and threads -------------

function parseDigest(text) {
  const [main, boardsPart = ""] = text.split(/\n\s*On the boards this week:\s*\n/);
  const [summary, sourcesPart = ""] = main.split(/\n\s*Sources:\s*\n/);
  const sources = {};
  sourcesPart.replace(/\[(\d+)\]\s*(.+?)(?:\s+—\s+(.+?))?\s*\n\s*(https?:\/\/\S+)/g, (_, n, title, outlet, url) => {
    sources[n] = { title, outlet: outlet || "", url };
  });
  const points = summary.split("\n")
    .map((l) => l.trim())
    .filter((l) => /^[-*•]/.test(l))
    .map((l) => ({
      text: l.replace(/^[-*•]\s*/, "").replace(/\s*\[\d+\]/g, "").trim(),
      refs: [...l.matchAll(/\[(\d+)\]/g)].map((m) => sources[m[1]]).filter(Boolean),
    }));
  const boards = [];
  boardsPart.replace(/-\s*(.+?)\s*\(([^)]*)\)\s*\n\s*(https?:\/\/\S+)/g, (_, title, meta, url) => {
    boards.push({ title, meta, url });
  });
  return { points, boards, fallback: points.length ? "" : summary.trim() };
}

async function loadNews() {
  const body = document.getElementById("news-body");
  const when = document.getElementById("digest-date");
  try {
    const digests = await getJSON("/api/news?limit=1");
    if (!digests.length) {
      when.textContent = "The Sa'ei Weekly arrives on Mondays at 9:00.";
      body.innerHTML = `<p class="text-body-md text-on-surface-variant">No digest yet. Set the topic it follows in <a class="font-semibold text-primary underline" href="settings.html#email">Settings</a>.</p>`;
      return;
    }
    const d = digests[0];
    when.textContent = `From The Sa'ei Weekly on ${d.date_created ? fmtDate(d.date_created) : "its last run"}, following ${d.field || "your field"}.`;
    const { points, boards, fallback } = parseDigest(d.content || "");
    const link = (r) => `<a href="${escapeHtml(r.url)}" target="_blank" rel="noopener">${escapeHtml(r.outlet || "Read the story")}</a>`;
    body.innerHTML = (points.length
      ? `<ul class="rp-news">${points.map((p) => `
          <li>${escapeHtml(p.text)}${p.refs.length ? `<span>${p.refs.map(link).join(", ")}</span>` : ""}</li>`).join("")}</ul>`
      : `<p class="whitespace-pre-wrap text-body-md text-on-surface-variant">${escapeHtml(fallback)}</p>`)
      + (boards.length ? `
        <h3 class="mt-space-lg text-label-lg font-semibold text-on-surface">On the boards</h3>
        <div class="rp-boards">${boards.map((b) => `
          <a href="${escapeHtml(b.url)}" target="_blank" rel="noopener">${escapeHtml(b.title)}<span>${escapeHtml(b.meta)}</span></a>`).join("")}
        </div>` : "");
  } catch (e) {
    body.innerHTML = `<p class="sa-empty">Couldn't load the news digest.</p>`;
  }
}

// ---- sent by Sa'ei ----------------------------------------------------------------

// What went wrong, said plainly; anything unrecognised is shown as it came.
function plainError(err) {
  if (/invalid_grant|sign-in has expired/i.test(err)) return "Google sign-in had expired. Sign in again under Settings, Email & reports.";
  if (/isn't connected|not connected/i.test(err)) return "Gmail wasn't connected. Sign in under Settings, Email & reports.";
  return err;
}

function dispatchItem(d) {
  const failed = d.status === "failed";
  const state = failed ? `<span class="rp-failed">Not sent</span>` : d.dry_run ? "Logged only (dry run)" : "Sent";
  return `
    <li>
      <time datetime="${escapeHtml(d.at || "")}">${d.at ? fmtDate(d.at) : "—"}</time>
      <div>
        <h3>${escapeHtml(d.title)}</h3>
        <p class="rp-meta">${state}${d.sub ? ` to ${escapeHtml(d.sub)}` : ""}</p>
        ${d.error ? `<p class="text-body-sm text-error" title="${escapeHtml(d.error)}">${escapeHtml(plainError(d.error))}</p>` : ""}
        ${d.content ? `<details><summary>Show what was sent</summary><pre>${escapeHtml(d.content)}</pre></details>` : ""}
      </div>
    </li>`;
}

function renderDispatches() {
  const el = document.getElementById("reports-list");
  const more = document.getElementById("reports-more");
  const list = dispatches.filter((d) => dispatchFilter === "all" || d.kind === dispatchFilter);
  if (!list.length) {
    el.innerHTML = `<li class="sa-empty">${dispatchFilter === "email"
      ? "No application emails yet. Approve one on the Applications page to send it."
      : dispatchFilter === "report" ? "No reports yet. The daily summary goes out at 20:00." : "Nothing sent yet."}</li>`;
    more.hidden = true;
    return;
  }
  const shown = expanded ? list : list.slice(0, PREVIEW);
  el.innerHTML = shown.map(dispatchItem).join("");
  more.hidden = shown.length >= list.length;
  more.textContent = `Show ${list.length - shown.length} older`;
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
      kind: "report", at: x.sent_at, status: x.status, dry_run: x.dry_run,
      title: x.report_type === "daily" ? "Daily application summary" : "The Sa'ei Weekly",
      sub: "", content: x.content, error: x.error_message,
    })),
    ...emails.map((x) => ({
      kind: "email", at: x.sent_at, status: x.status, dry_run: x.dry_run,
      title: x.job_title ? `Application: ${x.job_title}${x.company ? ` at ${x.company}` : ""}` : x.subject,
      sub: x.to_email, content: "", error: x.error_message,
    })),
  ].sort((x, y) => new Date(y.at || 0) - new Date(x.at || 0));

  if (a.status === "rejected" && s.status === "rejected") {
    document.getElementById("lede").textContent = "Sa'ei isn't running.";
    document.getElementById("momentum").textContent = "Start it with run.bat (or ./run.sh) and reload this page.";
  } else {
    paintOpening();
  }
  paintGaps();
  paintSources();
  if (r.status === "rejected" && e.status === "rejected") {
    document.getElementById("reports-list").innerHTML = `<li class="sa-empty">Couldn't load what Sa'ei sent.</li>`;
  } else {
    renderDispatches();
  }
}

load();
loadNews();
