"""A requirement-extraction answer cut off by its token budget keeps every
requirement that arrived complete instead of failing the posting."""
from agents.ats_agent import _safe_json_object, _salvage_requirements

CUT_OFF = '''{
  "requirements": [
    {"name": "Python", "category": "technical_skill", "importance": "required", "type": "skill", "concept_family": null, "evidence_expected": "hands_on"},
    {"name": "PyTorch", "category": "tool", "importance": "required", "type": "skill", "concept_family": "ml_framework", "evidence_expected": "hands_on"},
    {"name": "Kuber'''


def test_cut_off_answer_keeps_the_complete_requirements():
    assert _safe_json_object(CUT_OFF) == {}
    salvaged = _salvage_requirements(CUT_OFF)
    assert [r["name"] for r in salvaged["requirements"]] == ["Python", "PyTorch"]
    assert salvaged["years_experience_required"] is None


def test_nothing_to_salvage():
    assert _salvage_requirements("I think the posting wants...") == {}
    assert _salvage_requirements("") == {}


def test_extract_requirements_uses_the_salvage(monkeypatch):
    from agents import ats_agent

    class Resp:
        content = CUT_OFF
        additional_kwargs = {}
        response_metadata = {}

    class LLM:
        def invoke(self, messages):
            return Resp()

    monkeypatch.setattr(ats_agent, "get_llm", lambda **kw: LLM())
    result = ats_agent.extract_requirements("We need Python and PyTorch. " * 20)
    assert {r["name"] for r in result["requirements"]} >= {"Python", "PyTorch"}
