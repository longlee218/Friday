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
from typing import Any

from friday.config import ConfigError, MCPServerConfig
from friday.agent.harness import (
    MCPServer,
    MCPServerSse,
    MCPServerStdio,
    MCPServerStreamableHttp,
    create_static_tool_filter,
)

__all__ = ["build"]

log = logging.getLogger(__name__)


def build(
    configs: Sequence[MCPServerConfig], *, allowed: frozenset[str]
) -> list[MCPServer]:
    """Turn configuration into servers. Nothing is connected yet.

    Connecting is the composition root's business, because a connection has a
    lifetime and something has to close it.

    `allowed` is every tool any reader in this build declares it calls, and
    it is passed in rather than read from configuration: the operator's call
    of 2026-09-21 moved that list into code, onto the classes that make the
    calls, so a file cannot widen what a server offers. A server whose
    catalogue holds `release_rollback` hands over a catalogue that does not.
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

    from friday.agent.auth import SsoTokens, TokenStore

    # **Nothing is required in the block.** The sign-in is `authorize.py`'s,
    # once, with a person and a browser; it discovers the token endpoint,
    # registers this machine, and writes both beside the refresh token. What
    # is missing at boot is therefore the sign-in itself and never the
    # configuration to go and do it — and a value set here still wins, for
    # an install that has to pin one.
    return SsoTokens(
        store=TokenStore(Path("data/credentials") / f"{config.name}.json"),
        token_url=str(config.auth.get("token_url", "")),
        client_id=str(config.auth.get("client_id", "")),
        client_secret=str(config.auth.get("client_secret", "")),
    )


def _one(config: MCPServerConfig, allowed: frozenset[str]) -> MCPServer:
    # Always a filter, and never an empty one. "No filter means every tool"
    # was a choice this module used to state and can no longer justify: the
    # one server it actually talks to offers fifteen tools that change
    # production.
    tool_filter = create_static_tool_filter(allowed_tool_names=sorted(allowed))
    auth = _auth(config)
    if config.url:
        # Two ways to speak to a server that is already running, and the
        # choice is the server's rather than ours. The devops MCP measured on
        # 2026-09-21 is streamable HTTP; SSE is what this module assumed, and
        # an SSE client against an HTTP server fails at connect with nothing
        # about transports in the message.
        #
        # `headers` is how a token reaches it. Nothing here obtains one: this
        # server authenticates per person, and a graph that could mint its
        # own credential is a graph that can reach further than the operator
        # meant it to.
        if config.transport == "sse":
            log.info("mcp server %s over sse: %s", config.name, config.url)
            return MCPServerSse(
                params={
                    "url": config.url,
                    "headers": dict(config.headers),
                    "auth": auth,
                },
                name=config.name,
                tool_filter=tool_filter,
                cache_tools_list=True,
            )
        log.info("mcp server %s over http: %s", config.name, config.url)
        return MCPServerStreamableHttp(
            params={
                "url": config.url,
                "headers": dict(config.headers),
                # Decided per request rather than baked into a header: a
                # Keycloak access token lapses in minutes, and a header is
                # set once when this object is built.
                "auth": auth,
            },
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
