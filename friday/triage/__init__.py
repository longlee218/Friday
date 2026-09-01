from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, fields, replace
from typing import Any, Literal

from friday.config import AgentConfig
from friday.harness import Harness, ToolContext, tool
from friday.models import (
    AccessRequestParams,
    ApiIssueParams,
    DocQuestionParams,
    InboundEvent,
    Params,
    SkipParams,
    TaskType,
)
from friday.triage.prefilter import is_compensation_talk
from friday.triage.params import clean

__all__ = [
    "AccessRequestParams",
    "ApiIssueParams",
    "Decided",
    "DocQuestionParams",
    "NeedsHuman",
    "SkipParams",
    "TaskType",
    "Triage",
    "TriageOutcome",
]

log = logging.getLogger(__name__)

@dataclass(frozen=True, slots=True)
class Decided:
    """Triage reached a conclusion. It has not acted on it."""

    type: TaskType
    confidence: float
    params: Params


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


INSTRUCTIONS = """You triage chat messages that mention a backend engineer.

Call exactly one tool describing what the message is.

For every parameter, look for a matching value in the message and copy it
verbatim. Pass null only when the value is genuinely absent — never invent one,
and never summarise a field that asks for a literal value.

Messages about salary, personal matters, or social talk are always skip."""


@tool
def create_api_issue_task(
    ctx: ToolContext[_Capture],
    confidence: float,
    summary: str,
    environment: str | None,
    correlation_id: str | None,
    curl: str | None,
) -> str:
    """An API is behaving incorrectly: an error, a wrong response, a failure.

    Args:
        confidence: How certain you are of this classification, 0 to 1.
        summary: One line describing the problem.
        environment: The environment named in the message (production, staging, dev). null if absent.
        correlation_id: The correlation id, trace id or request id in the message. null if absent.
        curl: The curl command or request example in the message. null if absent.
    """
    ctx.context.decided = Decided(
        type="api_issue",
        confidence=confidence,
        params=ApiIssueParams(summary, environment, correlation_id, curl),
    )
    return "recorded"


@tool
def create_access_request_task(
    ctx: ToolContext[_Capture],
    confidence: float,
    project: str,
    permission: str,
    summary: str,
) -> str:
    """Someone is asking for permission or access to a project or repository.

    Args:
        confidence: How certain you are of this classification, 0 to 1.
        project: The project or repository named.
        permission: The access being asked for.
        summary: One line describing the request.
    """
    ctx.context.decided = Decided(
        type="access_request",
        confidence=confidence,
        params=AccessRequestParams(project, permission, summary),
    )
    return "recorded"


@tool
def create_doc_question_task(
    ctx: ToolContext[_Capture],
    confidence: float,
    question: str,
    doc_ref: str | None,
) -> str:
    """A question about documentation, a specification, or intended behaviour.

    Args:
        confidence: How certain you are of this classification, 0 to 1.
        question: What is being asked.
        doc_ref: The document or spec referred to. null if none is named.
    """
    ctx.context.decided = Decided(
        type="doc_question",
        confidence=confidence,
        params=DocQuestionParams(question, doc_ref),
    )
    return "recorded"


@tool
def skip(ctx: ToolContext[_Capture], confidence: float, reason: str) -> str:
    """The message needs no action: social talk, salary, or anything off topic.

    Args:
        confidence: How certain you are of this classification, 0 to 1.
        reason: Why no action is needed.
    """
    ctx.context.decided = Decided(
        type="skip", confidence=confidence, params=SkipParams(reason)
    )
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

    def __init__(self, *, config: AgentConfig, model=None) -> None:
        self._run = Harness(
            config=config,
            instructions=INSTRUCTIONS,
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
            return Decided(
                type="skip", confidence=1.0, params=SkipParams("compensation talk")
            )

        capture = _Capture()
        # One extra turn: the answer arrives as a tool call, which is the call
        # and its result where a written answer would be one turn.
        result = await self._run.run(
            _prompt(event, context), context=capture, calls=calls, extra_turns=1
        )
        if result is None:
            return NeedsHuman(f"triage failed: {self._run.last_error}")

        if capture.decided is None:
            return NeedsHuman("triage produced no classification")
        decided = replace(
            capture.decided, params=_hygiene(capture.decided.params, event.text)
        )
        log.info(
            "triaged %s: %s (%.2f) %s",
            event.provider_message_id,
            decided.type,
            decided.confidence,
            decided.params,
        )
        return decided


def _hygiene(params: Params, text: str) -> Params:
    """Clean every value, then let the message correct the model.

    Where a pattern matches the original text, that wins: a value copied out of
    the message cannot be a hallucination, and the model's can.
    """
    cleaned = {
        f.name: clean(getattr(params, f.name))
        if isinstance(getattr(params, f.name), str)
        else getattr(params, f.name)
        for f in fields(params)
    }
    return type(params)(**cleaned)


def _prompt(event: InboundEvent, context: Sequence[InboundEvent]) -> str:
    """Prior messages first, the message to classify last and clearly marked."""
    lines = []
    if context:
        lines.append("Earlier in this conversation:")
        lines += [f"  {m.author_name}: {m.text}" for m in context]
        lines.append("")
    lines.append("Classify this message:")
    lines.append(f"  {event.author_name}: {event.text}")
    return "\n".join(lines)
