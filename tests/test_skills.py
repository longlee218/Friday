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
    """One skill, in the directory convention: `<name>/SKILL.md`."""
    stem = name.removesuffix(".md")
    (directory / stem).mkdir(parents=True, exist_ok=True)
    (directory / stem / "SKILL.md").write_text(text, encoding="utf-8")


SKILL = """---
name: trace-a-request
description: Find the log lines for one request
---

Query the log store for the id, over the last hour.
"""


# --- adding a skill is adding a file ---------------------------------------


def test_a_skill_is_one_file_and_needs_no_list_edited(tmp_path):
    write(tmp_path, "trace-a-request", SKILL)

    library = SkillLibrary(tmp_path).load()

    assert len(library) == 1
    assert "trace-a-request" in library


def test_a_second_file_appears_without_touching_the_first(tmp_path):
    write(tmp_path, "trace-a-request", SKILL)
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
    write(tmp_path, "trace-a-request", SKILL)

    library = SkillLibrary(tmp_path).load()

    assert len(library) == 1


# --- what the prompt carries ------------------------------------------------


def test_the_catalogue_carries_the_description_not_the_body(tmp_path):
    """The whole point. A hundred descriptions is a page; a hundred bodies is
    a context window."""
    write(tmp_path, "trace-a-request", SKILL)

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
    write(tmp_path, "trace-a-request", SKILL)

    body = SkillLibrary(tmp_path).load().fetch("trace-a-request")

    assert "Query the log store" in body


def test_fetching_one_that_does_not_exist_is_an_answer_not_an_error(tmp_path):
    """An agent that guessed at a name made an ordinary mistake. Ending the
    run turns a recoverable wrong guess into a task for a person; the useful
    answer is the list of names it could have used."""
    write(tmp_path, "trace-a-request", SKILL)

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
    assert "broken/SKILL.md" in library.problems[0]
    assert expected in library.problems[0]


def test_one_bad_file_does_not_hide_the_good_ones(tmp_path):
    write(tmp_path, "broken.md", "no frontmatter")
    write(tmp_path, "trace-a-request", SKILL)

    library = SkillLibrary(tmp_path).load()

    assert len(library) == 1
    assert len(library.problems) == 1


def test_a_frontmatter_disagreeing_with_its_directory_is_reported(tmp_path):
    """The catalogue advertises one name and `fetch` would know another; an
    agent following the catalogue exactly still guesses wrong. Two names for
    one skill is the directory-era shape of the old duplicate-name problem."""
    write(tmp_path, "trace-a-request", SKILL)
    write(tmp_path, "impostor", SKILL)  # frontmatter still says trace-a-request

    library = SkillLibrary(tmp_path).load()

    assert len(library) == 1
    assert "must match" in library.problems[0]


def test_reloading_replaces_rather_than_accumulates(tmp_path):
    library = SkillLibrary(tmp_path)
    write(tmp_path, "trace-a-request", SKILL)
    library.load()

    import shutil

    shutil.rmtree(tmp_path / "trace-a-request")
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
    from friday.tools.fetch_skill import fetch_skill_tool

    write(tmp_path, "trace-a-request", SKILL)
    tool = fetch_skill_tool(SkillLibrary(tmp_path).load())

    assert tool.name == "fetch_skill"
    # The description is what the model reads to decide whether to call it.
    assert "skill" in (tool.description or "").lower()


# --- the wiring: reaching an agent, not just sitting on disk ----------------


def _library(tmp_path):
    write(tmp_path, "trace-a-request", SKILL)
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
    from friday.agent.instruction_prompt import skill_system

    rendered = skill_system(_library(tmp_path).catalogue()).render()

    assert "trace-a-request" in rendered
    assert "fetch_skill" in rendered
    # The body stays out. That is the whole economy of the thing.
    assert "Query the log store" not in rendered



def test_a_file_saved_with_a_byte_order_mark_still_loads(tmp_path):
    """UTF-8 with a BOM is what Notepad writes by default. Rejecting it tells
    the operator their file has no frontmatter while they are looking at it."""
    (tmp_path / "traced").mkdir()
    (tmp_path / "traced" / "SKILL.md").write_text(
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
    unreadable = tmp_path / "adir" / "SKILL.md"
    unreadable.parent.mkdir()
    unreadable.mkdir()  # a *directory* named SKILL.md: read_text raises OSError
    write(tmp_path, "fine", "---\nname: fine\ndescription: d\n---\nB")

    library = SkillLibrary(tmp_path).load()

    assert len(library.problems) == 1
    assert "adir/SKILL.md" in library.problems[0]
    assert "fine" in library, "one bad file stopped the good ones loading"


# --- ticket 36: the ask explains itself, from a skill -----------------------


def test_a_skill_written_for_the_reporter_exists_and_loads():
    """The mechanism is only demoable with something to draw on. This one is
    written for the person being asked — where the id is, what to send
    instead — not for the agent."""
    from pathlib import Path

    from friday.agent.skills import SkillLibrary

    library = SkillLibrary(Path(__file__).resolve().parents[1] / "skills").load()

    assert library.problems == []
    assert "where-to-find-a-correlation-id" in library
    assert "x-request-id" in library.fetch("where-to-find-a-correlation-id")


def test_the_responder_is_told_not_to_invent_a_location():
    """With no skill covering it, the ask is exactly what it is today. The
    guard is a sentence in the instructions; the live provider honoured it in
    every sample, and the test pins the sentence so it does not quietly go."""
    from friday.responder import INSTRUCTIONS

    assert "If no skill covers it, ask plainly and add nothing" in INSTRUCTIONS


# --- the third step of disclosure: files inside a skill -----------------------


BIG_SKILL = """---
name: deploy
description: How to deploy
---

Steps are short. For rollback, see [rollback.md](rollback.md).
"""


def test_a_skill_with_more_files_announces_them(tmp_path):
    """Disclosure has three steps — catalogue, body, then the file the body
    points at — and the second step has to name the third or the agent cannot
    take it."""
    write(tmp_path, "deploy", BIG_SKILL)
    (tmp_path / "deploy" / "rollback.md").write_text("Rollback: revert the tag.")

    library = SkillLibrary(tmp_path).load()

    body = library.fetch("deploy")
    assert "fetch by name: deploy/rollback.md" in body
    assert library.fetch("deploy/rollback.md") == "Rollback: revert the tag."


def test_a_skill_with_one_file_stays_one_fetch(tmp_path):
    write(tmp_path, "trace-a-request", SKILL)

    assert "fetch by name" not in SkillLibrary(tmp_path).load().fetch("trace-a-request")


def test_a_path_that_was_not_catalogued_is_not_served(tmp_path):
    """Files are read at load and served from memory, so a fabricated path —
    `deploy/../../.env` — is unservable by construction: there is no filesystem
    lookup at fetch time to traverse."""
    write(tmp_path, "deploy", BIG_SKILL)
    (tmp_path.parent / "secret.md").write_text("credentials")

    library = SkillLibrary(tmp_path).load()

    answer = library.fetch("deploy/../secret.md")
    assert "credentials" not in answer
    assert "no file" in answer


def test_the_old_flat_layout_is_reported_not_ignored(tmp_path):
    """A skill quietly skipped because it predates the directory convention is
    the same silence as an unreadable one."""
    (tmp_path / "trace.md").write_text(SKILL)

    library = SkillLibrary(tmp_path).load()

    assert len(library) == 0
    assert "move this to trace/SKILL.md" in library.problems[0]
