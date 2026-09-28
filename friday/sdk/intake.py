"""What core Intake hands a domain's enricher, and what every agent receives.

Contracts only. Core `intake()` (`friday/kernel/spine/intake.py`) builds an
`IntakeSeed` from the task's own text, passes it to the domain's one enricher
(`Plugin.enricher`, e.g. backend's `enrich`, which returns a `Placement`), then
retrieves memory and skills keyed by what the enricher returned (board `domains-plug-in`, ticket 04).

A domain type — the enricher's return — carries two things core asks of it:
`IDENTITY`, the names of the fields a reply may not move without the case
becoming another case, and `retrieval_keys()`, the named keys memory is
matched on. Everything else on it is the domain's own business.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["ArtifactRef", "Hints", "IntakeContext", "IntakeSeed"]


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """One `[artifact <id>: <description>]` the store wrote into the text."""

    id: str
    #: As the store labelled it ("a curl, POST /v1/…"). Which ref is *the
    #: curl* is the domain's call, not core's.
    description: str


@dataclass(frozen=True, slots=True)
class Hints:
    """Raw regex pulls from the whole conversation, in the order they appear.
    What they mean (the correlationId, the curl) is the domain's to decide."""

    uuids: tuple[str, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()


@dataclass(frozen=True, slots=True)
class IntakeSeed:
    """Core's first half, before the domain and before retrieval — what an
    enricher is handed."""

    channel_id: str
    #: Every reporter turn, replies included, joined oldest first.
    request_text: str
    reported_at: str
    hints: Hints
    #: The same turns kept apart, oldest first — for a reader that asks which
    #: turn said something (backend: the newest URL names the env).
    turns: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class IntakeContext:
    """What every agent in a run receives. Rebuilt every pass over the whole
    conversation, never checkpointed."""

    request_text: str
    reported_at: str
    hints: Hints
    #: The enricher's return, or `None` for a domain with no enricher (`ops`).
    domain: Any = None
    memory: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()

    @property
    def identity(self) -> tuple:
        """The staleness anchor: the domain type's `IDENTITY` fields, in
        order. `()` with no domain, so a reply always continues."""
        if self.domain is None:
            return ()
        return tuple(getattr(self.domain, name) for name in type(self.domain).IDENTITY)
