"""What an extractor may ask about, and where the answer lands.

Its own module because `friday/tools/ask_for_fields.py` needs both, and a
tool importing the package that imports the tool is a cycle.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Clarify", "FieldsCapture"]


@dataclass(frozen=True, slots=True)
class Clarify:
    """The extractor's own request: these fields, because of this.

    Intent, not words — `fields` names which of the type's own fields it
    means, closed to a per-type enum so the model cannot invent one that does
    not exist. The Responder turns this into the sentence a reporter reads;
    this module never writes one.
    """

    fields: tuple[str, ...]
    because: str


@dataclass
class FieldsCapture:
    """Per-run scratch space for `ask_clarification`, same pattern as
    triage's — the tool writes here rather than to a module global, so
    concurrent runs cannot overwrite each other."""

    clarify: Clarify | None = None


