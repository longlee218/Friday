"""Tools that live outside this process.

Loki is reached through an MCP server, and whatever comes after it probably will
be too. So a server is **configuration**: adding one is a block in
`config.yaml`, not a module — the same reason `base_url` and `model` are
configuration, and for the same payoff.

An agent is handed only the tools it is supposed to have. A log server offers
whatever it offers, and a trace is a read-only act: nothing about answering
"why did this request fail" should be able to delete a log stream. The filter is
the guard, and it is declared next to the server rather than trusted to the
agent's instructions — a prompt is a request, and a filter is not.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from agents.mcp import (
    MCPServer,
    MCPServerSse,
    MCPServerStdio,
    create_static_tool_filter,
)

from friday.config import MCPServerConfig

__all__ = ["build"]

log = logging.getLogger(__name__)


def build(configs: Sequence[MCPServerConfig]) -> list[MCPServer]:
    """Turn configuration into servers. Nothing is connected yet.

    Connecting is the composition root's business, because a connection has a
    lifetime and something has to close it.
    """
    return [_one(config) for config in configs]


def _one(config: MCPServerConfig) -> MCPServer:
    # No filter means every tool the server offers, which is a choice rather
    # than an oversight — it is stated here so it reads as one.
    tool_filter = (
        create_static_tool_filter(allowed_tool_names=list(config.allow))
        if config.allow
        else None
    )
    if config.url:
        log.info("mcp server %s over sse: %s", config.name, config.url)
        return MCPServerSse(
            params={"url": config.url},
            name=config.name,
            tool_filter=tool_filter,
            cache_tools_list=True,
        )
    log.info("mcp server %s over stdio: %s", config.name, config.command)
    return MCPServerStdio(
        params={
            "command": config.command,
            "args": list(config.args),
            "env": dict(config.env),
        },
        name=config.name,
        tool_filter=tool_filter,
        # The list rarely changes and fetching it costs a round trip per run.
        cache_tools_list=True,
    )
