"""Finding a skill by what it does, when the agent does not already know its name.

A factory rather than a bare tool: the library is the injection site, the same
shape as `fetch_skill`. The catalogue in the prompt is the fast path for skills
it already knows about; this tool is the way to find skills the catalogue does
not surface.
"""

from __future__ import annotations

import logging

from friday.agent.harness import tool
from friday.agent.skills import SkillLibrary

__all__ = ["search_skills_tool"]

log = logging.getLogger(__name__)


def search_skills_tool(library: SkillLibrary):
    """The tool an agent calls to find a skill it cannot name by heart."""

    def search_skills(query: str) -> str:
        """Find skills whose name or description matches the query.

        Ranks exact-name match above name-prefix above description-substring
        above token match. Caps at five results so a vague query does not
        dump the whole library into the prompt. A description that does not
        say the right words is the operator's description, not this tool's
        problem — the body is too expensive to use as the index.

        Args:
            query: what the agent is looking for, in one phrase.
        """
        return library.search(query)

    search_skills.__doc__ = (
        "Find skills by what they are for, when you do not already know the "
        "name.\n\n"
        "Use this when the catalogue in the prompt does not show a skill that "
        "sounds like what you need. Returns ranked `name: description` "
        "lines — exact-name match first, then name prefix, then description "
        "substring, then a token match. Capped at five results so a vague "
        "query does not dump the library.\n\n"
        "Args:\n"
        "    query: a phrase — what you are trying to do, not the skill's "
        "name.\n"
    )
    return tool(search_skills)