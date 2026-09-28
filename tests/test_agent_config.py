"""Named model tiers, and the agent declarations that pick one by name (board
`domains-plug-in`, tickets 07 and 17)."""

from __future__ import annotations

import pytest

from friday.kernel.config import ConfigError, load_config
from friday.sdk.agent import AgentDeclaration

SAMPLE = """
tiers:
  flash:
    api_key: ${FLASH_API_KEY}
    base_url: "https://api.minimax.io/v1"
    model: "MiniMax-M3"
    settings:
      max_tokens: 4096
  strong:
    api_key: ${STRONG_API_KEY}
    base_url: "https://api.openai.com/v1"
    model: "gpt-4o"
"""

TRIAGE_LIKE = AgentDeclaration(
    name="triage", tier="flash", temperature=0.0, max_turns=1, tokens=50_000,
    request_timeout_seconds=30.0,
)


def write(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return path


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("FLASH_API_KEY", "k1")
    monkeypatch.setenv("STRONG_API_KEY", "k2")


def test_each_tier_carries_its_own_endpoint_and_model(tmp_path, keys):
    tiers = load_config(write(tmp_path, SAMPLE)).tiers

    assert (tiers["flash"].base_url, tiers["flash"].model) == (
        "https://api.minimax.io/v1", "MiniMax-M3",
    )
    assert (tiers["strong"].base_url, tiers["strong"].model) == (
        "https://api.openai.com/v1", "gpt-4o",
    )


def test_the_key_is_read_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("FLASH_API_KEY", "secret-1")
    monkeypatch.setenv("STRONG_API_KEY", "secret-2")

    assert load_config(write(tmp_path, SAMPLE)).tiers["flash"].api_key == "secret-1"


def test_an_unset_variable_is_an_error_naming_it(tmp_path, monkeypatch):
    """Substituting empty string would surface as a confusing 401 instead."""
    monkeypatch.delenv("FLASH_API_KEY", raising=False)
    monkeypatch.setenv("STRONG_API_KEY", "k2")

    with pytest.raises(ConfigError, match="FLASH_API_KEY"):
        load_config(write(tmp_path, SAMPLE))


def test_an_agent_takes_its_endpoint_from_its_tier_and_its_behaviour_from_code(
    tmp_path, keys
):
    """The tier says where the model lives; the declaration says how the agent
    behaves. Temperature is per job, so it joins the tier's provider settings
    rather than living in the file."""
    agent = load_config(write(tmp_path, SAMPLE)).agent(TRIAGE_LIKE)

    assert (agent.name, agent.model, agent.api_key) == ("triage", "MiniMax-M3", "k1")
    assert agent.settings == {"max_tokens": 4096, "temperature": 0.0, "timeout": 30.0}
    assert (agent.max_turns, agent.tokens) == (1, 50_000)


def test_an_undeclared_tier_refuses_the_boot(tmp_path, keys):
    """A tier name code picks and the file does not carry is a typo or a
    missing block — never a reason to run without the model."""
    config = load_config(write(tmp_path, SAMPLE))
    typo = AgentDeclaration(
        name="triage", tier="flsh", temperature=0.0, max_turns=1, tokens=1,
        request_timeout_seconds=30.0,
    )

    with pytest.raises(ConfigError, match="'flsh'.*flash, strong"):
        config.agent(typo)


def test_a_behaviour_knob_in_a_tier_is_refused(tmp_path):
    """`max_turns` under a tier would be a knob the file claims to set and
    nothing reads."""
    text = "tiers:\n  flash: { provider: openai, api_key: k, model: m, max_turns: 3 }\n"

    with pytest.raises(ConfigError, match="max_turns"):
        load_config(write(tmp_path, text))


def test_the_old_agents_block_is_refused_and_says_where_it_went(tmp_path):
    text = "agents:\n  triage: { provider: openai, api_key: k, model: m }\n"

    with pytest.raises(ConfigError, match="tiers"):
        load_config(write(tmp_path, text))


def test_a_tier_missing_its_model_is_rejected_by_name(tmp_path):
    text = 'tiers:\n  flash:\n    base_url: "https://x"\n    api_key: "k"\n'

    with pytest.raises(ConfigError, match="flash"):
        load_config(write(tmp_path, text))


def test_a_provider_shorthand_fills_the_base_url(tmp_path):
    """All Chat Completions, so a `provider:` names one instead of pasting its
    URL, and nothing in the harness changes."""
    text = (
        "tiers:\n"
        "  a: { provider: minimax,  api_key: k, model: MiniMax-M3 }\n"
        "  b: { provider: deepseek, api_key: k, model: deepseek-chat }\n"
        "  c: { provider: openai,   api_key: k, model: gpt-4o }\n"
    )

    tiers = load_config(write(tmp_path, text)).tiers

    assert tiers["a"].base_url == "https://api.minimax.io/v1"
    assert tiers["b"].base_url == "https://api.deepseek.com"
    assert tiers["c"].base_url == "https://api.openai.com/v1"


def test_an_explicit_base_url_wins_over_the_shorthand(tmp_path):
    text = (
        "tiers:\n"
        '  flash: { provider: openai, base_url: "https://gateway.internal/v1", '
        "api_key: k, model: m }\n"
    )

    assert (
        load_config(write(tmp_path, text)).tiers["flash"].base_url
        == "https://gateway.internal/v1"
    )


def test_an_unknown_provider_is_refused_by_name(tmp_path):
    text = "tiers:\n  flash: { provider: llama, api_key: k, model: m }\n"

    with pytest.raises(ConfigError, match="llama"):
        load_config(write(tmp_path, text))


def test_neither_base_url_nor_provider_names_the_shorthand_in_the_error(tmp_path):
    text = "tiers:\n  flash: { api_key: k, model: m }\n"

    with pytest.raises(ConfigError, match="provider"):
        load_config(write(tmp_path, text))


def test_max_tokens_reaches_the_model_settings_without_a_knob_for_it():
    """`max_tokens` needs no code here, and the point is to say so once.

    A tier's `settings:` are splatted into `ModelSettings`, so anything that
    class accepts is already configurable per tier. It bounds one answer — a
    provider setting, not a budget: the per-run budget is the declaration's
    `tokens`.
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


def test_the_client_retries_nothing_and_keeps_its_own_timeout():
    """The client's two silent retries bill three calls where the record holds
    one; retrying belongs to `Harness._attempts`, where it is counted. Its
    timeout is the client's own default — the one unbounded wait left, and the
    operator's call (ticket 17)."""
    from openai import DEFAULT_TIMEOUT

    from friday.kernel.harness.harness import _chat_model
    from friday.kernel.config import AgentConfig

    model = _chat_model(
        AgentConfig(
            name="a", api_key="k", base_url="https://example.invalid/v1",
            model="test-model",
        )
    )

    assert model.client.max_retries == 0
    assert model.client.timeout == DEFAULT_TIMEOUT
