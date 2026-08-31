from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, fields, replace
from typing import Any, Literal

from agents import (
    Agent,
    ModelSettings,
    OpenAIChatCompletionsModel,
    RunConfig,
    RunContextWrapper,
    Runner,
    function_tool,
    set_tracing_disabled,
)
from openai import AsyncOpenAI

from friday.config import AgentConfig
from friday.llm_log import LogHooks
from friday.models import InboundEvent
from friday.triage.prefilter import is_compensation_talk
from friday.triage.params import (
    clean,
    find_correlation_id,
    find_curl,
    find_environment,
)

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

# Tracing is on by default and exports to OpenAI using the same key as model
# requests. With a third-party provider that leaks both the traffic and the
# credential, so it is switched off at import.
set_tracing_disabled(True)

TaskType = Literal["api_issue", "access_request", "doc_question", "skip"]


@dataclass(frozen=True, slots=True)
class ApiIssueParams:
    summary: str
    environment: str | None = None
    correlation_id: str | None = None
    curl: str | None = None


@dataclass(frozen=True, slots=True)
class AccessRequestParams:
    project: str
    permission: str
    summary: str


@dataclass(frozen=True, slots=True)
class DocQuestionParams:
    question: str
    doc_ref: str | None = None


@dataclass(frozen=True, slots=True)
class SkipParams:
    reason: str


Params = ApiIssueParams | AccessRequestParams | DocQuestionParams | SkipParams


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


@function_tool
def create_api_issue_task(
    ctx: RunContextWrapper[_Capture],
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


@function_tool
def create_access_request_task(
    ctx: RunContextWrapper[_Capture],
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


@function_tool
def create_doc_question_task(
    ctx: RunContextWrapper[_Capture],
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


@function_tool
def skip(ctx: RunContextWrapper[_Capture], confidence: float, reason: str) -> str:
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
        self._config = config
        self._agent = Agent[_Capture](
            name=config.name,
            instructions=INSTRUCTIONS,
            model=model or self._chat_model(config),
            tools=TOOLS,
            model_settings=ModelSettings(
                # Without a forced tool call the vaguest message — "the api is
                # wrong", the most common shape there is — produces no call at
                # all and the mention silently yields nothing.
                tool_choice="required",
                **config.settings,
            ),
            tool_use_behavior="stop_on_first_tool",
            hooks=LogHooks(),
        )

    @staticmethod
    def _chat_model(config: AgentConfig) -> OpenAIChatCompletionsModel:
        client = AsyncOpenAI(base_url=config.base_url, api_key=config.api_key)
        return OpenAIChatCompletionsModel(model=config.model, openai_client=client)

    async def decide(
        self, event: InboundEvent, *, context: Sequence[InboundEvent] = ()
    ) -> TriageOutcome:
        if is_compensation_talk(event.text):
            # Decided, not dropped: it is still a recorded outcome, and the
            # model is never told what anyone earns.
            log.debug("skipping %s without a model call", event.provider_message_id)
            return Decided(
                type="skip", confidence=1.0, params=SkipParams("compensation talk")
            )

        capture = _Capture()
        try:
            await Runner.run(
                self._agent,
                _prompt(event, context),
                context=capture,
                max_turns=self._config.max_turns + 1,
                run_config=RunConfig(tracing_disabled=True),
            )
        except Exception as exc:  # noqa: BLE001 - every failure becomes work
            log.warning("triage failed for %s: %s", event.provider_message_id, exc)
            return NeedsHuman(f"triage failed: {exc}")

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
    if isinstance(params, ApiIssueParams):
        for name, found in (
            ("environment", find_environment(text)),
            ("correlation_id", find_correlation_id(text)),
            ("curl", find_curl(text)),
        ):
            if found is not None:
                cleaned[name] = found
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
