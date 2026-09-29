"""Ticket 03 — a dead credential must not look like a quiet channel.

The library retries everything forever, so classification is ours to do: a
process stuck retrying a rejected credential is alive, logging 'reconnecting',
and receiving nothing.
"""

from __future__ import annotations

import asyncio

import discord_self
import pytest
from conftest import captured

from friday.kernel.inbox import Inbox
from friday.kernel.providers.discord.user import is_credential_rejected


def test_a_rejected_login_is_fatal():
    assert is_credential_rejected(discord_self.LoginFailure("bad token"))


def test_close_code_4004_is_fatal():
    assert is_credential_rejected(discord_self.ConnectionClosed(code=4004))


def test_an_ordinary_close_is_not_fatal():
    """1006 is an abnormal closure — retrying is the right response."""
    assert not is_credential_rejected(discord_self.ConnectionClosed(code=1006))


def test_a_network_error_is_not_fatal():
    assert not is_credential_rejected(OSError("connection reset"))


def test_a_close_with_no_code_is_not_fatal():
    assert not is_credential_rejected(discord_self.ConnectionClosed())


async def test_a_failure_inside_the_inbox_surfaces_instead_of_hanging(db, config):
    """`stream()` waits on background workers that are the only things feeding
    it. One dying quietly stalls ingestion for the life of the process, and a
    stalled agent is indistinguishable from a quiet day."""

    class Exploding:
        name = "fake"
        reconnected = asyncio.Event()

        async def stream(self):
            raise RuntimeError("the gateway fell over")
            yield  # pragma: no cover - makes this an async generator

        async def history(self, channel_id, *, after, since):
            return
            yield  # pragma: no cover

        async def recent(self, conversation, *, before, limit):
            return
            yield  # pragma: no cover

    inbox = Inbox(provider=Exploding(), db=db, config=config)
    with pytest.raises(RuntimeError, match="the gateway fell over"):
        await asyncio.wait_for(captured(inbox), timeout=5)
