"""Ticket 24 — how to do something, written once, findable by whoever needs it.

The property that makes a hundred skills affordable: the prompt carries one
line per skill, and the body is fetched only when an agent decides it needs
it. Every test here guards some edge of that, or of the promise that adding
a skill is adding a file.
"""

from __future__ import annotations

import pytest

from friday.agent.skills import Skill, SkillLibrary


def write(directory, name: str, text: str) -> None:
    (directory / name).write_text(text, encoding="utf-8")


SKILL = """---
name: trace-a-request
description: Find the log lines for one request
---

Query the log store for the id, over the last hour.
"""


# --- adding a skill is adding a file ---------------------------------------


def test_a_skill_is_one_file_and_needs_no_list_edited(tmp_path):
    write(tmp_path, "trace.md", SKILL)

    library = SkillLibrary(tmp_path).load()

    assert len(library) == 1
    assert "trace-a-request" in library


def test_a_second_file_appears_without_touching_the_first(tmp_path):
    write(tmp_path, "trace.md", SKILL)
    write(
        tmp_path,
        "voice.md",
        "---\nname: voice\ndescription: How they write\n---\n\nKeep it short.",
    )

    library = SkillLibrary(tmp_path).load()

    assert len(library) == 2


def test_a_directory_that_does_not_exist_is_not_an_error(tmp_path):
    """A fresh install has no skills. That is a state, not a fault."""
    library = SkillLibrary(tmp_path / "nothing-here").load()

    assert len(library) == 0
    assert library.problems == []


def test_files_that_are_not_markdown_are_left_alone(tmp_path):
    write(tmp_path, "notes.txt", "not a skill")
    write(tmp_path, "trace.md", SKILL)

    library = SkillLibrary(tmp_path).load()

    assert len(library) == 1


# --- what the prompt carries ------------------------------------------------


def test_the_catalogue_carries_the_description_not_the_body(tmp_path):
    """The whole point. A hundred descriptions is a page; a hundred bodies is
    a context window."""
    write(tmp_path, "trace.md", SKILL)

    (line,) = SkillLibrary(tmp_path).load().catalogue()

    assert "Find the log lines for one request" in line
    assert "Query the log store" not in line


def test_the_catalogue_grows_with_the_number_of_skills_not_their_length(tmp_path):
    long_body = "x" * 50_000
    write(
        tmp_path,
        "a.md",
        f"---\nname: a\ndescription: short\n---\n\n{long_body}",
    )
    write(
        tmp_path,
        "b.md",
        f"---\nname: b\ndescription: short\n---\n\n{long_body}",
    )

    catalogue = SkillLibrary(tmp_path).load().catalogue()

    assert len(catalogue) == 2
    assert sum(len(line) for line in catalogue) < 200


# --- fetching ---------------------------------------------------------------


def test_fetching_a_skill_returns_the_body(tmp_path):
    write(tmp_path, "trace.md", SKILL)

    body = SkillLibrary(tmp_path).load().fetch("trace-a-request")

    assert "Query the log store" in body


def test_fetching_one_that_does_not_exist_is_an_answer_not_an_error(tmp_path):
    """An agent that guessed at a name made an ordinary mistake. Ending the
    run turns a recoverable wrong guess into a task for a person; the useful
    answer is the list of names it could have used."""
    write(tmp_path, "trace.md", SKILL)

    answer = SkillLibrary(tmp_path).load().fetch("trce-a-reqest")

    assert "no skill called" in answer
    assert "trace-a-request" in answer


def test_fetching_when_nothing_is_defined_still_answers(tmp_path):
    answer = SkillLibrary(tmp_path).load().fetch("anything")

    assert "none are defined" in answer


# --- malformed files --------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("no frontmatter at all", "no frontmatter"),
        ("---\nname: a\ndescription: b\n\nnever closed", "never closed"),
        ("---\nname: a\n---\n\nbody", "no 'description'"),
        ("---\ndescription: b\n---\n\nbody", "no 'name'"),
        ("---\n- a list\n---\n\nbody", "should be a mapping"),
        ("---\nname: [\n---\n\nbody", "not valid YAML"),
    ],
)
def test_a_malformed_file_is_reported_by_name_rather_than_failing_later(
    tmp_path, text, expected
):
    """Reported at startup, naming the file. A skill that fails mid-run fails
    inside somebody's task, hours after the operator wrote it."""
    write(tmp_path, "broken.md", text)

    library = SkillLibrary(tmp_path).load()

    assert len(library) == 0
    assert len(library.problems) == 1
    assert "broken.md" in library.problems[0]
    assert expected in library.problems[0]


def test_one_bad_file_does_not_hide_the_good_ones(tmp_path):
    write(tmp_path, "broken.md", "no frontmatter")
    write(tmp_path, "trace.md", SKILL)

    library = SkillLibrary(tmp_path).load()

    assert len(library) == 1
    assert len(library.problems) == 1


def test_two_files_claiming_one_name_is_reported(tmp_path):
    """Silently letting the second win is how "why is it reading the old
    one?" happens a week later."""
    write(tmp_path, "a.md", SKILL)
    write(tmp_path, "b.md", SKILL)

    library = SkillLibrary(tmp_path).load()

    assert len(library) == 1
    assert "already defined" in library.problems[0]


def test_reloading_replaces_rather_than_accumulates(tmp_path):
    library = SkillLibrary(tmp_path)
    write(tmp_path, "trace.md", SKILL)
    library.load()

    (tmp_path / "trace.md").unlink()
    library.load()

    assert len(library) == 0
    assert library.problems == []


# --- the shipped skills -----------------------------------------------------


def test_the_skills_that_ship_with_the_repo_all_parse():
    """They are the example an operator copies. One that does not load is a
    worked example of the wrong shape."""
    library = SkillLibrary("skills").load()

    assert library.problems == []
    assert len(library) >= 1


# --- the tool ---------------------------------------------------------------


def test_the_tool_is_bound_to_one_library(tmp_path):
    """What an agent can reach is composition, not something it declares."""
    from friday.agent.skills import fetch_skill_tool

    write(tmp_path, "trace.md", SKILL)
    tool = fetch_skill_tool(SkillLibrary(tmp_path).load())

    assert tool.name == "fetch_skill"
    # The description is what the model reads to decide whether to call it.
    assert "skill" in (tool.description or "").lower()


# --- the wiring: reaching an agent, not just sitting on disk ----------------


def _library(tmp_path):
    write(tmp_path, "trace.md", SKILL)
    return SkillLibrary(tmp_path).load()


def test_the_responder_is_given_the_catalogue_and_the_tool(tmp_path):
    """Loading the library and never handing it to anyone is the failure this
    guards: the ticket's promise is that an agent can *reach* a skill, not
    that the files parse."""
    from friday.config import AgentConfig
    from friday.responder import Responder

    cfg = AgentConfig(
        name="responder",
        api_key="sk-x",
        base_url="https://example.invalid/v1",
        model="m",
    )

    responder = Responder(config=cfg, skills=_library(tmp_path))

    assert [t.name for t in responder._run.agent.tools] == ["fetch_skill"]


def test_the_responder_without_skills_carries_no_tool():
    from friday.config import AgentConfig
    from friday.responder import Responder

    cfg = AgentConfig(
        name="responder",
        api_key="sk-x",
        base_url="https://example.invalid/v1",
        model="m",
    )

    assert Responder(config=cfg)._run.agent.tools == []


def test_the_catalogue_reaches_the_prompt_the_responder_builds(tmp_path):
    from friday.agent.instruction_prompt import skills as skills_section

    rendered = skills_section(_library(tmp_path).catalogue()).render()

    assert "trace-a-request" in rendered
    assert "fetch_skill" in rendered
    # The body stays out. That is the whole economy of the thing.
    assert "Query the log store" not in rendered


def test_only_the_reasoning_nodes_of_a_graph_get_skills(tmp_path):
    """"How to trace a request" is written down for whoever decides what the
    logs mean, not for the thing fetching them."""
    from types import SimpleNamespace

    from friday.config import AgentConfig
    from friday.dag.workflows import agents_for_api_issue

    def block(name):
        return AgentConfig(
            name=name,
            api_key="sk-x",
            base_url="https://example.invalid/v1",
            model="m",
        )

    config = SimpleNamespace(
        agents={
            "dag_read_logs": block("read"),
            "dag_analyze": block("analyze"),
            "dag_compose": block("compose"),
        }
    )

    agents = agents_for_api_issue(config, _library(tmp_path))

    assert [t.name for t in agents["analyze_stack"].agent.tools] == ["fetch_skill"]
    assert [t.name for t in agents["compose_reply"].agent.tools] == ["fetch_skill"]
    assert agents["read_logs"].agent.tools == []
    # And the catalogue is in the reasoning node's instructions, where it is
    # the same every call.
    assert "trace-a-request" in agents["analyze_stack"].agent.instructions
    assert "trace-a-request" not in agents["read_logs"].agent.instructions


def test_a_file_saved_with_a_byte_order_mark_still_loads(tmp_path):
    """UTF-8 with a BOM is what Notepad writes by default. Rejecting it tells
    the operator their file has no frontmatter while they are looking at it."""
    (tmp_path / "bom.md").write_text(
        "---\nname: traced\ndescription: how to trace\n---\n\nBody.",
        encoding="utf-8-sig",
    )

    library = SkillLibrary(tmp_path).load()

    assert library.problems == []
    assert "traced" in library
    assert "Body." in library.fetch("traced")


def test_something_unreadable_is_reported_rather_than_crashing_startup(tmp_path):
    """A directory named `*.md` raises `IsADirectoryError`, which is an
    `OSError` and not a `ValueError`. It used to reach the composition root
    and stop the process from starting."""
    (tmp_path / "adir.md").mkdir()
    (tmp_path / "fine.md").write_text("---\nname: fine\ndescription: d\n---\nB")

    library = SkillLibrary(tmp_path).load()

    assert len(library.problems) == 1
    assert "adir.md" in library.problems[0]
    assert "fine" in library, "one bad file stopped the good ones loading"
