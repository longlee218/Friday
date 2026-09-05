"""Finding a skill by what it does, when the agent does not already know its name.

A factory rather than a bare tool: the library is the injection site, the same
shape as `fetch_skill`. The catalogue in the prompt is the fast path for the
skills an agent has already been shown; this is the way to reach one the
catalogue's wording does not surface.
"""

from __future__ import annotations

import logging

from friday.agent.harness import tool
from friday.agent.skills import SkillLibrary

__all__ = ["search_skills_tool"]

log = logging.getLogger(__name__)


def search_skills_tool(library: SkillLibrary):
    """The tool an agent calls to find a skill it cannot name by heart.

    Bound to one library rather than reaching for a module global: what an
    agent can reach is composition, not something the agent declares.
    """

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
