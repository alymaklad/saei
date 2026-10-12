"""
Re-scoring a tailored CV must not re-parse it with the LLM.

This was a live 500: the bench scored the master CV, paid for a full rewrite,
and then blew up sending the rewritten text back through the profile
extractor. Groq's free tier caps tokens PER MINUTE counting prompt plus the
reserved output budget, so the request was rejected with a 413 before the
model ran -- discarding a tailored CV that had already been paid for.

Skipping the re-parse is also just correct. cv_rewriter_agent may not
fabricate experience, so the rewrite cannot change the dates the profile
supplies; only the wording changes, and wording is read structurally.
"""
from unittest.mock import patch

import pytest

import config
from agents import ats_agent, cv_profile, llm as llm_module


TAILORED = """
Aly Tarek Maklad

Summary
Machine learning engineer.

Experience
AI Research Intern, Acme  Jul/2025 - May/2026
- Trained ResNet50 CNN models using PyTorch
- Deployed inference services with Docker

Skills
Python, SQL, Kubernetes
"""

MASTER_PROFILE = {
    "experience": [{"title": "AI Research Intern", "organization": "Acme",
                    "start": "2025-07", "end": "2026-05", "is_professional": True,
                    "bullets": ["Trained ResNet50 CNN models using PyTorch"]}],
    "projects": [], "education": [], "certifications": [],
    "skills_claimed": ["Python"], "_cached": True,
}

REQS = ats_agent._normalize_requirements({
    "requirements": [
        {"name": "PyTorch", "category": "technical_skill", "importance": "required"},
        {"name": "Kubernetes", "category": "tool", "importance": "preferred"},
    ],
    "years_experience_required": 2, "seniority": None,
})


def test_rescoring_makes_no_profile_call():
    with patch.object(ats_agent.cv_profile, "build_profile") as mock_build, \
         patch.object(ats_agent, "_entailment_pass", return_value={}):
        ats_agent.compute_requirements_score(
            TAILORED, "jd", extracted=REQS,
            profile=MASTER_PROFILE, evidence_text=TAILORED)
    mock_build.assert_not_called()


def test_experience_still_comes_from_the_master_profile():
    """A rewrite can't change the work history, so the years must not move --
    and must not silently drop to zero just because the parse was skipped."""
    with patch.object(ats_agent, "_entailment_pass", return_value={}):
        result = ats_agent.compute_requirements_score(
            TAILORED, "jd", extracted=REQS,
            profile=MASTER_PROFILE, evidence_text=TAILORED)
    assert result["cv_years"] == pytest.approx(0.9, abs=0.2)


def test_newly_surfaced_skills_are_still_found_in_the_rewritten_text():
    """The whole point of tailoring. Kubernetes is absent from the master
    profile's skills but present in the rewritten CV."""
    with patch.object(ats_agent, "_entailment_pass", return_value={}):
        result = ats_agent.compute_requirements_score(
            TAILORED, "jd", extracted=REQS,
            profile=MASTER_PROFILE, evidence_text=TAILORED)
    row = next(r for r in result["requirement_results"] if r["name"] == "Kubernetes")
    assert row["match"] != "missing"


# ---- structural span reading -------------------------------------------------

def test_section_headings_drive_the_demonstrated_flag():
    spans = cv_profile.spans_from_text(TAILORED)
    by_text = {s["text"]: s for s in spans}
    assert by_text["Trained ResNet50 CNN models using PyTorch"]["demonstrated"] is True
    assert by_text["Python, SQL, Kubernetes"]["demonstrated"] is False


def test_a_sentence_mentioning_experience_does_not_start_a_section():
    """The length guard: without it, one prose line containing the word
    'experience' would re-classify everything after it."""
    text = ("Skills\nPython\n"
            "I have considerable experience delivering production systems "
            "across several teams and domains\nSQL\n")
    spans = {s["text"]: s for s in cv_profile.spans_from_text(text)}
    assert spans["SQL"]["source"] == "skills"


def test_bullet_markers_are_stripped_from_evidence():
    spans = cv_profile.spans_from_text("Experience\n- Did a thing\n")
    assert spans[0]["text"] == "Did a thing"


# ---- the two bugs that made tailoring LOSE points ----------------------------
#
# Both found in one real run: job 23 scored 0.3683 on the master CV and 0.2107
# after tailoring. Nothing was wrong with the rewrite -- the two readers below
# were throwing away evidence that was sitting in plain sight on the page.

SKILLS_BLOCK = """TECHNICAL SKILLS
Programming Languages: Python, C/C++, Java
Web Frameworks: FastAPI, NestJS, REST APIs
Databases: PostgreSQL, MongoDB, SQLite, SQLAlchemy
Tools & DevOps: Git, Docker
"""


def test_a_skills_row_labelled_tools_is_evidence_not_a_heading():
    """"Tools & DevOps: Git, Docker" begins with a word the skills heading
    pattern matches, so the whole line was read as a section header and
    DISCARDED -- Docker and Git scored Not found on a CV that lists them."""
    spans = cv_profile.spans_from_text(SKILLS_BLOCK)
    texts = " | ".join(s["text"] for s in spans)
    assert "Git" in texts and "Docker" in texts


def test_the_skills_row_still_counts_as_claimed_not_demonstrated():
    """Recovering the line must not promote it: it is still a keyword row."""
    spans = cv_profile.spans_from_text(SKILLS_BLOCK)
    row = next(s for s in spans if "Docker" in s["text"])
    assert row["source"] == "skills"
    assert row["demonstrated"] is False


def test_a_label_that_is_exactly_a_section_name_keeps_its_payload():
    """"Skills: Python, Java" is both -- it opens the section AND contributes
    two skills. Dropping either half loses something real."""
    spans = cv_profile.spans_from_text("Skills: Python, Java\nSQL\n")
    assert spans[0]["text"] == "Python, Java"
    assert spans[0]["source"] == "skills"
    assert spans[1]["source"] == "skills", "the section stays open for later lines"


@pytest.mark.parametrize("heading, section", [
    ("SKILLS & TOOLS", "skills"),
    ("Education and Certifications", "education"),
    ("PROFESSIONAL EXPERIENCE", "experience"),
    ("Selected Projects", "project"),
])
def test_decorated_headings_still_open_their_section(heading, section):
    spans = cv_profile.spans_from_text(f"{heading}\nA line of content\n")
    assert spans[0]["source"] == section


# ---- typography ---------------------------------------------------------------

FANCY = "Built LangChain‑based tool‑calling agents in Python for Bachelor’s coursework"


def test_evidence_quoted_with_a_plain_hyphen_matches_a_fancy_one():
    """The rewrite comes back with U+2011 non-breaking hyphens. The entailment
    pass quotes its evidence with an ASCII hyphen, _evidence_is_real could not
    find the quote in the line it came from, and four confirmed verdicts were
    discarded as fabrications -- worth ten points of the real run above."""
    plain = FANCY.replace("‑", "-").replace("’", "'")
    assert ats_agent._evidence_is_real(plain, [{"text": FANCY}])


def test_a_hyphenated_skill_matches_across_the_dash_variants():
    from agents import skill_matching
    assert skill_matching.find_term("tool-calling", FANCY) == "tool-calling"
    assert skill_matching.find_term("scikit-learn", "Used scikit‑learn for baselines")


def test_folding_preserves_offsets_so_snippets_stay_aligned():
    """find_term_with_context slices the ORIGINAL text with offsets found in
    the folded copy; a fold that changed length would shift every snippet."""
    from agents import skill_matching
    assert len(skill_matching.fold_typography(FANCY)) == len(FANCY)
    form, snippet = skill_matching.find_term_with_context("Python", FANCY)
    assert "Python" in snippet


# ---- provider error translation ---------------------------------------------

def test_a_tpm_rejection_explains_that_waiting_will_not_help(monkeypatch):
    """The actionable part of a 413 is buried in provider JSON. 'Rate limit'
    also implies 'try again shortly', which is wrong here -- the request is too
    large for the window, not too frequent."""
    monkeypatch.setattr(config, "LLM_PROVIDER", "groq")
    raw = Exception(
        "Error code: 413 - {'error': {'message': 'Request too large for model "
        "`qwen/qwen3.6-27b` ... on tokens per minute (TPM): Limit 8000, "
        "Requested 8546, please reduce your message size', "
        "'code': 'rate_limit_exceeded'}}")
    message = llm_module.describe_llm_error(raw)
    assert "8546" in message and "8000" in message
    assert "Ollama" in message
    assert "will not help" in message


def test_a_daily_budget_error_is_distinguished_from_a_per_minute_one(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "groq")
    raw = Exception("Rate limit reached ... tokens per day (TPD): Limit 200000, "
                    "Used 197723 ... code: rate_limit_exceeded")
    assert "daily token budget" in llm_module.describe_llm_error(raw)


def test_an_unrelated_error_passes_through_unchanged(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "ollama")
    assert llm_module.describe_llm_error(ValueError("something else")) == "something else"


def test_extraction_calls_reserve_a_smaller_output_budget():
    """Metered providers bill the RESERVATION, not the usage: prompt +
    max_tokens is what Groq counts against 8,000/minute. A JSON-emitting call
    holding the 4096 prose default is what pushed this over."""
    seen = {}

    class FakeLLM:
        def invoke(self, _m):
            return type("R", (), {"content": "{}"})()

    def fake_get_llm(temperature=0.0, max_tokens=None, prompt_text=""):
        # prompt_text is how a call asks for a context sized to its own prompt
        # (agents/llm.context_for). Accepted here so this double keeps matching
        # the real signature: the test is about the RESERVATION, and a double
        # that stopped being callable would "pass" by failing earlier.
        seen["max_tokens"] = max_tokens
        return FakeLLM()

    with patch.object(ats_agent, "get_llm", fake_get_llm):
        ats_agent.extract_requirements("jd")
        assert seen["max_tokens"] == 4096   # room for a long requirement list
        # A long posting gets less, so prompt + reservation stays under 8,000.
        long_jd = "We need Python. " * 1000
        try:
            ats_agent.extract_requirements(long_jd)
        except RuntimeError:
            pass   # "{}" has no requirements; only the reservation matters here
        system = ats_agent.prepare_system(ats_agent._REQUIREMENT_EXTRACTION_PROMPT)
        assert 2048 <= seen["max_tokens"] < 4096
        assert ats_agent.estimate_tokens(system + long_jd) + seen["max_tokens"] <= 8000
