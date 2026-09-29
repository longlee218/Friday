"""The triage answer schema is built from the registered action names at boot.

`Decided` (the value type) is a plain `type: str`; the *closed set* the model is
held to is built by `make_decided(names)` from the registered actions (board
`domains-plug-in`, ticket 02 §2). These pin the factory: it closes the set, it
carries no per-label text (label meaning lives in the prompt), and it is
memoised so the class has a stable identity.
"""

from __future__ import annotations

from typing import get_args, get_type_hints

from friday.kernel.domain.triage import Decided, make_decided

NAMES = ("backend.trace_problem", "ops.request_permission", "backend.answer_question")


def test_decided_is_a_plain_value_type():
    """The value type carries a bare string — no static Literal, so it needs no
    catalog at import."""
    d = Decided(type="backend.trace_problem", confidence=0.9)
    assert d.type == "backend.trace_problem" and d.confidence == 0.9
    assert get_type_hints(Decided)["type"] is str


def test_make_decided_closes_the_type_to_the_names_plus_skip():
    cls = make_decided(NAMES)
    allowed = get_args(get_type_hints(cls)["type"])
    assert allowed == (*sorted(NAMES), "skip")


def test_make_decided_describes_no_label():
    cls = make_decided(NAMES)
    doc = next(
        f for f in cls.__dataclass_fields__.values() if f.name == "type"
    ).metadata["doc"]
    assert "labels above" in doc
    for name in NAMES:
        assert name not in doc


def test_make_decided_is_memoised_for_a_stable_identity():
    """Same set -> the same class object, so isinstance/== hold across callers."""
    assert make_decided(NAMES) is make_decided(list(reversed(NAMES)))
