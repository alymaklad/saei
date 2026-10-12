/**
 * Profile page — edit the structured CV data that scoring and CV tailoring
 * both read from.
 *
 * Editing model: `state.profile` is the single source of truth. Text inputs
 * write straight into it on every keystroke and are NEVER re-rendered while
 * the user types (re-rendering an input mid-edit moves the caret and drops
 * focus). Only structural changes — adding or removing an entry, adding or
 * removing a skill chip — trigger a re-render, and then only of the one list
 * that changed.
 */

const SKELETON = {
  experience: () => ({ title: "", organization: "", location: "", start: null,
                       end: null, is_professional: true, bullets: [""] }),
  projects: () => ({ name: "", url: "", start: null, end: null, bullets: [""] }),
  education: () => ({ degree: "", field: "", institution: "", location: "",
                      start: null, end: null }),
  certifications: () => "",
};

const state = {
  profile: null,
  saved: null,        // JSON snapshot of the last saved profile, for dirty checks
  meta: {},
  collapsed: new Set(),
};

// ---- helpers ----------------------------------------------------------------

function el(tag, className, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  Object.entries(attrs || {}).forEach(([k, v]) => {
    if (v === null || v === undefined || v === false) return;
    if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v;
    else node.setAttribute(k, v === true ? "" : v);
  });
  return node;
}

function icon(paths, cls) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  if (cls) svg.setAttribute("class", cls);
  svg.innerHTML = paths;
  return svg;
}

const PLUS = '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>';
const TIMES = '<line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/>';
const CHEVRON = '<polyline points="6 9 12 15 18 9"/>';
const GRIP = '<circle cx="9" cy="6" r="1.4"/><circle cx="15" cy="6" r="1.4"/><circle cx="9" cy="12" r="1.4"/><circle cx="15" cy="12" r="1.4"/><circle cx="9" cy="18" r="1.4"/><circle cx="15" cy="18" r="1.4"/>';

function snapshot(profile) {
  return JSON.stringify(profile);
}

function isDirty() {
  return state.saved !== null && snapshot(state.profile) !== state.saved;
}

function markDirty() {
  const pill = document.getElementById("profile-unsaved");
  if (pill) pill.hidden = !isDirty();
  // The identity card and section summaries (profile-ui.js) follow every edit.
  if (typeof window.renderProfileChrome === "function") window.renderProfileChrome();
}

/** A text/textarea input bound to a property on an object in state. */
function boundInput({ tag = "input", value, onInput, placeholder, rows, label, wrapClass }) {
  const wrap = el("div", wrapClass || "form-field");
  if (label) {
    const lab = el("label", null, { text: label });
    wrap.appendChild(lab);
  }
  const input = el(tag, null, { placeholder: placeholder || "" });
  if (tag === "textarea") {
    input.rows = rows || 2;
    input.value = value ?? "";
  } else {
    input.type = "text";
    input.value = value ?? "";
  }
  input.addEventListener("input", () => {
    onInput(input.value);
    markDirty();
  });
  wrap.appendChild(input);
  return wrap;
}

// ---- project link -----------------------------------------------------------
//
// Stored verbatim, the way it is written on the CV -- "github.com/me/thing" is
// what people type, and rewriting it to a canonical URL in the field would
// fight them while they type it. The scheme is added only for the little
// "open" button beside the input, so a bare domain is still clickable without
// the stored value changing.

const OPEN_LINK = '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>'
  + '<polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>';

function browsableUrl(value) {
  const text = String(value || "").trim();
  if (!text) return null;
  if (/^(https?|mailto):/i.test(text)) return text;
  // Anything else that could be a host gets https://. A value that is plainly
  // not a link ("internal GitLab") gets no button rather than a broken one.
  if (/^[\w-]+(\.[\w-]+)+([/?#].*)?$/.test(text)) return `https://${text}`;
  return null;
}

function linkField(entry) {
  const wrap = el("div", "form-field");
  wrap.appendChild(el("label", null, { text: "Link" }));

  const row = el("div", "link-row");
  const input = el("input", null, { placeholder: "github.com/you/project" });
  input.type = "text";
  input.value = entry.url ?? "";

  const open = el("a", "link-open", {
    target: "_blank", rel: "noopener noreferrer",
    title: "Open in a new tab", "aria-label": "Open link in a new tab",
  });
  open.appendChild(icon(OPEN_LINK));

  // The button keeps its space when there's nothing to open, so the Link
  // input stays the same width as the fields above and below it instead of
  // growing by 38px the moment the field is cleared.
  const syncOpen = () => {
    const href = browsableUrl(entry.url);
    open.classList.toggle("is-off", !href);
    if (href) {
      open.setAttribute("href", href);
      open.removeAttribute("aria-hidden");
      open.removeAttribute("tabindex");
    } else {
      open.removeAttribute("href");
      open.setAttribute("aria-hidden", "true");
      open.setAttribute("tabindex", "-1");
    }
  };

  input.addEventListener("input", () => {
    entry.url = input.value;
    syncOpen();
    markDirty();
  });
  syncOpen();

  row.appendChild(input);
  row.appendChild(open);
  wrap.appendChild(row);
  return wrap;
}

function removeButton(title, onClick) {
  const btn = el("button", "entry-remove", { type: "button", title, "aria-label": title });
  btn.appendChild(icon(TIMES));
  btn.addEventListener("click", onClick);
  return btn;
}

// ---- date pair --------------------------------------------------------------
//
// Dates are stored the way agents/cv_profile.py parses them: "YYYY-MM",
// "YYYY", "present", or null. A native <input type="month"> silently rejects a
// bare "2024" and would blank out real extracted values, so these are plain
// text fields with the accepted formats stated.

function datePair(entry, { allowPresent }) {
  const row = el("div", "date-pair");

  row.appendChild(boundInput({
    label: "Start", placeholder: "YYYY-MM", value: entry.start,
    onInput: (v) => { entry.start = v.trim() || null; },
    wrapClass: "form-field date-field",
  }));

  const endWrap = el("div", "form-field date-field");
  endWrap.appendChild(el("label", null, { text: "End" }));
  const endRow = el("div", "end-date-row");
  const endInput = el("input", null, { placeholder: "YYYY-MM" });
  endInput.type = "text";

  const isPresent = String(entry.end || "").toLowerCase() === "present";
  endInput.value = isPresent ? "" : (entry.end ?? "");
  endInput.disabled = isPresent;
  endInput.addEventListener("input", () => {
    entry.end = endInput.value.trim() || null;
    markDirty();
  });
  endRow.appendChild(endInput);

  if (allowPresent) {
    const presentLabel = el("label", "present-check");
    const box = el("input", null, { type: "checkbox" });
    box.checked = isPresent;
    box.addEventListener("change", () => {
      if (box.checked) {
        entry.end = "present";
        endInput.value = "";
        endInput.disabled = true;
      } else {
        entry.end = null;
        endInput.disabled = false;
      }
      markDirty();
    });
    presentLabel.appendChild(box);
    presentLabel.appendChild(el("span", null, { text: "Present" }));
    endRow.appendChild(presentLabel);
  }

  endWrap.appendChild(endRow);
  row.appendChild(endWrap);
  return row;
}

// ---- description ------------------------------------------------------------
//
// Stored as `bullets: []` — the shape the scorer indexes and quotes evidence
// from — but edited as one free-text box, because maintaining a list of
// separately-removable rows is fiddly for what is really just a paragraph.
//
// Each LINE round-trips to one array element, which matters beyond
// convenience: an evidence span is quoted back verbatim in the score
// explanation, so a 400-word paragraph on one line becomes one unreadable
// quote. Line breaks are how someone keeps those quotes short, and the
// placeholder says so.

function descriptionField(entry, sectionKey, index, opts = {}) {
  const wrap = el("div", "form-field profile-full-field");
  wrap.appendChild(el("label", null, { text: opts.label || "Description" }));

  const area = el("textarea", null, {
    rows: 4,
    placeholder: opts.placeholder || "What you did here. One achievement per line.",
  });
  area.value = (entry.bullets || []).join("\n");

  const autosize = () => {
    area.style.height = "auto";
    area.style.height = `${Math.max(area.scrollHeight, 90)}px`;
  };
  area.addEventListener("input", () => {
    // Blank lines are dropped on save by profile_store.normalize, but they're
    // kept in the box while typing -- collapsing them mid-keystroke would
    // fight the user every time they press Enter twice.
    entry.bullets = area.value.split("\n");
    autosize();
    markDirty();
  });
  requestAnimationFrame(autosize);

  wrap.appendChild(area);
  wrap.appendChild(el("p", "field-hint", {
    text: "Each line is quoted separately as evidence when a job is scored.",
  }));
  return wrap;
}

// ---- entry cards ------------------------------------------------------------

function entryShell(sectionKey, index, summaryTitle, summarySub) {
  const key = `${sectionKey}:${index}`;
  const card = el("div", "entry-card");
  if (state.collapsed.has(key)) card.classList.add("collapsed");

  const handle = el("span", "entry-grip", { "aria-hidden": "true" });
  handle.appendChild(icon(GRIP));
  card.appendChild(handle);

  const body = el("div", "entry-body");
  card.appendChild(body);

  const collapsedRow = el("button", "entry-collapsed-row", { type: "button" });
  collapsedRow.appendChild(el("span", "entry-collapsed-title", { text: summaryTitle || "Untitled entry" }));
  if (summarySub) collapsedRow.appendChild(el("span", "entry-collapsed-sub", { text: summarySub }));
  collapsedRow.appendChild(icon(CHEVRON, "entry-chevron"));
  collapsedRow.addEventListener("click", () => {
    state.collapsed.delete(key);
    renderSection(sectionKey);
  });
  card.appendChild(collapsedRow);

  const controls = el("div", "entry-controls");
  const collapse = el("button", "entry-collapse", { type: "button", title: "Collapse", "aria-label": "Collapse" });
  collapse.appendChild(icon(CHEVRON));
  collapse.addEventListener("click", () => {
    state.collapsed.add(key);
    renderSection(sectionKey);
  });
  controls.appendChild(collapse);
  controls.appendChild(removeButton("Remove entry", () => {
    state.profile[sectionKey].splice(index, 1);
    state.collapsed.clear();   // indices shift; stale keys would collapse the wrong card
    renderSection(sectionKey);
    markDirty();
  }));
  card.appendChild(controls);

  return { card, body };
}

function renderExperience(entry, index) {
  const { card, body } = entryShell("experience", index, entry.title,
    [entry.organization, entry.location,
     [entry.start, entry.end].filter(Boolean).join(" – ")]
      .filter(Boolean).join(" · "));

  const grid = el("div", "profile-grid");
  grid.appendChild(boundInput({
    label: "Job title", value: entry.title, placeholder: "Senior Engineer",
    onInput: (v) => { entry.title = v; },
  }));
  grid.appendChild(boundInput({
    label: "Organization", value: entry.organization, placeholder: "Company name",
    onInput: (v) => { entry.organization = v; },
  }));
  grid.appendChild(boundInput({
    label: "Location", value: entry.location, placeholder: "City, Country — or Remote",
    onInput: (v) => { entry.location = v; },
  }));
  grid.appendChild(el("div", "grid-spacer"));   // keeps Location alone on its own row
  body.appendChild(grid);
  body.appendChild(datePair(entry, { allowPresent: true }));

  // The single most consequential field on this page: it decides whether the
  // entry counts toward years of experience at all, and a wrong answer moves
  // the experience bucket on every job scored from here on.
  const toggleRow = el("label", "toggle-row entry-toggle");
  const textWrap = el("span", "toggle-text");
  textWrap.appendChild(el("span", "toggle-title", { text: "Counts as professional experience" }));
  textWrap.appendChild(el("span", "toggle-desc", {
    text: "Jobs and internships count toward your years of experience. Courses, training programs and volunteering don't.",
  }));
  toggleRow.appendChild(textWrap);

  const sw = el("span", "toggle-switch");
  const box = el("input", null, { type: "checkbox" });
  box.checked = entry.is_professional !== false;
  box.addEventListener("change", () => {
    entry.is_professional = box.checked;
    markDirty();
  });
  sw.appendChild(box);
  sw.appendChild(el("span", "toggle-track"));
  sw.appendChild(el("span", "toggle-thumb"));
  toggleRow.appendChild(sw);
  body.appendChild(toggleRow);

  body.appendChild(descriptionField(entry, "experience", index));
  return card;
}

function renderProject(entry, index) {
  const { card, body } = entryShell("projects", index, entry.name,
    [entry.start, entry.end].filter(Boolean).join(" – "));

  const grid = el("div", "profile-grid");
  grid.appendChild(boundInput({
    label: "Project name", value: entry.name, placeholder: "Project name",
    onInput: (v) => { entry.name = v; },
  }));
  grid.appendChild(linkField(entry));
  body.appendChild(grid);
  body.appendChild(datePair(entry, { allowPresent: false }));
  body.appendChild(descriptionField(entry, "projects", index));
  return card;
}

function renderEducation(entry, index) {
  const { card, body } = entryShell("education", index,
    [entry.degree, entry.field].filter(Boolean).join(", "),
    [entry.institution, entry.location,
     [entry.start, entry.end].filter(Boolean).join(" – ")]
      .filter(Boolean).join(" · "));

  const grid = el("div", "profile-grid");
  grid.appendChild(boundInput({
    label: "Degree", value: entry.degree, placeholder: "B.Sc.",
    onInput: (v) => { entry.degree = v; },
  }));
  grid.appendChild(boundInput({
    label: "Field of study", value: entry.field, placeholder: "Computer Science",
    onInput: (v) => { entry.field = v; },
  }));
  body.appendChild(grid);

  const placeGrid = el("div", "profile-grid profile-grid-row");
  placeGrid.appendChild(boundInput({
    label: "Institution", value: entry.institution, placeholder: "University name",
    onInput: (v) => { entry.institution = v; },
  }));
  placeGrid.appendChild(boundInput({
    label: "Location", value: entry.location, placeholder: "City, Country",
    onInput: (v) => { entry.location = v; },
  }));
  body.appendChild(placeGrid);

  body.appendChild(datePair(entry, { allowPresent: false }));
  return card;
}

// ---- certifications ---------------------------------------------------------
//
// Stored as flat strings ("AWS Certified — Amazon — 2024"), because that's what
// gets matched against job requirements as evidence text and what the CV
// writer prints. Edited as three fields, because one free-text box gives no
// hint that the issuer or the year belong there at all.
//
// The two representations round-trip through the same " — " separator
// profile_store uses when it flattens the object form, so a certification
// typed here and one extracted from a CV end up identical.

const CERT_SEPARATOR = " — ";

function splitCertification(value) {
  const parts = String(value ?? "").split(CERT_SEPARATOR);
  return {
    name: (parts[0] || "").trim(),
    issuer: (parts[1] || "").trim(),
    year: (parts[2] || "").trim(),
  };
}

function joinCertification({ name, issuer, year }) {
  return [name, issuer, year].map((p) => (p || "").trim()).filter(Boolean)
    .join(CERT_SEPARATOR);
}

function renderCertification(value, index) {
  const parts = splitCertification(value);
  const row = el("div", "cert-row");

  const field = (key, placeholder, cls) => {
    const input = el("input", cls, { placeholder });
    input.type = "text";
    input.value = parts[key];
    input.addEventListener("input", () => {
      parts[key] = input.value;
      state.profile.certifications[index] = joinCertification(parts);
      markDirty();
    });
    return input;
  };

  row.appendChild(field("name", "Certification name"));
  row.appendChild(field("issuer", "Issuer"));
  row.appendChild(field("year", "Year", "cert-year"));
  row.appendChild(removeButton("Remove certification", () => {
    state.profile.certifications.splice(index, 1);
    renderSection("certifications");
    markDirty();
  }));
  return row;
}

const RENDERERS = {
  experience: renderExperience,
  projects: renderProject,
  education: renderEducation,
  certifications: renderCertification,
};

const COUNT_IDS = {
  experience: "count-experience",
  projects: "count-projects",
  education: "count-education",
  certifications: "count-certifications",
};

function renderSection(sectionKey) {
  const list = document.getElementById(`list-${sectionKey}`);
  const empty = document.getElementById(`empty-${sectionKey}`);
  if (!list) return;

  const items = state.profile[sectionKey] || [];
  list.innerHTML = "";
  items.forEach((item, i) => list.appendChild(RENDERERS[sectionKey](item, i)));

  if (empty) empty.hidden = items.length > 0;
  const badge = document.getElementById(COUNT_IDS[sectionKey]);
  if (badge) badge.textContent = items.length;

  // The certifications column headers only make sense above actual rows --
  // shown with an empty list they read as a table that failed to load.
  const certTable = document.getElementById("cert-table");
  if (sectionKey === "certifications" && certTable) {
    certTable.hidden = items.length === 0;
  }
}

// ---- skills -----------------------------------------------------------------

function renderSkills() {
  const field = document.getElementById("skills-field");
  const skills = state.profile.skills_claimed || [];
  field.innerHTML = "";

  skills.forEach((skill, i) => {
    const chip = el("span", "chip");
    chip.appendChild(el("span", "chip-label", { text: skill }));
    const x = el("button", "chip-remove", { type: "button", "aria-label": `Remove ${skill}` });
    x.appendChild(icon(TIMES));
    x.addEventListener("click", () => {
      state.profile.skills_claimed.splice(i, 1);
      renderSkills();
      markDirty();
    });
    chip.appendChild(x);
    field.appendChild(chip);
  });

  // The chip area collapses entirely when there's nothing in it -- an empty
  // bordered box next to an input reads as a broken control.
  field.hidden = skills.length === 0;
  const empty = document.getElementById("empty-skills");
  if (empty) empty.hidden = skills.length > 0;

  const badge = document.getElementById("count-skills");
  if (badge) badge.textContent = skills.length;
}

function addSkill(raw) {
  // Comma-separated paste is the common case when copying a skills line out of
  // an existing CV, so split on it rather than creating one chip of ten skills.
  const parts = raw.split(",").map((s) => s.trim()).filter(Boolean);
  const existing = new Set((state.profile.skills_claimed || []).map((s) => s.toLowerCase()));
  parts.forEach((p) => {
    if (existing.has(p.toLowerCase())) return;
    existing.add(p.toLowerCase());
    state.profile.skills_claimed.push(p);
  });
  renderSkills();
  markDirty();
}

// ---- section order & custom sections ------------------------------------------
//
// `section_order` lists every section below About & Contact, which is the CV
// header and never moves. Built-in sections use their profile key; a custom
// section is "custom:<id>". profile_store.normalize repairs the list on save
// (unknown keys dropped, missing ones appended); orderedKeys() does the same
// here so the page never renders a section twice or loses one.

const RECOMMENDED_ORDER = ["experience", "projects", "education", "skills_claimed", "certifications"];

// The page's data-acc names differ from the profile key in one place.
const ACC_NAME = { skills_claimed: "skills" };
const ORDER_KEY = { skills: "skills_claimed" };

function accName(orderKey) { return ACC_NAME[orderKey] || orderKey; }

function sectionEl(orderKey) {
  return document.querySelector(`#profile-form .acc[data-acc="${CSS.escape(accName(orderKey))}"]`);
}

function customKeys() {
  return (state.profile.custom_sections || []).map((c) => `custom:${c.id}`);
}

function orderedKeys(order) {
  const valid = [...RECOMMENDED_ORDER, ...customKeys()];
  const out = (order || []).filter((k, i, all) => valid.includes(k) && all.indexOf(k) === i);
  return [...out, ...valid.filter((k) => !out.includes(k))];
}

/** Recommended order, with custom sections after the built-ins in the order the user has them. */
function recommendedOrder() {
  const customs = state.profile.section_order.filter((k) => k.startsWith("custom:"));
  return [...RECOMMENDED_ORDER, ...customs];
}

function newSectionId() {
  return Math.random().toString(36).slice(2, 10);
}

function moveControls() {
  const box = el("div", "acc-move");
  [["-1", "arrow_upward", "Move section up"], ["1", "arrow_downward", "Move section down"]]
    .forEach(([dir, glyph, label]) => {
      const btn = el("button", "acc-move-btn", { type: "button", "data-move": dir, title: label, "aria-label": label });
      btn.appendChild(el("span", "ms", { text: glyph }));
      box.appendChild(btn);
    });
  return box;
}

/** Put the header button and the reorder arrows side by side. */
function addMoveControls(section) {
  const head = section.querySelector(":scope > .acc-head");
  if (!head) return;
  const row = el("div", "acc-row");
  section.insertBefore(row, head);
  row.appendChild(head);
  row.appendChild(moveControls());
}

function buildCustomSection(section) {
  const acc = el("section", "acc", { "data-acc": `custom:${section.id}`, "data-custom": true });

  const head = el("button", "acc-head", { type: "button" });
  const ic = el("span", "acc-icon");
  ic.appendChild(el("span", "ms", { text: "article" }));
  head.appendChild(ic);
  const text = el("span", "acc-text");
  const title = el("span", "acc-title");
  title.appendChild(el("span", "acc-num"));
  title.appendChild(document.createTextNode(" "));
  const name = el("span", null, { text: section.title || "Untitled section" });
  title.appendChild(name);
  title.appendChild(el("span", "acc-tag", { text: "Custom section" }));
  text.appendChild(title);
  text.appendChild(el("span", "acc-sub"));
  head.appendChild(text);
  head.appendChild(el("span", "acc-toggle", { text: "Expand" }));
  acc.appendChild(head);
  addMoveControls(acc);

  const body = el("div", "acc-body");
  body.appendChild(boundInput({
    label: "Section title", value: section.title,
    placeholder: "e.g. Publications, Volunteering, Languages, Awards",
    onInput: (v) => { section.title = v; name.textContent = v.trim() || "Untitled section"; },
  }));
  body.appendChild(descriptionField(section, "custom", 0, {
    label: "Content",
    placeholder: "One item per line — a publication, an award, a language and its level…",
  }));
  const remove = el("button", "acc-remove", { type: "button" });
  remove.appendChild(el("span", "ms text-[18px]", { text: "delete" }));
  remove.appendChild(document.createTextNode("Remove section"));
  remove.addEventListener("click", () => {
    const hasContent = section.title.trim() || (section.bullets || []).some((b) => b.trim());
    if (hasContent && !confirm(`Remove the “${section.title.trim() || "Untitled"}” section?`)) return;
    state.profile.custom_sections = state.profile.custom_sections.filter((c) => c !== section);
    state.profile.section_order = orderedKeys(state.profile.section_order);
    renderCustomSections();
    markDirty();
  });
  body.appendChild(remove);
  acc.appendChild(body);
  return acc;
}

/** Rebuild the custom sections (structural changes only), then re-sort. */
function renderCustomSections() {
  const form = document.getElementById("profile-form");
  const open = form.querySelector(".acc[data-custom].is-open")?.dataset.acc;
  form.querySelectorAll(".acc[data-custom]").forEach((node) => node.remove());
  (state.profile.custom_sections || []).forEach((section) => {
    const acc = buildCustomSection(section);
    if (acc.dataset.acc === open) {
      acc.classList.add("is-open");
      acc.querySelector(".acc-toggle").textContent = "Collapse";
    }
    form.appendChild(acc);
  });
  applyOrder();
}

/** Arrange the sections in `section_order` and refresh numbers, arrows and the note. */
function applyOrder() {
  const form = document.getElementById("profile-form");
  const order = state.profile.section_order;
  order.forEach((key, i) => {
    const sec = sectionEl(key);
    if (!sec) return;
    form.appendChild(sec);   // About & Contact stays first; appending in order sorts the rest
    sec.querySelector(".acc-num").textContent = `${i + 2}.`;
    const [up, down] = sec.querySelectorAll(".acc-move-btn");
    if (up) up.disabled = i === 0;
    if (down) down.disabled = i === order.length - 1;
  });

  (document.getElementById("section-count") || {}).textContent = `${order.length + 1} sections total`;

  const custom = order.join("|") !== recommendedOrder().join("|");
  document.getElementById("order-note-lead").textContent =
    custom ? "Your own order. Recommended:" : "Recommended order:";
  document.getElementById("order-reset").hidden = !custom;
}

function moveSection(orderKey, delta) {
  const order = state.profile.section_order;
  const from = order.indexOf(orderKey);
  const to = from + delta;
  if (from < 0 || to < 0 || to >= order.length) return;
  [order[from], order[to]] = [order[to], order[from]];
  applyOrder();
  markDirty();

  const sec = sectionEl(orderKey);
  sec.classList.add("just-moved");
  setTimeout(() => sec.classList.remove("just-moved"), 700);
  // Moving the node drops focus; keep it on the arrow so repeated presses work.
  const btn = sec.querySelector(`.acc-move-btn[data-move="${delta}"]`);
  (btn && !btn.disabled ? btn : sec.querySelector(".acc-move-btn:not(:disabled)"))?.focus();
  sec.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function addCustomSection() {
  const section = { id: newSectionId(), title: "", bullets: [""] };
  state.profile.custom_sections.push(section);
  state.profile.section_order.push(`custom:${section.id}`);
  renderCustomSections();
  markDirty();
  const key = `custom:${section.id}`;
  if (typeof window.openProfileSection === "function") window.openProfileSection(key);
  const sec = sectionEl(key);
  sec.scrollIntoView({ block: "center", behavior: "smooth" });
  sec.querySelector(".acc-body input")?.focus({ preventScroll: true });
}

// ---- contact & summary ------------------------------------------------------

function fillContact() {
  document.querySelectorAll("[data-contact]").forEach((input) => {
    const key = input.dataset.contact;
    input.value = (state.profile.contact || {})[key] || "";
    input.oninput = () => {
      state.profile.contact = state.profile.contact || {};
      state.profile.contact[key] = input.value;
      markDirty();
    };
  });

  const summary = document.getElementById("pf-summary");
  summary.value = state.profile.summary || "";
  summary.oninput = () => {
    state.profile.summary = summary.value;
    markDirty();
  };
}

// ---- status strip -----------------------------------------------------------

function relativeTime(iso) {
  if (!iso) return null;
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return null;
  const seconds = Math.round((Date.now() - then.getTime()) / 1000);
  if (seconds < 90) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

function renderStatus() {
  const source = document.getElementById("profile-source");
  const meta = state.meta;

  if (!meta.has_profile) {
    source.innerHTML = meta.has_cv
      ? `Nothing extracted yet from <strong>${escapeHtml(meta.cv_filename)}</strong> — press “Re-extract from CV” to fill this in.`
      : "No CV uploaded yet. Add one on the CV page, then extract.";
  } else {
    const when = relativeTime(meta.extracted_at);
    const file = meta.source_cv_filename || meta.cv_filename || "your CV";
    let text = `Last extracted from <strong>${escapeHtml(file)}</strong>`;
    if (when) text += ` · ${when}`;
    if (meta.edited_at) {
      const editedWhen = relativeTime(meta.edited_at);
      text += ` · edited by hand${editedWhen ? ` ${editedWhen}` : ""}`;
    }
    source.innerHTML = text;
  }

  const alert = document.getElementById("profile-alert");
  if (meta.cv_changed_since_extraction) {
    alert.hidden = false;
    alert.innerHTML =
      "Your uploaded CV file has changed since this profile was extracted. " +
      "Scoring still uses what's below — press <strong>Re-extract from CV</strong> to pick up the new file.";
  } else {
    alert.hidden = true;
  }
}

// ---- load / save ------------------------------------------------------------

function applyPayload(payload) {
  state.meta = payload;
  state.profile = payload.profile;
  // Defaulted before the snapshot, so a profile saved before sections could
  // be reordered doesn't load already "dirty".
  state.profile.custom_sections = state.profile.custom_sections || [];
  state.profile.section_order = orderedKeys(state.profile.section_order);
  state.saved = snapshot(state.profile);
  state.collapsed.clear();

  fillContact();
  ["experience", "projects", "education", "certifications"].forEach(renderSection);
  renderSkills();
  renderCustomSections();
  renderStatus();
  markDirty();
}

async function load() {
  try {
    applyPayload(await getJSON("/api/profile"));
  } catch (err) {
    document.getElementById("profile-source").textContent =
      `Could not load your profile: ${err.message}`;
  }
}

function setMessage(text, cls) {
  const node = document.getElementById("profile-save-message");
  node.textContent = text;
  node.className = `save-message ${cls || ""}`;
}

async function save() {
  const buttons = [document.getElementById("btn-save"), document.getElementById("btn-save-footer")];
  buttons.forEach((b) => { b.disabled = true; });
  setMessage("Saving…", "");
  try {
    applyPayload(await postJSON("/api/profile", { profile: state.profile }));
    setMessage("Saved.", "save-success");
    // The sidebar caches your name/title for the session (shell.js).
    try { sessionStorage.removeItem("saei.sidebarUser"); } catch (e) { /* ignore */ }
  } catch (err) {
    setMessage(err.message, "save-error");
  } finally {
    buttons.forEach((b) => { b.disabled = false; });
  }
}

// ---- re-extract -------------------------------------------------------------

function openModal(el_) { el_.hidden = false; }
function closeModal(el_) { el_.hidden = true; }

async function openReExtract() {
  const modal = document.getElementById("reextract-modal");
  document.getElementById("reextract-error").hidden = true;
  document.getElementById("reextract-filename").textContent =
    state.meta.cv_filename || "your CV";

  let preview = { edited_section_labels: [] };
  try {
    preview = await getJSON("/api/profile/re-extract/preview");
  } catch (err) { /* the warning is advisory; the action still works without it */ }

  const warning = document.getElementById("reextract-warning");
  const list = document.getElementById("reextract-sections");
  list.innerHTML = "";
  const labels = preview.edited_section_labels || [];
  labels.forEach((label) => list.appendChild(el("li", null, { text: label })));
  warning.hidden = labels.length === 0;

  openModal(modal);
}

async function confirmReExtract() {
  const btn = document.getElementById("reextract-confirm");
  const error = document.getElementById("reextract-error");
  btn.disabled = true;
  btn.textContent = "Extracting…";
  error.hidden = true;
  try {
    applyPayload(await postJSON("/api/profile/re-extract", {}));
    closeModal(document.getElementById("reextract-modal"));
    setMessage("Re-extracted from your CV.", "save-success");
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "Re-extract";
  }
}

// ---- wiring -----------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll('#profile-form .acc:not([data-acc="contact"])').forEach(addMoveControls);
  load();

  document.getElementById("profile-form").addEventListener("click", (e) => {
    const btn = e.target.closest(".acc-move-btn");
    if (!btn || !state.profile) return;
    const name = btn.closest(".acc").dataset.acc;
    moveSection(ORDER_KEY[name] || name, Number(btn.dataset.move));
  });
  document.getElementById("order-reset").addEventListener("click", () => {
    state.profile.section_order = recommendedOrder();
    applyOrder();
    markDirty();
  });
  document.getElementById("btn-add-section").addEventListener("click", () => {
    if (state.profile) addCustomSection();
  });

  document.querySelectorAll("[data-add]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.add;
      state.profile[key] = state.profile[key] || [];
      state.profile[key].push(SKELETON[key]());
      renderSection(key);
      markDirty();
      const list = document.getElementById(`list-${key}`);
      const last = list.lastElementChild;
      if (last) last.querySelector("input, textarea")?.focus();
    });
  });

  const skillInput = document.getElementById("skill-input");
  const commitSkill = () => {
    if (!skillInput.value.trim()) return;
    addSkill(skillInput.value);
    skillInput.value = "";
    skillInput.focus();
  };

  skillInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      commitSkill();
    }
  });
  // Deliberately NOT committing on blur: with a visible "Add skill" button,
  // clicking it after typing would fire blur first and then the click handler,
  // adding the same skill twice. The button is the explicit path now.
  document.getElementById("skill-add").addEventListener("click", commitSkill);

  document.getElementById("btn-save").addEventListener("click", save);
  document.getElementById("btn-save-footer").addEventListener("click", save);

  document.getElementById("btn-discard").addEventListener("click", () => {
    if (isDirty() && !confirm("Discard your unsaved changes?")) return;
    load();
    setMessage("", "");
  });

  document.getElementById("btn-re-extract").addEventListener("click", openReExtract);
  document.getElementById("reextract-confirm").addEventListener("click", confirmReExtract);
  ["reextract-cancel", "reextract-close"].forEach((id) => {
    document.getElementById(id).addEventListener("click",
      () => closeModal(document.getElementById("reextract-modal")));
  });

  // Losing hand-typed corrections to a stray navigation is exactly the failure
  // this page exists to prevent, so guard the tab close too.
  window.addEventListener("beforeunload", (e) => {
    if (!isDirty()) return;
    e.preventDefault();
    e.returnValue = "";
  });
});
