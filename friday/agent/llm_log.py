"""What was sent to the model, and what came back.

A triage decision is otherwise a black box: the prompt is assembled from stored
context, the answer arrives as a tool call, and all that survives is a task row.
When a classification looks wrong the only useful question is which of those two
was at fault, and that needs both sides written down.

Attached as `agent.hooks`, so it costs nothing unless DEBUG is on.
"""

from __future__ import annotations

import json
import logging
import time

from friday.agent.harness import Hooks
from friday.domain.models import ModelCall, ToolCall
from friday.ops.redact import scrub

__all__ = ["LogHooks"]

log = logging.getLogger("friday.llm")


class LogHooks(Hooks):
    """Logs both sides of a call, and hands them to whoever asked for them.

    It collects rather than stores: triage performs no writes, so the caller
    decides whether a call is worth keeping.
    """

    def __init__(
        self, calls: list | None = None, *, model: str = "", tools: list | None = None
    ) -> None:
        self._calls = calls
        #: What the agent reached for, collected the same way and handed to
        #: the same sink. A second seam would be a second thing to forget.
        self._tools = tools
        #: When each tool call started, by call id — the SDK gives the hooks
        #: no way to correlate a start with its end except this.
        self._reached: dict[str, float] = {}
        #: Call ids whose tool failed. See `tool_failed`.
        self._failed: set[str] = set()
        # The configured name, not whatever object the SDK wrapped it in: what
        # matters later is which model we asked for.
        self._model = model
        self._pending: dict = {}
        #: Which agent asked. Known from `on_llm_start`, and needed by
        #: `unfinished()` because there is no `agent` argument there.
        self._agent = ""
        #: When the request went out, monotonic. Per *call* rather than per
        #: run: a run may make several, and the number worth having is how
        #: long the provider took on each, not how long the loop took.
        self._sent_at = 0.0

    async def on_llm_start(self, context, agent, system_prompt, input_items) -> None:
        log.debug("→ %s system:\n%s", agent.name, system_prompt)
        self._agent = agent.name
        self._sent_at = time.monotonic()
        prompt = []
        for item in input_items:
            line = _short(item)
            prompt.append(line)
            log.debug("→ %s %s", agent.name, line)
        self._pending = {
            "system_prompt": scrub(system_prompt or ""),
            "prompt": scrub("\n".join(prompt)),
        }

    async def on_llm_end(self, context, agent, response) -> None:
        output = []
        for item in response.output:
            line = _short(item)
            output.append(line)
            log.debug("← %s %s", agent.name, line)
        usage = response.usage
        log.debug(
            "← %s usage: %d in / %d out",
            agent.name,
            usage.input_tokens,
            usage.output_tokens,
        )
        if self._calls is None:
            return
        self._calls.append(
            ModelCall(
                agent=agent.name,
                model=self._model,
                output=scrub("\n".join(output)),
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                latency_ms=self._elapsed_ms(),
                **self._pending,
            )
        )
        self._pending = {}


    async def on_tool_start(self, context, agent, tool) -> None:
        log.debug("→ %s tool %s", agent.name, getattr(tool, "name", tool))
        self._reached[getattr(context, "tool_call_id", "")] = time.monotonic()

    async def on_tool_end(self, context, agent, tool, result) -> None:
        name = getattr(tool, "name", str(tool))
        log.debug("← %s tool %s -> %s", agent.name, name, str(result)[:120])
        if self._tools is None:
            return
        call_id = getattr(context, "tool_call_id", "")
        started = self._reached.pop(call_id, None)
        self._tools.append(
            ToolCall(
                agent=agent.name,
                tool=name,
                # Scrubbed on the way in, like every other stored string: a
                # model chose these, out of text somebody else wrote.
                arguments=scrub(str(getattr(context, "tool_arguments", "") or "")),
                result=scrub(str(result)),
                failed=call_id in self._failed,
                latency_ms=(
                    int((time.monotonic() - started) * 1000)
                    if started is not None
                    else None
                ),
            )
        )
        self._failed.discard(call_id)

    def tool_failed(self, call_id: str) -> None:
        """Told by `harness._tool_failed`, because the SDK is not.

        A tool that raises does not reach the hooks as a failure: the harness
        turns it into a message for the model, and `on_tool_end` sees a
        perfectly ordinary result. Without being told, every failure would be
        recorded as an answer.
        """
        self._failed.add(call_id)

    def unfinished(self) -> ModelCall | None:
        """The call that was sent and never answered, if there is one.

        A timeout cancels the run inside the provider request, so `on_llm_end`
        never fires and the ordinary path records nothing. That is the run
        whose prompt is worth the most: it is the one nobody can reconstruct
        from the outcome, because there is no outcome.

        What `on_llm_start` knew is real and is kept. What it could not know is
        written as absent — an empty output and no usage — rather than as a
        number that reads like a measurement.

        **Consuming, not reading.** `on_llm_end` clears `_pending` for a call
        that finished, and this clears it for one that did not — because there
        is more than one caller now. The retry loop flushes after every failed
        attempt and the harness flushes again in its `finally`, so a method
        that only *looked* handed the same unanswered call to both: a run that
        made three calls recorded four, and the record over-counting the
        invoice is the same failure as under-counting it.
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
            # How long it hung before being cut off, which is the one number
            # a call that never answered can still supply.
            latency_ms=self._elapsed_ms(),
            **pending,
        )

    def _elapsed_ms(self) -> int:
        return int((time.monotonic() - self._sent_at) * 1000)


def _short(item) -> str:
    """One line per item, tool calls with their arguments spelled out."""
    data = item if isinstance(item, dict) else item.model_dump()
    name = data.get("name")
    if name:  # a tool call — the arguments are the whole point
        return f"{name}({_compact(data.get('arguments'))})"
    content = data.get("content")
    if isinstance(content, list):
        content = " ".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return f"[{data.get('role', data.get('type', '?'))}] {content}"


def _compact(arguments) -> str:
    try:
        return json.dumps(json.loads(arguments), ensure_ascii=False)
    except (TypeError, ValueError):
        return str(arguments)

