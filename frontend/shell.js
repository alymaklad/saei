// Shared app shell: the Sa'ei sidebar + top bar, injected on every page so the
// nav lives in one place instead of being copy-pasted into every HTML file.
//
// Each page marks itself with <body data-page="search"> and puts its content
// in <main id="page">. Load this script synchronously at the END of <body>,
// before config.js: it wraps #page in the shell immediately, so the
// #status-pill that config.js fills on DOMContentLoaded already exists.

(function () {
  // Settings has a layout of its own (its own section menu, "Back to app"),
  // like a desktop app's preferences window -- see settingsShell() below.
  if (document.body.dataset.layout === "settings") {
    settingsShell();
    return;
  }

  // Six destinations. Settings isn't one of them: it opens from the account
  // menu on the profile card at the bottom of the sidebar. Email and Features
  // were folded in elsewhere: sending lives on Applications, the Gmail
  // connection on Settings, and the sent-email log on Reports.
  const NAV = [
    { page: "dashboard", href: "index.html", icon: "home", label: "Home" },
    { page: "search", href: "search.html", icon: "explore", label: "Search" },
    { page: "applications", href: "applications.html", icon: "task_alt", label: "Applications" },
    { page: "cv", href: "cv.html", icon: "description", label: "CV" },
    { page: "profile", href: "profile.html", icon: "person", label: "Profile" },
    { page: "reports", href: "reports.html", icon: "insights", label: "Reports" },
  ];

  const HELP_URL = "https://github.com/alymaklad/Saei#readme";
  const menuItem = (href, icon, label, extra = "") => `
    <a href="${href}" role="menuitem" ${extra}
       class="flex items-center gap-space-sm px-space-md py-2.5 text-label-md text-on-surface transition-colors hover:bg-surface-container-low">
      <span class="ms text-[20px] text-on-surface-variant">${icon}</span>${label}
    </a>`;
  const ACCOUNT_MENU = `
    ${menuItem("profile.html", "account_circle", "Profile")}
    ${menuItem("settings.html", "settings", "Settings")}
    <div class="my-1 border-t border-surface-container"></div>
    ${menuItem(HELP_URL, "help", "Help", 'target="_blank" rel="noopener"')}`;

  const current = document.body.dataset.page || "";
  const currentItem = NAV.find((n) => n.page === current);

  const navHTML = NAV.map((n) => {
    const active = n.page === current;
    const cls = active
      ? "bg-secondary-container text-on-secondary-fixed font-semibold"
      : "text-on-surface-variant hover:bg-surface-container hover:text-on-surface";
    return `
      <a href="${n.href}" ${active ? 'aria-current="page"' : ""}
         class="flex items-center gap-space-sm rounded-lg px-space-sm py-space-sm transition-colors ${cls}">
        <span class="ms">${n.icon}</span>
        <span class="text-label-md">${n.label}</span>
      </a>`;
  }).join("");

  const sidebar = `
    <aside id="sa-sidebar"
      class="fixed left-0 top-0 z-50 flex h-screen w-72 flex-col justify-between bg-surface-container-low/70 p-space-md shadow-soft backdrop-blur-xl">
      <div class="flex flex-col gap-space-lg overflow-y-auto">
        <a href="index.html" class="flex items-center gap-space-sm px-space-xs pt-space-sm">
          <img src="assets/saei-logo.png" alt="" class="h-11 w-11 rounded-xl object-contain">
          <div class="flex flex-col">
            <div class="flex items-baseline gap-space-xs">
              <span class="text-headline-sm tracking-tight text-on-surface">Sa'ei</span>
              <span class="text-label-md text-primary" lang="ar">ساعي</span>
            </div>
            <span class="text-label-sm uppercase tracking-widest text-on-surface-variant">Career Companion</span>
          </div>
        </a>
        <nav class="flex flex-col gap-space-xs" aria-label="Main">${navHTML}</nav>
      </div>
      <div class="relative flex flex-col gap-space-sm">
      <div id="status-pill" class="pill pill-muted self-start">Checking status&hellip;</div>
      <div id="sa-account-menu" role="menu" hidden
           class="absolute bottom-full left-0 right-0 mb-space-xs overflow-hidden rounded-xl bg-surface-container-lowest py-1 shadow-lg ring-1 ring-surface-container">
        ${ACCOUNT_MENU}
      </div>
      <button type="button" id="sa-account-btn" aria-haspopup="menu" aria-expanded="false"
         class="flex w-full items-center justify-between gap-space-sm rounded-lg bg-surface-container-lowest/80 p-space-sm text-left shadow-soft transition-colors hover:bg-surface-container-lowest">
        <div class="flex min-w-0 items-center gap-space-sm">
          <div class="relative flex-shrink-0">
            <div class="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-label-sm text-on-primary" id="sa-user-initials">
              <span class="ms text-[18px]">person</span>
            </div>
            <span id="sa-health-dot" title="Checking backend…"
                  class="absolute bottom-0 right-0 h-2 w-2 rounded-full bg-outline ring-1 ring-surface-container-lowest"></span>
          </div>
          <div class="flex min-w-0 flex-col">
            <span class="truncate text-label-md font-semibold text-on-surface" id="sa-user-name">Your profile</span>
            <span class="truncate text-label-sm normal-case tracking-normal text-on-surface-variant" id="sa-user-title">Set up from your CV</span>
          </div>
        </div>
        <span class="ms text-[18px] text-on-surface-variant">unfold_more</span>
      </button>
      </div>
    </aside>`;


  // Wrap the page's own <main id="page"> in the shell: a centred 72rem column
  // under a fixed 4rem header, as in the design.
  const main = document.getElementById("page");
  const wrap = document.createElement("div");
  wrap.className = "pl-72";
  const column = document.createElement("div");
  column.className = "min-h-screen w-full bg-background";
  main.classList.add("mx-auto", "max-w-6xl", "px-space-lg", "py-space-xl");
  main.parentNode.insertBefore(wrap, main);
  wrap.appendChild(column);
  column.appendChild(main);

  // Page footer: the landscape mark and the caravan quote on every page, plus
  // an optional page-specific note from <body data-footer="...">.
  const note = document.body.dataset.footer || "";
  main.insertAdjacentHTML("beforeend", `
    <footer class="mt-space-xl flex items-center justify-between gap-space-md border-t border-surface-container pt-space-md text-body-sm text-on-surface-variant">
      <span class="flex items-center gap-space-sm">
        <span class="ms text-[18px] text-primary" aria-hidden="true">landscape</span>
        <span class="italic">“Big dreams aren't reached in a single stride — the caravan advances step by deliberate step.”</span>
      </span>
      ${note ? `<span class="text-right">${note.replace(/</g, "&lt;")}</span>` : ""}
    </footer>`);
  document.body.insertAdjacentHTML("afterbegin", sidebar);

  // ---- account menu: the profile card opens Profile / Settings / Help ----
  const accountBtn = document.getElementById("sa-account-btn");
  const accountMenu = document.getElementById("sa-account-menu");
  function setMenu(open) {
    accountMenu.hidden = !open;
    accountBtn.setAttribute("aria-expanded", String(open));
    if (open) accountMenu.querySelector("a")?.focus();
  }
  accountBtn.addEventListener("click", (e) => { e.stopPropagation(); setMenu(accountMenu.hidden); });
  document.addEventListener("click", (e) => { if (!accountMenu.contains(e.target)) setMenu(false); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !accountMenu.hidden) { setMenu(false); accountBtn.focus(); }
  });

  // ---- sidebar user card ----
  // /api/profile re-parses the CV file on every call, so the name/title is
  // cached for the browser session rather than fetched on every page view.
  // profile.js clears this key after a save so a rename shows up at once.
  const USER_KEY = "saei.sidebarUser";
  function paintUser(u) {
    if (!u || !u.name) return;
    document.getElementById("sa-user-name").textContent = u.name;
    document.getElementById("sa-user-title").textContent = u.title || "Profile";
    const initials = u.name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
    document.getElementById("sa-user-initials").textContent = initials;
    // Pages that greet the user by name (Home) listen for this.
    window.saeiUser = u;
    document.dispatchEvent(new CustomEvent("saei:user", { detail: u }));
  }
  let cached = null;
  try { cached = JSON.parse(sessionStorage.getItem(USER_KEY) || "null"); } catch (e) { cached = null; }
  if (cached) paintUser(cached);

  window.addEventListener("DOMContentLoaded", async () => {
    if (typeof getJSON !== "function") return;
    const dot = document.getElementById("sa-health-dot");
    try {
      const s = await getJSON("/api/status");
      dot.classList.remove("bg-outline");
      dot.classList.add(s.dry_run ? "bg-tertiary-fixed-dim" : "bg-tertiary-container");
      dot.title = `Backend online · LLM: ${s.llm_provider}${s.dry_run ? " · dry run" : ""}`;
    } catch (e) {
      dot.classList.replace("bg-outline", "bg-error");
      dot.title = "Backend unreachable";
    }
    if (cached) return;
    try {
      const p = await getJSON("/api/profile");
      const c = (p.profile && p.profile.contact) || {};
      const u = { name: c.full_name || "", title: c.title || "" };
      if (u.name) {
        try { sessionStorage.setItem(USER_KEY, JSON.stringify(u)); } catch (e) { /* private mode */ }
        paintUser(u);
      }
    } catch (e) { /* card keeps its placeholder */ }
  });

  // ---- Settings: its own window-like layout -------------------------------------
  //
  // Each settings section in settings.html is an element with
  // data-settings-panel="key" plus data-title / data-icon / data-group. This
  // builds the left menu from them and shows one at a time, chosen by the URL
  // hash (settings.html#email), so a section can be linked to directly.
  function settingsShell() {
    const main = document.getElementById("page");
    const panels = [...main.querySelectorAll("[data-settings-panel]")];
    const ALIASES = { channels: "email", llm: "model" };   // older links

    // "Back to app" returns where you came from, if that was a page of Sa'ei.
    let back = "index.html";
    try {
      const ref = new URL(document.referrer);
      if (ref.origin === location.origin && !ref.pathname.endsWith("settings.html")) back = ref.pathname + ref.search;
    } catch (e) { /* no referrer: Home */ }

    const groups = [];
    panels.forEach((panel) => {
      const name = panel.dataset.group || "";
      let group = groups.find((g) => g.name === name);
      if (!group) groups.push(group = { name, panels: [] });
      group.panels.push(panel);
    });
    const navHTML = groups.map((g) => `
      <div class="mt-space-md first:mt-0" data-settings-group>
        <p class="px-space-sm pb-1 text-label-sm normal-case tracking-normal text-on-surface-variant">${g.name}</p>
        ${g.panels.map((p) => `
          <a href="#${p.dataset.settingsPanel}" data-settings-link="${p.dataset.settingsPanel}"
             class="flex items-center gap-space-sm rounded-lg px-space-sm py-2 text-label-md text-on-surface transition-colors hover:bg-surface-container">
            <span class="ms text-[20px] text-on-surface-variant">${p.dataset.icon || "settings"}</span>${p.dataset.title}
          </a>`).join("")}
      </div>`).join("");

    document.body.insertAdjacentHTML("afterbegin", `
      <aside class="fixed left-0 top-0 z-40 flex h-screen w-72 flex-col gap-space-md border-r border-surface-container bg-surface-container-low/70 p-space-md backdrop-blur-xl">
        <a href="${back}" class="flex items-center gap-space-sm rounded-lg px-space-sm py-2 text-label-md text-on-surface transition-colors hover:bg-surface-container">
          <span class="ms text-[20px]">arrow_back</span>Back to app
        </a>
        <label class="flex items-center gap-space-sm rounded-full bg-surface-container px-space-md py-2 text-on-surface-variant">
          <span class="ms text-[18px]">search</span>
          <input type="search" id="settings-search" placeholder="Search" aria-label="Search settings"
                 class="w-full bg-transparent text-label-md text-on-surface outline-none placeholder:text-on-surface-variant">
        </label>
        <nav class="flex flex-col overflow-y-auto" aria-label="Settings">${navHTML}</nav>
      </aside>`);

    const column = document.createElement("div");
    column.className = "min-h-screen bg-background pl-72";
    main.parentNode.insertBefore(column, main);
    column.appendChild(main);
    main.classList.add("mx-auto", "max-w-3xl", "px-space-lg", "py-space-xl");
    main.insertAdjacentHTML("afterbegin", '<h1 class="sa-page-title mb-space-lg" id="settings-title">Settings</h1>');

    const links = [...document.querySelectorAll("[data-settings-link]")];
    const saveBar = document.getElementById("settings-save-bar");
    function show(key) {
      key = ALIASES[key] || key;
      const panel = panels.find((p) => p.dataset.settingsPanel === key) || panels[0];
      panels.forEach((p) => { p.hidden = p !== panel; });
      links.forEach((a) => {
        const on = a.dataset.settingsLink === panel.dataset.settingsPanel;
        a.classList.toggle("bg-surface-container", on);
        a.classList.toggle("font-semibold", on);
        if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
      });
      document.getElementById("settings-title").textContent = panel.dataset.title;
      document.title = `Sa'ei — Settings · ${panel.dataset.title}`;
      if (saveBar) saveBar.classList.toggle("hidden", !("saves" in panel.dataset));
      window.scrollTo(0, 0);
    }
    window.addEventListener("hashchange", () => show(location.hash.slice(1)));
    show(location.hash.slice(1));

    // Search: narrows the menu to sections whose title or contents mention
    // the words typed; Enter opens the first match.
    const search = document.getElementById("settings-search");
    const matches = () => {
      const words = search.value.toLowerCase().split(/\s+/).filter(Boolean);
      return links.filter((a) => {
        const panel = panels.find((p) => p.dataset.settingsPanel === a.dataset.settingsLink);
        const text = `${a.textContent} ${panel.textContent}`.toLowerCase();
        return words.every((w) => text.includes(w));
      });
    };
    search.addEventListener("input", () => {
      const hits = matches();
      links.forEach((a) => a.classList.toggle("hidden", !hits.includes(a)));
      document.querySelectorAll("[data-settings-group]").forEach((g) => {
        g.classList.toggle("hidden", ![...g.querySelectorAll("[data-settings-link]")]
          .some((a) => !a.classList.contains("hidden")));
      });
    });
    search.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      const first = matches()[0];
      if (first) location.hash = first.dataset.settingsLink;
    });
  }
})();
