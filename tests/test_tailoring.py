"""Tailoring never sends a CV that scores lower than the original."""
from agents import tailoring

PROFILE = {
    "contact": {"full_name": "Jane Doe"},
    "summary": "AI engineer.",
    "experience": [{
        "title": "AI Engineer", "organization": "Acme", "start": "2023", "end": "present",
        "bullets": ["Built a multi-agent pipeline with LangGraph, Python and FastAPI.",
                    "Contributed across the stack: Flutter frontend, NestJS backend services."],
    }],
    "projects": [{"name": "Radiology reports", "bullets": ["Generated reports from 3D CT scans with VLMs."]}],
    "skills_claimed": ["Python", "LangGraph"],
}

ORIGINAL = {
    "score": 0.50,
    "missing_skills": [],
    "required_skills": ["Python", "Backend fundamentals", "Computer vision"],
    "requirement_results": [
        {"name": "Python", "matched_form": "Python", "credit": 1.0, "evidence_location": "demonstrated",
         "evidence": "Built a multi-agent pipeline with LangGraph, Python and FastAPI."},
        {"name": "Backend fundamentals", "credit": 0.75, "evidence_location": "demonstrated",
         "evidence": "Contributed across the stack: Flutter frontend, NestJS backend services."},
        {"name": "Computer vision", "credit": 0.75, "evidence_location": "demonstrated",
         "evidence": "Generated reports from 3D CT scans with VLMs."},
    ],
}


def _rewritten():
    """What a rewrite did on real runs: dropped "Python" from the bullet that
    earned it, paraphrased the backend bullet, and left a project out."""
    return {
        "name": "Jane Doe", "summary": "Agentic AI engineer.",
        "experience": [{"title": "AI Engineer", "organization": "Acme", "start": "2023", "end": "present",
                        "bullets": ["Built a multi-agent LangGraph pipeline served over FastAPI.",
                                    "Worked across the stack on mobile and services."]}],
        "projects": [], "education": [], "skills": [], "integrity_warnings": [],
    }


def test_a_dropped_term_gets_its_original_bullet_back():
    doc, kept = tailoring.keep_earned_terms(_rewritten(), PROFILE, ORIGINAL["requirement_results"])
    assert kept == ["Python"]
    bullets = doc["experience"][0]["bullets"]
    assert bullets[0] == "Built a multi-agent pipeline with LangGraph, Python and FastAPI."
    assert bullets[1] == "Worked across the stack on mobile and services."   # not a term, left alone
    assert len(bullets) == 2


def test_lost_requirements_get_their_lines_back_when_the_score_drops():
    calls = []

    def rescore(text):
        calls.append(text)
        # First score: the paraphrase lost backend; after the repair it's back.
        if "NestJS backend services" in text and "3D CT scans" in text:
            return {"score": 0.62, "requirement_results": [dict(r) for r in ORIGINAL["requirement_results"]]}
        return {"score": 0.45, "requirement_results": [
            {"name": "Python", "credit": 1.0}, {"name": "Backend fundamentals", "credit": 0.0},
            {"name": "Computer vision", "credit": 0.0}]}

    out = tailoring.tailor("cv", "jd", ORIGINAL, PROFILE, rescore, rewrite=lambda *a, **k: _rewritten())
    assert out["result"]["score"] == 0.62 and not out["kept_original"]
    assert len(calls) == 2
    assert "Contributed across the stack: Flutter frontend, NestJS backend services." in out["text"]
    assert [p["name"] for p in out["document"]["projects"]] == ["Radiology reports"]   # added back


def test_still_lower_means_the_cv_goes_out_as_it_is():
    out = tailoring.tailor("cv", "jd", ORIGINAL, PROFILE,
                           lambda text: {"score": 0.40, "requirement_results": []},
                           rewrite=lambda *a, **k: _rewritten())
    assert out["kept_original"]
    assert out["result"]["score"] == ORIGINAL["score"]
    doc = out["document"]
    assert doc["summary"] == "AI engineer."
    assert doc["experience"][0]["bullets"] == PROFILE["experience"][0]["bullets"]
    assert "used as it is" in " ".join(doc["integrity_warnings"])


def test_a_better_rewrite_is_kept_untouched():
    better = {"score": 0.70, "requirement_results": []}
    calls = []
    out = tailoring.tailor("cv", "jd", ORIGINAL, PROFILE,
                           lambda text: calls.append(text) or better,
                           rewrite=lambda *a, **k: _rewritten())
    assert out["result"] is better and len(calls) == 1 and not out["kept_original"]


def test_plain_text_rewrite_passes_through():
    out = tailoring.tailor("cv", "jd", ORIGINAL, PROFILE,
                           lambda text: {"score": 0.55}, rewrite=lambda *a, **k: "PLAIN CV TEXT")
    assert out["text"] == "PLAIN CV TEXT" and out["result"]["score"] == 0.55
