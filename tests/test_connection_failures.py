"""Ticket 03 — a dead credential must not look like a quiet channel.

The library retries everything forever, so classification is ours to do: a
process stuck retrying a rejected credential is alive, logging 'reconnecting',
and receiving nothing.
"""

from __future__ import annotations

import discord_self

from friday.providers.discord import is_credential_rejected


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
