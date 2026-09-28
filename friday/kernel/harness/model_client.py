"""What the harness sends a provider: the client, and the answer shape as a
JSON schema.

No `pydantic_ai` import: `harness.py` wraps the client in the SDK's chat model.
"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields
from typing import Any

from openai import AsyncOpenAI
from pydantic import TypeAdapter

from friday.kernel.config import AgentConfig


def _client(config: AgentConfig) -> AsyncOpenAI:
    """Chat Completions rather than the Responses API, so `base_url`, `api_key`
    and `model` are the whole of what it takes to use a different
    OpenAI-compatible provider.

    The client is built here rather than left to the provider's default so its
    retries can be switched off (retrying is `_attempts`'s job, where it can be
    seen and counted). Its timeout is the client's own default — the one
    unbounded wait left, and the operator's call (board `domains-plug-in`,
    ticket 17). `harness.py` wraps it in the SDK's chat model.
    """
    return AsyncOpenAI(
        base_url=config.base_url,
        api_key=config.api_key,
        max_retries=0,
    )


#: What the model calls to answer. One name for every shape, because an agent
#: built with `answers=` has exactly one way to finish.
ANSWER = "answer"


def _answer_params(schema: type) -> dict[str, Any]:
    """The answer shape as a JSON schema, with each field's own `doc` on it.

    Kept as the canonical description of the shape for the model — the same
    `doc` metadata `describe` renders into the prompt — and read by
    `tests/test_tools.py`, which requires every field of every tool to carry a
    description. The class's own docstring is developer prose and is dropped:
    what the model needs about the shape as a whole is on the tool's
    description, generated from the fields.
    """
    described = TypeAdapter(schema).json_schema()
    described.pop("description", None)
    properties = described.get("properties", {})
    for field in dataclass_fields(schema):
        doc = field.metadata.get("doc")
        if doc and field.name in properties:
            properties[field.name].setdefault("description", doc)
    return described
