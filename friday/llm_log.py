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

from agents import AgentHooks

__all__ = ["LogHooks"]

log = logging.getLogger("friday.llm")


class LogHooks(AgentHooks):
    async def on_llm_start(self, context, agent, system_prompt, input_items) -> None:
        log.debug("→ %s system:\n%s", agent.name, system_prompt)
        for item in input_items:
            log.debug("→ %s %s", agent.name, _short(item))

    async def on_llm_end(self, context, agent, response) -> None:
        for item in response.output:
            log.debug("← %s %s", agent.name, _short(item))
        usage = response.usage
        log.debug(
            "← %s usage: %d in / %d out",
            agent.name,
            usage.input_tokens,
            usage.output_tokens,
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
