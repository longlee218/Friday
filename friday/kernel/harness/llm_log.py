"""What was sent to the model, and what came back.

A triage decision is otherwise a black box: the prompt is assembled from stored
context, the answer arrives as a tool call, and all that survives is a task row.
When a classification looks wrong the only useful question is which of those two
was at fault, and that needs both sides written down.

Built per run and handed to `agent.run(capabilities=[...])`, so two runs of one
harness each record their own calls — the shared `agent.hooks` this replaced put
one run's call under the other's task. It collects rather than stores: triage
performs no writes, so the caller decides whether a call is worth keeping.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from friday.kernel.harness.harness import Hooks, ModelRetry
from friday.kernel.domain.models import ModelCall, ToolCall
from friday.kernel.ops.redact import scrub

__all__ = ["LogHooks", "UNAVAILABLE"]

log = logging.getLogger("friday.llm")

#: What the model is told when a tool fails, for every tool. Not the real error:
#: a store error carries the database path, an `OSError` from a skill file
#: carries the filesystem, and neither is the model's to see — `scrub` matches
#: credential shapes and nothing else, so what keeps these from the model is
#: this substitution, not the scrub. And not "please try again": retrying a
#: write that may already have landed is how a row gets recorded twice.
UNAVAILABLE = "that tool is unavailable right now — carry on without it"


class LogHooks:
    """Logs both sides of a call, and hands them to whoever asked for them.

    Not itself a capability — it holds the collected rows and the per-call
    bookkeeping, and exposes the Pydantic AI `Hooks` object on `.capability`.
    """

    def __init__(
        self,
        calls: list | None = None,
        *,
        model: str = "",
        tools: list | None = None,
        agent: str = "",
    ) -> None:
        self._calls = calls
        #: What the agent reached for, collected the same way and handed to the
        #: same sink. A second seam would be a second thing to forget.
        self._tools = tools
        #: The configured name, not whatever object the SDK wrapped it in.
        self._model = model
        #: The request in flight, so a timeout that cancels before the response
        #: arrives still leaves the prompt (`unfinished`).
        self._pending: dict = {}
        #: Which agent asked. The harness's configured name, not the run's
        #: state: a hand-off builds a fresh harness with its own name, and this
        #: is what the old `agent.name` on the shared hooks carried.
        self._agent = agent
        #: When the request went out, monotonic — per call, because a run may
        #: make several and the number worth having is how long each took.
        self._sent_at = 0.0
        #: When each tool call started, by call id.
        self._reached: dict[str, float] = {}
        #: Call ids already recorded as failed by `_tool_error`, so the
        #: `after_tool_execute` that fires with the substituted result does not
        #: record the same call a second time.
        self._errored: set[str] = set()

        self.capability = Hooks()
        self.capability.on.before_model_request(self._before_model)
        self.capability.on.after_model_request(self._after_model)
        self.capability.on.before_tool_execute(self._tool_starting)
        self.capability.on.after_tool_execute(self._tool_finished)
        self.capability.on.tool_execute_error(self._tool_error)

    # -- model calls ---------------------------------------------------------

    def _before_model(self, ctx, request_context):
        self._sent_at = time.monotonic()
        system_prompt, prompt = _render_request(request_context.messages)
        self._pending = {
            "system_prompt": scrub(system_prompt),
            "prompt": scrub(prompt),
        }
        return request_context

    def _after_model(self, ctx, *, request_context, response):
        pending, self._pending = self._pending, {}
        if self._calls is None:
            return response
        usage = response.usage
        self._calls.append(
            ModelCall(
                agent=self._agent,
                model=self._model,
                output=scrub(_render_response(response)),
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                latency_ms=self._elapsed_ms(),
                **pending,
            )
        )
        return response

    def unfinished(self) -> ModelCall | None:
        """The call that was sent and never answered, if there is one.

        A timeout cancels the run inside the provider request, so
        `after_model_request` never fires. That is the run whose prompt is worth
        the most, because there is no outcome to reconstruct it from. What was
        known at send time is kept; what could not be known is written as absent
        — empty output, no usage — rather than a number that reads like a
        measurement. Consuming, not reading: the retry loop and the harness's
        `finally` both flush, so a call read twice would be recorded twice.
        """
        if not self._pending:
            return None
        pending, self._pending = self._pending, {}
        return ModelCall(
            agent=self._agent,
            model=self._model,
            output="",
            input_tokens=0,
            output_tokens=0,
            latency_ms=self._elapsed_ms(),
            **pending,
        )

    # -- tool calls ----------------------------------------------------------

    def _tool_starting(self, ctx, *, call, tool_def, args):
        self._reached[call.tool_call_id] = time.monotonic()
        return args

    def _tool_finished(self, ctx, *, call, tool_def, args, result):
        if call.tool_call_id in self._errored:
            # `_tool_error` already recorded this call as failed and returned
            # the substituted result; this fires with that result and must not
            # record it a second time.
            self._errored.discard(call.tool_call_id)
            return result
        self._write_tool_row(ctx, call, result, failed=False)
        return result

    def _tool_error(self, ctx, *, call, tool_def, args, error):
        if isinstance(error, ModelRetry):
            # A deliberate correction the model can act on — not a failure to
            # hide. Let Pydantic AI handle the retry.
            raise error
        log.warning("tool failed: %s", scrub(str(error)))
        self._errored.add(call.tool_call_id)
        self._write_tool_row(ctx, call, UNAVAILABLE, failed=True)
        return UNAVAILABLE

    def _write_tool_row(self, ctx, call, result: Any, *, failed: bool) -> None:
        if self._tools is None:
            return
        started = self._reached.pop(call.tool_call_id, None)
        self._tools.append(
            ToolCall(
                agent=self._agent,
                tool=call.tool_name,
                # Scrubbed on the way in, like every other stored string: a
                # model chose these, out of text somebody else wrote.
                arguments=scrub(_args_text(call.args)),
                result=scrub(str(result)),
                failed=failed,
                latency_ms=(
                    int((time.monotonic() - started) * 1000)
                    if started is not None
                    else None
                ),
            )
        )

    def _elapsed_ms(self) -> int:
        return int((time.monotonic() - self._sent_at) * 1000)


def _render_request(messages: list) -> tuple[str, str]:
    """The system side and the prompt side of one request, from its message
    history. Instructions and any system prompt make the system side; the user
    turn and any tool outputs or corrections make the prompt side."""
    system_bits: list[str] = []
    prompt_bits: list[str] = []
    for message in messages:
        instructions = getattr(message, "instructions", None)
        if instructions:
            system_bits.append(instructions)
        for part in getattr(message, "parts", []):
            kind = getattr(part, "part_kind", "")
            if kind == "system-prompt":
                system_bits.append(part.content)
            elif kind == "user-prompt":
                prompt_bits.append(_content_text(part.content))
            elif kind == "tool-return":
                prompt_bits.append(f"[tool {part.tool_name}] {part.content}")
            elif kind == "retry-prompt":
                prompt_bits.append(
                    f"[retry {getattr(part, 'tool_name', '') or ''}] "
                    f"{_retry_text(part)}"
                )
    return "\n".join(system_bits), "\n".join(prompt_bits)


def _render_response(response) -> str:
    """One line per part the model produced — a tool call with its arguments
    spelled out, because the arguments are the whole point of recording it."""
    bits: list[str] = []
    for part in response.parts:
        kind = getattr(part, "part_kind", "")
        if kind == "tool-call":
            bits.append(f"{part.tool_name}({_args_text(part.args)})")
        elif kind == "text":
            bits.append(part.content)
    return "\n".join(bits)


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            item if isinstance(item, str) else getattr(item, "text", "")
            for item in content
        )
    return str(content)


def _retry_text(part) -> str:
    content = part.content
    if isinstance(content, str):
        return content
    return "; ".join(
        f"{'.'.join(str(p) for p in e.get('loc', ()))}: {e.get('msg', '')}"
        if isinstance(e, dict)
        else str(e)
        for e in content
    )


def _args_text(args) -> str:
    if args is None:
        return ""
    if isinstance(args, str):
        return args
    try:
        return json.dumps(args, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(args)
