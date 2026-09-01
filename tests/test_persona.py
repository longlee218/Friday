"""Who the agents are, and which of them is told what.

The persona is the one piece of prompt text that is shared, so it is also the
one whose mistakes are shared. Two failures matter and neither announces
itself: an agent that should carry it and does not — the replies simply stop
sounding like anyone — and an agent that carries the *voice* when it should
only carry the language rule, which corrupts a value something else validates.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from friday.config import ConfigError, load_config
from friday.agent.persona import Mode, load

REPO = Path(__file__).resolve().parents[1]


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "PERSONA.md"
    path.write_text(body, encoding="utf-8")
    return path


SAMPLE = """# PERSONA

Preamble nobody is meant to send to a model.

## Who you are

You are Long Lee's assistant.

## How Long writes

Short. Usually one or two sentences.

## Language

Anything a person reads is in Vietnamese.

## Modes

| Mode | For |
|---|---|
| full | prose |
"""


# --- assembling ------------------------------------------------------------


def test_full_takes_identity_voice_and_language(tmp_path):
    persona = load(write(tmp_path, SAMPLE))

    rendered = persona.render(Mode.FULL)

    assert "Long Lee's assistant" in rendered
    assert "one or two sentences" in rendered
    assert "in Vietnamese" in rendered


def test_language_takes_identity_and_language_but_not_the_voice(tmp_path):
    """The voice is what pushes a classifier into writing `sản xuất` where
    `production` was required. An agent filling in a validated field keeps the
    identity and the language rule and loses the part that would."""
    persona = load(write(tmp_path, SAMPLE))

    rendered = persona.render(Mode.LANGUAGE)

    assert "Long Lee's assistant" in rendered
    assert "in Vietnamese" in rendered
    assert "one or two sentences" not in rendered


def test_none_takes_nothing(tmp_path):
    """A node asked for a `path:line` and a diff has nothing to say in
    anyone's voice, and a persona in its prompt is tokens spent on every call
    to make its output worse."""
    assert load(write(tmp_path, SAMPLE)).render(Mode.NONE) == ""


def test_headings_no_mode_asks_for_are_left_out(tmp_path):
    """`## Modes` is a table explaining the file to whoever edits it. Sending
    it to a model is telling the agent about agents it is not."""
    rendered = load(write(tmp_path, SAMPLE)).render(Mode.FULL)

    assert "| Mode |" not in rendered
    assert "Preamble nobody" not in rendered


def test_the_heading_itself_is_not_sent(tmp_path):
    """A signpost for the editor. A model reading "## Who you are" above the
    text is being told the same thing twice."""
    assert "## Who you are" not in load(write(tmp_path, SAMPLE)).render(Mode.FULL)


# --- when the file is not there ---------------------------------------------


def test_a_missing_persona_file_is_not_a_startup_failure(tmp_path):
    """This is prose the operator writes. A system that will not start without
    it is worse than one that starts without a voice."""
    persona = load(tmp_path / "nothing-here.md")

    assert persona.render(Mode.FULL) == ""
    assert len(persona) == 0


def test_something_unreadable_is_reported_rather_than_crashing(tmp_path):
    """A directory named `PERSONA.md` raises `IsADirectoryError`, which is an
    `OSError` and not a `FileNotFoundError`."""
    (tmp_path / "PERSONA.md").mkdir()

    assert load(tmp_path / "PERSONA.md").render(Mode.FULL) == ""


def test_a_byte_order_mark_does_not_hide_the_first_section(tmp_path):
    path = tmp_path / "PERSONA.md"
    path.write_text(SAMPLE, encoding="utf-8-sig")

    assert "Long Lee's assistant" in load(path).render(Mode.FULL)


# --- what configuration does with it ----------------------------------------


def test_an_unknown_mode_is_refused_when_the_file_is_read(tmp_path):
    """A typo silently giving an agent no persona is the one failure that
    leaves no trace anywhere: nothing errors, the replies just stop sounding
    like anyone."""
    (tmp_path / "config.yaml").write_text(
        "database_path: ./x.db\n"
        "agents:\n"
        "  triage:\n"
        "    persona: langauge\n"
        "    api_key: k\n"
        "    base_url: http://localhost/v1\n"
        "    model: m\n"
    )

    with pytest.raises(ConfigError) as refused:
        load_config(tmp_path / "config.yaml")

    assert "langauge" in str(refused.value)
    assert "full, language, none" in str(refused.value)


def test_the_persona_is_found_beside_the_config_not_beside_the_process(tmp_path):
    """The two are the same when run from the repository and are not the same
    in a container. The file that names it is the one it sits beside."""
    write(tmp_path, SAMPLE)
    (tmp_path / "config.yaml").write_text(
        "database_path: ./x.db\n"
        "agents:\n"
        "  responder:\n"
        "    api_key: k\n"
        "    base_url: http://localhost/v1\n"
        "    model: m\n"
    )

    config = load_config(tmp_path / "config.yaml")

    assert "Long Lee's assistant" in config.agents["responder"].persona


def test_full_is_the_default_for_an_agent_that_does_not_say(tmp_path):
    """The safe direction. A persona that should have been `none` costs
    tokens; one that should have been `full` sends a stranger's message under
    the operator's name."""
    write(tmp_path, SAMPLE)
    (tmp_path / "config.yaml").write_text(
        "database_path: ./x.db\n"
        "agents:\n"
        "  newcomer:\n"
        "    api_key: k\n"
        "    base_url: http://localhost/v1\n"
        "    model: m\n"
    )

    persona = load_config(tmp_path / "config.yaml").agents["newcomer"].persona

    assert "one or two sentences" in persona


def test_persona_is_not_left_in_the_step_specific_options(tmp_path):
    """`options` is whatever configuration did not recognise. A `persona` key
    surviving into it means something read it as a step-specific knob."""
    write(tmp_path, SAMPLE)
    (tmp_path / "config.yaml").write_text(
        "database_path: ./x.db\n"
        "agents:\n"
        "  triage:\n"
        "    persona: language\n"
        "    api_key: k\n"
        "    base_url: http://localhost/v1\n"
        "    model: m\n"
    )

    assert "persona" not in load_config(tmp_path / "config.yaml").agents["triage"].options


# --- where it ends up --------------------------------------------------------


def test_the_persona_sits_above_the_agents_own_instructions():
    """In the instructions, not the per-call prompt: it is the same text every
    call, so it costs one cache entry rather than one per task. First, because
    shared bytes at the front are the ones a provider's cache reuses across
    agents."""
    from friday.config import AgentConfig
    from friday.agent.harness import Harness

    built = Harness(
        config=AgentConfig(
            name="x",
            api_key="k",
            base_url="http://localhost/v1",
            model="m",
            persona="You are Long Lee's assistant.",
        ),
        instructions="You classify messages.",
        notes="Ask for the env first.",
    )

    assert built.instructions == (
        "You are Long Lee's assistant.\n\n"
        "You classify messages.\n\n"
        "Ask for the env first."
    )


def test_an_agent_with_no_persona_reads_exactly_as_it_did_before():
    from friday.config import AgentConfig
    from friday.agent.harness import Harness

    built = Harness(
        config=AgentConfig(
            name="x", api_key="k", base_url="http://localhost/v1", model="m"
        ),
        instructions="You read log lines.",
    )

    assert built.instructions == "You read log lines."


# --- the shipped configuration ----------------------------------------------


def _shipped():
    import os

    for key in ("TRIAGE_API_KEY", "RESPONDER_API_KEY"):
        os.environ.setdefault(key, "test-key")
    return load_config(REPO / "config.yaml")


def test_every_shipped_agent_declares_what_it_wants():
    """Not that they all get the same thing — that each one's choice was made
    on purpose. An agent silently taking the default is fine for a new one and
    is not fine for the seven that exist, because the reason differs per
    agent and is written next to each."""
    import yaml

    raw = yaml.safe_load((REPO / "config.yaml").read_text())["agents"]

    undeclared = [name for name, spec in raw.items() if "persona" not in (spec or {})]
    assert undeclared == [], f"no persona declared for {undeclared}"


def test_the_agent_that_speaks_for_the_operator_has_the_voice():
    config = _shipped()

    for name in ("responder", "dag_compose"):
        assert "one or two sentences" in config.agents[name].persona, name


def test_the_agent_that_only_picks_a_tool_carries_nothing():
    """Triage writes no text at all — its output is a tool name and a number.
    There is no language to rule on and no voice to write in, and every word
    of a persona would be paid for on the highest-volume call in the system to
    change a choice between four tools, which it cannot.

    It was `language` while triage still filled in `environment` and wrote a
    `summary`. It stopped doing both and this did not follow, which is what a
    persona mode set once and never revisited looks like."""
    assert _shipped().agents["triage"].persona == ""


def test_the_agents_that_fill_in_validated_fields_keep_the_language_rule():
    """`environment` has to be one of production / staging / dev. An agent
    carrying "write in Vietnamese" alongside a voice instruction writes
    `sản xuất`, validation rejects it, and the reporter is asked to confirm an
    environment they already gave."""
    config = _shipped()

    for name in ("extractor_api_issue", "dag_analyze"):
        persona = config.agents[name].persona
        assert "Long Lee's assistant" in persona, name
        assert "one or two sentences" not in persona, name
        # And they are told which values are never translated.
        assert "no longer refers to anything" in persona, name


def test_the_nodes_that_return_a_path_or_a_diff_carry_nothing():
    config = _shipped()

    for name in ("dag_read_logs", "dag_find_code"):
        assert config.agents[name].persona == "", name


def test_the_shipped_persona_file_has_every_section_a_mode_names():
    """A heading renamed in `PERSONA.md` and not in `persona.py` drops that
    section from every agent, silently — the file still parses, the agents
    still run, and nothing anywhere says the voice is gone."""
    from friday.agent.persona import _SECTIONS

    persona = load(REPO / "PERSONA.md")
    wanted = {name for names in _SECTIONS.values() for name in names}

    assert set(persona._sections) == wanted


# --- the composition root asks; it does not know ----------------------------


def test_no_agent_configuration_is_read_in_the_composition_root():
    """`run_agent.py` constructs the adapters and starts the loops. Which knobs
    a step has — its confidence threshold, how many examples it shows, how many
    tone examples it wants — is that step's business, and reading them here
    means adding one is a change in two files.

    Enforced by grep because the leak is invisible: nothing breaks when a
    `options.get(...)` appears here, it just quietly makes the root know one
    more thing about one more step.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "run_agent.py").read_text()

    for leak in ("config.agents", "options.get("):
        assert leak not in source, f"{leak!r} belongs in the module that owns it"
