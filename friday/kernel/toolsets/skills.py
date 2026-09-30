"""`core.skills`: finding, describing and reading the operator's skills.

The four skill tools, one toolset (build-the-spine ticket 08 regrouped them
from four modules into this one). Each is a factory rather than a bare tool:
what an agent may *reach* is composition, so the library is injected at build
time and the tool it returns is the same shape as every other one.

**The format is not decided here and not in the library either.** Metadata is
`instruction_prompt.skill_metadata`, beside every other renderer and beside
the escaping every one of them shares. A store that renders is a second
renderer, and the last time this codebase had two of those, one of them did
not escape.
"""

from __future__ import annotations

import logging
from typing import Any

from friday.kernel.harness.harness import tool
from friday.kernel.harness.instruction_prompt import skill_metadata
from friday.kernel.harness.skills import SkillLibrary

__all__ = [
    "describe",
    "describe_skill_tool",
    "fetch_skill_tool",
    "read_skill_file_tool",
    "search_skills_tool",
    "skill_toolset",
]

log = logging.getLogger(__name__)


def skill_toolset(library: SkillLibrary) -> list[Any]:
    """The four, bound to one library — what `core.skills` hands an agent."""
    return [
        fetch_skill_tool(library),
        search_skills_tool(library),
        describe_skill_tool(library),
        read_skill_file_tool(library),
    ]


def fetch_skill_tool(library: SkillLibrary):
    """The tool an agent calls to read a skill it decided it needs."""

    @tool
    def fetch_skill(name: str) -> str:
        """Read the full instructions for one of the available skills.

        Args:
            name: The skill's name as listed in the skills section — or, when
                a fetched skill names more files, that file, like "tdd/tests.md".
        """
        log.info("skill fetched: %s", name)
        return library.fetch(name)

    return fetch_skill


def search_skills_tool(library: SkillLibrary):
    """The tool an agent calls to find a skill it cannot name by heart. The
    catalogue in the prompt is the fast path for the skills an agent has
    already been shown; this reaches one the catalogue's wording does not
    surface."""

    @tool
    def search_skills(query: str) -> str:
        """Find skills by what they are for, when you do not know the name.

        Use this when the catalogue you were shown has no line that sounds
        like what you need. A skill named `deploy` whose description says
        "release a build" will not catch your eye if you are looking for
        "rolling out", and this is how you reach it anyway.

        Returns up to five `name: description` lines, best match first. Never
        the bodies — call fetch_skill once you have picked one.

        Every word of your query has to turn up somewhere, and it is matched
        as written: "writing" does not find "writes". So a search that comes
        back with nothing is usually a word away from one that works — try
        fewer words, or read the catalogue you were given, which lists every
        skill there is.

        Args:
            query: a phrase describing what you are trying to do, not the
                skill's name. Word order does not matter.
        """
        log.info("skills searched: %s", query)
        return library.search(query)

    return search_skills


def describe(library: SkillLibrary, name: str) -> str:
    """One skill's metadata, or a sentence saying what exists instead.

    The whole of what `describe_skill` does, as a plain function, so it can be
    tested without standing up a run. Never raises: an agent that guessed at a
    name made an ordinary mistake, and the useful answer is what it could have
    asked for.
    """
    skill = library.get(name)
    if skill is None:
        return f"There is no skill called {name!r}. Available: {library.known()}."
    return skill_metadata(skill, str(library.location_of(name)))


def describe_skill_tool(library: SkillLibrary):
    """The tool an agent calls to see what a skill is for, before reading it."""

    @tool
    def describe_skill(name: str) -> str:
        """Read one skill's metadata without paying for its body.

        Four lines: the skill's name, its description with a tag saying
        whether the operator edits it (`[custom, editable]`) or not
        (`[built-in]`), the tools the skill expects (or `(all)`), and the
        path to the file on disk.

        Call fetch_skill when you have decided you want what it says.

        Args:
            name: the skill's name, as the catalogue or search_skills gave it.
        """
        log.info("skill described: %s", name)
        return describe(library, name)

    return describe_skill


def read_skill_file_tool(library: SkillLibrary):
    """The tool an agent calls to read a file a skill's body pointed at — the
    third step of disclosure, so a skill split into several files has each
    reachable by the path its body wrote."""

    @tool
    def read_skill_file(name: str, file_path: str) -> str:
        """Read one of a skill's supporting files.

        A skill's body may link to another file beside it — a longer
        procedure, a rollback note. This reads that file by the path the body
        wrote, so you can follow the link without guessing at a name.

        Only files that were on disk when the process started can be read, so
        a path that leads out of the skill's own directory finds nothing.

        Args:
            name: the skill's name.
            file_path: the path the body wrote, relative to the skill —
                "references/setup.md", not "setup.md".
        """
        log.info("skill file read: %s/%s", name, file_path)
        return library.fetch(f"{name}/{file_path}")

    return read_skill_file
