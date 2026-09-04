"""Structured metadata for one skill, before the agent decides to read it.

The same factory pattern as `fetch_skill`: what an agent can reach is
composition, not something the agent declares.

**The format is not decided here and not in the library either.** It is
`instruction_prompt.skill_metadata`, beside every other renderer and beside
the escaping every one of them shares. A store that renders is a second
renderer, and the last time this codebase had two of those, one of them did
not escape.
"""

from __future__ import annotations

import logging

from friday.agent.harness import tool
from friday.agent.instruction_prompt import skill_metadata
from friday.agent.skills import SkillLibrary

__all__ = ["describe", "describe_skill_tool"]

log = logging.getLogger(__name__)


def describe(library: SkillLibrary, name: str) -> str:
    """One skill's metadata, or a sentence saying what exists instead.

    The whole of what the tool does, as a plain function, so it can be tested
    without standing up a run: composing three things nobody else composes is
    the part worth pinning, and the tool body around it is a docstring and a
    log line.

    Never raises, for `fetch`'s reason: an agent that guessed at a name made
    an ordinary mistake, and the useful answer is what it could have asked
    for.
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
