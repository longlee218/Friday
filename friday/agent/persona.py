"""Who an agent is, before it is told what its job is.

One file, `PERSONA.md`, read once at startup. Two families of agent get a
section of it — the ones that write to a person, and the ones that are a step
inside an investigation — and the family is decided where the agent is built,
not in configuration. It is not a knob: triage's job changed once and the mode
it had been given did not follow, and 79% of the highest-volume prompt in the
system was a description of how to write replies, sent to something that never
writes one.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from pathlib import Path

__all__ = ["Family", "Persona", "load"]

log = logging.getLogger(__name__)


class Family(StrEnum):
    """Which section of `PERSONA.md` an agent is built with.

    The value is the `## ` heading it reads. Triage and the extractors are no
    family: they get nothing, and there is deliberately no member for that.
    """

    RESPONDER = "Responder"
    NODE = "Node"


class Persona:
    def __init__(self, sections: dict[str, str]) -> None:
        self._sections = sections

    def render(self, family: Family) -> str:
        """The section for one family. Empty when the file had none."""
        return self._sections.get(str(family), "")

    def __len__(self) -> int:
        return len(self._sections)


def load(path: Path | str) -> Persona:
    """Read `PERSONA.md`. Missing is not an error — an agent without a persona
    still does its job, and refusing to start over a prose file would trade a
    working system for a tidy one. Unreadable is worth a warning."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        log.info("no persona file at %s — agents run without one", path)
        return Persona({})
    except OSError as exc:
        log.warning("persona could not be read — %s", exc)
        return Persona({})
    return Persona(_sections(text))


def _sections(text: str) -> dict[str, str]:
    """Split on `## ` headings. `### ` stays inside its parent; headings no
    family names are dropped, because the file is also prose for a person."""
    wanted = {str(f) for f in Family}
    found: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            heading = line[3:].strip()
            current = heading if heading in wanted else None
            if current:
                found[current] = []
        elif current:
            found[current].append(line)
    return {k: "\n".join(v).strip() for k, v in found.items() if "\n".join(v).strip()}
