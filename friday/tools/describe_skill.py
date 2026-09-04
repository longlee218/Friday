"""Structured metadata for one skill, before the agent decides to read it.

The same factory pattern as `fetch_skill`: what an agent can reach is
composition, not something the agent declares. The four-line `key: value`
block mirrors the catalogue's `name: description` shape — same format, more
detail — so the model does not have to learn a new one.
"""

from __future__ import annotations

import logging

from friday.agent.harness import tool
from friday.agent.skills import SkillLibrary

__all__ = ["describe_skill_tool"]

log = logging.getLogger(__name__)


def describe_skill_tool(library: SkillLibrary):
    """The tool an agent calls to see what a skill is for, before reading it."""

    def describe_skill(name: str) -> str:
        """The structured metadata for one skill.

        Four fields, in this order: name, description (with `[custom, editable]`
        or `[built-in]` appended), allowed tools (or `(all)`), and the
        absolute path to the skill's `SKILL.md`. The body is not included;
        use `fetch_skill` for that.

        Args:
            name: the skill's name, as listed by `search_skills` or the
                catalogue.
        """
        return library.metadata_for(name)

    describe_skill.__doc__ = (
        "Read the structured metadata for one skill before deciding to read "
        "its body.\n\n"
        "Returns four fields: `name`, `description` (with the mutability "
        "tag), `allowed_tools` (or `(all)`), and `location` (the absolute "
        "path to the skill's `SKILL.md`). The body is not included — use "
        "`fetch_skill` for that. The mutability tag is `[custom, editable]` "
        "for skills the operator writes, `[built-in]` for skills they do "
        "not.\n\n"
        "Args:\n"
        "    name: the skill's name, as listed by `search_skills` or the "
        "catalogue.\n"
    )
    return tool(describe_skill)