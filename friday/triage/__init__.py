from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from friday.triage.prompt import build_input, build_instructions
from friday.config import AgentConfig
from friday.agent.harness import Harness, ToolContext, tool
from friday.domain.models import PARAMS, InboundEvent, TaskType
from friday.triage.prefilter import Sensitive

__all__ = ["Decided", "NeedsHuman", "TaskType", "Triage", "TriageOutcome"]

log = logging.getLogger(__name__)

@dataclass(frozen=True, slots=True)
class Decided:
    """Triage reached a conclusion: this message is of this type.

    That is the whole of it. No parameters, no summary — triage classifies and
    stops. Lifting values out of the message is a different job with a
    different failure mode, it belongs to whoever needs those values, and
    doing both here meant two producers for one set of fields and a merge to
    reconcile them. See `friday/extraction/`.
    """

    type: TaskType
    confidence: float


@dataclass(frozen=True, slots=True)
class NeedsHuman:
    """Triage could not conclude. The message still becomes work — never
    silence, which is indistinguishable from the system working."""

    reason: str


TriageOutcome = Decided | NeedsHuman


@dataclass
class _Capture:
    """Per-run scratch space. Tools write the decision here rather than to a
    module global, so concurrent runs cannot overwrite each other."""

    decided: Decided | None = None


#: The bare text, kept as an attribute because tests pin sentences in it.
#: Assembly — examples and all — lives in `friday.triage.prompt`.
INSTRUCTIONS = build_instructions()


#: The task types triage may open — everything `PARAMS` fills the fields of.
#: A model proposes one of these; `skip` is its own tool because it creates
#: nothing, and a tool named `create_task` that creates nothing would be
#: lying in its name.
_TASK_TYPES = Literal[tuple(PARAMS)]

#: Each type's description, as the model reads it — pulled from its own
#: `Params` class docstring rather than written a second time here, so
#: adding a task type is adding one class, not a class and a description of
#: it in this module too.
_TASK_TYPE_DOC = "\n".join(
    f"        {name}: {cls.__doc__.strip()}" for name, cls in PARAMS.items()
)


def create_task(
    ctx: ToolContext[_Capture], task_type: _TASK_TYPES, confidence: float
) -> str:
    ctx.context.decided = Decided(type=task_type, confidence=confidence)
    return "recorded"


create_task.__doc__ = f"""Open a task: there is work here for a person.

Args:
    task_type: which kind of task this is —
{_TASK_TYPE_DOC}
    confidence: How certain you are of this classification, 0 to 1.
"""

create_task = tool(create_task)


@tool
def skip(ctx: ToolContext[_Capture], confidence: float) -> str:
    """The message needs no action: social talk, salary, or anything off topic.

    Args:
        confidence: How certain you are of this classification, 0 to 1.
    """
    ctx.context.decided = Decided(type="skip", confidence=confidence)
    return "recorded"


TOOLS = [create_task, skip]


class Triage:
    """Decides what a mention is. Performs no writes.

    `create_task` takes the type as a closed-enum argument rather than being
    one tool per type: the type still comes from the model, but naming a
    fourth type is adding a `Params` class, not a fourth tool. Tool calling is
    used rather than a structured output type because some OpenAI-compatible
    providers reject `response_format: json_schema` outright.
    """

    def __init__(
        self,
        *,
        config: AgentConfig,
        model=None,
        examples: Sequence[tuple[str, str]] = (),
        sensitive: Sensitive | None = None,
    ) -> None:
        #: Empty by default, which means nothing is held. An install that has
        #: not thought about this yet gets the behaviour it would have had
        #: without the feature, rather than a silent list of someone else's
        #: guesses about what is sensitive in their workplace.
        self._sensitive = sensitive or Sensitive(())
        # Examples are appended to the instructions rather than passed per
        # call: the instructions are the stable prefix, and a list that
        # changed per call would cost the cache hit on everything after it.
        # A mark made now therefore takes effect at the next start.
        self._run = Harness(
            config=config,
            instructions=build_instructions(examples),
            tools=TOOLS,
            model=model,
            context_type=_Capture,
            model_settings={
                # Without a forced tool call the vaguest message — "the api is
                # wrong", the most common shape there is — produces no call at
                # all and the mention silently yields nothing.
                "tool_choice": "required",
            },
            tool_use_behavior="stop_on_first_tool",
        )

    async def decide(
        self,
        event: InboundEvent,
        *,
        context: Sequence[InboundEvent] = (),
        calls: list | None = None,
    ) -> TriageOutcome:
        """Decide what a message is. Writes nothing, here or anywhere.

        `calls` collects both sides of every model call, for a caller that
        wants to keep them — which is the runner, because storing is a write.
        """
        held = self._sensitive.found(event.text)
        if held is not None:
            # Held, not dropped. The guarantee is that the model does not see
            # it, not that nobody does: this list contains words that appear in
            # ordinary reports — "token hết hạn rồi" is a bug — and skipping
            # them would be dropping real mentions on the strength of one word.
            #
            # The word is named; the message is not. It is the thing being
            # kept quiet.
            log.info(
                "holding %s for you: it mentions %r, so it was not sent to the "
                "model", event.provider_message_id, held,
            )
            return NeedsHuman(f"mentions {held!r} — not sent to the model")

        capture = _Capture()
        # One extra turn: the answer arrives as a tool call, which is the call
        # and its result where a written answer would be one turn.

        bundle = build_input(list(context) + [event])
        result = await self._run.run(
            bundle, context=capture, calls=calls, extra_turns=1
        )
        if result is None:
            return NeedsHuman(f"triage failed: {self._run.last_error}")

        if capture.decided is None:
            return NeedsHuman("triage produced no classification")
        decided = capture.decided
        log.info(
            "triaged %s: %s (%.2f)",
            event.provider_message_id,
            decided.type,
            decided.confidence,
        )
        return decided
