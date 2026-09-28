"""Ticket 04 slice 1 — per-agent model configuration."""

from __future__ import annotations

import pytest

from friday.kernel.config import ConfigError, load_config

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


def test_a_provider_shorthand_fills_the_base_url(tmp_path):
    """The first version runs on OpenAI, MiniMax or DeepSeek — all Chat
    Completions — so a `provider:` names one instead of pasting its URL, and
    nothing in the harness changes."""
    text = (
        'agents:\n'
        '  triage:    { provider: minimax,  api_key: k, model: MiniMax-M3 }\n'
        '  responder: { provider: deepseek, api_key: k, model: deepseek-chat }\n'
        '  extractor: { provider: openai,   api_key: k, model: gpt-4o }\n'
    )

    agents = load_config(write(tmp_path, text)).agents

    assert agents["triage"].base_url == "https://api.minimax.io/v1"
    assert agents["responder"].base_url == "https://api.deepseek.com"
    assert agents["extractor"].base_url == "https://api.openai.com/v1"


def test_an_explicit_base_url_wins_over_the_shorthand(tmp_path):
    """For a custom endpoint or a provider not listed: `base_url` is still the
    real field, and pinning one overrides the shorthand."""
    text = (
        'agents:\n'
        '  triage: { provider: openai, base_url: "https://gateway.internal/v1", '
        'api_key: k, model: m }\n'
    )

    assert (
        load_config(write(tmp_path, text)).agents["triage"].base_url
        == "https://gateway.internal/v1"
    )


def test_an_unknown_provider_is_refused_by_name(tmp_path):
    text = 'agents:\n  triage: { provider: llama, api_key: k, model: m }\n'

    with pytest.raises(ConfigError, match="llama"):
        load_config(write(tmp_path, text))


def test_neither_base_url_nor_provider_names_the_shorthand_in_the_error(tmp_path):
    text = 'agents:\n  triage: { api_key: k, model: m }\n'

    with pytest.raises(ConfigError, match="provider"):
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
    from friday.kernel.harness.harness import Harness
    from friday.kernel.config import AgentConfig

    built = Harness(
        config=AgentConfig(
            name="a", api_key="k", base_url="https://example.invalid/v1",
            model="test-model", settings={"temperature": 0, "max_tokens": 4096},
        ),
        instructions="i",
    )

    assert built.agent.model_settings["max_tokens"] == 4096


def test_the_client_retries_nothing_and_waits_no_longer_than_the_run():
    """Both numbers chosen here rather than inherited from the SDK.

    Its defaults are ten minutes and two silent retries. The first makes a run
    unbounded from this side — the pool works one task at a time, so that is
    every task waiting. The second is worse than slow: the provider bills three
    calls where the record holds one, and a record that disagrees with the
    invoice is what this board exists to stop. Retrying belongs to
    `Harness._attempts`, where it is counted and each attempt gets its own row.
    """
    from friday.kernel.harness.harness import _chat_model
    from friday.kernel.config import AgentConfig

    model = _chat_model(
        AgentConfig(
            name="a", api_key="k", base_url="https://example.invalid/v1",
            model="test-model", timeout_seconds=45.0, max_attempts=1,
        )
    )

    assert model.client.max_retries == 0
    # One attempt allowed, so one attempt gets the whole budget. What a share
    # of it buys when there is more than one is
    # `test_one_request_may_not_spend_the_whole_run`.
    assert model.client.timeout == 45.0
