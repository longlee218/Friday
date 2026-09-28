"""Reading a supporting file inside a skill's directory, by its path.

The third step of disclosure, given its own name: a skill that splits into
several files has each of them reachable by the path its body wrote, rather
than by knowing to concatenate `name/file.md` onto a fetch.

Same factory pattern as `fetch_skill` — the library is the injection site.
"""

from __future__ import annotations

import logging

from friday.kernel.harness.harness import tool
from friday.kernel.harness.skills import SkillLibrary

__all__ = ["read_skill_file_tool"]

log = logging.getLogger(__name__)


def read_skill_file_tool(library: SkillLibrary):
    """The tool an agent calls to read a file a skill's body pointed at."""

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
