"""The credential a server wants, obtained rather than pasted.

The operator's call, 2026-09-21: every MCP server here is behind Keycloak,
and the alternative to this is a token copied into `.env` by hand and copied
again when it lapses — which means the system is down between the lapse and
the noticing.

The objection this reverses was about the wrong credential. Friday borrowing
the *operator's* identity is what "nothing here mints a credential" forbade;
presenting its **own** Keycloak client — its own roles, revocable on its own,
logged as Friday rather than as a person — is narrower than a pasted token,
not wider.
"""

from __future__ import annotations

import asyncio

import pytest

from friday.agent.auth import ClientCredentials


class FakeToken:
    """A token endpoint that counts what it was asked for."""

    def __init__(self, *answers):
        self.answers = list(answers) or [{"access_token": "t1", "expires_in": 300}]
        self.posts: list[dict] = []

    async def post(self, url, data=None):
        self.posts.append(dict(data or {}))
        said = self.answers[min(len(self.posts) - 1, len(self.answers) - 1)]
        return FakeAnswer(said)

    async def aclose(self):  # pragma: no cover - the fake is not closed
        raise AssertionError("a client passed in is not this module's to close")


class FakeAnswer:
    def __init__(self, said, status_code=200):
        self._said = said
        self.status_code = status_code

    def json(self):
        return self._said

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def credentials(client, **kwargs):
    return ClientCredentials(
        token_url="https://keycloak.invalid/realms/r/protocol/openid-connect/token",
        client_id="friday",
        client_secret="s3cret",
        client=client,
        **kwargs,
    )


async def test_the_grant_is_client_credentials_and_carries_no_user():
    """No user, no browser, no refresh token: an id and a secret exchanged
    for a short access token."""
    endpoint = FakeToken()

    token = await credentials(endpoint).token()

    assert token == "t1"
    (asked,) = endpoint.posts
    assert asked["grant_type"] == "client_credentials"
    assert asked["client_id"] == "friday"
    assert "username" not in asked and "code" not in asked


async def test_a_token_still_good_is_not_fetched_again():
    endpoint = FakeToken()
    auth = credentials(endpoint)

    await auth.token()
    await auth.token()

    assert len(endpoint.posts) == 1


async def test_a_burst_with_no_token_yet_makes_one_exchange():
    """Ten nodes starting together must not mint ten tokens. Re-checked
    inside the lock, so the calls queued behind the one that fetched do not
    each fetch again."""
    endpoint = FakeToken()
    auth = credentials(endpoint)

    await asyncio.gather(*(auth.token() for _ in range(10)))

    assert len(endpoint.posts) == 1


async def test_a_token_about_to_lapse_is_replaced_before_it_does():
    """A token that expires mid-request costs a round trip and a retry; a few
    seconds of overlap costs nothing."""
    endpoint = FakeToken(
        {"access_token": "short", "expires_in": 5},
        {"access_token": "next", "expires_in": 300},
    )
    auth = credentials(endpoint)

    first = await auth.token()
    second = await auth.token()

    assert (first, second) == ("short", "next"), "5s is inside the early window"
    assert len(endpoint.posts) == 2


async def test_an_answer_with_no_access_token_says_so_without_quoting_it():
    """A token endpoint's error body is small, and it is the one response in
    this system most likely to carry a credential back."""
    endpoint = FakeToken({"error": "invalid_client", "secret_echo": "s3cret"})

    with pytest.raises(RuntimeError) as refused:
        await credentials(endpoint).token()

    assert "access_token" in str(refused.value)
    assert "s3cret" not in str(refused.value)


def test_neither_the_secret_nor_the_token_is_in_its_repr():
    """This object is reachable from a server object that ends up in more
    than one log line."""
    auth = credentials(FakeToken())

    assert "s3cret" not in repr(auth)
    assert repr(auth) == "ClientCredentials(client_id='friday')"


# --- what reaches the server ------------------------------------------------


async def test_the_header_is_decided_per_request_not_once(monkeypatch):
    """A header set when the server object is built is a header that outlives
    the token in it. The SDK's params take an auth handler for exactly this."""
    endpoint = FakeToken()
    auth = credentials(endpoint)

    class Request:
        headers: dict = {}

    first, second = Request(), Request()
    first.headers, second.headers = {}, {}
    for request in (first, second):
        flow = auth.async_auth_flow(request)
        sent = await flow.asend(None)
        with pytest.raises(StopAsyncIteration):
            await flow.asend(FakeAnswer({}, status_code=200))
        assert sent.headers["Authorization"] == "Bearer t1"


async def test_a_401_is_retried_once_with_a_fresh_token():
    """A token can lapse between the check and the server reading it, and a
    clock can differ across a network. One retry covers both; a second 401 is
    the server saying no, which another token does not fix."""
    endpoint = FakeToken(
        {"access_token": "stale", "expires_in": 300},
        {"access_token": "fresh", "expires_in": 300},
    )
    auth = credentials(endpoint)

    class Request:
        def __init__(self):
            self.headers = {}

    # The value at each yield, not the object: httpx hands the *same*
    # request back to be retried, so reading `.headers` afterwards reads the
    # retry's header twice.
    flow = auth.async_auth_flow(Request())
    sent = [(await flow.asend(None)).headers["Authorization"]]
    sent.append((await flow.asend(FakeAnswer({}, status_code=401)))
                .headers["Authorization"])

    assert sent == ["Bearer stale", "Bearer fresh"]
    assert len(endpoint.posts) == 2
    with pytest.raises(StopAsyncIteration):
        await flow.asend(FakeAnswer({}, status_code=200))
