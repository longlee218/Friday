"""The skeleton for tools that live outside this process.

Loki is reached through an MCP server, and so will whatever comes after it. What
matters here is that a server is *configuration* — a new one is a block in
`config.yaml`, not a module — and that an agent is only ever handed the tools it
is supposed to have.
"""

from __future__ import annotations

import os

import pytest

from friday.kernel.config import ConfigError, MCPServerConfig, load_config
from friday.kernel.harness.mcp import build


def test_a_server_is_configuration_not_code(tmp_path):
    (tmp_path / "config.yaml").write_text(
        """
database_path: ./x.db
agents: {}
mcp_servers:
  loki:
    command: npx
    args: ["-y", "some-loki-mcp"]
    env:
      LOKI_URL: https://logs.example.invalid
"""
    )

    config = load_config(tmp_path / "config.yaml")

    (loki,) = config.mcp_servers
    assert loki.name == "loki"
    assert loki.command == "npx"
    assert loki.env == {"LOKI_URL": "https://logs.example.invalid"}


def test_an_allow_list_in_the_file_is_refused_and_says_where_it_went(tmp_path):
    """The operator's call, 2026-09-21: which tools may be called is declared
    in code, on the class that calls them. A file that can widen a guard is a
    guard the file's next editor widens by accident — on a server that also
    offers `release_rollback`. Refused rather than ignored, because an
    ignored `allow:` reads as one that is being honoured."""
    (tmp_path / "config.yaml").write_text(
        """
database_path: ./x.db
agents: {}
mcp_servers:
  loki:
    command: npx
    allow: [query_range]
"""
    )

    with pytest.raises(ConfigError, match="declared in code"):
        load_config(tmp_path / "config.yaml")


def test_a_server_reached_over_http_needs_no_command(tmp_path):
    (tmp_path / "config.yaml").write_text(
        """
database_path: ./x.db
agents: {}
mcp_servers:
  loki:
    url: https://mcp.example.invalid/sse
"""
    )

    (loki,) = load_config(tmp_path / "config.yaml").mcp_servers
    assert loki.url == "https://mcp.example.invalid/sse"


def test_a_server_that_is_neither_is_refused(tmp_path):
    """Half a connection is worse than none: it fails at the first tool call,
    inside an agent run, hours after anyone edited the file."""
    (tmp_path / "config.yaml").write_text(
        "database_path: ./x.db\nagents: {}\nmcp_servers:\n  loki: {}\n"
    )

    with pytest.raises(ConfigError, match="loki"):
        load_config(tmp_path / "config.yaml")


def test_a_server_offers_only_what_some_reader_declared():
    """An MCP server offers whatever it offers. The one this system talks to
    offers `release_rollback`, `release_apply` and `godaddy_dns_edit_record`
    beside its log tools, so what reaches an agent is the set the readers in
    `friday/sources/` say they call — passed in from the composition root,
    never read from the file."""
    (server,) = build(
        [MCPServerConfig(name="loki", command="npx", args=(), env={})],
        allowed=frozenset({"loki_query_range"}),
    )

    assert server.filter_func is not None


def test_a_child_gets_only_the_declared_env_not_the_parents(monkeypatch):
    """An MCP child is a process boundary, and §12 wants that boundary to carry
    only what it was given: the SDK's safe base env plus the server's declared
    secrets, nothing else. `build` passes exactly the declared `env` to the
    transport — it never copies this process's environment — so a Discord token
    or an API key sitting in `os.environ` does not cross into the child.

    The SDK adds the safe base env (`HOME`, `PATH`, …) on top when it spawns; a
    secret is never in that set, which is the half this test does not have to
    prove because `get_default_environment` names the keys it inherits.
    """
    monkeypatch.setenv("DISCORD_USER_TOKEN", "a-real-secret-should-not-leak")

    (server,) = build(
        [MCPServerConfig(name="loki", command="npx", args=(), env={"LOKI_TOKEN": "x"})],
        allowed=frozenset({"loki_query_range"}),
    )

    env = server.wrapped.client.transport.env
    assert env == {"LOKI_TOKEN": "x"}
    assert "DISCORD_USER_TOKEN" not in env


def test_the_sdk_safe_env_carries_no_secret():
    """What the child actually runs with is `get_default_environment()` merged
    with the declared env. The safe set is a fixed allow-list of innocuous keys,
    so pinning it here is what makes "nothing else" a checked claim rather than
    a trusted one."""
    from mcp.client.stdio import DEFAULT_INHERITED_ENV_VARS, get_default_environment

    assert "DISCORD_USER_TOKEN" not in DEFAULT_INHERITED_ENV_VARS
    assert "OPENAI_API_KEY" not in DEFAULT_INHERITED_ENV_VARS
    assert set(get_default_environment()) <= set(DEFAULT_INHERITED_ENV_VARS)


def test_there_is_always_a_filter_now_and_that_is_the_reversal():
    """This module used to say "no filter means every tool the server offers,
    which is a choice rather than an oversight". The choice could not be
    justified once the real catalogue was read: fifteen of its tools change
    production. An empty declaration now means a server that offers
    nothing."""
    (server,) = build(
        [MCPServerConfig(name="loki", command="npx", args=(), env={})],
        allowed=frozenset(),
    )

    assert server.filter_func is not None
