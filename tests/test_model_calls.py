"""Ticket 13 — what was sent to a model and what came back.

"Why did it classify that as an access request?" is always asked *after* the
fact. A log line answers it while the process is alive and never again.
"""

from __future__ import annotations

from friday.sdk.testing import FunctionModel
from dataclasses import asdict

from friday.sdk.testing import ScriptedModel, function_call

from conftest import captured, make_event
from friday.kernel.config import AgentConfig
from friday.kernel.triage import Triage

CONFIG = AgentConfig(
    name="triage", api_key="sk-secret", base_url="https://example.invalid/v1",
    model="test-model", options={"confidence_threshold": 0.7},
)


def api_issue_call():
    return function_call(
        "answer", {"type": "devops.api_issue", "confidence": 0.9}, call_id="1"
    )


async def test_a_run_reports_both_sides_of_the_call():
    calls: list = []

    async def sink(call) -> None:
        calls.append(call)

    triage = Triage(
        config=CONFIG, model=ScriptedModel([[api_issue_call()]]), record=sink
    )

    await triage.decide(make_event(text="checkout is 500ing"))

    # Two entries now: the prompt, and the `answer` call the classification
    # arrives as. This test is about the first.
    (call,) = [c for c in calls if getattr(c, "prompt", None) is not None]
    assert call.agent == "triage"
    assert call.model == "test-model"
    assert "You decide what a chat message is" in call.system_prompt
    assert "checkout is 500ing" in call.prompt
    assert "devops.api_issue" in call.output
    # ScriptedModel reports no usage; the plumbing is what is pinned here.
    # A live run fills these in — verified against MiniMax at 1037 in / 171 out.
    assert isinstance(call.input_tokens, int)


async def test_triage_still_writes_nothing_itself():
    """It reaches no store. Built without a sink, it records nowhere and still
    decides — which is what makes it testable without a database."""
    triage = Triage(config=CONFIG, model=ScriptedModel([[api_issue_call()]]))

    outcome = await triage.decide(make_event())

    assert outcome.type == "devops.api_issue"


def _recording(db):
    """The composition root's sink, in miniature: one seam, two kinds of row.

    A tool call and a model call travel the same way for the reason D1 gives —
    a second seam is a second thing to forget — and part paths at the store,
    which is the only place that knows there are two tables.
    """
    from friday.kernel.domain.models import ModelCall

    async def record(entry) -> None:
        if isinstance(entry, ModelCall):
            await db.record_model_call(**asdict(entry))
        else:
            await db.record_tool_call(**asdict(entry))

    return record


async def test_a_decision_can_be_traced_back_to_the_call_that_made_it(
    inbox, provider, db
):
    """The point of storing them: from the message, to the decision, to the
    prompt and the tool call behind it."""
    from friday.kernel.triage.runner import TriageRunner

    provider.emit(make_event(message_id="10", text="checkout is 500ing"))
    await captured(inbox)
    triage = Triage(
        config=CONFIG,
        model=ScriptedModel([[api_issue_call()]]),
        record=_recording(db),
    )

    await TriageRunner(db=db, triage=triage, confidence_threshold=0.7).run_once()

    (decision,) = await db.decisions()
    (call,) = await db.model_calls(message_id=decision["message_id"])
    assert "checkout is 500ing" in call.prompt
    assert "devops.api_issue" in call.output


async def test_a_credential_never_reaches_storage(inbox, provider, db):
    """The Discord user token is unscoped account access. A stored prompt is a
    place it must never turn up, and the realistic leak is an exception."""
    from friday.kernel.triage.runner import TriageRunner

    def _leak(messages, info):
        raise RuntimeError("401 from Bearer sk-abcdefghijklmnopqrstuvwxyz012345")

    leaking = FunctionModel(_leak, model_name="test-model")

    provider.emit(make_event(message_id="10"))
    await captured(inbox)

    await TriageRunner(
        db=db, triage=Triage(config=CONFIG, model=leaking), confidence_threshold=0.7
    ).run_once()

    (decision,) = await db.decisions()
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in str(decision["params"])
    assert "REDACTED" in str(decision["params"])


async def test_old_calls_are_trimmed(db):
    """A container that never restarts would otherwise fill its volume with
    prompts nobody will read."""
    from datetime import datetime, timedelta, timezone

    old = datetime.now(timezone.utc) - timedelta(days=30)
    for age, message_id in ((old, "old"), (datetime.now(timezone.utc), "new")):
        await db.record_model_call(
            message_id=message_id, agent="triage", model="m", system_prompt="s",
            prompt="p", output="o", input_tokens=1, output_tokens=1, created_at=age,
        )

    removed = await db.trim_model_calls(keep_days=14)

    assert removed == 1
    assert [c.message_id for c in await db.model_calls()] == ["new"]
