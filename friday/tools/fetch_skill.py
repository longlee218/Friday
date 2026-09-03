"""Reading one skill in full, once an agent has decided it wants it.

A factory rather than a bare tool, and that is not an exception to "a tool
serves everything": what an agent may *reach* is composition, so the library
is injected at build time. The tool it returns is the same shape as every
other one here.
"""

from __future__ import annotations

import logging

from friday.agent.harness import tool
from friday.agent.skills import SkillLibrary

__all__ = ["fetch_skill_tool"]

log = logging.getLogger(__name__)


def fetch_skill_tool(library: SkillLibrary):
    """The tool an agent calls to read a skill it decided it needs.

    Bound to one library rather than reaching for a module global: what an
    agent can reach is composition, not something the agent declares.
    """
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
