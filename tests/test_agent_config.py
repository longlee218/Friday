"""Ticket 04 slice 1 — per-agent model configuration."""

from __future__ import annotations

import pytest

from friday.config import ConfigError, load_config

SAMPLE = """
agents:
  triage:
    api_key: ${TRIAGE_API_KEY}
    base_url: "https://api.minimax.io/v1"
    model: "MiniMax-M3"
    settings:
      temperature: 0
    max_turns: 1
    confidence_threshold: 0.7
  responder:
    api_key: ${RESPONDER_API_KEY}
    base_url: "https://api.openai.com/v1"
    model: "gpt-4o"
    max_turns: 1
"""


def write(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return path


def test_each_agent_carries_its_own_endpoint_and_model(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIAGE_API_KEY", "k1")
    monkeypatch.setenv("RESPONDER_API_KEY", "k2")

    config = load_config(write(tmp_path, SAMPLE))

    triage = config.agents["triage"]
    responder = config.agents["responder"]
    assert (triage.base_url, triage.model) == ("https://api.minimax.io/v1", "MiniMax-M3")
    assert (responder.base_url, responder.model) == ("https://api.openai.com/v1", "gpt-4o")


def test_the_key_is_read_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIAGE_API_KEY", "secret-1")
    monkeypatch.setenv("RESPONDER_API_KEY", "secret-2")

    config = load_config(write(tmp_path, SAMPLE))

    assert config.agents["triage"].api_key == "secret-1"


def test_an_unset_variable_is_an_error_naming_it(tmp_path, monkeypatch):
    """Substituting empty string would surface as a confusing 401 instead."""
    monkeypatch.delenv("TRIAGE_API_KEY", raising=False)
    monkeypatch.setenv("RESPONDER_API_KEY", "k2")

    with pytest.raises(ConfigError, match="TRIAGE_API_KEY"):
        load_config(write(tmp_path, SAMPLE))


def test_model_settings_are_carried_through(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIAGE_API_KEY", "k1")
    monkeypatch.setenv("RESPONDER_API_KEY", "k2")

    config = load_config(write(tmp_path, SAMPLE))

    assert config.agents["triage"].settings == {"temperature": 0}
    assert config.agents["responder"].settings == {}


def test_step_specific_options_are_available(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIAGE_API_KEY", "k1")
    monkeypatch.setenv("RESPONDER_API_KEY", "k2")

    config = load_config(write(tmp_path, SAMPLE))

    assert config.agents["triage"].options["confidence_threshold"] == 0.7


def test_an_agent_missing_its_model_is_rejected_by_name(tmp_path):
    text = 'agents:\n  triage:\n    base_url: "https://x"\n    api_key: "k"\n'

    with pytest.raises(ConfigError, match="triage"):
        load_config(write(tmp_path, text))


def test_max_tokens_reaches_the_model_settings_without_a_knob_for_it():
    """`max_tokens` needs no code here, and the point is to say so once.

    `settings:` is splatted into `ModelSettings`, so anything that class
    accepts is already configurable per agent — a fact worth a test rather
    than a comment, because the alternative is somebody adding a
    `max_tokens:` field beside it and two ways to say the same thing.

    It bounds one answer; `daily_token_budget` bounds a day. Both exist
    because they fail differently: a run that hits `max_tokens` comes back
    *truncated*, which `_UNCLOSED` in the responder exists to survive, while a
    day that hits its budget is a refusal that reaches a person.
    """
    from friday.agent.harness import Harness
    from friday.config import AgentConfig

    built = Harness(
        config=AgentConfig(
            name="a", api_key="k", base_url="https://example.invalid/v1",
            model="test-model", settings={"temperature": 0, "max_tokens": 4096},
        ),
        instructions="i",
    )

    assert built.agent.model_settings.max_tokens == 4096
