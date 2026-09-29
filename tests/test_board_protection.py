"""Ticket 02 — the board accepts a write only from the operator's own page.

The board answers on loopback and shows every captured message and model
prompt, and it now *writes* the operator's own memory rows. Reads stay open on
loopback; a write has to prove it came from the operator's own tab and not from
a page in another tab or a site that rebound DNS to this port. That proof is
three things checked in kernel code, not trusted from the request: a loopback
`Host`, an `Origin` that is this board's own (or a configured dev origin), and
the session's CSRF token, minted at startup and handed to the page in a
`SameSite=Strict` cookie.

These are behaviour tests: they drive the real routes and assert what the guard
lets through and what it refuses, not which function ran.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from friday.kernel.ops.api import build_api

A_FACT = {"kind": "fact", "text": "the office is in district one"}


@pytest.fixture
def raw(db):
    """A client that speaks from loopback but adds nothing of its own — every
    header a write needs is set by the test, so each guard can be isolated."""
    return TestClient(
        build_api(db=db, provider_status=lambda: "connected"),
        base_url="http://127.0.0.1",
    )


def _token(client: TestClient) -> str:
    """The CSRF token the page would read from the cookie a GET sets."""
    client.get("/api/board")
    return client.cookies["friday_csrf"]


def test_a_get_hands_the_page_a_samesite_strict_csrf_cookie(raw):
    got = raw.get("/api/board")
    setc = got.headers.get("set-cookie", "")
    assert "friday_csrf=" in setc
    assert "samesite=strict" in setc.lower()


def test_a_read_needs_no_token(raw):
    assert raw.get("/api/board").status_code == 200


def test_the_session_secret_is_minted_per_process(db):
    """ "Minted at startup" means each process gets its own random secret, not a
    fixed value baked into the code — two separately built apps hand out two
    different tokens."""
    from friday.kernel.ops.api import build_api

    one = TestClient(
        build_api(db=db, provider_status=lambda: "x"), base_url="http://127.0.0.1"
    )
    two = TestClient(
        build_api(db=db, provider_status=lambda: "x"), base_url="http://127.0.0.1"
    )
    one.get("/api/board")
    two.get("/api/board")
    assert one.cookies["friday_csrf"] != two.cookies["friday_csrf"]


def test_a_write_without_the_token_is_refused(raw):
    refused = raw.post("/api/channels/c1/memories", json=A_FACT)
    assert refused.status_code == 403


def test_a_write_with_the_token_from_the_cookie_succeeds(raw):
    token = _token(raw)
    made = raw.post(
        "/api/channels/c1/memories", json=A_FACT, headers={"x-csrf-token": token}
    )
    assert made.status_code == 201, made.text


def test_a_wrong_token_is_refused(raw):
    _token(raw)
    refused = raw.post(
        "/api/channels/c1/memories",
        json=A_FACT,
        headers={"x-csrf-token": "not-the-secret"},
    )
    assert refused.status_code == 403


def test_a_write_from_a_foreign_host_is_refused(raw):
    """A page that rebound DNS to this port sends a foreign `Host`. Even with a
    valid token the write is refused, so the Host guard stands on its own."""
    token = _token(raw)
    refused = raw.post(
        "/api/channels/c1/memories",
        json=A_FACT,
        headers={"x-csrf-token": token, "host": "evil.example"},
    )
    assert refused.status_code == 403


def test_a_write_from_a_foreign_origin_is_refused(raw):
    token = _token(raw)
    refused = raw.post(
        "/api/channels/c1/memories",
        json=A_FACT,
        headers={"x-csrf-token": token, "origin": "http://evil.example"},
    )
    assert refused.status_code == 403


def test_a_write_from_the_boards_own_origin_is_allowed(raw):
    token = _token(raw)
    made = raw.post(
        "/api/channels/c1/memories",
        json=A_FACT,
        headers={"x-csrf-token": token, "origin": "http://127.0.0.1"},
    )
    assert made.status_code == 201, made.text


def test_a_configured_dev_origin_may_write(db):
    """The dev frontend runs cross-origin from Vite; a write from it carries a
    foreign Origin that `board_origins` names, and must be let through."""
    client = TestClient(
        build_api(
            db=db,
            provider_status=lambda: "connected",
            origins=["http://localhost:5173"],
        ),
        base_url="http://127.0.0.1",
    )
    token = _token(client)
    made = client.post(
        "/api/channels/c1/memories",
        json=A_FACT,
        headers={"x-csrf-token": token, "origin": "http://localhost:5173"},
    )
    assert made.status_code == 201, made.text
