"""The docs plugin: the `docs.doc_question` persona.

The second persona (ticket 15), and the proof G2 holds — a new persona is a new
plugin, added with **zero kernel diff**. `doc_question` has no investigation past
node 0, so this plugin is about as small as a plugin gets: `params.py` and this
file. Its whole graph is the shared node-0 (extract, then ask for what is missing
or hand over), which it builds through `api.caps.simple_dag` rather than importing
`friday.dag` — so, like `plugins/devops`, it imports `friday.sdk` only.

A small plugin may be `__init__.py` and `params.py` alone (DESIGN-v2 §4.1); this
is that shape.
"""

from __future__ import annotations

from typing import Any

from friday.sdk import Plugin, TaskTypeSpec
from plugins.docs.params import DocQuestionParams

__all__ = ["PLUGIN", "register"]

TASK_TYPE = "docs.doc_question"


def register(api: Any) -> None:
    """Contribute the `docs.doc_question` task type. No memory kinds and no
    model node of its own: its graph is the shared simple node-0, built from the
    boot caps. The `graph` builder is deferred and closes over `api` so it runs
    only in the task-type lifecycle, where `api.caps` exists."""
    api.task_type(
        TaskTypeSpec(
            name=TASK_TYPE,
            params=DocQuestionParams,
            graph=lambda _deps: api.caps.simple_dag(TASK_TYPE, DocQuestionParams),
        )
    )


PLUGIN = Plugin(id="docs", register=register)
