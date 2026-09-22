"""Test doubles for the agent harness, behind Friday's own name (seam S2).

The suite drove the vendor's scripted transport — `agents.testing.ScriptedModel`
and friends — directly, from fourteen files. Named here once instead, so that
moving the harness off `openai-agents` and onto Pydantic AI (ticket 05) changes
this one module rather than every test that scripts a model.

Today these are the `openai-agents` doubles, re-exported unchanged: this slice
moves the tests onto the seam with no behaviour change. When the harness swaps
to Pydantic AI, this module's insides become the Pydantic-AI equivalents
(`FunctionModel`, `capture_run_messages`, `ToolContext` as an alias of
`RunContext`) with the same names and call shapes, and the tests do not move
again.

This is the one place under `friday/` besides `harness.py` that may import the
vendor SDK — everything an agent is built from still takes its names through
`harness.py`; these are the names a *test* builds a scripted run from.
"""

from __future__ import annotations

from agents import Agent
from agents.items import ModelResponse
from agents.models.chatcmpl_converter import Converter
from agents.models.interface import Model
from agents.testing import ScriptedModel, assistant_message, function_call
from agents.tool_context import ToolContext
from agents.tracing import get_trace_provider
from agents.usage import Usage

__all__ = [
    "Agent",
    "Converter",
    "Model",
    "ModelResponse",
    "ScriptedModel",
    "ToolContext",
    "Usage",
    "assistant_message",
    "function_call",
    "get_trace_provider",
]
