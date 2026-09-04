"""Structured metadata for one skill, before the agent decides to read it.

The same factory pattern as `fetch_skill`: what an agent can reach is
composition, not something the agent declares.

The block is four `key: value` lines, which is the catalogue's own
`name: description` shape carried to four fields rather than a second format
the model has to learn.
"""

from __future__ import annotations

import logging

from friday.agent.harness import tool
from friday.agent.skills import SkillLibrary

__all__ = ["describe_skill_tool"]

log = logging.getLogger(__name__)


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
        return library.metadata_for(name)

    return describe_skill
