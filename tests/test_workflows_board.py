"""Ticket 08 — the workflow panel's data, end to end short of a browser.

The operator watches running, queued, succeeded and failed workflows on the
existing board. The backend half is: `/api/workflows` maps `DBOSClient.
list_workflows` out of DBOS's vocabulary, and a `workflow` event on the SSE bus
(one per node that finishes) tells the panel to refresh. This pins the shape the
page reads, the status mapping, the SSE progress event, and the empty answer
when DBOS is not running.
"""

from __future__ import annotations

import asyncio
import pathlib
import re
from types import SimpleNamespace

from conftest import BoardClient

from friday.kernel.dag import adapter
from friday.kernel.ops import events as events_module

TYPES = pathlib.Path(__file__).resolve().parents[1] / "web" / "src" / "api-types.ts"


def declared(interface: str) -> set[str]:
    body = re.search(
        rf"export interface {interface} \{{(.*?)\n\}}", TYPES.read_text(), re.DOTALL
    )
    assert body, f"web/src/api-types.ts declares no interface {interface}"
    return set(re.findall(r"^\s{2}(\w+)[?]?:", body.group(1), re.MULTILINE))


def _status(**kw) -> SimpleNamespace:
    """A stand-in for a DBOS `WorkflowStatus` — only the fields the view reads."""
    base = dict(
        workflow_id="wf-1",
        name="_run_graph",
        status="PENDING",
        queue_name=None,
        created_at=1_700_000_000_000,
        updated_at=1_700_000_001_000,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_the_view_matches_the_page_interface():
    """The keys the adapter emits are exactly what `web/src/api-types.ts`
    declares — the same contract guard the other routes have, applied to the
    view builder because a route with DBOS down emits an empty list."""
    assert set(adapter._workflow_view(_status())) == declared("Workflow")


def test_dbos_vocabulary_maps_to_the_four_board_words():
    cases = {
        "PENDING": "running",
        "ENQUEUED": "queued",
        "DELAYED": "queued",
        "SUCCESS": "succeeded",
        "ERROR": "failed",
        "CANCELLED": "failed",
        "MAX_RECOVERY_ATTEMPTS_EXCEEDED": "failed",
    }
    for dbos_status, board_word in cases.items():
        assert (
            adapter._workflow_view(_status(status=dbos_status))["status"] == board_word
        )


def test_epoch_millis_become_iso_strings():
    """The board's `ago`/`shortTime` read ISO, like every other time it
    renders; DBOS carries epoch ms."""
    view = adapter._workflow_view(_status(created_at=1_700_000_000_000))
    assert view["created_at"].startswith("2023-11-14T")
    assert adapter._workflow_view(_status(updated_at=None))["updated_at"] is None


async def test_the_route_is_empty_when_dbos_is_not_running(db):
    """A board opened before the first workflow, or a test that never launched
    DBOS, gets an empty list rather than a 500."""
    client = BoardClient(build_api_for(db))
    assert client.get("/api/workflows").json() == []


async def test_a_finished_node_publishes_a_workflow_event(db):
    """`record_node_run` is the live progress signal — one per node that ends —
    and it publishes on the same bus the model/tool calls use, so the panel
    refreshes its DBOS snapshot."""
    events_module.reset_bus_for_tests()
    try:
        queue, _ = await events_module.get_bus().subscribe()
        await db.record_node_run(
            task_id=7,
            dag_name="backend.trace_problem",
            node="resolve",
            attempt=1,
            status="ok",
            reason="",
            duration_ms=12,
        )
        event = await asyncio.wait_for(queue.get(), 1.0)
        assert event.type == "workflow"
        assert event.payload == {
            "task_id": 7,
            "dag_name": "backend.trace_problem",
            "node": "resolve",
            "status": "ok",
            "attempt": 1,
        }
    finally:
        events_module.reset_bus_for_tests()


def build_api_for(db):
    from friday.kernel.ops.api import build_api

    return build_api(
        db=db, provider_status=lambda: "connected", confidence_threshold=0.7
    )
