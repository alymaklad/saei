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
  const replace = `<button type="button" class="${status.has_cv ? "sa-btn-ghost" : "sa-btn-primary"}" id="replace-cv">
      <span class="ms text-[18px]">upload_file</span>${status.has_cv ? "Replace" : "Upload CV"}</button>`;

  if (!status.has_cv) {
    el.innerHTML = `
      <h2 class="text-headline-sm text-on-surface">No CV yet</h2>
      <p class="mb-space-md mt-1 text-body-md text-on-surface-variant">Upload a PDF or Word file. Your profile is filled from it.</p>
      ${replace}`;
    bindReplace();
    return;
  }

  const skills = ((profile && profile.skills_claimed) || []).slice(0, 6);
  const updated = status.modified ? timeAgo(new Date(status.modified * 1000).toISOString()) : "";
  el.innerHTML = `
    <div class="flex flex-wrap items-center justify-between gap-space-md">
      <div class="flex min-w-0 items-center gap-space-md">
        <span class="sa-icon-badge"><span class="ms">description</span></span>
        <div class="min-w-0">
          <h2 class="truncate text-headline-sm text-on-surface">${escapeHtml(name || "Your CV")}</h2>
          <p class="truncate text-body-sm text-on-surface-variant">${escapeHtml(status.filename)}${updated ? ` · updated ${escapeHtml(updated)}` : ""}</p>
        </div>
      </div>
      <div class="flex flex-wrap items-center gap-space-sm">
        <a href="${API_BASE}${status.file_url}" target="_blank" rel="noopener" class="sa-btn"><span class="ms text-[18px]">visibility</span>View</a>
        ${replace}
      </div>
    </div>
    ${status.parse_error
      ? `<p class="save-error mt-space-md">Couldn't read this file: ${escapeHtml(status.parse_error)}</p>`
      : skills.length ? `<div class="mt-space-md flex flex-wrap gap-space-xs">${skills.map((k) => `<span class="sa-chip">${escapeHtml(k)}</span>`).join("")}</div>` : ""}`;
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

function scoreChange(r) {
  const o = pct(r.ats_score), t = pct(r.tailored_ats_score);
  if (t === null) return `<span class="text-body-sm text-on-surface-variant">${o !== null ? `${o}%` : "Not scored"}</span>`;
  if (o === null) return `<span class="text-body-md font-semibold text-on-surface">${t}%</span>`;
  const d = t - o;
  const chip = d > 0 ? "sa-chip-teal" : d < 0 ? "sa-chip-error" : "sa-chip";
  return `<span class="whitespace-nowrap text-body-md text-on-surface">${o}% <span class="ms align-middle text-[14px] text-on-surface-variant">arrow_forward</span> <strong>${t}%</strong></span>
    <span class="${chip}">${d > 0 ? "+" : ""}${d}</span>`;
}

function rewriteRow(r) {
  const compare = r.tailored_ats_breakdown || r.ats_breakdown
    ? `<button type="button" class="sa-btn-ghost sa-btn-sm" onclick="openWhyModalForRewrite(${r.application_id})">Compare</button>` : "";
  return `
    <li class="flex flex-wrap items-center gap-x-space-md gap-y-space-xs px-space-lg py-space-md">
      <div class="min-w-0 flex-1">
        <p class="truncate text-body-lg font-semibold text-on-surface">${escapeHtml(r.job_title || "Untitled role")}</p>
        <p class="truncate text-body-sm text-on-surface-variant">${[r.company, timeAgo(r.date_created)].filter(Boolean).map(escapeHtml).join(" · ")}</p>
      </div>
      <div class="flex items-center gap-space-sm" title="Score before and after tailoring">${scoreChange(r)}</div>
      <div class="flex items-center gap-space-xs">
        ${compare}
        <a href="${API_BASE}${r.download_url}" target="_blank" rel="noopener" class="sa-btn sa-btn-sm"><span class="ms text-[16px]">description</span>Open</a>
      </div>
    </li>`;
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
    body.innerHTML = `<li class="sa-empty">${allRewrites.length ? "Nothing matches that filter." : "None yet. Sa'ei tailors your CV for roles where it scores low."}</li>`;
    more.hidden = true;
    return;
  }
  const shown = expanded ? list : list.slice(0, PREVIEW);
  body.innerHTML = shown.map(rewriteRow).join("");
  more.hidden = shown.length >= list.length;
  more.textContent = `Show all ${list.length}`;
}

document.getElementById("cv-rewrites-more").addEventListener("click", () => { expanded = true; renderRewrites(); });
document.getElementById("cv-filter").addEventListener("input", renderRewrites);
document.getElementById("cv-sort").addEventListener("change", renderRewrites);

async function loadCvRewrites() {
  try {
    allRewrites = await getJSON("/api/cv/rewrites?limit=50");
    rewritesById = {};
    allRewrites.forEach((r) => { rewritesById[r.application_id] = r; });
    document.getElementById("cv-variant-chip").textContent = allRewrites.length ? `(${allRewrites.length})` : "";
    renderRewrites();
  } catch (e) {
    document.getElementById("cv-rewrites-body").innerHTML = `<li class="sa-empty">Couldn't reach Sa'ei.</li>`;
  }
}

// ---- test against a job ------------------------------------------------------
// The answer first (the scores and what's missing); the requirement-by-
// requirement evidence and the tailoring notes stay one click away.

const debugRunBtn = document.getElementById("debug-run");
const debugMessage = document.getElementById("debug-message");
const debugResults = document.getElementById("debug-results");

/** Every requirement across the breakdown's buckets, each with a verdict. */
function requirementsOf(breakdown) {
  const out = [];
  Object.values(breakdown || {}).forEach((bucket) => {
    ((bucket && bucket.items) || []).forEach((it) => {
      const earned = Number(it.points_earned) || 0, total = Number(it.points) || 0;
      const verdict = total && earned >= total ? "met" : earned > 0 ? "partly" : "missing";
      out.push({ ...it, verdict });
    });
  });
  return out;
}

function scoreTile(label, value, extra = "") {
  return `<div class="rounded-xl bg-surface-container-low px-space-md py-space-sm">
      <p class="text-label-md text-on-surface-variant">${label}</p>
      <p class="text-headline-md text-on-surface">${value}${extra}</p>
    </div>`;
}

const niceToHave = (it) => (it.importance === "preferred" ? ` <span class="font-normal opacity-70">(nice to have)</span>` : "");

function chipList(title, items, cls) {
  if (!items.length) return "";
  return `<div class="mt-space-md">
      <p class="mb-space-xs text-label-md font-semibold text-on-surface">${title}</p>
      <div class="flex flex-wrap gap-space-xs">${items.map((it) =>
        `<span class="${cls}">${escapeHtml(it.name)}${niceToHave(it)}</span>`).join("")}</div>
    </div>`;
}

const VERDICT_CHIP = { met: ["sa-chip-teal", "Met"], partly: ["sa-chip-amber", "Partly"], missing: ["sa-chip-error", "Missing"] };

function requirementTable(reqs) {
  return `<div class="overflow-x-auto"><table class="w-full text-left text-body-sm">
    <thead class="text-label-md text-on-surface-variant"><tr>
      <th class="py-2 pr-space-md font-semibold">Requirement</th><th class="py-2 pr-space-md"></th><th class="py-2 font-semibold">From your CV</th></tr></thead>
    <tbody class="divide-y divide-surface-container">${reqs.map((it) => {
      const [cls, label] = VERDICT_CHIP[it.verdict];
      return `<tr class="align-top">
        <td class="py-2 pr-space-md text-on-surface">${escapeHtml(it.name)}${niceToHave(it)}</td>
        <td class="py-2 pr-space-md"><span class="${cls}">${label}</span></td>
        <td class="py-2 text-on-surface-variant">${it.evidence ? escapeHtml(it.evidence) : "—"}</td></tr>`;
    }).join("")}</tbody></table></div>`;
}

function fold(title, body) {
  return `<details class="group mt-space-md rounded-xl bg-surface-container-low">
      <summary class="flex cursor-pointer list-none items-center justify-between px-space-md py-space-sm text-label-lg text-on-surface">
        ${title}<span class="ms text-on-surface-variant transition-transform group-open:rotate-180">expand_more</span></summary>
      <div class="px-space-md pb-space-md">${body}</div>
    </details>`;
}

function renderDebugResults(r) {
  const ats = r.ats || {};
  const rw = r.rewrite;
  const tailoredOnly = r.score_mode === "tailored_only";
  const before = tailoredOnly ? null : pct(ats.score);
  const after = rw ? pct(rw.tailored_score) : null;

  const tiles = [];
  if (r.compatibility) tiles.push(scoreTile("Readable by ATS", `${pct(r.compatibility.score)}%`));
  if (before !== null) tiles.push(scoreTile("Match", `${before}%`));
  if (after !== null) {
    const d = before !== null ? after - before : null;
    tiles.push(scoreTile(before !== null ? "After tailoring" : "Match", `${after}%`,
      d !== null ? ` <span class="${d >= 0 ? "sa-chip-teal" : "sa-chip-error"} align-middle">${d >= 0 ? "+" : ""}${d}</span>` : ""));
  }

  const reqs = requirementsOf(rw ? rw.tailored_breakdown : ats.breakdown);
  const firstRequired = (list) => [...list.filter((x) => x.importance !== "preferred"),
                                   ...list.filter((x) => x.importance === "preferred")];
  const missing = firstRequired(reqs.filter((x) => x.verdict === "missing"));
  const partly = firstRequired(reqs.filter((x) => x.verdict === "partly"));

  let html = `<div class="grid grid-cols-2 gap-space-sm sm:grid-cols-3">${tiles.join("")}</div>`;
  const issues = (r.compatibility && r.compatibility.issues) || [];
  if (issues.length) html += `<p class="mt-space-sm text-body-sm text-error">${issues.map(escapeHtml).join(" ")}</p>`;
  html += chipList("Missing", missing, "sa-chip-error");
  html += chipList("Partly there", partly, "sa-chip-amber");

  if (rw && rw.pdf_url) {
    html += `<div class="mt-space-lg"><a class="sa-btn-primary" href="${API_BASE}${rw.pdf_url}" target="_blank" rel="noopener"><span class="ms text-[18px]">description</span>Open tailored CV</a></div>`;
  } else if (!rw && r.rewrite_skipped_because) {
    html += `<p class="mt-space-md text-body-sm text-on-surface-variant">Not tailored: ${escapeHtml(r.rewrite_skipped_because)}.</p>`;
  }
  if (r.pdf_error) html += `<p class="save-error mt-space-sm">The PDF couldn't be made: ${escapeHtml(r.pdf_error)}</p>`;

  if (reqs.length) html += fold(`Every requirement (${reqs.length})`, requirementTable(reqs));

  if (rw) {
    const notes = [];
    if (rw.improvement) notes.push(escapeHtml(rw.improvement));
    (rw.integrity_warnings || []).forEach((w) => notes.push(escapeHtml(w)));
    if ((rw.listed_only || []).length) {
      notes.push(`Only in your skills list, so they count for less: ${rw.listed_only.map(escapeHtml).join(", ")}. Mention them in a role on your profile to count fully.`);
    }
    if (notes.length) html += fold("Tailoring notes", `<ul class="list-disc space-y-1 pl-5 text-body-sm text-on-surface-variant">${notes.map((n) => `<li>${n}</li>`).join("")}</ul>`);
    if (rw.tailored_cv) html += fold("Tailored CV text", `<pre class="max-h-96 overflow-auto whitespace-pre-wrap rounded-lg bg-surface-container-lowest p-space-md text-body-sm text-on-surface">${escapeHtml(rw.tailored_cv)}</pre>`);
  }

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
  debugMessage.textContent = "Scoring… this can take a minute.";
  debugMessage.className = "save-message";
  try {
    const result = await postJSON("/api/debug/ats", {
      job_description: jd,
      force_rewrite: document.getElementById("debug-force-rewrite").checked,
      render_pdf: document.getElementById("debug-render-pdf").checked,
    });
    debugMessage.textContent = "";
    debugMessage.className = "save-message";
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
