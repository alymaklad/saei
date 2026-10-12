"""
Orchestrator — LangGraph state machine.

score -> (low fit) -> rewrite_cv -> (tailored score still low, or
                                      config.AUTO_APPLY_ON_TAILORED_SCORE is
                                      off) -> END (notify user)
                                  -> (config.AUTO_APPLY_ON_TAILORED_SCORE is
                                      on AND tailored score clears
                                      config.FIT_THRESHOLD) -> decide_apply_path
       -> (good fit) -> decide_apply_path -> auto_submit | email_apply | draft_for_review -> END

"Low fit" / "good fit" above is decided against config.FIT_THRESHOLD (0.0-1.0,
default 0.7, user-configurable from the Settings page -- see
route_on_score/route_after_rewrite, both read it fresh at call time).

decide_apply_path (agents/apply_agent.py) is the single choke point both
branches above funnel into before anything can auto-submit -- it checks
config.AUTO_APPLY_MODE ("off" / "any" / "whitelist", Settings page) and, in
"whitelist" mode, config.WHITELISTED_SOURCES too. See that file's docstring
for what each mode actually does.

Every path out of decide_apply_path carries a cover letter
(agents/cover_letter_agent.py) and a CV to attach -- the tailored one when the
job was rewritten, otherwise the master CV the user uploaded.

Respects config.DRY_RUN globally: when true, auto_submit and email_apply
record status "would_apply" (what WOULD have been sent) instead of submitting
or emailing anything. A job routed to a submitter that doesn't exist yet
falls back to a draft rather than failing.
"""
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END

import config
from agents.ats_agent import compute_ats_score, build_improvement_explanation
from agents.cv_rewriter_agent import rewrite_cv, render_cv_text, save_cv_as_pdf
from agents.apply_agent import (decide_apply_path, draft_for_review, auto_submit_greenhouse,
                                find_application_email)


class State(TypedDict):
    job: dict
    cv_text: str
    ats_result: Optional[dict]
    cv_rewritten: Optional[str]
    cv_path: Optional[str]
    tailored_ats_result: Optional[dict]
    tailored_ats_explanation: Optional[str]
    apply_path: Optional[str]
    attachment_path: Optional[str]
    cover_letter: Optional[str]
    cover_letter_source: Optional[str]
    email: Optional[dict]
    note: Optional[str]
    status: str
    result: Optional[dict]


def score_node(state: State) -> State:
    """Score the candidate against this job.

    Evidence comes from the STORED PROFILE, not from the CV file's text. The
    profile is the record the user maintains on the Profile page -- seeded
    from their upload, then corrected by hand -- so it is the truthful account
    of what they have done, and it is also what the tailored CV will be built
    from. Scoring the file instead would grade a snapshot the user has since
    fixed, and would report a skill gap they had already closed.

    Falls back to None, which makes compute_ats_score extract a throwaway
    profile from the text, when nothing has been stored yet.
    """
    state["ats_result"] = compute_ats_score(
        state["cv_text"], state["job"].get("description", ""),
        profile=_stored_profile(),
    )
    return state


def _stored_profile():
    import profile_store
    try:
        return profile_store.load_profile_dict()
    except Exception:  # noqa: BLE001 -- a DB hiccup must not stop the run
        return None


def route_on_score(state: State) -> str:
    # tailored_only has no baseline number to gate on by definition, so every
    # job is tailored and the decision is taken on the tailored score instead.
    # That is one LLM rewrite per job -- see config.ATS_SCORE_MODE.
    if config.ATS_SCORE_MODE == "tailored_only":
        return "rewrite_cv"
    return "rewrite_cv" if state["ats_result"]["score"] < config.FIT_THRESHOLD else "decide_apply_path"


def _rescore_kwargs(original: dict, rewritten: str) -> dict:
    """What to hand compute_ats_score() when re-scoring a tailored CV.

    Reuses the whole structured extraction rather than a list of names
    (compute_requirements_score checks `extracted.get("requirements")` and
    silently re-extracts without it), plus the profile and the rewritten text
    as evidence. Without those last two it re-parses the tailored CV with a
    second large LLM call -- which is where this exact path failed on Groq's
    8,000-tokens-per-minute tier, AFTER the expensive rewrite had already
    succeeded and been paid for.
    """
    kwargs = {
        "required_skills": original.get("requirements"),
        # Structural, no-LLM evidence extraction from the rewritten text.
        "evidence_text": rewritten,
    }
    if original.get("cv_profile"):
        # Dates and seniority come from the profile already built. The
        # no-fabrication rule means a rewrite cannot change the work history,
        # so re-deriving it from the tailored text would spend a call to
        # arrive at the same answer.
        kwargs["profile"] = original["cv_profile"]
    return kwargs


def _tailoring_profile(state: State) -> dict | None:
    """Which profile the rewrite is built from.

    The stored profile: it is the record the user maintains, the one this
    job was scored against, and the only one carrying the fields a CV needs
    but scoring never reads (per-role locations, project links). The profile
    the scoring pass built for itself is the fallback for a run before
    anything was stored, and None makes rewrite_cv drop to its plain-text
    path.
    """
    return _stored_profile() or (state.get("ats_result") or {}).get("cv_profile")


def rewrite_node(state: State) -> State:
    from agents.tailoring import tailor

    job_description = state["job"].get("description", "")
    # A document dict, not text -- the layout is applied by cv_render from the
    # structured result. tailor() rewrites, re-scores, and puts original lines
    # back (or keeps the CV as it is) whenever the rewrite would score LOWER
    # than the CV it was made from -- see agents/tailoring.py.
    #
    # The re-score reuses the extracted requirements and reads evidence from
    # the rewritten text; see _rescore_kwargs for why getting that wrong is
    # invisible -- the score still comes out, it just quietly costs an extra
    # large LLM call.
    outcome = tailor(
        state["cv_text"], job_description, state["ats_result"], _tailoring_profile(state),
        rescore=lambda text: compute_ats_score(
            text, job_description, **_rescore_kwargs(state["ats_result"], text)),
        rewrite=rewrite_cv,
    )
    document = outcome["document"]
    rewritten = outcome["text"]
    state["cv_rewritten"] = rewritten

    job_id = state["job"].get("id", "job")
    out_path = f"cv_output/cv_{job_id}.pdf"
    save_cv_as_pdf(document, out_path)
    state["cv_path"] = out_path

    tailored_ats_result = outcome["result"]
    state["tailored_ats_result"] = tailored_ats_result
    explanation = build_improvement_explanation(state["ats_result"], tailored_ats_result)

    # The rewrite's own checks have to reach the user on THIS path too. The
    # debug bench renders them beside the score; an unattended run has no
    # bench, and a guard that silently drops a bullet is worse than no guard --
    # the CV goes out with a hole in it and nothing says why. Appended to the
    # explanation because that is the field already stored on the Application
    # row and already read back into the daily report.
    if config.ATS_SCORE_MODE == "tailored_only":
        # No "up from X" line: the user asked to be shown one number, and a
        # comparison against a score that is never reported reads as noise.
        explanation = tailored_ats_result.get("explanation") or explanation

    notes = _rewrite_notes(document)
    if notes:
        explanation = f"{explanation}\n\n{notes}"
    state["tailored_ats_explanation"] = explanation

    state["status"] = "cv_rewritten_notify_user"
    state["result"] = {
        "status": state["status"],
        "cv_path": out_path,
        "ats_result": state["ats_result"],
        "tailored_ats_result": tailored_ats_result,
        "tailored_ats_explanation": explanation,
        "score_mode": config.ATS_SCORE_MODE,
        "integrity_warnings": _document_field(document, "integrity_warnings", []),
        "target_coverage": _document_field(document, "target_coverage", None),
        "listed_only": _document_field(document, "listed_only", []),
    }
    return state


def _document_field(document, key, default):
    """The degraded path returns a plain string, not a document."""
    return document.get(key, default) if isinstance(document, dict) else default


def _rewrite_notes(document) -> str:
    """The tailoring's own findings, as lines a person can read in a report."""
    lines = []
    warnings = _document_field(document, "integrity_warnings", [])
    if warnings:
        lines.append("Checks that rejected something during tailoring:")
        lines.extend(f"- {w}" for w in warnings)

    coverage = _document_field(document, "target_coverage", None) or {}
    if coverage.get("used"):
        lines.append("Wrote the job's own wording for: " + ", ".join(coverage["used"]) + ".")
    if coverage.get("missed"):
        lines.append("Could not place: " + ", ".join(coverage["missed"]) + ".")

    listed = _document_field(document, "listed_only", [])
    if listed:
        lines.append(
            "Only in your skills list, so earning partial credit: "
            + ", ".join(listed)
            + ". If a role or project actually used one, adding it there on the "
              "Profile page is worth the difference."
        )
    return "\n".join(lines)


def route_after_rewrite(state: State) -> str:
    """
    Opt-in re-entry point (config.AUTO_APPLY_ON_TAILORED_SCORE, off by
    default). If enabled and the tailored CV's re-scored ATS score now clears
    config.FIT_THRESHOLD, hand the job to the same decide_apply_path gate a
    naturally-good-fit job goes through -- so a rewritten CV never skips
    whatever AUTO_APPLY_MODE currently allows just because it took the
    rewrite path first. If disabled, or the tailored score still doesn't
    clear the bar, stop and notify the user, same as before this flag
    existed.
    """
    if not config.AUTO_APPLY_ON_TAILORED_SCORE:
        return "end"
    tailored = state.get("tailored_ats_result") or {}
    if (tailored.get("score") or 0) >= config.FIT_THRESHOLD:
        return "decide_apply_path"
    return "end"


def _master_cv() -> str | None:
    from cv_parser import find_default_cv
    return find_default_cv("cv")


def decide_apply_path_node(state: State) -> State:
    job = state["job"]
    if "apply_email" not in job:
        job["apply_email"] = find_application_email(job.get("description"))
    # Always something to attach: the tailored CV when this job was rewritten,
    # otherwise the master CV -- the one that scored well enough to skip it.
    state["attachment_path"] = state.get("cv_path") or _master_cv()
    state["apply_path"] = decide_apply_path(job)
    return state


def route_on_apply_path(state: State) -> str:
    return state["apply_path"]  # "auto_submit", "email_apply" or "draft_for_review"


def _write_cover_letter(state: State) -> str:
    """Generates (once) and stores the job's cover letter."""
    if not state.get("cover_letter"):
        from agents.cover_letter_agent import generate_cover_letter
        ats = state.get("tailored_ats_result") or state.get("ats_result") or {}
        letter = generate_cover_letter(_stored_profile() or ats.get("cv_profile"), state["job"],
                                       requirement_names=_evidenced_requirements(ats))
        state["cover_letter"] = letter["text"]
        state["cover_letter_source"] = letter["source"]
    return state["cover_letter"]


def _evidenced_requirements(ats: dict) -> list[str]:
    """Names of the job's requirements the profile already evidences."""
    names = []
    for item in ats.get("requirement_results") or []:
        if isinstance(item, dict) and (item.get("met") or (item.get("credit") or 0) > 0):
            name = item.get("name") or item.get("requirement") or item.get("text")
            if name:
                names.append(str(name))
    return names or list(ats.get("matched_skills") or [])


def _applicant_name() -> str:
    profile = _stored_profile() or {}
    return ((profile.get("contact") or {}).get("full_name") or config.APPLICANT_NAME or "").strip()


def _as_draft(state: State, note: str | None = None) -> State:
    letter = _write_cover_letter(state)
    state["result"] = draft_for_review(state["job"], state.get("attachment_path") or "", letter, note=note)
    state["note"] = note
    state["status"] = "pending_review"
    return state


def auto_submit_node(state: State) -> State:
    job = state["job"]
    letter = _write_cover_letter(state)
    if config.DRY_RUN:
        state["status"] = "would_apply"
        state["note"] = "Dry run: this would have been submitted through the site's application form."
        state["result"] = {
            "dry_run": True,
            "status": "would_apply",
            "job_url": job.get("url") or job.get("hostedUrl"),
        }
        return state

    # Real submission only reaches here per config.AUTO_APPLY_MODE (see
    # agents/apply_agent.py::decide_apply_path). No site has a working
    # submitter yet (auto_submit_greenhouse is a stub), so this lands in the
    # draft fallback rather than failing a job that's already been scored
    # and tailored.
    try:
        auto_submit_greenhouse(
            job_url=job.get("url") or job.get("hostedUrl"),
            cv_path=state.get("attachment_path") or "",
            cover_letter=letter,
            applicant_info={"name": config.APPLICANT_NAME, "email": config.APPLICANT_EMAIL},
        )
    except NotImplementedError:
        return _as_draft(state, "Sa'ei can't submit to this site's application form yet, "
                                "so it's ready for you to send.")
    state["status"] = "auto_submitted"
    return state


def email_apply_node(state: State) -> State:
    """Emails the CV and cover letter to the address the posting gave.

    Falls back to a draft (address filled in) whenever sending isn't
    possible: no CV to attach, or Gmail not connected -- checked up front,
    because an unattended run must never open the interactive sign-in."""
    from agents import email_agent
    job = state["job"]
    to = job.get("apply_email")
    attachment = state.get("attachment_path")
    if not attachment:
        return _as_draft(state, "No CV to attach -- upload one on the CV page.")
    if not config.DRY_RUN and not email_agent.is_authenticated():
        return _as_draft(state, "Gmail isn't connected, so the email wasn't sent. "
                                "Connect it in Settings, or send it from here.")

    letter = _write_cover_letter(state)
    name = _applicant_name()
    subject = f"Application for {job.get('title') or 'the open position'}" + (f" – {name}" if name else "")
    try:
        sent = email_agent.send_application_email(to, subject, letter, attachment,
                                                  applicant_name=name, job_title=job.get("title") or "")
    except Exception as exc:  # noqa: BLE001 -- a send failure leaves a ready draft
        state["email"] = {"to": to, "subject": subject, "status": "failed", "dry_run": False,
                          "error": str(exc)}
        return _as_draft(state, f"Sending the email failed ({exc}), so it's ready for you to send.")

    dry = bool(sent.get("dry_run"))
    state["email"] = {"to": to, "subject": subject, "dry_run": dry,
                      "status": "dry_run" if dry else "sent",
                      "gmail_message_id": sent.get("gmail_message_id")}
    state["status"] = "would_apply" if dry else "sent"
    state["note"] = (f"Dry run: this would have been emailed to {to}." if dry
                     else f"Emailed to {to}.")
    state["result"] = {"status": state["status"], "email": state["email"]}
    return state


def draft_node(state: State) -> State:
    note = None
    if state["job"].get("apply_email"):
        note = f"The posting asks for applications by email to {state['job']['apply_email']}."
    return _as_draft(state, note)


def build_graph():
    graph = StateGraph(State)
    graph.add_node("score", score_node)
    graph.add_node("rewrite_cv", rewrite_node)
    graph.add_node("decide_apply_path", decide_apply_path_node)
    graph.add_node("auto_submit", auto_submit_node)
    graph.add_node("email_apply", email_apply_node)
    graph.add_node("draft_for_review", draft_node)

    graph.set_entry_point("score")
    graph.add_conditional_edges("score", route_on_score, {
        "rewrite_cv": "rewrite_cv", "decide_apply_path": "decide_apply_path",
    })
    graph.add_conditional_edges("rewrite_cv", route_after_rewrite, {
        "decide_apply_path": "decide_apply_path", "end": END,
    })
    graph.add_conditional_edges("decide_apply_path", route_on_apply_path, {
        "auto_submit": "auto_submit", "email_apply": "email_apply",
        "draft_for_review": "draft_for_review",
    })
    graph.add_edge("auto_submit", END)
    graph.add_edge("email_apply", END)
    graph.add_edge("draft_for_review", END)
    return graph.compile()


app = build_graph()
