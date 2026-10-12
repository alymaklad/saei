# Architecture

Technical deep-dive into how the Job Application Agent actually works under
the hood — one level below the [README](../README.md), which covers setup and
usage. This document exists to explain the code itself: what each module in
`agents/` and `jobs/` does, how they call each other, and how a job posting
gets from "found on the internet" to "sitting in the database as a scored,
drafted, or auto-submitted application."

If you're trying to modify the system rather than just run it, start here.

## The short version

`jobs/` holds the entry points — the scripts that actually get executed,
either by hand, by `scheduler.py`, or by `api.py`. `agents/` holds the
capabilities those entry points compose together — search, scoring, CV
rewriting, applying, emailing, reporting. Neither package knows about the
other's internals beyond function signatures: a `jobs/` script imports the
`agents/` functions it needs and wires them to the database, and
`orchestrator.py` (in the project root, not inside either package) wires the
per-job decision logic — score, then rewrite-or-apply — into a small
LangGraph state machine that both `jobs/daily_run.py` and
`jobs/apply_from_link.py` call for every individual job.

```
jobs/daily_run.py ──┐
                     ├─> agents/search_agent.py (find raw jobs)
                     │
                     └─> for each new job:
                             orchestrator.py (per-job state machine)
                               ├─> agents/ats_agent.py       (score fit)
                               ├─> agents/cv_rewriter_agent.py (if low fit)
                               └─> agents/apply_agent.py      (if good fit)
                         └─> writes Job/Application/SkillGap rows to SQLite

jobs/daily_report.py ──> agents/reporter_agent.py  (reads today's Applications, emails the report)
jobs/weekly_news.py  ──> agents/news_agent.py + agents/reporter_agent.py
jobs/apply_from_link.py ──> same orchestrator.py path as daily_run.py, for one URL

scheduler.py  ──> calls all three jobs/*.py entry points on a cron schedule
api.py        ──> reads the same SQLite DB for the dashboard, and can invoke
                   jobs/daily_run.py's search on demand ("Search now" button)
```

## Entry points: `jobs/`

Everything in `jobs/` is a script with a `run_*()` function and an
`if __name__ == "__main__"` block, so each one is independently runnable from
the command line, importable by `scheduler.py`, and (for the search job)
callable from `api.py`. None of them contain business logic themselves —
they fetch data, hand it to an agent or the orchestrator, and persist the
result.

### `jobs/daily_run.py` — search, score, act

The main pipeline. `run_daily_search_and_apply()`:

1. Calls `db.init_db()` (creates tables if missing, seeds the default search
   sites — see `agents/search_agent.py` below).
2. Resolves the active CV via `cv_parser.find_default_cv("cv")` (looks for
   `cv/current_cv.pdf` or `cv/current_cv.docx`) and parses it to plain text
   with `cv_parser.parse_cv()`. Raises immediately if no CV exists.
3. Resolves `position` / `seniority` / `max_results_per_site` / `max_age_days`
   — each defaults to the matching `config.SEARCH_*` value (whatever's saved
   from the dashboard's Search tab) if not passed explicitly. This is what
   keeps a manual `python jobs/daily_run.py` run, the 8am scheduler run, and
   the dashboard's "Search now" button all searching the same thing without
   three separate config paths.
4. **Query expansion.** `agents.query_expansion_agent.get_target_roles()`
   widens the typed Position into the set of equivalent role titles (cached;
   degrades to `[position]` if the LLM is unavailable).
5. **Retrieval.** `agents.search_agent.run_search(..., target_roles=...)`
   returns `(found_jobs, source_errors)` — a flat, URL-deduplicated list of
   raw job dicts from every configured source, plus any individual sources
   that failed (a dead Lever slug, a network timeout) without aborting the rest.
6. **`_drop_already_known()`** removes jobs whose URL is already in the `Job`
   table. `run_search()` has no memory across runs, so a daily schedule
   re-fetches the same postings every morning — without this, embeddings,
   skill extraction and ranking would all be paid for again on jobs that are
   about to be discarded as duplicates at INSERT time anyway.
7. **`_rank_candidates()`** runs the semantic retrieval path over the
   token-path misses only, unions the two, and ranks the result (see
   `agents/ranking_agent.py`). Every step degrades gracefully: if embeddings
   are unavailable (no local Ollama, no Gemini key) the run continues with
   token-retrieved jobs and no semantic factor, rather than failing.
8. **The match-score gate.** Jobs below `config.MATCH_SCORE_THRESHOLD` skip
   the orchestrator's per-job LLM pipeline entirely. **Off by default (0.0)**
   — a non-zero default would silently discard jobs against a threshold
   nobody has calibrated, the same failure mode as this project's earlier
   over-filtering bugs. The count held back is *reported*, so a too-high
   threshold looks like "N below match threshold" rather than "found nothing".
9. For each surviving job with a URL, `_process_one_job()` handles dedupe and
   persistence — see below.
10. Returns `{"processed": [...], "errors": [...], "found": N,
    "source_errors": [...], "target_roles": [...], "new_after_dedup": N,
    "ranked": N, "skipped_low_match": N}` — printed to stdout when run from
    the CLI, returned as JSON when triggered from `api.py`.

**`_process_one_job(raw_job, cv_text)`** is split into three short,
independent transactions rather than one transaction spanning the whole
thing — a deliberate fix, not the original design:

1. **`_insert_job_row()`** — dedupe-check (`_job_exists`, keyed on URL) plus
   insert the `Job` row, in one short `with get_session()` block that
   commits immediately. Returns `None` if the URL already exists, or if it
   lost a race to another process inserting the same URL concurrently
   (`Job.url`'s unique constraint raises `IntegrityError`, caught here).
   Committing early (rather than deferring the insert) is what makes the
   row's id available for the CV-output filename in step 2, without needing
   to hold this transaction open while that happens.
2. **`orchestrator_app.invoke(...)`** (see
   [Orchestrator](#orchestrator-the-per-job-state-machine) below) runs with
   **no open database transaction at all** — this is the important part. If
   it raises, **`_delete_job_row()`** removes the row step 1 just committed,
   so the URL is free to be retried on the next run instead of permanently
   stuck behind the dedupe check with no `Application` row to show for it.
3. **`_finalize_job()`** — a second short transaction inserting the
   `Application` row (plus a `SkillGap` row if the orchestrator surfaced
   missing skills).

This replaced an earlier version where the entire batch loop ran inside one
`with get_session()` block, with each job wrapped in only a `SAVEPOINT`
(`session.begin_nested()`) for isolation — meaning the single underlying
SQLite connection held its write lock from the first job's insert until the
*whole batch* finished, including every job's orchestrator call (multiple
LLM round-trips each). With dozens of jobs per run that could hold the lock
for minutes. SQLite only allows one writer at a time even in WAL mode (see
`db.py` below), so a second process writing during that window — the
scheduler's own automatic run overlapping a manual "Search now" click, for
example — failed outright with `sqlite3.OperationalError: database is
locked`, a failure actually hit in production. Splitting into three short,
independent transactions per job means the write lock is only ever held for
a single row write (milliseconds), so real overlap between two runs just
makes one wait briefly instead of erroring.

### `jobs/apply_from_link.py` — one URL, on demand

`apply_from_link(url, cv_path=None)` is the manual counterpart to the daily
batch job: paste a single job URL (from any site, not just
Greenhouse/Lever), and it runs that one job through the exact same
orchestrator path. It fetches the page with `requests` + `BeautifulSoup`,
guesses the source from the URL (`greenhouse.io` / `lever.co` / otherwise
`manual_link`), checks the `jobs` table for the same URL (returns
`{"status": "already_processed", ...}` if found instead of reprocessing),
inserts a `Job` row, and calls `orchestrator_app.invoke()` directly — no
`Application`/`SkillGap` bookkeeping wrapper like `daily_run.py` has, since
this is a one-off CLI tool, not a batch job the dashboard reports on.
Runnable as `python jobs/apply_from_link.py <url> [cv_path]`.

### `jobs/daily_report.py` — evening summary

`run_daily_report()` queries `Application` joined to `Job` for everything
created in the last 24 hours, builds a plain-text summary via
`agents.reporter_agent.build_daily_report()`, sends it with
`agents.reporter_agent.send_report()` (an email), and — regardless of whether
the send succeeded, failed, or was a dry run — writes a `ReportLog` row
(`status` is one of `sent` / `dry_run` / `failed`) so the dashboard's Reports
tab has a permanent record of every attempt, not just the last one. A raised
exception from the send is caught and logged as `status="failed"`
rather than crashing the scheduler process.

### `jobs/weekly_news.py` — field news digest

`run_weekly_news_digest(field="software engineering")` calls
`agents.news_agent.fetch_news()` then `summarize_news()`, stores the summary
as a `NewsDigest` row, emails it the same way
`daily_report.py` does, and logs a matching `ReportLog` row with the same
sent/dry_run/failed bookkeeping. `field` is currently a hardcoded default
rather than a dashboard setting — the module comment on `DEFAULT_FIELD` is
the whole configuration surface right now.

### How `scheduler.py` uses these

`scheduler.py` just imports `run_daily_search_and_apply`,
`run_daily_report`, and `run_weekly_news_digest` and registers them on
`APScheduler` cron triggers (8am / 8pm / Monday 9am). It has no logic of its
own beyond that — every actual behavior described above lives in `jobs/`.

It previously also scheduled a one-time "run all three jobs 1 minute after
this process starts" block on every restart, added to verify the automation
worked end-to-end without waiting for a real cron slot. That block is now
commented out (not deleted — see the comment in `scheduler.py` for how to
re-enable it) rather than removed outright, for two reasons: automation is
confirmed working, and it was a direct contributor to the `database is
locked` failures above — this test run firing while a manual "Search now"
click was already mid-batch was exactly the kind of process overlap that
surfaced the bug.

## Orchestrator: the per-job state machine

`orchestrator.py` (project root) is what `daily_run.py` and
`apply_from_link.py` both call once per job. It's a small
[LangGraph](https://langchain-ai.github.io/langgraph/) `StateGraph` — a
directed graph of Python functions sharing a typed state dict — rather than
a chain of nested if/else calls, mainly so the flow is inspectable and
easy to extend with new branches later without restructuring the whole thing.

```
                    score
                      │
         ats score < FIT_THRESHOLD (0.7)?
             ┌────────┴────────┐
            yes                 no
             │                   │
         rewrite_cv        decide_apply_path
             │                   │
   tailored score clears   ┌─────┴─────┐
   FIT_THRESHOLD AND        auto_submit  draft_for_review
   AUTO_APPLY_ON_               │             │
   TAILORED_SCORE?             END           END
      ┌────┴────┐
     yes         no
      │           │
 decide_apply_path  END
  (see right branch)
```

`FIT_THRESHOLD` (default `0.7`) and `AUTO_APPLY_MODE` (`"off"` /
`"any"` / `"whitelist"`, default `"whitelist"`, checked inside
`decide_apply_path`) are both user-configurable from the Settings page's
"Auto-Apply Behavior" panel — see the `decide_apply_path_node` and
`route_on_score`/`route_after_rewrite` bullets below.

State (`orchestrator.State`, a `TypedDict`): `job`, `cv_text`, `ats_result`,
`cv_rewritten`, `cv_path`, `tailored_ats_result`, `tailored_ats_explanation`,
`apply_path`, `status`, `result`. Each node function takes the state dict,
mutates it, and returns it.

- **`score_node`** calls `agents.ats_agent.compute_ats_score(cv_text,
  job_description)` and stores the result.
- **`route_on_score`** — the conditional edge — sends the job to
  `rewrite_cv` if `ats_result["score"] < config.FIT_THRESHOLD` (default
  `0.7`), otherwise to `decide_apply_path`. `FIT_THRESHOLD` is
  user-configurable from the Settings page's "Auto-Apply Behavior" panel,
  read fresh at call time (not cached), so a change applies to the next job
  scored — no restart needed.
- **`rewrite_node`** (only reached on low fit) calls
  `agents.cv_rewriter_agent.rewrite_cv()` — passing the hand-edited profile
  from `profile_store`, falling back to the extraction the scoring pass
  already built — then `save_cv_as_pdf()`, writes the output to
  `cv_output/cv_<job_id>.pdf`, re-scores `render_cv_text()`'s flattening of
  the same document via a second `compute_ats_score()` call, and sets
  `status = "cv_rewritten_notify_user"`.
- **`route_after_rewrite`** — a second conditional edge, opt-in via
  `config.AUTO_APPLY_ON_TAILORED_SCORE` (Settings page toggle, **off by
  default**). When off (the original, still-default behavior), the graph
  always ends here — **a low-fit job is never auto-applied to**, even with
  the rewritten CV; it's surfaced to the user (via the Dashboard/CV tab)
  instead. When on, a tailored CV whose re-scored `tailored_ats_result`
  clears `config.FIT_THRESHOLD` is routed to `decide_apply_path` — the same
  gate a naturally good-fit job goes through, so a rewritten CV still can't
  bypass whatever `config.AUTO_APPLY_MODE` currently allows. A tailored CV
  that still doesn't clear the threshold always ends here regardless of the
  flag.
- **`decide_apply_path_node`** — reached either from a good-fit job
  directly, or from `rewrite_cv` via `route_after_rewrite` above — calls
  `agents.apply_agent.decide_apply_path(job)`.
- **`route_on_apply_path`** sends the job to `auto_submit` or
  `draft_for_review` based on that decision.
- **`auto_submit_node`** — if `config.DRY_RUN` is true, just records intent
  (`status = "auto_submitted"`, `result["dry_run"] = True`) without calling
  anything real. If `DRY_RUN` is false, it calls
  `agents.apply_agent.auto_submit_greenhouse()` — which is a deliberate
  stub that raises `NotImplementedError` until someone hand-verifies a
  specific board's form fields and implements the real submit payload (see
  [Enabling auto-submit](setup.md#enabling-auto-submit) in the setup guide).
  Reaching this node at all already implies `decide_apply_path` approved it
  — `config.AUTO_APPLY_MODE` is `"any"`, or is `"whitelist"` and the source
  is in `WHITELISTED_SOURCES`.
- **`draft_node`** calls `agents.apply_agent.draft_for_review()`, which just
  packages the job URL, CV path, and an empty cover letter into a dict with
  `status = "pending_review"` — this is what shows up in the Dashboard's
  Applications table waiting for the user to send it manually via the Email
  tab.

## Agents: `agents/`

Each file in `agents/` is a stand-alone capability with plain functions (no
classes, no shared base) — the orchestrator and `jobs/` scripts import
exactly the functions they need. None of them touch the database directly
except `search_agent.py` (for `SearchSite` config) — everything else is
pure input-in / result-out, which is what makes the orchestrator's node
functions thin wrappers around them.

### `agents/llm.py` — provider abstraction

`get_llm(temperature=0.0)` is the one function every other agent calls
instead of instantiating a LangChain chat model directly — swapping
providers is a one-line `.env` change (`LLM_PROVIDER`), not a code change
anywhere else. Three providers, selected by `config.LLM_PROVIDER`:

| Provider | Class | Notes |
|---|---|---|
| `ollama` (default) | `ChatOllama` | Local, free, needs `ollama serve` running + the model pulled. |
| `gemini` | `ChatGoogleGenerativeAI` | Hosted, free tier, needs `GEMINI_API_KEY`. Model hardcoded to `gemini-1.5-flash`. |
| `openrouter` | `ChatOpenRouter` | One key in front of ~400 models with automatic cross-provider failover. Needs `OPENROUTER_API_KEY`. **Its free tier is metered differently from every other provider here:** model ids ending in `:free` are capped by *request count*, not tokens — 20/minute and 50/**day** on an unfunded account (1,000/day after $10 of credits). The orchestrator spends ~2–3 calls per job that clears the match gate, so 50/day is roughly 15–20 jobs. Paid ids have no request cap. Free ids also come and go, so a 404 means picking another from OpenRouter's catalogue. `max_tokens=4096` and the `gpt-oss` `reasoning_effort` handling mirror the Groq row below, since the underlying models are the same. Uses the dedicated `langchain-openrouter` package rather than the older `ChatOpenAI` + `base_url` override, which needs `langchain-openai` and loses OpenRouter's routing metadata. |
| `groq` | `ChatGroq` | Hosted, free tier, fastest of the three. Needs `GROQ_API_KEY`. `max_tokens=4096` is set explicitly — without a cap, a long prompt (full CV + job description) can exhaust Groq's default token budget before emitting any content. For `gpt-oss` models specifically, `reasoning_effort="low"` is also set, because those models bill hidden internal reasoning as completion tokens — verified directly that a trivial 2-line rewrite spent ~85% of its budget on invisible reasoning before this flag was added, sometimes returning `content=""` with `finish_reason="length"` and no error. |

All four are switchable live from the dashboard's Settings page as well as
`.env` (see `env_store.py` and `api.py`'s `/api/settings` endpoint).

Note the two hosted providers fail in opposite ways under load, which is
worth knowing when choosing between them for this workload: Groq runs out of
**tokens per day** (200k on the free tier — a single 250-job run once spent
~197k of it), while OpenRouter's free ids run out of **requests per day**
(50 unfunded). Token budgets favour many small calls; request budgets favour
few large ones. Ranking being LLM-free (see `agents/ranking_agent.py`) is
what keeps either from being hit by ordinary search volume.

### `agents/search_agent.py` — pulling jobs from every configured source

The largest file in `agents/` (23KB) because it fans out to seven different
free sources and normalizes/filters everything into one shape. Read
top-to-bottom, its job is: fetch raw job listings from wherever's
configured, filter them by title/seniority/age, cap how many are kept per
source, and tag each with which source it came from (used later by
`apply_agent.py` for whitelist checks).

**Sources, in the order `run_search()` queries them:**

1. **Greenhouse public API** (`search_greenhouse(board_token)`) — no key
   needed, hits `boards-api.greenhouse.io`. Board tokens come from
   `config.GREENHOUSE_BOARD_TOKENS` (`.env`) and from any dashboard-added
   `SearchSite` row whose URL was detected as a Greenhouse board.
2. **Lever public API** (`search_lever(company)`) — same idea, `.env`'s
   `LEVER_COMPANY_SLUGS` plus dashboard-added Lever sites.
3. **RemoteOK** (`search_remoteok(position)`) — RemoteOK's own free public
   JSON feed (`remoteok.com/api`), no key/auth needed. Always queried, no
   per-user config — the response's first element is RemoteOK's
   legal/attribution notice rather than a job, filtered out naturally by a
   `job.get("id") and job.get("position")` guard rather than a hardcoded
   "skip index 0". The endpoint has no real search parameter, so `position`
   narrows results client-side via `_matches_position` (see below).
4. **We Work Remotely** (`search_weworkremotely()`) — WWR publishes each job
   category as a plain RSS 2.0 feed (`weworkremotely.com/categories/
   remote-programming-jobs.rss` by default), parsed with the stdlib
   `xml.etree.ElementTree` rather than scraped as HTML. WWR titles each
   listing `"Company: Job Title"`; that's split on the first `": "` into
   separate `company`/`title` fields to match every other source's shape,
   falling back to the whole string as the title if a listing doesn't
   follow that convention.
5. **Google Sheets watchlist** (`search_from_watchlist()` /
   `read_watchlist_sheet()`) — an optional manually-curated sheet
   (`config.GOOGLE_SHEETS_ID`) with columns `company | role_keyword |
   greenhouse_board_token | lever_company_slug`; each row is searched via
   whichever of Greenhouse/Lever/SerpAPI applies to it. A single bad row
   (missing creds, bad slug) is skipped, not fatal to the rest.
6. **Dashboard-added sites** (`get_configured_sites()`, the `search_sites`
   table via `models.SearchSite`) — each row is dispatched based on
   `site_type`:
   - `greenhouse` / `lever` → reuse the API-backed functions above.
   - a `KNOWN_JOB_BOARD_TEMPLATES` key (`wuzzuf`, `bayt`, `simplyhired`,
     `wellfound`, `gulftalent`) → the per-template `build_url(position)`
     callable rebuilds a real, query-filtered search URL fresh every run
     (see below), then scrapes it with `search_generic_site()`.
   - anything else → `search_generic_site()` directly on the stored URL.
7. **SerpAPI / Google Jobs** (`search_serpapi(query)`) — optional, free tier
   100 searches/month, skipped entirely if `config.SERPAPI_KEY` is unset.
   Query is built from `seniority` + `position` combined.

RemoteOK and We Work Remotely are always-on structured sources like
Greenhouse/Lever rather than `SearchSite` rows: no per-user config, and no
way to remove them from the Search tab short of a code change, since (unlike
the `KNOWN_JOB_BOARD_TEMPLATES` sites) they were never added as rows in the
first place.

**`search_generic_site(url, position, max_candidates)`** is the fallback for
any career-page URL that isn't Greenhouse/Lever: it fetches the page,
collects same-domain `<a>` links whose href/text look job-related
(`JOB_LINK_KEYWORDS`), optionally narrows to ones matching `position`, caps
to `max_candidates` (or `GENERIC_SITE_MAX_CANDIDATES` = 15) since each
candidate costs a real HTTP fetch, then fetches each candidate page's text
as its "description." This is explicitly noisier than the API-backed paths —
treat results as leads, not a guaranteed feed — and the docstring notes to
check a site's Terms of Service before relying on it.

**`KNOWN_JOB_BOARD_TEMPLATES`** — `wuzzuf`, `bayt`, `simplyhired`,
`wellfound`, `gulftalent` — are job boards verified by hand (plain
unauthenticated HTTP GET returns real job links, server-rendered, no JS
needed) to be safely scrapable. Each has a `build_url(position)` lambda
rather than a naive `?q={query}` string, because query filtering behavior
turned out to differ per site:

- Wuzzuf's and SimplyHired's `?q=` genuinely filter server-side.
- Bayt's `?keyword=` silently ignores the query and serves unrelated
  generic listings — confirmed by testing (a search for "Ai Engineer"
  returned "Civil Rights Attorney"). Bayt's real filtering only works
  through its SEO slug pages (`/en/international/jobs/<slug>-jobs/`), so
  that's what `build_url` builds instead.
- GulfTalent's `?keyword=` has the same problem as Bayt originally did —
  confirmed by testing: it silently redirects to the same unfiltered "all
  jobs" listing regardless of the query. Rather than surface irrelevant
  jobs, its `build_url` ignores `position` entirely and always points at
  GulfTalent's own Software category page, leaning on `run_search()`'s
  global position-filter (applied to the combined result set at the end)
  to narrow it down — so unlike every other template here, this one is
  only useful while searching software-adjacent titles.
- Wellfound has no free-text query at all — it's a curated role taxonomy
  (`/role/r/<role-slug>`) — so `build_url` slugifies `position` into that
  URL shape. This matches Wellfound's taxonomy for common titles
  ("Software Engineer" → `software-engineer`, confirmed working, 1,827
  real results) but won't for unusual ones; an unmatched slug just returns
  an empty page rather than wrong results.

LinkedIn, Indeed, and NaukriGulf were tested the same way and all failed —
LinkedIn and Indeed block unauthenticated requests outright (HTTP 999 and
403 respectively), and NaukriGulf serves a JS-only shell with no listings
in the raw HTML — so all three are deliberately excluded; adding any of
them manually via the Search tab wouldn't work either.

**`seed_default_search_sites()`** inserts each `KNOWN_JOB_BOARD_TEMPLATES`
entry as an ordinary, deletable `SearchSite` row the first time it exists
(called from `db.init_db()`), tracked per-template-key against
`config.SEARCH_DEFAULT_SITES_SEEDED` (a set of already-seeded keys) rather
than "is the table empty" — the latter would silently never fire for a user
who'd already added their own sites before this feature existed. Per-key
tracking (rather than one global bool) also means a template added later —
e.g. `simplyhired`/`wellfound`/`gulftalent` landing in this file after an
install already had `wuzzuf`/`bayt` seeded under the old boolean flag —
still gets seeded on the next run instead of the old all-or-nothing flag
permanently blocking it. `config.py::_seeded_template_keys()` migrates a
legacy `SEARCH_DEFAULT_SITES_SEEDED=true` value to exactly `{"wuzzuf",
"bayt"}` (what "true" used to mean, back when those were the only two
templates), rather than treating it as "every template, including ones that
don't exist yet, is already seeded."

**Filtering, applied in this order** (order matters — see the code comment
on `_filter_relevant`):

1. `_filter_relevant()` — position and seniority
   (`SENIORITY_KEYWORDS` heuristics — e.g. "senior" / "sr." for Senior,
   "intern" for Intern; "mid" has no positive keyword list, it matches
   titles that hit *none* of the other levels' keywords), applied to each
   source's **raw, uncapped** results. Position matching (`_matches_position`)
   is word-based rather than a literal phrase match: a title matches if it
   contains every significant word from the query, in any order —
   searching "AI Engineer" (query words `{"ai", "engineer"}`) matches "AI
   Software Engineer", "AI/ML Software Engineer", "Gen AI Engineer", and
   "Gen AI/Agentic AI Engineer" alike, none of which contain "ai engineer"
   as one contiguous phrase. This replaced a literal substring check that
   silently missed exactly these cases.
2. `_cap()` — truncates to `max_results_per_site`, applied **after** step 1.
   This ordering was a deliberately fixed bug: capping before filtering on a
   545-job Greenhouse board (returned roughly alphabetically by title) once
   zeroed out all 38 real "Software Engineer" matches, because none of them
   happened to fall within the first 100 raw results.
3. `_within_max_age()` — applied last, across the whole combined result set.
   Only enforced where a real posted date can be extracted
   (`_extract_posted_at()`: Greenhouse's `first_published` ISO date, Lever's
   epoch-millisecond `createdAt`, or SerpAPI's relative text like "3 days
   ago" via `_parse_relative_date()`). Jobs with no extractable date are
   always kept, never excluded — this matters most for `search_generic_site`
   results, which have no structured date at all.

`WHITELISTABLE_SOURCES = {"greenhouse", "lever"}` is also defined here (not
in `apply_agent.py`) since it's fundamentally about what a *source* can
support, not about apply-path decision logic — `apply_agent.py` imports it.

### `agents/query_expansion_agent.py` — one Position → many role titles

`get_target_roles(position, cv_text)` turns one typed Position ("AI Engineer")
into the set of job titles that describe the same kind of role ("Machine
Learning Engineer", "Generative AI Engineer", "AI Software Engineer", …).
One LLM call, cached per Position string in `models.QueryExpansionCache`.

Why an LLM rather than a static title taxonomy, a hand-written synonym dict,
or embedding nearest-neighbors over a known-titles list: the expansions that
matter most are recent and compound role names ("Agentic AI Engineer", "Gen
AI Engineer") that pre-built taxonomies and models trained on older job
corpora are structurally bad at surfacing.

Three properties worth knowing:

- **The typed Position is always first and never dropped**, so expansion can
  only ever *widen* a search, never redirect it to the model's paraphrases of
  what the user asked for.
- **It's CV-aware.** Expanding "AI Engineer" generically gives generic
  synonyms; expanding it alongside a CV mentioning PyTorch and agent
  orchestration gives a list calibrated to that candidate. The CV hash is
  stored with the cache entry, so a materially different CV re-expands.
- **Every failure degrades to `[position]`** — an unreachable LLM,
  unparseable output, or `SEARCH_QUERY_EXPANSION=false` all fall back to
  exactly the pre-expansion behavior rather than failing the search run.

### `agents/embeddings.py` — CV ↔ job-description similarity

Deliberately a *separate* provider switch from `agents/llm.py`
(`config.EMBEDDING_PROVIDER`, not `LLM_PROVIDER`): Groq — one of the three
chat providers — has no embeddings endpoint at all, so a user on
`LLM_PROVIDER=groq` still needs an independent choice. `ollama` (default,
`qwen3-embedding:4b`, free forever, needs a one-time `ollama pull`) or
`gemini` (`gemini-embedding-001`, hosted free tier).

**Why `qwen3-embedding:4b`** over the obvious alternatives, for *this*
project specifically: 40K context and 100+ languages at 2.5 GB.
`nomic-embed-text` has ample context (8K) but is English-centric, and this
agent scrapes MENA boards (Wuzzuf, Bayt, GulfTalent) whose postings are
frequently Arabic or mixed-language — an English-only embedder scores those
near-randomly. `embeddinggemma:300m` is multilingual and tiny but capped at
2K context, and measured against this project's own stored jobs the 90th
percentile description is already ~1.9k tokens (longest ~2.2k), with the
generic scraper's whole-page text running far longer.

**Query-side instruction prefix.** Qwen3-Embedding is trained to take a task
instruction on the query only (`Instruct: {task}\nQuery: {text}`), with the
corpus embedded bare. Here the CV is the query — we're retrieving job
postings that match it — so `embed_cv()` applies
`config.EMBEDDING_QUERY_INSTRUCTION` and `embed_texts()` (job descriptions)
does not. This is plain string formatting done before the text reaches the
provider, which is why the feature needs no `transformers`/`torch`
dependency; `supports_instruction_prefix()` gates it to models actually
trained this way, since prepending it elsewhere would just add noise. The
instruction is part of the CV cache key, so editing the wording re-embeds.

**On the Gemini model name:** `text-embedding-004` was shut down on
2026-01-14 (and `embedding-001` on 2025-08-14); both now return
`404 NOT_FOUND` from `embedContent`. `gemini-embedding-001` is the
documented text-only replacement — `gemini-embedding-2` is the newer
multimodal model, unnecessary here since this only ever embeds plain text.
`active_model()` normalizes away a `models/` prefix, since both shapes appear
in Google's own docs and they must not produce two different cache keys for
the same model.

**Rate limits are handled, not just reported.** Gemini's free tier meters
embeddings per *minute* (`EmbedContentRequestsPerMinutePerUserPerProjectPerModel-FreeTier`,
100) and counts each content in a batch against it, so a run over a few
hundred job descriptions trips it. Two things keep that from killing the
semantic stage:

- `embed_texts` chunks at `config.EMBEDDING_BATCH_SIZE` (100, the API's own
  batch cap) and retries a rate-limited chunk after the delay **the API
  itself specifies** (`"Please retry in 18.049973242s"` / `retryDelay`),
  plus 1s of headroom — retrying at the exact boundary tends to race the
  server's window accounting and trip a second 429. Retries are bounded
  (4), so a genuinely exhausted *daily* quota surfaces as a real failure
  instead of stalling a run forever on a limit that won't clear.
- `ranking_agent.attach_semantic_scores()` embeds every job needing a score
  in **one batched pass** before the scoring loop. `score_job` previously
  embedded each job individually — one HTTP request per job, which is what
  exhausted the quota. Measured: ranking 200 jobs went from 200 requests to 2.

**The CV embedding cache is keyed on CV text + provider + model**, not text
alone. This matters more than it looks: models emit different
dimensionalities (`nomic-embed-text` 768, `gemini-embedding-001` 3072), and
`cosine_similarity` returns `0.0` on a length mismatch rather than raising —
so a text-only cache key would, after any provider or model switch, silently
score every job `0.0` on the semantic factor and rescue nothing, with no
error anywhere to explain why. The cached entry also records the provider,
model, and dimension count so a stale file is diagnosable by reading it.

`cosine_similarity()` is plain Python rather than numpy, and there's no vector
database: this app compares one CV against tens-to-low-hundreds of jobs per
run, nowhere near the scale that would justify FAISS/Chroma or a numpy
dependency the project doesn't otherwise have. Raw cosine ranges −1..1 but is
clamped to 0..1 so it's directly comparable to every other score here.

`embed_cv()` caches the CV's embedding in `data/cv_embedding_cache.json`,
keyed by a hash of the CV *text* — so replacing the CV invalidates it
automatically, with no explicit "clear cache" step. Only the current CV's
entry is kept; the file is rewritten wholesale rather than accumulating every
CV ever embedded.

### `agents/ranking_agent.py` — "is this job right for ME?"

The precision half of the two-stage matching pipeline, and a deliberately
*different question* from `ats_agent.py`'s score. That one asks "would my CV,
as written, survive **this employer's** ATS parser". This one asks "is this
job worth my time applying to" — and looks at signals the ATS score never
considers at all (location, salary, education, seniority). A job can score
well on one and badly on the other.

**Two entry points, matching the two stages:**

- **`semantic_retrieval_path(jobs, cv_embedding)`** (retrieval) — jobs whose
  *description* is semantically close to the CV regardless of title, above
  `config.CV_JOB_SIMILARITY_THRESHOLD`. This is what catches a "Backend
  Developer" posting for a "Software Engineer" search when the JD content
  genuinely fits — something the title-token path structurally cannot do.
  Callers pass only the jobs the token path **rejected** (`split_by_token_match`
  does that partition): re-embedding a job the token filter already accepted
  would spend a call confirming a decision that's already made, since the two
  paths are unioned anyway.

  **This only works because `run_search(include_title_mismatches=True)` keeps
  those jobs alive.** The first implementation didn't, and the semantic path
  was silently dead weight — `run_search`'s own title filter had already
  discarded every job the semantic retriever exists to rescue, so it could
  never recover anything. Caught by the very first debug report (zero
  `semantic` rows on the Retrieval sheet). Title mismatches are now carried
  forward, capped globally at `config.SEMANTIC_CANDIDATE_CAP` (default 200)
  since a single Greenhouse board can return 500+ jobs and each candidate
  costs an embedding call. Seniority mismatches are still dropped outright —
  that filter stays hard.
- **`rank_jobs(...)`** (ranking) — scores every candidate and returns them
  sorted best-first. It never drops anything; gating on
  `config.MATCH_SCORE_THRESHOLD` is the caller's decision (`jobs/daily_run.py`),
  kept separate so ranking stays a pure transformation.

**Ranking is LLM-free, deliberately.** It runs over *every* retrieved
candidate — hundreds per run — so an LLM call per job isn't affordable:
measured, a 250-job run consumed ~197k of Groq's 200k free-tier daily token
budget, all spent ranking jobs that mostly never get applied to. The skills
factor therefore inverts the question: instead of "what does this JD require,
and do I have it" (needs a model to read the JD), it asks "which of *my*
skills does this JD mention" — computable by intersecting the CV's own
vocabulary (`extract_cv_skill_terms`, which prefers an explicit `Skills:`
section) with the JD text. For "is this job right for me" that framing is
arguably the more direct one anyway. Real LLM extraction still happens once
per job in `ats_agent.py`, for the far smaller set that reaches the
orchestrator — and if a caller already has that list, `score_skills` accepts
it and uses the stronger measure instead.

**Embeddings are budgeted per run.** Gemini's free tier meters them at 100
per *minute* counted **per job description**, not per HTTP request — so
batching alone cannot get under it. Two limits apply together, and both
default **provider-aware** (an explicit `.env` value always wins):
`SEMANTIC_CANDIDATE_CAP` (90 on Gemini / 400 on Ollama) bounds the rescue
path, and `EMBEDDING_MAX_PER_RUN` (95 / 500) bounds the run as a whole,
because the
token-matched jobs need embedding too and the two together can exceed the
quota even when each is individually under it. Discovery gets first claim on
the budget (the semantic path finds jobs nothing else would); token-matched
jobs that miss out score neutral on that one factor, since they're already
known relevant by title. Nothing is ever dropped, and whatever was skipped is
recorded in the debug report. `rank_jobs` deliberately passes
`cv_embedding=None` into `score_job` for exactly this reason — leaving it set
re-enables a per-job embedding fallback that sails straight past the budget
(measured: a 95-embedding budget became 250 actual embeddings).

**Factor weights** — semantic similarity takes 20%, and the seven rule-based
factors keep their relative proportions scaled into the remaining 80%:

| Factor | Weight | Notes |
|---|---|---|
| Semantic CV/JD similarity | 20% | Reused from retrieval when already computed |
| Skills match | 32% | Reuses `ats_agent.extract_required_skills` / `keyword_overlap_score` |
| Experience match | 16% | Reuses `ats_agent`'s years extraction |
| Job title match | 12% | Token match against `target_roles` |
| Location | 8% | Remote-only sources score full; Greenhouse's nested location object is read |
| Education | 4% | Highest degree in CV vs. stated requirement |
| Salary | 4% | Rewards *disclosure* — without a configured target figure, that's the only honest signal |
| Seniority | 4% | Title vs. the configured level |

**Missing data scores NEUTRAL (0.5), never zero.** Location and salary are
absent from most of this project's sources — the generic scraper and job-board
templates usually yield only a title and raw page text, with no structured
salary field at all — so scoring "not stated" as 0 would systematically punish
jobs for how their *source* happens to be structured rather than for anything
about the job. Every factor reports whether its data was actually found.

### `agents/search_trace.py` — per-run Excel debug reports

Records what happened at every pipeline stage and writes it to a timestamped
`.xlsx` under `data/search_reports/` (config: `SEARCH_DEBUG_REPORTS`,
`SEARCH_REPORT_DIR`, `SEARCH_REPORT_KEEP`). Eight sheets:

| Sheet | Contents |
|---|---|
| Summary | Run metadata, the settings in force, and the whole funnel — raw → filtered → deduped → ranked → processed |
| Query Expansion | Every role phrase searched, and whether it came from the LLM or the cache |
| Sources | Per source *and per expanded phrase*: raw returned, surviving each filter, dropped by filter vs. by cap, seconds, error |
| Retrieval | Every job seen, which path handled it (token / semantic / role1), kept or dropped, and the reason |
| Ranking | Every candidate × every factor: raw score, weight, weighted contribution, and the evidence string |
| Match Gate | Each ranked job's score against the threshold, processed or held back |
| Source Errors | Every failed source with its exception |
| Stage Timings | Wall-clock seconds per stage |

Two rules this module follows strictly. **Tracing never changes behavior** —
a `NullTrace` makes every call a no-op when reporting is off, the stage timer
never swallows an exception, and a failed report write is caught and logged
rather than failing a search run that already did its real work. And **the
Summary sheet's counts are formulas, not Python-computed literals**
(`COUNTIFS(Retrieval!...)` etc., with `fullCalcOnLoad` set so Excel evaluates
them on open), so filtering a detail sheet doesn't leave a stale number behind.

This report earned its keep immediately: the first one generated showed *zero*
rows on the `semantic` path, which is what surfaced the recall bug described
under `include_title_mismatches` below.

### `agents/ats_agent.py` — scoring fit

`compute_ats_score(cv_text, job_description, required_skills=None)` is the
entry point the orchestrator's `score_node` calls (and, separately, the
entry point `rewrite_node` calls a second time to re-score a tailored CV —
see below). It scores four independently-computed, weighted pillars rather
than a single hybrid number, modeled directly on how real-world ATS
scoring is broken down: **Keyword Match (45%)**, **Formatting &
Parsability (22%)**, **Section Completeness (18%)**, and **Experience
Alignment (15%)** — each weight sits inside the range that research into
real ATS scoring reports for that pillar (`agents/ats_agent.py::WEIGHTS`,
asserted to sum to 1.0 at import time).

1. **Keyword Match** — `extract_required_skills()` (one LLM call asking for
   a JSON array of required/preferred skills from the JD; `_safe_json_list()`
   regex-extracts a `[...]` block even if the model wraps it in prose or a
   code fence, falling back to an empty list on a parse failure) feeds
   `keyword_overlap_score()`, the deterministic fraction of those skills
   that appear as a substring in the (lowercased) CV text.
2. **Formatting & Parsability** — fully deterministic, no LLM call.
   `formatting_score()` can't inspect the original file's layout (columns,
   tables, fonts) since `cv_text` has already been flattened to plain text
   by `cv_parser` before scoring ever runs; instead it checks signals that
   correlate with clean, parser-friendly formatting: standard section
   headers presented as short, distinct lines (`_header_like_lines()`), a
   healthy 150–1200 word length, and consistent bullet usage (3+ bulleted
   lines). Returns specific issues (e.g. "CV text is short (91 words)") for
   the explanation, not just a number.
3. **Section Completeness** — also deterministic. `section_completeness_score()`
   checks for the *content* five standard sections should contain — contact
   info (email/phone regex), a summary/objective, dated work experience
   (2+ four-digit years or "present"), an education section (degree
   keywords like "bachelor"/"university"), and a skills section (the word
   "skill(s)" anywhere) — deliberately distinct from formatting's
   header-line check, since a CV can have real content without a perfectly
   labeled header, or a clean header with nothing behind it.
4. **Experience Alignment** — `experience_alignment_score()` blends
   `llm_fit_score()` (a second LLM call, unchanged from the original
   design: asks for a single 0–1 overall-fit number given the full CV and
   JD) with a best-effort years-of-experience check: `_extract_required_years()`
   regexes for a number like "5+ years" near the word "experience" in the
   JD, `_estimate_cv_experience_years()` spans the earliest-to-latest
   4-digit year mentioned anywhere in the CV as a rough proxy for career
   length. If the JD states no number, or the CV has no years at all, this
   pillar is just the LLM score; otherwise it's `0.7 * llm_score + 0.3 *
   years_ratio`.

The final `score` is the weighted sum of the four pillar scores. The
returned dict keeps `keyword_score`/`llm_score`/`missing_skills`/
`required_skills` for backward compatibility with existing callers, and
adds `breakdown` (per-pillar score/weight/evidence) and `explanation` — a
plain-English, fully deterministic (no extra LLM call)
`build_score_explanation()` rendering of the breakdown, e.g. "Keyword
Match — 58% (weight 45%): matched 7 of 12 required skills... Missing:
Kubernetes, Terraform." This is what the Dashboard shows under every
application's "Why?" link — see
[Explaining and re-scoring the tailored CV](#explaining-and-re-scoring-the-tailored-cv) below.

Two LLM calls per job scored (skill extraction + fit judgment) is the main
cost driver of a search run — this is why "Max results per site" exists on
the Search tab, to bound how many jobs get this treatment in one run. A
low-fit job that goes through the CV rewrite path costs one more LLM call
than before (see below) to re-score the tailored CV's contextual fit.

#### Explaining and re-scoring the tailored CV

`orchestrator.py::rewrite_node` calls `compute_ats_score()` a second time,
against the freshly rewritten CV text and the same job description — but
passes `required_skills=state["ats_result"]["required_skills"]` through
from the original score, so this second call skips the skill-extraction
LLM call (the JD hasn't changed) and only pays for a fresh
`llm_fit_score()` call. The result is stored as `tailored_ats_result`
alongside a `tailored_ats_explanation` built by
`build_improvement_explanation()` — another fully deterministic,
no-LLM-call function that diffs the two breakdowns pillar by pillar and
calls out which previously-missing skills the rewrite was able to
incorporate (by comparing `missing_skills` before and after — never by
asking the LLM to explain itself, since that could invent a justification
that doesn't match what the rewrite actually did). Both `ats_result` and
`tailored_ats_result` flow through to `jobs/daily_run.py::_finalize_job`,
which persists all five new `Application` columns
(`ats_breakdown`/`ats_explanation`/`tailored_ats_score`/
`tailored_ats_breakdown`/`tailored_ats_explanation`) — `ats_*` is set for
*every* application (`score_node` runs unconditionally before the
rewrite/auto-submit/draft branch), while `tailored_*` stays `NULL` unless
that job went through the rewrite path.

#### The requirements-based scoring engine — the only one

Everything above describes the four-pillar scorer, which was **removed on
2026-08-26**. `compute_requirements_score()` is what the scheduler, the
dashboard and the bench all run: structured requirement extraction against
the job description, the user's stored profile as the evidence source,
deterministic evidence matching (exact/alias/subset, plus a guarded
batched-LLM entailment pass for anything unmatched), and a deterministic
weighted score — with ATS parse-compatibility split out as its own CV-only
Pass/Warning/Fail check rather than folded into the per-job number.
`compute_ats_score()` is now a thin name over it, kept because every caller
reaches scoring through it.

Removed with the engine: `config.SCORING_ENGINE`, the bench's engine
selector and its side-by-side comparison, `keyword_overlap_score`,
`llm_fit_score`, `_estimate_cv_experience_years` and the pillar weights.
Kept, because they answer questions that are still asked:
`formatting_score()` and `section_completeness_score()` now feed only
`compute_ats_compatibility()`, and `_extract_required_years()` still reads
"5+ years" out of a posting for the ranking stage.

Two consequences live outside `ats_agent.py`:

- **`Application.scoring_engine`** records which engine produced each row's
  `ats_score`/`ats_breakdown`. `NULL` on pre-cutover rows means legacy (the
  API defaults it), and `frontend/why-modal.js` still renders those rows —
  labelled on screen as scored by a retired engine and not comparable with
  newer numbers. The breakdowns have incompatible shapes, and a legacy row
  read under the requirements renderer renders *empty* rather than erroring,
  which is the kind of thing nobody notices for weeks.
- **`orchestrator._rescore_kwargs()`** decides what the tailored-CV re-score
  reuses: the whole structured extraction (a flat list of names silently fails its
  `extracted.get("requirements")` check and triggers a re-extraction), the
  already-built CV profile, and the rewritten text as `evidence_text` — the
  last two removing a second large CV-parsing call on a path that had
  previously failed against Groq's per-minute token limit *after* the rewrite
  was already paid for. `tests/test_engine_cutover.py` asserts the counts.
- **`FIT_THRESHOLD` was left at 0.7**, uncalibrated for the new range, on the
  reasoning that an unreachable threshold reproduces existing behaviour while
  a too-low one starts auto-applying to mismatched jobs.

See [scoring.md](scoring.md) for the full pipeline, the credit/weight tables,
and a worked example on a real posting from the database.

### `agents/cv_rewriter_agent.py` — tailoring the CV for low-fit jobs

Only reached when `ats_result["score"] < 0.7`. Two responsibilities, now
split across two modules: decide what the tailored CV should say
(`cv_rewriter_agent.py`), and lay it out (`cv_render.py`).

**`rewrite_cv(cv_text, job_description, missing_skills, profile=None)`** — one
LLM call (`temperature=0.3`, the only agent that doesn't use `0.0`, since some
rephrasing variety is wanted here) that returns **JSON, not a CV**. The model
is given the stored profile with an index on every experience entry and every
project, and answers with refs plus rewritten bullets:

    {"summary": "...",
     "experience": [{"ref": 0, "bullets": ["..."]}],
     "projects":   [{"ref": 2, "bullets": ["..."]}],
     "skills":     [{"category": "AI/LLM", "items": ["..."]}]}

Contact details, employers, dates, locations, project URLs and degrees are
never sent to the model and never come back from it — `build_document()`
merges them in afterwards from `profile_store`, which is what makes a
fabricated employer or a moved date structurally impossible rather than
merely discouraged. `build_document()` also drops any skill that appears
neither in the profile nor in the CV text, drops anything on the
`missing_skills` list, and flags any bullet whose numbers appear nowhere in
that entry's source bullets. Those findings are returned as
`document["integrity_warnings"]` and surfaced in the debug bench rather than
silently repaired.

`profile=None` (the user has never run an extraction on the Profile page)
falls back to the previous plain-text path, since there is nothing to
reconcile against. An unparseable JSON response falls back to returning the
raw text, which the renderer still lays out with its legacy line-based
layout — a worse CV, but not a discarded one. If the LLM returns empty
content (seen in practice with Groq's `gpt-oss` models running out of their
reasoning token budget — see `llm.py` above), this raises a `RuntimeError`
with an actionable message instead of silently writing a blank PDF.

### One profile, every reader

The CV page uploads a file; that upload extracts and REPLACES the stored
profile (`api._extract_and_store_profile`, best-effort — the file is saved and
the upload succeeds even when the model call fails, with the reason shown on
the page). The Profile page is where the user corrects and extends that
record. Everything downstream then reads the profile, not the file:

- **searching** — `query_expansion_agent.get_target_roles(position,
  candidate_text=…)` calibrates the role list to the profile, and
  `embeddings.embed_candidate()` builds the semantic query vector from it.
  Both caches key on that text's content, so an edit on the Profile page
  re-expands and re-embeds rather than reusing an answer calibrated to the
  old record. This is the most expensive stage to read a stale document in:
  it decides which jobs are ever seen.
- **ranking** — every factor. Skills through `skill_matching.find_term` over
  the profile, experience through `cv_profile.professional_years`, education
  from the profile's structured degree entries. `rank_jobs` loads the profile
  once per run and threads it down; the stage stays LLM-free.
- **scoring** — `orchestrator.score_node` and the debug bench both pass the
  stored profile into `compute_ats_score`, so requirement matching, the skill
  gap and `missing_skills` describe what the candidate HAS, not what one
  snapshot happened to say. A gap the user closed on the Profile page stops
  being reported.
- **targeting and tailoring** — `build_targets` and `build_document` work
  from the profile.
- **the CV file itself** is read for exactly two things now, both genuinely
  properties of the document rather than of the candidate:
  `compute_ats_compatibility` (can a parser read it?) and the fallback for a
  run before anything has been extracted. `cv_profile.profile_text()` is the
  shared rendering every other stage searches.

`config.ATS_SCORE_MODE` decides how many numbers the user sees. `both` scores
the profile as it stands, tailors only below `FIT_THRESHOLD`, and reports the
delta. `tailored_only` reports one number — which necessarily means every job
is tailored, since there is no baseline left to gate on, at one model call per
job. The requirement match runs either way: it is where the gap, the missing
skills and the tailoring targets come from, so the setting governs what is
reported and gated on, never whether the candidate is examined.

### `agents/cv_profile.py` — reading the skills back out of the work

`demonstrated_skills(profile)` answers the question the skills row cannot:
which skills does the dated work actually evidence? It scans experience and
project spans only — never the skills list, whose whole problem is that it
asserts without context — over the matcher's existing vocabulary, and
returns each skill with the line that earned it and how: **direct** (the
span names it) or **implied** (the span names something that requires it —
FastAPI, so Python; PostgreSQL, so SQL). No new table and no LLM call: the
alias, hyponym and prerequisite tables already are the vocabulary, and
anything outside them is left alone rather than guessed at. `debug_ats`
reports it beside ATS compatibility, since like compatibility it is a
property of the profile rather than of any one job.

### `agents/cv_targeting.py` — writing the job's own vocabulary

A CV can describe exactly the right work in the wrong words. The job asks for
"Deep Learning" and the CV says "CNN"; it asks for "SQL" and the CV says
"PostgreSQL"; it writes "Large Language Models (LLM)" and the CV only ever
writes "LLM". The scorer already knows these are the same claim — that is what
`skill_matching.HYPONYMS` and `skill_matching.IMPLIED_BY` are for — but it
pays them at a discount: `subset, demonstrated` is 0.80 and
`implied, demonstrated` 0.90 where `exact, demonstrated` is 1.00. The points
were being lost to wording alone.

**`build_targets(profile, requirement_results=…, missing_skills=…)`** turns
that gap into instructions. For each requirement not already earning full
credit, it finds the entry whose own bullets contain a term the entailment
tables connect to it, and emits a **bridge**: "experience[1] says 'cnn',
which is a kind of 'Deep Learning' — keep 'cnn' and work the job's wording
into the same bullet", or, for a prerequisite, "experience[0] says
'fastapi', which is not used without 'Python' — name Python in the same
bullet as the thing it was built with." A second kind, **form**, fires when the entry
and the job use the two halves of an acronym pair, and asks for the full form
once and the short form elsewhere; that one is worth nothing to this scorer,
which treats them as aliases, and everything to an ATS on the other side doing
literal string matching.

The direction of the whole design is the point. The obvious alternative —
hand the model the job description and say "use its terminology" — produces
exactly the fabrication this pipeline exists to prevent, because a model told
to match a posting's vocabulary writes "Kubernetes" into a CV that only ever
used Docker. So the bridges are enumerated from the tables instead, each one
naming the entry that holds the evidence, and the model is told which of the
job's words apply and where rather than being asked. `build_document` then
re-checks every job term that appears in a rewritten bullet but not in that
entry's source: a term on the missing-skills list takes its bullet with it, a
term the tables can't connect to that entry is reported. The summary is
checked the same way against the whole profile.

Measured on a real profile against a real posting: 0.6375 → 0.6975, entirely
from wording — Deep Learning and SQL each 0.80 → 1.00, Kubernetes untouched at
0. `tests/test_cv_targeting.py` pins both halves, including the score itself.

`listed_only()` reports the requirements the CV only NAMES — PyTorch in a
skills list, in no role. Since 2026-08-27 it no longer counts a skill the
work implies: Python listed in the keyword row with a FastAPI bullet on
record is evidenced, and telling the candidate to go and prove it would be
advice about a gap that isn't there. A rewrite cannot honestly move those into a role, so
they surface in the bench as advice: only the candidate knows whether that
internship used PyTorch, and saying so on the Profile page is worth 35% of
that requirement.

### `agents/cv_render.py` — laying the tailored CV out

**`save_cv_as_pdf(document, output_path)`** renders the document with
`reportlab` in the blueprint layout: headline and name centered in a navy
accent color (`CV_ACCENT_COLOR = "#1F3A5F"`, sampled directly from the
original CV's own divider-rule color via `pdfplumber`), a muted contact line
with clickable LinkedIn/GitHub, section headers each followed by a full-width
rule, and one two-cell table row per entry so **dates and location sit
right-aligned on the same line as the job title** — the thing plain text
could not express, and most of the reason tailored CVs used to run to two
pages. Bullets carry `**bold**` spans converted to `<b>` after XML-escaping
(never before), and project links render as real PDF link annotations.

One page is a target, not a hope, and the fit runs in three steps. First,
cut only as much as the FLOOR density needs: `_trim_step()` removes one thing
at a time in a fixed order — the optional footnote, then bullets off the
projects the model itself ranked lowest, then whole trailing projects, then
experience bullets down to a floor of two. A job or a degree is never
dropped, because that leaves an unexplained gap. Second, set the type as
large as it will go, re-running `FIT_DENSITIES` from the top (largest first)
so that whatever step one removed can buy back a comfortable size rather than
leaving the page set at the minimum. Third,
`_section_gap_that_fills_the_page()` binary-searches the largest per-section
gap that still fits, so leftover vertical space is spread between sections
instead of pooling as an inch of white space at the bottom.

The page geometry is measured, not chosen. `PAGE_MARGIN_X = 31.0`,
`BULLET_GLYPH_INDENT = 4.5` and `BULLET_TEXT_INDENT = 16.0` come from running
pdfplumber over the user's own `cv/current_cv.pdf`: every line of text on it
starts at x=31.0, its bullet dots at 35.5 and their text at 46.9, its rules
run 29.5→582.5, and its content reaches y=777.7. Those margins are narrow by
word-processor defaults and they are most of why that CV holds a page's worth
of content — a tailored CV expected to hold the same amount has to use the
same page.

Everything on the page shares one left edge, and that is load-bearing enough
to have its own test: the frame `SimpleDocTemplate` builds pads its content
by 6pt a side, so a table set to `doc.width` is WIDER than the content area,
and `Table`'s default `hAlign="CENTER"` then nudges every entry row 6pt left
of the section headers above it. `_doc_width()` accounts for
`_FRAME_PADDING`, `_new_doc()` subtracts half of it from each margin so the
first text lands exactly on `PAGE_MARGIN_X`, and the tables are explicitly
`hAlign="LEFT"`. `test_page_geometry_matches_the_users_own_cv` asserts that
below the header block, every line on the page begins on the margin, the
bullet dot indent, or the bullet text indent — and nothing else.

**`render_cv_text(document)`** flattens the same document to plain text with
ALL-CAPS section headers. This is not a convenience: rescoring reads evidence
structurally out of it via `cv_profile.spans_from_text()` instead of paying
for a second parse, so the headers have to stay ones that reader recognizes.
Both entry points accept a plain string and pass it through unchanged, which
is what keeps the degraded path (and every test that patches `rewrite_cv`
with a string) working.

Helvetica throughout rather than the original's exact font, since embedding
non-free fonts (e.g. Calibri) isn't viable — the goal is matching the
dominant visual signature, not pixel-identical reproduction.

`_sanitize_for_pdf_font()` / `_PDF_UNSAFE_PUNCTUATION` exist because
reportlab's base Helvetica font only supports the WinAnsi character set, and
LLMs routinely emit "typographically correct" Unicode punctuation (Unicode
hyphen variants, the minus sign) that silently falls back to a
ZapfDingbats/Symbol glyph instead of erroring — rendering as a solid black
square in the output PDF. This was a real, reproduced bug (confirmed by
building a test PDF and checking which font reportlab actually used
per-glyph), and the fix normalizes those characters to plain ASCII (plus a
few other Unicode punctuation marks that render fine but are normalized
anyway for consistency/defense) before layout.

`save_cv_as_docx()` also exists (renders the same text into a `.docx` via
`python-docx`) but isn't currently wired into the orchestrator — only the
PDF path is.

### `agents/apply_agent.py` — the auto-apply gate

`decide_apply_path(job)` is the single function every job's apply decision
runs through, and it's entirely driven by `config.AUTO_APPLY_MODE` (Settings
page's "Auto-Apply Behavior" panel), one of three string values:

1. **`"off"`** — always `"draft_for_review"`. Nothing ever auto-submits.
2. **`"any"`** — always `"auto_submit"`, regardless of source. This is a
   deliberate, explicit opt-in escape hatch from the whitelist-only default
   below. **Caveat, stated plainly:** `auto_submit_greenhouse()` (see below)
   is still a Greenhouse-specific stub, so choosing `"any"` changes
   *routing* — which jobs reach the `auto_submit` node — but can't make a
   non-Greenhouse job actually get submitted until that function is
   generalized or a per-site submitter is built. With `DRY_RUN=true` (the
   default) this mode is harmless and just logs "would have auto-submitted"
   intent for every job instead of only whitelisted ones.
3. **`"whitelist"`** — (default, and the original pre-mode design) mirrors
   the project's original safety rule: builds a `"source:identifier"` key
   via `_job_whitelist_key()` (identifier is the job's board token, company
   slug, or watchlist company, whichever is present), and returns
   `"auto_submit"` only if that exact key *or* the bare source name is in
   `config.WHITELISTED_SOURCES` *and* `job["source"]` is in
   `search_agent.WHITELISTABLE_SOURCES` (`{"greenhouse", "lever"}`) — no
   other source type can ever auto-submit in this mode, full stop. Anything
   else → `"draft_for_review"`.

`config.py` itself falls back to `"whitelist"` for any unrecognized
`.env` value (via the shared `_choice()` helper), so a typo or hand-edit
can't put the app into an undefined auto-apply state.

This function is the single choke point both `orchestrator.py` entry paths
funnel through — a naturally good-fit job via `route_on_score`, and (only if
`config.AUTO_APPLY_ON_TAILORED_SCORE` is on) a rewritten CV that cleared
`config.FIT_THRESHOLD` via `route_after_rewrite` — so neither path can
bypass whatever mode is currently set.

**`auto_submit_greenhouse()`** is an intentional stub that always raises
`NotImplementedError`. The docstring explains why it can't be generic:
different Greenhouse/Lever boards define different custom application
fields (screening questions, EEO fields), so there's no one payload that
works everywhere — a real implementation has to be hand-verified per board
before that board is added to the whitelist. This is the one piece of the
system the README explicitly calls out as meant to be implemented and
tested per employer, not automated blindly.

**`draft_for_review(job, cv_path, cover_letter)`** is the far more commonly
hit path (every non-whitelisted source, which by default is *everything*
since `WHITELISTED_SOURCES` starts empty) — it just packages the job URL,
CV path, and cover letter into a dict with `status: "pending_review"`. This
becomes an `Application` row the Dashboard shows and the Email tab can send.

### `agents/email_agent.py` — sending drafted applications

Gmail API via OAuth (the user's own Gmail account, no per-email cost,
replacing a paid SendGrid option from the original design). Three concerns:

- **Auth state without side effects.** `credentials_configured()` just
  checks whether `config.GMAIL_CREDENTIALS_JSON` (the OAuth client file
  downloaded from Google Cloud Console) exists on disk.
  `is_authenticated()` checks whether a cached, still-usable token exists at
  `config.GMAIL_TOKEN_JSON` — used by `GET /api/email/status` so the
  frontend can show "Connected" without popping open a browser window just
  to check.
- **`get_gmail_service()`** does the actual OAuth dance: loads a cached
  token if present, refreshes it if expired-but-refreshable, otherwise runs
  `InstalledAppFlow.run_local_server()` which opens a real browser tab for
  consent. This blocks the calling thread until the user finishes (or
  abandons) the flow, bounded by `GMAIL_AUTH_TIMEOUT_SECONDS` (180s) so an
  abandoned tab can't hang a request forever — `api.py` runs this inside
  FastAPI's threadpool for exactly that reason. A successful flow writes the
  token to `GMAIL_TOKEN_JSON` so future calls skip the browser entirely.
- **`send_application_email(to_email, subject, body, cv_path)`** — respects
  the global `config.DRY_RUN` flag: if true, returns a dict describing the
  intended send without touching Gmail at all. If false, builds a
  `MIMEMultipart` message with the CV file attached and sends it via
  `service.users().messages().send()`. Every call site (`api.py`'s email
  endpoint) wraps this in the `EmailLog` bookkeeping regardless of outcome,
  so the Email tab's history is a real log, not just a cache of the last
  session.

### `agents/reporter_agent.py` — report delivery + daily summary text

Reports are emailed through the same Gmail connection that sends
applications (it replaced an earlier Telegram bot: one account to connect,
and the reports land where job replies do). Two functions:

- **`send_report(subject, report_text)`** — calls
  `email_agent.send_report_email()`, which has the same `DRY_RUN`
  short-circuit as application emails and sends to `REPORT_EMAIL_TO` (blank =
  your own address). Returns an `{"error": ...}` dict (not an exception) when
  Gmail isn't connected, which is what `jobs/daily_report.py` and
  `jobs/weekly_news.py` check to decide `status = "failed"` for the
  `ReportLog` row.
- **`build_daily_report(applications_today)`** — pure text formatting, one
  bullet line per application (`title at company (status)`), or a fixed
  "No applications submitted today" string if the list is empty.

### `agents/news_agent.py` — weekly field news

Also SerpAPI-based (Google News engine this time), also free-tier and
optional. **`fetch_news(field)`** returns `[]` immediately if
`config.SERPAPI_KEY` is unset — no error, just nothing to summarize.
Otherwise fetches up to 15 results. **`summarize_news(field, articles)`**
hands the title+snippet of each article to the LLM with a prompt asking for
a themed, 5–8 bullet-point summary; returns a canned "no news fetched"
string if `articles` is empty rather than making an LLM call with nothing to
summarize.

### `agents/skill_gap_agent.py` — aggregating missing skills

The smallest file in `agents/` (711 bytes) and the only one with zero
external dependencies (no LLM call, no HTTP request) — pure aggregation.
**`summarize_skill_gaps(job_scores)`** takes a list of dicts (each with a
`missing_skills` list, as produced by `ats_agent.compute_ats_score()`),
flattens and counts every missing skill across all of them with
`collections.Counter`, and formats the top 10 as `"- <skill>: missing in N
of M jobs reviewed"`. Note this function isn't currently called from any
`jobs/` script directly — the Dashboard's skill-gap display is built from
the `SkillGap` table rows (one per job, written by `daily_run.py`) rather
than calling this aggregator live; it exists as the shared summarization
logic `api.py` could call, or that a future job could call to feed straight
into a report.

## Supporting modules (referenced above, not the focus of this doc)

These live in the project root, not in `agents/` or `jobs/`, but every
agent and job depends on them:

- **`config.py`** — the only place `os.getenv()` is called; every other
  module imports typed values from here instead of reading `.env` directly.
- **`db.py`** — SQLAlchemy engine/session setup (SQLite,
  `expire_on_commit=False` so rows built inside a `with get_session()` block
  are still readable after it exits). `init_db()` creates tables, then calls
  `_migrate_schema()` — `Base.metadata.create_all()` only creates *missing
  tables*, so it silently does nothing for a table that already exists with
  an older column set (every real user's database, each time a new field is
  added to a model). `_migrate_schema()` runs guarded `ALTER TABLE ADD
  COLUMN` statements instead, checking `PRAGMA table_info` first so it's a
  no-op both on a fresh database (already has every column from
  `create_all()`) and on a second run against the same old database. Then
  `init_db()` calls `search_agent.seed_default_search_sites()`. The engine is created with
  `connect_args={"timeout": 30}` and a `connect` event listener sets
  `PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout=30000` on every
  connection — both layers exist because this app routinely has multiple
  processes open on the same SQLite file at once (`api.py` + `scheduler.py`,
  both started by `run.bat`; a manual CLI run on top of either), and
  SQLite's defaults (`busy_timeout=0`, rollback-journal mode locking the
  whole file per writer) turned real overlap between them into immediate
  `database is locked` failures instead of a brief wait. See the
  `jobs/daily_run.py` section above for the other half of this fix — a
  long-held transaction can still exceed even a generous timeout, which is
  why that file also splits its writes into short transactions rather than
  relying on this alone.
- **`models.py`** — the SQLAlchemy schema: `Job`, `Application`,
  `SkillGap`, `NewsDigest`, `EmailLog`, `SearchSite`, `ReportLog`. `Job.url`
  is the unique dedupe key referenced throughout `jobs/`.
- **`cv_parser.py`** — `parse_cv()` (PDF via `pdfplumber`, `.docx` via
  `python-docx`) and `find_default_cv()` (looks for
  `cv/current_cv.pdf`/`.docx`), used by every entry point in `jobs/` that
  needs CV text.
- **`env_store.py`** — line-preserving `.env` reader/writer used by
  `api.py`'s Settings endpoint, so saving a setting doesn't clobber comments
  or unrelated keys.
- **`orchestrator.py`** — see [Orchestrator](#orchestrator-the-per-job-state-machine)
  above.

## Data model quick reference

| Table | Written by | Purpose |
|---|---|---|
| `jobs` | `jobs/daily_run.py`, `jobs/apply_from_link.py` | One row per job seen, ever. `url` is the dedupe key. |
| `applications` | `jobs/daily_run.py` (`_process_one_job`) | One row per job that made it through the orchestrator — status tracks `scored_low` / `pending_review` / `auto_submitted` / etc. `ats_score`/`ats_breakdown`/`ats_explanation` are set for every row; `tailored_ats_score`/`tailored_ats_breakdown`/`tailored_ats_explanation` only for rows that went through the CV-rewrite path. `match_score`/`match_breakdown` come from the ranking stage that ran *before* the orchestrator, so one row carries both "is this job right for me" and "would my CV pass this employer's ATS". |
| `skill_gaps` | `jobs/daily_run.py` | One row per job with missing skills, JSON-encoded list. |
| `news_digests` | `jobs/weekly_news.py` | One row per weekly digest generated. |
| `email_logs` | `api.py`'s email-send endpoint | One row per send *attempt* (sent/dry_run/failed), not just successes. |
| `search_sites` | dashboard Search tab, seeded by `search_agent.seed_default_search_sites()` | User-managed extra sites to search, beyond `.env`'s Greenhouse/Lever lists. |
| `report_logs` | `jobs/daily_report.py`, `jobs/weekly_news.py` | One row per report send attempt (daily or weekly). |
| `query_expansion_cache` | `agents/query_expansion_agent.py` | One row per distinct Position searched with — the LLM-expanded role list, plus the CV hash it was calibrated to. Avoids re-asking daily for an unchanged Position. |

## Safety mechanisms, and where they live in the code

- **Auto-apply gate** — enforced in exactly one place,
  `agents/apply_agent.py::decide_apply_path`. Nothing else in the codebase
  can cause a real submission. It's governed by `config.AUTO_APPLY_MODE`
  (Settings page → "Auto-Apply Behavior"; `"off"` / `"any"` / `"whitelist"`,
  default `"whitelist"` — the original, still-recommended whitelist-only
  behavior). `config.AUTO_APPLY_ON_TAILORED_SCORE` (also in that panel, off
  by default) sits on top without weakening this: it only controls whether a
  rewritten CV's tailored score is *allowed to reach* `decide_apply_path` at
  all via `orchestrator.py::route_after_rewrite` — it still has to pass the
  identical `AUTO_APPLY_MODE` check as any other job once it gets there.
  `config.FIT_THRESHOLD` (also Settings-page-configurable, default `0.7`) is
  a routing input, not a safety gate itself — it decides which jobs get a CV
  rewrite, not which jobs can auto-submit.
- **Dry-run** — checked independently in three places that each perform a
  real external side effect: `orchestrator.py::auto_submit_node`,
  `agents/email_agent.py::send_application_email`, and
  `agents/email_agent.py::send_report_email`. There's no single
  global "dry-run wrapper" — each side-effecting function checks
  `config.DRY_RUN` itself.
- **Never fabricate CV content** — asked for in the system prompt of
  `agents/cv_rewriter_agent.py::rewrite_cv`, and enforced in code by
  `build_document()`: facts are merged in from the stored profile rather than
  taken from the model, an entry referring to a role or project that isn't in
  the profile is dropped, skills absent from the profile/CV and skills on the
  `missing_skills` list are filtered out, and bullets citing numbers with no
  source are reported in `integrity_warnings`. What remains a prompting
  guarantee is the wording of a bullet itself — that a rewritten bullet still
  means what the source bullet meant.
- **Idempotency** — `jobs/daily_run.py::_job_exists` (and the equivalent
  check in `apply_from_link.py`) — a job's URL is checked against the `jobs`
  table before any processing happens, so re-running the search job never
  double-processes the same posting. This also covers jobs that failed
  mid-processing: `_delete_job_row()` removes a job's row if the
  orchestrator raises after it was inserted, so a failed job is retried on
  the next run instead of being silently and permanently skipped by this
  same check.
- **Partial-failure isolation** — `daily_run.py::_process_one_job` writes
  each job in short, independent transactions (see above) rather than one
  shared transaction or SAVEPOINT, and `search_agent.run_search()` wraps
  each source in its own try/except — one bad job or one dead source
  degrades that one item, not the whole run.
