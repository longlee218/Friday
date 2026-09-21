"""The skeleton for tools that live outside this process.

Loki is reached through an MCP server, and so will whatever comes after it. What
matters here is that a server is *configuration* — a new one is a block in
`config.yaml`, not a module — and that an agent is only ever handed the tools it
is supposed to have.
"""

from __future__ import annotations

import pytest

from friday.config import ConfigError, MCPServerConfig, load_config
from friday.agent.mcp import build


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

    assert server.tool_filter is not None


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

    assert server.tool_filter is not None
