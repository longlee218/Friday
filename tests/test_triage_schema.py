"""Ticket 11 — the triage answer schema is built from the registry at boot.

`Decided` (the value type) is a plain `type: str` now; the *closed set* the model
is held to is built at boot by `make_decided(params_by_type)` from the task-type
registry, not from a static `Literal[PARAMS]` at import. These pin the factory:
it renders the enum + per-label docs the classifier reads, it is memoised so the
class has a stable identity, and `Decided` itself stays a static value type.
"""

from __future__ import annotations

from typing import Literal, get_args, get_type_hints

import pytest

from friday.domain.actions import Decided, make_decided
from friday.domain.models import (
    AccessRequestParams,
    ApiIssueParams,
    DocQuestionParams,
)

CATALOG = {
    "api_issue": ApiIssueParams,
    "access_request": AccessRequestParams,
    "doc_question": DocQuestionParams,
}


def test_decided_is_a_plain_value_type():
    """The value type carries a bare string — no static Literal, so it needs no
    task-type catalog at import."""
    d = Decided(type="api_issue", confidence=0.9)
    assert d.type == "api_issue" and d.confidence == 0.9
    assert get_type_hints(Decided)["type"] is str


def test_make_decided_closes_the_type_to_the_registry_plus_skip():
    cls = make_decided(CATALOG)
    allowed = set(get_args(get_type_hints(cls)["type"]))
    assert allowed == {"api_issue", "access_request", "doc_question", "skip"}


def test_make_decided_carries_each_types_own_description():
    cls = make_decided(CATALOG)
    doc = next(f for f in cls.__dataclass_fields__.values() if f.name == "type").metadata["doc"]
    # Each label's line comes from its Params class docstring.
    for name in CATALOG:
        assert f"    {name}:" in doc
    assert "    skip:" in doc


def test_make_decided_is_memoised_for_a_stable_identity():
    """Same set -> the same class object, so isinstance/== hold across callers."""
    assert make_decided(CATALOG) is make_decided(dict(CATALOG))


def test_make_decided_refuses_a_type_with_no_description():
    class Undocumented:
        pass

    with pytest.raises(ValueError, match="no docstring"):
        make_decided({"mystery": Undocumented})
