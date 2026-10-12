"""
CV scoring: ATS compatibility and job match.

Two questions, deliberately kept apart:

    ATS Compatibility   "can a parser read this document at all?" A property
                        of the CV alone, with no job involved, so it is
                        computed once and reported as Pass/Warning/Fail
                        rather than mixed into a per-job number.
    Job Match           "how well does this candidate satisfy THIS job?"
                        Requirement-level, evidence-backed, deterministic.

Mixing them is the defect this design exists to prevent: a candidate with
excellent experience and slightly imperfect formatting could rank below a weak
candidate with tidy formatting, because 40% of every score was a CV-level
constant that never saw the job description.

The four-pillar scorer that used to live here alongside this one was retired
once the requirements engine had replaced it everywhere. It was measured
against the 15 real applications in this project's database and had two
defects the numbers made unarguable: formatting and section completeness
returned the same value on every job (a constant carrying 40% of the weight
and zero ranking signal), and years of experience were read as
max(year) - min(year) over every number in the CV, which scores a graduation
year and a copyright notice as employment. docs/scoring.md keeps the measurements;
the code is gone rather than sitting unreachable.

What is kept from the original design, deliberately: every score carries a
breakdown and a plain-English explanation, the final number is computed in
Python rather than produced by an LLM, extracted requirements are reused when
re-scoring a tailored CV, and nothing claims a skill the CV does not support.
"""
import json
import re

from langchain_core.messages import SystemMessage, HumanMessage

import config
from agents.llm import estimate_tokens, get_llm, no_think_prefix, prepare_system, visible_content

# ---- pillar weights --------------------------------------------------------


def _rating_label(score: float) -> str:
    """Same 80/60 percentage bands real-world ATS-readiness guidance uses,
    expressed on this module's 0..1 scale."""
    if score >= 0.8:
        return "Excellent"
    if score >= 0.6:
        return "Needs Work"
    return "Critical"


# ---- pillar 2: formatting & parsability -------------------------------------
#
# cv_text has already been flattened from PDF/DOCX to plain text by
# cv_parser before this ever runs, so this can't inspect layout the way a
# real ATS parser reading the raw file would (columns, tables, fonts). What
# it checks instead are text-level signals that correlate with clean,
# parser-friendly formatting: standard section headers presented as short,
# distinct lines rather than buried in prose; a length in the healthy 1-2
# page range; and consistent use of bullet points for achievements rather
# than dense paragraphs.

_HEADER_KEYWORDS = {
    "summary": {"summary", "profile", "objective", "professional summary"},
    "experience": {"experience", "work experience", "employment", "employment history",
                   "professional experience"},
    "education": {"education", "academic background", "qualifications"},
    "skills": {"skills", "technical skills", "core skills", "key skills"},
}


def _header_like_lines(cv_text: str) -> set[str]:
    """Category names (from _HEADER_KEYWORDS) found as a short, standalone
    line -- the presentation a parser can reliably detect as a header,
    as opposed to the same word merely appearing somewhere in a sentence."""
    found = set()
    for raw_line in cv_text.split("\n"):
        line = raw_line.strip().rstrip(":").strip().lower()
        if not line or len(line) > 40:
            continue
        for category, variants in _HEADER_KEYWORDS.items():
            if line in variants:
                found.add(category)
    return found


def formatting_score(cv_text: str) -> tuple[float, list[str]]:
    issues = []

    headers_found = _header_like_lines(cv_text)
    header_score = len(headers_found) / len(_HEADER_KEYWORDS)
    if header_score < 1.0:
        missing = sorted(set(_HEADER_KEYWORDS) - headers_found)
        issues.append(f"no clearly labeled header line found for: {', '.join(missing)}")

    word_count = len(cv_text.split())
    if word_count < 150:
        length_score = max(0.0, word_count / 150)
        issues.append(f"CV text is short ({word_count} words) — may read as incomplete")
    elif word_count > 1200:
        length_score = max(0.4, 1 - (word_count - 1200) / 1200)
        issues.append(f"CV text is long (~{word_count} words) — 1-2 pages is the safer target")
    else:
        length_score = 1.0

    bullet_lines = sum(1 for l in cv_text.split("\n") if re.match(r"^\s*[-*•–]\s+", l))
    bullet_score = 1.0 if bullet_lines >= 3 else bullet_lines / 3
    if bullet_lines < 3:
        issues.append(f"only {bullet_lines} bulleted line(s) detected — bullets parse more "
                       "reliably than paragraph text")

    score = 0.5 * header_score + 0.3 * length_score + 0.2 * bullet_score
    return max(0.0, min(1.0, score)), issues


# ---- pillar 3: section completeness -----------------------------------------
#
# Deliberately checks *content* presence rather than reusing
# _header_like_lines() above -- a CV can have a real work-history section
# without the word "Experience" ever appearing as its own line (e.g. dated
# entries under a company name), and this pillar cares whether the
# substance is there, not whether it's labeled the way formatting_score()
# checks for.

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"(\+?\d[\d\-\s()]{7,}\d)")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_DEGREE_KEYWORDS = (
    "bachelor", "master", "b.sc", "bsc", "m.sc", "msc", "b.tech", "m.tech",
    "phd", "ph.d", "university", "college", "institute of technology", "diploma",
)


def section_completeness_score(cv_text: str) -> tuple[float, list[str]]:
    lower = cv_text.lower()
    present, missing = [], []

    if _EMAIL_RE.search(cv_text) or _PHONE_RE.search(cv_text):
        present.append("contact information")
    else:
        missing.append("contact information (no email or phone number found)")

    if re.search(r"\b(summary|profile|objective)\b", lower):
        present.append("a summary/objective")
    else:
        missing.append("a summary/objective")

    if len(_YEAR_RE.findall(cv_text)) >= 2 or "present" in lower:
        present.append("a work experience section with dates")
    else:
        missing.append("a work experience section with dates")

    if any(k in lower for k in _DEGREE_KEYWORDS):
        present.append("an education section")
    else:
        missing.append("an education section")

    if re.search(r"\bskills?\b", lower):
        present.append("a skills section")
    else:
        missing.append("a skills section")

    return len(present) / 5, missing


# ---- pillar 4: experience & qualification alignment -------------------------

_REQUIRED_YEARS_RE = re.compile(r"(\d+)\+?\s*(?:to\s*\d+\s*)?years?", re.IGNORECASE)


def _extract_required_years(job_description: str) -> int | None:
    """Best-effort: looks for phrasing like '5+ years' near the word
    'experience'. Returns None if the JD doesn't state a number -- common,
    and treated as 'no explicit requirement', not a penalty."""
    for match in _REQUIRED_YEARS_RE.finditer(job_description):
        window = job_description[max(0, match.start() - 40): match.end() + 20].lower()
        if "experience" in window:
            return int(match.group(1))
    return None


# ---- putting it together -----------------------------------------------------

def compute_ats_score(cv_text: str, job_description: str,
                      required_skills: list[str] | None = None,
                      profile: dict | None = None,
                      evidence_text: str | None = None) -> dict:
    """Score a candidate against a job description.

    Kept as a thin name over compute_requirements_score because every caller
    in the app reaches scoring through it. There is one engine now: the
    four-pillar scorer this module used to also host was retired once the
    requirements engine had replaced it everywhere, and its two structural
    defects -- a constant 40% of every score carrying no ranking signal, and
    years of experience read as max(year) - min(year) -- are recorded in
    docs/scoring.md rather than in code nothing calls.

    `required_skills` accepts an already-extracted requirements dict, so
    re-scoring a tailored CV reuses the extraction rather than paying for it
    twice. The name is historical; the requirements engine calls the same
    argument `extracted`.
    """
    return compute_requirements_score(cv_text, job_description,
                                      extracted=required_skills,
                                      profile=profile,
                                      evidence_text=evidence_text)


def build_improvement_explanation(original: dict, tailored: dict) -> str:
    """Before/after comparison. Deterministic -- no LLM call."""
    return build_requirements_improvement(original, tailored)


# =============================================================================
# Requirements engine
# =============================================================================
#
# Pipeline, and where each LLM call goes:
#
#   job description --[1 call]--> structured requirements
#   CV              --[1 call, CACHED BY HASH]--> structured profile
#   requirements x profile -----> deterministic token/alias/entailment match
#   still unmatched --[1 BATCHED call]--> directional entailment verdicts
#   matched requirements -------> deterministic weighted score
#
# The call budget is the binding constraint, not an implementation detail.
# Measured on this project's own data: ~31 requirements per job. One LLM call
# per requirement across 250 candidate jobs would be ~7,900 calls, and this
# project has already exhausted Groq's 200k tokens/day at ONE call per job.
# So entailment is batched into a single call covering every unmatched
# requirement, and the CV parse is cached so a run costs one call for the CV
# plus two per job rather than two per job plus one per requirement.
#
# This also belongs at the tailoring stage, not the ranking stage.
# agents/ranking_agent.py is deliberately LLM-free and scores all ~250
# retrieved jobs; this engine should only ever see the jobs that survive it.

from agents import (cv_profile, evidence_retrieval, matching_types,
                    recommendation, requirement_normalizer,
                    semantic_matching, skill_matching, soft_skills)

REQUIREMENT_CATEGORIES = (
    "technical_skill", "tool", "domain_knowledge", "soft_skill",
    "education", "certification", "experience", "responsibility",
)

BUCKET_LABELS = {
    "required_skills": "Required skills & tools",
    "experience": "Experience & seniority",
    "preferred_skills": "Preferred skills & tools",
    "responsibilities": "Responsibilities alignment",
    "education": "Education & certifications",
    "soft_skills": "Soft skills",
}

# How the CV term relates to the requirement. Deliberately NOT called
# "Equivalent" for the inferred cases: CNN supports a Deep Learning
# requirement, it is not another word for it, and a UI that says "Equivalent"
# next to a partial score is telling the user something the score contradicts.
RELATION_LABELS = {
    "exact": "Exact",
    "alias": "Alias",
    "implied": "Prerequisite",
    "subset": "Subset",
    "semantic_support": "Semantic support",
    "none": "Not found",
}

# Where the evidence sits, which multiplies the relation credit.
EVIDENCE_LABELS = {
    "demonstrated": "Demonstrated",
    "claimed": "Listed only",
    "none": "",
}

# Flat labels kept for the legacy `match` field described in _flat_match below.
MATCH_LABELS = {
    "explicit": "Demonstrated",
    "claimed": "Listed",
    "implied": "Prerequisite",
    "subset": "Subset",
    "semantic_support": "Semantic support",
    "missing": "Not found",
}


def _flat_match(relation: str, location: str) -> str:
    """Collapse the two axes into the single `match` string older callers and
    the Dashboard still read. Lossy on purpose -- new code should use
    `relation` and `evidence_location`, which is why both are returned."""
    if relation == "none":
        return "missing"
    if relation in ("exact", "alias"):
        return "explicit" if location == "demonstrated" else "claimed"
    return relation

_REQUIREMENT_EXTRACTION_PROMPT = """You extract structured hiring requirements from a job description. Respond with ONLY a JSON object, no prose, no code fences.

Schema:
{
  "requirements": [
    {"name": "short canonical name", "category": "<category>", "importance": "required|preferred",
     "type": "skill|capability|credential|experience", "concept_family": "<short label or null>",
     "evidence_expected": "hands_on|demonstrated|familiarity|any"}
  ],
  "years_experience_required": <integer or null>,
  "seniority": "intern|entry|mid|senior|lead|manager|null"
}

category is one of: technical_skill, tool, domain_knowledge, soft_skill, education, certification, experience, responsibility

Rules:
- "name" must be a SHORT canonical term ("PyTorch", "Kubernetes", "Medical imaging"), NOT a sentence copied from the posting. Write "Bachelor's degree in Computer Science" as name "Bachelor's degree" with category "education".
- importance is "required" only when the posting presents it as a must-have. Anything under "nice to have", "preferred", "bonus", or "a plus" is "preferred".
- Use category "responsibility" for what the person will DO (e.g. "Deploy models to production"), not for skills.
- Use category "experience" only for years-of-experience or seniority statements.
- Do not list the same requirement twice. Do not invent requirements the posting does not state.
- "type" is "skill" for a named thing someone either has or does not ("PyTorch", "Kubernetes"), and "capability" for something a CV demonstrates through work rather than by naming it ("stakeholder management", "requirements gathering", "campaign performance analysis"). Use "credential" for degrees and certifications, "experience" for years/seniority statements.
- "concept_family" is a short grouping label ("web_framework", "cloud_platform", "collaboration"), or null when nothing obvious fits. It is used for reporting, never for scoring.
- "evidence_expected" is what the posting asks for: "hands_on" when it wants direct use, "demonstrated" when it wants shown-through-work, "familiarity" when exposure is enough, "any" when it does not say."""

_ENTAILMENT_PROMPT = """You decide whether a CV demonstrates specific job requirements. Respond with ONLY a JSON array, no prose, no code fences.

For each requirement you are given, output:
{"requirement": "<exactly as given>", "satisfied": true|false, "evidence": "<the CV line that shows it, verbatim>", "confidence": 0.0-1.0}

Rules:
- Answer the DIRECTIONAL question: does this CV evidence DEMONSTRATE this requirement? Not "are they related", not "are they similar".
- A more specific skill satisfies a more general requirement: "Trained a CNN" DOES demonstrate "Deep learning".
- A sibling technology does NOT satisfy a requirement: Google Cloud does NOT demonstrate AWS, TensorFlow does NOT demonstrate PyTorch. Related is not the same as equivalent.
- satisfied must be false when no CV line supports it. Never write evidence that is not copied verbatim from the CV lines given.
- Being plausible for the candidate is not evidence. Only what the CV states counts.
- A SOFT SKILL is demonstrated by what the work required, not by the CV naming it: "Led the technical team and managed delivery" demonstrates team management and leadership. Lines marked "<- shows ..." were read from the whole CV as showing those soft skills; still decide whether that is the requirement asked about. Copy only the CV line itself as evidence, never the "<- shows" note."""


def extract_requirements(job_description: str) -> dict:
    """One LLM call. Structured requirements instead of a flat keyword array.

    The legacy extract_required_skills() asked for "a JSON array of strings",
    which is why the database now holds entries as unusable as "R" and a
    105-character degree sentence side by side, each contributing exactly the
    same weight to the score. Categories and importance are what let the
    scorer treat "Python (required)" differently from "Kubernetes (preferred)".
    """
    # Structured JSON out, not prose -- see cv_profile.build_profile for why
    # the reservation size matters on metered providers.
    # Sized to the posting. A scraped job description is the second-longest
    # prompt in the app, and a fixed local context truncates it silently --
    # which does not fail, it just quietly extracts the requirements from
    # whatever fitted. See agents/llm.context_for.
    #
    # Up to 4096, not a flat 2048: a requirement is ~60 tokens of JSON, and a
    # 30-requirement posting plus gpt-oss's hidden reasoning measured 1,830
    # tokens -- one longer posting away from being cut off mid-array, which
    # read as "not the requested JSON". But the reservation still has to fit
    # Groq's 8,000 tokens/minute together with the prompt, so a long posting
    # gets less room (never under 2048); _salvage_requirements covers a cut.
    system = prepare_system(_REQUIREMENT_EXTRACTION_PROMPT)
    prompt_tokens = estimate_tokens(system + (job_description or ""))
    budget = max(2048, min(4096, 7600 - prompt_tokens))
    llm = get_llm(temperature=0.0, max_tokens=budget,
                  prompt_text=system + (job_description or ""))
    resp = llm.invoke([
        SystemMessage(content=system),
        HumanMessage(content=no_think_prefix() + (job_description or "")),
    ])
    raw = visible_content(resp)
    parsed = _safe_json_object(raw) or _salvage_requirements(raw)
    extracted = _normalize_requirements(parsed)

    # A posting with no requirements is not a posting. Every bucket goes
    # inactive, every weight redistributes into nothing, and the bench reports
    # "0% — not applicable to this posting" for a job description that plainly
    # asks for things: a confident, wrong, zero. That happened for real when a
    # local thinking model answered the extraction call with its own
    # deliberation instead of JSON. Fail where the failure is, not four layers
    # downstream in a number.
    if not extracted["requirements"] and len((job_description or "").strip()) > 200:
        raise RuntimeError(
            "Requirement extraction returned no requirements for a "
            f"{len(job_description.strip())}-character posting. The model "
            "answered with something that was not the requested JSON — on a "
            "local thinking model that usually means it reasoned instead of "
            "answering. Try a non-thinking or larger model in Settings.")
    return extracted


def _safe_json_object(raw: str) -> dict:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    candidate = match.group(0) if match else raw
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _salvage_requirements(raw: str) -> dict:
    """What a cut-off answer still holds: every requirement object that arrived
    complete. An answer that ran out of tokens stops mid-array, so the whole
    thing isn't JSON -- but the 25 requirements before the cut are fine, and
    scoring on them beats failing the posting."""
    requirements = []
    for chunk in re.findall(r"\{[^{}]*\}", raw or ""):
        try:
            item = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("name"):
            requirements.append(item)
    if not requirements:
        return {}
    years = re.search(r'"years_experience_required"\s*:\s*(\d+)', raw)
    seniority = re.search(r'"seniority"\s*:\s*"(\w+)"', raw)
    return {"requirements": requirements,
            "years_experience_required": int(years.group(1)) if years else None,
            "seniority": seniority.group(1) if seniority else None}


def _normalize_requirements(parsed: dict) -> dict:
    """Coerce whatever the model returned into the shape the scorer expects.

    Also deduplicates on canonical form, so "ML", "Machine Learning" and
    "machine learning" extracted from the same posting count once. Without
    this a verbose posting inflates its own requirement count and dilutes
    every individual match -- the measured spread here is 11 to 66
    requirements per job, which under the legacy scorer meant matching 9 of 26
    scored 0.35 while matching the same 9 of 66 scored 0.14.
    """
    out, seen = [], set()
    for item in parsed.get("requirements") or []:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        canon = skill_matching.canonical(name)
        if canon in seen:
            continue
        seen.add(canon)
        category = str(item.get("category") or "technical_skill").strip().lower()
        if category not in REQUIREMENT_CATEGORIES:
            category = "technical_skill"
        importance = str(item.get("importance") or "required").strip().lower()
        if importance not in ("required", "preferred"):
            importance = "required"
        out.append(requirement_normalizer.normalize({
            "name": name,
            "raw_text": str(item.get("raw_text") or name),
            "canonical": canon,
            "category": category,
            "importance": importance,
            # Model-supplied and validated inside the normalizer, which
            # replaces anything outside the allowed vocabulary. These fields
            # change what the matcher DOES, so a hallucinated value would
            # change a score.
            "requirement_type": item.get("type") or item.get("requirement_type"),
            "concept_family": item.get("concept_family"),
            "evidence_expected": item.get("evidence_expected"),
        }).to_dict() | {"name": name, "canonical": canon,
                        "category": category, "importance": importance})

    years = parsed.get("years_experience_required")
    try:
        years = int(years) if years is not None else None
    except (TypeError, ValueError):
        years = None

    seniority = parsed.get("seniority")
    seniority = str(seniority).strip().lower() if seniority else None
    if seniority in ("null", "none", ""):
        seniority = None

    return {"requirements": out, "years_experience_required": years,
            "seniority": seniority}


# ---- buckets -----------------------------------------------------------------

def _bucket_for(requirement: dict) -> str:
    """Every requirement lands in EXACTLY ONE bucket.

    The partition is the point. Weighting "required requirements" and
    "relevant technical skills" as separate components double-counts every
    required technical skill, which quietly makes the weights mean something
    other than what the table says.
    """
    category, importance = requirement["category"], requirement["importance"]
    if category == "experience":
        return "experience"
    if category == "soft_skill":
        # Their own small bucket, required or preferred alike. Inside
        # "required skills" seven soft skills (2 pts each) outweighed Python
        # (10), and a CV rarely states them in words -- so the bucket that
        # matters most was decided by how a CV is phrased.
        return "soft_skills"
    if category in ("education", "certification"):
        return "education"
    if category == "responsibility":
        return "responsibilities"
    return "required_skills" if importance == "required" else "preferred_skills"


def _redistribute_weights(active: list[str]) -> dict[str, float]:
    """Weights for the buckets a given job actually uses, renormalised to 1.

    A posting that states no education requirement should not cap the
    candidate at 90%. This is the same objection as "if a job doesn't require
    a summary, why should the absence of a summary reduce fit" -- generalised
    so it can't recur bucket by bucket.
    """
    if not active:
        return {}
    total = sum(config.JOB_MATCH_WEIGHTS[b] for b in active)
    if total <= 0:
        share = 1.0 / len(active)
        return {b: share for b in active}
    return {b: config.JOB_MATCH_WEIGHTS[b] / total for b in active}


# ---- matching ----------------------------------------------------------------

def match_requirement(requirement: dict, spans: list[dict]) -> dict:
    """Find evidence for one requirement. No LLM call.

    Returns the match class, the evidence span behind it, and which surface
    form actually matched -- so every point awarded can be traced to a line of
    the CV. Order matters: a demonstrated span (a dated role or project) is
    preferred over a skills-list mention, because the difference between
    having USED a technology and having LISTED it is the main thing a keyword
    count throws away.
    """
    name = requirement["name"]
    canon = requirement.get("canonical") or skill_matching.canonical(name)
    long_phrase = len(name) > config.REQUIREMENT_TOKEN_MATCH_MAX_CHARS
    # Phase 6: a capability is not a word to look for. "Stakeholder
    # management" is demonstrated by a line that never contains the phrase,
    # and term-matching it can only produce two outcomes -- nothing, or a
    # posting that happens to quote itself in the CV. Both are noise, so
    # capabilities skip Level A entirely and go to retrieval, which is the
    # level built for them. Nothing is lost: a CV that DOES name the
    # capability is retrieved by that same line, at a higher similarity than
    # anything else, and still has to be adjudicated.
    capability = str(requirement.get("requirement_type") or "") == "capability"

    if not long_phrase and not capability:
        # Collect EVERY way this requirement could be satisfied, then keep the
        # best one. An earlier version returned the first hit in a fixed pass
        # order, which silently encoded a ranking -- "an exact term anywhere
        # beats a related term in a dated role" -- that belongs in the credit
        # table where it can be seen and changed, not in the shape of a loop.
        candidates = []

        for span in spans:
            location = "demonstrated" if span.get("demonstrated") else "claimed"

            hit = skill_matching.find_term(name, span["text"])
            if hit:
                # Whether the CV used the requirement's own word or a synonym.
                # Same credit, but worth showing: "Postgres (synonym of
                # PostgreSQL)" is a more useful line than a bare tick, and it
                # makes a bad alias-table entry visible.
                relation = ("exact"
                            if skill_matching.normalize(hit) == skill_matching.normalize(name)
                            else "alias")
                candidates.append((relation, location, hit, span,
                                   None if relation == "exact"
                                   else f"“{hit}” is a synonym of “{name}”"))

            # Prerequisite. Someone who built a FastAPI service wrote Python
            # and someone who queried PostgreSQL wrote SQL -- so this is not
            # "related work", it is the requirement itself under another name,
            # and it counts as demonstrated when the bullet is dated. Checked
            # before the hierarchy below because it scores higher; the
            # candidate ranking would pick it anyway, and doing it in this
            # order keeps the two tables' roles legible.
            for evidence_term in skill_matching.implying_terms(name):
                sub_hit = skill_matching.find_term(evidence_term, span["text"])
                if sub_hit:
                    candidates.append((
                        "implied", location, sub_hit, span,
                        f"“{sub_hit}” requires “{name}”, so this work used it",
                    ))

            # Curated hierarchy. A "Deep learning" requirement is supported by
            # "CNN"; the reverse is not, and the sibling guard means "Google
            # Cloud" can never support "AWS".
            for evidence_term in skill_matching.HYPONYMS.get(canon, ()):
                if not skill_matching.entails(canon, evidence_term):
                    continue
                sub_hit = skill_matching.find_term(evidence_term, span["text"])
                if sub_hit:
                    candidates.append((
                        "subset", location, sub_hit, span,
                        f"“{sub_hit}” is a kind of “{canon}”, which supports the "
                        f"requirement without being it"))
                    break  # one hyponym per span is enough

        if candidates:
            best = max(candidates, key=_candidate_rank)
            return _outcome(best[0], best[1], best[2], best[3], via=best[4])

        # Same words, any order: "Computer Science degree" against "Bachelor
        # Degree Computer Science". Every content word of the requirement has
        # to sit close together in one line (filler like "degree", "in",
        # "or related field" is ignored), so it is the requirement itself,
        # not a coincidence of words across a paragraph.
        credential = requirement.get("category") in ("education", "certification")
        word_hits = []
        for span in spans:
            hit = skill_matching.find_words_any_order(name, span["text"], allow_single=credential)
            if hit:
                location = "demonstrated" if span.get("demonstrated") else "claimed"
                word_hits.append(("exact", location, hit, span,
                                  f"the same words in a different order: “{hit}”"))
        if word_hits:
            best = max(word_hits, key=_candidate_rank)
            return _outcome(best[0], best[1], best[2], best[3], via=best[4])

    return {
        "match": "missing", "relation": "none", "evidence_location": "none",
        "relation_label": RELATION_LABELS["none"], "evidence_label": "",
        "credit": 0.0, "matched_form": None, "evidence": None,
        "evidence_source": None,
        "via": ("phrase too long for term matching — judged by entailment instead"
                if long_phrase else
                "a capability, not a keyword — judged from retrieved evidence"
                if capability else None),
    }


_RELATION_RANK = {"exact": 4, "alias": 3, "implied": 2, "subset": 1,
                  "semantic_support": 0}


def _candidate_rank(candidate: tuple) -> tuple:
    """Order candidate matches: credit first, then directness.

    Credit leads because it is the number that actually scores, so the choice
    can never disagree with the points shown. The tie-breaks only decide which
    of two equally-scoring matches gets DISPLAYED, and there the more direct
    evidence is the more useful thing to show the user.
    """
    relation, location = candidate[0], candidate[1]
    return (config.match_credit(relation, location),
            _RELATION_RANK.get(relation, 0),
            1 if location == "demonstrated" else 0)


def _outcome(relation: str, location: str, hit: str, span: dict,
             via: str | None = None) -> dict:
    notes = [via] if via else []
    if location == "claimed":
        # Without this the evidence column just echoes the requirement back --
        # "AWS" as proof of AWS says nothing, and the discount looks arbitrary.
        # Spell out WHERE the term was found and what that costs, because the
        # fix ("mention it in the role where you used it") is only obvious once
        # you know why the points were withheld.
        percent = round(config.EVIDENCE_MULTIPLIER["claimed"] * 100)
        notes.append(f"found only in your skills list — no dated role or project "
                     f"mentions it, so it earns {percent}% of the credit")
    return {
        "match": _flat_match(relation, location),
        "relation": relation,
        "evidence_location": location,
        "relation_label": RELATION_LABELS[relation],
        "evidence_label": EVIDENCE_LABELS[location],
        "credit": round(config.match_credit(relation, location), 4),
        "matched_form": hit,
        "evidence": span["text"],
        "evidence_source": span["source"],
        "via": " · ".join(notes) or None,
    }


def _soft_skill_block(requirements: list[dict], soft_lines: dict[str, list[str]],
                      work_lines: list[str]) -> str:
    """One block for every soft-skill requirement: the CV's work lines, with
    the ones the whole-CV read found soft skills in (agents/soft_skills.py)
    marked with what they show, then the requirements to judge.

    All the work lines, not just the marked ones: the read lists the obvious
    skills, and "experimentation mindset" can still sit in a line it didn't
    flag. Retrieval by similarity doesn't rescue that case -- soft skills sit
    far from any one line in embedding space, so it routinely returns nothing
    for all of them. Printed once for every soft skill, so the cost is one
    copy of the work section."""
    ordered = list(dict.fromkeys(list(soft_lines) + list(work_lines)))
    lines = "\n".join(
        f"  - {line}  <- shows {', '.join(soft_lines[line])}" if line in soft_lines else f"  - {line}"
        for line in ordered)
    names = "\n".join(f"  - {r['name']}" for r in requirements)
    return (f"Soft skills (each judged separately):\n{names}\n"
            f"CV lines that show soft skills:\n{lines}")


def _entailment_payload(unmatched: list[dict], evidence_spans: list[dict],
                        decisions: dict | None,
                        soft_lines: dict[str, list[str]] | None = None) -> str | None:
    """The user message for the adjudication call, or None if there is nothing
    worth asking.

    Two shapes. With retrieval, the prompt is per requirement and quotes only
    the lines retrieval nominated for it; a requirement retrieval found
    nothing for is dropped from the call entirely rather than asked about
    against irrelevant text -- that is the "low similarity -> no match" band
    doing its job, and it is why enabling retrieval can only shrink this
    prompt. Without retrieval, every requirement is asked against the same
    slab, exactly as before.

    Soft-skill requirements are pulled out first whenever the whole-CV soft
    skill read found anything (`soft_lines`): they are judged against those
    lines, which retrieval by similarity can't find for them.
    """
    soft_block = None
    if soft_lines:
        soft = [r for r in unmatched if r.get("category") == "soft_skill"]
        if soft:
            soft_block = _soft_skill_block(soft, soft_lines,
                                           [sp["text"] for sp in evidence_spans[:40]])
            unmatched = [r for r in unmatched if r.get("category") != "soft_skill"]

    if decisions or (soft_block and not unmatched):
        blocks, unnarrowed = [], []
        for requirement in unmatched:
            decision = decisions.get(_requirement_id(requirement))
            if decision is not None and decision.route == "adjudicate":
                lines = "\n".join(f"  - {span.text}" for span in decision.spans())
                blocks.append(f'Requirement: {requirement["name"]}\n'
                              f"Candidate CV lines:\n{lines}")
            elif not config.SEMANTIC_RETRIEVAL_STRICT:
                # Uncalibrated default: retrieval NARROWS the question, it
                # never deletes it. A floor set too high otherwise removes a
                # requirement from the call silently, and the row comes back
                # "Not found" as if the CV had nothing -- which is exactly
                # what happened to three requirements on a real tailored CV
                # that had scored them the run before. (STRICT, for calibrated
                # deployments, trusts the floor and drops it: the plan's "low
                # similarity -> no match" band.)
                unnarrowed.append(requirement["name"])
        if unnarrowed:
            # Every requirement retrieval found nothing for is asked against
            # the SAME general slab -- so it is printed once, with the names
            # listed under it. Repeating it per requirement made this prompt
            # grow by ~1k tokens per requirement: fifteen of them reached
            # 15k tokens and Groq's free tier (8k tokens/minute, prompt plus
            # reserved output) rejected the call before the model ran.
            general = "\n".join(f"  - {s['text']}" for s in evidence_spans[:40])
            names = "\n".join(f"  - {name}" for name in unnarrowed)
            blocks.append(f"Requirements (each judged separately):\n{names}\n"
                          f"Candidate CV lines for these requirements:\n{general}")
        if soft_block:
            blocks.append(soft_block)
        if not blocks:
            return None
        return ("Judge each requirement against ONLY the CV lines listed "
                "for it.\n\n" + "\n\n".join(blocks))

    lines = [f"- {s['text']}" for s in evidence_spans[:80]]
    wanted = [r["name"] for r in unmatched]
    return ("CV lines:\n" + "\n".join(lines)
            + "\n\nRequirements to judge:\n" + "\n".join(f"- {w}" for w in wanted)
            + (f"\n\n{soft_block}" if soft_block else ""))


def _requirement_id(requirement: dict) -> str:
    return matching_types.Requirement.from_dict(requirement).id


def _entailment_pass(unmatched: list[dict], spans: list[dict],
                     decisions: dict | None = None,
                     soft_lines: dict[str, list[str]] | None = None) -> dict[str, dict]:
    """One batched LLM call covering every still-unmatched requirement.

    Batched, not per-requirement, for the reason set out at the top of this
    section: per-requirement calls do not fit any free tier this project runs
    on. Only spans that represent real work are offered as evidence, and any
    verdict citing a sibling technology is discarded before it can score --
    the model is not trusted to hold the AWS/GCP line on its own.

    Level C of the three-level matcher. `decisions` is what retrieval
    nominated (see agents/semantic_matching.py): when it is present, each
    requirement is shown ITS OWN handful of closest lines instead of the same
    first-80 slab of the CV. Same call, same cost, a far better question --
    "stakeholder management" now arrives next to the line about coordinating
    with physicians and product owners rather than buried under thirty
    bullets about model training. Without retrieval (no embedding provider,
    or SEMANTIC_RETRIEVAL off) the old whole-CV payload is used unchanged, so
    this path degrades rather than failing.
    """
    if not unmatched or not config.REQUIREMENT_ENTAILMENT:
        return {}

    evidence_spans = [s for s in spans if s.get("demonstrated")] or spans
    payload = _entailment_payload(unmatched, evidence_spans, decisions, soft_lines)
    if payload is None:
        return {}
    system = prepare_system(_ENTAILMENT_PROMPT)
    llm = get_llm(temperature=0.0, max_tokens=1536, prompt_text=system + payload)
    resp = llm.invoke([
        SystemMessage(content=system),
        HumanMessage(content=payload),
    ])

    verdicts: dict[str, dict] = {}
    for item in _safe_json_list_of_objects(visible_content(resp)):
        name = str(item.get("requirement") or "").strip()
        if not name or not item.get("satisfied"):
            continue
        evidence = str(item.get("evidence") or "").split("<- shows")[0].strip()
        # The soft-skill lines come from the stored profile, which a tailored
        # CV's rewritten text may phrase differently -- they were verified
        # against the profile when they were read, and a rewrite can't change
        # the work they describe.
        soft_evidence = [{"text": line} for line in (soft_lines or {})]
        if not evidence or not (_evidence_is_real(evidence, evidence_spans)
                                or _evidence_is_real(evidence, soft_evidence)):
            continue
        if _cites_a_sibling(name, evidence):
            continue
        # The LLM only ever sees demonstrated spans (or all spans when there
        # are none), so its verdicts are scored at that location. The relation
        # is semantic_support, never alias -- an unverifiable judgement should
        # not earn the same credit as a curated synonym.
        verdicts[name.lower()] = {
            "match": _flat_match("semantic_support", "demonstrated"),
            "relation": "semantic_support",
            "evidence_location": "demonstrated",
            "relation_label": RELATION_LABELS["semantic_support"],
            "evidence_label": EVIDENCE_LABELS["demonstrated"],
            "credit": round(config.match_credit("semantic_support", "demonstrated"), 4),
            "matched_form": None, "evidence": evidence,
            "evidence_source": "llm_entailment",
            "via": f"judged by the model to demonstrate this requirement "
                   f"(confidence {float(item.get('confidence') or 0):.2f})",
        }
        # Provenance: which candidate retrieval offered, how close it was and
        # where it ranked. A verdict that looks wrong is then traceable to
        # what the model was shown, not just to what it said.
        candidate = _retrieved_candidate(name, evidence, decisions)
        if candidate:
            verdicts[name.lower()].update({
                "semantic_similarity": candidate.similarity,
                "retrieval_rank": candidate.rank,
                "confidence_band": candidate.band,
                "evidence_source": "semantic_retrieval+llm",
            })
    return verdicts


def _retrieved_candidate(requirement_name: str, evidence: str, decisions):
    """The candidate whose span the model actually quoted, if any."""
    if not decisions:
        return None
    target = skill_matching.normalize(evidence)[:60]
    for decision in decisions.values():
        if skill_matching.normalize(decision.requirement.raw_text) != \
                skill_matching.normalize(requirement_name):
            continue
        for candidate in decision.candidates:
            if candidate.span and target and target in skill_matching.normalize(candidate.span.text):
                return candidate
        return decision.best
    return None


def _safe_json_list_of_objects(raw: str) -> list[dict]:
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    candidate = match.group(0) if match else raw
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return []
    return [x for x in parsed if isinstance(x, dict)] if isinstance(parsed, list) else []


def _evidence_is_real(evidence: str, spans: list[dict]) -> bool:
    """Reject a verdict whose quoted evidence is not actually in the CV.

    The prompt says to copy verbatim, but a model under pressure to find a
    match will paraphrase or invent a plausible bullet. Since the whole claim
    of this engine is that scores are evidence-backed, an unverifiable quote
    has to be thrown away rather than displayed as proof.
    """
    needle = skill_matching.normalize(evidence)[:60]
    if not needle:
        return False
    return any(needle in skill_matching.normalize(s["text"]) for s in spans)


def _cites_a_sibling(requirement: str, evidence: str) -> bool:
    """Discard a verdict where the cited evidence names a sibling of the
    requirement and not the requirement itself -- "has Google Cloud" offered
    as proof of AWS. This runs after the model, because the guard has to hold
    regardless of how persuasive the model's reasoning was."""
    if skill_matching.find_term(requirement, evidence):
        return False
    for group in skill_matching.EXCLUSIVE_GROUPS:
        canon = skill_matching.canonical(requirement)
        if canon not in {skill_matching.canonical(m) for m in group}:
            continue
        for member in group:
            if skill_matching.canonical(member) == canon:
                continue
            if skill_matching.find_term(member, evidence):
                return True
    return False


# ---- scoring -----------------------------------------------------------------

_SENIORITY_ORDER = ["intern", "entry", "mid", "senior", "lead", "manager"]


def _seniority_score(required: str | None, cv_years: float) -> tuple[float | None, str]:
    """Seniority fit from years actually worked, not from an LLM's impression.

    Overshooting is penalised far more gently than undershooting: a senior
    engineer applying to a mid-level role is a plausible application, whereas
    a candidate two levels short of the requirement is usually not.
    """
    if not required or required not in _SENIORITY_ORDER:
        return None, ""
    expected_years = {"intern": 0, "entry": 0, "mid": 2, "senior": 5,
                      "lead": 7, "manager": 8}[required]
    if cv_years >= expected_years:
        return 1.0, (f"Posting reads as {required}-level (~{expected_years}+ years); "
                     f"the CV's dated professional entries total {cv_years} years.")
    if expected_years == 0:
        return 1.0, f"Posting reads as {required}-level, which sets no years bar."
    ratio = max(0.0, cv_years / expected_years)
    return ratio, (f"Posting reads as {required}-level (~{expected_years}+ years); "
                   f"the CV's dated professional entries total {cv_years} years.")


def _experience_bucket(requirements: dict, profile: dict) -> tuple[float | None, dict]:
    """Years and seniority, computed from merged date intervals.

    Replaces max(year) - min(year), which for a CV reading "Education
    2019-2023 / Internship 2024 / Project 2025" reported six years of
    experience for someone with about one. Returns None when the posting
    states neither a years figure nor a seniority level -- an absent
    requirement is not a failed one, so the bucket drops out and its weight
    goes to the buckets that do apply.
    """
    years_required = requirements.get("years_experience_required")
    seniority = requirements.get("seniority")
    cv_years = cv_profile.professional_years(profile)

    parts, notes = [], []
    if years_required:
        ratio = min(1.0, cv_years / years_required) if years_required > 0 else 1.0
        parts.append(ratio)
        notes.append(
            f"Posting asks for {years_required}+ years. The CV's dated professional "
            f"entries total {cv_years} years once overlapping roles are merged"
            + (", which meets the bar." if cv_years >= years_required
               else f", about {round(years_required - cv_years, 1)} short.")
        )

    sen_score, sen_note = _seniority_score(seniority, cv_years)
    if sen_score is not None:
        parts.append(sen_score)
        notes.append(sen_note)

    if not parts:
        return None, {"cv_years": cv_years,
                      "note": "Posting states no years-of-experience or seniority "
                              "requirement, so this component does not apply and its "
                              "weight is redistributed."}

    return sum(parts) / len(parts), {
        "cv_years": cv_years, "years_required": years_required,
        "seniority_required": seniority, "note": " ".join(notes),
    }


def compute_requirements_score(cv_text: str, job_description: str,
                               extracted=None, profile: dict | None = None,
                               evidence_text: str | None = None) -> dict:
    """Job match: requirement-level, evidence-backed, deterministically scored.

    `extracted` accepts an already-extracted requirements dict, so re-scoring a
    tailored CV against the same posting reuses the extraction rather than
    paying for it twice -- the same optimisation the legacy path uses, kept
    because it is what makes the before/after comparison affordable.

    The LLM extracts and classifies. It never produces the final number: every
    weight, credit and total below is arithmetic in Python, which is what makes
    a score reproducible and a disagreement with it actionable.
    """
    if isinstance(extracted, dict) and extracted.get("requirements") is not None:
        requirements = extracted
    else:
        requirements = extract_requirements(job_description)

    if profile is None:
        profile = cv_profile.build_profile(cv_text)

    if evidence_text is not None:
        # Scoring a REWRITTEN CV. Dates and seniority come from the profile
        # passed in (the master CV's, already cached), because the
        # no-fabrication rule means a rewrite cannot change the work history.
        # Only the wording changes, so evidence is read structurally from the
        # new text -- no second LLM call, which is both wasted money and, on a
        # metered provider, a real failure point after the expensive rewrite
        # has already succeeded.
        spans = cv_profile.spans_from_text(evidence_text)
    else:
        spans = cv_profile.evidence_spans(profile, cv_text)

    results = []
    for req in requirements["requirements"]:
        outcome = match_requirement(req, spans)
        results.append({**req, **outcome})

    # What the adjudication call is asked about. "Missing" is the obvious
    # case; a CLAIMED-only row is the subtle one, and it cost a real run 10
    # points: the tailored CV said "full-stack development" in its summary,
    # the deterministic pass matched it there at exact x claimed (0.65), the
    # row stopped being "missing", and so the pass that had previously found
    # it DEMONSTRATED in a dated role was never asked. A keyword in a summary
    # outranked the job it describes.
    #
    # So a row whose only evidence is a claim is still offered for judgement,
    # and the verdict is taken only when it beats what the tables found --
    # see the credit comparison below.
    unmatched = [r for r in results
                 if r["category"] != "experience"
                 and (r["match"] == "missing"
                      or r.get("evidence_location") == "claimed")]

    # Level B. The index is per CV and built once for the whole job; a
    # requirement the deterministic pass already resolved never reaches it,
    # so a CV of well-known technologies costs nothing here at all.
    # Soft skills: read from the whole profile once (cached per profile) when
    # the posting asks for any the tables couldn't settle. Read from the
    # PROFILE's spans even when scoring a rewrite -- the work is the same.
    soft_lines = None
    if config.SOFT_SKILL_INFERENCE and any(r.get("category") == "soft_skill" for r in unmatched):
        profile_spans = cv_profile.evidence_spans(profile, cv_text) if evidence_text is not None else spans
        soft_lines = soft_skills.evidence_lines(soft_skills.demonstrated_soft_skills(profile_spans)) or None
    retrievable = [r for r in unmatched if not (soft_lines and r.get("category") == "soft_skill")]
    index = evidence_retrieval.build_index(spans) if retrievable else None
    decisions = semantic_matching.shortlist(retrievable, spans, index=index) if retrievable else {}
    verdicts = _entailment_pass(unmatched, spans, decisions=decisions, soft_lines=soft_lines)
    for row in results:
        verdict = verdicts.get(row["name"].lower())
        # Better credit only. A model verdict never overwrites a stronger
        # deterministic match: semantic_support is capped below every curated
        # relation precisely so an opinion cannot outrank a table.
        if verdict and float(verdict.get("credit") or 0.0) > float(row.get("credit") or 0.0):
            row.update(verdict)

    buckets: dict[str, dict] = {}
    for row in results:
        bucket = _bucket_for(row)
        if bucket == "experience":
            continue  # scored from dates, not from term matching
        points = config.REQUIREMENT_POINTS.get(row["category"], 5)
        credit = row.get("credit", 0.0)
        entry = buckets.setdefault(bucket, {"earned": 0.0, "possible": 0.0, "items": []})
        entry["possible"] += points
        entry["earned"] += points * credit
        entry["items"].append({**row, "points": points,
                               "points_earned": round(points * credit, 2)})

    exp_score, exp_detail = _experience_bucket(requirements, profile)
    if exp_score is not None:
        buckets["experience"] = {"earned": exp_score, "possible": 1.0,
                                 "items": [], "detail": exp_detail}

    active = [b for b in config.JOB_MATCH_WEIGHTS if b in buckets and buckets[b]["possible"] > 0]
    weights = _redistribute_weights(active)

    breakdown = {}
    for bucket in active:
        data = buckets[bucket]
        score = data["earned"] / data["possible"] if data["possible"] else 0.0
        breakdown[bucket] = {
            "label": BUCKET_LABELS[bucket],
            "score": round(max(0.0, min(1.0, score)), 4),
            "weight": round(weights[bucket], 4),
            "base_weight": config.JOB_MATCH_WEIGHTS[bucket],
            "items": data["items"],
            "detail": data.get("detail"),
        }

    final_score = sum(b["score"] * b["weight"] for b in breakdown.values())

    retrieval = (semantic_matching.retrieval_report(decisions, index)
                 if decisions or index else None)

    missing = [r["name"] for r in results if r["match"] == "missing"]
    matched = [r["name"] for r in results if r["match"] != "missing"]
    inactive = [b for b in config.JOB_MATCH_WEIGHTS if b not in breakdown]

    return {
        "score": round(final_score, 4),
        "engine": "requirements",
        # What fraction of the posting's requirements found ANY evidence.
        # Deliberately not called a keyword score: nothing here counts
        # keywords, and the number says how much of the job is covered.
        "requirement_coverage": round(
            len(matched) / len(results), 4) if results else 0.0,
        # Legacy-compatible keys so orchestrator.py, api.py and the dashboard
        # keep working against either engine without branching. DEPRECATED --
        # remove once nothing reads it; `requirement_coverage` is the same
        # number under an honest name.
        "keyword_score": round(
            len(matched) / len(results), 4) if results else 0.0,
        "llm_score": None,
        "missing_skills": missing,
        "required_skills": [r["name"] for r in results],
        "matched_skills": matched,
        "breakdown": breakdown,
        "requirements": requirements,
        "requirement_results": results,
        "inactive_buckets": inactive,
        "cv_profile_cached": bool(profile.get("_cached")),
        # Returned so a caller re-scoring a rewrite can hand it straight back
        # instead of paying for the parse twice, and so the debug bench can
        # show HOW the CV was read -- a wrong score is usually a wrong parse,
        # and that is invisible from the score alone.
        "cv_profile": profile,
        "cv_years": cv_profile.professional_years(profile),
        # What the semantic layer did, or why it did nothing. Reported rather
        # than logged: the thresholds it routes on are guesses until someone
        # looks at this distribution against labelled cases, and a reader
        # seeing "unavailable: connection refused" learns something a missing
        # match never tells them.
        "retrieval": retrieval,
        "explanation": build_requirements_explanation(final_score, breakdown, results, inactive),
        # Phase 8. Deterministic, derived from the rows above, and separating
        # the gaps a rewrite may honestly close from the ones it must not
        # touch. See agents/recommendation.py.
        "recommendation": recommendation.build_recommendation(
            {"score": final_score, "requirement_results": results,
             "breakdown": breakdown, "cv_years": cv_profile.professional_years(profile)},
            rating=_rating_label(final_score)),
    }


def build_requirements_explanation(final_score: float, breakdown: dict,
                                   results: list[dict], inactive: list[str]) -> str:
    """Plain-English, deterministic. Every line traces to a requirement and the
    CV text that satisfied it, so the number is arguable rather than opaque."""
    lines = [f"Job Match: {round(final_score * 100)}% ({_rating_label(final_score)})", ""]

    for key, data in breakdown.items():
        lines.append(f"{data['label']} — {round(data['score'] * 100)}% "
                     f"(weight {round(data['weight'] * 100)}%)")
        if data.get("detail", {}) and data["detail"].get("note"):
            lines.append(f"  {data['detail']['note']}")
        for item in data["items"]:
            if item["relation"] == "none":
                lines.append(f"  [Not found] {item['name']} "
                             f"({item['importance']}, 0/{item['points']} pts)")
                continue
            label = item["relation_label"]
            if item["evidence_location"] == "claimed":
                label += ", listed only"
            evidence = (item["evidence"] or "")[:110]
            lines.append(f"  [{label}] {item['name']} "
                         f"({item['points_earned']}/{item['points']} pts) — \"{evidence}\"")
            if item.get("via"):
                lines.append(f"      {item['via']}")
        lines.append("")

    if inactive:
        labels = ", ".join(BUCKET_LABELS[b] for b in inactive)
        lines.append(f"Not applicable to this posting, weight redistributed: {labels}.")

    return "\n".join(lines).strip()


def build_requirements_improvement(original: dict, tailored: dict) -> str:
    """Before/after for the requirements engine.

    Reports which requirements the rewrite genuinely moved, and is explicit
    about the fact that a rewrite cannot change the candidate's dated work
    history -- so the experience component holding still is correct behaviour,
    not a bug.
    """
    delta = round((tailored["score"] - original["score"]) * 100)
    sign = "+" if delta >= 0 else ""
    lines = [
        f"Tailored Job Match: {round(tailored['score'] * 100)}% "
        f"({_rating_label(tailored['score'])}), up from "
        f"{round(original['score'] * 100)}% ({_rating_label(original['score'])}) "
        f"— {sign}{delta} points for this job.",
        "",
    ]

    before = {r["name"]: r["match"] for r in original.get("requirement_results", [])}
    after = {r["name"]: r["match"] for r in tailored.get("requirement_results", [])}

    newly = [n for n, m in after.items()
             if m != "missing" and before.get(n) == "missing"]
    lost = [n for n, m in after.items()
            if m == "missing" and before.get(n, "missing") != "missing"]
    still = [n for n, m in after.items() if m == "missing"]

    lines.append("Newly evidenced: " + (", ".join(newly) if newly else "none."))
    if lost:
        lines.append("LOST by the rewrite (was evidenced before, is not now): "
                     + ", ".join(lost) + ".")
    lines.append("Still not evidenced: " + (", ".join(still) if still else "none."))
    lines.append("")

    for key, data in tailored.get("breakdown", {}).items():
        before_score = original.get("breakdown", {}).get(key, {}).get("score")
        if before_score is None:
            continue
        lines.append(f"{data['label']}: {round(before_score * 100)}% → "
                     f"{round(data['score'] * 100)}%.")

    lines.append("")
    lines.append("Experience & seniority is computed from the CV's dated entries, so a "
                 "rewrite cannot move it — the no-fabrication rule means the work "
                 "history itself is unchanged. Any movement elsewhere comes from "
                 "requirements the rewrite surfaced evidence for.")
    return "\n".join(lines)


# ---- ATS compatibility (job-description independent) -------------------------

def compute_ats_compatibility(cv_text: str) -> dict:
    """Can a parser read this CV? Nothing to do with any particular job.

    Kept strictly separate from job match because mixing them produced the
    defect this rewrite exists to fix: a candidate with excellent experience
    and slightly imperfect formatting could rank below a weak candidate with
    tidy formatting, and 40% of every job's score was a CV-level constant.

    Reported as a status rather than a number folded into a ranking, because
    formatting is a gate to being read at all, not evidence of being
    qualified.

    One caution on comparing this across a rewrite: the master CV is measured
    on text extracted out of a PDF, whereas a tailored CV is raw generated
    markdown. Literal "- " bullets in the latter score better for reasons that
    have nothing to do with quality, which is exactly how the legacy scorer
    manufactured its improvements. Treat this as a property of the master CV.
    """
    fmt_score, fmt_issues = formatting_score(cv_text)
    section_score, missing_sections = section_completeness_score(cv_text)

    contact = []
    if not _EMAIL_RE.search(cv_text):
        contact.append("no email address found")
    if not _PHONE_RE.search(cv_text):
        contact.append("no phone number found")
    contact_score = (2 - len(contact)) / 2

    # Text extracted from a PDF that used unusual glyphs, ligatures or columns
    # comes back peppered with replacement characters. A high ratio means the
    # parser this app used already struggled -- and a real ATS parser will too.
    total = max(1, len(cv_text))
    junk = sum(cv_text.count(ch) for ch in ("�", "\x00", ""))
    junk_ratio = junk / total
    extraction_score = 1.0 if junk_ratio < 0.001 else max(0.0, 1 - junk_ratio * 50)
    extraction_issues = ([] if junk_ratio < 0.001 else
                         [f"{junk} unreadable character(s) in the extracted text "
                          f"— the PDF may use non-standard fonts or columns"])

    score = (0.35 * fmt_score + 0.30 * section_score
             + 0.20 * contact_score + 0.15 * extraction_score)

    if score >= config.ATS_COMPAT_PASS:
        status = "Pass"
    elif score >= config.ATS_COMPAT_WARN:
        status = "Warning"
    else:
        status = "Fail"

    return {
        "score": round(score, 4),
        "status": status,
        "checks": {
            "formatting": {"score": round(fmt_score, 4), "issues": fmt_issues},
            "sections": {"score": round(section_score, 4), "missing": missing_sections},
            "contact": {"score": contact_score, "issues": contact},
            "text_extraction": {"score": round(extraction_score, 4),
                                "issues": extraction_issues},
        },
        "issues": fmt_issues + [f"missing {m}" for m in missing_sections]
                  + contact + extraction_issues,
    }
