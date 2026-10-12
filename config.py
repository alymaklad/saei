"""
Central configuration. All values come from environment variables (.env),
so no secrets ever live in code. Copy .env.example to .env and fill it in.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _list(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _int_or_none(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _choice(name: str, choices: set[str], default: str) -> str:
    """Reads an env var expected to be one of `choices`, falling back to
    `default` for anything blank or unrecognized -- so a hand-edited or
    corrupted .env value can't put the app into an undefined state, it just
    silently reverts to the safe default instead."""
    raw = os.getenv(name, default).strip().lower()
    return raw if raw in choices else default


def _seeded_template_keys(name: str) -> set[str]:
    """
    Which agents.search_agent.KNOWN_JOB_BOARD_TEMPLATES keys have already
    been seeded into the search_sites table. Stored as a comma-separated
    list of keys (e.g. "bayt,wuzzuf") rather than a plain bool, so that
    adding a NEW template later (e.g. adding "remoteok" to the dict) still
    gets seeded on the next run for existing installs, without silently
    re-adding a template the user deliberately removed.

    Migrates the old boolean format transparently: this flag used to be a
    plain true/false before per-template tracking existed, back when
    KNOWN_JOB_BOARD_TEMPLATES only had "wuzzuf" and "bayt" -- so a legacy
    "true" value means exactly {"wuzzuf", "bayt"} were seeded, not "every
    template that will ever exist."
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return set()
    if raw.lower() in ("true", "1", "yes", "on"):
        return {"wuzzuf", "bayt"}
    if raw.lower() in ("false", "0", "no", "off"):
        return set()
    return {item.strip() for item in raw.split(",") if item.strip()}


# LLM
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")
# qwen3:4b (~2.5GB) rather than an 8B model, so it can sit in VRAM alongside
# qwen3-embedding:4b (also ~2.5GB) on a typical 6-8GB laptop GPU instead of
# the two swapping in and out. The pipeline uses them in phases (expansion ->
# embeddings -> per-job scoring) so swapping wouldn't be catastrophic, but
# co-residency avoids it entirely. Trade-off: a 4B model writes weaker
# tailored CVs than a hosted 120B one -- if the rewrites disappoint, keep
# embeddings local and point LLM_PROVIDER at groq/openrouter, since the two
# providers are independent settings.
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:4b")

# Context window for local chat models. Set EXPLICITLY because Ollama
# otherwise falls back to the model's own default (commonly 4096) and
# SILENTLY TRUNCATES anything longer -- no error, no warning, just a prompt
# the model never fully saw. That matters here: the CV-rewrite prompt carries
# a full CV plus a full job description, and the expansion prompt now carries
# the whole CV. 8192 covers input+output for both with headroom.
# Raising this costs VRAM (the KV cache scales with it), which is worth
# knowing when the chat model shares a GPU with the embedding model.
OLLAMA_NUM_CTX = _int("OLLAMA_NUM_CTX", 8192)

# Ollama counts the PROMPT AND THE GENERATED TOKENS against num_ctx, and
# silently truncates rather than erroring. The CV rewrite is this app's
# longest prompt -- profile JSON + a whole scraped job description + target
# terms -- and reserves 3,000 tokens of output on top, so a fixed 8,192 is
# where it runs out: the model gets a truncated prompt, has no room left to
# answer, and returns an empty string with no error anywhere. That surfaced as
# "CV rewrite came back empty (finish_reason=unknown)".
#
# So the context is sized per call from the actual prompt (see
# agents/llm.get_llm) and this is the ceiling for that. Raising it costs KV
# cache memory, not quota -- the model is local.
OLLAMA_NUM_CTX_MAX = _int("OLLAMA_NUM_CTX_MAX", 16384)

# Hybrid reasoning models (qwen3, deepseek-r1) emit a <think> block before the
# answer, and it is billed against the same output budget. A 4B model given a
# long CV-rewrite prompt can spend the entire budget thinking and return
# nothing at all. Nothing in this app asks the model to reason its way to an
# answer -- every judgement call is either a table lookup or a directional
# yes/no -- so thinking is off by default and the whole budget goes to output.
OLLAMA_THINKING = _bool("OLLAMA_THINKING", False)

# The job description is the one unbounded input in the rewrite prompt: a
# scraped posting can run to tens of thousands of characters of boilerplate.
# Trimmed from the END, where the benefits/EEO/apply-by text lives.
REWRITE_JD_MAX_CHARS = _int("REWRITE_JD_MAX_CHARS", 6000)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

# OpenRouter -- one API key in front of ~400 models from many providers, with
# automatic cross-provider failover. Useful here mainly as a way to reach a
# different model without wiring up another provider, and as somewhere to go
# when Groq's daily token budget is spent.
#
# IMPORTANT free-tier shape (differs from every other provider here): models
# whose id ends in ":free" cost nothing but are limited by REQUEST COUNT, not
# tokens -- 20 requests/minute, and 50 requests/DAY on an unfunded account
# (1,000/day once you've bought $10 of credits). This project's orchestrator
# spends roughly 2-3 LLM calls per job that clears the match gate, so 50/day
# is on the order of ~15-20 jobs. Paid model ids (no ":free" suffix) have no
# such request cap. See README "Enabling auto-submit"/LLM notes.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
# Free model ids come and go -- if this one 404s, pick another from
# https://openrouter.ai/models?q=free and set it in Settings.
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openai/gpt-oss-120b:free")

# Job search
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
GREENHOUSE_BOARD_TOKENS = _list("GREENHOUSE_BOARD_TOKENS")
LEVER_COMPANY_SLUGS = _list("LEVER_COMPANY_SLUGS")
# Two more ATS vendors with public, keyless job-board feeds, read exactly like
# Greenhouse/Lever above: the org slug from jobs.ashbyhq.com/<slug>, and the
# company identifier from jobs.smartrecruiters.com/<Company>.
ASHBY_BOARD_SLUGS = _list("ASHBY_BOARD_SLUGS")
SMARTRECRUITERS_COMPANIES = _list("SMARTRECRUITERS_COMPANIES")

# LinkedIn's logged-out job search (the same endpoint its public jobs page
# uses). Each entry is a LinkedIn location searched once per role phrase;
# "Worldwide" is searched as remote-only, since an on-site worldwide listing is
# meaningless. Blank disables the source. LinkedIn's User Agreement forbids
# automated collection, so requests are paced (LINKEDIN_REQUEST_DELAY seconds
# apart -- under ~1.5s gets the IP blocked for an hour) and descriptions are
# fetched only for jobs that survive the title filter.
LINKEDIN_LOCATIONS = _list("LINKEDIN_LOCATIONS")
LINKEDIN_REQUEST_DELAY = _float("LINKEDIN_REQUEST_DELAY", 2.0)

# Where the candidate can legally work from, for remote boards that say who a
# job is open to (Himalayas, Remotive, Jobicy, Working Nomads). A job is kept
# when it's open worldwide, names no restriction, or its restriction mentions
# any of these (e.g. "Egypt,EMEA,MENA,Middle East,Africa"). Blank keeps
# everything.
SEARCH_ELIGIBLE_LOCATIONS = _list("SEARCH_ELIGIBLE_LOCATIONS")

# Always-on feeds switched off from the Search page's source manager (keys of
# agents.search_agent.BUILTIN_FEEDS, e.g. "jobicy,workingnomads").
SEARCH_DISABLED_FEEDS = set(_list("SEARCH_DISABLED_FEEDS"))

# Default position/title to search for -- used by the Search tab's "search
# now" button and by the scheduler's automatic 8am run alike, so both stay in
# sync with whatever the user last saved. Blank means "no title filter".
SEARCH_POSITION_QUERY = os.getenv("SEARCH_POSITION_QUERY", "")

# Seniority filter: one of "" (any), "intern", "entry", "mid", "senior",
# "lead", "manager". Same sync behavior as SEARCH_POSITION_QUERY above.
SEARCH_SENIORITY_LEVEL = os.getenv("SEARCH_SENIORITY_LEVEL", "")

# Tracks which agents.search_agent.KNOWN_JOB_BOARD_TEMPLATES keys have
# already been seeded into search_sites, so seed_default_search_sites()
# knows what's left to add without re-adding anything the user removed by
# hand. See _seeded_template_keys() above for the legacy-bool migration.
SEARCH_DEFAULT_SITES_SEEDED = _seeded_template_keys("SEARCH_DEFAULT_SITES_SEEDED")

# Which of those templates a fresh install adds to the Search tab by itself.
# None by default: every known board is listed under Search -> Manage
# sources with an Add button, so you choose the ones for your region.
# Keys: wellfound, simplyhired, wuzzuf, bayt, gulftalent, tanqeeb.
SEARCH_DEFAULT_BOARDS = set(_list("SEARCH_DEFAULT_BOARDS"))

# Max raw results kept from each individual source (each Greenhouse board,
# Lever company, watchlist row, or added website) before filtering. Blank/0 =
# no limit -- preserves the original "pull everything" behavior. Generic
# scraped sites are already capped internally (see GENERIC_SITE_MAX_CANDIDATES
# in agents/search_agent.py) even when this is unset, since each candidate
# there costs a real HTTP fetch.
SEARCH_MAX_RESULTS_PER_SITE = _int_or_none("SEARCH_MAX_RESULTS_PER_SITE")

# Max age in days for a job posting to be included. Blank/0 = no limit. Only
# applied where a posted date can actually be determined (Greenhouse, Lever,
# SerpAPI/Google Jobs) -- sources with no reliable date (generic scraped
# sites) are never excluded by this filter since their age can't be verified.
SEARCH_MAX_AGE_DAYS = _int_or_none("SEARCH_MAX_AGE_DAYS")

# Google Sheets watchlist
GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID", "")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "credentials/service_account.json")

# Gmail -- sends your applications, and your daily report and weekly news,
# through "Sign in with Google" (Google's account chooser and consent screen).
# Sa'ei's shared Google sign-in app, shipped with the code -- what makes
# "Sign in with Google" work with no Google Cloud setup. A client file of
# your own at GMAIL_CREDENTIALS_JSON takes precedence over it.
SAEI_OAUTH_CLIENT_JSON = os.getenv("SAEI_OAUTH_CLIENT_JSON", "google_oauth_client.json")
GMAIL_CREDENTIALS_JSON = os.getenv("GMAIL_CREDENTIALS_JSON", "credentials/gmail_credentials.json")
GMAIL_TOKEN_JSON = os.getenv("GMAIL_TOKEN_JSON", "credentials/gmail_token.json")
APPLICANT_NAME = os.getenv("APPLICANT_NAME", "")
APPLICANT_EMAIL = os.getenv("APPLICANT_EMAIL", "")
# Where the daily report and weekly news go. Blank = your own Gmail address.
REPORT_EMAIL_TO = os.getenv("REPORT_EMAIL_TO", "").strip()

# Whitelist: entries look like "greenhouse:stripe" or "lever:netflix".
# Empty by default — nothing auto-submits until you explicitly add an entry
# AFTER manually verifying that board's application form works end-to-end.
WHITELISTED_SOURCES = set(_list("WHITELISTED_SOURCES"))

# Three-way auto-apply mode, set from the Settings page's "Auto-Apply
# Behavior" panel. Read dynamically (module attribute access, not cached) by
# agents/apply_agent.py:decide_apply_path -- the single place this is
# checked -- so a Settings change takes effect on the next job, no restart
# needed.
#   "off"       -- never auto-submit. Every job drafts for review.
#   "any"       -- auto-submit regardless of source, ignoring
#                  WHITELISTABLE_SOURCES/WHITELISTED_SOURCES entirely. NOTE:
#                  the only real submit implementation, auto_submit_greenhouse
#                  (agents/apply_agent.py), is a Greenhouse-specific stub --
#                  choosing "any" changes ROUTING (which jobs reach the
#                  auto-submit node) but can't make a non-Greenhouse job
#                  actually submit until that function is generalized or a
#                  per-site submitter is added. With DRY_RUN=true (the
#                  default) this is harmless and just logs intent for every
#                  job instead of only whitelisted ones.
#   "whitelist" -- (default, matches the original pre-mode behavior) only
#                  sources in WHITELISTABLE_SOURCES that are also in
#                  WHITELISTED_SOURCES auto-submit; everything else drafts.
# Falls back to the safe default if the .env value isn't one of the three.
AUTO_APPLY_MODES = {"off", "any", "whitelist"}
AUTO_APPLY_MODE = _choice("AUTO_APPLY_MODE", AUTO_APPLY_MODES, "whitelist")

# When true, a job that was rewritten because its ORIGINAL CV scored below
# FIT_THRESHOLD can still reach the normal apply-path decision if the
# TAILORED CV's re-scored ATS score clears that same threshold -- instead of
# always stopping at "notify user" the way orchestrator.py's docstring
# originally guaranteed. Defaults to False: a rewritten CV is never
# auto-inspected for auto-submit eligibility unless you explicitly opt in,
# since the score doesn't verify the rewrite didn't overstate anything. Only
# changes whether a job's tailored CV *reaches* decide_apply_path at all --
# AUTO_APPLY_MODE above still governs what happens once it's there. See
# orchestrator.py:route_after_rewrite.
AUTO_APPLY_ON_TAILORED_SCORE = _bool("AUTO_APPLY_ON_TAILORED_SCORE", False)

# The email route (agents/apply_agent.py): when a posting says "send your CV to
# <address>", email the CV and a cover letter there through the connected
# Gmail instead of only drafting. Off by default. AUTO_APPLY_MODE="off" still
# blocks it, and DRY_RUN still turns it into a "would have applied" record.
AUTO_APPLY_EMAIL = _bool("AUTO_APPLY_EMAIL", False)

# ATS fit threshold (orchestrator.py): the score, 0.0-1.0, below which a job's
# CV gets rewritten instead of proceeding straight to the auto-apply
# decision. User-configurable from the Settings page (default 0.7, matching
# the original hardcoded value). Read dynamically by
# orchestrator.py:route_on_score/route_after_rewrite, so a Settings change
# applies to the next job scored, no restart needed.
FIT_THRESHOLD = _float("FIT_THRESHOLD", 0.7)

# ---- Job matching: query expansion, embeddings, ranking ---------------------
#
# These drive the two-stage retrieve-then-rank matching pipeline (see
# ARCHITECTURE.md "Job matching"). Retrieval is deliberately recall-biased
# (cheap signals, don't drop anything plausible); ranking does the deeper
# per-job analysis on the already-narrowed candidate pool.

# Turns the whole LLM query-expansion step on/off. When false, the pipeline
# falls back to searching the single literal SEARCH_POSITION_QUERY string --
# i.e. exactly the pre-expansion behavior -- so this is the escape hatch if
# expansion ever produces bad phrases or the LLM is unavailable.
SEARCH_QUERY_EXPANSION = _bool("SEARCH_QUERY_EXPANSION", True)

# How many expanded role phrases the LLM is asked for. The original typed
# position is always included on top of these (see query_expansion_agent),
# so the real list is at most this + 1.
QUERY_EXPANSION_MAX_ROLES = _int("QUERY_EXPANSION_MAX_ROLES", 8)

# Whether to reuse a previously generated expansion for the same Position.
# Defaults OFF: the cache existed to avoid burning a hosted provider's daily
# quota on an identical answer every morning, but on a local model an
# expansion costs seconds rather than quota -- and regenerating means prompt
# edits, model changes, and temperature variation actually show up in the
# next run instead of being masked by a stale row. Turn it back on if you
# move to a metered provider and want the daily scheduler to stop re-asking.
QUERY_EXPANSION_CACHE = _bool("QUERY_EXPANSION_CACHE", False)

# How much of the CV to include in the expansion prompt. 0 = the WHOLE CV
# (the default). A cap existed to keep the prompt small, but it was cutting
# real signal: a measured 6.3k-character CV lost 36% of itself -- including
# the project descriptions that say what the candidate actually builds, which
# is precisely what the role list should be calibrated to. Set a positive
# number here only if a very long CV starts crowding the model's context.
QUERY_EXPANSION_CV_CHARS = _int("QUERY_EXPANSION_CV_CHARS", 0)

# SerpAPI's free tier is 100 searches/MONTH, and every expanded phrase would
# otherwise be its own search -- 8 phrases on a daily schedule is ~240/month,
# blowing the quota in under two weeks. Only this many phrases are ever sent
# to SerpAPI; the unlimited scrape/API sources still get the full list.
SERPAPI_EXPANSION_LIMIT = _int("SERPAPI_EXPANSION_LIMIT", 2)

# Embeddings provider for CV<->job-description semantic matching. Deliberately
# NOT tied to LLM_PROVIDER: Groq has no embeddings endpoint at all, so a user
# on LLM_PROVIDER=groq still needs a separate choice here.
#   "ollama" (default) -- local, free forever, no quota. Needs `ollama serve`
#                          running and `ollama pull nomic-embed-text` once.
#   "gemini"           -- hosted free tier, needs GEMINI_API_KEY, uses quota.
EMBEDDING_PROVIDERS = {"ollama", "gemini"}
EMBEDDING_PROVIDER = _choice("EMBEDDING_PROVIDER", EMBEDDING_PROVIDERS, "ollama")
# nomic-embed-text -- 274MB, 768 dimensions, 8K context, CPU-fast. Aly's
# choice (2026-08-31), and what .env has been pointing at; the default now
# says the same thing so a fresh checkout, the bench and the tests all behave
# like the running app.
#
# What that choice costs and buys, since neither is obvious:
#
#   + Fast enough to embed every CV span and every job description on CPU,
#     which is what makes the semantic retrieval layer free per job.
#   + 768 dimensions instead of qwen3-embedding:4b's 2560 -- a third of the
#     cache on disk and a third of the work per cosine.
#   - English-centric. This project scrapes MENA boards (Wuzzuf, Bayt,
#     GulfTalent) where postings are frequently Arabic or mixed-language, and
#     nomic is weak on those; qwen3-embedding:4b is the multilingual option
#     and is one .env line away (OLLAMA_EMBEDDING_MODEL=qwen3-embedding:4b).
#   - 8K context against qwen's 40K. Job descriptions here measure ~2.2k
#     tokens so they fit, but the generic scraper's whole-page text can run
#     far longer and will be truncated.
#
# Switching models is safe at any time: every embedding cache key includes
# the model, so vectors from two models can never be compared to each other.
OLLAMA_EMBEDDING_MODEL = os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")

# The QUERY-side task instruction, used ONLY by Qwen3-Embedding, which is
# trained to take one ("Instruct: {task}" / "Query: {text}") with documents
# embedded bare. It is ignored under nomic-embed-text, whose prefixes are
# fixed strings the model was trained on ("search_query: " /
# "search_document: ", both sides, not optional) rather than wording for a
# user to choose -- see agents/embeddings.py::_PREFIX_SCHEMES.
#
# Either way it is plain string formatting, which is why none of this needs a
# transformers/torch dependency: the prefix is prepended before the text ever
# reaches Ollama.
# Known-good embedding models, surfaced as suggestions on the Settings page.
# NOT a whitelist -- the model field stays free text so any Ollama tag or
# Gemini id still works. Recorded here because the tradeoffs are specific to
# this app and easy to get wrong: context length decides whether long scraped
# job descriptions get silently truncated, and multilingual capability
# decides whether the Arabic/mixed-language postings from Wuzzuf, Bayt and
# GulfTalent score meaningfully or essentially at random.
KNOWN_EMBEDDING_MODELS = {
    "ollama": [
        {"id": "nomic-embed-text", "size": "274 MB", "context": "8K", "multilingual": False,
         "note": "Current default. Fast on CPU, 768 dimensions. English-centric -- "
                 "weak on Arabic/mixed postings. Needs: ollama pull nomic-embed-text"},
        {"id": "qwen3-embedding:4b", "size": "2.5 GB", "context": "40K", "multilingual": True,
         "note": "Best quality and the multilingual option. Slowest -- a full "
                 "forward pass per job description."},
        {"id": "qwen3-embedding:0.6b", "size": "639 MB", "context": "32K", "multilingual": True,
         "note": "~6x faster than the 4b, same context and languages, some precision lost."},
        {"id": "embeddinggemma:300m", "size": "622 MB", "context": "2K", "multilingual": True,
         "note": "Small and multilingual, but 2K context truncates longer job descriptions."},
    ],
    "gemini": [
        {"id": "gemini-embedding-001", "size": "hosted", "context": "2K", "multilingual": True,
         "note": "Text-only. Free tier meters 100 embeddings/minute, counted per job."},
        {"id": "gemini-embedding-2", "size": "hosted", "context": "2K", "multilingual": True,
         "note": "Newer multimodal model; unnecessary here since only text is embedded."},
    ],
}

EMBEDDING_QUERY_INSTRUCTION = os.getenv(
    "EMBEDDING_QUERY_INSTRUCTION",
    "Given a candidate's CV, retrieve job postings that match their skills and experience",
)
# text-embedding-004 was SHUT DOWN on 2026-01-14 (embedding-001 before it, on
# 2025-08-14) and now returns a 404 NOT_FOUND from embedContent.
# gemini-embedding-001 is its documented text-only replacement;
# gemini-embedding-2 is the newer multimodal model, unnecessary here since
# this only ever embeds plain text.
GEMINI_EMBEDDING_MODEL = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")

# Texts per embedding request. Gemini's API caps a batch at 100; Ollama is
# local and unmetered, so the same value is fine there. Chunking also means a
# rate-limit retry only redoes one chunk rather than the whole set.
EMBEDDING_BATCH_SIZE = _int("EMBEDDING_BATCH_SIZE", 100)

# Cosine similarity (0..1) between the CV and a job description, above which
# a job the title-token filter did NOT match is still pulled into the
# candidate pool -- this is what catches "Backend Developer" for a
# "Software Engineer" search when the JD content genuinely fits. Needs
# calibrating against a real CV + a real batch of fetched jobs; 0.5 is a
# starting estimate, not a measured value.
CV_JOB_SIMILARITY_THRESHOLD = _float("CV_JOB_SIMILARITY_THRESHOLD", 0.5)

# How many TITLE-MISMATCHED jobs are carried forward for the semantic
# retriever to evaluate. Without this the semantic path is dead weight:
# run_search's title filter would have already discarded every job it exists
# to rescue, so a "Backend Developer" posting could never be recovered for a
# "Software Engineer" search no matter how well its description fits.
#
# The default is PROVIDER-AWARE, because the two providers are limited by
# completely different things:
#   gemini -- 100 embeddings per MINUTE on the free tier, counted PER JOB
#             DESCRIPTION (not per HTTP request, so batching alone can't get
#             under it). 90 leaves headroom for the CV embedding and the
#             token-matched jobs, so a run finishes without waiting out a
#             rate limit.
#   ollama -- local and unmetered. The only cost is wall-clock time, so the
#             cap can be far higher and the semantic path gets to consider
#             many more title-mismatched jobs.
# An explicit .env value always wins over either default.
_LOCAL_EMBEDDINGS = EMBEDDING_PROVIDER == "ollama"
SEMANTIC_CANDIDATE_CAP = _int("SEMANTIC_CANDIDATE_CAP", 400 if _LOCAL_EMBEDDINGS else 90)

# Hard ceiling on job descriptions embedded in ONE run, across both paths
# (semantic rescue + scoring the token-matched jobs). SEMANTIC_CANDIDATE_CAP
# alone isn't enough: the token-matched jobs need embedding too, and together
# they can exceed the quota even when each is individually under it.
#
# Discovery is prioritized over scoring when the budget runs short -- the
# semantic path finds jobs nothing else would, whereas a token-matched job is
# already known relevant by title and only loses its semantic *factor*
# (scored neutral instead). Whatever gets skipped is recorded in the debug
# report rather than silently dropped.
# Provider-aware for the same reason as the cap above: 95 keeps a run inside
# Gemini's 100-per-minute free tier; local Ollama has no quota to respect, so
# the ceiling is far higher and bounded by patience rather than billing.
EMBEDDING_MAX_PER_RUN = _int("EMBEDDING_MAX_PER_RUN", 500 if _LOCAL_EMBEDDINGS else 95)

# Minimum ranking_agent match score (0..1) a job needs before it's handed to
# the orchestrator's per-job LLM pipeline (ATS scoring, possibly a full CV
# rewrite -- the expensive part). DEFAULTS TO 0.0, i.e. the gate is OFF and
# every candidate is processed exactly as before: a non-zero default would
# mean silently discarding jobs against a threshold nobody has calibrated
# yet, which is the same failure mode as this project's earlier
# over-filtering bugs. Raise it from the Settings page once you've seen real
# match scores against real jobs.
MATCH_SCORE_THRESHOLD = _float("MATCH_SCORE_THRESHOLD", 0.0)

# ---- Scoring engine: ATS compatibility vs job match -------------------------
#
# The original scorer folded four pillars into one "ATS score": keyword match
# 45%, formatting 22%, section completeness 18%, experience alignment 15%.
# Measured against 15 real applications in this project's own database, that
# model had a structural defect: formatting returned 0.800 and section
# completeness returned 1.000 on EVERY job, because neither function takes the
# job description as an argument at all. 0.22*0.8 + 0.18*1.0 = 0.356 of every
# score was therefore a constant, contributing nothing to ranking, while
# consuming 40% of the weight. Every one of the 15 scores landed in
# 0.408-0.643, so nothing ever reached FIT_THRESHOLD (0.7) before or after
# tailoring -- meaning every job was rewritten, always, and the auto-apply
# gate could never fire.
#
# The replacement splits the two questions that were being conflated:
#
#   ATS Compatibility  -- "can a parser read this CV?" Depends ONLY on the CV,
#                         so it's computed once and reported as a status, not
#                         mixed into a per-job number.
#   Job Match          -- "how well does this candidate satisfy THIS job?"
#                         Requirement-level matching with evidence.
#
#   "legacy"       -- the original four-pillar scorer, unchanged. Kept
#                     runnable so the 15 already-stored scores remain
#                     reproducible and comparable against.
#   "requirements" -- structured requirement extraction + evidence matching +
#                     deterministic scoring. THE DEFAULT as of 2026-08-25,
#                     after being exercised against real postings from the
#                     debug bench.
#
# The debug bench (POST /api/debug/ats) can still request either engine per
# call regardless of this setting, which is how the two get compared on a
# specific posting.
#
# WHAT THE SWITCH INVALIDATES, and what was done about each
# ---------------------------------------------------------
# A score from this engine does not mean what a score from the legacy one
# meant, so three things stop being comparable the moment it becomes the
# default:
#
#   Stored scores. Every Application row written before this date holds a
#     legacy score with a legacy-shaped breakdown. Rows now record which
#     engine produced them (Application.scoring_engine) so the dashboard
#     renders each with the matching renderer instead of showing an empty
#     breakdown for one of them. Old rows are NOT back-filled or rescored --
#     rescoring 15 jobs would cost real LLM calls to restate history nobody
#     is acting on.
#
#   FIT_THRESHOLD (below, default 0.7). Calibrated against the legacy
#     0.408-0.643 band, where it was unreachable. This engine has no 0.356
#     constant floor, so it scores genuinely poor matches far lower -- a real
#     posting measured 0.319 where its legacy score was 0.542. The threshold
#     is deliberately LEFT AT 0.7 through the cutover rather than guessed at:
#     if the new distribution also sits below it the behaviour is "rewrite
#     everything", which is exactly what the app already did and is safe,
#     whereas a threshold set too low starts auto-applying to jobs the
#     candidate does not match. Recalibrate it from real runs (the Dashboard
#     lists every score), not from a guess.
#
#   MATCH_SCORE_THRESHOLD is unaffected -- it gates on the ranking agent's
#     match_score, which this engine does not produce.
# The four-pillar scorer that SCORING_ENGINE used to select between is gone;
# there is one engine and nothing to choose. See agents/ats_agent.py.

ATS_SCORE_MODES = {"both", "tailored_only"}
ATS_SCORE_MODE = _choice("ATS_SCORE_MODE", ATS_SCORE_MODES, "both")

# Job-match weights. Each requirement lands in EXACTLY ONE bucket, decided by
# (importance, category) -- see agents/ats_agent.py::_bucket_for. That
# partition matters: an earlier draft weighted "required requirements" and
# "relevant technical skills" separately, which double-counts every required
# technical skill.
#
# Buckets with nothing in them for a given job are DROPPED and their weight
# redistributed proportionally across the rest (see _redistribute_weights).
# Without that, a job with no stated education requirement would silently cap
# at 90%, penalising the candidate for a requirement the employer never made.
#
# Soft skills are their own small bucket (2026-10-05). Inside required_skills
# they were weighted like technical requirements' peers, and a posting listing
# seven of them made them outweigh Python -- for things a CV rarely states in
# words. They're judged from what the work shows (agents/soft_skills.py), and
# worth 5%: real, but not what decides a technical role. The other five are
# the original weights scaled by 0.95, so a posting that lists no soft skills
# redistributes back to exactly the old weights and scores exactly as before.
JOB_MATCH_WEIGHTS = {
    "required_skills": 0.4275,
    "experience": 0.19,
    "preferred_skills": 0.1425,
    "responsibilities": 0.095,
    "education": 0.095,
    "soft_skills": 0.05,
}
assert abs(sum(JOB_MATCH_WEIGHTS.values()) - 1.0) < 1e-9

# Points per requirement WITHIN its bucket, by category. A bucket's score is
# earned/possible, so these express relative importance among peers, not an
# absolute scale. This is what stops "Kubernetes (preferred)" counting the
# same as "Python (required)" -- the flaw in the legacy scorer, where every
# extracted keyword contributed equally.
REQUIREMENT_POINTS = {
    "technical_skill": 10,
    "domain_knowledge": 9,
    "experience": 10,
    "education": 9,
    "certification": 6,
    "tool": 6,
    "responsibility": 5,
    "soft_skill": 2,
}

# Credit for a match, along TWO independent axes.
#
# A single flat scale conflated two different questions. "Alias" and "subset"
# describe how the CV term RELATES to the requirement; "demonstrated" and
# "claimed" describe WHERE in the CV the evidence sits. They vary
# independently: an exact term buried in a skills list is weaker evidence than
# a subset term inside a dated role, and a flat enum cannot express that
# without inventing a row for every combination.
#
# RELATION -- how the CV term relates to the requirement.
#   exact             the requirement's own word, e.g. "PyTorch" for PyTorch
#   alias             a true synonym: Postgres for PostgreSQL, RESTful APIs
#                     for REST API. Interchangeable in both directions, so
#                     full credit.
#   implied           the CV term could not have been used WITHOUT the
#                     requirement: FastAPI or PyTorch means Python was
#                     written; PostgreSQL means SQL was written; React means
#                     JavaScript. The candidate did not do something adjacent
#                     to the requirement -- they DID the requirement, under
#                     another name. Scores above subset for that reason, and
#                     below exact because the CV never says the word.
#   subset            the CV term is a KIND OF the requirement: CNN for Deep
#                     Learning, LoRA for PEFT, Git for version control.
#                     Genuine support, but not the same claim -- partial.
#   semantic_support  an LLM judged the evidence to demonstrate the
#                     requirement. Real but unverifiable against a table, and
#                     the model is the weakest link, so it sits below alias.
RELATION_CREDIT = {
    "exact": 1.00,
    "alias": 1.00,
    "implied": 0.90,
    "subset": 0.80,
    "semantic_support": 0.75,
    "none": 0.0,
}

# EVIDENCE LOCATION -- multiplies the relation credit above.
#
# This is the cheap, robust stand-in for per-skill years of experience. A skill
# named inside a dated role or project has been DEMONSTRATED; one appearing
# only in a Skills list has merely been CLAIMED. Deriving "2.3 years of Python"
# would need per-role skill attribution that real CVs don't provide, so it
# would end up inferred by an LLM -- reintroducing exactly the unexplainable
# number this rewrite exists to remove.
EVIDENCE_MULTIPLIER = {
    "demonstrated": 1.00,
    "claimed": 0.65,
    "none": 0.0,
}

# CALIBRATION NOTE, 2026-08-19. The first values here were subset 0.70 /
# claimed 0.75, which produced an inversion the first real example caught: a
# CV listing "SQL" in its skills section scored 0.75 for that requirement,
# while the same CV describing SQLAlchemy work inside a dated project scored
# 0.70. A bare keyword in a list beat demonstrated related work -- exactly
# backwards from the premise the demonstrated/claimed split exists to encode.
#
# Now subset 0.80 / claimed 0.65, so the ordering reads:
#
#   exact or alias, demonstrated   1.00   used the term, in real work
#   implied, demonstrated          0.90   did work that required it
#   subset, demonstrated           0.80   did something that is a kind of it
#   semantic_support, demonstrated 0.75   model judged it demonstrated
#   exact or alias, listed only    0.65   named it, no context
#   implied, listed only           0.585  a listed tool that requires it
#   subset, listed only            0.52   named something adjacent, no context
#
# CALIBRATION NOTE, 2026-08-27. "implied" added at 0.90, above subset and
# below exact. The case that forced it: a CV listing Python in its skills
# section, with FastAPI, PyTorch and LLM work described in dated roles, scored
# 0.65 for a Python requirement -- the skills-list discount -- while the roles
# themselves are proof that Python was written. 0.65 was measuring how the
# candidate had written their CV, not what they had done. It is not 1.00
# because the CV never says the word, which an ATS on the other side may well
# be searching for literally; the tailoring pass turns exactly that gap into a
# bridge (see agents/cv_targeting.py).
#
# Widening the gap also increases the reward for tailoring, since moving a
# skill from the list into the role that used it is now worth 35% of that
# requirement's points rather than 25%.
#
# These are still judgement calls, not measurements.
# tests/test_matching_eval.py holds the labelled pairs; change a number here
# and the evaluation report tells you what it did.


def match_credit(relation: str, location: str) -> float:
    """Credit for one matched requirement: relation x where the evidence sits."""
    return (RELATION_CREDIT.get(relation, 0.0)
            * EVIDENCE_MULTIPLIER.get(location, 0.0))

# Whether unmatched requirements get a second look from the LLM, asked the
# DIRECTIONAL question ("does this evidence demonstrate this requirement?")
# rather than a similarity question. This distinction is the crux: cosine
# similarity is symmetric, and embeddings score "AWS"/"GCP" high precisely
# BECAUSE they are the same category -- comparably high to "CNN"/"Deep
# Learning". No threshold separates them. But CNN is a KIND OF deep learning
# (evidence entails requirement) while GCP is a SIBLING of AWS (neither
# implies the other). Embeddings are therefore used only to RETRIEVE candidate
# CV spans; the verdict comes from the alias table or this call.
# Off -> alias table only: fully deterministic and free, but systematically
# misses genuine matches phrased unusually.
REQUIREMENT_ENTAILMENT = _bool("REQUIREMENT_ENTAILMENT", True)

# Read the soft skills the profile's work demonstrates (one cached model call
# per profile version, agents/soft_skills.py) and judge a posting's soft-skill
# requirements against those lines. Off: soft skills are judged like any other
# requirement, from lines retrieved by similarity.
SOFT_SKILL_INFERENCE = _bool("SOFT_SKILL_INFERENCE", True)

# Candidate CV spans retrieved per unmatched requirement before entailment.
# All unmatched requirements are judged in ONE batched call, not one call
# each: a measured 31 requirements/job across 250 jobs would be ~7,900 calls,
# and this project has already exhausted Groq's 200k tokens/day at one call
# per job.
REQUIREMENT_EVIDENCE_TOP_K = _int("REQUIREMENT_EVIDENCE_TOP_K", 4)

# ---- semantic retrieval (the generic-matching plan, phases 3-4) -------------
#
# Retrieval NOMINATES evidence; it never scores. A requirement the
# deterministic tables cannot resolve is embedded, the candidate's spans are
# searched, and the closest few are what the adjudication call is shown --
# instead of the first 80 demonstrated spans, which is what it used to get.
# The gain is recall on requirements no vocabulary will ever contain
# ("stakeholder management", "campaign performance analysis") without an
# extra LLM call: the call was already being made, it is now shown the right
# lines.
SEMANTIC_RETRIEVAL = _bool("SEMANTIC_RETRIEVAL", True)

# How many spans per requirement retrieval offers the adjudicator.
SEMANTIC_TOP_K = _int("SEMANTIC_TOP_K", 5)

# Confidence bands, in cosine similarity.
#
#   below IGNORE   no candidate -- not offered, not judged
#   IGNORE..STRONG ambiguous -- offered to the batched LLM call
#   STRONG+        strong candidate -- STILL offered to the LLM
#
# The last line is the deliberate part. A strong candidate could be accepted
# on similarity alone and save the call, but similarity is exactly what
# cannot tell AWS from GCP -- they are near-identical vectors BECAUSE they
# are the same kind of thing. So the band is recorded for calibration and
# reporting, and the model still has to say yes. Nothing in this project
# scores on cosine similarity alone.
#
# These defaults are starting points, not measurements: bench/report.py
# prints the band distribution so they can be calibrated against the golden
# dataset once it has enough labelled pairs. They matter more under
# nomic-embed-text than they would under a model with a wider spread --
# nomic's cosine values sit high and close together, so a threshold picked by
# intuition from "0.8 sounds similar" is likely to be wrong in both
# directions. Run `python -m bench.report` with Ollama up before trusting
# them.
# Whether the floor is trusted to DROP a requirement from the adjudication
# call. Off by default, and it stays off until bench.report --calibrate has
# been run against the model in use: with an uncalibrated floor, dropping is
# indistinguishable from the CV not having the evidence, and the row comes
# back "Not found" with no way to tell which happened. While this is off,
# retrieval narrows the question for the requirements it resolved and leaves
# the rest asked the old way -- strictly better than before it existed.
SEMANTIC_RETRIEVAL_STRICT = _bool("SEMANTIC_RETRIEVAL_STRICT", False)

SEMANTIC_SIMILARITY_IGNORE = _float("SEMANTIC_SIMILARITY_IGNORE", 0.55)
SEMANTIC_SIMILARITY_STRONG = _float("SEMANTIC_SIMILARITY_STRONG", 0.80)

# Extracted requirement names longer than this are phrases, not skills -- the
# live database contains a 105-character "skill" reading "Bachelor's degree in
# Computer Science, Machine Learning, Mathematics, Physics, Statistics or
# related field". Substring matching can never satisfy such an item, so it was
# counted missing on every job forever and then handed to the CV rewriter as
# something to add. These now skip token matching and go straight to
# entailment, where they can actually be judged.
REQUIREMENT_TOKEN_MATCH_MAX_CHARS = _int("REQUIREMENT_TOKEN_MATCH_MAX_CHARS", 45)

# ATS compatibility bands (0..1). Reported as Pass/Warning/Fail rather than
# folded into the job-match number.
ATS_COMPAT_PASS = _float("ATS_COMPAT_PASS", 0.85)
ATS_COMPAT_WARN = _float("ATS_COMPAT_WARN", 0.60)

# Where the structured CV parse is cached, keyed by CV hash + model. The CV
# changes rarely and job descriptions change every job, so hoisting this out
# of the per-job path halves the LLM work. Same pattern as the CV embedding
# cache next to it.
CV_PROFILE_CACHE_PATH = os.getenv("CV_PROFILE_CACHE_PATH", "data/cv_profile_cache.json")


# ---- Search debug reports ---------------------------------------------------
#
# When on, every search run writes a multi-sheet .xlsx tracing what happened at
# each stage -- what the position expanded to, what each source returned before
# and after each filter, which jobs the token/semantic paths kept or dropped
# and why, every ranking factor's contribution, and what the match gate held
# back. Purely observational: tracing never changes what the pipeline does.
# Search-only mode: run the whole MATCHING pipeline (query expansion ->
# retrieval -> dedupe -> semantic rescue -> ranking -> match gate), write the
# debug report, and stop there. The orchestrator never runs, so no ATS
# scoring, no CV rewriting, no apply decisions, and nothing is written to the
# jobs/applications tables.
#
# The point is a cheap, repeatable loop for tuning the search itself:
# ranking is already LLM-free and query expansion is cached, so a full run
# costs roughly one LLM call plus the embeddings -- instead of the 2-4 LLM
# calls PER JOB the orchestrator spends, which for a few hundred candidates
# is several times a free tier's daily budget. Because nothing is persisted,
# consecutive runs see the same jobs and their reports are directly
# comparable, which is exactly what you want while moving thresholds.
SEARCH_ONLY_MODE = _bool("SEARCH_ONLY_MODE", False)

SEARCH_DEBUG_REPORTS = _bool("SEARCH_DEBUG_REPORTS", True)
SEARCH_REPORT_DIR = os.getenv("SEARCH_REPORT_DIR", "data/search_reports")
# Reports accumulate one file per run; the oldest are pruned beyond this.
# 0 = keep everything.
SEARCH_REPORT_KEEP = _int("SEARCH_REPORT_KEEP", 30)

# Safety
DRY_RUN = _bool("DRY_RUN", True)

# Storage
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/job_agent.db")

# ── Hosting ────────────────────────────────────────────────────────
# Everything below matters only once the app runs somewhere other than your
# own machine; the defaults keep a local install exactly as it was.

# Access control. Unset = no login (a local install only you can reach). Set
# it on ANY host reachable from the internet: the API holds your CV and
# profile, your API keys, and the right to send email from your Gmail.
APP_USERNAME = os.getenv("APP_USERNAME", "saei").strip() or "saei"
APP_PASSWORD = os.getenv("APP_PASSWORD", "")

# Origins allowed to call the API from another site. The dashboard served by
# the API itself (http://host/) is same-origin and needs nothing here; the
# default covers the local `python -m http.server 5500` setup.
CORS_ALLOW_ORIGINS = _list("CORS_ALLOW_ORIGINS") or [
    "http://localhost:5500", "http://127.0.0.1:5500",
]

# Gmail's first sign-in opens a browser on the machine running the API. On a
# headless server there is no browser: sign in locally, then copy
# credentials/gmail_token.json over. false = fail with that instruction
# instead of waiting on a browser that will never open.
GMAIL_INTERACTIVE_AUTH = _bool("GMAIL_INTERACTIVE_AUTH", True)

# Timezone for the 08:00 search / 20:00 report / Monday news schedule, e.g.
# "Africa/Cairo". Blank = the host's own clock, which on a cloud VM is UTC.
SCHEDULER_TIMEZONE = os.getenv("SCHEDULER_TIMEZONE", "").strip()

# true = the API process also runs the daily schedule, so one service does
# everything: half the memory on a small VM, and the dashboard's Stop button
# and "search already running" check also cover the scheduled 08:00 run.
# Don't also run scheduler.py when this is on.
RUN_SCHEDULER_IN_API = _bool("RUN_SCHEDULER_IN_API", False)
