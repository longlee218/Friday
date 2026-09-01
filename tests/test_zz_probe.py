"""TEMPORARY QA PROBE — delete before finishing."""
from __future__ import annotations

import pytest

from friday.conversation import ConversationId
from friday.workflows import Ask, Park
from friday.workflows.runner import WorkflowRunner


async def make_task(db, **params):
    return await db.create_task(
        conversation=ConversationId("fake", "watched"),
        type="api_issue",
        state="pending",
        confidence=0.9,
        params={"summary": "checkout 500", "environment": None,
                "correlation_id": None, "curl": None, **params},
    )


async def test_probe_malformed_correlation_id_still_validated(db):
    """Pre-change, plan() validated ApiIssueParams._RULES before dispatch.
    Does the DAG path still catch a non-uuid correlation_id?"""
    await make_task(db, correlation_id="not-a-uuid")
    acted = await WorkflowRunner(db=db, auto_ask=True).run_once()
    rows = await db.outbound()
    print("\nPROBE validation: state=", acted[0].state,
          " outbound=", [(r.kind, r.text) for r in rows])


async def test_probe_bad_environment_still_validated(db):
    await make_task(db, environment="prd", correlation_id="abcdef01-2345-6789-abcd-ef0123456789")
    acted = await WorkflowRunner(db=db, auto_ask=True).run_once()
    rows = await db.outbound()
    print("\nPROBE env validation: state=", acted[0].state,
          " outbound=", [(r.kind, r.text) for r in rows])


async def test_probe_extractor_is_reached_on_the_dag_path(db, monkeypatch):
    """Does the graph path run the registered LLM extractor at all?"""
    calls = []

    import friday.workflows as wf

    async def spy(task_type, text):
        calls.append((task_type, text))
        return None

    monkeypatch.setattr(wf, "_extract", spy)
    await make_task(db)
    await WorkflowRunner(db=db, auto_ask=True).run_once()
    print("\nPROBE extractor calls on DAG path:", calls)


async def test_probe_original_text_for_is_reached(db):
    """The DAG path never asks for the original text either."""
    seen = []
    orig = db.original_text_for

    async def spy(task_id):
        seen.append(task_id)
        return await orig(task_id)

    db.original_text_for = spy  # type: ignore[method-assign]
    await make_task(db)
    await WorkflowRunner(db=db, auto_ask=True).run_once()
    print("\nPROBE original_text_for calls:", seen)
