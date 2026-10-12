// Shared "Why?" modal (the 2026-09-25 breakdown design) -- renders a job's ATS
// score breakdown (original CV vs. tailored CV, if one was generated) side by
// side with the actual document, requirement evidence, and matched/missing chips. Used by
// both the Dashboard's applications table and the CV page's Tailored CVs
// table -- both pages include the same modal markup (#why-modal, see
// index.html / cv.html) and this one script drives it.
//
// Load order matters: config.js (API_BASE/getJSON/escapeHtml) must load
// before this file, and this file must load before app.js/cv.js.
//
// Callers open the modal with a plain "record" object shaped like:
//   { ats_score, ats_breakdown, tailored_ats_score, tailored_ats_breakdown,
//     tailored_download_url }
// -- tailored_* fields may be null/undefined when a job never went through
// the CV-rewrite path (see openWhyModalForApplication in app.js and
// openWhyModalForRewrite in cv.js for how each page builds one of these
// from its own API response shape).

function fmtPct(score) {
  return score !== null && score !== undefined ? Math.round(score * 100) + "%" : "—";
}

const RATING_LABEL = { excellent: "Excellent", needs_work: "Needs Work", critical: "Critical" };

function ratingKey(score) {
  if (score === null || score === undefined) return "critical";
  if (score >= 0.8) return "excellent";
  if (score >= 0.6) return "needs_work";
  return "critical";
}

// Mirrors agents/ats_agent.py's WEIGHTS/PILLAR_LABELS ordering.
const PILLAR_ORDER = ["keyword_match", "formatting", "section_completeness", "experience_alignment"];
const PILLAR_LABELS = {
  keyword_match: "Keyword Match",
  formatting: "Formatting & Parsability",
  section_completeness: "Section Completeness",
  experience_alignment: "Experience Alignment",
};

function fillClass(score) {
  if (score >= 0.8) return "fill-ok";
  if (score >= 0.6) return "";
  return "fill-danger";
}

function pillarEvidence(key, pillar) {
  if (key === "keyword_match") {
    const n = pillar.matched_skills ? pillar.matched_skills.length : 0;
    const total = pillar.required_skills ? pillar.required_skills.length : 0;
    return `Matched ${n} of ${total} required skills/keywords.`;
  }
  if (key === "formatting") {
    return pillar.issues && pillar.issues.length
      ? pillar.issues.join("; ") + "."
      : "No formatting issues detected.";
  }
  if (key === "section_completeness") {
    return pillar.missing_sections && pillar.missing_sections.length
      ? `Missing: ${pillar.missing_sections.join(", ")}.`
      : "All standard sections found.";
  }
  if (key === "experience_alignment") {
    return pillar.note || "";
  }
  return "";
}

// ---- requirements-engine rendering ------------------------------------------
//
// There is one scoring engine now, but not one shape of stored row. The
// four-pillar scorer was retired from the code; the rows it wrote are still in
// the database, and a breakdown is not self-describing enough to sniff
// reliably, so every record carries `scoring_engine` and the renderers
// dispatch on it. Rows written before that column existed come back as
// "legacy" from the API, which is what they are.
//
//   legacy       { keyword_match: {...}, formatting: {...}, ... }   ARCHIVED
//   requirements { required_skills: {label, score, weight, items:[...]}, ... }
//
// The legacy branch below is kept for reading history, not for scoring. It
// says so on screen, because those numbers came from a scorer whose defects
// are the reason it was replaced -- see SCORING.md.

const RELATION_SHORT = {
  exact: "Exact", alias: "Alias", implied: "Prerequisite", subset: "Subset",
  semantic_support: "Semantic support", none: "Not found",
};

// A relation this map has never heard of still has to render as words. The
// backend ships `relation_label` on every row for exactly this reason; adding
// `implied` here without the fallback would just move the next "undefined"
// badge one release down the road.
function relationShort(item) {
  return RELATION_SHORT[item.relation] || item.relation_label || item.relation;
}

function requirementRow(item) {
  const found = item.relation && item.relation !== "none";
  const label = found
    ? relationShort(item) + (item.evidence_location === "claimed" ? ", listed only" : "")
    : "Not found";
  const points = `${item.points_earned ?? 0}/${item.points ?? 0} pts`;
  const evidence = found && item.evidence
    ? `<p class="req-evidence">“${escapeHtml(String(item.evidence).slice(0, 160))}”</p>`
    : "";
  const via = item.via ? `<p class="req-via">${escapeHtml(item.via)}</p>` : "";
  return `
    <li class="req-row${found ? "" : " req-row-missing"}">
      <span class="req-name">${escapeHtml(item.name || "")}</span>
      <span class="req-tags">
        <span class="req-chip req-rel-${item.relation || "none"}">${escapeHtml(label)}</span>
        <span class="req-points">${points}</span>
      </span>
      ${evidence}${via}
    </li>
  `;
}

function renderRequirementBuckets(breakdown) {
  if (!breakdown || !Object.keys(breakdown).length) {
    return `<p class="empty">No score breakdown recorded for this CV.</p>`;
  }
  return Object.entries(breakdown).map(([, bucket]) => {
    const pct = Math.round((bucket.score || 0) * 100);
    // The experience bucket carries a prose note instead of requirement rows;
    // it is scored from merged CV date intervals, not from term matching.
    const note = bucket.detail && bucket.detail.note
      ? `<p class="score-pillar-evidence">${escapeHtml(bucket.detail.note)}</p>` : "";
    const items = (bucket.items || []).length
      ? `<ul class="req-list">${bucket.items.map(requirementRow).join("")}</ul>` : "";
    return `
      <div class="score-pillar-row">
        <div class="score-pillar-head">
          <span class="score-pillar-name">${escapeHtml(bucket.label || "")}</span>
          <span class="score-pillar-meta">${pct}% &middot; weight ${Math.round((bucket.weight || 0) * 100)}%</span>
        </div>
        <div class="score-pillar-track"><div class="score-pillar-fill ${fillClass(bucket.score || 0)}" style="width:${pct}%"></div></div>
        ${note}${items}
      </div>
    `;
  }).join("");
}

function requirementChips(breakdown) {
  const matched = [], missing = [];
  Object.values(breakdown || {}).forEach((bucket) => {
    (bucket.items || []).forEach((item) => {
      (item.relation && item.relation !== "none" ? matched : missing).push(item.name);
    });
  });
  return { matched, missing };
}

/** Every CV surface form that actually matched — what to highlight in the
 *  document pane. The requirement's own name is often NOT what appears in the
 *  CV (a subset match highlights "CNN", not "Deep Learning"). */
function requirementMatchedForms(breakdown) {
  const forms = [];
  Object.values(breakdown || {}).forEach((bucket) => {
    (bucket.items || []).forEach((item) => {
      if (item.matched_form) forms.push(item.matched_form);
    });
  });
  return forms;
}

function isRequirementsRecord(record) {
  return (record && record.scoring_engine) === "requirements";
}

function renderPillars(breakdown) {
  if (!breakdown) return `<p class="empty">No score breakdown recorded for this CV.</p>`;
  if (isRequirementsRecord(activeRecord)) return renderRequirementBuckets(breakdown);
  return `<p class="card-sub retired-engine-note">Scored by the four-pillar engine, which
    has since been retired: two of its four pillars never saw the job description
    and returned the same number on every posting. Kept so this record still
    reads, not comparable with newer scores.</p>` + PILLAR_ORDER.map((key) => {
    const p = breakdown[key];
    if (!p) return "";
    const pct = Math.round(p.score * 100);
    return `
      <div class="score-pillar-row">
        <div class="score-pillar-head">
          <span class="score-pillar-name">${PILLAR_LABELS[key]}</span>
          <span class="score-pillar-meta">${pct}% &middot; weight ${Math.round(p.weight * 100)}%</span>
        </div>
        <div class="score-pillar-track"><div class="score-pillar-fill ${fillClass(p.score)}" style="width:${pct}%"></div></div>
        <p class="score-pillar-evidence">${escapeHtml(pillarEvidence(key, p))}</p>
      </div>
    `;
  }).join("");
}

function renderChips(breakdown) {
  let matched, missing;
  if (isRequirementsRecord(activeRecord)) {
    ({ matched, missing } = requirementChips(breakdown));
  } else {
    const kw = breakdown && breakdown.keyword_match;
    matched = (kw && kw.matched_skills) || [];
    missing = (kw && kw.missing_skills) || [];
  }
  return {
    matched: matched.length
      ? matched.map((s) => `<span class="keyword-chip chip-matched">${escapeHtml(s)}</span>`).join("")
      : `<span class="empty">None matched.</span>`,
    missing: missing.length
      ? missing.map((s) => `<span class="keyword-chip chip-missing">${escapeHtml(s)}</span>`).join("")
      : `<span class="empty">None missing.</span>`,
  };
}

function highlightMatches(text, skills) {
  let safe = escapeHtml(text);
  const sorted = (skills || []).filter(Boolean).slice().sort((a, b) => b.length - a.length);
  for (const skill of sorted) {
    const escaped = skill.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    if (!escaped) continue;
    const re = new RegExp(escaped, "gi");
    safe = safe.replace(re, (m) => `<mark class="hl-match">${m}</mark>`);
  }
  return safe;
}

function isPdfUrl(url) {
  return /\.pdf($|\?)/i.test(url || "");
}

// Cached across both tabs and repeated opens -- the original CV doesn't
// change while the dashboard/CV page is open, so there's no need to refetch
// it every time the modal opens.
let cachedCvStatusPromise = null;
function getCvStatus() {
  if (!cachedCvStatusPromise) {
    cachedCvStatusPromise = getJSON("/api/cv/status").catch(() => ({ has_cv: false }));
  }
  return cachedCvStatusPromise;
}

// ---- the dialog (2026-09-25 "ATS Score Breakdown & Evaluation Hub" design) ----
//
// The markup is injected by this file on first open, so a page only needs to
// load why-modal.js -- no copy of the dialog HTML per page.

const BUCKET_SHORT = {
  required_skills: "Req", experience: "Exp", preferred_skills: "Pref",
  responsibilities: "Resp", education: "Edu", soft_skills: "Soft",
};

// Found evidence reads green, partial relations amber, unmet orange -- the
// same split as the legend in the tab bar.
const REL_CHIP = {
  exact: "border-emerald-200 bg-emerald-50 text-emerald-800",
  alias: "border-emerald-200 bg-emerald-50 text-emerald-800",
  implied: "border-tertiary-fixed-dim bg-tertiary-fixed/60 text-on-tertiary-fixed-variant",
  subset: "border-tertiary-fixed-dim bg-tertiary-fixed/60 text-on-tertiary-fixed-variant",
  semantic_support: "border-tertiary-fixed-dim bg-tertiary-fixed/60 text-on-tertiary-fixed-variant",
  none: "border-primary-container/60 bg-transparent text-primary-container",
};

function reqCard(item) {
  const found = item.relation && item.relation !== "none";
  const rel = item.relation || "none";
  const label = found
    ? relationShort(item) + (item.evidence_location === "claimed" ? " · listed only" : "")
    : "Not found";
  const pts = `${item.points_earned ?? 0} / ${item.points ?? 0} pts`;
  const chip = `<span class="rounded-full border px-2 py-0.5 text-label-sm normal-case tracking-normal ${REL_CHIP[rel] || REL_CHIP.none}">${escapeHtml(label)}</span>`;
  if (!found) {
    return `
      <div class="rounded-xl border border-surface-container-high bg-surface-container-lowest px-space-md py-space-sm">
        <div class="flex items-center justify-between gap-space-sm">
          <span class="flex items-center gap-space-sm">
            <span class="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-primary-container"></span>
            <span class="text-body-md text-on-surface">${escapeHtml(item.name || "")}</span>${chip}
          </span>
          <span class="whitespace-nowrap font-mono text-body-sm text-primary-container">${pts}</span>
        </div>
        ${item.via ? `<p class="ml-4 mt-0.5 text-body-sm italic text-on-surface-variant">${escapeHtml(item.via)}</p>` : ""}
      </div>`;
  }
  return `
    <div class="rounded-xl border border-surface-container-high bg-surface-container-lowest p-space-md">
      <div class="mb-space-sm flex items-center justify-between gap-space-sm">
        <span class="flex items-center gap-space-sm"><span class="text-body-md text-on-surface">${escapeHtml(item.name || "")}</span>${chip}</span>
        <span class="whitespace-nowrap font-mono text-body-sm font-semibold text-on-surface">${pts}</span>
      </div>
      ${item.evidence ? `<blockquote class="rounded-lg border border-surface-container bg-surface-container-low/60 px-space-md py-space-sm font-serif text-body-sm italic leading-6 text-on-surface-variant">“${escapeHtml(String(item.evidence).slice(0, 260))}${String(item.evidence).length > 260 ? "…" : ""}”</blockquote>` : ""}
      ${item.via ? `<p class="mt-space-sm flex items-start gap-1 text-body-sm italic text-outline"><span class="ms text-[16px] not-italic">subdirectory_arrow_right</span><span><span class="not-italic">Reason:</span> ${escapeHtml(item.via)}</span></p>` : ""}
    </div>`;
}

function bucketSection(key, bucket) {
  const p = Math.round((bucket.score || 0) * 100);
  const w = Math.round((bucket.weight || 0) * 100);
  const items = bucket.items || [];
  const note = bucket.detail && bucket.detail.note;
  return `
    <section class="mb-space-lg">
      <div class="flex items-baseline justify-between gap-space-md">
        <h4 class="flex items-baseline gap-2 text-body-md font-semibold text-on-surface">
          ${escapeHtml(bucket.label || key)}
          <span class="text-body-sm normal-case tracking-normal ${p >= 60 ? "text-secondary" : "text-primary-container"}"><span class="text-outline-variant">•</span> ${p}% achieved</span>
        </h4>
        <span class="whitespace-nowrap font-mono text-body-sm text-on-surface-variant">Weight: ${w}%</span>
      </div>
      <div class="my-space-sm h-1.5 overflow-hidden rounded-full bg-surface-container-high">
        <div class="h-full rounded-full ${p >= 60 ? "bg-secondary" : "bg-primary-container"}" style="width:${p}%"></div>
      </div>
      ${note ? `<p class="mb-space-sm text-body-sm text-on-surface-variant">${escapeHtml(note)}</p>` : ""}
      <div class="flex flex-col gap-space-sm">${items.map(reqCard).join("")}</div>
    </section>`;
}

function renderRequirementsPane(breakdown) {
  if (!breakdown || !Object.keys(breakdown).length) {
    return `<p class="sa-empty">No score breakdown recorded for this CV.</p>`;
  }
  return Object.entries(breakdown).map(([k, b]) => bucketSection(k, b)).join("");
}

function engineWeights(breakdown) {
  return Object.entries(breakdown || {})
    .map(([k, b]) => `${BUCKET_SHORT[k] || b.label || k} ${Math.round((b.weight || 0) * 100)}%`)
    .join(" · ");
}

function chipList(items, cls) {
  return items.length
    ? items.map((s) => `<span class="rounded-full px-2.5 py-0.5 text-body-sm ${cls}">${escapeHtml(s)}</span>`).join("")
    : `<span class="text-body-sm italic text-on-surface-variant">None.</span>`;
}

function renderDocPane(tab, record, cvStatus) {
  const frame = (inner) => `<div class="flex h-full items-start justify-center overflow-auto p-space-lg">${inner}</div>`;
  if (tab === "tailored") {
    if (!record.tailored_download_url) {
      return frame(`<p class="mt-space-xl max-w-sm text-center text-body-md italic text-on-surface-variant">No tailored CV was generated for this job — its original score was already above the rewrite threshold.</p>`);
    }
    return `<iframe class="h-full w-full bg-surface-container-lowest" src="${API_BASE}${record.tailored_download_url}" title="Tailored CV"></iframe>`;
  }
  if (!cvStatus || !cvStatus.has_cv || !cvStatus.file_url) {
    return frame(`<p class="mt-space-xl text-body-md italic text-on-surface-variant">Original CV file not available.</p>`);
  }
  if (isPdfUrl(cvStatus.file_url)) {
    return `<iframe class="h-full w-full bg-surface-container-lowest" src="${API_BASE}${cvStatus.file_url}" title="Original CV"></iframe>`;
  }
  // .docx has no in-browser renderer -- show the parsed text with the matched
  // surface forms highlighted (the forms that matched, not the requirement
  // names: a subset match highlights "CNN", not "Deep Learning").
  const matched = isRequirementsRecord(record)
    ? requirementMatchedForms(record.ats_breakdown)
    : (record.ats_breakdown && record.ats_breakdown.keyword_match ? record.ats_breakdown.keyword_match.matched_skills : []);
  const excerpt = cvStatus.preview ? highlightMatches(cvStatus.preview, matched) : "No preview available.";
  return frame(`
    <div class="w-full max-w-xl bg-surface-container-lowest p-space-lg shadow-soft">
      <pre class="whitespace-pre-wrap font-sans text-body-sm text-on-surface">${excerpt}</pre>
      <p class="mt-space-md text-body-sm text-on-surface-variant">.docx files can't be rendered inline — showing the parsed text. <a class="text-primary underline" href="${API_BASE}${cvStatus.file_url}" target="_blank" rel="noopener">Download the file</a>.</p>
    </div>`);
}

let whyModal = null;
let activeRecord = null;
let activeCvStatus = null;
let activeTab = "original";

function ensureModal() {
  if (whyModal) return;
  // Pages built before this design shipped their own #why-modal markup;
  // replace it so there is exactly one dialog, this one.
  const legacy = document.getElementById("why-modal");
  if (legacy) legacy.remove();
  document.body.insertAdjacentHTML("beforeend", `
    <div id="why-modal" class="fixed inset-0 z-[70] flex items-center justify-center bg-inverse-surface/40 p-space-lg backdrop-blur-sm" hidden>
      <div class="flex h-[calc(100vh-3rem)] w-full max-w-[1320px] flex-col overflow-hidden rounded-3xl bg-surface shadow-2xl" role="dialog" aria-modal="true" aria-labelledby="why-score">
        <header class="flex items-center justify-between gap-space-lg px-space-lg py-space-md">
          <div class="flex min-w-0 items-center gap-space-md">
            <span class="flex h-14 w-14 flex-shrink-0 items-center justify-center rounded-xl bg-primary-fixed/60 text-[26px] font-bold text-primary" lang="ar" aria-hidden="true">س</span>
            <span class="text-[44px] font-bold leading-none tracking-tight text-on-surface" id="why-score">—</span>
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-space-sm" id="why-chips"></div>
              <p class="mt-1 truncate text-body-md text-on-surface-variant" id="why-role"></p>
            </div>
          </div>
          <div class="flex flex-shrink-0 items-center gap-space-md">
            <a id="why-action" class="inline-flex items-center gap-2 rounded-lg bg-primary-container px-4 py-2.5 text-label-lg text-on-primary shadow-md hover:bg-primary" target="_blank" rel="noopener" hidden>
              <span class="ms text-[18px]">auto_awesome</span>Open tailored CV</a>
            <a id="why-download" class="text-on-surface-variant hover:text-primary" title="Download this CV" download><span class="ms">download</span></a>
            <span class="h-6 w-px bg-surface-container-highest"></span>
            <button type="button" id="why-close" class="text-on-surface-variant hover:text-on-surface" aria-label="Close"><span class="ms text-[26px]">close</span></button>
          </div>
        </header>

        <div class="flex items-center justify-between gap-space-md border-y border-surface-container-high bg-surface-container-low px-space-lg">
          <div class="flex items-center gap-space-lg" id="why-tabs">
            <button type="button" data-tab="original" class="why-tab flex items-center gap-2 border-b-2 py-space-sm text-body-md">Original CV <span class="rounded bg-surface-container-high px-1.5 text-label-sm normal-case tracking-normal text-on-surface-variant">master</span></button>
            <button type="button" data-tab="tailored" class="why-tab flex items-center gap-2 border-b-2 py-space-sm text-body-md">Tailored CV <span class="rounded-full bg-emerald-50 px-2 text-label-sm normal-case tracking-normal text-emerald-800" id="why-tailored-chip"></span></button>
          </div>
          <div class="flex items-center gap-space-lg text-body-sm text-on-surface-variant">
            <span class="flex items-center gap-1.5"><span class="h-2.5 w-2.5 rounded-full bg-emerald-600"></span>Exact &amp; semantic evidence</span>
            <span class="flex items-center gap-1.5"><span class="h-2.5 w-2.5 rounded-full bg-primary-container"></span>Unmet job requirements</span>
          </div>
        </div>

        <div class="grid min-h-0 flex-1 grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
          <div class="flex min-h-0 flex-col border-r border-surface-container-high bg-surface-container-low/60">
            <div class="flex items-center justify-between border-b border-surface-container-high px-space-lg py-space-sm text-body-sm text-on-surface-variant">
              <span class="truncate" id="why-docname">—</span>
              <a id="why-open" class="flex items-center gap-1 hover:text-primary" target="_blank" rel="noopener"><span class="ms text-[18px]">open_in_new</span>Open</a>
            </div>
            <div class="min-h-0 flex-1" id="why-doc"></div>
          </div>
          <div class="flex min-h-0 flex-col">
            <div class="flex items-center justify-between gap-space-md border-b border-surface-container-high px-space-lg py-space-sm text-body-sm">
              <span class="text-on-surface-variant"><span class="font-semibold text-on-surface">Scored by:</span> <span id="why-engine"></span></span>
              <span class="text-on-surface-variant" id="why-weights"></span>
            </div>
            <div class="min-h-0 flex-1 overflow-y-auto px-space-lg pt-space-lg" id="why-body"></div>
            <div class="px-space-lg pb-space-lg pt-space-sm" id="why-banner"></div>
          </div>
        </div>
      </div>
    </div>`);
  whyModal = document.getElementById("why-modal");
  document.getElementById("why-close").addEventListener("click", closeWhyModal);
  whyModal.addEventListener("click", (e) => { if (e.target === whyModal) closeWhyModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && whyModal && !whyModal.hidden) closeWhyModal(); });
  whyModal.querySelectorAll(".why-tab").forEach((btn) => btn.addEventListener("click", () => {
    activeTab = btn.dataset.tab;
    renderWhyModal();
  }));
}

const RATING_CHIP = {
  excellent: ["Strong fit", "bg-emerald-50 text-emerald-800"],
  needs_work: ["Needs work", "bg-tertiary-fixed/70 text-on-tertiary-fixed-variant"],
  critical: ["Critical gap", "bg-error-container text-on-error-container"],
};

function renderWhyModal() {
  if (!activeRecord || !whyModal) return;
  const r = activeRecord;
  const tailored = activeTab === "tailored";
  const score = tailored ? r.tailored_ats_score : r.ats_score;
  const breakdown = tailored ? r.tailored_ats_breakdown : r.ats_breakdown;
  const requirements = isRequirementsRecord(r);

  whyModal.querySelectorAll(".why-tab").forEach((b) => {
    const on = b.dataset.tab === activeTab;
    b.classList.toggle("border-primary", on);
    b.classList.toggle("text-on-surface", on);
    b.classList.toggle("font-semibold", on);
    b.classList.toggle("border-transparent", !on);
    b.classList.toggle("text-on-surface-variant", !on);
  });
  const tChip = document.getElementById("why-tailored-chip");
  tChip.textContent = r.tailored_ats_score != null ? `${fmtPct(r.tailored_ats_score)} ATS` : "none";

  document.getElementById("why-score").textContent = fmtPct(score);
  const [ratingText, ratingCls] = RATING_CHIP[ratingKey(score)];
  document.getElementById("why-chips").innerHTML =
    `<span class="rounded-full px-3 py-0.5 text-body-sm font-medium ${ratingCls}">${ratingText}</span>`
    + (requirements ? "" : `<span class="rounded border border-tertiary-fixed-dim px-2 py-0.5 text-label-sm text-tertiary">Legacy engine</span>`);
  document.getElementById("why-role").innerHTML = r.title
    ? `Target role: <span class="text-on-surface">${escapeHtml(r.title)}${r.company ? ` · ${escapeHtml(r.company)}` : ""}</span>`
    : "ATS score breakdown";

  const action = document.getElementById("why-action");
  action.hidden = !r.tailored_download_url || tailored;
  if (r.tailored_download_url) action.href = `${API_BASE}${r.tailored_download_url}`;

  // Left pane: the document and its toolbar.
  const cv = activeCvStatus || {};
  const docUrl = tailored ? r.tailored_download_url : cv.file_url;
  document.getElementById("why-docname").textContent = tailored
    ? (r.tailored_download_url ? r.tailored_download_url.split("/").pop() : "No tailored CV")
    : (cv.filename || "Master CV");
  ["why-open", "why-download"].forEach((id) => {
    const a = document.getElementById(id);
    a.hidden = !docUrl;
    if (docUrl) a.href = `${API_BASE}${docUrl}`;
  });
  document.getElementById("why-doc").innerHTML = renderDocPane(activeTab, r, activeCvStatus);

  // Right pane: engine line, buckets, keyword summary.
  document.getElementById("why-engine").textContent = requirements ? "Requirement matcher" : "Four-pillar scorer (retired)";
  document.getElementById("why-weights").textContent = requirements && breakdown ? `Weights: ${engineWeights(breakdown)}` : "";
  const { matched, missing } = requirements ? requirementChips(breakdown)
    : { matched: (breakdown && breakdown.keyword_match && breakdown.keyword_match.matched_skills) || [],
        missing: (breakdown && breakdown.keyword_match && breakdown.keyword_match.missing_skills) || [] };
  document.getElementById("why-body").innerHTML =
    (requirements ? renderRequirementsPane(breakdown) : renderPillars(breakdown))
    + `<section class="mb-space-lg border-t border-surface-container-high pt-space-md">
         <h4 class="mb-space-sm text-label-md font-semibold text-on-surface-variant">Matched requirements</h4>
         <div class="mb-space-md flex flex-wrap gap-1.5">${chipList(matched, "bg-emerald-50 text-emerald-800")}</div>
         <h4 class="mb-space-sm text-label-md font-semibold text-on-surface-variant">Missing requirements</h4>
         <div class="flex flex-wrap gap-1.5">${chipList(missing, "bg-error-container/70 text-on-error-container")}</div>
       </section>`;
  document.getElementById("why-body").scrollTop = 0;

  // Bottom banner: what the other version of the CV scores.
  const banner = document.getElementById("why-banner");
  const o = r.ats_score, t = r.tailored_ats_score;
  if (!tailored && t != null && o != null) {
    const d = Math.round((t - o) * 100);
    banner.innerHTML = `
      <div class="flex items-center justify-between gap-space-md rounded-xl border border-surface-container-high bg-surface-container-lowest p-space-md shadow-soft">
        <div class="flex items-center gap-space-md">
          <span class="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg bg-tertiary-fixed/60 text-tertiary"><span class="ms">auto_awesome</span></span>
          <div>
            <p class="text-body-md font-semibold text-on-surface">The tailored CV ${d > 0 ? `recovers +${d} point${d === 1 ? "" : "s"}` : d < 0 ? `scores ${-d} point${d === -1 ? "" : "s"} lower` : "scores the same"} — ${fmtPct(t)} ATS</p>
            <p class="text-body-sm text-on-surface-variant">Reworded around this posting's requirements from your real experience, then re-scored against the same list.</p>
          </div>
        </div>
        <button type="button" class="whitespace-nowrap rounded-lg bg-inverse-surface px-4 py-2.5 text-label-lg text-inverse-on-surface hover:bg-on-surface" data-why-go="tailored">Review tailored CV →</button>
      </div>`;
  } else if (tailored && o != null) {
    banner.innerHTML = `
      <div class="flex items-center justify-between gap-space-md rounded-xl border border-surface-container-high bg-surface-container-lowest p-space-md shadow-soft">
        <p class="text-body-md text-on-surface">Your master CV scored <strong>${fmtPct(o)}</strong> against this posting.</p>
        <button type="button" class="whitespace-nowrap rounded-lg bg-surface-container-high px-4 py-2.5 text-label-lg text-on-surface hover:bg-surface-container-highest" data-why-go="original">← Back to original</button>
      </div>`;
  } else {
    banner.innerHTML = "";
  }
  const go = banner.querySelector("[data-why-go]");
  if (go) go.addEventListener("click", () => { activeTab = go.dataset.whyGo; renderWhyModal(); });
}

/** record: { scoring_engine, ats_score, ats_breakdown, tailored_ats_score,
 *            tailored_ats_breakdown, tailored_download_url, title?, company? } */
async function openWhyModal(record) {
  ensureModal();
  activeRecord = record;
  activeTab = "original";
  whyModal.hidden = false;
  document.body.style.overflow = "hidden";
  renderWhyModal(); // render now; the CV file details fill in below
  const thisRecord = record;
  activeCvStatus = await getCvStatus();
  if (activeRecord === thisRecord) renderWhyModal(); // a second open may have raced this fetch
}
window.openWhyModal = openWhyModal;

function closeWhyModal() {
  if (!whyModal) return;
  whyModal.hidden = true;
  document.getElementById("why-doc").innerHTML = ""; // stop the iframe loading in the background
  document.body.style.overflow = "";
}
window.closeWhyModal = closeWhyModal;
