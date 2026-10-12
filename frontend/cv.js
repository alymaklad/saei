// CV page: the master CV (view / download / replace), every tailored CV with
// its before/after score, and the ATS & tailoring bench. Shared helpers come
// from config.js / ui.js / why-modal.js.

const fileInput = document.getElementById("cv-file-input");
const uploadMessage = document.getElementById("upload-message");

function fmtBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

let cvStatus = null;
let profile = null;

function paintMaster() {
  const el = document.getElementById("cv-current");
  const status = cvStatus;
  if (!status) return;
  const name = profile && profile.contact && profile.contact.full_name;
  const replace = `<button type="button" class="flex items-center gap-2 px-space-sm py-2 text-label-md text-on-surface-variant hover:text-primary" id="replace-cv">
      <span class="ms text-[18px]">upload_file</span>${status.has_cv ? "Replace CV" : "Upload CV"}</button>`;

  if (!status.has_cv) {
    el.innerHTML = `
      <h2 class="text-headline-md text-on-surface">No master CV yet</h2>
      <p class="mb-space-lg mt-1 text-body-md text-on-surface-variant">Upload a PDF or DOCX. Your profile is extracted from it automatically, and every score and tailored CV reads from that profile.</p>
      ${replace}`;
    bindReplace();
    return;
  }

  const summary = (profile && profile.summary) || status.preview || "";
  const skills = ((profile && profile.skills_claimed) || []).slice(0, 5);
  const updated = status.modified ? timeAgo(new Date(status.modified * 1000).toISOString()) : "—";
  el.innerHTML = `
    <div class="flex items-center gap-2">
      <h2 class="text-headline-md text-on-surface">${escapeHtml(name ? `${name} — Master CV` : "Master CV")}</h2>
      ${status.parse_error ? `<span class="ms text-[20px] text-error" title="Unreadable">error</span>`
                           : `<span class="ms text-[20px] text-primary" title="Parsed">verified</span>`}
    </div>
    <p class="mt-1 flex flex-wrap items-center gap-x-2 text-body-sm text-on-surface-variant">
      <span>Updated ${escapeHtml(updated)}</span><span>·</span>
      <span>File <code class="rounded bg-surface-container px-1.5 py-0.5">${escapeHtml(status.filename)}</code></span><span>·</span>
      <span>${fmtBytes(status.size_bytes)}</span>
    </p>
    ${status.parse_error
      ? `<p class="save-error mt-space-md">Could not read this file: ${escapeHtml(status.parse_error)}</p>`
      : `<p class="mt-space-md text-body-md text-on-surface">${escapeHtml(summary.slice(0, 420))}${summary.length > 420 ? "…" : ""}</p>`}
    ${skills.length ? `
      <p class="mb-space-sm mt-space-lg text-label-md font-semibold text-on-surface-variant">Key skills</p>
      <div class="flex flex-wrap gap-space-sm">${skills.map((s) => `<span class="rounded bg-surface-container px-2.5 py-1 text-body-md text-on-surface">${escapeHtml(s)}</span>`).join("")}</div>` : ""}
    <div class="mt-space-lg flex flex-wrap items-center gap-space-md">
      <a href="${API_BASE}${status.file_url}" target="_blank" rel="noopener" class="sa-btn bg-surface-container-lowest"><span class="ms text-[18px]">visibility</span>View CV</a>
      <a href="${API_BASE}${status.file_url}" download class="sa-btn-primary"><span class="ms text-[18px]">download</span>Download</a>
      ${replace}
    </div>`;
  bindReplace();
}

function bindReplace() {
  document.getElementById("replace-cv").addEventListener("click", () => fileInput.click());
}

async function loadMaster() {
  const [s, p] = await Promise.allSettled([getJSON("/api/cv/status"), getJSON("/api/profile")]);
  if (p.status === "fulfilled") profile = p.value.profile;
  if (s.status === "fulfilled") {
    cvStatus = s.value;
    paintMaster();
  } else {
    document.getElementById("cv-current").innerHTML = `<p class="sa-empty">Could not reach the backend API.</p>`;
  }
}

// ---- replace (upload) ------------------------------------------------------------

fileInput.addEventListener("change", async () => {
  const file = fileInput.files[0];
  if (!file) return;
  if (!/\.(pdf|docx)$/i.test(file.name)) {
    uploadMessage.textContent = "Only .pdf or .docx files are supported.";
    uploadMessage.className = "save-message save-error relative mt-space-sm";
    fileInput.value = "";
    return;
  }
  // Replacing the CV also re-extracts the profile, overwriting hand edits --
  // worth one explicit confirmation.
  if (cvStatus && cvStatus.has_cv && !confirm(
      `Replace ${cvStatus.filename} with ${file.name}?\n\nYour profile is re-extracted from the new file, which overwrites any hand edits on the Profile page.`)) {
    fileInput.value = "";
    return;
  }
  uploadMessage.textContent = `Uploading ${file.name}…`;
  uploadMessage.className = "save-message relative mt-space-sm";
  const formData = new FormData();
  formData.append("file", file);
  try {
    const res = await fetch(`${API_BASE}/api/cv/upload`, { method: "POST", body: formData });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `Upload failed (${res.status})`);
    if (data.profile_extracted) {
      uploadMessage.innerHTML = 'Uploaded, and your profile was filled from it. <a href="profile.html">Review it</a>.';
      try { sessionStorage.removeItem("saei.sidebarUser"); } catch (e) { /* ignore */ }
    } else if (data.profile_error) {
      uploadMessage.innerHTML = 'Uploaded, but your profile could not be read from it: '
        + `${escapeHtml(data.profile_error)} Open the <a href="profile.html">Profile page</a> and press "Re-extract from CV" to try again.`;
    } else {
      uploadMessage.textContent = "Uploaded.";
    }
    uploadMessage.className = "save-message save-success relative mt-space-sm";
    await loadMaster();
  } catch (e) {
    uploadMessage.textContent = e.message || "Upload failed.";
    uploadMessage.className = "save-message save-error relative mt-space-sm";
  } finally {
    fileInput.value = "";
  }
});

// ---- "Why?" / compare: opens the shared modal (why-modal.js) --------------------

let rewritesById = {};

function openWhyModalForRewrite(applicationId) {
  const r = rewritesById[applicationId];
  if (!r) return;
  openWhyModal({
    scoring_engine: r.scoring_engine,
    ats_score: r.ats_score,
    ats_breakdown: r.ats_breakdown,
    tailored_ats_score: r.tailored_ats_score,
    tailored_ats_breakdown: r.tailored_ats_breakdown,
    tailored_download_url: r.download_url,
    title: r.job_title,
    company: r.company,
  });
}
window.openWhyModalForRewrite = openWhyModalForRewrite;

// ---- tailored CVs ----------------------------------------------------------------------

const PREVIEW = 6;
let allRewrites = [];
let expanded = false;

function liftBar(r) {
  const o = pct(r.ats_score), t = pct(r.tailored_ats_score);
  if (o === null || t === null) {
    return `<p class="text-body-sm text-on-surface-variant">${t !== null ? `Tailored ${t}% ATS` : "Not re-scored"}</p>`;
  }
  const lo = Math.min(o, t), hi = Math.max(o, t);
  const up = t >= o;
  return `
    <div class="flex items-center justify-between text-body-sm">
      <span class="text-on-surface">Original ${o}%</span>
      <span class="ms text-[16px] text-on-surface-variant">arrow_forward</span>
      <span class="${up ? "text-secondary" : "text-error"}">Tailored <strong>${t}%</strong> ATS</span>
    </div>
    <div class="relative my-1.5 h-1.5 overflow-hidden rounded-full bg-surface-container-highest">
      <div class="absolute inset-y-0 left-0 bg-secondary-fixed-dim" style="width:${lo}%"></div>
      <div class="absolute inset-y-0 ${up ? "bg-secondary" : "bg-error"}" style="left:${lo}%;width:${hi - lo}%"></div>
    </div>
    <div class="flex items-center justify-between text-label-sm normal-case tracking-normal text-on-surface-variant">
      <span></span><span class="${up ? "text-secondary" : "text-error"}">${up ? "+" : ""}${t - o} points</span>
    </div>`;
}

function rewriteRow(r) {
  return `
    <div class="grid grid-cols-[1.2fr_1.2fr_1fr] items-center gap-space-md py-space-md">
      <div class="flex min-w-0 items-center gap-space-md">
        <span class="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg bg-surface-container text-primary"><span class="ms">apartment</span></span>
        <div class="min-w-0">
          <p class="truncate text-headline-sm text-on-surface">${escapeHtml(r.job_title || "Untitled role")}</p>
          <p class="truncate text-body-sm text-on-surface-variant">${escapeHtml(r.company || "Company not listed")}</p>
        </div>
      </div>
      <div>${liftBar(r)}</div>
      <div class="flex items-center justify-end gap-space-md">
        <span class="whitespace-nowrap text-body-sm text-on-surface-variant">${escapeHtml(timeAgo(r.date_created))}</span>
        ${r.tailored_ats_breakdown || r.ats_breakdown
          ? `<button type="button" class="text-label-md text-on-surface-variant hover:text-primary" onclick="openWhyModalForRewrite(${r.application_id})" title="Compare the two scores requirement by requirement">Compare</button>` : ""}
        <a href="${API_BASE}${r.download_url}" target="_blank" rel="noopener" class="flex items-center gap-1 whitespace-nowrap text-label-md text-primary hover:underline">View tailored CV <span class="ms text-[18px]">arrow_forward</span></a>
      </div>
    </div>`;
}

function sortedFiltered() {
  const q = document.getElementById("cv-filter").value.trim().toLowerCase();
  let list = allRewrites.filter((r) => !q
    || (r.job_title || "").toLowerCase().includes(q) || (r.company || "").toLowerCase().includes(q));
  const mode = document.getElementById("cv-sort").value;
  const lift = (r) => (r.ats_score != null && r.tailored_ats_score != null) ? r.tailored_ats_score - r.ats_score : -Infinity;
  if (mode === "lift") list = [...list].sort((a, b) => lift(b) - lift(a));
  else if (mode === "score") list = [...list].sort((a, b) => (b.tailored_ats_score ?? -1) - (a.tailored_ats_score ?? -1));
  else list = [...list].sort((a, b) => new Date(b.date_created || 0) - new Date(a.date_created || 0));
  return list;
}

function renderRewrites() {
  const body = document.getElementById("cv-rewrites-body");
  const more = document.getElementById("cv-rewrites-more");
  const list = sortedFiltered();
  if (!list.length) {
    body.innerHTML = `<p class="sa-empty">${allRewrites.length ? "No matches." : "None yet — generated when a job's ATS score is below the fit threshold."}</p>`;
    more.hidden = true;
    return;
  }
  const shown = expanded ? list : list.slice(0, PREVIEW);
  body.innerHTML = shown.map(rewriteRow).join("");
  more.hidden = shown.length >= list.length;
  more.textContent = `Show all ${list.length} →`;
}

document.getElementById("cv-rewrites-more").addEventListener("click", () => { expanded = true; renderRewrites(); });
document.getElementById("cv-filter").addEventListener("input", renderRewrites);
document.getElementById("cv-sort").addEventListener("change", renderRewrites);

async function loadCvRewrites() {
  try {
    allRewrites = await getJSON("/api/cv/rewrites?limit=50");
    rewritesById = {};
    allRewrites.forEach((r) => { rewritesById[r.application_id] = r; });
    document.getElementById("cv-variant-chip").textContent = `${allRewrites.length} total`;
    renderRewrites();
  } catch (e) {
    document.getElementById("cv-rewrites-body").innerHTML = `<p class="sa-empty">Could not reach the backend API.</p>`;
  }
}

// ---- ATS & tailoring debug bench -------------------------------------------
// Reuses why-modal.js's pillar labels/ordering so a score shown here reads
// exactly like the same score shown in the Why? modal -- two renderings of
// one number that disagreed cosmetically would undermine the whole point.

const debugRunBtn = document.getElementById("debug-run");
const debugMessage = document.getElementById("debug-message");
const debugResults = document.getElementById("debug-results");

// Bucket order for the requirements engine. Kept in sync with
// config.JOB_MATCH_WEIGHTS by hand; a bucket missing from here still renders,
// it just sorts last.
const BUCKET_ORDER = [
  "required_skills", "experience", "preferred_skills",
  "responsibilities", "education", "soft_skills",
];

// Two independent axes: how the CV term relates to the requirement, and where
// the evidence sits. The relation label comes from the backend so the two
// never drift apart. "Semantic support" rather than "Equivalent" is
// deliberate -- CNN supports a Deep Learning requirement without being
// another word for it, and a label saying "Equivalent" beside a partial score
// contradicts the score.
const MATCH_CLASS_LABEL = {
  explicit: "Demonstrated",
  claimed: "Listed",
  implied: "Prerequisite",
  subset: "Subset",
  semantic_support: "Semantic support",
  missing: "Not found",
};

function debugPillars(breakdown) {
  // Handles both engines. The requirements engine names its own buckets and
  // ships a `label`; the legacy engine's four pillars come from why-modal.js
  // so a score shown here reads exactly like the same score in the Why? modal.
  const isRequirements = Object.values(breakdown).some((d) => d && d.label);
  const order = isRequirements
    ? BUCKET_ORDER.filter((k) => breakdown[k]).concat(
        Object.keys(breakdown).filter((k) => !BUCKET_ORDER.includes(k)))
    : PILLAR_ORDER.filter((k) => breakdown[k]);

  return order.map((k) => {
    const d = breakdown[k];
    const pct = Math.round((d.score || 0) * 100);
    const name = d.label || PILLAR_LABELS[k] || k;
    // A redistributed weight differs from the configured one, and hiding that
    // would make the arithmetic impossible to follow.
    const reweighted = d.base_weight && Math.abs(d.base_weight - d.weight) > 0.001
      ? ` (base ${Math.round(d.base_weight * 100)}%)` : "";
    const evidence = isRequirements
      ? (d.detail && d.detail.note ? d.detail.note : "")
      : pillarEvidence(k, d);
    return `
      <div class="score-pillar">
        <div class="score-pillar-head">
          <span class="score-pillar-name">${escapeHtml(name)}</span>
          <span class="score-pillar-meta">${pct}% &middot; weight ${Math.round(d.weight * 100)}%${reweighted}</span>
        </div>
        <div class="score-bar-track"><div class="score-bar-fill ${fillClass(d.score)}" style="width:${pct}%"></div></div>
        ${evidence ? `<div class="score-pillar-evidence">${escapeHtml(evidence)}</div>` : ""}
        ${d.items && d.items.length ? requirementRows(d.items) : ""}
      </div>`;
  }).join("");
}

function requirementRows(items) {
  // Every point awarded traces to a line of the CV. This table is the whole
  // argument for the requirements engine: a keyword count can only tell you
  // "9 of 26", never which nine or on what evidence.
  const rows = items.map((it) => {
    const relation = it.relation || (it.match === "missing" ? "none" : "exact");
    const relLabel = it.relation_label || MATCH_CLASS_LABEL[it.match] || it.match;
    // Two chips, because these vary independently: an exact term buried in a
    // skills list is weaker evidence than a subset term inside a dated role,
    // and one combined label can't say that.
    const evidenceChip = it.evidence_location === "claimed"
      ? `<span class="req-match req-loc-claimed">Listed only</span>` : "";
    return `
    <tr class="req-${escapeHtml(relation)}">
      <td>${escapeHtml(it.name)}</td>
      <td><span class="req-tag req-${escapeHtml(it.importance)}">${escapeHtml(it.importance)}</span></td>
      <td>${escapeHtml((it.category || "").replace(/_/g, " "))}</td>
      <td><span class="req-match req-rel-${escapeHtml(relation)}">${escapeHtml(relLabel)}</span>${evidenceChip}</td>
      <td class="req-pts">${it.points_earned}/${it.points}</td>
      <td class="req-evidence">${it.evidence ? escapeHtml(it.evidence) : "&mdash;"}${
        it.via ? `<span class="req-via">${escapeHtml(it.via)}</span>` : ""}</td>
    </tr>`;
  }).join("");
  return `<div class="req-table-wrap"><table class="req-table">
    <thead><tr><th>Requirement</th><th></th><th>Type</th><th>How it matched</th><th>Pts</th><th>Evidence in your CV</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

function renderCompatibility(c) {
  if (!c) return "";
  const key = c.status === "Pass" ? "good" : (c.status === "Warning" ? "mid" : "bad");
  const issues = (c.issues || []).length
    ? `<ul class="compat-issues">${c.issues.map((i) => `<li>${escapeHtml(i)}</li>`).join("")}</ul>`
    : `<p class="card-sub">No parsing problems detected.</p>`;
  return `
    <div class="debug-block">
      <div class="debug-headline">
        <span class="compat-status compat-${key}">${escapeHtml(c.status)}</span>
        <span class="debug-score-small">${Math.round(c.score * 100)}%</span>
        <span class="debug-sub">ATS compatibility &mdash; can a parser read this CV? Depends only on the CV, not on any job, so it is reported separately rather than mixed into the match score.</span>
      </div>
      ${issues}
    </div>`;
}

function renderDebugResults(r) {
  const ats = r.ats;
  const rw = r.rewrite;
  const engineName = "Job match (requirement matching)";

  // tailored_only: one number was asked for, so the untailored score is not
  // rendered at all and the tailored block below drops its "up from X" line.
  const tailoredOnly = r.score_mode === "tailored_only";

  let html = renderCompatibility(r.compatibility);

  if (!tailoredOnly) {
    html += `
    <div class="debug-block">
      <div class="debug-headline">
        <span class="debug-score">${Math.round(ats.score * 100)}%</span>
        <span class="rating-badge rating-${ratingKey(ats.score)}">${escapeHtml(ats.rating)}</span>
        <span class="debug-sub">${escapeHtml(engineName)} &middot; untailored &middot; scored from your ${escapeHtml(r.profile_source || "profile")} &middot; fit threshold ${Math.round(r.fit_threshold * 100)}%${
          ats.cv_years != null ? ` &middot; ${ats.cv_years} yrs professional experience from dated entries` : ""}</span>
      </div>
      <div class="score-pillars">${debugPillars(ats.breakdown)}</div>
      ${(ats.inactive_buckets || []).length ? `<p class="card-sub">Not applicable to this posting, weight redistributed: ${
        ats.inactive_buckets.map((b) => escapeHtml(b.replace(/_/g, " "))).join(", ")}.</p>` : ""}
    </div>`;
  }

  if (rw) {
    const delta = rw.delta;
    const arrow = delta >= 0 ? "&uarr;" : "&darr;";
    html += `
      <div class="debug-block">
        <div class="debug-headline">
          <span class="debug-score">${Math.round(rw.tailored_score * 100)}%</span>
          <span class="rating-badge rating-${ratingKey(rw.tailored_score)}">${escapeHtml(rw.tailored_rating)}</span>
          <span class="debug-sub ${delta >= 0 ? "delta-up" : "delta-down"}">
            ${tailoredOnly ? escapeHtml(rw.triggered_by)
                           : `${arrow} ${Math.abs(Math.round(delta * 100))} points after tailoring &middot; ${escapeHtml(rw.triggered_by)}`}
          </span>
        </div>
        <div class="score-pillars">${debugPillars(rw.tailored_breakdown)}</div>
        <div class="debug-why">${escapeHtml(rw.improvement)}</div>
        ${(rw.integrity_warnings || []).length ? `
        <div class="debug-warnings">
          <div class="debug-warnings-title">Checks that rejected something</div>
          <ul>${rw.integrity_warnings.map((w) => `<li>${escapeHtml(w)}</li>`).join("")}</ul>
        </div>` : ""}
        ${rw.target_coverage && (rw.target_coverage.used || []).length + (rw.target_coverage.missed || []).length ? `
        <div class="debug-targets">
          <div class="debug-warnings-title">The job's wording</div>
          <p class="card-sub">Terms this CV already earned, rewritten in the words the posting uses.</p>
          ${(rw.target_coverage.used || []).length ? `<p><span class="target-yes">Used</span> ${rw.target_coverage.used.map(escapeHtml).join(", ")}</p>` : ""}
          ${(rw.target_coverage.missed || []).length ? `<p><span class="target-no">Not used</span> ${rw.target_coverage.missed.map(escapeHtml).join(", ")}</p>` : ""}
        </div>` : ""}
        ${(rw.listed_only || []).length ? `
        <div class="debug-targets">
          <div class="debug-warnings-title">Only in your skills list</div>
          <p class="card-sub">These earn partial credit because no role or project on your profile mentions them. If you used one in a specific role, adding it there on the Profile page is worth the difference.</p>
          <p>${rw.listed_only.map(escapeHtml).join(", ")}</p>
        </div>` : ""}
        ${rw.pdf_url ? `<p><a class="btn" href="${API_BASE}${rw.pdf_url}" target="_blank" rel="noopener">Open rendered PDF</a></p>` : ""}
        <div class="debug-sub" style="margin-top:14px">Tailored CV text (${rw.tailored_chars.toLocaleString()} chars)</div>
        <pre class="debug-cv">${escapeHtml(rw.tailored_cv)}</pre>
      </div>`;
  } else if (r.rewrite_skipped_because) {
    html += `<div class="debug-block"><p class="card-sub">Rewrite skipped — ${escapeHtml(r.rewrite_skipped_because)}.</p></div>`;
  }

  const t = r.timings || {};
  html += `<p class="debug-sub">Timings: ${Object.entries(t).map(([k, v]) => `${escapeHtml(k)} ${v}s`).join(" &middot; ")} &middot; LLM: ${escapeHtml(r.llm_provider)}${
    ats.cv_profile_cached ? " &middot; CV profile served from cache" : ""}</p>`;
  if (r.pdf_error) html += `<p class="save-error">PDF render failed: ${escapeHtml(r.pdf_error)}</p>`;

  debugResults.innerHTML = html;
  debugResults.hidden = false;
}

debugRunBtn.addEventListener("click", async () => {
  const jd = document.getElementById("debug-jd").value.trim();
  if (!jd) {
    debugMessage.textContent = "Paste a job description first.";
    debugMessage.className = "save-message save-error";
    return;
  }
  debugRunBtn.disabled = true;
  debugResults.hidden = true;
  // A local model can take a while, and the request blocks -- say so rather
  // than leaving a dead-looking button.
  debugMessage.textContent = "Running… this makes real LLM calls and can take a minute on a local model.";
  debugMessage.className = "save-message";
  try {
    const result = await postJSON("/api/debug/ats", {
      job_description: jd,
      force_rewrite: document.getElementById("debug-force-rewrite").checked,
      render_pdf: document.getElementById("debug-render-pdf").checked,
    });
    debugMessage.textContent = "Done.";
    debugMessage.className = "save-message save-success";
    renderDebugResults(result);
  } catch (err) {
    debugMessage.textContent = err.message || "Run failed.";
    debugMessage.className = "save-message save-error";
  } finally {
    debugRunBtn.disabled = false;
  }
});



loadMaster();
loadCvRewrites();
