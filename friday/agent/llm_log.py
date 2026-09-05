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

from friday.agent.harness import Hooks
from friday.domain.models import ModelCall
from friday.ops.redact import scrub

__all__ = ["LogHooks"]

log = logging.getLogger("friday.llm")


class LogHooks(Hooks):
    """Logs both sides of a call, and hands them to whoever asked for them.

    It collects rather than stores: triage performs no writes, so the caller
    decides whether a call is worth keeping.
    """

    def __init__(self, calls: list | None = None, *, model: str = "") -> None:
        self._calls = calls
        # The configured name, not whatever object the SDK wrapped it in: what
        # matters later is which model we asked for.
        self._model = model
        self._pending: dict = {}
        #: Which agent asked. Known from `on_llm_start`, and needed by
        #: `unfinished()` because there is no `agent` argument there.
        self._agent = ""

    async def on_llm_start(self, context, agent, system_prompt, input_items) -> None:
        log.debug("→ %s system:\n%s", agent.name, system_prompt)
        self._agent = agent.name
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
                **self._pending,
            )
        )
        self._pending = {}


    def unfinished(self) -> ModelCall | None:
        """The call that was sent and never answered, if there is one.

        A timeout cancels the run inside the provider request, so `on_llm_end`
        never fires and the ordinary path records nothing. That is the run
        whose prompt is worth the most: it is the one nobody can reconstruct
        from the outcome, because there is no outcome.

        What `on_llm_start` knew is real and is kept. What it could not know is
        written as absent — an empty output and no usage — rather than as a
        number that reads like a measurement. `on_llm_end` clears `_pending`,
        so a completed call never comes back through here.
        """
        if not self._pending:
            return None
        return ModelCall(
            agent=self._agent,
            model=self._model,
            output="",
            input_tokens=0,
            output_tokens=0,
            **self._pending,
        )


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

