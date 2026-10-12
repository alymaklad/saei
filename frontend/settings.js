const providerSelect = document.getElementById("llm-provider");
const ollamaFields = document.getElementById("ollama-fields");
const geminiFields = document.getElementById("gemini-fields");
const groqFields = document.getElementById("groq-fields");
const openrouterFields = document.getElementById("openrouter-fields");
const geminiKeyStatus = document.getElementById("gemini-key-status");
const groqKeyStatus = document.getElementById("groq-key-status");
const openrouterKeyStatus = document.getElementById("openrouter-key-status");
const saveMessage = document.getElementById("save-message");

function updateVisibleFields() {
  const provider = providerSelect.value;
  ollamaFields.style.display = provider === "ollama" ? "" : "none";
  geminiFields.style.display = provider === "gemini" ? "" : "none";
  groqFields.style.display = provider === "groq" ? "" : "none";
  openrouterFields.style.display = provider === "openrouter" ? "" : "none";
}

providerSelect.addEventListener("change", updateVisibleFields);

const checkIcon = `<svg viewBox="0 0 24 24" style="width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:2"><path d="M20 6 9 17l-5-5"/></svg>`;

function setKeyStatus(el, isSet, label) {
  el.classList.toggle("field-note-muted", !isSet);
  el.innerHTML = isSet ? `${checkIcon}${label}` : label;
}

document.querySelectorAll(".password-toggle").forEach((btn) => {
  btn.addEventListener("click", () => {
    const input = document.getElementById(btn.dataset.target);
    input.type = input.type === "password" ? "text" : "password";
  });
});

// ---- Groq model: dropdown of known ids, plus a free-text escape hatch ------
//
// Groq adds and retires models faster than this list gets updated, so the
// dropdown can't be the only way in. Same "Custom…" pattern as the embedding
// model control further down: a real <select> keeps the options discoverable
// (an <input list=datalist> renders as a bare text box with no chevron, which
// is why that approach was abandoned there), and the text input appears only
// when it's actually needed.

const groqModelSelect = document.getElementById("groq-model");
const groqModelCustom = document.getElementById("groq-model-custom");

function syncGroqCustomVisibility() {
  const custom = groqModelSelect.value === "__custom__";
  groqModelCustom.hidden = !custom;
  if (custom) groqModelCustom.focus();
}

/** The model id to save: the typed one when Custom is selected, otherwise the
 *  dropdown's. Never returns the "__custom__" sentinel -- that's a frontend
 *  detail and writing it to GROQ_MODEL would break every LLM call with a
 *  404 that gives no clue where the bad value came from. */
function groqModelValue() {
  if (groqModelSelect.value !== "__custom__") return groqModelSelect.value || null;
  return groqModelCustom.value.trim() || null;
}

groqModelSelect.addEventListener("change", syncGroqCustomVisibility);

async function loadSettings() {
  try {
    const s = await getJSON("/api/settings");
    providerSelect.value = s.llm_provider;
    document.getElementById("ollama-model").value = s.ollama_model || "";
    document.getElementById("ollama-base-url").value = s.ollama_base_url || "";

    if (s.groq_model && ![...groqModelSelect.options].some((o) => o.value === s.groq_model)) {
      // Saved value isn't one of the current options (e.g. Groq retired it
      // since this list was written, or it was typed into the Custom field
      // on a previous visit) -- add it rather than silently switching the
      // dropdown to something the user didn't choose. Inserted BEFORE the
      // "Custom…" entry so that entry stays last.
      const opt = document.createElement("option");
      opt.value = s.groq_model;
      opt.textContent = `${s.groq_model} (currently saved, not in the known list)`;
      groqModelSelect.insertBefore(opt, groqModelSelect.lastElementChild);
    }
    groqModelSelect.value = s.groq_model || "openai/gpt-oss-120b";
    syncGroqCustomVisibility();
    document.getElementById("openrouter-model").value = s.openrouter_model || "";
    setKeyStatus(geminiKeyStatus, s.gemini_api_key_set, s.gemini_api_key_set ? "A Gemini API key is currently saved." : "No Gemini API key saved yet.");
    setKeyStatus(groqKeyStatus, s.groq_api_key_set, s.groq_api_key_set ? "A Groq API key is currently saved." : "No Groq API key saved yet.");
    setKeyStatus(openrouterKeyStatus, s.openrouter_api_key_set, s.openrouter_api_key_set ? "An OpenRouter API key is currently saved." : "No OpenRouter API key saved yet.");
    updateVisibleFields();
    syncProviderCards();
  } catch (e) {
    saveMessage.textContent = "Could not reach the backend API.";
    saveMessage.className = "save-message save-error";
  }
}

document.getElementById("settings-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  // Catch the empty Custom box here rather than posting null and letting the
  // backend's "blank means keep the current value" rule quietly save nothing
  // -- the user would see "Saved." and the old model still in use.
  if (groqModelSelect.value === "__custom__" && !groqModelCustom.value.trim()) {
    saveMessage.textContent = "Type a Groq model id, or pick one from the list.";
    saveMessage.className = "save-message save-error";
    groqModelCustom.focus();
    return;
  }

  saveMessage.textContent = "Saving…";
  saveMessage.className = "save-message";

  const payload = {
    llm_provider: providerSelect.value,
    ollama_model: document.getElementById("ollama-model").value || null,
    ollama_base_url: document.getElementById("ollama-base-url").value || null,
    gemini_api_key: document.getElementById("gemini-api-key").value || null,
    groq_api_key: document.getElementById("groq-api-key").value || null,
    groq_model: groqModelValue(),
    openrouter_api_key: document.getElementById("openrouter-api-key").value || null,
    openrouter_model: document.getElementById("openrouter-model").value || null,
  };

  try {
    const result = await postJSON("/api/settings", payload);
    saveMessage.textContent = "Saved.";
    saveMessage.className = "save-message save-success";
    document.getElementById("gemini-api-key").value = "";
    document.getElementById("groq-api-key").value = "";
    document.getElementById("openrouter-api-key").value = "";
    // Fold a newly typed model into the dropdown and select it, so the control
    // reflects what's actually saved instead of sitting on "Custom…" with a
    // stray text box open. Next reload it comes back from the server anyway.
    if (result.groq_model && ![...groqModelSelect.options].some((o) => o.value === result.groq_model)) {
      const opt = document.createElement("option");
      opt.value = result.groq_model;
      opt.textContent = `${result.groq_model} (currently saved, not in the known list)`;
      groqModelSelect.insertBefore(opt, groqModelSelect.lastElementChild);
    }
    if (result.groq_model) groqModelSelect.value = result.groq_model;
    groqModelCustom.value = "";
    syncGroqCustomVisibility();
    loadOllamaStatus();   // same staleness issue as the matching panel above
    setKeyStatus(geminiKeyStatus, result.gemini_api_key_set, result.gemini_api_key_set ? "A Gemini API key is currently saved." : "No Gemini API key saved yet.");
    setKeyStatus(groqKeyStatus, result.groq_api_key_set, result.groq_api_key_set ? "A Groq API key is currently saved." : "No Groq API key saved yet.");
    setKeyStatus(openrouterKeyStatus, result.openrouter_api_key_set, result.openrouter_api_key_set ? "An OpenRouter API key is currently saved." : "No OpenRouter API key saved yet.");
  } catch (err) {
    saveMessage.textContent = err.message || "Could not save settings.";
    saveMessage.className = "save-message save-error";
  }
});

// ---- Ollama preflight ------------------------------------------------------
// Reports whether the daemon is up and the configured models are pulled, so a
// missing model is a visible answer here rather than a connection error deep
// inside a search run.

const ollamaStatusEl = document.getElementById("ollama-status");
const crossIcon = `<svg viewBox="0 0 24 24" style="width:14px;height:14px;stroke:currentColor;fill:none;stroke-width:2"><path d="M18 6 6 18M6 6l12 12"/></svg>`;

async function loadOllamaStatus() {
  if (!ollamaStatusEl) return;
  try {
    const s = await getJSON("/api/ollama/status");
    if (!s.reachable) {
      ollamaStatusEl.className = "field-note save-error";
      ollamaStatusEl.innerHTML = `${crossIcon}Can't reach Ollama at ${escapeHtml(s.base_url)} — is \`ollama serve\` running?`;
      return;
    }
    const missing = [];
    if (!s.llm_model_present) missing.push(s.llm_model);
    if (!s.embedding_model_present) missing.push(s.embedding_model);
    if (missing.length) {
      ollamaStatusEl.className = "field-note save-error";
      ollamaStatusEl.innerHTML = `${crossIcon}Ollama is running, but not pulled yet: ` +
        missing.map((m) => `<code>ollama pull ${escapeHtml(m)}</code>`).join(" &middot; ");
    } else {
      ollamaStatusEl.className = "field-note";
      ollamaStatusEl.innerHTML = `${checkIcon}Ollama running — ${escapeHtml(s.llm_model)} (chat) and ${escapeHtml(s.embedding_model)} (embeddings) are both installed.`;
    }
  } catch (e) {
    ollamaStatusEl.className = "field-note field-note-muted";
    ollamaStatusEl.textContent = "Could not check Ollama status.";
  }
}

loadSettings();
loadOllamaStatus();

// ---- Auto-Apply Behavior panel (separate form, separate endpoint) ---------

const autoApplyModeSelect = document.getElementById("auto-apply-mode");
const fitThresholdInput = document.getElementById("fit-threshold");
const tailoredScoreToggle = document.getElementById("toggle-auto-apply-tailored");
const emailApplyToggle = document.getElementById("toggle-auto-apply-email");
const autoApplySaveMessage = document.getElementById("auto-apply-save-message");
const atsScoreModeSelect = document.getElementById("ats-score-mode");
const atsScoreModeDesc = document.getElementById("ats-score-mode-desc");

const ATS_SCORE_MODE_DESC = {
  both: "Every job is scored as your CV stands. Only the ones below the fit "
      + "threshold get tailored, and you see both numbers with the difference.",
  tailored_only: "You see one number: what the tailored CV scores. Every job is "
      + "tailored, since there is no baseline score left to decide which are "
      + "worth it — that is one model call per job.",
};

function updateAtsScoreModeDesc() {
  atsScoreModeDesc.textContent = ATS_SCORE_MODE_DESC[atsScoreModeSelect.value] || "";
  // The threshold only gates anything when there is a baseline score to
  // compare against it.
  const gated = atsScoreModeSelect.value !== "tailored_only";
  fitThresholdInput.disabled = !gated;
  fitThresholdInput.closest(".form-field").classList.toggle("field-disabled", !gated);
}

atsScoreModeSelect.addEventListener("change", updateAtsScoreModeDesc);
const autoApplyModeDesc = document.getElementById("auto-apply-mode-desc");

let lastKnownWhitelistedSources = [];

function updateAutoApplyModeDesc() {
  const mode = autoApplyModeSelect.value;
  if (mode === "off") {
    autoApplyModeDesc.textContent = "Every job drafts for review, with its CV and cover letter ready. Nothing is sent.";
  } else if (mode === "any") {
    autoApplyModeDesc.textContent = "Applies wherever a job clears your threshold. Email applications are sent when the email switch is on; jobs on sites with application forms become drafts, since no site has a form submitter yet.";
  } else {
    autoApplyModeDesc.textContent = lastKnownWhitelistedSources.length
      ? `Only these whitelisted sources auto-submit: ${lastKnownWhitelistedSources.join(", ")}. Everything else drafts for review.`
      : "Only whitelisted sources auto-submit — none are whitelisted yet, so every job currently drafts for review.";
  }
}

autoApplyModeSelect.addEventListener("change", updateAutoApplyModeDesc);

async function loadAutoApplySettings() {
  try {
    const s = await getJSON("/api/settings/auto-apply");
    autoApplyModeSelect.value = s.auto_apply_mode;
    fitThresholdInput.value = Math.round(s.fit_threshold * 100);
    tailoredScoreToggle.checked = s.auto_apply_on_tailored_score;
    emailApplyToggle.checked = !!s.auto_apply_email;
    atsScoreModeSelect.value = s.ats_score_mode || "both";
    lastKnownWhitelistedSources = s.whitelisted_sources;
    updateAutoApplyModeDesc();
    updateAtsScoreModeDesc();
    syncModeCards();
    syncFitRange();
  } catch (e) {
    autoApplySaveMessage.textContent = "Could not reach the backend API.";
    autoApplySaveMessage.className = "save-message save-error";
  }
}

document.getElementById("auto-apply-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  autoApplySaveMessage.textContent = "Saving…";
  autoApplySaveMessage.className = "save-message";

  const pct = Number(fitThresholdInput.value);
  if (!pct || pct < 1 || pct > 100) {
    autoApplySaveMessage.textContent = "ATS fit threshold must be between 1 and 100.";
    autoApplySaveMessage.className = "save-message save-error";
    return;
  }

  try {
    const result = await postJSON("/api/settings/auto-apply", {
      auto_apply_mode: autoApplyModeSelect.value,
      auto_apply_on_tailored_score: tailoredScoreToggle.checked,
      auto_apply_email: emailApplyToggle.checked,
      fit_threshold: pct / 100,
      ats_score_mode: atsScoreModeSelect.value,
    });
    autoApplySaveMessage.textContent = "Saved.";
    autoApplySaveMessage.className = "save-message save-success";
    lastKnownWhitelistedSources = result.whitelisted_sources;
    updateAutoApplyModeDesc();
  } catch (err) {
    autoApplySaveMessage.textContent = err.message || "Could not save settings.";
    autoApplySaveMessage.className = "save-message save-error";
  }
});

loadAutoApplySettings();

// ---- Job Matching panel (expansion / embeddings / ranking) ----------------

const searchOnlyToggle = document.getElementById("toggle-search-only");
const expansionToggle = document.getElementById("toggle-query-expansion");
const expansionMaxRoles = document.getElementById("expansion-max-roles");
const serpapiExpansionLimit = document.getElementById("serpapi-expansion-limit");
const embeddingProvider = document.getElementById("embedding-provider");
const embeddingModel = document.getElementById("embedding-model");
const embeddingModelHint = document.getElementById("embedding-model-hint");
const similarityThreshold = document.getElementById("similarity-threshold");
const matchScoreThreshold = document.getElementById("match-score-threshold");
const matchingSaveMessage = document.getElementById("matching-save-message");

let geminiKeySet = false;
let savedModels = { ollama: "", gemini: "" };
let candidateCap = null;
let knownEmbeddingModels = { ollama: [], gemini: [] };

const embeddingModelSelect = document.getElementById("embedding-model-select");
const embeddingModelNote = document.getElementById("embedding-model-note");

// The <select> is the visible control; the (hidden) text input stays the
// single source of truth for saving, so the submit handler is unchanged
// whether the value came from the list or was typed as a custom id.
function refreshEmbeddingModelOptions() {
  // Options for the CURRENT provider only -- offering a Gemini id while
  // Ollama is selected would just produce a confusing 404 on the next run.
  const list = knownEmbeddingModels[embeddingProvider.value] || [];
  const current = embeddingModel.value.trim();

  embeddingModelSelect.innerHTML =
    list.map((m) =>
      `<option value="${escapeHtml(m.id)}">${escapeHtml(m.id)} — ${escapeHtml(m.size)} · ${escapeHtml(m.context)} context · ${m.multilingual ? "multilingual" : "English-only"}</option>`
    ).join("") + `<option value="__custom__">Custom…</option>`;

  // A saved value that isn't in the catalogue (an older pick, or a model
  // added upstream since) must still show as selected rather than silently
  // switching the user to something they didn't choose.
  if (current && !list.some((m) => m.id === current)) {
    const opt = document.createElement("option");
    opt.value = current;
    opt.textContent = `${current} (currently saved)`;
    embeddingModelSelect.insertBefore(opt, embeddingModelSelect.lastChild);
  }
  embeddingModelSelect.value = current || (list[0] && list[0].id) || "__custom__";
  syncCustomVisibility();
  updateEmbeddingModelNote();
}

function syncCustomVisibility() {
  const custom = embeddingModelSelect.value === "__custom__";
  embeddingModel.hidden = !custom;
  if (!custom) embeddingModel.value = embeddingModelSelect.value;
}

// Show the tradeoffs for the model actually in play -- context length decides
// whether long scraped job descriptions get truncated, and multilingual
// support decides whether the Arabic/mixed postings score meaningfully.
function updateEmbeddingModelNote() {
  const list = knownEmbeddingModels[embeddingProvider.value] || [];
  const hit = list.find((m) => m.id === embeddingModel.value.trim());
  if (!hit) {
    embeddingModelNote.textContent = embeddingModel.value.trim()
      ? "Custom model — check its context window fits a full job description."
      : "";
    return;
  }
  embeddingModelNote.textContent =
    `${hit.size} · ${hit.context} context · ${hit.multilingual ? "100+ languages" : "English-centric"} — ${hit.note}`;
}

embeddingModelSelect.addEventListener("change", () => {
  syncCustomVisibility();
  updateEmbeddingModelNote();
  updateEmbeddingHint();
});

function updateEmbeddingHint() {
  const provider = embeddingProvider.value;
  if (provider === "ollama") {
    embeddingModelHint.textContent =
      `Local and unmetered — run \`ollama pull ${embeddingModel.value || "qwen3-embedding:4b"}\` once if you haven't.` +
      (candidateCap ? ` Semantic candidates per run: ${candidateCap}.` : "");
  } else if (!geminiKeySet) {
    embeddingModelHint.textContent = "No Gemini API key saved yet — add one in the LLM Provider panel above before saving this.";
  } else {
    embeddingModelHint.textContent =
      "Hosted free tier: 100 embeddings per minute, counted per job description — the per-run budget is lowered automatically to fit." +
      (candidateCap ? ` Semantic candidates per run: ${candidateCap}.` : "");
  }
}

// Swapping provider should show that provider's own saved model, not carry
// the other one's name across (they're stored under separate .env keys).
embeddingProvider.addEventListener("change", () => {
  embeddingModel.value = savedModels[embeddingProvider.value] || "";
  refreshEmbeddingModelOptions();
  updateEmbeddingHint();
});
embeddingModel.addEventListener("input", () => {
  updateEmbeddingModelNote();
  updateEmbeddingHint();
});

async function loadMatchingSettings() {
  try {
    const s = await getJSON("/api/settings/matching");
    searchOnlyToggle.checked = s.search_only_mode;
    expansionToggle.checked = s.search_query_expansion;
    expansionMaxRoles.value = s.query_expansion_max_roles;
    serpapiExpansionLimit.value = s.serpapi_expansion_limit;
    embeddingProvider.value = s.embedding_provider;
    savedModels = { ollama: s.ollama_embedding_model, gemini: s.gemini_embedding_model };
    embeddingModel.value = s.embedding_model || "";
    candidateCap = s.semantic_candidate_cap;
    knownEmbeddingModels = s.known_embedding_models || { ollama: [], gemini: [] };
    refreshEmbeddingModelOptions();
    similarityThreshold.value = Math.round(s.cv_job_similarity_threshold * 100);
    matchScoreThreshold.value = Math.round(s.match_score_threshold * 100);
    document.getElementById("match-score-range").value = matchScoreThreshold.value;
    geminiKeySet = s.gemini_api_key_set;
    updateEmbeddingHint();
  } catch (e) {
    matchingSaveMessage.textContent = "Could not reach the backend API.";
    matchingSaveMessage.className = "save-message save-error";
  }
}

document.getElementById("matching-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  matchingSaveMessage.textContent = "Saving…";
  matchingSaveMessage.className = "save-message";

  const similarityPct = Number(similarityThreshold.value);
  const matchPct = Number(matchScoreThreshold.value);
  if (!similarityPct || similarityPct < 1 || similarityPct > 100) {
    matchingSaveMessage.textContent = "Semantic match threshold must be between 1 and 100.";
    matchingSaveMessage.className = "save-message save-error";
    return;
  }
  if (matchPct < 0 || matchPct > 100 || Number.isNaN(matchPct)) {
    matchingSaveMessage.textContent = "Minimum match score must be between 0 and 100.";
    matchingSaveMessage.className = "save-message save-error";
    return;
  }

  try {
    const result = await postJSON("/api/settings/matching", {
      search_only_mode: searchOnlyToggle.checked,
      search_query_expansion: expansionToggle.checked,
      query_expansion_max_roles: Number(expansionMaxRoles.value),
      serpapi_expansion_limit: Number(serpapiExpansionLimit.value),
      embedding_provider: embeddingProvider.value,
      embedding_model: embeddingModel.value || null,
      cv_job_similarity_threshold: similarityPct / 100,
      match_score_threshold: matchPct / 100,
    });
    matchingSaveMessage.textContent = "Saved.";
    matchingSaveMessage.className = "save-message save-success";
    geminiKeySet = result.gemini_api_key_set;
    savedModels = { ollama: result.ollama_embedding_model, gemini: result.gemini_embedding_model };
    candidateCap = result.semantic_candidate_cap;
    updateEmbeddingHint();
    // Re-check Ollama against the model just saved. The status panel was
    // filled on page load, so without this it keeps reporting the PREVIOUS
    // model -- a newly-selected-but-unpulled one reads as fine, and the
    // failure only surfaces mid-run as "model not found", after retrieval
    // has already been paid for.
    loadOllamaStatus();
  } catch (err) {
    matchingSaveMessage.textContent = err.message || "Could not save settings.";
    matchingSaveMessage.className = "save-message save-error";
  }
});

document.getElementById("clear-expansion-cache").addEventListener("click", async () => {
  matchingSaveMessage.textContent = "Clearing…";
  matchingSaveMessage.className = "save-message";
  try {
    const result = await deleteJSON("/api/settings/matching/expansion-cache");
    matchingSaveMessage.textContent = `Cleared ${result.deleted} cached expansion(s) — the next search will regenerate them.`;
    matchingSaveMessage.className = "save-message save-success";
  } catch (err) {
    matchingSaveMessage.textContent = err.message || "Could not clear the cache.";
    matchingSaveMessage.className = "save-message save-error";
  }
});

loadMatchingSettings();

// ---- Sa'ei card controls -----------------------------------------------------
// The mode cards, provider cards and threshold slider are friendlier fronts
// for the (hidden) <select>/<input> elements above, which stay the single
// source of truth -- every load/save path in this file reads and writes those.

const MODE_GUARDRAIL = {
  off: "Active guardrail: human review for everything",
  whitelist: "Active guardrail: vetted whitelist",
  any: "No source guardrail — any website",
};

function syncModeCards() {
  document.querySelectorAll("#mode-cards [data-mode]").forEach((c) => {
    c.classList.toggle("is-active", c.dataset.mode === autoApplyModeSelect.value);
    c.setAttribute("aria-pressed", String(c.dataset.mode === autoApplyModeSelect.value));
  });
  const chip = document.getElementById("mode-guardrail-chip");
  chip.textContent = MODE_GUARDRAIL[autoApplyModeSelect.value] || "";
  chip.className = autoApplyModeSelect.value === "any" ? "sa-chip-amber self-start" : "sa-chip-teal self-start";
}

document.getElementById("mode-cards").addEventListener("click", (e) => {
  const card = e.target.closest("[data-mode]");
  if (!card) return;
  autoApplyModeSelect.value = card.dataset.mode;
  autoApplyModeSelect.dispatchEvent(new Event("change"));
});
autoApplyModeSelect.addEventListener("change", syncModeCards);

function syncProviderCards() {
  document.querySelectorAll("#provider-cards [data-provider]").forEach((c) => {
    c.classList.toggle("is-active", c.dataset.provider === providerSelect.value);
    c.setAttribute("aria-pressed", String(c.dataset.provider === providerSelect.value));
  });
}

document.getElementById("provider-cards").addEventListener("click", (e) => {
  const card = e.target.closest("[data-provider]");
  if (!card) return;
  providerSelect.value = card.dataset.provider;
  providerSelect.dispatchEvent(new Event("change"));
});
providerSelect.addEventListener("change", syncProviderCards);

const fitRange = document.getElementById("fit-threshold-range");
fitRange.addEventListener("input", () => { fitThresholdInput.value = fitRange.value; });
fitThresholdInput.addEventListener("input", () => { fitRange.value = fitThresholdInput.value; });

function syncFitRange() {
  fitRange.value = fitThresholdInput.value || 70;
  fitRange.disabled = fitThresholdInput.disabled;
}
atsScoreModeSelect.addEventListener("change", syncFitRange);

syncModeCards();
syncProviderCards();

// Section tabs: highlight the section currently in view.
const sectionTabs = [...document.querySelectorAll("[data-section]")];
const sectionObserver = new IntersectionObserver((entries) => {
  entries.forEach((en) => {
    if (!en.isIntersecting) return;
    sectionTabs.forEach((t) => t.classList.toggle("is-active", t.getAttribute("href") === `#${en.target.id}`));
  });
}, { rootMargin: "-30% 0px -60% 0px" });
sectionTabs.forEach((t) => sectionObserver.observe(document.querySelector(t.getAttribute("href"))));

// ---- 2026-09-24 layout: one Save for all three forms, extra slider ---------

const matchRange = document.getElementById("match-score-range");
matchRange.addEventListener("input", () => { matchScoreThreshold.value = matchRange.value; });
matchScoreThreshold.addEventListener("input", () => { matchRange.value = matchScoreThreshold.value; });

// Each form keeps its own endpoint and its own message next to its heading;
// the page-level button just submits all three.
document.getElementById("save-all").addEventListener("click", () => {
  ["settings-form", "auto-apply-form", "matching-form"].forEach((id) => {
    document.getElementById(id).requestSubmit();
  });
});

// ---- Notifications & channels (Gmail moved here from the Email page) -------

const connectBtn = document.getElementById("connect-btn");

function setChip(el, text, good) {
  el.textContent = text;
  el.className = `rounded px-1.5 py-0.5 text-label-sm normal-case tracking-normal ${good
    ? "bg-secondary-container text-on-secondary-fixed-variant" : "bg-tertiary-fixed text-on-tertiary-fixed-variant"}`;
}

// ---- Gmail: Sign in with Google ----------------------------------------------------

let emailStatus = null;

function paintEmailStatus(s) {
  emailStatus = s;
  const chip = document.getElementById("gmail-chip");
  const statusEl = document.getElementById("gmail-status");
  if (s.authenticated) {
    setChip(chip, "Connected", true);
    statusEl.textContent = `Sending as ${s.address || "your Gmail account"}.`;
  } else {
    setChip(chip, "Not connected", false);
    statusEl.textContent = "Not connected yet — sign in below.";
  }
  const oauth = document.getElementById("oauth-status");
  oauth.textContent = s.oauth_authenticated
    ? `Signed in${s.address ? ` as ${s.address}` : ""}.`
    : s.credentials_configured ? "" : "Google sign-in isn't set up in this copy of Sa'ei (no google_oauth_client.json).";
  connectBtn.disabled = !s.credentials_configured;
  document.getElementById("connect-label").textContent = s.oauth_authenticated
    ? "Sign in with a different account" : "Sign in with Google";

  const reportsChip = document.getElementById("reports-chip");
  const note = document.getElementById("reports-note");
  const reportTo = document.getElementById("report-to");
  if (document.activeElement !== reportTo) reportTo.value = s.report_to_custom ? s.report_to : "";
  reportTo.placeholder = s.address || "Your Gmail address";
  if (!s.authenticated) {
    setChip(reportsChip, "Off", false);
    note.textContent = "Connect Gmail above and reports start arriving.";
  } else {
    setChip(reportsChip, s.dry_run ? "Dry run" : "On", !s.dry_run);
    note.textContent = s.dry_run
      ? `Dry run is on, so reports are logged on the Reports page rather than sent to ${s.report_to}.`
      : `Reports go to ${s.report_to}.`;
  }
}

async function loadGmailStatus() {
  try {
    paintEmailStatus(await getJSON("/api/email/status"));
  } catch (e) {
    document.getElementById("gmail-status").textContent = "Could not reach the backend API.";
  }
}

function emailMessage(id, text, ok) {
  const el = document.getElementById(id);
  el.textContent = text;
  el.className = `mt-1 text-body-sm ${ok === undefined ? "text-on-surface-variant" : ok ? "save-success" : "save-error"}`;
}

connectBtn.addEventListener("click", async () => {
  connectBtn.disabled = true;
  emailMessage("connect-message", "Google's sign-in opened in a new tab — finish there, then come back.");
  try {
    await postJSON("/api/email/authenticate", {});
    emailMessage("connect-message", "Signed in.", true);
    await loadGmailStatus();
  } catch (e) {
    emailMessage("connect-message", e.message || "Authentication failed.", false);
  } finally {
    connectBtn.disabled = !emailStatus?.credentials_configured;
  }
});

document.getElementById("report-to-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    paintEmailStatus(await postJSON("/api/email/report-to", { address: document.getElementById("report-to").value }));
  } catch (err) {
    document.getElementById("reports-note").textContent = err.message || "Could not save.";
  }
});

loadGmailStatus();
