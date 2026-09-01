from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from friday.config import AgentConfig
from friday.agent.harness import Harness, ToolContext, tool
from friday.domain.models import InboundEvent, TaskType
from friday.triage.prefilter import is_compensation_talk

__all__ = ["Decided", "NeedsHuman", "TaskType", "Triage", "TriageOutcome"]

log = logging.getLogger(__name__)

@dataclass(frozen=True, slots=True)
class Decided:
    """Triage reached a conclusion: this message is of this type.

    That is the whole of it. No parameters, no summary — triage classifies and
    stops. Lifting values out of the message is a different job with a
    different failure mode, it belongs to whoever needs those values, and
    doing both here meant two producers for one set of fields and a merge to
    reconcile them. See `friday/extraction.py`.
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


INSTRUCTIONS = """You decide what a chat message is. Nothing else.

Call exactly one tool. Which tool you call is the answer; the only thing you
add is how certain you are of it.

Do not copy values out of the message, do not summarise it, do not answer it.
Something else reads the message for what it contains — your job is the label
and your confidence in it.

Messages about salary, personal matters, or social talk are always skip."""


@tool
def create_api_issue_task(ctx: ToolContext[_Capture], confidence: float) -> str:
    """An API is behaving incorrectly: an error, a wrong response, a failure.

    Args:
        confidence: How certain you are of this classification, 0 to 1.
    """
    ctx.context.decided = Decided(type="api_issue", confidence=confidence)
    return "recorded"


@tool
def create_access_request_task(
    ctx: ToolContext[_Capture], confidence: float
) -> str:
    """Someone is asking for permission or access to a project or repository.

    Args:
        confidence: How certain you are of this classification, 0 to 1.
    """
    ctx.context.decided = Decided(type="access_request", confidence=confidence)
    return "recorded"


@tool
def create_doc_question_task(
    ctx: ToolContext[_Capture], confidence: float
) -> str:
    """A question about documentation, a specification, or intended behaviour.

    Args:
        confidence: How certain you are of this classification, 0 to 1.
    """
    ctx.context.decided = Decided(type="doc_question", confidence=confidence)
    return "recorded"


@tool
def skip(ctx: ToolContext[_Capture], confidence: float) -> str:
    """The message needs no action: social talk, salary, or anything off topic.

    Args:
        confidence: How certain you are of this classification, 0 to 1.
    """
    ctx.context.decided = Decided(type="skip", confidence=confidence)
    return "recorded"


TOOLS = [
    create_api_issue_task,
    create_access_request_task,
    create_doc_question_task,
    skip,
]


class Triage:
    """Decides what a mention is. Performs no writes.

    The type and its parameters are expressed as one tool per type: each tool's
    schema declares the parameters its own type needs, which is how the
    discriminated union is encoded. Tool calling is used rather than a
    structured output type because some OpenAI-compatible providers reject
    `response_format: json_schema` outright.
    """

    def __init__(
        self,
        *,
        config: AgentConfig,
        model=None,
        examples: Sequence[tuple[str, str]] = (),
    ) -> None:
        # Examples are appended to the instructions rather than passed per
        # call: the instructions are the stable prefix, and a list that
        # changed per call would cost the cache hit on everything after it.
        # A mark made now therefore takes effect at the next start.
        self._run = Harness(
            config=config,
            instructions=INSTRUCTIONS + _examples_block(examples),
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
        if is_compensation_talk(event.text):
            # Decided, not dropped: it is still a recorded outcome, and the
            # model is never told what anyone earns.
            log.debug("skipping %s without a model call", event.provider_message_id)
            return Decided(type="skip", confidence=1.0)

        capture = _Capture()
        # One extra turn: the answer arrives as a tool call, which is the call
        # and its result where a written answer would be one turn.
        from datetime import datetime, timezone

        from friday.agent.instruction_prompt import (
            ContextBundle,
            conversation,
            identity,
            task,
            base,
        )

        bundle = ContextBundle(
            identity=identity("triage", "You classify mentions of the watched account."),
            base=base(datetime.now(timezone.utc)),
            conversation=conversation(list(context) + [event]),
            task=task("classify", None, None),
        )
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


def _examples_block(examples: Sequence[tuple[str, str]]) -> str:
    """Past classifications the operator vouched for, as few-shot examples.

    Empty when nobody has vouched for anything, which is the state a fresh
    install is in and the state it stays in until somebody reacts. That is
    deliberate: an example nobody looked at teaches the classifier its own
    habits, and the drift has no floor because every generation of examples
    is drawn from the last one's output.
    """
    if not examples:
        return ""
    lines = ["", "", "Past messages, and what they turned out to be:"]
    lines += [f"  {text!r} -> {kind}" for text, kind in examples]
    return "\n".join(lines)
