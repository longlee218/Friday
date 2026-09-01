"""Ticket 04 slice 3 — parameter hygiene.

Two problems, both found by probing the live provider: the model emits the
*string* "null" often enough to matter, and the fields that matter most —
correlationId, environment, curl — are mechanically detectable, so making the
model the sole source of them was never the strongest option available.
"""

from __future__ import annotations

import pytest

from friday.param_hygiene import clean, find_correlation_id, find_curl, find_environment
from friday.models import ApiIssueParams


@pytest.mark.parametrize("value", ["null", "NULL", "none", "None", "N/A", "n/a", "", "   "])
def test_absent_looking_values_become_absent(value):
    """The model sometimes says "null" rather than sending null."""
    assert clean(value) is None


@pytest.mark.parametrize("value", ["production", "7f3a91c2", "  staging  "])
def test_real_values_survive_and_are_trimmed(value):
    assert clean(value) == value.strip()


@pytest.mark.parametrize(
    "text,expected",
    [
        ("the api fails, correlationId 7f3a91c2", "7f3a91c2"),
        ("correlation-id: abc-123-def", "abc-123-def"),
        ("traceId=xyz789", "xyz789"),
        ("requestId is 55aa-77bb", "55aa-77bb"),
        ("x-correlation-id: deadbeef", "deadbeef"),
        ("api sai roi, correlationId abc-123-def", "abc-123-def"),
        ("no identifier here at all", None),
    ],
)
def test_correlation_ids_are_found_by_their_label(text, expected):
    assert find_correlation_id(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("returns 500 on production", "production"),
        ("failing in staging since noon", "staging"),
        ("moi truong staging bi loi", "staging"),
        ("broken on prod", "prod"),
        ("the developer said it was fine", None),
        ("no environment mentioned", None),
    ],
)
def test_environments_are_found_as_whole_words(text, expected):
    assert find_environment(text) == expected


def test_a_curl_command_is_found():
    text = 'here you go:\n curl -X POST https://api/x -H "a: b"'
    assert find_curl(text).startswith("curl -X POST")


def test_no_curl_means_none():
    assert find_curl("the api is wrong") is None
