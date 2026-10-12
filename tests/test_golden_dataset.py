"""
The golden dataset, run as tests.

Phase 0 of the generic-matching plan: freeze what the matcher does today so
the semantic layer can only add coverage, never quietly trade precision for
it. Every case in bench/matching_cases.json is a labelled judgement — the
plan's MATCH / PARTIAL / NO_MATCH / SIBLING / INSUFFICIENT_EVIDENCE — and the
deterministic relation it must produce with no LLM in the loop.

bench/score_regression.json pins the other half: relations can all be right
while a weight, a points table or a redistribution rule moves the final
number. That case is the worked example in docs/matching.md.

`bench/report.py` computes precision, recall and fallback rates from the same
files; these tests are the pass/fail gate, that is the measurement.
"""
import json
import pathlib
from unittest.mock import patch

import pytest

import config
from agents import ats_agent, cv_profile

BENCH = pathlib.Path(__file__).resolve().parent.parent / "bench"
CASES = json.loads((BENCH / "matching_cases.json").read_text(encoding="utf-8"))["cases"]
SCORE_CASES = json.loads((BENCH / "score_regression.json").read_text(encoding="utf-8"))["cases"]


def _spans(case):
    return [{"text": s["text"], "source": s["source"],
             "demonstrated": s["demonstrated"], "entry": "", "dated": True}
            for s in case["spans"]]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_the_deterministic_matcher_reproduces_the_golden_label(case):
    outcome = ats_agent.match_requirement(case["requirement"], _spans(case))
    expected = case["expect"]

    assert outcome["relation"] == expected["relation"], (
        f'{case["id"]}: {case["why"]}\n'
        f'  expected {expected["relation"]}, got {outcome["relation"]} '
        f'(evidence: {outcome.get("evidence")!r})')

    if "location" in expected:
        assert outcome["evidence_location"] == expected["location"], case["why"]
        assert outcome["credit"] == config.match_credit(
            expected["relation"], expected["location"])


@pytest.mark.parametrize("case", [c for c in CASES if c["expect"]["relation"] != "none"],
                         ids=[c["id"] for c in CASES if c["expect"]["relation"] != "none"])
def test_every_awarded_point_quotes_the_cv(case):
    """The auditability guarantee, checked case by case rather than asserted."""
    outcome = ats_agent.match_requirement(case["requirement"], _spans(case))
    assert outcome["evidence"], f'{case["id"]} scored with no evidence'
    assert any(outcome["evidence"] in span["text"] or span["text"] in outcome["evidence"]
               or outcome["evidence"] == span["text"]
               for span in case["spans"]) or outcome["evidence_location"] == "claimed", (
        f'{case["id"]}: evidence {outcome["evidence"]!r} is not one of the CV spans')


def test_no_case_labelled_no_match_or_sibling_scores_anything():
    """The single most important property of the whole dataset. A semantic
    layer that improves recall by loosening these is not an improvement."""
    offenders = []
    for case in CASES:
        if case["label"] not in ("NO_MATCH", "SIBLING"):
            continue
        outcome = ats_agent.match_requirement(case["requirement"], _spans(case))
        if outcome["credit"] > 0:
            offenders.append((case["id"], outcome["relation"], outcome["credit"]))
    assert not offenders, offenders


def test_the_dataset_covers_more_than_one_job_family():
    """The plan's genericity goal is not testable on software engineering
    alone -- one engine has to work across families, so the bench has to
    contain more than one."""
    families = {c["family"] for c in CASES}
    assert len(families) >= 5, families
    assert {"finance", "marketing", "product_management"} <= families


# ---- end-to-end score --------------------------------------------------------

@pytest.mark.parametrize("case", SCORE_CASES, ids=[c["id"] for c in SCORE_CASES])
def test_the_end_to_end_score_is_unchanged(case):
    profile, expected = case["profile"], case["expect"]

    spans = cv_profile.evidence_spans(profile)
    assert len(spans) == expected["span_count"]
    assert sum(1 for s in spans if s["demonstrated"]) == expected["demonstrated_span_count"]
    assert round(cv_profile.professional_years(profile), 1) == expected["cv_years"]

    with patch.object(ats_agent, "extract_requirements",
                      return_value=ats_agent._normalize_requirements(case["requirements"])), \
         patch.object(ats_agent.cv_profile, "build_profile", return_value=profile), \
         patch.object(ats_agent, "_entailment_pass", return_value={}):
        result = ats_agent.compute_requirements_score("cv text", "jd text")

    rows = {r["name"]: r for r in result["requirement_results"]}
    for name, want in expected["rows"].items():
        row = rows[name]
        assert row["relation"] == want["relation"], name
        assert row["evidence_location"] == want["location"], name
        assert abs(row["credit"] - want["credit"]) < 1e-9, name

    for bucket, want in expected["buckets"].items():
        got = result["breakdown"][bucket]
        assert abs(got["score"] - want["score"]) < 5e-4, bucket
        assert abs(got["weight"] - want["weight"]) < 5e-4, bucket

    points = {item["name"]: (item["points_earned"], item["points"])
              for bucket in result["breakdown"].values()
              for item in bucket.get("items") or []}
    for name, want in expected["rows"].items():
        earned, possible = points[name]
        assert abs(earned - want["points_earned"]) < 1e-9, name
        assert possible == want["points"], name

    assert result.get("inactive_buckets") == expected["inactive_buckets"]
    assert result["missing_skills"] == expected["missing_skills"]
    assert abs(result["score"] - expected["score"]) < 5e-4, result["score"]
    assert ats_agent._rating_label(result["score"]) == expected["rating"]
