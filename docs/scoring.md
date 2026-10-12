# Job match & ATS compatibility scoring

This document explains how the requirements-based scoring engine works —
the pipeline behind `agents/ats_agent.py`, `agents/skill_matching.py`, and
`agents/cv_profile.py`. **It is the only scoring engine in the app.** The
four-pillar scorer it replaced was removed from the code on 2026-08-26,
along with `config.SCORING_ENGINE` and the bench's engine selector. The
measurements that condemned it are kept in [The scorer this replaced, and
why](#legacy-scorer-and-why-it-was-replaced) at the bottom — they are the
argument for the design above, and every score stored before the cutover
came from it.

## Two questions, not one

The scoring engine answers two separate questions and never mixes them:

- **ATS compatibility** — *can a parser read this CV?* Depends only on your
  CV, not on any particular job. Computed once, reported as **Pass / Warning
  / Fail**, never folded into a per-job number.
- **Job match** — *how well does this candidate satisfy THIS posting?*
  Requirement-by-requirement, every point traceable to a line of your CV.

Splitting these fixes a real defect: mixing formatting into a per-job score
meant a candidate with excellent experience and slightly imperfect
formatting could rank below a weak candidate with tidy formatting. Job match
now measures fit; ATS compatibility measures whether you'll be read at all.

## The job match pipeline

```
job description ──[LLM call 1]──▶ structured requirements
                                   {name, category, importance}

your CV ─────────[LLM call 2]───▶ structured profile      (cached by CV hash)
                                   dated roles, projects,
                                   education, skills list

           requirements × profile ──▶ deterministic matching   (no LLM)
                                            │
                     still unmatched ──[LLM call 3, batched]──▶ entailment
                                            │
                                            ▼
                                   deterministic scoring        (no LLM)
                                            │
                                            ▼
                                     job match score
```

Three LLM calls per job, and the second is cached by CV content hash, so
scoring a second posting against the same CV costs two calls, not three.
This budget is a hard constraint, not a nicety: this project's own postings
carry roughly 31 requirements each, and one LLM call per requirement across
250 candidate jobs would be ~7,900 calls — well past what a free-tier
provider's daily quota survives.

**The LLM extracts and classifies. It never produces the final number.**
Every weight, credit, and total below is arithmetic in Python. That is what
makes a score reproducible and a disagreement with it actionable — you can
point at the exact line of arithmetic that produced 73%, rather than asking
a model to justify an 82 it made up.

### Stage 1 — requirement extraction (LLM call)

The job description goes to the LLM once, asked for structured output:

```json
{
  "requirements": [
    {"name": "PyTorch", "category": "technical_skill", "importance": "required"},
    {"name": "Kubernetes", "category": "tool", "importance": "preferred"}
  ],
  "years_experience_required": 3,
  "seniority": "mid"
}
```

Categories: `technical_skill`, `tool`, `domain_knowledge`, `soft_skill`,
`education`, `certification`, `experience`, `responsibility`.

Results are deduplicated on canonical form — "ML", "Machine Learning", and
"machine learning" extracted from the same posting collapse to one entry.
Without this, a verbose posting inflates its own requirement count and
dilutes every individual match: matching 9 of 26 requirements scores 0.35;
the same 9 out of a bloated 66 scores 0.14, purely from how wordy the
posting was.

### Stage 2 — CV profiling (LLM call, cached)

Your CV goes to the LLM once, asked to extract dated experience entries,
projects, education, certifications, and the literal contents of your
skills section — with each experience entry flagged `is_professional` so an
internship counts and a university course doesn't.

The result is cached to `data/cv_profile_cache.json`, keyed by a hash of the
CV text plus which model produced it. Your CV changes rarely; job
descriptions change every time. Hoisting this out of the per-job path is
what keeps the per-job cost at two calls instead of three.

**Experience years are computed from merged date intervals, not from every
4-digit number in the text.** The naive approach — `max(year) - min(year)`
over every year mentioned anywhere — reported years of experience by
spanning a degree start date to a Coursera certificate date. This project's
own CV triggered exactly that: 4 years computed against roughly one real
year of professional work. The fix parses each entry into a `(start, end)`
month interval, drops anything not flagged professional, and **merges
overlapping intervals** before summing — so two concurrent part-time roles
count once, not twice.

### Stage 3 — deterministic matching (no LLM)

For each requirement, every CV *span* (an experience bullet, a project
line, an education entry, or the skills list) is checked three ways:

- **Direct term match** — does the requirement's own word appear, or a true
  synonym from the alias table (`Postgres` for `PostgreSQL`)? Word-boundary
  matching, not substring: `"R"` no longer matches inside `"Scalable"` or
  `"Director"` the way it would under a naive `in` check. Word boundaries
  don't help when the same letters name a different skill, so a short
  `FALSE_FRIENDS` list rejects specific occurrences: "LangGraph **ReAct**
  agent" is a reasoning pattern, not the UI library, and matched a `React`
  requirement at full credit until 2026-08-27 — then handed a `JavaScript`
  row to a CV with no frontend work, since React is a prerequisite for it.
  The guard skips the *occurrence*, not the document: a CV describing both a
  ReAct agent and a React console still scores React, and the evidence
  snippet quotes the real one.
- **Prerequisite match** — does something appear that *cannot have been
  done without* the requirement? A bullet reading "built a FastAPI service
  serving a PyTorch model" is evidence of Python whether or not the word
  Python is anywhere in the CV; "queried PostgreSQL" is evidence of SQL.
  The `IMPLIED_BY` table is curated and one-way (`python ← fastapi`, never
  `fastapi ← python`) and runs through the same exclusive-group guard, so
  Kubernetes never falls out of Docker and TensorFlow never falls out of
  PyTorch. Deliberately excluded even though they look similar: Docker ←
  Kubernetes, Linux ← Docker, Machine Learning ← pandas, REST API ←
  FastAPI — in each case the second can be done by someone who does not
  have the first.
- **Hierarchy match** — does a *narrower* term appear, one that's a kind of
  the requirement without being it? `CNN` supports a `Deep Learning`
  requirement. This lookup is directional — it only runs
  requirement → evidence, never the reverse — and is guarded by an
  exclusive-group check: members of the same category (AWS/GCP/Azure,
  PyTorch/TensorFlow, Python/Java) can never satisfy each other, no matter
  how related an embedding model would say they are. This is the fix for
  the specific failure mode where cosine similarity scores `AWS`/`GCP` as
  nearly identical — they're the same *kind* of thing, which is exactly why
  similarity can't tell them apart, but neither implies the other.

Every hit found becomes a candidate. Nothing stops at the first match — the
matcher collects every way a requirement could be satisfied across every
span, then keeps the single **highest-credit** candidate. An earlier version
returned the first hit in a fixed pass order, which silently encoded a
ranking ("an exact term anywhere beats a related term in a dated role")
in the shape of a loop instead of in the credit table where it can be seen
and tuned.

### Reading a TAILORED CV back (no LLM)

Re-scoring a rewrite reads its evidence structurally out of the rendered text
(`cv_profile.spans_from_text`) instead of paying for a second parse. That puts
a different reader on each side of the before/after comparison, and on
2026-08-27 two defects in it were making tailoring *lose* points on a CV that
had genuinely improved — job 23 scored 0.3683 before and 0.2107 after.

- **A skills row read as a heading.** The skills section pattern matches
  `tools`, and a heading only had to match the start of a short line, so
  `Tools & DevOps: Git, Docker` was treated as a section header and its
  contents thrown away. Docker and Git scored `Not found` on a CV that lists
  both. A labelled line is now only a heading when the label before the colon
  is exactly a section name, and its payload is kept either way — so
  `Skills: Python, Java` opens the section *and* contributes both skills.
- **Typography the writer never chose.** Rewrites come back with U+2011
  non-breaking hyphens and curly apostrophes. The entailment pass quotes its
  evidence with an ASCII hyphen, the verification step could not find that
  quote in the line it came from, and every verdict was discarded as invented
  — the responsibilities bucket dropped from 0.60 to 0.00. Dashes, spaces and
  quotes are now folded 1:1 before any comparison.

With both fixed, that run's tailored CV scores 0.3791 against 0.3683 — a gain
rather than a 16-point loss.

### Stage 4 — entailment pass (LLM call, batched, guarded)

Whatever's left unmatched after stage 3 goes to the LLM in a single batched
call — one request covering every unresolved requirement for that job, not
one request per requirement. The prompt asks the *directional* question:
does this CV evidence **demonstrate** this requirement, not "are these
related". Two guards run on the response before anything scores:

- **Evidence must be verifiable.** A verdict citing a CV line that isn't
  actually in your CV (a paraphrase, or an invented plausible bullet) is
  discarded outright.
- **Sibling citations are rejected.** If the model tries to justify an
  `AWS` requirement with "has Google Cloud experience," that verdict is
  thrown out regardless of how confident the model was — the exclusive-group
  guard runs after the model, not instead of it.

Matches from this stage are always credited as `semantic_support` at the
`demonstrated` location — never `alias`, because an unverifiable judgment
call shouldn't earn the same credit as a curated synonym.

**A `semantic_support` row is usually a bug report about the tables.** The
model only sees what stages 3 didn't resolve, so a requirement landing here
with high confidence means the vocabulary is missing a word, and the 0.75 cap
is then punishing the tables rather than the candidate. "Agentic AI
solutions" against a bullet reading "autonomous multi-agent system of 8
specialized agents" scored 6.75/9 this way until `agentic ai` was added with
its naming variants; it now matches deterministically at 9/9. Before
adjusting any credit, check whether the term is in `ALIASES`, `HYPONYMS` or
`IMPLIED_BY` at all.

### Stage 5 — deterministic scoring (no LLM)

Every matched requirement carries two independent scores that get
multiplied together:

**Relation** — how the CV term relates to the requirement:

| Relation | Meaning | Credit |
|---|---|---|
| `exact` | the requirement's own word appears | 1.00 |
| `alias` | a true synonym appears (`Postgres` for `PostgreSQL`) | 1.00 |
| `implied` | something that requires it appears (`FastAPI` for `Python`, `PostgreSQL` for `SQL`) | 0.90 |
| `subset` | a narrower term appears (`CNN` for `Deep Learning`) | 0.80 |
| `semantic_support` | the LLM judged it demonstrated | 0.75 |
| `none` | no evidence found | 0.00 |

**Evidence location** — where in the CV the term was found:

| Location | Meaning | Multiplier |
|---|---|---|
| `demonstrated` | inside a dated experience or project entry | 1.00 |
| `claimed` | only in the skills list, no context | 0.65 |

`credit = relation_credit × location_multiplier`. So an exact term used in
a real project scores 1.00, the same term merely listed scores 0.65, and
both a prerequisite match (0.90) and a subset match (0.80) inside real work
beat a bare listed exact keyword (0.65) — this ordering was calibrated after a real example showed the
opposite (listing "SQL" outscoring described SQLAlchemy work), which
contradicted the entire premise of distinguishing demonstrated from
claimed.

The prerequisite relation was calibrated against the same kind of example.
A CV listing Python in its skills row and describing FastAPI, PyTorch and
PostgreSQL work in dated bullets scored Python at 0.65 — a keyword — while
the work that could not have happened without it counted for nothing. It
now scores 0.90 as demonstrated work. It sits below `alias` because naming
the skill is still better evidence than inferring it, and above `subset`
because "you used FastAPI, so you wrote Python" is a surer inference than
"a CNN is a kind of deep learning": the first is a hard dependency, the
second is category membership. Where a requirement is evidenced both ways
in the same CV, the higher-credit relation is the one reported.

Every requirement carries a point value by category (`technical_skill`: 10,
`domain_knowledge`: 9, `experience`: 10, `education`: 9, `certification`: 6,
`tool`: 6, `responsibility`: 5, `soft_skill`: 2), so a required Python skill
isn't weighted the same as a preferred nice-to-have. Points earned for a
requirement = `points × credit`.

Every requirement lands in **exactly one** bucket — never two, which would
double-count it:

| Bucket | Weight |
|---|---|
| Required skills & tools | 45% |
| Experience & seniority | 20% |
| Preferred skills & tools | 15% |
| Responsibilities alignment | 10% |
| Education & certifications | 10% |

A bucket's score is `earned points ÷ possible points`. **Buckets a posting
doesn't use are dropped and their weight redistributed proportionally
across the rest** — a job that states no education requirement can't cap a
candidate at 90% for a requirement nobody made.

The experience bucket is scored separately from term matching entirely: it
combines a years-required-vs-years-worked ratio with a seniority check
(against expected years per level: entry 0, mid 2, senior 5, lead 7,
manager 8), where overshooting is penalised far more gently than
undershooting — a senior engineer applying to a mid-level posting is
plausible, a candidate two levels short usually isn't.

The final job match score is the weighted sum of active bucket scores.

## How the code actually flows

The stages above describe what happens conceptually. This section is the
same pipeline traced through actual function calls, file by file, for
readers who want to jump into the source.

**Entry points — who calls the scorer, and with what engine**

There are exactly two callers, and they deliberately request different
engines:

```
orchestrator.py:score_node()
    → agents/ats_agent.py:compute_ats_score(cv_text, jd, profile=<stored profile>)

api.py:debug_ats()               [POST /api/debug/ats, the CV page's debug panel]
    → compute_ats_score(cv_text, jd, profile=<stored profile>)
```

`compute_ats_score()` is now a thin name over `compute_requirements_score()`
— kept because every caller in the app reaches scoring through it, not
because there is anything left to dispatch on:

```python
def compute_ats_score(cv_text, job_description, required_skills=None,
                      profile=None, evidence_text=None):
    return compute_requirements_score(cv_text, job_description,
                                      extracted=required_skills,
                                      profile=profile,
                                      evidence_text=evidence_text)
```

Note what `profile` is: the user's STORED profile, seeded from their CV
upload and corrected on the Profile page. Evidence comes from that record,
not from the uploaded file's text, so the skill gap describes what the
candidate has rather than what one snapshot happened to say.

**Inside `compute_requirements_score()` — the five stages as five calls**

`compute_requirements_score()` (`agents/ats_agent.py:998`) is the function
that actually walks stages 1–5. Reading it top to bottom:

```python
def compute_requirements_score(cv_text, job_description,
                                extracted=None, profile=None,
                                evidence_text=None):
    # Stage 1 — skipped if `extracted` was already passed in (rescoring path)
    requirements = (extracted if extracted and extracted.get("requirements")
                    is not None else extract_requirements(job_description))

    # Stage 2 — skipped if `profile` was already passed in
    if profile is None:
        profile = cv_profile.build_profile(cv_text)

    # Evidence spans: two different sources depending on the caller
    if evidence_text is not None:
        spans = cv_profile.spans_from_text(evidence_text)   # no LLM call
    else:
        spans = cv_profile.evidence_spans(profile, cv_text)  # from the profile

    # Stage 3 — one call per requirement, all local/deterministic
    results = [{**req, **match_requirement(req, spans)}
               for req in requirements["requirements"]]

    # Stage 4 — one batched call for everything stage 3 left unmatched
    unmatched = [r for r in results if r["match"] == "missing"
                 and r["category"] != "experience"]
    verdicts = _entailment_pass(unmatched, spans)
    # verdicts get merged back into `results` here

    # Stage 5 — bucket, weight, and sum; see compute_requirements_score's
    # tail (agents/ats_agent.py:1045 onward) for the bucketing/weighting loop
    ...
    return {"score": ..., "breakdown": ..., "requirement_results": results, ...}
```

`extract_requirements()` (line 607) and `cv_profile.build_profile()`
(`agents/cv_profile.py:370`) are the only two functions in this whole call
tree that invoke an LLM directly — both call `agents/llm.py:get_llm()` and
`.invoke()` a `SystemMessage`/`HumanMessage` pair, then parse the JSON back
out with `_safe_json_object()`. `match_requirement()` (line 723),
`_entailment_pass()`'s guards, and everything under "Stage 5" are ordinary
Python — no network call, which is what makes them safe to unit-test and
safe to re-run without a rate limit.

**The rescoring path — why `evidence_text` and `profile` exist as
parameters at all**

`api.py:debug_ats()` calls `compute_ats_score()` twice per rewrite: once
for the original CV, once for the tailored one. The second call is where
`evidence_text` and `profile` get threaded through:

```python
extra = {"evidence_text": tailored, "profile": ats["cv_profile"]}
tailored_ats = compute_ats_score(tailored, jd, required_skills=reuse,
                                 engine=primary, **extra)
```

`ats["cv_profile"]` is the exact dict `compute_requirements_score()`
returned from the *first* call — `build_profile()` was already run once, so
it's passed straight back in rather than re-derived. `evidence_text=tailored`
routes stage 3's evidence through `cv_profile.spans_from_text()`
(`agents/cv_profile.py:317`) instead of `cv_profile.evidence_spans()`
(line 221) — a plain regex/section-header walk over the rewritten markdown,
with no LLM call, versus the profile-derived version used for the original
CV. That's the entire mechanism behind "rescoring a rewrite costs one LLM
call, not two": the second `build_profile()` call — and the token budget it
would have needed — never happens.

**Where `skill_matching.py` sits underneath**

`match_requirement()` and `_entailment_pass()`'s guards never touch a CV or
job description directly — they call into `agents/skill_matching.py` for
every term-level decision:

```
match_requirement()
    ├─ skill_matching.canonical(name)         — reduce to a lookup key
    ├─ skill_matching.find_term(name, text)   — word-boundary match, direct term
    ├─ skill_matching.HYPONYMS[canon]         — candidate narrower terms
    ├─ skill_matching.entails(canon, term)    — is this hierarchy edge real
    └─ skill_matching.find_term(term, text)   — word-boundary match, hyponym

_cites_a_sibling()  (guards stage 4's LLM verdicts)
    ├─ skill_matching.find_term(...)
    └─ skill_matching.EXCLUSIVE_GROUPS        — is the cited evidence a sibling
```

None of `skill_matching.py` is LLM-backed; it's the alias/hyponym/exclusive
tables plus the normalization functions described earlier in this doc. That
separation is deliberate: everything that decides *whether two terms mean
the same thing* lives in one file with its own test suite
(`tests/test_skill_matching.py`), independent of how a requirement or a CV
line got extracted.

**The worked example below was produced this way**: stages 3 and 5 are
`match_requirement()` and `compute_requirements_score()`'s tail, actually
executed; stages 1, 2, and 4 are called out explicitly wherever this
sandbox's lack of LLM access meant they couldn't run for real.

## ATS compatibility (separate, CV-only)

`compute_ats_compatibility(cv_text)` never sees a job description — its
function signature only takes the CV. It checks:

- **Formatting** (35%) — labeled section headers as short standalone lines,
  a healthy word count (not too thin, not too long), consistent bullet use.
- **Section completeness** (30%) — do the standard sections actually exist:
  contact info, a summary, dated work experience, education, skills.
- **Contact details** (20%) — is there a findable email or phone number.
- **Text extraction quality** (15%) — how much of the extracted text is
  replacement/unreadable characters, a proxy for whether the source PDF
  used fonts or a layout a parser (this app's, or a real ATS) will choke on.

Score bands to status: **Pass** ≥ 85%, **Warning** ≥ 60%, **Fail** below
that (`config.ATS_COMPAT_PASS` / `ATS_COMPAT_WARN`).

One caution baked into the code: comparing this score across a CV rewrite
is misleading. The master CV is scored on text extracted out of a PDF; a
freshly tailored CV is scored on raw generated markdown, which reads as
better-formatted for reasons that have nothing to do with quality. Treat
this score as a property of your master CV, not something a rewrite should
be expected to move.

## Seeing it

The CV page's **ATS & Tailoring Debug** panel runs this pipeline against a
pasted job description. The requirement table shows every requirement, its relation and evidence
location, points earned out of points possible, and the exact CV line
behind the match — nothing here is a bare percentage with no explanation
attached.

Nothing run from the debug panel is written to your application history.

## The live pipeline runs this engine

The scheduler, the dashboard and every automatic run use it, because there
is nothing else to use. `compute_ats_score()` remains the shared entry point
every caller goes through.

The scorer was removed rather than left switchable once it had been the
default for a while and nothing was reaching for it: keeping ~400 lines and
an LLM call per job runnable, to reproduce numbers nobody trusts, was paying
rent on a room no one enters. What could not be removed is the stored rows
it wrote — see below.

### What the cutover invalidated, and what was done about each

A score from this engine does not mean what a legacy score meant, so three
things stopped being comparable the moment it became the default.

**Stored scores.** Every `Application` row now records
`scoring_engine` — `"legacy"` or `"requirements"`. Rows written before the
column existed come back as `"legacy"`, which is what they are, and the
dashboard's "Why?" modal picks its renderer from that field. Without it, a
pre-cutover row read under the new renderer shows an *empty* breakdown rather
than an error, which is the kind of thing nobody notices for weeks. Old rows
are **not** back-filled or rescored: that would cost real LLM calls to restate
history nobody is acting on.

**`FIT_THRESHOLD` (0.7).** Calibrated against the legacy 0.408–0.643 band,
where it was unreachable. This engine has no 0.356 constant floor, so it
scores genuinely poor matches much lower — a real posting in the database
measured **0.319** here against **0.542** under legacy. The threshold is
deliberately **left at 0.7 through the cutover** rather than guessed at:

- If the new distribution also sits below 0.7, the behaviour is "rewrite
  every job", which is exactly what the app already did. Safe, and no worse.
- A threshold set too low starts auto-applying to jobs the candidate doesn't
  match. That is not recoverable.

Recalibrate it from real runs once there are enough of them — the Dashboard
lists every score, and the debug bench scores any posting on demand. Don't set
it from a single example.

**`MATCH_SCORE_THRESHOLD` is unaffected.** It gates on the ranking agent's
`match_score`, which this engine doesn't produce.

### Cost per job on the live path

Scoring a job costs 2 LLM calls once the CV profile is cached (extraction +
entailment). A job that also gets rewritten costs 1 more for the rewrite
itself, and **0 extra for the re-score** — `orchestrator._rescore_kwargs()`
hands the tailored pass the structured extraction it already has, the CV
profile it already built, and the rewritten text as evidence. Getting this
wrong is silent: the score still comes out, it just costs two more calls than
it should, on the exact path that previously failed against Groq's
8,000-tokens-per-minute limit *after* the expensive rewrite had already been
paid for. `tests/test_engine_cutover.py` asserts the call counts.

## Calibration is ongoing, not fixed

`config.RELATION_CREDIT` and `config.EVIDENCE_MULTIPLIER` are explicitly
judgment calls, not measured constants — the file says so in a comment.
`tests/test_matching_eval.py` holds a labelled set of requirement/evidence
pairs (exact, alias, subset, none) plus a `KNOWN_GAPS` list of cases the
tables deliberately don't resolve yet, and reports accuracy alongside a
**false-equivalence count that must stay zero** — awarding full credit
where the truth is partial or nothing is the dangerous failure direction,
because it's what sends the agent applying to jobs it doesn't actually
match. Run it directly for a full report:

```
python tests/test_matching_eval.py
```

Any change to the alias table, the hierarchy table, or the credit constants
should be checked against this before it ships.

## Worked example: a real job from the database

This section runs the actual pipeline code against a real stored job and the
current CV, stage by stage, so the tables above have a concrete number
attached to them.

**The job**: id 42 in the database, *Applied AI Engineer* at Interview
Copilot AI (Oslo, remote), sourced from Wellfound. $150k–$230k, "3 years of
exp", no sponsorship. Chosen because it has an explicit `Skills` tag list and
clearly separated "Required skills" / "Useful nice-to-haves" prose sections,
so what stage 1 should extract is easy to check by eye.

**The CV**: `cv/current_cv.pdf`, parsed exactly as the app parses it — three
experience entries (a training program, an internship, a startup role), three
projects, one degree, and a technical-skills line that includes `Agent
workflows, AWS, Azure, GCP` — the same skills line that came up earlier when
we were debugging why those exact terms only scored partial credit.

One honest caveat up front: this sandbox has no outbound network access to
the configured LLM provider, so stages 1 and 2 below (which normally cost one
LLM call each) could not be run live for this walkthrough. Stage 1's output
is transcribed directly from the posting's own `Skills` / `Required skills` /
`Useful nice-to-haves` text, applying the same category/importance rules the
real extraction prompt uses. Stage 2's output is transcribed directly from
the CV text the same way. **Stages 3 and 5 — deterministic matching and
scoring — are not transcribed; they're the actual shipped functions
(`match_requirement`, `compute_requirements_score`) executed against those
inputs.** Stage 4 (entailment) needs the same unreachable LLM call, so it's
disabled for this run rather than faked — anything it would normally resolve
is left `missing` below and called out as such, not given an invented score.
ATS compatibility needs no LLM at all, so that number is fully live.

**Stage 1 — requirements (transcribed from the posting)**

| Requirement | Category | Importance |
|---|---|---|
| Python | technical_skill | required |
| Machine Learning | technical_skill | required |
| Node.js | technical_skill | required |
| PostgreSQL | tool | required |
| Websockets | technical_skill | required |
| TypeScript | technical_skill | required |
| CloudFlare Workers | tool | required |
| LLM pipelines | technical_skill | required |
| Prompting | technical_skill | required |
| Evaluation | technical_skill | required |
| Debugging | technical_skill | required |
| Data analysis | technical_skill | required |
| Speech recognition | technical_skill | preferred |
| Multi-provider LLM routing | technical_skill | preferred |

`years_experience_required: 3`. The posting states no education requirement
or explicit seniority word, so those buckets end up inactive later, exactly
as the redistribution rule describes.

**Stage 2 — CV profile (transcribed from the CV)**

Three `is_professional: true` entries (AI Research Intern at Manipal,
Jul 2025–May 2026; Full-Stack Developer & Technical Lead at Notopia,
Oct 2024–Jan 2025) and one `is_professional: false` entry (the D-Hub
training program, Jun–Jul 2026, correctly excluded from the years count
since it's a course, not employment). Merging the intervals gives **1.2
years** of professional experience — against a posting asking for 3, that's
1.8 years short.

**Stage 3 — deterministic matching (live code, real output)**

| Requirement | Relation | Location | Credit | Evidence |
|---|---|---|---|---|
| Python | implied | demonstrated | 0.90 | "…using LangChain and Flowise" — the agent bullet |
| Machine Learning | subset | demonstrated | 0.80 | "llms" in the D-Hub training bullet |
| Node.js | none | — | 0.00 | not found |
| PostgreSQL | exact | claimed | 0.65 | "PostgreSQL" — skills list only |
| Websockets | none | — | 0.00 | not found |
| TypeScript | implied | demonstrated | 0.90 | "NestJS backend services and REST APIs" |
| CloudFlare Workers | none | — | 0.00 | not found |
| LLM pipelines | alias | demonstrated | 1.00 | "llms" in the D-Hub training bullet |
| Prompting | none | — | 0.00 | not found (would go to entailment, unreachable here) |
| Evaluation | exact | demonstrated | 1.00 | "Defined evaluation criteria upfront…" |
| Debugging | none | — | 0.00 | not found |
| Data analysis | subset | claimed | 0.52 | "SQL" — skills list only |
| Speech recognition | none | — | 0.00 | not found |
| Multi-provider LLM routing | none | — | 0.00 | not found |

A few of these are worth pointing at directly:

- **Python** lands at 0.90 (`implied × demonstrated`). The word "Python"
  appears only in the skills line, so under the relations that existed
  before 2026-08-27 it scored 0.65 — a bare listed keyword. The dated
  training-program bullet describes tool-calling agents built with
  LangChain, which is not something anyone does without Python, and that is
  where the credit now comes from. **PostgreSQL** still lands at 0.65
  (`exact × claimed`): it is in the skills line and nothing in any role or
  project implies it, which is precisely the difference the two rows are
  here to show.
- **TypeScript** moves from `none` to 0.90 the same way, through "NestJS
  backend services" in the Notopia role. Nothing on this CV says
  TypeScript; the framework it names is not used without it.
- **Machine Learning** matches at `subset × demonstrated` (0.80) through
  `llms`, a narrower term inside the training-program bullet — genuinely
  demonstrated work beating a bare listed keyword, which is the exact
  ordering the earlier SQLAlchemy calibration bug was fixed to produce.
- **LLM pipelines** matches `alias × demonstrated` (1.00) through the same
  `llms` evidence — LLM/LLMs is a curated true synonym, not a hierarchy
  relation, so it's full credit rather than 0.80.
- **Data analysis** matches at `subset × claimed` (0.52) through `SQL` in
  the skills list — both discounts stack: SQL is narrower than "data
  analysis," and it's only listed, not demonstrated.
- **Node.js, Websockets, TypeScript, CloudFlare Workers, Debugging,
  Speech recognition, Multi-provider LLM routing** — genuinely absent from
  this CV. No amount of hierarchy or synonym lookup manufactures evidence
  that isn't there. **Prompting** is a near-miss: "Prompt Engineering" is on
  the CV, but that's a different canonical concept from "Prompting" in the
  current alias/hyponym tables, so this is exactly the kind of case that
  would normally reach stage 4's entailment call rather than resolving
  deterministically — left `missing` here since that call isn't reachable
  in this environment, not scored as a guess.

**Stage 5 — scoring (live code, real output)**

| Bucket | Score | Weight (redistributed) |
|---|---|---|
| Required skills & tools | 49% | 56% |
| Experience & seniority | 40% | 25% |
| Preferred skills & tools | 0% | 19% |
| Responsibilities alignment | — inactive, not stated by this posting |
| Education & certifications | — inactive, not stated by this posting |

**Job match score: 38% — Critical.**

Both numbers moved on 2026-08-27, when the prerequisite relation landed:
required skills went 39% → 49% and the overall score 32% → 38%, entirely
from the Python and TypeScript rows above. Nothing was added to the CV and
no threshold moved — two skills the work already evidenced stopped being
scored as if the work did not exist.

The two inactive buckets' 20 points of weight redistribute across the three
active ones, which is why required-skills carries 56% here instead of its
base 45%. The experience bucket at 40% reflects 1.2 actual years against 3
required — short, but not zero, since the ratio-based scoring in stage 5
penalizes a shortfall more gently than it would penalize, say, having no
professional experience at all.

**ATS compatibility (live code, no LLM, unrelated to the score above)**

`compute_ats_compatibility` on this same CV: **100% — Pass**, no issues
flagged. This number would be identical against any job posting — it's a
property of the CV file, not this match.

## The scorer this replaced, and why

**Removed from the code on 2026-08-26.** This section is kept because the
measurements are the argument for everything above, and because the scores
stored before the cutover came from it.

The original scorer (`compute_legacy_score`) folded four weighted pillars
into one number: keyword match (45%), formatting (22%), section completeness
(18%), experience alignment (15%).

Measured against the 15 real applications already in this project's
database, two of those four pillars never took the job description as an
argument at all — `formatting_score(cv_text)` and
`section_completeness_score(cv_text)` are CV-only functions. Across all 15
stored jobs, formatting returned exactly 0.800 and section completeness
returned exactly 1.000 every single time. `0.22×0.8 + 0.18×1.0 = 0.356` of
every score was therefore a constant, carrying 40% of the weight and zero
ranking signal.

The consequences compounded: every score landed in the narrow range
0.408–0.643, so nothing ever reached the 0.7 fit threshold before or after
tailoring — every job got rewritten, always, and the auto-apply gate could
never fire. The keyword pillar itself did plain substring containment
(`skill.lower() in cv_text.lower()`), which is both too loose (`"R"` matches
inside any CV containing the letter r) and too strict (a 105-character
"skill" string copied verbatim from a posting can never substring-match
anything).

Its experience pillar read years as `max(year) - min(year)` over every
number in the CV, so a graduation year and a copyright notice counted as
employment — six years for a candidate with one, always in the flattering
direction. That heuristic outlived the pillar itself: the ranking stage went
on importing it until 2026-08-26, when it was replaced there by
`cv_profile.professional_years()`, which counts dated professional entries
with overlaps merged.

**What was kept when the code went.** `formatting_score()` and
`section_completeness_score()` survive, but as inputs to
`compute_ats_compatibility()` — "can a parser read this document?" — which is
the question they were always answering. `_extract_required_years()` survives
because reading "5+ years" out of a posting is a property of the posting, and
the ranking stage still asks it. Everything else went: the LLM fit call, the
year-span estimator, substring keyword overlap, and the pillar weights.

**What could not be removed.** Fifteen `Application` rows hold legacy-shaped
breakdowns. `why-modal.js` still renders those, labelled on screen as scored
by a retired engine and not comparable with newer numbers. They are read, not
reproduced: there is no code left that could score a CV that way again.
