"""Getting a token for a server that wants one, without a person pasting it.

Every MCP server this system reaches is behind Keycloak, and these servers
authenticate **a person**, not a service. So Friday carries the operator's
own session: they sign in once, interactively, and what is kept is a
**refresh token** — from then on the running process exchanges it for an
access token and never asks anyone anything.

**This replaces the `client_credentials` grant written here the day before,
and the reversal is the operator's** (2026-09-21). That version argued for
Friday presenting an identity of its own, which is the better answer when a
server will issue one. These will not: `devops-generic` and `db-generic`
authorise per person, and a service account is not on offer. Carrying the
operator's session says plainly whose reads these are — which is the honest
description either way, and now also the true one.

**What that costs, said out loud.** Friday reads as the operator. Keycloak's
log will show their name, not Friday's, and a right they hold is a right
Friday has. The guards that remain are the ones that were always doing the
work: what `friday.sources.Reads` declares it may call, what the server
filter allows, and what the database grants (every one of them `reader`).
None of those ever depended on which identity was presented.

**The interactive half is a command, not a startup step.** `authorize.py`
runs the sign-in once; the service only ever refreshes. A process that
blocks its own boot waiting for a browser is a process nobody can restart
unattended.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2

__all__ = ["NotAuthorised", "SsoTokens", "TokenStore"]

log = logging.getLogger(__name__)

#: Fetch a new access token this long before the old one lapses. A token that
#: expires mid-request costs a round trip and a retry; a few seconds of
#: overlap costs nothing.
EARLY_SECONDS = 30.0


class NotAuthorised(Exception):
    """Nobody has signed in for this server yet, or the refresh token has
    stopped being accepted.

    Its message names the command that fixes it, because the alternative is
    a 401 from somewhere three layers down that reads as the server being
    broken.
    """


@dataclass(frozen=True, slots=True)
class TokenStore:
    """Where a refresh token lives: one file, owner-readable, outside the
    database.

    **Outside the database on purpose.** `replay_case.py` copies the database
    to a temporary directory to run a case against it, the board renders what
    the database holds, and migrations rewrite rows wholesale. A credential in
    there is a credential that travels; this one does not.

    Under `data/`, which `.gitignore` already covers.
    """

    path: Path

    def _all(self) -> dict[str, Any]:
        try:
            found = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return found if isinstance(found, dict) else {}

    def read(self) -> str:
        return str(self._all().get("refresh_token") or "")

    def settings(self) -> dict[str, str]:
        """Where to exchange it and as whom.

        **In the file rather than in `config.yaml`**, because both are
        *discovered* rather than chosen: the server advertises its token
        endpoint at `/.well-known/oauth-authorization-server`, and the
        client id comes back from registering against it. Config that
        restates a discovered value is config that goes stale silently —
        and the file that has to be right is the one the sign-in wrote.
        """
        found = self._all()
        return {key: str(found.get(key) or "") for key in ("token_url", "client_id")}

    def write(
        self, refresh_token: str, *, token_url: str = "", client_id: str = ""
    ) -> None:
        # **Merged, not replaced.** Every refresh writes a rotated token
        # through here, and a write that dropped the endpoint and the client
        # id would work once and then lock the operator out with a file that
        # no longer says where to go or who to be.
        kept = self._all()
        kept["refresh_token"] = refresh_token
        if token_url:
            kept["token_url"] = token_url
        if client_id:
            kept["client_id"] = client_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Created 0600 *before* anything is written to it: writing first and
        # chmod'ing after leaves a window where the file is readable, and the
        # window is the whole of what this is protecting against.
        opened = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(opened, "w") as out:
            json.dump(kept, out)

    def forget(self) -> None:
        self.path.unlink(missing_ok=True)


@dataclass
class SsoTokens(httpx2.Auth):
    """The operator's Keycloak session, as an httpx auth handler.

    An auth handler rather than a header, because a header is set once when
    the server is constructed and an access token lapses in minutes. The
    SDK's streamable-HTTP params take `auth`, so the token is decided per
    request and a refresh needs no reconnection.

    **The refresh token rotates.** Keycloak hands back a new one with each
    exchange, and the old one stops working; a store that keeps the first
    one works until the first refresh and then locks the operator out with
    no obvious cause. So every exchange that returns one writes it.

    **A 401 is retried exactly once**, with a fresh access token. One can
    lapse between the check and the server reading it, and clocks differ
    across a network; a second 401 is the server saying no, which another
    token does not fix.
    """

    store: TokenStore
    #: Both are normally empty and come from the store, which is where the
    #: sign-in put what it discovered. Set them only to override.
    token_url: str = ""
    client_id: str = ""
    client_secret: str = ""
    #: Set in tests. `None` means one is made per exchange.
    client: Any = None

    _access: str = field(default="", repr=False, compare=False)
    _expires_at: float = field(default=0.0, repr=False, compare=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)

    def __repr__(self) -> str:
        # Never the secret, never either token. This object is reachable from
        # a server object that ends up in more than one log line.
        return f"SsoTokens(store={self.store.path.name!r})"

    async def async_auth_flow(self, request):
        request.headers["Authorization"] = f"Bearer {await self.token()}"
        answer = yield request
        if answer.status_code != 401:
            return
        log.info("%s: 401, exchanging the refresh token once more", self.store.path)
        request.headers["Authorization"] = f"Bearer {await self.token(fresh=True)}"
        yield request

    async def token(self, *, fresh: bool = False) -> str:
        """The current access token, exchanging the refresh token for one if
        there is none, if it is about to lapse, or if the caller says the one
        it had was refused.

        Under a lock, so a burst of calls with no token yet makes one
        exchange rather than one per call — and re-checked inside it, so the
        calls queued behind the one that fetched do not each fetch again.
        """
        if not fresh and self._access and time.monotonic() < self._expires_at:
            return self._access
        async with self._lock:
            if not fresh and self._access and time.monotonic() < self._expires_at:
                return self._access
            await self._exchange()
            return self._access

    def _where(self) -> tuple[str, str]:
        """The token endpoint and the client id, config first, file second."""
        found = self.store.settings()
        return (
            self.token_url or found["token_url"],
            self.client_id or found["client_id"],
        )

    async def _exchange(self) -> None:
        refresh = self.store.read()
        token_url, client_id = self._where()
        if not token_url or not client_id:
            raise NotAuthorised(
                f"{self.store.path} does not say where to exchange the token "
                f"or as whom. Run `uv run authorize.py <server>` once."
            )
        if not refresh:
            raise NotAuthorised(
                f"nobody has signed in to {token_url} yet. Run "
                f"`uv run authorize.py <server>` once; after that this "
                f"refreshes on its own."
            )

        form = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh,
        }
        if self.client_secret:
            form["client_secret"] = self.client_secret

        client = self.client or httpx2.AsyncClient(timeout=15.0)
        try:
            answer = await client.post(token_url, data=form)
            said = answer.json() if answer.status_code < 500 else {}
        finally:
            if self.client is None:
                await client.aclose()

        if answer.status_code >= 400:
            # A refused refresh token is not a hiccup: it has expired, been
            # revoked, or been rotated out by another process. Saying so
            # here, once, is better than a 401 three layers down that reads
            # as the server being broken. The stored one goes, so nothing
            # retries with it for the rest of the day.
            self.store.forget()
            raise NotAuthorised(
                f"{token_url} refused the stored refresh token "
                f"({said.get('error', answer.status_code)}). Sign in again "
                f"with `uv run authorize.py <server>`."
            )

        access = said.get("access_token")
        if not access:
            raise NotAuthorised(
                f"{token_url} answered without an access_token (keys: {sorted(said)})"
            )
        rotated = said.get("refresh_token")
        if rotated and rotated != refresh:
            self.store.write(str(rotated))
        self._access = str(access)
        self._expires_at = time.monotonic() + max(
            0.0, float(said.get("expires_in", 60)) - EARLY_SECONDS
        )
        log.info(
            "%s: access token for %s, good for %ss",
            token_url,
            client_id,
            said.get("expires_in"),
        )
