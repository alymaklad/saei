"""Anthropic (Claude) and OpenAI as LLM providers, chosen on the Settings page."""
import pytest
from fastapi.testclient import TestClient

import config
from agents import llm


def test_anthropic_model(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(config, "ANTHROPIC_MODEL", "claude-sonnet-5-5")
    chat = llm.get_llm(temperature=0.0, max_tokens=2048)
    assert type(chat).__name__ == "ChatAnthropic"
    assert chat.model == "claude-sonnet-5-5" and chat.max_tokens == 2048
    assert llm.get_llm().max_tokens == 4096   # Anthropic requires a cap


def test_openai_reasoning_and_chat_models(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-5-mini")
    chat = llm.get_llm(temperature=0.0, max_tokens=1000)
    assert type(chat).__name__ == "ChatOpenAI"
    assert chat.model_name == "gpt-5-mini" and chat.max_tokens == 1000
    assert chat.reasoning_effort == "low"

    monkeypatch.setattr(config, "OPENAI_MODEL", "gpt-4.1-mini")
    chat = llm.get_llm(temperature=0.2)
    assert chat.reasoning_effort is None and chat.temperature == 0.2


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_missing_key_says_so(monkeypatch, provider):
    monkeypatch.setattr(config, "LLM_PROVIDER", provider)
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    with pytest.raises(RuntimeError, match="API_KEY is not set"):
        llm.get_llm()


@pytest.fixture
def client(monkeypatch):
    import api
    saved = {}
    monkeypatch.setattr(api.env_store, "update_env_file", lambda updates: saved.update(updates))
    monkeypatch.setattr(api.importlib, "reload", lambda module: module)
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setattr(config, key, "")
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    return TestClient(api.app), saved


def test_settings_save_the_keys_and_never_return_them(client, monkeypatch):
    http, saved = client
    res = http.post("/api/settings", json={"llm_provider": "anthropic",
                                           "anthropic_api_key": " sk-ant-123 ",
                                           "anthropic_model": "claude-opus-5-5"})
    assert res.status_code == 200
    assert saved == {"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "sk-ant-123",
                     "ANTHROPIC_MODEL": "claude-opus-5-5"}
    assert "sk-ant-123" not in res.text
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_switching_without_a_key_is_refused(client, provider):
    http, saved = client
    res = http.post("/api/settings", json={"llm_provider": provider})
    assert res.status_code == 400 and "API key is required" in res.json()["detail"]
    assert saved == {}
