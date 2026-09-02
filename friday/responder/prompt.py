"""What the responder's prompt looks like, and from what it is assembled.

The family with real assembly, so the one where "open one file, see the whole
prompt" earns its keep. The stable half is the Responder persona and the job
text; the per-call half is the room, the counterpart, the catalogue, the
operator's real messages, the conversation and the task — that order exactly.

**Order is load-bearing.** Sections render stable-first, so two calls that
differ only late in the list share a byte-identical prefix — which is the
provider's prompt-cache hit. Reordering these because a different order reads
better is a silent cost on every call; a test holds the prefix property.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from friday.agent.instruction_prompt import (
    Section,
    base,
    channel_base,
    channel_derived,
    channel_overrides,
    conversation,
    task,
    tone_examples,
)
from friday.agent.instruction_prompt import skills as skills_section
from friday.agent.prompts import prompt as _text
from friday.domain.models import InboundEvent, Params

__all__ = ["build_input", "build_instructions"]

#: Loaded at import like every prompt, so a missing file fails at startup —
#: not at the first message to a stranger, hours later, mid-draft.
_COUNTERPART = _text("responder-counterpart")


def build_instructions(persona: str = "") -> str:
    """Who it is, then the job. Persona first: shared bytes at the front of a
    prompt are the ones a provider's cache reuses across agents."""
    job = _text("responder")
    return f"{persona}\n\n{job}" if persona else job


def build_input(
    *,
    asking: str,
    params: Params | None = None,
    room=None,
    stranger: bool = False,
    skills_catalogue: list[str] | None = None,
    tone: Sequence[InboundEvent] = (),
    context: Sequence[InboundEvent] = (),
    now: datetime | None = None,
) -> str:
    """Everything one draft call knows, rendered stable-first."""
    parts = [
        base(now or datetime.now(timezone.utc)),
        channel_base(room),
        channel_derived(room),
        channel_overrides(room),
        Section("counterpart", _COUNTERPART if stranger else ""),
        skills_section(skills_catalogue),
        tone_examples(list(tone)),
        conversation(list(context)),
        task("respond", params, asking),
    ]
    return "\n".join(p for p in (s.render() for s in parts) if p)
