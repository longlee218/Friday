"""Who each agent is told it is — and, for two of them, that it is told nothing.

Was `test_persona.py`, which tested a loader for `PERSONA.md`: one file, split
by heading, a section handed to each agent by its family. Ticket 16 removed
that. Each agent's prompt now carries its own voice in its own module, so
what is left to check is not how a file parses but **what each agent ends up
being told** — which is what the loader was only ever a means to.

Three facts survive the move, and each cost something to learn:

- the responder and the graph's composing node speak in the operator's voice,
  because a person reads what they write, under that name;
- every other graph node does not, and is told so explicitly — an agent
  carrying the voice writes `sản xuất` where the code wants `production`;
- triage and the extractors are told **nothing** about voice. Their output is
  a tool call and a number, or values copied out of a message. A persona there
  is paid for on the highest-volume calls in the system to change nothing, and
  once was: 79% of triage's prompt was instructions for writing replies it
  never writes.
"""

from __future__ import annotations

import os
from pathlib import Path

from friday.config import load_config

REPO = Path(__file__).resolve().parents[1]


def _shipped():
    for key in ("TRIAGE_API_KEY", "RESPONDER_API_KEY"):
        os.environ.setdefault(key, "test-key")
    return load_config(REPO / "config.yaml")


def _every_node_built():
    from types import SimpleNamespace

    from friday.config import AgentConfig
    from friday.dag.api_issue.graph import NODES, build_agents

    config = SimpleNamespace(
        agents={
            spec.block: AgentConfig(
                name=spec.block, api_key="k", base_url="http://x/v1", model="m"
            )
            for spec in NODES.values()
        }
    )
    return build_agents(config)




def test_the_responder_speaks_in_that_voice_too():
    from friday.responder import Responder

    responder = Responder.build(_shipped())

    assert "How Long writes" in responder._run.instructions


async def test_triage_and_the_extractors_are_told_nothing_about_voice():
    """Their output is a tool name and a number, or values copied out of a
    message. There is no sentence either writes that a voice could improve,
    and every word would be paid for on the highest-volume calls in the
    system to change nothing."""
    from friday.extraction import EXTRACTS, register_extractors, registered
    from friday.triage.runner import TriageRunner

    config = _shipped()

    class NoDb:
        async def confirmed_classifications(self, **kw):
            return []

    triage = await TriageRunner.build(config, db=NoDb())
    register_extractors(config)

    assert "Long" not in triage._triage._run.instructions
    for task_type in EXTRACTS:
        assert "Long" not in registered()[task_type]._harness.instructions


def test_one_familys_prompt_does_not_wrap_another_familys_rules():
    """One bundle used to wrap every prompt in the same sentence, and triage
    was told how to resolve a precedence conflict between three sections it is
    never passed."""
    from friday.responder import Responder

    responder = Responder.build(_shipped())

    assert "You are an agent in the friday system" not in responder._run.instructions
    assert "Section precedence" not in responder._run.instructions
    # The precedence rule lives in the one family that has channel sections.
    assert "channel_overrides" in responder._run.instructions
