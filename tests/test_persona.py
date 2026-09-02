"""Who the agents are, and which of them is told what.

Two families get a section of `PERSONA.md` — the ones that write to a person,
and the ones that are a step inside an investigation. Triage and the
extractors get nothing. The family is decided where the agent is built, not
in configuration: the last time it was a knob it went stale the first time an
agent's job changed.
"""

from __future__ import annotations

import os
from pathlib import Path

from friday.agent.persona import Family, load
from friday.config import load_config

REPO = Path(__file__).resolve().parents[1]

SAMPLE = """# PERSONA

Prose for whoever edits this.

## Responder

You are Long Lee's assistant.

### How Long writes

Short. Usually one or two sentences.

## Node

You do not invent.

## Who gets what

| a table |
"""


def _write(tmp_path, body=SAMPLE):
    path = tmp_path / "PERSONA.md"
    path.write_text(body, encoding="utf-8")
    return path


# --- splitting -------------------------------------------------------------


def test_each_family_reads_its_own_section(tmp_path):
    persona = load(_write(tmp_path))

    assert "Long Lee's assistant" in persona.render(Family.RESPONDER)
    assert "one or two sentences" in persona.render(Family.RESPONDER)
    assert persona.render(Family.NODE) == "You do not invent."


def test_a_subheading_stays_inside_its_family(tmp_path):
    """`### How Long writes` is part of the responder, not a section of its
    own."""
    rendered = load(_write(tmp_path)).render(Family.RESPONDER)

    assert "How Long writes" in rendered


def test_headings_no_family_names_are_left_out(tmp_path):
    """The file is also prose for a person; the table explaining it is not
    something to send a model."""
    persona = load(_write(tmp_path))

    for family in Family:
        assert "| a table |" not in persona.render(family)
        assert "Prose for whoever" not in persona.render(family)


# --- when the file is not there ---------------------------------------------


def test_a_missing_file_is_not_a_startup_failure(tmp_path):
    persona = load(tmp_path / "nothing.md")

    assert persona.render(Family.RESPONDER) == ""
    assert len(persona) == 0


def test_a_directory_named_like_the_file_is_reported_not_fatal(tmp_path):
    (tmp_path / "PERSONA.md").mkdir()

    assert load(tmp_path / "PERSONA.md").render(Family.NODE) == ""


def test_a_byte_order_mark_does_not_hide_the_first_section(tmp_path):
    path = tmp_path / "PERSONA.md"
    path.write_text(SAMPLE, encoding="utf-8-sig")

    assert "Long Lee" in load(path).render(Family.RESPONDER)


# --- who gets what, in the shipped build ------------------------------------


def _shipped():
    for key in ("TRIAGE_API_KEY", "RESPONDER_API_KEY"):
        os.environ.setdefault(key, "test-key")
    return load_config(REPO / "config.yaml")


def test_the_shipped_file_has_a_section_for_every_family():
    """A heading renamed in `PERSONA.md` and not here drops that family's
    persona silently — the file still parses, the agents still run."""
    persona = load(REPO / "PERSONA.md")

    for family in Family:
        assert persona.render(family), f"no section for {family}"


def test_the_responder_carries_the_voice_and_a_node_does_not():
    persona = _shipped().persona

    assert "How Long writes" in persona.render(Family.RESPONDER)
    assert "How Long writes" not in persona.render(Family.NODE)
    assert "do not invent" in persona.render(Family.NODE).lower()


async def test_triage_and_the_extractors_carry_nothing():
    """Their output is a tool name and a number, or values copied out of a
    message. There is no sentence either writes that a persona could improve,
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


def test_the_responder_and_the_composing_node_are_the_same_family():
    """Both write a message a person reads under the operator's name. The
    other graph nodes are steps and get the node section."""
    from types import SimpleNamespace

    from friday.config import AgentConfig
    from friday.dag.router import _API_ISSUE_AGENTS, agents_for_api_issue

    config = _shipped()
    every_node = SimpleNamespace(
        agents={
            block: AgentConfig(name=block, api_key="k", base_url="http://x/v1", model="m")
            for block in _API_ISSUE_AGENTS.values()
        },
        persona=config.persona,
    )
    built = agents_for_api_issue(every_node)

    assert "How Long writes" in built["compose_reply"].instructions
    assert "How Long writes" not in built["analyze_stack"].instructions
    assert "do not invent" in built["analyze_stack"].instructions.lower()


def test_no_family_text_appears_in_another_familys_prompt():
    """The rule this ticket exists for. One bundle used to wrap every prompt
    in the same sentence, and triage was told how to resolve a precedence
    conflict between three sections it is never passed."""
    from friday.responder import Responder

    config = _shipped()
    responder = Responder.build(config)

    assert "You are an agent in the friday system" not in responder._run.instructions
    assert "Section precedence" not in responder._run.instructions
    # The precedence rule lives in the one family that has channel sections.
    assert "channel_overrides" in responder._run.instructions
