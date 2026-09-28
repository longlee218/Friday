"""Sign in to an MCP server once, so the running process never has to.

    uv run authorize.py devops-generic

Opens the operator's browser at Keycloak, waits for the redirect, exchanges
the code, and writes the **refresh token** where `friday.kernel.harness.auth` will
find it. Everything after that is `run_agent.py` refreshing on its own.

**A command rather than a startup step.** The sign-in needs a person and a
browser; a process that blocks its own boot waiting for one is a process
nobody can restart unattended. `run_agent.py` starts without it, says which
server is unauthorised, and goes on without that server.

**Authorization code with PKCE, over a loopback redirect.** The code comes
back to `127.0.0.1` and never leaves the machine, and the verifier means a
code intercepted on the way is useless without it. There is no client
secret in a public client and none is invented here.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any

import httpx2
from dotenv import load_dotenv

from friday.kernel.harness.auth import TokenStore
from friday.kernel.config import load_config


def pkce() -> tuple[str, str]:
    """A verifier and its S256 challenge, base64url with no padding.

    Padding is not optional-but-tidy: Keycloak compares the challenge it was
    given against one it computes, and `=` in one and not the other is a
    mismatch with a message about the code, not about the padding.
    """
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def discover(url: str) -> dict[str, Any]:
    """What the server says about signing in to it (RFC 8414).

    **Measured against both servers, 2026-09-22.** Neither needs a Keycloak
    realm spelled into `config.yaml`: each is its own authorization server
    and advertises everything at
    `<origin>/.well-known/oauth-authorization-server` — `authorization_code`
    and `refresh_token`, PKCE `S256`, a registration endpoint, and
    `token_endpoint_auth_methods_supported: ["none", "client_secret_post"]`,
    which is a public client and no secret to keep.

    Discovered rather than configured because a value copied into a file is
    a value that is wrong the day it moves, and this one is one HTTP GET
    away from being right.
    """
    origin = urllib.parse.urlsplit(url)._replace(
        path="/.well-known/oauth-authorization-server", query="", fragment=""
    )
    answer = httpx2.get(urllib.parse.urlunsplit(origin), timeout=20.0)
    answer.raise_for_status()
    said = answer.json()
    if not isinstance(said, dict):
        raise ValueError(f"{url}: its metadata is not an object")
    return said


def register(endpoint: str, redirect: str) -> str:
    """A client id for this machine, asked for rather than issued by hand.

    Dynamic client registration (RFC 7591), which both servers offer and
    which is how every MCP client reaches them — it is why the editor's own
    config for these is two lines. Nobody has to create a client, and there
    is no id to keep in step with a file.

    `token_endpoint_auth_method: none` asks for a *public* client. This runs
    on the operator's machine, where a secret would sit in a file next to
    the token it protects and protect nothing; PKCE is what actually binds
    the code to this process.
    """
    answer = httpx2.post(
        endpoint,
        json={
            "client_name": "friday",
            "redirect_uris": [redirect],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
        timeout=30.0,
    )
    said = answer.json() if answer.status_code < 500 else {}
    client_id = said.get("client_id")
    if not client_id:
        raise ValueError(
            f"{endpoint} registered no client "
            f"({said.get('error', answer.status_code)})"
        )
    return str(client_id)


class Caught(http.server.BaseHTTPRequestHandler):
    """The redirect, caught once.

    The browser is told something plain and true; the code goes to the
    waiting thread, never to a log line.
    """

    code: str = ""
    state: str = ""

    def do_GET(self) -> None:  # noqa: N802 - the stdlib's spelling
        asked = urllib.parse.urlparse(self.path)
        found = urllib.parse.parse_qs(asked.query)
        Caught.code = (found.get("code") or [""])[0]
        Caught.state = (found.get("state") or [""])[0]
        body = (
            b"Signed in. You can close this tab."
            if Caught.code
            else b"No code came back. Try again."
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: object) -> None:
        """Silent: the one thing this server ever receives is a URL with an
        authorization code in it."""


def authorize(name: str) -> int:
    config = load_config()
    server = next((s for s in config.mcp_servers if s.name == name), None)
    if server is None:
        print(
            f"no mcp server called {name!r} in config.yaml "
            f"(have: {', '.join(s.name for s in config.mcp_servers) or 'none'})",
            file=sys.stderr,
        )
        return 1
    auth = server.auth or {}
    if not server.url:
        print(f"{name}: no url to sign in to", file=sys.stderr)
        return 1

    # Ask the server before reading the file: anything the block does set
    # still wins, so an install that must pin an endpoint can.
    try:
        said = discover(server.url)
    except Exception as exc:  # noqa: BLE001 — a probe that fails is the answer
        print(f"{name}: could not read its metadata: {exc}", file=sys.stderr)
        return 1
    authorize_url = auth.get("authorize_url") or said.get("authorization_endpoint")
    token_url = auth.get("token_url") or said.get("token_endpoint")
    if not authorize_url or not token_url:
        print(
            f"{name}: {server.url} advertises no authorization or token "
            f"endpoint, and its auth block names neither",
            file=sys.stderr,
        )
        return 1

    verifier, challenge = pkce()
    state = secrets.token_urlsafe(24)
    listener = http.server.HTTPServer(("127.0.0.1", 0), Caught)
    redirect = f"http://127.0.0.1:{listener.server_port}/callback"

    # The redirect has to be known before registering, because it is part of
    # what is registered — hence a listener opened first, on whatever port
    # the machine gives.
    client_id = auth.get("client_id")
    if not client_id:
        endpoint = auth.get("registration_url") or said.get("registration_endpoint")
        if not endpoint:
            listener.server_close()
            print(
                f"{name}: no client_id configured and {server.url} offers no "
                f"registration endpoint — somebody has to create a client",
                file=sys.stderr,
            )
            return 1
        try:
            client_id = register(str(endpoint), redirect)
        except Exception as exc:  # noqa: BLE001
            listener.server_close()
            print(f"{name}: could not register a client: {exc}", file=sys.stderr)
            return 1
        print(f"Registered this machine with {name} as client {client_id}.")

    asked = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect,
        "scope": auth.get("scope") or "openid",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        **({"resource": auth["resource"]} if auth.get("resource") else {}),
    })
    where = f"{authorize_url}?{asked}"

    print(f"Opening your browser to sign in to {name}.")
    print(f"If it does not open, go to:\n\n{where}\n")
    threading.Thread(target=lambda: webbrowser.open(where), daemon=True).start()
    listener.handle_request()
    listener.server_close()

    if not Caught.code:
        print("no authorization code came back", file=sys.stderr)
        return 1
    if Caught.state != state:
        # Not a formality: a code arriving with somebody else's state is a
        # code this process did not ask for.
        print("the redirect carried a state this did not send", file=sys.stderr)
        return 1

    answer = httpx2.post(
        token_url,
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": Caught.code,
            "redirect_uri": redirect,
            "code_verifier": verifier,
            **({"client_secret": auth["client_secret"]} if auth.get("client_secret") else {}),
        },
        timeout=30.0,
    )
    said = answer.json() if answer.status_code < 500 else {}
    refresh = said.get("refresh_token")
    if not refresh:
        print(
            f"{token_url} answered without a refresh_token "
            f"({said.get('error', answer.status_code)}). The client may not "
            f"be allowed the offline scope.",
            file=sys.stderr,
        )
        return 1

    TokenStore(Path("data/credentials") / f"{name}.json").write(
        str(refresh), token_url=str(token_url), client_id=str(client_id)
    )
    print(f"Signed in. {name} will refresh on its own from now on.")
    return 0


def main() -> int:
    load_dotenv()
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    return authorize(sys.argv[1])


if __name__ == "__main__":
    raise SystemExit(main())
