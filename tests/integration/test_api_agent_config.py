from types import SimpleNamespace

import pytest
from deployd.adapters.outgoing.ai.agno_agent import AgnoGroqAgent
from deployd.entrypoints import api


@pytest.fixture
def fresh_agent(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key-not-used")
    monkeypatch.setattr(api, "_agent", None)
    monkeypatch.setattr(api, "_get_retrieval", lambda: SimpleNamespace(chroma=None, repo=None))


@pytest.mark.usefixtures("fresh_agent")
def test_the_agent_model_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("DEPLOYD_GROQ_MODEL", " openai/gpt-oss-20b ")

    assert api._get_agent().model_id == "openai/gpt-oss-20b"


@pytest.mark.usefixtures("fresh_agent")
def test_without_the_variable_the_default_model_is_used(monkeypatch):
    monkeypatch.delenv("DEPLOYD_GROQ_MODEL", raising=False)

    assert api._get_agent().model_id == AgnoGroqAgent.MODEL_ID
