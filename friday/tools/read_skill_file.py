"""Reading a supporting file inside a skill's directory, by its path.

The third step of disclosure, given its own name: a skill that splits into
several markdown files has each reachable by the path the body referred to,
without the model having to concatenate `name/file.md`. Same factory pattern
as `fetch_skill` — the library is the injection site.
"""

from __future__ import annotations

import logging

from friday.agent.harness import tool
from friday.agent.skills import SkillLibrary

__all__ = ["read_skill_file_tool"]

log = logging.getLogger(__name__)


def read_skill_file_tool(library: SkillLibrary):
    """The tool an agent calls to read a file a skill's body pointed at."""

    def read_skill_file(name: str, file_path: str) -> str:
        """Read one supporting file from a skill's directory.

        The lookup is exact, served from what was catalogued at startup — a
        fabricated path like `deploy/../../.env` is unservable because there
        is no filesystem traversal at fetch time.

        Args:
            name: the skill's name.
            file_path: the file's path inside the skill, exactly as the body
                wrote it (`references/setup.md`, not `setup.md`).
        """
        return library.fetch(f"{name}/{file_path}")

    read_skill_file.__doc__ = (
        "Read a supporting file inside a skill's directory, by its path.\n\n"
        "A skill whose body links to `references/setup.md` is reachable here "
        "with that exact path — no `name/file.md` concatenation needed. Files "
        "are served from what was catalogued at startup, so a fabricated path "
        "outside the skill's directory cannot escape it.\n\n"
        "Args:\n"
        "    name: the skill's name.\n"
        "    file_path: the file's path inside the skill, exactly as the body "
        "wrote it.\n"
    )
    return tool(read_skill_file)