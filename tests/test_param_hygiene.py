"""Ticket 04 slice 3 — parameter hygiene.

One problem, found by probing the live provider: the model emits the *string*
"null" often enough to matter, and a workflow that believes it has a
correlationId never asks for the one it needs.

There was a second claim here — that correlationId, environment and curl are
mechanically detectable, so the model should not be their sole source. Three
regex finders and twelve assertions guarded it. Nothing ever called them, and
the premise expired: they were written when *triage* extracted with a model
that could not, and extraction is now its own step with its own model. Two
producers for one field is the shape that needed `_merge`.
"""

from __future__ import annotations

import pytest

from friday.kernel.text.param_hygiene import clean


@pytest.mark.parametrize("value", ["null", "NULL", "none", "None", "N/A", "n/a", "", "   "])
def test_absent_looking_values_become_absent(value):
    """The model sometimes says "null" rather than sending null."""
    assert clean(value) is None


@pytest.mark.parametrize("value", ["production", "7f3a91c2", "  staging  "])
def test_real_values_survive_and_are_trimmed(value):
    assert clean(value) == value.strip()


def test_none_stays_none():
    assert clean(None) is None
