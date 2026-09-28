"""The model port: what a plugin's model node is handed to call a model.

A graph node that calls a model does not build the harness — the harness
imports the agent SDK, which a plugin may not. Instead the composition root
builds the concrete `Harness` (from the plugin's own instructions and answer
shape) and injects it; the node types it against this `Model` port and touches
only `run_structured` and `last_error`, which is the whole surface a structured
answer needs.

`friday/kernel/harness/harness.py`'s `Harness` satisfies this structurally, so nothing
implements it explicitly — the port exists so a plugin node names a contract
rather than the concrete class above the sdk line.
"""

from __future__ import annotations

from typing import Any, Protocol

__all__ = ["Model"]


class Model(Protocol):
    """A built model harness, ready to run — the minimum a plugin node calls.

    `run_structured` returns an instance of the harness's declared answer
    shape, or `None` when there was no answer that fits; `last_error` carries
    why, scrubbed. Everything else the harness does — budget, redaction,
    recording, the one correction turn — is the harness's own and needs no
    surface here.
    """

    last_error: str | None

    async def run_structured(
        self,
        prompt: str,
        *,
        context: Any = None,
        extra_turns: int = 0,
        message_id: str | None = None,
        task_id: int | None = None,
        node: str | None = None,
    ) -> Any | None: ...
