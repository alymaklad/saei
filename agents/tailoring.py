"""
Tailoring that never costs the candidate points.

Measured on this project's own runs: half of the tailored CVs scored LOWER
than the CV they were made from (39% -> 33%, 62% -> 56%). Not scoring noise --
re-rendering the profile unchanged and re-reading it gives the same matches
(1 difference in 261 requirements, and that one went up). The rewrite itself
lost them, two ways:

  * it reworded the bullet that earned a requirement and dropped the term:
    "Python" or "FastAPI" went from a dated role (demonstrated, full credit)
    to the summary or skills list only (claimed, partial credit), or nowhere;
  * it paraphrased a bullet the scorer had judged to show a requirement
    ("NestJS backend services" -> backend fundamentals), and the paraphrase
    no longer did.

So tailoring here is three steps, each only as expensive as it needs to be:

  1. after the rewrite, a bullet that lost a job term it had earned gets its
     original wording back (deterministic, no LLM call);
  2. if the re-score is still below the original, the original bullets for
     exactly the requirements that lost credit go back in, and it is scored
     once more;
  3. if that is still below, the candidate's own CV is used as it is -- an
     unchanged CV scores what it scored, and that is never lower.
"""
import copy
import re

from agents import skill_matching
from agents.cv_rewriter_agent import (MAX_EXPERIENCE_BULLETS, MAX_PROJECT_BULLETS,
                                      render_cv_text, rewrite_cv)

_WORD = re.compile(r"[a-z0-9+#.]+")


def _norm(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def _words(text) -> set[str]:
    return {w for w in _WORD.findall(_norm(text)) if len(w) > 2}


def _similarity(a: str, b: str) -> float:
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


def _earned(results) -> list[dict]:
    """Requirements the original CV earned from a line in a role or project."""
    return [r for r in results or [] if isinstance(r, dict)
            and r.get("evidence_location") == "demonstrated"
            and float(r.get("credit") or 0) > 0 and r.get("evidence")]


def _locate(profile: dict, evidence: str):
    """("experience"|"projects", index, bullet) for the profile bullet that is
    this evidence line, or None (a title line, or text from elsewhere)."""
    target = _norm(evidence)
    if not target:
        return None
    for section in ("experience", "projects"):
        for i, entry in enumerate(profile.get(section) or []):
            for bullet in (entry or {}).get("bullets") or []:
                b = _norm(bullet)
                if b and (b == target or target in b or b in target):
                    return section, i, str(bullet).strip()
    return None


def _doc_entry(document: dict, profile: dict, section: str, index: int, create: bool):
    """The document's entry for profile[section][index]. Experience keeps the
    profile's order; projects are the model's selection, matched by name, and
    a project it left out is added back when `create`."""
    source = (profile.get(section) or [])[index]
    if section == "experience":
        entries = document.get("experience") or []
        return entries[index] if index < len(entries) else None
    for entry in document.get("projects") or []:
        if _norm(entry.get("name")) == _norm(source.get("name")):
            return entry
    if not create:
        return None
    entry = {"name": source.get("name", ""), "url": source.get("url", ""),
             "start": source.get("start"), "end": source.get("end"), "bullets": []}
    document.setdefault("projects", []).append(entry)
    return entry


def _put_back(entry: dict, original: str, limit: int) -> bool:
    """Put an original bullet back in place of its own rewrite (the most
    similar bullet), or alongside the others when none is recognisably it."""
    bullets = list(entry.get("bullets") or [])
    if any(_norm(b) == _norm(original) for b in bullets):
        return False
    scores = [_similarity(original, b) for b in bullets]
    if scores and (max(scores) >= 0.2 or len(bullets) >= limit):
        bullets[scores.index(max(scores))] = original
    else:
        bullets.append(original)
    entry["bullets"] = bullets[:limit]
    return True


def restore_evidence(document, profile: dict, results) -> tuple[dict, list[str]]:
    """A copy of `document` with the original bullet back for each requirement
    in `results` (the ORIGINAL scoring's rows). Returns (document, names put
    back). A plain-text document (the degraded rewrite path) is returned as is."""
    if not isinstance(document, dict) or not isinstance(profile, dict):
        return document, []
    doc = copy.deepcopy(document)
    restored = []
    for row in results:
        found = _locate(profile, row.get("evidence"))
        if not found:
            continue
        section, index, bullet = found
        entry = _doc_entry(doc, profile, section, index, create=True)
        if entry is None:
            continue
        limit = MAX_EXPERIENCE_BULLETS if section == "experience" else MAX_PROJECT_BULLETS
        if _put_back(entry, bullet, limit):
            restored.append(str(row.get("name")))
    return doc, restored


def keep_earned_terms(document, profile: dict, results) -> tuple[dict, list[str]]:
    """Step 1: a bullet whose rewrite dropped a job term the original bullet
    earned gets its original wording back. Only terms that are literally in
    the original bullet -- a requirement the scorer inferred ("backend
    fundamentals" from "NestJS services") has no word to look for, and is
    left to the score check in tailor()."""
    if not isinstance(document, dict) or not isinstance(profile, dict):
        return document, []
    at_risk = []
    for row in _earned(results):
        found = _locate(profile, row.get("evidence"))
        if not found:
            continue
        section, index, bullet = found
        term = next((t for t in (row.get("matched_form"), row.get("name"))
                     if t and skill_matching.find_term(str(t), bullet)), None)
        if not term:
            continue
        entry = _doc_entry(document, profile, section, index, create=False)
        bullets = (entry or {}).get("bullets") or []
        if not any(skill_matching.find_term(term, b) for b in bullets):
            at_risk.append(row)
    return restore_evidence(document, profile, at_risk)


def lost_requirements(original: dict, tailored: dict) -> list[dict]:
    """The original scoring's rows for requirements the tailored CV scored
    lower on."""
    after = {str(r.get("name")).casefold(): float(r.get("credit") or 0)
             for r in tailored.get("requirement_results") or []}
    return [r for r in _earned(original.get("requirement_results"))
            if after.get(str(r.get("name")).casefold(), 0.0) < float(r.get("credit") or 0) - 1e-9]


def untailored_document(document: dict, profile: dict) -> dict:
    """The candidate's own CV in the tailored layout: every role and project
    with its own bullets and the profile's summary. The skills section keeps
    the rewrite's grouping, which only ever holds the profile's own claims."""
    doc = copy.deepcopy(document)
    doc["summary"] = str(profile.get("summary") or "").strip()
    for entry, source in zip(doc.get("experience") or [], profile.get("experience") or []):
        entry["bullets"] = [str(b).strip() for b in (source or {}).get("bullets") or []
                            if str(b).strip()][:MAX_EXPERIENCE_BULLETS]
    doc["projects"] = [{
        "name": p.get("name", ""), "url": p.get("url", ""),
        "start": p.get("start"), "end": p.get("end"),
        "bullets": [str(b).strip() for b in p.get("bullets") or [] if str(b).strip()][:MAX_PROJECT_BULLETS],
    } for p in profile.get("projects") or [] if isinstance(p, dict)]
    return doc


def tailor(cv_text: str, job_description: str, ats: dict, profile, rescore,
           rewrite=rewrite_cv) -> dict:
    """Rewrite the CV for this job, making sure it doesn't score lower than
    the original.

    `rescore(text)` scores a rendered document against the same job (the
    caller's own compute_ats_score call, so both paths keep their reuse of the
    extracted requirements). Returns the document, its text, its score, and
    `notes` saying what was put back -- shown to the user beside the score.
    `rewrite` is rewrite_cv, passed in so a caller's own reference is used.
    """
    document = rewrite(cv_text, job_description, ats.get("missing_skills") or [],
                          profile=profile,
                          requirement_results=ats.get("requirement_results"),
                          required_skills=ats.get("required_skills"))
    notes = []
    document, kept = keep_earned_terms(document, profile, ats.get("requirement_results"))
    if kept:
        notes.append("Kept the original wording where the rewrite dropped a term "
                     "the job asks for: " + ", ".join(kept[:8]) + ".")

    text = render_cv_text(document)
    result = rescore(text)
    original = float(ats.get("score") or 0)

    if float(result.get("score") or 0) < original:
        repaired, restored = restore_evidence(document, profile, lost_requirements(ats, result))
        if restored:
            repaired_text = render_cv_text(repaired)
            repaired_result = rescore(repaired_text)
            if float(repaired_result.get("score") or 0) > float(result.get("score") or 0):
                document, text, result = repaired, repaired_text, repaired_result
                notes.append("Put back original lines the rewrite had weakened: "
                             + ", ".join(restored[:8]) + ".")

    kept_original = False
    if float(result.get("score") or 0) < original and isinstance(document, dict) \
            and isinstance(profile, dict):
        # Still lower: send the CV as the candidate wrote it. Unchanged
        # content scores what the original scored, so that score is reported
        # rather than spending another call to re-measure it.
        document = untailored_document(document, profile)
        text = render_cv_text(document)
        result = dict(ats)
        kept_original = True
        notes.append("Tailoring didn't beat your own CV for this job, so your CV "
                     "is used as it is.")

    if notes and isinstance(document, dict):
        document["integrity_warnings"] = list(document.get("integrity_warnings") or []) + notes
    return {"document": document, "text": text, "result": result,
            "kept_original": kept_original, "notes": notes}
