"""How to do something, written once, findable by whoever needs it.

Progressive disclosure. Every agent is shown one line per skill — the name
and what it is for — and fetches the body only when it decides to use it.
That is what makes a hundred skills affordable: a hundred descriptions is a
page, a hundred bodies is a context window.

A skill is a Markdown file with frontmatter, because the body is prose meant
for a person and a model to read the same way, and the operator already
writes in that shape. **Adding a skill is adding a file.** There is no
central list, since at a hundred skills a central list is the one place
everyone has to change at once.

Skills are a shared resource rather than a property of an agent: the same
"how to trace a request" serves whoever needs it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

__all__ = ["Skill", "SkillLibrary"]

log = logging.getLogger(__name__)

_FRONTMATTER = "---"


@dataclass(frozen=True, slots=True)
class Skill:
    """One thing the operator wrote down."""

    name: str
    description: str
    body: str

    def summary(self) -> str:
        """The one line an agent is shown before deciding to read it."""
        return f"{self.name}: {self.description}"


class SkillLibrary:
    """Every skill on disk, read once at startup.

    Read once rather than per call: the catalogue goes in the stable front of
    a prompt, and a list that changed mid-run would cost the cache hit on
    everything after it. A skill added while the process runs appears at the
    next restart, which is the same rhythm every other file-backed thing here
    has.
    """

    def __init__(self, directory: Path | str) -> None:
        self._dir = Path(directory)
        self._skills: dict[str, Skill] = {}
        self.problems: list[str] = []

    def load(self) -> "SkillLibrary":
        """Read every `.md` file in the directory. Returns self, for chaining.

        A file that cannot be read is recorded in `problems` and skipped. The
        composition root reports those at startup, naming each file: a skill
        the operator believes they wrote, which silently is not there, is
        worse than a noisy start.
        """
        self._skills.clear()
        self.problems.clear()

        if not self._dir.exists():
            return self

        for path in sorted(self._dir.glob("*.md")):
            try:
                skill = _read(path)
            except ValueError as exc:
                self.problems.append(f"{path}: {exc}")
                continue
            if skill.name in self._skills:
                self.problems.append(
                    f"{path}: a skill named {skill.name!r} is already defined in "
                    f"another file"
                )
                continue
            self._skills[skill.name] = skill
        return self

    def catalogue(self) -> list[str]:
        """One line per skill, for the prompt.

        The cost of this grows with the *number* of skills, not their length —
        which is the whole reason the body is fetched separately.
        """
        return [skill.summary() for skill in self._skills.values()]

    def fetch(self, name: str) -> str:
        """A skill's body, or a sentence saying it is not there.

        Never raises. An agent asking for a skill that does not exist has
        made an ordinary mistake — it guessed at a name — and the useful
        answer is the list of names it could have used. Ending the run
        instead turns a recoverable wrong guess into a task for a person.
        """
        skill = self._skills.get(name)
        if skill is not None:
            return skill.body
        known = ", ".join(sorted(self._skills)) or "none are defined"
        return f"There is no skill called {name!r}. Available: {known}."

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, name: object) -> bool:
        return name in self._skills


def _read(path: Path) -> Skill:
    """Parse one skill file, or say what is wrong with it."""
    text = path.read_text(encoding="utf-8")
    if not text.lstrip().startswith(_FRONTMATTER):
        raise ValueError("no frontmatter block; expected a '---' line first")

    # Split on the opening and closing fences. `2` because the text before the
    # first fence is empty and the body may itself contain '---' as a rule.
    _, _, rest = text.lstrip().partition(_FRONTMATTER)
    front, fence, body = rest.partition(f"\n{_FRONTMATTER}")
    if not fence:
        raise ValueError("frontmatter is never closed; expected a second '---'")

    try:
        meta = yaml.safe_load(front) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"frontmatter is not valid YAML: {exc}") from None
    if not isinstance(meta, dict):
        raise ValueError("frontmatter should be a mapping of name and description")

    name = str(meta.get("name") or "").strip()
    description = str(meta.get("description") or "").strip()
    if not name:
        raise ValueError("frontmatter has no 'name'")
    if not description:
        raise ValueError(
            f"skill {name!r} has no 'description' — it is the only thing an "
            f"agent sees before deciding to read the rest"
        )

    return Skill(name=name, description=description, body=body.strip())


def fetch_skill_tool(library: SkillLibrary):
    """The tool an agent calls to read a skill it decided it needs.

    Bound to one library rather than reaching for a module global, the same
    shape `remember_tool` uses: what an agent can reach is composition, not
    something the agent declares.
    """
    from friday.harness import tool

    @tool
    def fetch_skill(name: str) -> str:
        """Read the full instructions for one of the available skills.

        Args:
            name: The skill's name, exactly as listed in the skills section.
        """
        log.info("skill fetched: %s", name)
        return library.fetch(name)

    return fetch_skill
