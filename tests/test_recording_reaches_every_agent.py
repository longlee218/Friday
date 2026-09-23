"""Every agent records and is bounded, and the chain that makes it so is checked.

D1 says the recording sink is handed over at construction so that no caller
can forget it. That moved the forgetting one layer down: each builder now
takes `record=` and passes it on by hand, and a review found that deleting any
one of those four forwarding lines left the whole suite green. The `calls=`
list's own failure mode, reproduced inside its fix.

So this file drives each builder through its public entry point with a
sentinel sink, and asserts the `Harness` it ends up constructing was given
that exact object. Identity, not presence: `record=None` everywhere is the
bug this board exists to fix, and a test that only checks a keyword is
spelled would pass through it.

The list of builders is written out. Deriving it — from the config, from the
module names — is how the prompt-families test stopped checking anything when
a module moved.
"""

from __future__ import annotations

import pytest
from friday.agent import harness as harness_module
from friday.config import AgentConfig

CONFIG = AgentConfig(
    name="an-agent", api_key="k", base_url="https://example.invalid/v1",
    model="test-model", max_turns=1, settings={}, options={},
)


@pytest.fixture
def spy(monkeypatch):
    """Every `Harness` built during a test, and the sink each was given.

    A subclass rather than a stub: it runs the real constructor, so a builder
    that assembles its agent wrongly still fails here rather than being
    quietly accommodated.

    Patched per module, because each imports the name directly — and the list
    of modules patched *is* the enumeration this file is about. The source
    module is patched too: `friday.extraction.register` imports `Harness`
    *inside* the function, so it resolves at call time and a per-module patch
    never reaches it. `Spy`'s base is bound before any of this, so patching
    the name it inherits from does not make it its own parent.
    """
    given: list = []

    class Spy(harness_module.Harness):
        def __init__(self, **kw):
            given.append((kw.get("record"), kw.get("spent")))
            super().__init__(**kw)

        async def run(self, *a, **kw):
            """Inert. Every construction here is real; no execution is — the
            summariser builds its harness inside the call that uses it, so
            reaching that construction means letting the call happen."""
            return None

    for module in (
        "friday.agent.harness",
        "friday.triage",
        "friday.extraction",
        "friday.responder",
        "friday.memory.channel_context",
    ):
        monkeypatch.setattr(f"{module}.Harness", Spy)
    return given


SINK = object()
#: The other half. A ceiling that reaches three agents out of four is not a
#: ceiling — it is a ceiling and a hole, and the hole is silent.
LEDGER = object()


async def test_triage_records(spy, db):
    from friday.triage.runner import TriageRunner

    await TriageRunner.build(_config(triage=CONFIG), db=db, record=SINK, spent=LEDGER)

    assert spy == [(SINK, LEDGER)]


async def test_every_extractor_records(spy):
    """One block now, but still one agent per task type — the sink has to
    reach each of them. Collapsing the *configuration* must not collapse the
    agents: each type keeps its own prompt and its own schema."""
    from friday.dag import registry
    from friday.extraction import register_extractors

    register_extractors(_config(extractor=CONFIG), record=SINK, spent=LEDGER)

    assert spy == [(SINK, LEDGER)] * len(registry.decision_params()), "one per task type"


async def test_the_responder_records(spy):
    from friday.responder import Responder

    assert Responder.build(_config(responder=CONFIG), record=SINK, spent=LEDGER) is not None
    assert spy == [(SINK, LEDGER)]


async def test_the_summariser_records(spy):
    """Two forwarding lines rather than one — `build` to the constructor, and
    the constructor's field to the `Harness` built later — so this goes in at
    `build`, which is the door the composition root uses. Reaching the second
    line means letting the summary actually run; `Spy.run` is what makes that
    cost nothing.

    An earlier version of this test constructed `ContextRebuilder` directly
    and so covered only the second line: deleting `build`'s forwarding left it
    green. Both are covered now.
    """
    from friday.memory.channel_context import ContextRebuilder

    rebuilder = ContextRebuilder.build(
        _config(summary=CONFIG),
        db=_LoudChannel(),
        record=SINK,
        spent=LEDGER,
    )

    await rebuilder.rebuild_all()

    assert spy == [(SINK, LEDGER)]


# --- the least that lets each builder run ------------------------------------


def _config(**agents):
    from types import SimpleNamespace

    return SimpleNamespace(
        agents=agents,
        triage_examples=[],
        sensitive_words=(),
        ingest=SimpleNamespace(turn_seconds=0, watched_channels=frozenset({"c1"})),
        workflows=SimpleNamespace(use_responder=True),
        context=SimpleNamespace(summary_max_chars=6000),
    )


class _LoudChannel:
    """A room never summarised before, so it is worth summarising — which
    is what makes the harness get built at all."""

    async def room_summary(self, channel_id):
        return None

    async def memory_add(self, state, text, **kw):
        self.summarised = kw.get("data")

    async def relevant_messages_in_channel(self, provider, channel_id):
        from tests.conftest import make_event

        return [make_event(text="anything at all")]

