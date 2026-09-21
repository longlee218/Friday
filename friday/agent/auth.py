"""Getting a token for a server that wants one, without a person pasting it.

Every MCP server this system reaches is behind Keycloak, and the alternative
to this module is an operator copying a bearer token into `.env` and doing it
again when it lapses — which means the system is down between the lapse and
the noticing.

**The identity is the point, not the convenience** (the operator's call,
2026-09-21). A note in `friday/agent/mcp.py` used to argue that nothing here
should mint a credential, because a graph that can obtain one can reach
further than the operator meant it to. That argument was about the wrong
credential. What it forbids is Friday borrowing the *operator's* identity;
what this does is present its **own** — a Keycloak client with its own roles,
scoped by whoever configures it, revocable on its own, and visible in
Keycloak's log as Friday rather than as a person.

So the read-only guarantee does not rest here. It rests on the client's roles
in Keycloak, on the tool filter each server is built with, and on
`friday.sources.Reads` refusing a call no reader declared. This module only
answers "who is calling".

The grant is `client_credentials`: no user, no browser, no refresh token —
the client id and secret are exchanged for a short access token, and when it
lapses the same exchange is made again.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx2

__all__ = ["ClientCredentials"]

log = logging.getLogger(__name__)

#: Fetch a new token this long before the old one lapses. A token that
#: expires mid-request costs a round trip and a retry; a few seconds of
#: overlap costs nothing.
EARLY_SECONDS = 30.0


@dataclass
class ClientCredentials(httpx2.Auth):
    """Keycloak's client-credentials grant, as an httpx auth handler.

    An auth handler rather than a header, because a header is set once when
    the server is constructed and this token lapses in minutes. The SDK's
    streamable-HTTP params take `auth`, so the token is decided per request
    and a refresh needs no reconnection.

    **It retries a 401 exactly once, with a fresh token.** A token can lapse
    between the check and the server reading it, and a clock can differ
    across a network; one retry covers both. A second 401 is the server
    saying no, which is not something another token fixes.
    """

    token_url: str
    client_id: str
    client_secret: str
    scope: str = ""
    #: Set in tests. `None` means one is made per fetch.
    client: Any = None

    _token: str = field(default="", repr=False, compare=False)
    _expires_at: float = field(default=0.0, repr=False, compare=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)

    def __repr__(self) -> str:
        # Never the secret, and never the token. This object is reachable
        # from a server object that ends up in more than one log line.
        return f"ClientCredentials(client_id={self.client_id!r})"

    async def async_auth_flow(self, request):
        request.headers["Authorization"] = f"Bearer {await self.token()}"
        answer = yield request
        if answer.status_code != 401:
            return
        # Read the body before asking again: httpx will not let the response
        # be used after the next request is yielded, and a 401 whose reason
        # went unread is a 401 nobody can explain.
        log.info("%s: 401, asking for a new token once", self.token_url)
        request.headers["Authorization"] = f"Bearer {await self.token(fresh=True)}"
        yield request

    async def token(self, *, fresh: bool = False) -> str:
        """The current token, fetching one if there is none, if it is about
        to lapse, or if the caller says the one it had was refused.

        Under a lock, so a burst of calls with no token yet makes one
        exchange rather than one per call — and re-checked inside it, so the
        calls queued behind the one that fetched do not each fetch again.
        """
        if not fresh and self._token and time.monotonic() < self._expires_at:
            return self._token
        async with self._lock:
            if not fresh and self._token and time.monotonic() < self._expires_at:
                return self._token
            await self._fetch()
            return self._token

    async def _fetch(self) -> None:
        form = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        if self.scope:
            form["scope"] = self.scope

        client = self.client or httpx2.AsyncClient(timeout=15.0)
        try:
            answer = await client.post(self.token_url, data=form)
            answer.raise_for_status()
            said = answer.json()
        finally:
            if self.client is None:
                await client.aclose()

        token = said.get("access_token")
        if not token:
            # Named without quoting the body: a token endpoint's error body
            # is small, but it is the one response in this system most
            # likely to carry a credential back.
            raise RuntimeError(
                f"{self.token_url} answered without an access_token "
                f"(keys: {sorted(said)})"
            )
        self._token = token
        self._expires_at = time.monotonic() + max(
            0.0, float(said.get("expires_in", 60)) - EARLY_SECONDS
        )
        log.info(
            "%s: token for %s, good for %ss",
            self.token_url, self.client_id, said.get("expires_in"),
        )
