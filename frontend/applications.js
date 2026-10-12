// Applications page: the pipeline list, a detail panel for the selected
// application, and the send dialog (what used to be the Email page).

const PAGE_SIZE = 8;

let allApps = [];
let byId = {};
let statusFilter = "all";
let selectedId = null;
let shown = PAGE_SIZE;
let gmail = null;   // /api/email/status, loaded once

// ---- "Why?" modal adapter ------------------------------------------------------
function openWhyModalForApplication(id) {
  const a = byId[id];
  if (!a) return;
  openWhyModal({
    scoring_engine: a.scoring_engine,
    ats_score: a.ats_score,
    ats_breakdown: a.ats_breakdown,
    tailored_ats_score: a.tailored_ats_score,
    tailored_ats_breakdown: a.tailored_ats_breakdown,
    tailored_download_url: cvDownloadUrl(a.cv_path),
    title: a.title,
    company: a.company,
  });
}
window.openWhyModalForApplication = openWhyModalForApplication;

// ---- filtering -----------------------------------------------------------------

function matchesStatus(a, f) {
  if (f === "pending_review") return a.status === "pending_review";
  if (f === "tailored") return hasTailored(a);
  if (f === "applied") return isApplied(a);
  return true;
}

function visibleApps() {
  const q = document.getElementById("app-filter").value.trim().toLowerCase();
  const list = allApps.filter((a) => matchesStatus(a, statusFilter)
    && (!q || (a.title || "").toLowerCase().includes(q) || (a.company || "").toLowerCase().includes(q)));
  return sortApplicationsBy(list, document.getElementById("sort-select").value);
}

// ---- list ----------------------------------------------------------------------------

const STATUS_CHIP = {
  pending_review: ["Ready to apply", "bg-primary-fixed/60 text-primary"],
  cv_rewritten_notify_user: ["Tailored", "bg-tertiary-fixed/60 text-on-tertiary-fixed-variant"],
  auto_submitted: ["Submitted", "bg-surface-container-high text-on-surface-variant"],
  would_apply: ["Would have applied · dry run", "bg-secondary-container/60 text-on-secondary-fixed-variant"],
  sent: ["Emailed", "bg-surface-container-high text-on-surface-variant"],
};

function statusPill(a) {
  const [label, cls] = STATUS_CHIP[a.status] || [(a.status || "").replace(/_/g, " "), "bg-surface-container-high text-on-surface-variant"];
  const when = isApplied(a) && a.date_applied ? ` ${fmtDate(a.date_applied)}` : "";
  return `<span class="inline-flex whitespace-nowrap rounded-full px-2.5 py-0.5 text-label-sm normal-case tracking-normal ${cls}">${escapeHtml(label + when)}</span>`;
}

function listRow(a) {
  const selected = a.id === selectedId;
  const p = pct(a.match_score);
  return `
    <button type="button" data-id="${a.id}"
      class="grid w-full grid-cols-[1fr_7rem_9rem_2.5rem] items-center gap-space-sm border-l-4 px-space-md py-space-md text-left transition-colors ${selected
        ? "border-primary bg-surface-container-low"
        : "border-transparent hover:bg-surface-container-low/60"}">
      <div class="min-w-0">
        <p class="truncate text-headline-sm text-on-surface">${escapeHtml(a.title || "Untitled role")}</p>
        <p class="truncate text-body-sm text-on-surface-variant">${companyLine(a.company, a.source)}</p>
      </div>
      <span class="flex items-center gap-1.5 text-label-md text-on-surface">${p !== null
        ? `<span class="h-1.5 w-1.5 rounded-full bg-secondary"></span>${p}%`
        : `<span class="text-on-surface-variant">—</span>`}</span>
      <span>${statusPill(a)}</span>
      <span class="ms text-right text-on-surface-variant">arrow_forward</span>
    </button>`;
}

function renderList() {
  const list = visibleApps();
  const el = document.getElementById("app-list");
  el.innerHTML = list.length
    ? list.slice(0, shown).map(listRow).join("")
    : `<p class="sa-empty">${allApps.length ? "No applications match this view." : "No applications yet — run a search to find roles."}</p>`;
  const footer = document.getElementById("list-footer");
  footer.hidden = !list.length;
  document.getElementById("list-count").textContent = `Showing ${Math.min(shown, list.length)} of ${list.length}`;
  document.getElementById("list-more").hidden = shown >= list.length;
}

document.getElementById("app-list").addEventListener("click", (e) => {
  const row = e.target.closest("button[data-id]");
  if (row) select(Number(row.dataset.id));
});
document.getElementById("list-more").addEventListener("click", () => { shown += PAGE_SIZE; renderList(); });
document.getElementById("app-filter").addEventListener("input", () => { shown = PAGE_SIZE; renderList(); });
document.getElementById("sort-select").addEventListener("change", renderList);
bindTabs(document.getElementById("status-tabs"), (f) => { statusFilter = f; shown = PAGE_SIZE; renderList(); });

// The ranking factors' own names are engineering terms; say them plainly.
function plainFactor(f) {
  const label = f.label || "";
  if (/semantic|similarity/i.test(label)) {
    return { label: "Similar to your CV", evidence: "How closely this posting's wording matches your CV as a whole." };
  }
  return { label, evidence: f.evidence || "" };
}

// ---- side widgets --------------------------------------------------------------------


// ---- detail panel ----------------------------------------------------------------------

function topFactors(mb) {
  // The three factors that contributed most to the match (score x weight).
  return Object.values(mb || {})
    .filter((c) => c && c.label)
    .sort((x, y) => (y.score || 0) * (y.weight || 0) - (x.score || 0) * (x.weight || 0))
    .slice(0, 3);
}

function matchedRequirementNames(breakdown, n) {
  const names = [];
  Object.values(breakdown || {}).forEach((b) => (b.items || []).forEach((it) => {
    if (it.relation && it.relation !== "none" && it.name) names.push(it.name);
  }));
  return names.slice(0, n);
}

function ring(score) {
  const p = pct(score);
  const r = 26, c = 2 * Math.PI * r;
  const off = p === null ? c : c * (1 - p / 100);
  return `
    <div class="relative h-20 w-20 flex-shrink-0">
      <svg viewBox="0 0 64 64" class="h-20 w-20 -rotate-90">
        <circle cx="32" cy="32" r="${r}" fill="none" stroke="currentColor" stroke-width="5" class="text-surface-container-highest"/>
        <circle cx="32" cy="32" r="${r}" fill="none" stroke="currentColor" stroke-width="5" stroke-linecap="round"
                stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}" class="text-primary"/>
      </svg>
      <span class="absolute inset-0 flex items-center justify-center text-label-md font-semibold text-primary">${p === null ? "—" : p + "%"}</span>
    </div>`;
}

function renderDetail(a) {
  const panel = document.getElementById("detail");
  if (!a) { panel.innerHTML = `<p class="sa-empty">Select an application.</p>`; return; }

  const tailored = hasTailored(a);
  const cvUrl = cvDownloadUrl(a.cv_path);
  const [statusLabel, statusCls] = STATUS_CHIP[a.status] || ["", ""];

  let primary;
  if (a.status === "pending_review" || a.status === "would_apply") {
    primary = `<button type="button" class="sa-btn-primary flex-1 py-3" id="submit-btn"><span class="ms text-[18px]">send</span>${a.status === "would_apply" ? "Send it for real" : "Send application"}<span class="ms text-[18px]">arrow_forward</span></button>`;
  } else if (cvUrl) {
    primary = `<a href="${API_BASE}${cvUrl}" target="_blank" rel="noopener" class="sa-btn-primary flex-1 py-3"><span class="ms text-[18px]">download</span>Download tailored CV</a>`;
  } else {
    primary = `<a href="${escapeHtml(a.url || "#")}" target="_blank" rel="noopener" class="sa-btn-primary flex-1 py-3"><span class="ms text-[18px]">open_in_new</span>Open posting</a>`;
  }

  const factors = topFactors(a.match_breakdown);
  const breakdown = tailored ? a.tailored_ats_breakdown : a.ats_breakdown;
  const keywords = matchedRequirementNames(breakdown, 4);

  panel.innerHTML = `
    <div class="mb-space-sm flex items-center justify-between gap-space-sm">
      <div class="flex min-w-0 flex-wrap items-center gap-1.5 text-body-sm text-on-surface-variant">
        <span>${companyLine(a.company, a.source)}</span>
      </div>
      ${statusLabel ? `<span class="whitespace-nowrap rounded px-2 py-0.5 text-label-sm normal-case tracking-normal ${statusCls}">${escapeHtml(statusLabel)}</span>` : ""}
    </div>
    <h2 class="mb-space-md text-headline-md text-on-surface">${escapeHtml(a.title || "Untitled role")}</h2>
    <div class="mb-space-lg flex gap-space-sm">
      ${primary}
      <a href="${escapeHtml(a.url || "#")}" target="_blank" rel="noopener" title="Open the posting"
         class="flex w-11 items-center justify-center rounded-lg bg-surface-container-high text-on-surface-variant hover:bg-surface-container-highest">
        <span class="ms text-[20px]">open_in_new</span>
      </a>
    </div>

    <div class="flex items-center justify-between gap-space-sm">
      <span class="text-label-md font-semibold text-on-surface-variant">Overview</span>
      <button type="button" id="rescore-btn" title="Recompute this application's ATS score with the current scoring rules"
              class="flex items-center gap-1 text-label-md text-primary hover:underline disabled:opacity-50">
        <span class="ms text-[16px]">refresh</span>Re-score
      </button>
    </div>
    <p id="rescore-message" class="hidden"></p>
    <div class="mb-space-lg mt-space-sm grid grid-cols-2 gap-space-sm">
      <div class="rounded-lg bg-surface-container-low p-space-sm">
        <span class="text-label-sm normal-case tracking-normal text-on-surface-variant">ATS score</span>
        <p class="text-headline-md text-on-surface">${tailored ? pct(a.tailored_ats_score) + "%" : (a.ats_score != null ? pct(a.ats_score) + "%" : "—")}</p>
        <span class="text-label-sm normal-case tracking-normal text-on-surface-variant">${tailored ? `Tailored · was ${pct(a.ats_score)}%` : "Master CV"}</span>
      </div>
      <div class="rounded-lg bg-surface-container-low p-space-sm">
        <span class="text-label-sm normal-case tracking-normal text-on-surface-variant">Found</span>
        <p class="text-headline-md text-on-surface">${fmtDate(a.date_created)}</p>
        <span class="text-label-sm normal-case tracking-normal text-on-surface-variant">via ${escapeHtml(sourceName(a.source) || "—")}</span>
      </div>
    </div>

    <div class="mb-space-lg flex items-center justify-between gap-space-md rounded-xl bg-surface-container-low p-space-md">
      <div>
        <span class="text-label-md font-semibold text-on-surface-variant">Fit score</span>
        <p class="text-headline-md text-on-surface">${a.match_score != null ? pct(a.match_score) + "% fit" : "Not ranked yet"}</p>
      </div>
      ${ring(a.match_score)}
    </div>

    ${factors.length ? `
    <div class="mb-space-lg">
      <div class="mb-space-sm flex items-center justify-between">
        <span class="text-label-md font-semibold text-on-surface-variant">Why this role fits</span>
        <span class="text-label-sm normal-case tracking-normal text-primary">${factors.length} factors</span>
      </div>
      <div class="flex flex-col gap-space-sm">
        ${factors.map((f) => `
          <div class="flex gap-space-sm rounded-lg bg-surface-container-low p-space-sm">
            <span class="ms mt-0.5 text-[18px] ${(f.score || 0) >= 0.5 ? "text-secondary" : "text-error"}">${(f.score || 0) >= 0.5 ? "check_circle" : "error"}</span>
            <div>
              <p class="text-label-md font-semibold text-on-surface">${escapeHtml(plainFactor(f).label)} · ${pct(f.score)}%</p>
              <p class="text-body-sm text-on-surface-variant">${escapeHtml(plainFactor(f).evidence)}</p>
            </div>
          </div>`).join("")}
      </div>
    </div>` : ""}

    ${a.ats_breakdown ? `
    <div class="mb-space-lg rounded-xl bg-surface-container-low p-space-md">
      <div class="mb-1 flex items-start justify-between gap-space-sm">
        <span class="flex items-center gap-2 text-label-md font-semibold text-on-surface"><span class="ms text-[18px] text-primary">description</span>${tailored ? "Tailored dossier generated" : "ATS evidence"}</span>
        ${tailored ? `<span class="rounded bg-surface-container-high px-2 py-0.5 text-label-sm normal-case tracking-normal text-secondary">${pct(a.tailored_ats_score)}% ATS</span>` : ""}
      </div>
      ${keywords.length ? `<p class="text-body-sm text-on-surface-variant"><span class="text-on-surface">Requirements evidenced:</span> ${keywords.map(escapeHtml).join(", ")}.</p>` : ""}
      <div class="mt-space-sm flex items-center justify-between">
        <button type="button" class="flex items-center gap-1 text-label-md text-primary hover:underline" onclick="openWhyModalForApplication(${a.id})">Full requirement breakdown <span class="ms text-[16px]">north_east</span></button>
        ${cvUrl ? `<a href="${API_BASE}${cvUrl}" target="_blank" rel="noopener" class="text-label-sm normal-case tracking-normal text-on-surface-variant hover:text-primary">Preview tailored PDF</a>` : ""}
      </div>
    </div>` : ""}

    ${coverLetterCard(a)}

    ${a.date_applied ? `<p class="text-body-sm text-on-surface-variant">Applied ${fmtDate(a.date_applied)}</p>` : ""}`;

  if (a.apply_note || a.apply_email) {
    panel.querySelector("h2").insertAdjacentHTML("afterend", `
      <p class="-mt-space-sm mb-space-md flex items-start gap-2 rounded-lg bg-surface-container-low px-space-sm py-2 text-body-sm text-on-surface-variant">
        <span class="ms mt-0.5 text-[16px] text-primary">${a.apply_email ? "alternate_email" : "info"}</span>
        <span>${escapeHtml(a.apply_note || `The posting asks for applications by email to ${a.apply_email}.`)}</span>
      </p>`);
  }

  const submit = document.getElementById("submit-btn");
  if (submit) submit.addEventListener("click", () => openSend(a));
  wireCoverLetter(a);
  wireRescore(a);
}

// ---- re-score ----------------------------------------------------------------------------
// Scores are written when a job is processed; this recomputes one with the
// current rules. The note survives the panel re-render that follows.
let rescoreNote = null;   // {id, text, error}

function wireRescore(a) {
  const btn = document.getElementById("rescore-btn");
  const msg = document.getElementById("rescore-message");
  if (rescoreNote && rescoreNote.id === a.id) {
    msg.className = `mt-1 text-body-sm ${rescoreNote.error ? "text-error" : "text-on-surface-variant"}`;
    msg.textContent = rescoreNote.text;
    rescoreNote = null;
  }
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    btn.innerHTML = `<span class="ms animate-spin text-[16px]">progress_activity</span>Re-scoring…`;
    msg.className = "mt-1 text-body-sm text-on-surface-variant";
    msg.textContent = "Scoring your CV against this job again. This takes about half a minute.";
    try {
      const r = await postJSON(`/api/applications/${a.id}/rescore`, {});
      const pctOf = (v) => (v == null ? "—" : `${Math.round(v * 100)}%`);
      const parts = [`ATS ${pctOf(r.before.ats_score)} → ${pctOf(r.after.ats_score)}`];
      if (r.tailored_rescored) parts.push(`tailored ${pctOf(r.before.tailored_ats_score)} → ${pctOf(r.after.tailored_ats_score)}`);
      rescoreNote = { id: a.id, text: `Re-scored: ${parts.join(" · ")}.` };
      await loadApplications(a.id);
    } catch (err) {
      rescoreNote = { id: a.id, text: err.message || "Couldn't re-score this application.", error: true };
      renderDetail(a);
    }
  });
}

// ---- cover letter ---------------------------------------------------------------------

function coverLetterCard(a) {
  const letter = a.cover_letter || "";
  return `
    <div class="mb-space-lg rounded-xl bg-surface-container-low p-space-md">
      <div class="mb-space-sm flex items-center justify-between gap-space-sm">
        <span class="flex items-center gap-2 text-label-md font-semibold text-on-surface"><span class="ms text-[18px] text-primary">mail</span>Cover letter</span>
        <span class="flex items-center gap-space-sm">
          ${letter ? `<button type="button" class="text-label-md text-primary hover:underline" id="cl-copy">Copy</button>` : ""}
          <button type="button" class="flex items-center gap-1 text-label-md text-primary hover:underline" id="cl-write">
            <span class="ms text-[16px]">${letter ? "refresh" : "edit_note"}</span>${letter ? "Rewrite" : "Write one"}
          </button>
        </span>
      </div>
      ${letter ? `
        <details class="group">
          <summary class="cursor-pointer list-none text-body-sm text-on-surface-variant">
            <span class="line-clamp-2 group-open:hidden">${escapeHtml(letter)}</span>
            <span class="text-label-md text-primary group-open:hidden">Read it</span>
          </summary>
          <p class="whitespace-pre-line text-body-sm text-on-surface">${escapeHtml(letter)}</p>
        </details>
        ${a.cover_letter_source === "template" ? `<p class="mt-space-sm text-label-sm normal-case tracking-normal text-on-surface-variant">Written from a template because the AI model wasn't available. Rewrite it once the model is back.</p>` : ""}`
      : `<p class="text-body-sm text-on-surface-variant">None yet. Sa'ei writes one when a job is drafted or applied for, from your profile only.</p>`}
      <span class="save-message mt-1 block" id="cl-message"></span>
    </div>`;
}

function wireCoverLetter(a) {
  const copy = document.getElementById("cl-copy");
  if (copy) copy.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(a.cover_letter); copy.textContent = "Copied"; }
    catch (e) { copy.textContent = "Copy failed"; }
  });
  const write = document.getElementById("cl-write");
  if (!write) return;
  write.addEventListener("click", async () => {
    const msg = document.getElementById("cl-message");
    write.disabled = true;
    msg.textContent = "Writing…";
    msg.className = "save-message mt-1 block";
    try {
      const r = await postJSON(`/api/applications/${a.id}/cover-letter`, {});
      a.cover_letter = r.cover_letter;
      a.cover_letter_source = r.cover_letter_source;
      renderDetail(a);
    } catch (err) {
      msg.textContent = err.message || "Couldn't write the letter.";
      msg.className = "save-message save-error mt-1 block";
      write.disabled = false;
    }
  });
}

function select(id) {
  selectedId = id;
  renderDetail(byId[id]);
  renderList();
}

// ---- send dialog ------------------------------------------------------------------------

const sendModal = document.getElementById("send-modal");
const sendMessage = document.getElementById("send-message");
const sendBtn = document.getElementById("send-btn");
let sending = null;

function openSend(a) {
  sending = a;
  const title = a.title || "this role";
  const company = a.company || "your company";
  document.getElementById("send-title").textContent = `${title} · ${company}`;
  document.getElementById("to-email").value = a.apply_email || "";
  document.getElementById("to-hint").textContent = a.apply_email
    ? "Found in the job posting. Check it before sending."
    : "No application address in the posting. Add the company's hiring email.";
  document.getElementById("subject").value = `Application for ${title} at ${company}`;
  document.getElementById("body").value = a.cover_letter
    || `Hello,\n\nI'd like to apply for the ${title} position at ${company}. My CV is attached.\n\nBest regards,`;
  // Mirrors the send endpoint in api.py: it attaches the application's own
  // CV version when one was written, otherwise the master CV.
  const cvUrl = cvDownloadUrl(a.cv_path);
  document.getElementById("attachment").innerHTML = `
    <span class="ms text-primary">picture_as_pdf</span>
    <span class="flex-1 text-body-sm text-on-surface">${cvUrl ? "Tailored CV for this role" : "Your master CV"} is attached automatically</span>
    ${cvUrl ? `<a href="${API_BASE}${cvUrl}" target="_blank" rel="noopener" class="text-label-md text-primary hover:underline">Preview</a>` : ""}`;
  const warn = document.getElementById("gmail-warning");
  if (gmail && !gmail.authenticated) {
    warn.innerHTML = `Gmail isn't connected yet, so this can't be sent. Connect it under <a class="underline" href="settings.html#email">Settings → Email &amp; reports</a>.`;
    warn.classList.remove("hidden");
  } else {
    warn.classList.add("hidden");
  }
  sendMessage.textContent = "";
  sendMessage.className = "save-message mr-auto";
  sendBtn.disabled = false;
  sendModal.hidden = false;
  document.getElementById(a.apply_email ? "subject" : "to-email").focus();
}

function closeSend() { sendModal.hidden = true; sending = null; }
document.getElementById("send-close").addEventListener("click", closeSend);
document.getElementById("send-cancel").addEventListener("click", closeSend);
sendModal.addEventListener("click", (e) => { if (e.target === sendModal) closeSend(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !sendModal.hidden) closeSend(); });

document.getElementById("send-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!sending) return;
  sendBtn.disabled = true;
  sendMessage.textContent = "Sending…";
  sendMessage.className = "save-message mr-auto";
  try {
    const result = await postJSON(`/api/applications/${sending.id}/send-email`, {
      to_email: document.getElementById("to-email").value,
      subject: document.getElementById("subject").value,
      body: document.getElementById("body").value,
    });
    sendMessage.textContent = result.status === "dry_run" ? "Logged (dry run — not actually sent)." : "Sent.";
    sendMessage.className = "save-message save-success mr-auto";
    const id = sending.id;
    await loadApplications(id);
    setTimeout(closeSend, 1200);
  } catch (err) {
    sendMessage.textContent = err.message || "Failed to send.";
    sendMessage.className = "save-message save-error mr-auto";
    sendBtn.disabled = false;
  }
});

// ---- load ----------------------------------------------------------------------------------

async function loadApplications(keepId) {
  try {
    allApps = await getJSON("/api/applications?limit=1000");
  } catch (e) {
    document.getElementById("app-list").innerHTML = `<p class="sa-empty">Could not reach the backend API.</p>`;
    return;
  }
  byId = {};
  allApps.forEach((a) => { byId[a.id] = a; });
  document.querySelectorAll("[data-count]").forEach((el) => {
    el.textContent = allApps.filter((a) => matchesStatus(a, el.dataset.count)).length;
  });

  const params = new URLSearchParams(location.search);
  const wanted = keepId || Number(params.get("id"));
  if (!keepId && params.get("status")) {
    const tab = document.querySelector(`#status-tabs [data-filter="${CSS.escape(params.get("status"))}"]`);
    if (tab) tab.click();
  }
  if (wanted && byId[wanted]) {
    const idx = visibleApps().findIndex((a) => a.id === wanted);
    if (idx >= shown) shown = Math.ceil((idx + 1) / PAGE_SIZE) * PAGE_SIZE;
    select(wanted);
    return;
  }
  const first = visibleApps()[0];
  if (first) select(first.id); else { renderList(); renderDetail(null); }
}

// ---- apply from a link ---------------------------------------------------------------------

document.getElementById("link-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("link-url");
  const btn = document.getElementById("link-btn");
  const msg = document.getElementById("link-message");
  const url = input.value.trim();
  if (!url) return;
  btn.disabled = true;
  msg.className = "mt-space-sm flex items-start gap-2 text-body-sm text-on-surface-variant";
  msg.innerHTML = `<span class="ms text-[16px] animate-spin">progress_activity</span><span>Reading the posting, scoring it and writing your CV and cover letter. This takes a minute or so.</span>`;
  try {
    const r = await postJSON("/api/apply-from-link", { url });
    const word = { pending_review: "ready for you to send", would_apply: "recorded as a dry run",
                   sent: "emailed", cv_rewritten_notify_user: "scored and tailored",
                   already_processed: "already on your list" }[r.status] || (r.status || "").replace(/_/g, " ");
    msg.className = "mt-space-sm flex items-start gap-2 text-body-sm text-on-surface";
    msg.innerHTML = `<span class="ms text-[16px] text-primary">check_circle</span><span>${escapeHtml(`${r.title}${r.company ? ` at ${r.company}` : ""}: ${word}.`)}</span>`;
    input.value = "";
    statusFilter = "all";
    document.querySelectorAll("#status-tabs .sa-tab").forEach((t) => t.classList.toggle("is-active", t.dataset.filter === "all"));
    await loadApplications(r.application_id || undefined);
  } catch (err) {
    msg.className = "mt-space-sm flex items-start gap-2 text-body-sm text-error";
    msg.innerHTML = `<span class="ms text-[16px]">error</span><span>${escapeHtml(err.message || "Couldn't process that link.")}</span>`;
  } finally {
    btn.disabled = false;
  }
});

getJSON("/api/email/status").then((s) => { gmail = s; }).catch(() => { gmail = null; });
loadApplications();
