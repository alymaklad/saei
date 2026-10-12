"""
The apply step: the email route ("send your CV to ..."), cover letters, a CV
always attached, dry runs recorded as "would have applied", the draft fallback
where no form submitter exists, and reading a pasted posting.
"""
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import config
import db
import orchestrator
from agents import apply_agent, cover_letter_agent
from models import Application, Base, EmailLog

PROFILE = {
    "contact": {"full_name": "Aly Maklad", "title": "AI Engineer"},
    "summary": "AI Engineer building agentic LLM applications.",
    "experience": [{"title": "AI Intern", "organization": "D-Hub", "start": "2026-06", "end": "2026-07",
                    "bullets": ["Built tool-calling agents with LangChain."]}],
    "projects": [], "education": [], "skills_claimed": ["LangChain", "Python"],
}


# ---- find_application_email ----------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Interested candidates should send their CV to careers@acme.com with the job title.", "careers@acme.com"),
    ("To apply, email your resume to Jobs@Acme.io.", "jobs@acme.io"),
    ("يرجى إرسال السيرة الذاتية على hr@company.eg", "hr@company.eg"),
    ("Questions about privacy? Contact privacy@acme.com. Apply through our portal.", None),
    ("Built by our team. Contact support@acme.com for help with the site.", None),
    ("For more info, write to sara@acme.com.", None),  # no apply cue nearby
    ("", None),
])
def test_find_application_email(text, expected):
    assert apply_agent.find_application_email(text) == expected


def test_prefers_the_hiring_inbox_over_other_addresses():
    text = ("Send your CV to talent@acme.com. Media enquiries: press@acme.com. "
            "For anything else email office@acme.com.")
    assert apply_agent.find_application_email(text) == "talent@acme.com"


# ---- decide_apply_path ------------------------------------------------------------------

@pytest.mark.parametrize("mode, email_on, has_email, expected", [
    ("off", True, True, "draft_for_review"),        # Requires Review still wins
    ("whitelist", True, True, "email_apply"),
    ("any", True, True, "email_apply"),
    ("any", False, True, "auto_submit"),
    ("whitelist", True, False, "draft_for_review"),
])
def test_decide_apply_path_email_route(monkeypatch, mode, email_on, has_email, expected):
    monkeypatch.setattr(config, "AUTO_APPLY_MODE", mode)
    monkeypatch.setattr(config, "AUTO_APPLY_EMAIL", email_on)
    job = {"source": "linkedin", "apply_email": "hr@acme.com" if has_email else None}
    assert apply_agent.decide_apply_path(job) == expected


# ---- cover letters -------------------------------------------------------------------------

def test_cover_letter_falls_back_to_a_template_when_the_model_fails():
    with patch("agents.cover_letter_agent.get_llm", side_effect=RuntimeError("rate limit")):
        letter = cover_letter_agent.generate_cover_letter(
            PROFILE, {"title": "ML Engineer", "company": "Acme", "description": "x"}, ["Python"])
    assert letter["source"] == "template"
    assert "ML Engineer position at Acme" in letter["text"]
    assert letter["text"].rstrip().endswith("Aly Maklad")
    assert "Python" in letter["text"]


def test_cover_letter_rejects_placeholders_from_the_model():
    fake = Mock()
    fake.invoke.return_value = Mock(content="Dear [Hiring Manager],\n" + "word " * 120)
    with patch("agents.cover_letter_agent.get_llm", return_value=fake):
        letter = cover_letter_agent.generate_cover_letter(PROFILE, {"title": "ML Engineer"})
    assert letter["source"] == "template"


def test_cover_letter_from_the_model_is_signed():
    body = "Dear Hiring Team,\n\n" + ("I build agents with LangChain. " * 25)
    fake = Mock()
    fake.invoke.return_value = Mock(content=body)
    with patch("agents.cover_letter_agent.get_llm", return_value=fake):
        letter = cover_letter_agent.generate_cover_letter(PROFILE, {"title": "ML Engineer"})
    assert letter["source"] == "llm" and letter["text"].rstrip().endswith("Aly Maklad")


# ---- orchestrator apply nodes --------------------------------------------------------------

@pytest.fixture()
def quiet(monkeypatch):
    """No model call, no stored profile, a master CV on disk."""
    monkeypatch.setattr(orchestrator, "_stored_profile", lambda: PROFILE)
    monkeypatch.setattr(orchestrator, "_master_cv", lambda: "cv/current_cv.pdf")
    monkeypatch.setattr("agents.cover_letter_agent.generate_cover_letter",
                        lambda *a, **k: {"text": "Dear Hiring Team, ...", "source": "llm"})


def _state(**job):
    return {"job": {"title": "ML Engineer", "company": "Acme", "description": "d", **job},
            "cv_text": "", "ats_result": {"score": 0.8}, "status": ""}


def test_a_cv_is_always_attached(quiet):
    state = orchestrator.decide_apply_path_node(_state())
    assert state["attachment_path"] == "cv/current_cv.pdf"  # no tailored CV -> the master
    state = _state(); state["cv_path"] = "cv_output/cv_7.pdf"
    assert orchestrator.decide_apply_path_node(state)["attachment_path"] == "cv_output/cv_7.pdf"


def test_decide_reads_the_application_email_from_the_posting(quiet):
    state = orchestrator.decide_apply_path_node(_state(description="Send your CV to hr@acme.com"))
    assert state["job"]["apply_email"] == "hr@acme.com"


def test_draft_carries_cover_letter_attachment_and_address(quiet):
    state = orchestrator.decide_apply_path_node(_state(description="Send your CV to hr@acme.com"))
    state = orchestrator.draft_node(state)
    assert state["status"] == "pending_review"
    assert state["cover_letter"] and state["result"]["cv_path"] == "cv/current_cv.pdf"
    assert state["result"]["apply_email"] == "hr@acme.com"


def test_dry_run_auto_submit_is_would_apply(quiet, monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", True)
    state = orchestrator.auto_submit_node(orchestrator.decide_apply_path_node(_state()))
    assert state["status"] == "would_apply"


def test_missing_form_submitter_becomes_a_draft_not_a_failure(quiet, monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", False)
    state = orchestrator.auto_submit_node(orchestrator.decide_apply_path_node(_state()))
    assert state["status"] == "pending_review" and "can't submit" in state["note"]


def test_email_apply_dry_run(quiet, monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", True)
    state = orchestrator.decide_apply_path_node(_state(apply_email="hr@acme.com"))
    state = orchestrator.email_apply_node(state)
    assert state["status"] == "would_apply"
    assert state["email"]["to"] == "hr@acme.com" and state["email"]["status"] == "dry_run"
    assert state["email"]["subject"] == "Application for ML Engineer – Aly Maklad"


def test_email_apply_sends_when_live(quiet, monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", False)
    sent = {}
    monkeypatch.setattr("agents.email_agent.is_authenticated", lambda: True)
    monkeypatch.setattr("agents.email_agent.send_application_email",
                        lambda to, subject, body, cv, **kw: sent.update(to=to, cv=cv, body=body, **kw) or
                        {"dry_run": False, "gmail_message_id": "m1"})
    state = orchestrator.email_apply_node(orchestrator.decide_apply_path_node(_state(apply_email="hr@acme.com")))
    assert state["status"] == "sent" and state["email"]["gmail_message_id"] == "m1"
    assert sent == {"to": "hr@acme.com", "cv": "cv/current_cv.pdf", "body": "Dear Hiring Team, ...",
                    "applicant_name": "Aly Maklad", "job_title": "ML Engineer"}


def test_email_apply_without_gmail_drafts_instead(quiet, monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr("agents.email_agent.is_authenticated", lambda: False)
    state = orchestrator.email_apply_node(orchestrator.decide_apply_path_node(_state(apply_email="hr@acme.com")))
    assert state["status"] == "pending_review" and "Gmail isn't connected" in state["note"]


def test_email_send_failure_drafts_and_is_logged(quiet, monkeypatch):
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr("agents.email_agent.is_authenticated", lambda: True)
    monkeypatch.setattr("agents.email_agent.send_application_email",
                        Mock(side_effect=RuntimeError("quota")))
    state = orchestrator.email_apply_node(orchestrator.decide_apply_path_node(_state(apply_email="hr@acme.com")))
    assert state["status"] == "pending_review" and state["email"]["status"] == "failed"


# ---- persistence --------------------------------------------------------------------------

@pytest.fixture()
def temp_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'apply.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    original = db.engine, db.SessionLocal
    db.engine, db.SessionLocal = engine, Session
    try:
        yield engine
    finally:
        db.engine, db.SessionLocal = original
        engine.dispose()


def test_finalize_stores_letter_note_and_the_email_log(temp_db):
    from jobs import daily_run
    result = {"status": "sent", "ats_result": {"score": 0.8}, "cover_letter": "Dear...",
              "cover_letter_source": "llm", "note": "Emailed to hr@acme.com.",
              "job": {"apply_email": "hr@acme.com"},
              "email": {"to": "hr@acme.com", "subject": "Application", "status": "sent",
                        "dry_run": False, "gmail_message_id": "m1"}}
    daily_run._finalize_job(1, "ML Engineer", "Acme", result)
    with db.get_session() as session:
        app = session.query(Application).one()
        log = session.query(EmailLog).one()
    assert (app.status, app.email_sent, app.apply_email, app.cover_letter) == ("sent", True, "hr@acme.com", "Dear...")
    assert app.date_applied is not None
    assert (log.application_id, log.to_email, log.status) == (app.id, "hr@acme.com", "sent")


def test_would_apply_is_not_an_application_date(temp_db):
    from jobs import daily_run
    daily_run._finalize_job(1, "ML Engineer", "Acme", {"status": "would_apply", "ats_result": {}})
    with db.get_session() as session:
        app = session.query(Application).one()
    assert app.status == "would_apply" and app.date_applied is None and not app.email_sent


# ---- reading a pasted posting ----------------------------------------------------------------

JSON_LD_PAGE = """<html><head><title>ML Engineer | Acme Careers</title>
<script type="application/ld+json">{"@context": "https://schema.org", "@type": "JobPosting",
 "title": "ML Engineer", "hiringOrganization": {"@type": "Organization", "name": "Acme"},
 "description": "<p>%s</p>"}</script></head>
<body><nav>Home Jobs About</nav><main>ignored</main></body></html>""" % ("Build models. " * 30)

PLAIN_PAGE = """<html><head><title>Data Scientist - Beta Corp</title></head><body>
<nav>Menu items everywhere</nav><header>Logo</header>
<div id="jobDescriptionBody">%s</div><footer>Copyright</footer></body></html>""" % ("Analyse data. " * 30)


def _page(html, status=200):
    return Mock(status_code=status, text=html, raise_for_status=lambda: None)


def test_read_posting_uses_schema_org_job_posting():
    from jobs import apply_from_link
    with patch("jobs.apply_from_link.requests.get", return_value=_page(JSON_LD_PAGE)):
        job = apply_from_link.read_posting("https://careers.acme.com/jobs/1")
    assert (job["title"], job["company"], job["source"]) == ("ML Engineer", "Acme", "manual_link")
    assert job["description"].startswith("Build models.")


def test_read_posting_falls_back_to_the_description_block():
    from jobs import apply_from_link
    with patch("jobs.apply_from_link.requests.get", return_value=_page(PLAIN_PAGE)):
        job = apply_from_link.read_posting("https://beta.example/jobs/9")
    assert job["title"] == "Data Scientist"
    assert "Menu items" not in job["description"] and job["description"].startswith("Analyse data.")


def test_read_posting_reports_a_blocked_site():
    from jobs import apply_from_link
    with patch("jobs.apply_from_link.requests.get", return_value=_page("", status=403)):
        with pytest.raises(ValueError, match="blocks automated readers"):
            apply_from_link.read_posting("https://wuzzuf.net/jobs/p/123")


LINKEDIN_POSTING = """<section><h2 class="top-card-layout__title topcard__title">Senior FDE</h2>
<a class="topcard__org-name-link">Canada Data Labs</a><span class="topcard__flavor--bullet">Cairo, Egypt</span>
<div class="show-more-less-html__markup">%s</div></section>""" % ("Build agents. " * 30)


@pytest.mark.parametrize("url", [
    "https://www.linkedin.com/jobs/view/4474208050/",
    "https://www.linkedin.com/jobs/collections/recommended/?currentJobId=4474208050",
    "https://eg.linkedin.com/jobs/view/senior-fde-at-canada-data-labs-4474208050?trk=x",
])
def test_linkedin_links_are_read_from_the_posting_endpoint_not_the_sign_in_wall(url, monkeypatch):
    from jobs import apply_from_link
    monkeypatch.setattr(config, "LINKEDIN_REQUEST_DELAY", 0)
    calls = []

    def fake_get(u, **kw):
        calls.append(u)
        return _page(LINKEDIN_POSTING)

    # One fake for every request (both modules share the requests module):
    # the only URL fetched must be the posting endpoint, never the job page.
    with patch("requests.get", side_effect=fake_get):
        job = apply_from_link.read_posting(url)
    assert calls == ["https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4474208050"]
    assert (job["title"], job["company"], job["location"]) == ("Senior FDE", "Canada Data Labs", "Cairo, Egypt")
    assert job["url"] == "https://www.linkedin.com/jobs/view/4474208050/"


def test_a_sign_in_page_is_refused_not_saved_as_a_job():
    from jobs import apply_from_link
    wall = "<html><head><title>Sign in | Example</title></head><body><main>%s</main></body></html>" % ("Welcome back. " * 30)
    with patch("jobs.apply_from_link.requests.get", return_value=_page(wall)):
        with pytest.raises(ValueError, match="sign-in page"):
            apply_from_link.read_posting("https://careers.example.com/jobs/1")


def test_accommodation_and_help_addresses_never_receive_a_cv():
    """Seen in real postings: the only address in an Airbnb or ServiceNow
    posting is for candidates who need a disability accommodation."""
    airbnb = ("If you are a candidate with a disability and require reasonable accommodation "
              "in order to submit an application, please contact us at: reasonableaccommodations@airbnb.com")
    servicenow = ("If you require a reasonable accommodation to complete any part of the application "
                  "process, or are unable to use this online application and need an alternative method "
                  "to apply, please contact globaltalentss@servicenow.com for assistance.")
    assert apply_agent.find_application_email(airbnb) is None
    assert apply_agent.find_application_email(servicenow) is None
    real = "How to Apply: we are building our talent pool. Please submit your CV to: careers@canadadatalabs.ai"
    assert apply_agent.find_application_email(real) == "careers@canadadatalabs.ai"
