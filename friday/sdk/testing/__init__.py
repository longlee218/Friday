"""Test doubles for the agent harness, behind Friday's own name (seam S2).

The suite scripts a model from fourteen files. Named here once, so that the
harness's move onto Pydantic AI (ticket 05) changes this one module rather than
every test that scripts a run.

These are now the Pydantic AI doubles. `ScriptedModel` is a `FunctionModel`
that replays a list of turns and records each request it was handed;
`assistant_message` and `function_call` build the parts a turn is made of.
`ToolContext` is an alias of `RunContext`, the same alias `harness.py` exposes,
so a test types a tool's first parameter with the name production code uses.

This is the one place under `friday/` besides `harness.py` and `mcp.py` that
may import the vendor SDK — everything an agent is built from still takes its
names through `harness.py`; these are the names a *test* builds a scripted run
from.
"""

from __future__ import annotations

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage as Usage

__all__ = [
    "Agent",
    "AgentInfo",
    "FunctionModel",
    "Model",
    "ModelResponse",
    "ScriptedModel",
    "TextPart",
    "ToolCallPart",
    "ToolContext",
    "Usage",
    "assistant_message",
    "function_call",
]

#: The alias production code uses, re-exported so a scripted tool's first
#: parameter is typed with the same name (`ToolContext[FridayState]`).
ToolContext = RunContext


def assistant_message(text: str) -> TextPart:
    """One turn's plain-text answer."""
    return TextPart(content=text)


def function_call(name: str, args, *, call_id: str | None = None) -> ToolCallPart:
    """One turn's tool call. `args` is a dict the model 'sent', or a raw string
    for the case a provider emits arguments that are not a JSON object."""
    return ToolCallPart(tool_name=name, args=args, tool_call_id=call_id or name)


class _Request:
    """One request the scripted model was handed, so a test can read back what
    the harness actually sent — the message history, tool outputs and all."""

    __slots__ = ("input",)

    def __init__(self, messages: list[ModelMessage]) -> None:
        #: The `list[ModelMessage]` for this request. `ModelRequest` items hold
        #: the prompt and any prior `ToolReturnPart`/`RetryPromptPart`; a
        #: correction turn is the `RetryPromptPart` following a rejected call.
        self.input = list(messages)


class ScriptedModel(FunctionModel):
    """Replays a fixed list of turns and records every request it received.

    Each step is a list of parts — `assistant_message(...)` and/or
    `function_call(...)` — and becomes one `ModelResponse`. The Nth request in a
    run gets the Nth step; a run that makes more requests than there are steps
    (a correction turn, say) repeats the last one, so a script need only say as
    much as the test cares about.
    """

    def __init__(self, steps, *, model_name: str = "test-model") -> None:
        self.calls: list[_Request] = []
        self._steps = [list(step) for step in steps]

        def _reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            self.calls.append(_Request(messages))
            index = len(self.calls) - 1
            if index < len(self._steps):
                parts = self._steps[index]
            elif self._steps:
                parts = self._steps[-1]
            else:
                parts = [TextPart(content="")]
            return ModelResponse(parts=list(parts))

        super().__init__(_reply, model_name=model_name)
