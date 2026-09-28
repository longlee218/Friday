"""The ops plugin: the `ops.request_permission` task type.

Lifted out of the kernel in build-the-spine ticket 02, so the core registers no
task type of its own (`skip` is not one). Like the backend's `answer_question`, its
whole graph is the shared node-0 (extract, then ask for what is missing or hand
over), built through `api.caps.simple_dag` — so it imports `friday.sdk` only.
"""

from __future__ import annotations

from typing import Any

from friday.sdk import Plugin, TaskTypeSpec
from plugins.ops.params import AccessRequestParams

__all__ = ["PLUGIN", "register"]

TASK_TYPE = "ops.request_permission"


def register(api: Any) -> None:
    """Contribute the `ops.request_permission` task type. No memory kinds and no
    model node of its own; the `graph` builder is deferred and closes over `api`
    so it runs only in the task-type lifecycle, where `api.caps` exists."""
    api.task_type(
        TaskTypeSpec(
            name=TASK_TYPE,
            params=AccessRequestParams,
            graph=lambda _deps: api.caps.simple_dag(TASK_TYPE, AccessRequestParams),
        )
    )


PLUGIN = Plugin(id="ops", register=register)
