"""How to do something, written once, findable by whoever needs it.

Progressive disclosure. Every agent is shown one line per skill — the name
and what it is for — and fetches the body only when it decides to use it.
That is what makes a hundred skills affordable: a hundred descriptions is a
page, a hundred bodies is a context window.

A skill is a **directory** holding a `SKILL.md` — frontmatter with `name` and
`description`, then a prose body — plus whatever supporting files the body
refers to. That is the shape skills take everywhere else (`.agents/skills/` in
this very repo), and the shape matters: a skill big enough to need a second
page splits into `SKILL.md` plus `details.md`, and the body links to it. So
disclosure has three steps, not two: the catalogue line, then the body, then
the file the body pointed at — each fetched only when wanted.

**Adding a skill is adding a directory.** There is no central list, since at a
hundred skills a central list is the one place everyone has to change at once.

Skills are a shared resource rather than a property of an agent: the same
"how to trace a request" serves whoever needs it.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

__all__ = ["Skill", "SkillLibrary"]

log = logging.getLogger(__name__)

_FRONTMATTER = "---"

#: The two strings a skill's `mutability` field accepts. `"custom"` is the
#: default — every shipped skill is operator-written and editable, and the
#: truth about what exists today is that there is no built-in yet. `"built_in"`
#: marks the opposite: a skill the operator does not edit, the future home
#: of system-shipped content.
_MUTABILITY = ("built_in", "custom")


@dataclass(frozen=True, slots=True)
class Skill:
    """One thing the operator wrote down."""

    name: str
    description: str
    body: str
    #: Supporting files, relative path -> content. Read at load time with
    #: everything else — a fetch must not do file I/O on the event loop, and
    #: serving only what was catalogued at startup is also what makes a
    #: fabricated path ("../../.env") unservable by construction.
    files: dict[str, str] = None  # type: ignore[assignment]
    #: Whether the operator edits this skill. `"custom"` is the default; the
    #: other value is `"built_in"`. Rendered by `describe_skill` as
    #: `[custom, editable]` or `[built-in]` — the same two strings the
    #: reference code uses, so a model trained on it recognises the signal.
    mutability: str = "custom"
    #: The tool names this skill expects. Empty means any tool — the agent
    #: decides, the skill does not constrain. Rendered by `describe_skill`
    #: as a comma-joined list, or `(all)` when empty.
    allowed_tools: tuple[str, ...] = ()

    def summary(self) -> str:
        """The one line an agent is shown before deciding to read it."""
        return f"{self.name}: {self.description}"


class SkillLibrary:
    @classmethod
    def build(cls, config) -> "SkillLibrary":
        """Load from the configured directory and say what could not be read.

        A skill the operator believes they wrote and which silently is not
        there is worse than a noisy start, so every unreadable file is named.
        """
        library = cls(config.context.skills_directory).load()
        for problem in library.problems:
            log.warning("skill could not be read — %s", problem)
        log.info("%d skill(s) available", len(library))
        return library

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
        """Read every `*/SKILL.md` under the directory. Returns self.

        A skill that cannot be read is recorded in `problems` and skipped. The
        composition root reports those at startup, naming each one: a skill
        the operator believes they wrote, which silently is not there, is
        worse than a noisy start.

        A bare `.md` file at the top level is reported too, by name — it is
        the old layout, and a skill quietly ignored because it predates the
        directory convention is the same silence as an unreadable one.
        """
        self._skills.clear()
        self.problems.clear()

        if not self._dir.exists():
            return self

        for stray in sorted(self._dir.glob("*.md")):
            self.problems.append(
                f"{stray}: a skill is a directory now — move this to "
                f"{stray.stem}/SKILL.md"
            )

        for path in sorted(self._dir.glob("*/SKILL.md")):
            try:
                skill = _read(path)
            except (ValueError, OSError) as exc:
                self.problems.append(f"{path}: {exc}")
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
        """A skill's body, one of its files, or a sentence saying what exists.

        `"tdd"` is the body; `"tdd/tests.md"` is the supporting file the body
        pointed at. A body with supporting files ends with the list of them,
        so the third step of disclosure announces itself.

        Never raises. An agent asking for something that does not exist has
        made an ordinary mistake — it guessed at a name — and the useful
        answer is what it could have asked for. Ending the run instead turns
        a recoverable wrong guess into a task for a person.
        """
        skill_name, _, file_name = name.strip("/").partition("/")
        skill = self._skills.get(skill_name)
        if skill is None:
            known = ", ".join(sorted(self._skills)) or "none are defined"
            return f"There is no skill called {skill_name!r}. Available: {known}."
        if not file_name:
            if not skill.files:
                return skill.body
            listed = ", ".join(f"{skill.name}/{f}" for f in sorted(skill.files))
            return f"{skill.body}\n\nThis skill has more files — fetch by name: {listed}"
        content = skill.files.get(file_name) if skill.files else None
        if content is not None:
            return content
        listed = ", ".join(sorted(skill.files or ())) or "none"
        return (
            f"Skill {skill.name!r} has no file {file_name!r}. Its files: {listed}."
        )

    def get(self, name: str) -> Skill | None:
        """One skill by name, or `None` if it is not here.

        The body is `fetch`; the structured object is `get`. `describe_skill`
        and the frontmatter tests need the fields the reader parsed; `fetch`
        hides them.
        """
        return self._skills.get(name)

    def search(self, query: str) -> str:
        """Ranked `name: description` lines matching the query.

        Ranks in order: exact name > name prefix > description substring >
        every query token found somewhere in the name or description.
        Case-insensitive — `CORRELATION` finds `correlation`. Capped at five
        matches; an empty query and one that matches nothing both come back
        as a sentence naming what is available.

        The last rank splits the **query**, not the corpus, and that is the
        whole of what it buys: the tool asks the agent for a phrase, and a
        phrase is rarely a contiguous substring of anything. `"log find"`
        reaches `"Find log lines"`; splitting the corpus instead — which
        this did — could only ever match an infix of the name, because any
        query inside a description's word is already inside the description
        and caught one rank above. Rank 4 was advertised to the model as
        token matching and was not.

        **All** tokens, not any: a query of four ordinary words matches
        almost every skill on `"the"` alone, and a rank that matches
        everything ranks nothing.

        The body never participates — `search` exists to *find* a skill, not
        to read it. A description that does not say the right words is the
        operator's description, not the tool's problem.
        """
        q = query.strip().lower()
        if not q:
            return self._nothing_matched(query)

        # Score each skill 0..3; ties broken by name for stable order.
        scored: list[tuple[int, str, Skill]] = []
        for skill in self._skills.values():
            name_lower = skill.name.lower()
            desc_lower = skill.description.lower()
            if name_lower == q:
                scored.append((3, skill.name, skill))
            elif name_lower.startswith(q):
                scored.append((2, skill.name, skill))
            elif q in desc_lower:
                scored.append((1, skill.name, skill))
            elif all(
                token in f"{name_lower} {desc_lower}" for token in q.split()
            ):
                scored.append((0, skill.name, skill))

        scored.sort(key=lambda s: (-s[0], s[1]))
        if not scored:
            return self._nothing_matched(query)
        return "\n".join(s.summary() for _, _, s in scored[:5])

    def _nothing_matched(self, query: str) -> str:
        """What a search that found nothing hands back.

        A sentence rather than an empty string, for the reason `fetch` gives:
        an agent that guessed badly has made an ordinary mistake, and the
        useful answer is what it could have asked for. An empty string is the
        one reply it cannot act on.
        """
        known = ", ".join(sorted(self._skills)) or "none are defined"
        return f"No skill matches {query!r}. Available: {known}."

    def metadata_for(self, name: str) -> str:
        """The four-line `key: value` block `describe_skill` renders.

        Format follows the catalogue's `name: description` shape, extended
        to four — and the same `key: value` style `_render_yaml_escaped`
        already produces for `channel_overrides` and `channel_derived`.
        Values are escaped here, with `html.escape(..., quote=False)`, in
        the same shape and for the same reason the prompt sections escape:
        every field sits in element-text position, never inside an
        attribute, so a description with `</skills>` cannot close the
        section it claims to be in.

        An unknown name returns the same "no skill called" sentence
        `fetch` already uses, so an agent that guessed wrong gets the
        available names and not an exception.
        """
        skill = self._skills.get(name)
        if skill is None:
            known = ", ".join(sorted(self._skills)) or "none are defined"
            return f"There is no skill called {name!r}. Available: {known}."

        mutability = (
            "[custom, editable]" if skill.mutability == "custom" else "[built-in]"
        )
        description_with = f"{skill.description} {mutability}"
        tools = (
            ", ".join(skill.allowed_tools) if skill.allowed_tools else "(all)"
        )
        # Derived rather than carried on the dataclass, because a `Skill`
        # describes *what* the operator wrote, not where it lives. The
        # frontmatter `name` is required to equal the directory name, which
        # is what makes deriving it sound.
        #
        # **Resolved.** `config.py` defaults `skills_directory` to the
        # relative "skills", so without this the model is handed
        # `skills/trace-a-request/SKILL.md` — a path that means nothing
        # unless you already know which directory the process was started
        # from, which is the one thing a reader of a transcript does not.
        location = str((self._dir / skill.name / "SKILL.md").resolve())

        return (
            f"name: {html.escape(skill.name, quote=False)}\n"
            f"description: {html.escape(description_with, quote=False)}\n"
            f"allowed_tools: {html.escape(tools, quote=False)}\n"
            f"location: {html.escape(location, quote=False)}"
        )

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, name: object) -> bool:
        return name in self._skills


def _read(path: Path) -> Skill:
    """Parse one skill's `SKILL.md`, or say what is wrong with it.

    The frontmatter's `name` must be the directory's name. Two names for one
    skill means the catalogue advertises one and `fetch` may know the other —
    an agent following the catalogue exactly would still guess wrong.
    """
    # `utf-8-sig` rather than `utf-8`: it reads plain UTF-8 unchanged and also
    # strips the byte-order mark that Notepad and several Windows editors write
    # by default. With the mark in place the file does not start with `---`,
    # and the operator is told their file has no frontmatter while looking
    # straight at it.
    text = path.read_text(encoding="utf-8-sig")
    if not text.lstrip().startswith(_FRONTMATTER):
        raise ValueError("no frontmatter block; expected a '---' line first")

    # Split on the opening and closing fences. The text before the first fence
    # is empty, and the body may itself contain '---' as a horizontal rule —
    # `partition` takes the first closing fence, which is the right one.
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

    if name != path.parent.name:
        raise ValueError(
            f"frontmatter names it {name!r} but the directory is "
            f"{path.parent.name!r} — they must match"
        )

    mutability = str(meta.get("mutability") or "custom")
    if mutability not in _MUTABILITY:
        raise ValueError(
            f"frontmatter 'mutability' must be one of {', '.join(_MUTABILITY)}, "
            f"got {mutability!r}"
        )

    if "allowed_tools" in meta:
        raw = meta["allowed_tools"]
        if not isinstance(raw, list):
            raise ValueError(
                f"frontmatter 'allowed_tools' must be a list, got "
                f"{type(raw).__name__}"
            )
        allowed_tools = tuple(str(t) for t in raw)
    else:
        allowed_tools = ()

    files = {
        str(extra.relative_to(path.parent)): extra.read_text(encoding="utf-8-sig")
        for extra in sorted(path.parent.rglob("*.md"))
        if extra != path
    }
    return Skill(
        name=name,
        description=description,
        body=body.strip(),
        files=files,
        mutability=mutability,
        allowed_tools=allowed_tools,
    )
