"""Ticket 24 — how to do something, written once, findable by whoever needs it.

The property that makes a hundred skills affordable: the prompt carries one
line per skill, and the body is fetched only when an agent decides it needs
it. Every test here guards some edge of that, or of the promise that adding
a skill is adding a file.
"""

from __future__ import annotations

import pytest

from friday.kernel.harness.skills import SkillLibrary
from friday.kernel.toolsets.skills import describe


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


def test_a_miss_does_not_recite_a_large_library(tmp_path):
    """The miss exists so a wrong guess can recover, and up to a point the
    names *are* the recovery. Past it they are the catalogue's whole cost paid
    again, on the failure path, by the tool that exists so the catalogue would
    not have to grow.

    It is also what let a bad test pass once: an assertion of `name in answer`
    is satisfied by a miss that lists every name.
    """
    for i in range(30):
        write(
            tmp_path,
            f"skill-{i:02d}",
            f"---\nname: skill-{i:02d}\ndescription: d{i}\n---\nB",
        )

    library = SkillLibrary(tmp_path).load()

    for answer in (library.fetch("nope"), library.search("nope")):
        assert "skill-00" not in answer, answer
        assert "30" in answer, answer


def test_a_miss_still_names_them_while_there_are_few(tmp_path):
    """The threshold cuts the recital off, it does not remove it — a handful
    of names is the cheapest recovery there is."""
    write(tmp_path, "trace-a-request", SKILL)

    library = SkillLibrary(tmp_path).load()

    assert "trace-a-request" in library.fetch("nope")


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
    from friday.kernel.toolsets.skills import fetch_skill_tool

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
    from friday.kernel.config import AgentConfig
    from friday.kernel.responder import Responder

    cfg = AgentConfig(
        name="responder",
        api_key="sk-x",
        base_url="https://example.invalid/v1",
        model="m",
    )

    responder = Responder(config=cfg, skills=_library(tmp_path))

    assert [t.name for t in responder._run.tools] == [
        "fetch_skill",
        "search_skills",
        "describe_skill",
        "read_skill_file",
    ]


def test_an_empty_library_gives_neither_the_tools_nor_the_sections(tmp_path):
    """A library with nothing in it used to hand over four tools that could
    only answer "none are defined" — and the prompt described none of them,
    because the sections are decided by the catalogue having lines while the
    tools were wired on the library merely existing. A fresh install is
    exactly that case.

    The two facts agree now, and this is what says so: no tools, no sections,
    for the same reason.
    """
    from friday.kernel.config import AgentConfig
    from friday.kernel.responder import Responder
    from friday.kernel.responder.prompt import build_input

    cfg = AgentConfig(
        name="responder",
        api_key="sk-x",
        base_url="https://example.invalid/v1",
        model="m",
    )
    empty = SkillLibrary(tmp_path).load()
    assert len(empty) == 0, "the fixture is the point of the test"

    responder = Responder(config=cfg, skills=empty)
    text = build_input(asking="x")

    assert responder._run.tools == []
    # The four skill tools used to be described in three separate sections
    # (`search_skills_system`, `describe_skill_system`,
    # `read_skill_file_system`). Those sections are gone — the SDK
    # describes the tools via function-calling schema, so duplicating
    # the description in the prompt is what rots first.
    for section in ("skill_system", "<skill>"):
        assert f"<{section}>" not in text, section


def test_the_responder_without_skills_carries_no_tool():
    from friday.kernel.config import AgentConfig
    from friday.kernel.responder import Responder

    cfg = AgentConfig(
        name="responder",
        api_key="sk-x",
        base_url="https://example.invalid/v1",
        model="m",
    )

    assert Responder(config=cfg)._run.tools == []


def test_the_catalogue_reaches_the_prompt_the_responder_builds(tmp_path):
    """The DeerFlow `<skill>` block puts the catalogue in `<name>`, with
    the description and location close enough that the model can decide
    whether to fetch the body without re-reading the whole prompt."""
    from friday.kernel.harness.instruction_prompt import SkillMeta, skill_system

    meta = [
        SkillMeta(
            name="trace-a-request",
            description="find the log lines for one request",
            mutability="custom",
            location="/skills/trace/SKILL.md",
            allowed_tools=(),
        ),
    ]
    rendered = skill_system(meta).render()

    assert "<name>trace-a-request</name>" in rendered
    assert "<location>" in rendered
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

    from friday.kernel.harness.skills import SkillLibrary

    library = SkillLibrary(Path(__file__).resolve().parents[1] / "skills").load()

    assert library.problems == []
    assert "where-to-find-a-correlation-id" in library
    assert "x-request-id" in library.fetch("where-to-find-a-correlation-id")


def test_the_responder_is_told_not_to_invent_a_location():
    """With no skill covering it, the ask is exactly what it is today. The
    guard is a sentence in the instructions; the live provider honoured it in
    every sample, and the test pins the sentence so it does not quietly go."""
    from friday.kernel.responder import INSTRUCTIONS

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


# --- ticket 01: frontmatter grows `mutability` and `allowed_tools` ----------


def test_a_skill_without_mutability_defaults_to_custom(tmp_path):
    """Every shipped skill is operator-written and editable; the default
    is the truth about what exists, and a skill written before this
    field existed parses unchanged."""
    write(tmp_path, "trace-a-request", SKILL)

    library = SkillLibrary(tmp_path).load()

    assert library.problems == []
    assert library.get("trace-a-request").mutability == "custom"


def test_a_skill_without_allowed_tools_defaults_to_empty(tmp_path):
    """Empty means the agent may use any tool; `describe_skill` renders
    it as `(all)`. An empty tuple is the safe default."""
    write(tmp_path, "trace-a-request", SKILL)

    library = SkillLibrary(tmp_path).load()

    assert library.problems == []
    assert library.get("trace-a-request").allowed_tools == ()


def test_a_skill_can_declare_mutability_built_in(tmp_path):
    """The other half of the field. Without it, mutability is decorative."""
    (tmp_path / "shipped").mkdir()
    (tmp_path / "shipped" / "SKILL.md").write_text(
        "---\nname: shipped\ndescription: d\nmutability: built_in\n---\nB",
        encoding="utf-8",
    )

    library = SkillLibrary(tmp_path).load()

    assert library.problems == []
    assert library.get("shipped").mutability == "built_in"


def test_a_bad_mutability_is_rejected_by_name(tmp_path):
    """Same shape as every other frontmatter rejection — the file is
    named, the value is named, the loader keeps going."""
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "SKILL.md").write_text(
        "---\nname: bad\ndescription: d\nmutability: wrong\n---\nB",
        encoding="utf-8",
    )

    library = SkillLibrary(tmp_path).load()

    assert len(library) == 0
    assert len(library.problems) == 1
    assert "mutability" in library.problems[0]
    assert "bad/SKILL.md" in library.problems[0]


def test_allowed_tools_parses_a_list_from_frontmatter(tmp_path):
    """A list of tool names the skill expects; the model reads it before
    the body so a skill that wants only a log reader does not get a
    write tool handed to it."""
    (tmp_path / "trace").mkdir()
    (tmp_path / "trace" / "SKILL.md").write_text(
        "---\nname: trace\ndescription: d\n"
        "allowed_tools: [read_logs, query_range]\n---\nB",
        encoding="utf-8",
    )

    library = SkillLibrary(tmp_path).load()

    assert library.problems == []
    assert library.get("trace").allowed_tools == ("read_logs", "query_range")


def test_allowed_tools_must_be_a_list(tmp_path):
    """A scalar here is most likely a typo (`allowed_tools: read_logs`
    should be a list, not a single string); reject it rather than
    silently wrapping it into a one-element list."""
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "SKILL.md").write_text(
        "---\nname: bad\ndescription: d\nallowed_tools: read_logs\n---\nB",
        encoding="utf-8",
    )

    library = SkillLibrary(tmp_path).load()

    assert len(library.problems) == 1
    assert "allowed_tools" in library.problems[0]


def test_get_returns_none_for_a_skill_that_does_not_exist(tmp_path):
    """`get` does not raise — an agent asking for a name that does not
    exist deserves a hand back, the same shape as `fetch`."""
    write(tmp_path, "trace-a-request", SKILL)

    library = SkillLibrary(tmp_path).load()

    assert library.get("trace-a-request") is not None
    assert library.get("does-not-exist") is None


def test_the_shipped_skills_all_parse_with_safe_defaults():
    """The shipped library is the example an operator copies; every
    skill loads with the new defaults applied. The library's
    `problems` list is empty, and iterating the catalogue shows the
    expected mutability on every entry."""
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    library = SkillLibrary(repo / "skills").load()

    assert library.problems == []
    catalogue = library.catalogue()
    assert catalogue, "the shipped directory should have at least one skill"
    for line in catalogue:
        name = line.split(":", 1)[0]
        skill = library.get(name)
        assert skill.mutability == "custom", name
        assert skill.allowed_tools == (), name


# --- ticket 02: search, describe, read --------------------------------------


def test_search_finds_an_exact_name_match(tmp_path):
    """Exact name beats everything else."""
    write(tmp_path, "trace-a-request", SKILL)
    write(
        tmp_path,
        "deploy",
        "---\nname: deploy\ndescription: how to deploy\n---\nB",
    )

    library = SkillLibrary(tmp_path).load()

    lines = library.search("trace-a-request").split("\n")

    # Only `trace-a-request` matches the exact-name query; `deploy` does
    # not appear at all.
    assert lines == ["trace-a-request: Find the log lines for one request"]


def test_search_finds_a_name_prefix_match(tmp_path):
    """A prefix beats description substring and token match."""
    write(tmp_path, "trace-a-request", SKILL)
    write(
        tmp_path,
        "trace-corpus",
        "---\nname: trace-corpus\ndescription: indexing\n---\nB",
    )
    write(
        tmp_path,
        "deploy",
        "---\nname: deploy\ndescription: trace from staging\n---\nB",
    )

    library = SkillLibrary(tmp_path).load()
    lines = library.search("trace").split("\n")

    # Both `trace-a-request` and `trace-corpus` should rank above `deploy`
    assert lines[0].startswith("trace-")
    assert lines[1].startswith("trace-")
    assert "deploy" not in lines[:2]


def test_search_finds_a_description_substring(tmp_path):
    """A description substring beats a token match."""
    write(
        tmp_path,
        "tracing",
        "---\nname: tracing\ndescription: find a request by id\n---\nB",
    )
    write(
        tmp_path,
        "trace-corpus",
        "---\nname: trace-corpus\ndescription: indexing\n---\nB",
    )

    library = SkillLibrary(tmp_path).load()
    lines = library.search("request").split("\n")

    assert lines[0].startswith("tracing:")


def test_search_caps_at_five_matches(tmp_path):
    """Five is enough; a longer result is the agent's signal to narrow."""
    for i in range(8):
        write(
            tmp_path,
            f"trace-{i}",
            f"---\nname: trace-{i}\ndescription: how to trace {i}\n---\nB",
        )

    library = SkillLibrary(tmp_path).load()
    lines = library.search("trace").split("\n")

    assert len(lines) == 5


def test_search_empty_query_answers_rather_than_returning_nothing(tmp_path):
    """An empty string is the one reply a model cannot act on, and this
    module's own rule is that a bad guess comes back as something it can —
    which is why `fetch` answers a missing name with the list of real ones."""
    write(tmp_path, "trace-a-request", SKILL)

    library = SkillLibrary(tmp_path).load()

    for blank in ("", "   "):
        answer = library.search(blank)
        assert "trace-a-request" in answer, blank


def test_search_no_match_returns_a_sentence(tmp_path):
    """A query with no matches gets the same shape as `fetch`'s miss:
    the question back, the available names."""
    write(tmp_path, "trace-a-request", SKILL)

    library = SkillLibrary(tmp_path).load()
    answer = library.search("xyzzy")

    assert "no skill matches" in answer.lower()
    assert "trace-a-request" in answer


def test_search_matches_a_phrase_whose_words_are_out_of_order(tmp_path):
    """The query is split, not the corpus. The tool asks the agent for a
    phrase and a phrase is rarely a contiguous substring of anything, so
    without this the fourth rank could only ever match an infix of the name —
    any query inside a description's word is already inside the description
    and caught one rank above. It was advertised to the model as token
    matching and was not."""
    write(
        tmp_path,
        "trace-a-request",
        "---\nname: trace-a-request\ndescription: Find log lines\n---\nB",
    )

    library = SkillLibrary(tmp_path).load()

    # Not `name in answer`: the miss sentence lists every available skill by
    # name, so that assertion is satisfied by the failure it is meant to catch.
    assert library.search("log find") == "trace-a-request: Find log lines"


def test_search_needs_every_word_of_the_query_not_just_one(tmp_path):
    """A rank that matches everything ranks nothing: four ordinary words
    would hit every skill on `the` alone."""
    write(
        tmp_path,
        "trace-a-request",
        "---\nname: trace-a-request\ndescription: Find log lines\n---\nB",
    )

    library = SkillLibrary(tmp_path).load()

    assert "No skill matches" in library.search("find the deploy rollback")


def test_search_still_reaches_an_infix_of_the_name(tmp_path):
    """What the old fourth rank did do. Splitting the query rather than the
    corpus has to keep it: a one-word query is one token."""
    write(
        tmp_path,
        "trace-a-request",
        "---\nname: trace-a-request\ndescription: Find log lines\n---\nB",
    )

    library = SkillLibrary(tmp_path).load()

    assert library.search("a-req") == "trace-a-request: Find log lines"


def test_search_is_case_insensitive(tmp_path):
    """A reporter who typed `CORRELATION` should still find
    `correlation` — case is not a discovery aid."""
    write(tmp_path, "trace-a-request", SKILL)

    library = SkillLibrary(tmp_path).load()

    assert "trace-a-request" in library.search("TRACE")


# --- describe ----------------------------------------------------------------


def test_describe_returns_the_four_fields_in_order(tmp_path):
    write(tmp_path, "trace-a-request", SKILL)

    library = SkillLibrary(tmp_path).load()
    text = describe(library, "trace-a-request")
    lines = text.split("\n")

    assert lines[0].startswith("name:")
    assert lines[1].startswith("description:")
    assert lines[2].startswith("allowed_tools:")
    assert lines[3].startswith("location:")


def test_describe_mutability_custom_renders_as_editable(tmp_path):
    """The two strings the reference code shows: `[custom, editable]`
    and `[built-in]`. A model trained on the reference shape recognises
    either."""
    write(tmp_path, "trace-a-request", SKILL)

    library = SkillLibrary(tmp_path).load()

    assert "[custom, editable]" in describe(library, "trace-a-request")


def test_describe_mutability_built_in_renders_without_editable(tmp_path):
    (tmp_path / "shipped").mkdir()
    (tmp_path / "shipped" / "SKILL.md").write_text(
        "---\nname: shipped\ndescription: d\nmutability: built_in\n---\nB",
        encoding="utf-8",
    )

    library = SkillLibrary(tmp_path).load()

    assert "[built-in]" in describe(library, "shipped")


def test_describe_allowed_tools_empty_renders_as_all(tmp_path):
    """Empty means any tool — the agent decides."""
    write(tmp_path, "trace-a-request", SKILL)

    library = SkillLibrary(tmp_path).load()

    assert "(all)" in describe(library, "trace-a-request")


def test_describe_allowed_tools_list_renders_as_csv(tmp_path):
    (tmp_path / "trace").mkdir()
    (tmp_path / "trace" / "SKILL.md").write_text(
        "---\nname: trace\ndescription: d\n"
        "allowed_tools: [read_logs, query_range]\n---\nB",
        encoding="utf-8",
    )

    library = SkillLibrary(tmp_path).load()

    text = describe(library, "trace")
    assert "read_logs, query_range" in text


def test_describe_escapes_values_at_the_seam(tmp_path):
    """A description containing `<` or `&` cannot close its own block
    or inject HTML-entity tricks — escape once, the renderer."""
    (tmp_path / "tricky").mkdir()
    (tmp_path / "tricky" / "SKILL.md").write_text(
        '---\nname: tricky\ndescription: "<bad> & ampersand"\n---\nbody',
        encoding="utf-8",
    )

    library = SkillLibrary(tmp_path).load()

    text = describe(library, "tricky")
    assert "&lt;bad&gt;" in text
    assert "&amp;" in text
    assert "<bad>" not in text


def test_describe_unknown_name_says_so(tmp_path):
    write(tmp_path, "trace-a-request", SKILL)

    library = SkillLibrary(tmp_path).load()

    text = describe(library, "does-not-exist")
    assert "no skill called" in text.lower() or "no skill" in text.lower()
    assert "trace-a-request" in text


def test_describe_location_is_an_absolute_path(tmp_path):
    """`config.py` defaults `skills_directory` to the relative "skills", so
    without resolving it the model is handed `skills/x/SKILL.md` — a path that
    means nothing unless you already know where the process was started, which
    is the one thing a reader of a transcript does not.

    A **relative** directory is what production passes, so that is what this
    builds from. Asserting on a `tmp_path`-built library would pass whether or
    not anything resolved, which is how this went out wrong: the test was
    named for absoluteness and only checked the filename.
    """
    import os
    from pathlib import Path

    write(tmp_path, "trace-a-request", SKILL)
    here = Path.cwd()
    os.chdir(tmp_path.parent)
    try:
        library = SkillLibrary(tmp_path.name).load()
        text = describe(library, "trace-a-request")
    finally:
        os.chdir(here)

    location_line = next(l for l in text.split("\n") if l.startswith("location:"))
    path_value = location_line.split(":", 1)[1].strip()
    assert Path(path_value).is_absolute(), path_value
    assert path_value.endswith("trace-a-request/SKILL.md"), path_value


# --- read_skill_file ---------------------------------------------------------


def test_read_skill_file_returns_a_deep_path(tmp_path):
    """The third step of disclosure: a body points at a deep file,
    the agent can read it by path verbatim."""
    BIG_SKILL_PATH = (
        "---\nname: deploy\ndescription: how to deploy\n---\n\n"
        "Steps are short. For rollback, see [rollback.md](references/rollback.md).\n"
    )
    write(tmp_path, "deploy", BIG_SKILL_PATH)
    (tmp_path / "deploy" / "references").mkdir()
    (tmp_path / "deploy" / "references" / "rollback.md").write_text(
        "Rollback: revert the tag.", encoding="utf-8"
    )

    library = SkillLibrary(tmp_path).load()

    assert library.fetch("deploy/references/rollback.md") == "Rollback: revert the tag."


def test_read_skill_file_rejects_uncatalogued_path(tmp_path):
    write(tmp_path, "deploy", BIG_SKILL)
    (tmp_path / "deploy" / "rollback.md").write_text("Rollback: revert the tag.")

    library = SkillLibrary(tmp_path).load()

    answer = library.fetch("deploy/no-such-file.md")
    assert "no file" in answer.lower()


def test_read_skill_file_does_not_leak_outside_the_skill(tmp_path):
    """A fabricated path cannot reach a sibling skill's directory.
    Same property `fetch_skill(name/file.md)` already has: files are
    served from what was catalogued at startup, no filesystem lookup."""
    write(tmp_path, "deploy", BIG_SKILL)
    write(tmp_path, "secret", SKILL)

    library = SkillLibrary(tmp_path).load()

    answer = library.fetch("deploy/../secret/SKILL.md")
    assert "Find the log lines" not in answer
    assert "no file" in answer.lower()
