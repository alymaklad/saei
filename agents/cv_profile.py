"""
Structured CV parsing, cached by CV hash.

Replaces _estimate_cv_experience_years() from the original ats_agent, which
computed years of experience as:

    max(years_mentioned) - min(years_mentioned)

The module's own docstring conceded this "can't distinguish an employment date
range from, say, a graduation year". In practice that is not a rough proxy but
a wrong answer. For a CV reading

    Education   2019-2023
    Internship  2024
    Project     2025

it returns 6 years of experience for someone with roughly one. Any 4-digit
number in range counts -- a citation year, "ImageNet 2012", a copyright
notice. The error is unbounded and always in the flattering direction, and it
fed a pillar worth 15% of the score.

Here the CV is parsed into dated entries instead, professional entries are
identified as such, and their intervals are MERGED before summing -- so two
concurrent roles, or an internship overlapping a degree, count once rather
than twice.

Why this is cached
------------------
The CV changes rarely; job descriptions change every job. Hoisting the parse
out of the per-job path means a run costs one LLM call for the CV plus one per
job, instead of two per job. Cached to disk next to the CV embedding cache and
keyed the same way -- on content hash AND model, because a profile produced by
a different model is not interchangeable, and a cache keyed on content alone
would silently serve one model's parse to another. (That exact bug bit this
project once already, in the CV embedding cache, where a key missing the
provider would have served vectors of the wrong dimensionality and scored
everything 0.0.)
"""
import hashlib
import json
import os
import re
from datetime import date

from langchain_core.messages import SystemMessage, HumanMessage

import config
from agents import skill_matching
from agents.llm import get_llm, no_think_prefix, prepare_system, visible_content

_EXTRACTION_PROMPT = """You extract structured data from a CV. Respond with ONLY a JSON object, no prose, no code fences.

Schema:
{
  "contact": {
    "full_name": "...", "title": "the headline under the name, e.g. AI Engineer",
    "email": "...", "phone": "...",
    "location": "city, country", "linkedin": "url or null", "github": "url or null"
  },
  "summary": "the professional summary/objective paragraph, verbatim, or null",
  "experience": [
    {
      "title": "job title as written",
      "organization": "employer name",
      "location": "where the role was held, as written (Cairo, Egypt / Remote), or null",
      "start": "YYYY-MM or YYYY",
      "end": "YYYY-MM or YYYY or \\"present\\"",
      "is_professional": true,
      "bullets": ["each responsibility/achievement line, verbatim"]
    }
  ],
  "projects": [
    {"name": "...", "url": "repo or demo link as written, or null",
     "start": "YYYY-MM or YYYY or null", "end": "...", "bullets": ["..."]}
  ],
  "education": [
    {"degree": "...", "field": "...", "institution": "...",
     "location": "where the institution is, as written, or null",
     "start": "...", "end": "..."}
  ],
  "certifications": ["..."],
  "skills_claimed": ["each skill listed in a skills section, one per entry"],
  "seniority_self_described": "intern|entry|mid|senior|lead|manager|null"
}

Rules:
- Copy contact details exactly as written. Use null for anything absent. Never
  guess an email, phone number or profile URL.
- "title" is the professional headline printed under the name, if the CV has
  one. Do NOT substitute the most recent job title for it, and do not invent
  one -- use null when the CV states no headline.
- is_professional is true for paid employment INCLUDING internships, false for
  coursework, volunteering, and personal projects.
- Do NOT invent dates. If a date is absent, use null.
- Copy locations and project links exactly as written, and use null when the
  CV gives none. Never infer a company's headquarters as the role's location
  or guess a repository URL from a project name.
- Do NOT infer skills that are not written in the CV. skills_claimed contains
  only what a skills/technologies section actually lists.
- Copy bullets verbatim. They are used as evidence and must be quotable."""


def _cv_hash(cv_text: str) -> str:
    return hashlib.sha256(cv_text.encode("utf-8")).hexdigest()


def _model_id() -> str:
    """Which chat model produced a profile. Part of the cache key: two models
    parse the same CV differently, and serving one's output as the other's
    would make results irreproducible for no visible reason."""
    provider = config.LLM_PROVIDER
    return f"{provider}:" + {
        "ollama": config.OLLAMA_MODEL,
        "groq": config.GROQ_MODEL,
        "openrouter": config.OPENROUTER_MODEL,
        "anthropic": config.ANTHROPIC_MODEL,
        "openai": config.OPENAI_MODEL,
    }.get(provider, "default")


# Bumped whenever _EXTRACTION_PROMPT's schema changes shape. Without this, a
# cache written before a field existed is served afterwards as though it were
# complete -- the entry is valid JSON for the old schema and there is nothing
# in it to reveal that it predates the new fields. v2 added contact/summary,
# which the scorer never needed but a generated CV cannot be written without.
# v3 added contact.title, the headline printed under the name.
# v4 added experience.location, education.location and projects.url -- fields
# the Profile page exposes and a generated CV prints, absent from every v3
# cache entry.
_SCHEMA_VERSION = 4


def _cache_key(cv_text: str) -> str:
    return f"{_cv_hash(cv_text)}:{_model_id()}:v{_SCHEMA_VERSION}"


def _read_cache() -> dict:
    path = config.CV_PROFILE_CACHE_PATH
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        # A corrupt cache must never be fatal -- it's an optimisation, and the
        # profile can always be regenerated.
        return {}


def _write_cache(cache: dict) -> None:
    path = config.CV_PROFILE_CACHE_PATH
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, indent=2)
    except OSError:
        pass


def _safe_json_object(raw: str) -> dict:
    """LLMs wrap JSON in prose or code fences; pull the outermost object out."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    candidate = match.group(0) if match else raw
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


# ---- dates ------------------------------------------------------------------

_DATE_RE = re.compile(r"^(\d{4})(?:[-/](\d{1,2}))?")


def parse_month(value) -> tuple[int, int] | None:
    """'2025-07' -> (2025, 7); '2025' -> (2025, 1); 'present' -> today.

    Returns None for anything unparseable, which the caller treats as "no
    date" rather than guessing -- an entry with no date contributes no months
    instead of contributing a wrong number.
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text or text in ("null", "none"):
        return None
    if text in ("present", "current", "now", "ongoing", "today"):
        today = date.today()
        return today.year, today.month
    match = _DATE_RE.match(text)
    if not match:
        return None
    year = int(match.group(1))
    month = int(match.group(2)) if match.group(2) else 1
    if not (1900 <= year <= date.today().year + 1) or not (1 <= month <= 12):
        return None
    return year, month


def _to_index(ym: tuple[int, int]) -> int:
    return ym[0] * 12 + (ym[1] - 1)


def merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Union of half-open month intervals.

    Overlaps must collapse, not add. A candidate holding two concurrent
    part-time roles for a year has one year of experience, not two -- and CVs
    routinely list an internship that overlaps the final year of a degree.
    """
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def professional_months(profile: dict) -> int:
    """Total months of professional experience, overlaps merged.

    Only entries flagged is_professional and carrying a parseable start date
    count. An open-ended entry ("present") runs to today; one with no end date
    at all is treated as a single month rather than being dropped, since the
    CV clearly asserts the role existed.
    """
    intervals = []
    for entry in profile.get("experience") or []:
        if not entry.get("is_professional", True):
            continue
        start = parse_month(entry.get("start"))
        if start is None:
            continue
        end = parse_month(entry.get("end")) or start
        s, e = _to_index(start), _to_index(end)
        if e < s:
            s, e = e, s
        intervals.append((s, e + 1))
    return sum(end - start for start, end in merge_intervals(intervals))


def professional_years(profile: dict) -> float:
    return round(professional_months(profile) / 12, 1)


# ---- evidence ---------------------------------------------------------------

def _annotate(spans: list[dict]) -> list[dict]:
    """Give every span a stable id and its normalised concepts.

    Phase 1 of the generic-matching plan: the span keeps its own text, and
    gains a reading of that text. The id is a hash of the text rather than a
    position, so a span keeps its identity when the CV is reordered -- which
    matters because a retrieval cache and a stored Match row both point at it.

    Concepts come from the vocabulary the matcher already has (the same scan
    demonstrated_skills() runs), so this adds no table and no LLM call. A line
    whose terms are all outside the vocabulary simply gets an empty list --
    that is the case semantic retrieval exists to serve, and pretending to
    normalise it would hide exactly the lines worth retrieving.
    """
    index = _implying_index()
    for position, span in enumerate(spans):
        text = span.get("text") or ""
        span["id"] = _span_id(text, position)
        if "concepts" not in span:
            span["concepts"] = _concepts_in(text, index)
    return spans


def _span_id(text: str, position: int) -> str:
    digest = hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:10]
    return f"span_{position:03d}_{digest}"


_CONCEPT_CACHE: dict[str, list[str]] = {}


def _concepts_in(text: str, index: dict) -> list[str]:
    key = skill_matching.normalize(text)[:400]
    if key in _CONCEPT_CACHE:
        return _CONCEPT_CACHE[key]

    found = set()
    for term in _known_terms():
        if skill_matching.find_term(term, text):
            found.add(term)
    # A concept the line implies is part of what the line means, and the
    # retrieval layer reads these to explain why a span was offered.
    for term in list(found):
        for implied in index.get(term, ()):
            if not skill_matching.are_mutually_exclusive(implied, term):
                found.add(implied)

    out = sorted(found)
    if len(_CONCEPT_CACHE) < 4000:
        _CONCEPT_CACHE[key] = out
    return out


def evidence_spans(profile: dict, cv_text: str = "") -> list[dict]:
    """Every quotable span of the CV, tagged with where it came from.

    The `demonstrated` flag is what distinguishes a skill the candidate has
    USED from one they have merely LISTED. A term found in an experience or
    project bullet is backed by a dated engagement; the same term found only
    in a skills section is an assertion. Scoring them identically -- which the
    old substring match did -- means a CV that simply lists every keyword in
    the job description outscores one that describes real work.

    This is the cheap, verifiable stand-in for per-skill years of experience.
    Deriving "2.3 years of PyTorch" would require attributing each skill to
    specific roles, which real CVs (this project's included: skills live in
    one global section) do not support, so it would come out of an LLM's
    imagination rather than the document.
    """
    spans: list[dict] = []

    for entry in profile.get("experience") or []:
        header = " ".join(str(entry.get(k) or "") for k in ("title", "organization")).strip()
        dated = parse_month(entry.get("start")) is not None
        if header:
            spans.append({
                "text": header, "source": "experience", "entry": header,
                "demonstrated": True, "dated": dated,
            })
        for bullet in entry.get("bullets") or []:
            if str(bullet).strip():
                spans.append({
                    "text": str(bullet).strip(), "source": "experience", "entry": header,
                    "demonstrated": True, "dated": dated,
                })

    for entry in profile.get("projects") or []:
        header = str(entry.get("name") or "").strip()
        if header:
            spans.append({
                "text": header, "source": "project", "entry": header,
                "demonstrated": True, "dated": parse_month(entry.get("start")) is not None,
            })
        for bullet in entry.get("bullets") or []:
            if str(bullet).strip():
                spans.append({
                    "text": str(bullet).strip(), "source": "project", "entry": header,
                    "demonstrated": True, "dated": parse_month(entry.get("start")) is not None,
                })

    for entry in profile.get("education") or []:
        text = " ".join(str(entry.get(k) or "") for k in
                        ("degree", "field", "institution")).strip()
        if text:
            spans.append({
                "text": text, "source": "education", "entry": text,
                "demonstrated": True, "dated": parse_month(entry.get("end")) is not None,
            })

    for cert in profile.get("certifications") or []:
        if str(cert).strip():
            spans.append({
                "text": str(cert).strip(), "source": "certification",
                "entry": str(cert).strip(), "demonstrated": True, "dated": False,
            })

    # Sections the user added on the Profile page (Publications, Volunteering,
    # ...). Each line is its own span, like a project bullet.
    for section in profile.get("custom_sections") or []:
        if not isinstance(section, dict):
            continue
        header = str(section.get("title") or "").strip()
        for bullet in section.get("bullets") or []:
            if str(bullet).strip():
                spans.append({
                    "text": str(bullet).strip(), "source": "custom", "entry": header,
                    "demonstrated": True, "dated": False,
                })

    for skill in profile.get("skills_claimed") or []:
        if str(skill).strip():
            spans.append({
                "text": str(skill).strip(), "source": "skills",
                "entry": "Skills section", "demonstrated": False, "dated": False,
            })

    # Fallback: if the parse produced nothing usable, read the raw CV
    # structurally instead. A degraded profile should mean weaker evidence
    # attribution, not a score of zero across the board.
    if not spans and cv_text:
        return spans_from_text(cv_text)

    return _annotate(spans)


# ---- what the work evidences ------------------------------------------------

_IMPLYING_INDEX: dict[str, list[str]] | None = None
_KNOWN_TERMS: list[str] | None = None


def _known_terms() -> list[str]:
    """Every skill term the matcher has an opinion about, listed once.

    The vocabulary is already written down three times over -- as alias
    groups, as hyponym trees, as prerequisite lists -- so reading skills out
    of a bullet needs no new table and no LLM call: anything the scorer could
    recognise in a job description, it can recognise in a role description.
    Terms outside the vocabulary are left alone rather than guessed at; a
    house framework nobody has curated is not a skill this can name.
    """
    global _KNOWN_TERMS
    if _KNOWN_TERMS is None:
        terms: set[str] = set()
        for table in (skill_matching.ALIASES, skill_matching.HYPONYMS,
                      skill_matching.IMPLIED_BY):
            for key, values in table.items():
                terms.add(skill_matching.canonical(key))
                terms.update(skill_matching.canonical(v) for v in values)
        _KNOWN_TERMS = sorted(t for t in terms if t)
    return _KNOWN_TERMS


def _implying_index() -> dict[str, list[str]]:
    """term -> the skills that term is evidence for, built once.

    IMPLIED_BY is keyed by the skill ("python" -> ["fastapi", ...]), which is
    the right shape for asking "is this requirement implied?" but the wrong
    one for reading a CV, where the question runs the other way: here is a
    bullet mentioning FastAPI -- what does that prove? Inverting it once means
    a scan tests each distinct evidence term against a span exactly once
    instead of re-walking the whole table per skill.
    """
    global _IMPLYING_INDEX
    if _IMPLYING_INDEX is None:
        index: dict[str, set[str]] = {}
        for skill, evidence_terms in skill_matching.IMPLIED_BY.items():
            for term in evidence_terms:
                index.setdefault(skill_matching.canonical(term), set()).add(
                    skill_matching.canonical(skill))
        _IMPLYING_INDEX = {term: sorted(skills) for term, skills in index.items()}
    return _IMPLYING_INDEX


def demonstrated_skills(profile: dict, cv_text: str = "") -> list[dict]:
    """The skills the dated work supports, and the line that supports each.

    Two ways a skill earns a place here:

      direct   an experience or project span names it outright.
      implied  a span names something that cannot be done without it --
               FastAPI and PyTorch bullets prove Python; PostgreSQL proves
               SQL -- via the curated IMPLIED_BY table, which is directional
               and guarded by EXCLUSIVE_GROUPS, so a sibling technology never
               qualifies (Kubernetes is not evidence of Docker).

    The claimed skills are not the search list. A CV that never wrote "Docker"
    in its keyword row but containerised a service in a project has still done
    Docker work, and the point of reading the entries is to find exactly that
    -- so the scan runs over the matcher's whole known vocabulary, and the
    skills row only ever adds terms to it.

    Skills-section spans are deliberately not searched. The whole point of
    this list is the distinction the skills section erases: a term in a dated
    bullet describes work that happened, and a term in a keyword list is an
    assertion. A skill listed and never used appears nowhere in this result,
    which is exactly the gap cv_targeting.listed_only() reports back.
    """
    spans = [s for s in evidence_spans(profile, cv_text)
             if s.get("demonstrated") and s.get("source") in ("experience", "project")]
    index = _implying_index()

    best: dict[str, dict] = {}

    def record(skill: str, relation: str, via: str, span: dict) -> None:
        row = {
            "skill": skill,
            "relation": relation,
            "via": via,
            "source": span.get("source"),
            "entry": span.get("entry"),
            "text": span.get("text"),
            "dated": bool(span.get("dated")),
        }
        current = best.get(skill)
        # Direct beats implied, and a dated entry beats an undated one; first
        # writer wins within a tier so the earliest (topmost) entry is cited.
        rank = (relation == "direct", row["dated"])
        if current is None or rank > (current["relation"] == "direct", current["dated"]):
            best[skill] = row

    claimed = [str(s).strip() for s in profile.get("skills_claimed") or [] if str(s).strip()]
    direct_vocabulary = sorted({skill_matching.canonical(s) for s in claimed if s}
                               | set(_known_terms()))

    for span in spans:
        text = span.get("text") or ""

        for skill in direct_vocabulary:
            hit = skill_matching.find_term(skill, text)
            if hit:
                record(skill_matching.canonical(skill), "direct", hit, span)

        for term, skills in index.items():
            hit = skill_matching.find_term(term, text)
            if not hit:
                continue
            for skill in skills:
                if skill_matching.are_mutually_exclusive(skill, term):
                    continue
                if skill_matching.find_term(skill, text):
                    record(skill, "direct", skill, span)
                else:
                    record(skill, "implied", hit, span)

    return sorted(best.values(), key=lambda r: (r["relation"] != "direct", r["skill"]))


# ---- structural span extraction, no LLM -------------------------------------

_SECTION_PATTERNS = {
    "experience": re.compile(
        r"^\s*(work\s+)?(experience|employment|professional experience|"
        r"employment history|career)\b", re.IGNORECASE),
    "project": re.compile(r"^\s*(projects?|personal projects?|selected projects?)\b",
                          re.IGNORECASE),
    "education": re.compile(r"^\s*(education|academic|qualifications)\b", re.IGNORECASE),
    "skills": re.compile(r"^\s*(technical\s+)?(skills|technologies|tech stack|"
                         r"competencies|tools)\b", re.IGNORECASE),
    "certification": re.compile(r"^\s*(certifications?|courses?|licen[cs]es?|"
                                r"awards?)\b", re.IGNORECASE),
    "summary": re.compile(r"^\s*(summary|profile|objective|about)\b", re.IGNORECASE),
    # Titles people give the sections they add on the Profile page. Anchored
    # to the whole line, unlike the rest: "Volunteer Teacher | NGO" or
    # "Languages and Frameworks" must not open a section.
    "custom": re.compile(r"^\s*(publications?|volunteering|volunteer experience|"
                         r"languages|honou?rs|extracurricular activities|activities)\s*$",
                         re.IGNORECASE),
}


_LABELLED_LINE = re.compile(r"^\s*([^:]{1,40}):\s*(\S.*)$")


def _split_labelled_line(line: str) -> tuple[str | None, str | None]:
    """("Skills", "Python, Java") for a labelled line, (line, None) otherwise."""
    match = _LABELLED_LINE.match(line)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return (line.rstrip(":").strip(), None) if len(line) <= 45 else (None, None)


def _section_named(label: str, *, exact: bool) -> str | None:
    """Which section this label names, if any.

    `exact` demands the section name account for the WHOLE label. A bare
    heading may trail off into decoration -- "SKILLS & TOOLS", "EDUCATION AND
    CERTIFICATIONS" -- and still be a heading; a label sitting in front of
    real content has to be nothing but the section name to claim it, or every
    "Tools & DevOps:" row eats its own contents.
    """
    for name, pattern in _SECTION_PATTERNS.items():
        match = pattern.match(label)
        if match and (not exact or match.end() == len(label)):
            return name
    return None


def spans_from_text(cv_text: str) -> list[dict]:
    """Evidence spans read straight from CV text, tracking section headers.

    Exists so a TAILORED CV can be scored without spending a second LLM call
    parsing it. That call is pure waste: the no-fabrication rule means a
    rewrite cannot change the work history, so the dates are already known
    from the master CV's cached profile. What the rewrite CAN change is which
    skills are mentioned and where — which is exactly what this reads.

    It also removes a real failure mode. The tailored CV is generated text and
    can run long; sending it back through the profile extractor on a metered
    provider is how the bench hit a Groq 413 (request too large for the
    tokens-per-minute window) at the rescore step, after the expensive rewrite
    had already succeeded.

    The demonstrated/claimed distinction survives: lines under an experience or
    projects heading are demonstrated, lines under a skills heading are only
    claimed.
    """
    spans: list[dict] = []
    section = None
    for raw in cv_text.split("\n"):
        line = raw.strip().lstrip("#").strip()
        if not line:
            continue

        # A heading is short and matches a known section name. The length
        # guard stops a sentence merely containing "experience" from
        # re-sectioning everything that follows it.
        #
        # A line with content after a colon is only a heading when the label
        # BEFORE the colon is the whole section name -- and even then its
        # payload is kept. "Skills: Python, Java" opens the skills section and
        # still contributes Python and Java; "Tools & DevOps: Git, Docker"
        # opens nothing, because "Tools" is merely how the line starts.
        #
        # That distinction is why this exists. Under the old rule the second
        # line matched the skills pattern on its first word, was treated as a
        # heading, and was DISCARDED -- so a tailored CV whose skills row read
        # "Tools & DevOps: Git, Docker" scored Docker and Git as Not found
        # while both sat in plain sight on the page.
        label, payload = _split_labelled_line(line)
        if label is not None:
            matched = _section_named(label, exact=payload is not None)
            if matched:
                section = matched
                if not payload:
                    continue
                line = payload

        source = section or "cv"
        spans.append({
            "text": line.lstrip("-*• ").strip() or line,
            "source": source,
            "entry": "",
            "demonstrated": source in ("experience", "project", "education",
                                       "certification", "custom"),
            "dated": bool(_YEAR_IN_LINE.search(line)),
        })
    return _annotate(spans)


_YEAR_IN_LINE = re.compile(r"\b(?:19|20)\d{2}\b")


def profile_text(profile: dict) -> str:
    """Everything the profile asserts, as one searchable string.

    The profile -- not the uploaded file's text -- is what the app knows about
    the candidate: seeded from their upload, then corrected by hand on the
    Profile page. Anything asking "does this person have X?" should search
    this, so an answer never depends on which snapshot happens to be on disk.
    """
    profile = profile if isinstance(profile, dict) else {}
    parts = [str(profile.get("summary") or "")]

    for entry in profile.get("experience") or []:
        if isinstance(entry, dict):
            parts.append(" ".join(str(entry.get(k) or "") for k in ("title", "organization")))
            parts.extend(str(b or "") for b in entry.get("bullets") or [])
    for entry in profile.get("projects") or []:
        if isinstance(entry, dict):
            parts.append(str(entry.get("name") or ""))
            parts.extend(str(b or "") for b in entry.get("bullets") or [])
    for entry in profile.get("education") or []:
        if isinstance(entry, dict):
            parts.append(" ".join(str(entry.get(k) or "")
                                  for k in ("degree", "field", "institution")))

    for section in profile.get("custom_sections") or []:
        if isinstance(section, dict):
            parts.append(str(section.get("title") or ""))
            parts.extend(str(b or "") for b in section.get("bullets") or [])

    parts.extend(str(s) for s in profile.get("skills_claimed") or [])
    parts.extend(str(c) for c in profile.get("certifications") or [])
    return " \n".join(p.strip() for p in parts if p and p.strip())


# ---- entry point ------------------------------------------------------------

_PROFILE_SITES = {"linkedin": "linkedin.com", "github": "github.com"}


def fill_profile_links(profile: dict, links: list[str]) -> dict:
    """Puts the CV's real LinkedIn / GitHub URLs in the contact block.

    A CV often prints just the word "Linkedin" with the URL behind it as a
    link, so the extraction only ever sees the word. When the extracted value
    isn't a URL for that site, the CV's own link for it replaces it. For
    GitHub, a profile link (github.com/you) wins over a repository link.
    """
    contact = profile.setdefault("contact", {}) if isinstance(profile.get("contact"), dict) else {}
    profile["contact"] = contact
    for field, site in _PROFILE_SITES.items():
        current = str(contact.get(field) or "")
        if site in current.lower():
            continue
        matches = [u for u in links if site in u.lower()]
        if field == "github":
            matches.sort(key=lambda u: len([p for p in re.sub(r"^\w+://", "", u).split("/")[1:] if p]))
        if matches:
            contact[field] = matches[0]
    return profile


def build_profile(cv_text: str, use_cache: bool = True) -> dict:
    """Parse a CV into structured entries. One LLM call, cached by hash+model.

    Never raises on a bad parse: an empty profile degrades evidence quality
    (spans fall back to raw CV lines) rather than failing the whole scoring
    run, because a scoring pipeline that dies on an unusual CV layout is worse
    than one that scores it conservatively.
    """
    key = _cache_key(cv_text)
    cache = _read_cache() if use_cache else {}
    if use_cache and key in cache:
        profile = dict(cache[key])
        profile["_cached"] = True
        return profile

    # 2048 rather than the 4096 default: this emits structured JSON, not prose,
    # and metered providers bill the RESERVATION. Groq's free tier counts
    # prompt + max_tokens against 8,000 tokens/minute and rejects the whole
    # request with a 413 before the model runs, so an oversized reservation
    # fails a call whose prompt would otherwise have fit.
    system = prepare_system(_EXTRACTION_PROMPT)
    llm = get_llm(temperature=0.0, max_tokens=2048,
                  prompt_text=system + (cv_text or ""))
    resp = llm.invoke([
        SystemMessage(content=system),
        HumanMessage(content=no_think_prefix() + (cv_text or "")),
    ])
    profile = _safe_json_object(visible_content(resp))

    for field, default in (
        ("contact", {}), ("summary", None),
        ("experience", []), ("projects", []), ("education", []),
        ("certifications", []), ("skills_claimed", []),
        ("seniority_self_described", None),
    ):
        profile.setdefault(field, default)

    if use_cache:
        cache[key] = profile
        _write_cache(cache)

    profile["_cached"] = False
    return profile
