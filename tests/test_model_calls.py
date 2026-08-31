"""Ticket 13 — what was sent to a model and what came back.

"Why did it classify that as an access request?" is always asked *after* the
fact. A log line answers it while the process is alive and never again.
"""

from __future__ import annotations

from agents.models.interface import Model
from agents.testing import ScriptedModel, function_call

from conftest import captured, make_event
from friday.config import AgentConfig
from friday.triage import Triage

CONFIG = AgentConfig(
    name="triage", api_key="sk-secret", base_url="https://example.invalid/v1",
    model="test-model", options={"confidence_threshold": 0.7},
)


def api_issue_call():
    return function_call("create_api_issue_task", {
        "confidence": 0.9, "summary": "checkout 500", "environment": "production",
        "correlation_id": None, "curl": None,
    }, call_id="1")


async def test_a_run_reports_both_sides_of_the_call():
    triage = Triage(config=CONFIG, model=ScriptedModel([[api_issue_call()]]))
    calls: list = []

    await triage.decide(make_event(text="checkout is 500ing"), calls=calls)

    (call,) = calls
    assert call.agent == "triage"
    assert call.model == "test-model"
    assert "You triage chat messages" in call.system_prompt
    assert "checkout is 500ing" in call.prompt
    assert "create_api_issue_task" in call.output
    # ScriptedModel reports no usage; the plumbing is what is pinned here.
    # A live run fills these in — verified against MiniMax at 1037 in / 171 out.
    assert isinstance(call.input_tokens, int)


async def test_triage_still_writes_nothing_itself():
    """It reports the call; the caller decides whether to keep it."""
    triage = Triage(config=CONFIG, model=ScriptedModel([[api_issue_call()]]))

    outcome = await triage.decide(make_event(), calls=[])

    assert outcome.type == "api_issue"


async def test_a_decision_can_be_traced_back_to_the_call_that_made_it(
    inbox, provider, db
):
    """The point of storing them: from the message, to the decision, to the
    prompt and the tool call behind it."""
    from friday.triage.runner import TriageRunner

    provider.emit(make_event(message_id="10", text="checkout is 500ing"))
    await captured(inbox)
    triage = Triage(config=CONFIG, model=ScriptedModel([[api_issue_call()]]))

    await TriageRunner(db=db, triage=triage, confidence_threshold=0.7).run_once()

    (decision,) = await db.decisions()
    (call,) = await db.model_calls(message_id=decision["message_id"])
    assert "checkout is 500ing" in call.prompt
    assert "create_api_issue_task" in call.output


async def test_a_credential_never_reaches_storage(inbox, provider, db):
    """The Discord user token is unscoped account access. A stored prompt is a
    place it must never turn up, and the realistic leak is an exception."""
    from friday.triage.runner import TriageRunner

    class Leaking(Model):
        async def get_response(self, *a, **kw):
            raise RuntimeError("401 from Bearer sk-abcdefghijklmnopqrstuvwxyz012345")

        def stream_response(self, *a, **kw):
            raise NotImplementedError

    provider.emit(make_event(message_id="10"))
    await captured(inbox)

    await TriageRunner(
        db=db, triage=Triage(config=CONFIG, model=Leaking()), confidence_threshold=0.7
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
