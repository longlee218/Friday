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

Each server becomes a Pydantic AI `MCPToolset` over a FastMCP transport, with
the allow-list applied as a `.filtered()` wrapper. The vendor names come through
`harness.py`, which is the one module allowed to import the SDK; this module
builds toolsets from them.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from friday.kernel.harness.harness import (
    MCPToolset,
    SSETransport,
    StdioTransport,
    StreamableHttpTransport,
)
from friday.kernel.config import ConfigError, MCPServerConfig
from friday.sdk.sources import TOOL_CALL_TIMEOUT_SECONDS

__all__ = ["build", "name_of"]

log = logging.getLogger(__name__)


def name_of(toolset) -> str:
    """The configured name of a toolset `build` produced. A `FilteredToolset`
    does not carry its own id, so it is read off the `MCPToolset` it wraps —
    the one place the name this module set with `id=` still lives."""
    return getattr(getattr(toolset, "wrapped", None), "id", None) or ""


def build(configs: Sequence[MCPServerConfig], *, allowed: frozenset[str]) -> list:
    """Turn configuration into toolsets. Nothing is connected yet.

    Connecting is the composition root's business, because a connection has a
    lifetime and something has to close it — each toolset is an async context
    manager the root enters and the run unwinds.

    `allowed` is every tool any reader in this build declares it calls, and it
    is passed in rather than read from configuration: the operator's call of
    2026-09-21 moved that list into code, onto the classes that make the calls,
    so a file cannot widen what a server offers.
    """
    return [_one(config, allowed) for config in configs]


def _auth(config: MCPServerConfig) -> Any:
    """The credential handler for this server, or `None`.

    One kind today. A second would be a second branch here and nothing else,
    which is the shape worth keeping: whoever adds it does not have to find
    every place a token is attached.
    """
    if config.auth is None:
        return None
    kind = config.auth.get("kind", "oauth")
    if kind not in ("oauth", "keycloak"):
        raise ConfigError(
            f"mcp server {config.name!r}: auth kind {kind!r} is not one of: "
            "oauth, keycloak"
        )
    from pathlib import Path

    from friday.kernel.harness.auth import SsoTokens, TokenStore

    # **Nothing is required in the block.** The sign-in is `authorize.py`'s,
    # once, with a person and a browser; it discovers the token endpoint,
    # registers this machine, and writes both beside the refresh token. A value
    # set here still wins, for an install that has to pin one.
    return SsoTokens(
        store=TokenStore(Path("data/credentials") / f"{config.name}.json"),
        token_url=str(config.auth.get("token_url", "")),
        client_id=str(config.auth.get("client_id", "")),
        client_secret=str(config.auth.get("client_secret", "")),
    )


def _one(config: MCPServerConfig, allowed: frozenset[str]):
    """One server, as an allow-listed toolset.

    Always a filter, and never an empty one: "no filter means every tool" was a
    choice this module used to state and can no longer justify — the one server
    it actually talks to offers fifteen tools that change production, so an empty
    declaration means a server that offers nothing.

    `tool_error_behavior='failed'` so a failing tool becomes a message the model
    is told and carries on past, rather than a retry of a call it cannot fix.
    """
    def offered(ctx, tool_def) -> bool:
        return tool_def.name in allowed

    auth = _auth(config)
    transport: Any
    if config.url:
        # Two ways to speak to a server that is already running, and the choice
        # is the server's rather than ours. The devops MCP measured on
        # 2026-09-21 is streamable HTTP; SSE is what this module assumed, and an
        # SSE client against an HTTP server fails at connect with nothing about
        # transports in the message. `auth` reaches the server per request: a
        # Keycloak access token lapses in minutes, so it is decided on each call
        # rather than baked into a header set once.
        if config.transport == "sse":
            log.info("mcp server %s over sse: %s", config.name, config.url)
            transport = SSETransport(
                url=config.url, headers=dict(config.headers), auth=auth
            )
        else:
            log.info("mcp server %s over http: %s", config.name, config.url)
            transport = StreamableHttpTransport(
                url=config.url, headers=dict(config.headers), auth=auth
            )
    else:
        log.info("mcp server %s over stdio: %s", config.name, config.command)
        transport = StdioTransport(
            command=config.command, args=list(config.args), env=dict(config.env)
        )

    toolset = MCPToolset(
        transport,
        id=config.name,
        tool_error_behavior="failed",
        # The list rarely changes and fetching it costs a round trip per run.
        cache_tools=True,
        # A tool call an agent makes directly is bounded like one code makes
        # through `Reads.call`.
        read_timeout=TOOL_CALL_TIMEOUT_SECONDS,
    )
    return toolset.filtered(offered)
