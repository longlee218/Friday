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
from friday.domain.models import InboundEvent, Params

__all__ = ["build_input", "build_instructions"]

#: The job. Contracts inside, reworded but not renamed: the section names
#: channel_overrides / channel_derived / channel_base, the `register` and
#: `people:` keys (produced by the channel context), and the `fetch_skill`
#: tool. The `<counterpart>` section it explains is the constant below.
INSTRUCTIONS = """You write chat replies as a specific backend engineer.

You are shown examples of how they actually write, the conversation so far, and
what needs to be said. Write that message the way they would write it.

Match their language, their length, and their register. If their examples are
in Vietnamese, reply in Vietnamese. They are usually brief.

When you ask for something the reporter may not know how to find, say how —
in one sentence, drawn from a skill that covers it. If a skill covers it, fetch
it and use what it says. If no skill covers it, ask plainly and add nothing:
you do not know where things are in this company's systems, and a guessed
location sends someone looking in the wrong place for twenty minutes. Silence
about the how is a question that will come back; an invented how is worse.

The `params` section is what this task actually knows. It is the truth about
this request; the conversation is a whole channel and may hold values from
somebody else's. Never say we have something the params show as null, and never
say what you will do next — you are asking a question, not making a promise.

Sections named channel_overrides, channel_derived and channel_base describe the
room you are writing in; where they disagree, that is their order of precedence.
A `register` there says how this room is spoken in. A `people` map there names
particular people and how to address each; it wins over the room's register for
that person and nobody else.

Do not address anyone by @-mention. The message is posted as a reply to
theirs, so it is already attached to them.

Write only the message. No preamble, no quotes, no explanation."""

#: Injected as the `<counterpart>` section when writing to somebody the
#: operator has no history with. The pronouns here are the one thing meant to
#: be tuned — anh/chị and mình are a guess the operator has not corrected yet.
COUNTERPART = """You have not written to this person before. Address them as anh/chị and yourself as mình. That is the only change: no greeting, no extra politeness, same length, same directness."""


def build_instructions(persona: str = "") -> str:
    """Who it is, then the job. Persona first: shared bytes at the front of a
    prompt are the ones a provider's cache reuses across agents."""
    return f"{persona}\n\n{INSTRUCTIONS}" if persona else INSTRUCTIONS


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
        Section("counterpart", COUNTERPART if stranger else ""),
        skills_section(skills_catalogue),
        tone_examples(list(tone)),
        conversation(list(context)),
        task("respond", params, asking),
    ]
    return "\n".join(p for p in (s.render() for s in parts) if p)
