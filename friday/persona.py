"""Who every agent is, before it is told what its job is.

One file, `PERSONA.md`, read once at startup and prepended to each agent's own
instructions. In the instructions rather than the per-call prompt because it is
the stable part: the same text every call, so it costs one cache entry rather
than one per task.

Not every agent gets all of it, and that is the whole reason this module has a
`Mode` rather than being a string constant. An agent that fills in
`environment` through a tool call, and whose value has to be one of
`production` / `staging` / `dev`, must not be carrying an instruction to write
in Vietnamese in the same breath. See `## Modes` in `PERSONA.md`.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from pathlib import Path

__all__ = ["Mode", "Persona", "load"]

log = logging.getLogger(__name__)

#: The `##` headings this module knows how to assemble. A heading in the file
#: that is not here is ignored — the file is prose for a person as much as a
#: prompt, and a table of modes is not something to send a model.
_WHO = "Who you are"
_VOICE = "How Long writes"
_LANGUAGE = "Language"

_SECTIONS: dict[str, tuple[str, ...]] = {
    "full": (_WHO, _VOICE, _LANGUAGE),
    "language": (_WHO, _LANGUAGE),
    "none": (),
}


class Mode(StrEnum):
    """How much of the persona one agent gets.

    A closed set, checked when configuration is read, so a typo in
    `config.yaml` fails on the way up rather than silently giving an agent no
    persona at all — which is the one failure that leaves no trace anywhere.
    """

    #: Everything. For agents whose output a person reads as prose.
    FULL = "full"
    #: Identity and the language rule, without the voice. For agents that fill
    #: in structured fields, some of which happen to be free text.
    LANGUAGE = "language"
    #: Nothing. For agents that return a path, a log line or a diff.
    NONE = "none"


class Persona:
    """The assembled text, per mode."""

    def __init__(self, sections: dict[str, str]) -> None:
        self._sections = sections

    def render(self, mode: Mode) -> str:
        """The persona for one agent, ready to sit above its instructions.

        Empty for `Mode.NONE`, and empty when the file is missing — an agent
        with no persona still does its job, and refusing to start because a
        prose file is absent would be trading a working system for a tidy one.
        """
        wanted = [
            self._sections[name]
            for name in _SECTIONS[str(mode)]
            if name in self._sections
        ]
        return "\n\n".join(wanted)

    def __len__(self) -> int:
        return len(self._sections)


def load(path: Path | str) -> Persona:
    """Read `PERSONA.md`, or return an empty persona and say why.

    Missing is not an error: this is prose the operator writes, and a system
    that will not start without it is worse than one that starts without a
    voice. Unreadable *is* worth a warning, because an operator who wrote the
    file and does not see it applied has nothing else to look at.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        log.info("no persona file at %s — agents run without one", path)
        return Persona({})
    except OSError as exc:
        log.warning("persona could not be read — %s", exc)
        return Persona({})

    return Persona(_sections(text))


def _sections(text: str) -> dict[str, str]:
    """Split on `## ` headings, keeping only the ones a mode names.

    The heading itself is dropped: it is a signpost for whoever edits the file,
    and a model reading "## Who you are" above the text is being told the same
    thing twice.
    """
    found: dict[str, str] = {}
    current: str | None = None
    body: list[str] = []

    for line in text.splitlines():
        if line.startswith("## "):
            if current is not None:
                found[current] = "\n".join(body).strip()
            heading = line[3:].strip()
            current = heading if heading in _KNOWN else None
            body = []
        elif current is not None:
            body.append(line)

    if current is not None:
        found[current] = "\n".join(body).strip()
    return {name: text for name, text in found.items() if text}


#: Every heading any mode asks for. Built from `_SECTIONS` rather than listed
#: again, so adding a mode cannot leave its sections silently unparsed.
_KNOWN = {name for names in _SECTIONS.values() for name in names}
