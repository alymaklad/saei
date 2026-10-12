# How Sa'ei works

The pipeline, the scores and the dashboard, with diagrams. For setup see
[setup.md](setup.md); for a file-by-file deep dive see
[architecture.md](architecture.md).

## At a glance

```mermaid
flowchart TB
    CV[/"Your CV<br/>(.pdf / .docx)"/] -->|extract once| P[("Profile<br/>single source of truth")]

    subgraph Find["1 · Find"]
        S["Search Agent<br/>Greenhouse · Lever · RemoteOK<br/>Wuzzuf · Bayt · your sites"]
        Q["Query Expansion"] --> S
    end

    subgraph Judge["2 · Judge"]
        R["Ranking Agent<br/>match score 0–1"]
        A["ATS Agent<br/>requirement-level score"]
    end

    subgraph Act["3 · Act"]
        T["CV Tailoring<br/>(only if score is low)"]
        D{"Apply<br/>decision"}
        AS["Auto-submit<br/>(whitelist only)"]
        DR["Draft for your review"]
    end

    subgraph Report["4 · Report"]
        E["Email Agent<br/>(your Gmail)"]
        TG["Report emails<br/>daily + weekly"]
        G["Skill-gap tracker"]
    end

    P --> Q & R & A & T
    S --> R --> A
    A -->|below fit threshold| T --> D
    A -->|fits| D
    D --> AS
    D --> DR --> E
    A --> G --> TG
    DB[("SQLite<br/>everything is logged")]
    AS & DR & E --> DB --> TG
    DB --> UI["Sa'ei dashboard"]
```

A day in the life (all times are local, from `scheduler.py`):

```mermaid
timeline
    title What runs automatically
    8 AM daily : Search every source : Rank, score, tailor : Auto-submit or draft
    8 PM daily : Email summary of the day
    Monday 9 AM : Weekly news digest for your field
    Any time : Search on the Search page runs the 8 AM pipeline on demand
```

---

## Core concepts

### 1. Your profile is the single record of you

Uploading a CV extracts a structured profile (contact, summary, experience,
projects, education, certifications, skills). **Every stage reads the
profile, not the file**, so a correction you make on the Profile page
reaches search, ranking, scoring and tailoring the next time they run.

```mermaid
flowchart TD
    U["Upload CV<br/>(CV page)"] -->|LLM extraction| P[("Profile")]
    E["Hand edits<br/>(Profile page)"] --> P
    P --> QE["Query expansion"]
    P --> RK["Ranking"]
    P --> ATS["ATS matching"]
    P --> RW["CV tailoring"]
    F[/"Raw CV file"/] -.->|only for| PC["Parse-compatibility check<br/>(is the document itself ATS-readable?)"]
```

> ⚠️ Uploading a new CV **replaces** the profile, including your hand edits.
> To pick up changes from the file without losing edits, use
> **Profile → Re-extract from CV**, which previews what would change first.

### 2. Finding jobs: search wide, then rank carefully

Job boards describe the same role in a dozen different ways, so finding jobs
is split into two stages tuned in opposite directions. **Retrieval** favours
recall (don't miss anything plausible). **Ranking** favours precision (put
the best fits first).

```mermaid
flowchart TD
    POS["Typed Position<br/>e.g. 'AI Engineer'"] --> QE["Query Expansion<br/>(1 LLM call, cached per Position)"]
    QE --> PH["'AI Engineer', 'ML Engineer',<br/>'Generative AI Engineer', …"]

    PH --> QS["Query-based sources<br/>SerpAPI · Wuzzuf · SimplyHired · career pages<br/>→ searched once per phrase"]
    PH --> BS["Whole-board sources<br/>Greenhouse · Lever · RemoteOK · WWR<br/>→ everything fetched, then filtered"]

    BS --> TP{"Title matches<br/>a phrase?"}
    TP -->|yes| KEEP["Candidates"]
    TP -->|no| SP{"Description semantically<br/>close to your profile?<br/>(embeddings)"}
    SP -->|yes| KEEP
    SP -->|no| DROP["Dropped<br/>(logged with reason)"]
    QS --> KEEP

    KEEP --> DEDUP["Dedupe on URL<br/>(never apply twice)"] --> RANK["Ranking Agent<br/>8 weighted factors, no LLM"]
    RANK --> GATE{"≥ minimum<br/>match score?"}
    GATE -->|yes| PIPE["Per-job pipeline ↓"]
    GATE -->|no| HOLD["Held back<br/>(saves LLM calls)"]
```

The **semantic path** is what catches a "Backend Developer" posting during a
"Software Engineer" search when the content genuinely fits you. Only jobs the
title filter rejects are embedded; a job it already accepted isn't paid for
twice.

**The match score** answers *"is this job right for me?"*. It's a weighted
sum of eight factors. Ranking deliberately uses no LLM, so ordinary search
volume never uses up a free-tier quota.

```mermaid
pie showData
    title Match score weights (agents/ranking_agent.py)
    "Skills" : 32
    "Semantic CV/JD similarity" : 20
    "Experience" : 16
    "Job title" : 12
    "Location" : 8
    "Education" : 4
    "Salary" : 4
    "Seniority" : 4
```

When a posting doesn't state something (salary and location often aren't
listed), that factor scores **neutral, not zero**. A job isn't penalised for
how its source happens to format postings.

### 3. The ATS score: requirement by requirement, fully explained

The ATS score answers a different question: *"would my CV get through this
employer's screening?"* Rather than counting keywords, the ATS agent
(`agents/ats_agent.py`):

1. extracts every requirement from the posting (one LLM call),
2. matches each requirement against your profile in up to three layers,
   stopping as soon as one layer decides,
3. computes the final score **in Python** from a credit table. No number a
   model returns ever reaches the score.

```mermaid
flowchart TD
    REQ["One requirement<br/>e.g. 'Python (required)'"] --> L1{"Layer 1 · Deterministic rules<br/>exact · alias · subset · prerequisite"}
    L1 -->|decided| CREDIT["Credit from table<br/>+ evidence line from your CV"]
    L1 -->|uncertain| L2["Layer 2 · Semantic retrieval<br/>embedding search over your CV's own lines<br/><b>nominates</b> evidence, never scores"]
    L2 --> L3["Layer 3 · One batched LLM call per job<br/>judges only the still-uncertain requirements<br/>against their nominated evidence"]
    L3 --> CREDIT
    CREDIT --> BUCKET["Bucket score = earned ÷ possible"]
    BUCKET --> SCORE["ATS score = weighted sum of buckets"]

    GUARD["False-friend guard<br/>a LangGraph <b>ReAct</b> agent ≠ <b>React</b> frontend"] -.-> L1
    IMPL["Prerequisites<br/>FastAPI + PyTorch + LLM work ⇒ credits Python"] -.-> L1
```

How the final number is weighted (`config.JOB_MATCH_WEIGHTS`):

```mermaid
pie showData
    title ATS score bucket weights
    "Required skills" : 45
    "Experience" : 20
    "Preferred skills" : 15
    "Responsibilities" : 10
    "Education" : 10
```

Buckets a posting doesn't mention (no education line, say) are left out, and
their weight is spread over the rest. Inside a bucket, a *required* skill is
worth more points than a *preferred* one. Separately, a **parse-compatibility
check** (headers, length, bullet consistency, sections) grades the CV
*document* Pass / Warning / Fail, since that's a property of the file, not of
any one job.

Every **Why?** button in the dashboard opens this breakdown: each
requirement, how it matched (exact / alias / prerequisite / semantic / not
found), the points it earned, and the CV line that proves it. It's built from
the scoring pass itself, so explaining a score costs no extra LLM call.

Details: [matching.md](matching.md) (the matcher) and
[scoring.md](scoring.md) (the scoring pipeline, with a worked example).

### 4. What happens to each job

Every job that passes the match gate goes through a small LangGraph state
machine (`orchestrator.py`):

```mermaid
stateDiagram-v2
    [*] --> score : job + profile
    score --> rewrite_cv : ATS score < FIT_THRESHOLD<br/>(or ATS_SCORE_MODE = tailored_only)
    score --> decide_apply_path : ATS score ≥ FIT_THRESHOLD

    rewrite_cv --> decide_apply_path : AUTO_APPLY_ON_TAILORED_SCORE on<br/>and tailored score clears the bar
    rewrite_cv --> notified : otherwise
    notified : Tailored CV saved,<br/>you're notified (status cv_rewritten_notify_user)
    notified --> [*]

    decide_apply_path --> auto_submit : source allowed by AUTO_APPLY_MODE
    decide_apply_path --> draft_for_review : everything else
    auto_submit --> [*]
    draft_for_review --> [*] : waits on the Applications page
```

The tailored CV is scored again against the same requirements, which costs
one extra call rather than two. You see both numbers side by side. A tailored
CV never goes out scoring lower than your own: lines the rewrite weakened are
put back, and if it still can't beat your CV, your CV is used as it is
(`agents/tailoring.py`).

### 5. Who is allowed to press "send"

```mermaid
flowchart TD
    J["Job ready to apply"] --> M{"AUTO_APPLY_MODE"}
    M -->|off| DRAFT["Draft for review"]
    M -->|whitelist · default| W{"Source in<br/>WHITELISTED_SOURCES?"}
    W -->|no| DRAFT
    W -->|yes| DRY
    M -->|any| DRY{"DRY_RUN?"}
    DRY -->|true · default| LOG["Logged only<br/>nothing leaves your machine"]
    DRY -->|false| SUB["Real submission"]
    DRAFT --> YOU["You review it on the Applications page<br/>and press Send yourself"]
```

---

## The dashboard

`frontend/` is a static site, served by the API itself at `/`. It talks to
the FastAPI backend in `api.py`.

| Page | What it's for |
|---|---|
| **Home** | Greeting, the five-stage trajectory (Search → Match → Tailor → Apply → Grow), headline numbers, recent applications, and the skill gap Sa'ei noticed most |
| **Search** | Role, experience level and freshness; the job boards to scan (*Manage sources*); *Search* runs the pipeline now; the best recent matches |
| **Applications** | Every role found, with its match and ATS scores, and a detail panel with the reasons — and **Send application**, which emails it from your Gmail |
| **CV** | Your CV, every tailored version with its before → after score, and **Test against a job**: paste a posting to see your score and a tailored CV without saving anything |
| **Profile** | The extracted profile every agent reads, in sections that open one at a time; reorder them (tailored CVs follow the order) or add your own |
| **Reports** | One column, read top to bottom: the headline numbers as a sentence, the skills that would open the most doors, where your roles come from, this week's news, and everything Sa'ei sent |
| **Settings** | Opened from your name at the bottom of the sidebar: AI model, autonomy, matching thresholds, email & reports, and help |

Styling uses Tailwind, shipped **pre-compiled** as `frontend/tailwind.css`,
and fonts are stored in the repo, so the dashboard needs no CDN and no build
step. The dashboard is desktop-only (minimum width 1100px).

---

## Project layout

```
config.py / .env        settings (the Settings page writes .env via env_store.py)
models.py / db.py       SQLite schema and sessions
cv_parser.py            PDF/DOCX → text and links
profile_store.py        the stored profile every stage reads
orchestrator.py         LangGraph state machine for one job
scheduler.py            08:00 search · 20:00 report · Monday 09:00 news
api.py                  FastAPI backend, also serves the dashboard
agents/                 search, ranking, ATS scoring, CV tailoring, applying,
                        email, reports, news, and the matcher's internals
services/               embedding cache and per-CV semantic index
jobs/                   entry points: daily_run, daily_report, weekly_news, apply_from_link
frontend/               the dashboard (7 pages + shell.js, ui.js, config.js)
bench/                  labelled matching cases, precision/recall and calibration
tests/                  pytest suite
deploy/gcp/             server setup scripts (see deployment.md)
docs/                   these guides
```
