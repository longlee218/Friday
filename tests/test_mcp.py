"""The skeleton for tools that live outside this process.

Loki is reached through an MCP server, and so will whatever comes after it. What
matters here is that a server is *configuration* — a new one is a block in
`config.yaml`, not a module — and that an agent is only ever handed the tools it
is supposed to have.
"""

from __future__ import annotations

import pytest

from friday.config import ConfigError, MCPServerConfig, load_config
from friday.mcp import build


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
    allow: [query_range, labels]
"""
    )

    config = load_config(tmp_path / "config.yaml")

    (loki,) = config.mcp_servers
    assert loki.name == "loki"
    assert loki.command == "npx"
    assert loki.allow == ("query_range", "labels")


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


def test_an_agent_sees_only_the_tools_it_is_allowed():
    """An MCP server offers whatever it offers. A trace is read-only, and
    nothing about it should be able to delete a log stream."""
    (server,) = build([
        MCPServerConfig(
            name="loki", command="npx", args=(), env={},
            allow=("query_range",),
        )
    ])

    assert server.tool_filter is not None


def test_no_filter_means_every_tool_and_that_is_a_choice():
    (server,) = build([
        MCPServerConfig(name="loki", command="npx", args=(), env={}, allow=())
    ])

    assert server.tool_filter is None
