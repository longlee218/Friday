"""The operator signs in once; the process refreshes for ever after.

The operator's call, 2026-09-21, replacing the `client_credentials` grant
written here the day before. That version argued for Friday presenting an
identity of its own, which is the better answer **when a server will issue
one**. `devops-generic` and `db-generic` authorise a person and offer no
service account, so Friday carries the operator's session instead — which
says plainly whose reads these are.

What that costs is written in `friday/kernel/harness/auth.py` and not hidden: Friday
reads as the operator, and Keycloak's log will say so. The guards that
remain are the ones that were always doing the work — what a reader declares
it may call, what the server filter allows, and what the database grants.
None of them ever depended on which identity was presented.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat

import pytest

from friday.kernel.harness.auth import NotAuthorised, SsoTokens, TokenStore


class FakeToken:
    """A token endpoint that answers from a script and counts the asking."""

    def __init__(self, *answers, status=200):
        self.answers = list(answers) or [
            {"access_token": "a1", "expires_in": 300}
        ]
        self.status = status
        self.posts: list[dict] = []

    async def post(self, url, data=None):
        self.posts.append(dict(data or {}))
        said = self.answers[min(len(self.posts) - 1, len(self.answers) - 1)]
        return FakeAnswer(said, self.status)

    async def aclose(self):  # pragma: no cover - a client passed in is not ours
        raise AssertionError("a client passed in is not this module's to close")


class FakeAnswer:
    def __init__(self, said, status_code=200):
        self._said = said
        self.status_code = status_code

    def json(self):
        return self._said


def tokens(tmp_path, endpoint, *, refresh="r1", **kwargs) -> SsoTokens:
    store = TokenStore(tmp_path / "devops.json")
    if refresh:
        store.write(refresh)
    return SsoTokens(
        token_url="https://keycloak.invalid/realms/r/protocol/openid-connect/token",
        client_id="friday", store=store, client=endpoint, **kwargs,
    )


# --- where the refresh token lives ------------------------------------------


def test_the_store_keeps_a_token_and_hands_it_back(tmp_path):
    store = TokenStore(tmp_path / "t.json")

    store.write("r1")

    assert store.read() == "r1"


def test_the_file_is_owner_only_from_the_moment_it_exists(tmp_path):
    """Created 0600 rather than chmod'd afterwards: writing first and fixing
    the mode after leaves a window where it is readable, and that window is
    the whole of what this protects against."""
    store = TokenStore(tmp_path / "t.json")

    store.write("r1")

    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600


def test_no_file_reads_as_no_token_rather_than_an_error(tmp_path):
    assert TokenStore(tmp_path / "missing.json").read() == ""


def test_a_file_that_is_not_json_reads_as_no_token(tmp_path):
    store = TokenStore(tmp_path / "t.json")
    store.path.write_text("{ half written")

    assert store.read() == ""


def test_the_token_is_not_in_the_database(tmp_path):
    """`replay_case.py` copies the database to a temporary directory, the
    board renders what it holds, and migrations rewrite rows wholesale. A
    credential in there is a credential that travels."""
    import inspect

    import friday.store.db as store_module

    assert "refresh_token" not in inspect.getsource(store_module)


# --- exchanging it ----------------------------------------------------------


async def test_the_grant_is_a_refresh_and_carries_no_password(tmp_path):
    endpoint = FakeToken()

    access = await tokens(tmp_path, endpoint).token()

    assert access == "a1"
    (asked,) = endpoint.posts
    assert asked["grant_type"] == "refresh_token"
    assert asked["refresh_token"] == "r1"
    assert "password" not in asked and "code" not in asked


async def test_with_nobody_signed_in_it_says_which_command_fixes_it(tmp_path):
    """A 401 three layers down reads as the server being broken."""
    with pytest.raises(NotAuthorised, match="authorize.py"):
        await tokens(tmp_path, FakeToken(), refresh="").token()


async def test_a_rotated_refresh_token_is_kept(tmp_path):
    """Keycloak hands back a new one each exchange and the old stops working.
    A store that keeps the first works until the first refresh, then locks
    the operator out with no obvious cause."""
    endpoint = FakeToken({"access_token": "a1", "expires_in": 300,
                          "refresh_token": "r2"})
    auth = tokens(tmp_path, endpoint)

    await auth.token()

    assert auth.store.read() == "r2"


async def test_a_refused_refresh_token_is_forgotten_and_named(tmp_path):
    """Expired, revoked, or rotated out by another process. Saying so once is
    better than every later call retrying a token that will never work."""
    endpoint = FakeToken({"error": "invalid_grant"}, status=400)
    auth = tokens(tmp_path, endpoint)

    with pytest.raises(NotAuthorised, match="invalid_grant"):
        await auth.token()

    assert auth.store.read() == "", "not retried for the rest of the day"


async def test_an_access_token_still_good_is_not_exchanged_again(tmp_path):
    endpoint = FakeToken()
    auth = tokens(tmp_path, endpoint)

    await auth.token()
    await auth.token()

    assert len(endpoint.posts) == 1


async def test_a_burst_with_no_token_yet_makes_one_exchange(tmp_path):
    """Ten nodes starting together must not mint ten tokens."""
    endpoint = FakeToken()
    auth = tokens(tmp_path, endpoint)

    await asyncio.gather(*(auth.token() for _ in range(10)))

    assert len(endpoint.posts) == 1


async def test_a_token_about_to_lapse_is_replaced_before_it_does(tmp_path):
    endpoint = FakeToken(
        {"access_token": "short", "expires_in": 5},
        {"access_token": "next", "expires_in": 300},
    )
    auth = tokens(tmp_path, endpoint)

    assert (await auth.token(), await auth.token()) == ("short", "next")


async def test_an_answer_with_no_access_token_says_so_without_quoting_it(tmp_path):
    """A token endpoint's error body is the one response here most likely to
    carry a credential back."""
    endpoint = FakeToken({"id_token": "x", "secret_echo": "r1"})

    with pytest.raises(NotAuthorised) as refused:
        await tokens(tmp_path, endpoint).token()

    assert "access_token" in str(refused.value)
    assert "r1" not in str(refused.value)


def test_neither_secret_nor_token_is_in_its_repr(tmp_path):
    auth = tokens(tmp_path, FakeToken(), client_secret="s3cret")

    assert repr(auth) == "SsoTokens(store='devops.json')"
    assert "s3cret" not in repr(auth)


# --- what the sign-in discovered, kept beside the token ---------------------


def test_the_store_keeps_where_to_exchange_it_and_as_whom(tmp_path):
    """Both are *discovered* rather than chosen — the server advertises its
    token endpoint, and the client id comes back from registering against
    it. Config that restated either would go stale silently."""
    store = TokenStore(tmp_path / "t.json")

    store.write("r1", token_url="https://x/token", client_id="c1")

    assert store.settings() == {"token_url": "https://x/token", "client_id": "c1"}


def test_a_rotation_does_not_wipe_what_the_sign_in_wrote(tmp_path):
    """Every refresh writes a rotated token through the same call. A write
    that replaced the file would work once and then lock the operator out
    with a file that no longer says where to go or who to be."""
    store = TokenStore(tmp_path / "t.json")
    store.write("r1", token_url="https://x/token", client_id="c1")

    store.write("r2")

    assert store.read() == "r2"
    assert store.settings()["client_id"] == "c1"


async def test_the_exchange_uses_what_the_sign_in_discovered(tmp_path):
    endpoint = FakeToken()
    store = TokenStore(tmp_path / "devops.json")
    store.write("r1", token_url="https://discovered/token", client_id="auto-1")
    auth = SsoTokens(store=store, client=endpoint)

    await auth.token()

    (asked,) = endpoint.posts
    assert asked["client_id"] == "auto-1"


async def test_with_nothing_discovered_yet_it_says_to_sign_in(tmp_path):
    """A file with a token but no endpoint is a half-written file; so is no
    file at all. Either way the fix is the same command."""
    store = TokenStore(tmp_path / "devops.json")
    store.write("r1")

    with pytest.raises(NotAuthorised, match="authorize.py"):
        await SsoTokens(store=store, client=FakeToken()).token()


def test_an_empty_auth_block_survives_being_read_out_of_the_file(tmp_path):
    """`auth: {}` and no `auth:` at all are different answers — the first
    says "signs in, and everything about how is discovered", the second says
    "needs nothing". A loader that defaulted the key to `{}` would make them
    the same, and every server would silently become the second.

    Through the file rather than through the dataclass, because the loader
    is where they would be collapsed."""
    from friday.kernel.config import load_config

    path = tmp_path / "config.yaml"
    path.write_text(
        'mcp_servers:\n'
        '  signed:\n'
        '    url: "https://x/mcp"\n'
        '    auth: {}\n'
        '  plain:\n'
        '    url: "https://y/mcp"\n'
    )

    found = {s.name: s.auth for s in load_config(path).mcp_servers}

    assert found["signed"] == {}
    assert found["plain"] is None


def test_a_server_with_no_auth_key_is_not_signed_in_to(tmp_path):
    from friday.kernel.harness.mcp import _auth
    from friday.kernel.config import MCPServerConfig

    assert _auth(MCPServerConfig(name="plain", url="https://x/mcp")) is None
    assert _auth(MCPServerConfig(name="signed", url="https://x/mcp", auth={})) is not None


def test_nothing_in_the_auth_block_is_required(tmp_path):
    """What is missing at boot is the sign-in, never the configuration to go
    and do it — the sign-in writes the endpoint and the client id itself."""
    from friday.kernel.harness.mcp import _auth
    from friday.kernel.config import MCPServerConfig

    built = _auth(MCPServerConfig(name="devops-generic", url="https://x/mcp", auth={}))

    assert built.token_url == "" and built.client_id == ""
    assert built.store.path.name == "devops-generic.json"


# --- the sign-in discovers rather than being told ---------------------------


def test_discovery_asks_the_server_s_origin_not_its_mcp_path():
    """RFC 8414 puts the document at the origin, and the url in `config.yaml`
    points at `/mcp`. Asking `<url>/.well-known/...` 404s."""
    import authorize

    asked: list[str] = []

    class Answer:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"token_endpoint": "https://h/token"}

    def fake_get(url, **_):
        asked.append(url)
        return Answer()

    original, authorize.httpx2.get = authorize.httpx2.get, fake_get
    try:
        authorize.discover("https://h/mcp")
    finally:
        authorize.httpx2.get = original

    assert asked == ["https://h/.well-known/oauth-authorization-server"]


def test_registration_asks_for_a_public_client_and_this_redirect():
    """`token_endpoint_auth_method: none` is a public client: a secret on the
    operator's machine would sit in a file beside the token it protects and
    protect nothing. PKCE is what binds the code to this process."""
    import authorize

    sent: dict = {}

    class Answer:
        status_code = 201

        def json(self):
            return {"client_id": "auto-1"}

    def fake_post(url, json=None, **_):
        sent.update(json or {})
        return Answer()

    original, authorize.httpx2.post = authorize.httpx2.post, fake_post
    try:
        got = authorize.register("https://h/register", "http://127.0.0.1:5/callback")
    finally:
        authorize.httpx2.post = original

    assert got == "auto-1"
    assert sent["token_endpoint_auth_method"] == "none"
    assert sent["redirect_uris"] == ["http://127.0.0.1:5/callback"]
    assert "client_secret" not in sent


# --- what reaches the server ------------------------------------------------


class Request:
    def __init__(self):
        self.headers: dict[str, str] = {}


async def test_the_header_is_decided_per_request(tmp_path):
    """A header set when the server object is built outlives the token in
    it. The SDK's params take an auth handler for exactly this."""
    auth = tokens(tmp_path, FakeToken())

    flow = auth.async_auth_flow(Request())
    sent = await flow.asend(None)
    with pytest.raises(StopAsyncIteration):
        await flow.asend(FakeAnswer({}, 200))

    assert sent.headers["Authorization"] == "Bearer a1"


async def test_a_401_is_retried_once_with_a_fresh_token(tmp_path):
    """One can lapse between the check and the server reading it, and clocks
    differ across a network. A second 401 is the server saying no."""
    endpoint = FakeToken(
        {"access_token": "stale", "expires_in": 300},
        {"access_token": "fresh", "expires_in": 300},
    )
    auth = tokens(tmp_path, endpoint)

    # The value at each yield, not the object: httpx hands the *same* request
    # back to be retried, so reading `.headers` afterwards reads the retry's
    # header twice.
    flow = auth.async_auth_flow(Request())
    sent = [(await flow.asend(None)).headers["Authorization"]]
    sent.append((await flow.asend(FakeAnswer({}, 401))).headers["Authorization"])

    assert sent == ["Bearer stale", "Bearer fresh"]
    assert len(endpoint.posts) == 2
    with pytest.raises(StopAsyncIteration):
        await flow.asend(FakeAnswer({}, 200))


# --- the sign-in itself -----------------------------------------------------


def test_the_verifier_and_its_challenge_carry_no_padding():
    """Keycloak compares the challenge it was given against one it computes.
    An `=` in one and not the other is a mismatch reported as a bad code."""
    import base64
    import hashlib

    from authorize import pkce

    verifier, challenge = pkce()

    assert "=" not in verifier and "=" not in challenge
    assert challenge == base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()


def test_two_sign_ins_do_not_share_a_verifier():
    from authorize import pkce

    assert pkce()[0] != pkce()[0]
