"""Board `what-the-room-already-knows`, ticket 07 — verbatim material
becomes an artifact the build points at (D8).

`friday.text.transform.transform` has always split code out of a message's
prose; what it lifted out was reinserted into `text` and then forgotten —
`InboundEvent.code` reached `record_message` and stopped there, a producer
with no consumer. This is the consumer: each span becomes its own
`Artifact`, stored whole, and the one build that must never see it — the
summariser, through `relevant_messages_in_channel` — gets a reference
instead. Every other reader of a message's text is unaffected, and that is
asserted here as strongly as the redaction itself: a curl that reaches the
task it belongs to must still reach it whole.
"""

from __future__ import annotations

from dataclasses import replace

from conftest import make_event
from friday.domain.conversation import ConversationId
from friday.domain.models import MentionType


CURL = "curl -X GET /pay -H 'x-request-id: c0rr3l4t10n'"


def _with_code(event, *, code, text=None):
    return replace(event, code=code, text=text if text is not None else event.text)


def _curl_event(**kwargs):
    kwargs.setdefault("mention_type", MentionType.DIRECT)
    event = make_event(**kwargs)
    return _with_code(
        event,
        code=(CURL,),
        text=f"lỗi rồi anh ơi\n```\n{CURL}\n```\ntrên production nhé",
    )


async def test_a_curl_becomes_an_artifact_when_the_message_is_recorded(db):
    event = _curl_event(message_id="m1")

    await db.record_message(event)

    (artifact,) = await db.artifacts_for_message(event.provider, event.provider_message_id)
    assert artifact.content == CURL
    assert artifact.channel_id == event.channel_id
    assert artifact.source_message_id == event.provider_message_id
    assert artifact.description  # one line, not empty


async def test_a_message_with_no_code_gets_no_artifact(db):
    event = make_event(message_id="m1", text="anh ơi giúp em với")

    await db.record_message(event)

    assert await db.artifacts_for_message(event.provider, event.provider_message_id) == []


async def test_two_code_spans_become_two_artifacts_in_order(db):
    event = _with_code(
        make_event(message_id="m1", mention_type=MentionType.DIRECT),
        code=("curl -X GET /pay", "curl -X POST /refund"),
        text="```curl -X GET /pay``` rồi ```curl -X POST /refund```",
    )

    await db.record_message(event)

    found = await db.artifacts_for_message(event.provider, event.provider_message_id)
    assert [a.content for a in found] == ["curl -X GET /pay", "curl -X POST /refund"]


async def test_recording_the_same_message_twice_does_not_duplicate_its_artifacts(db):
    """The two delivery paths (gateway, backfill) call `record_message` on
    the same key — CLAUDE.md's dedup rule. An artifact must not be created
    again for a message that was already recorded."""
    event = _curl_event(message_id="m1")

    await db.record_message(event)
    again = await db.record_message(event)

    assert again is False
    found = await db.artifacts_for_message(event.provider, event.provider_message_id)
    assert len(found) == 1


async def test_the_description_never_quotes_the_content_however_short(db):
    """A one-line `curl` is common, and the failure this locks in is real:
    a description built from "the first N characters" of a one-line
    artifact *is* the artifact — exactly the leak D8 exists to close, since
    the description is what the summariser is shown."""
    event = _curl_event(message_id="m1")

    await db.record_message(event)

    (artifact,) = await db.artifacts_for_message(event.provider, event.provider_message_id)
    assert CURL not in artifact.description
    assert "c0rr3l4t10n" not in artifact.description
    assert "\n" not in artifact.description


async def test_the_description_says_how_big_the_artifact_is(db):
    body = "line one\nline two\nline three"
    event = _with_code(
        make_event(message_id="m1", mention_type=MentionType.DIRECT),
        code=(body,),
        text=f"```{body}```",
    )

    await db.record_message(event)

    (artifact,) = await db.artifacts_for_message(event.provider, event.provider_message_id)
    assert "3 line" in artifact.description
    assert str(len(body)) in artifact.description
    assert "line one" not in artifact.description


# --- the split: who sees the content, who sees the reference ---------------


async def test_the_summariser_never_sees_an_artifacts_content(db):
    event = _curl_event(message_id="m1")
    await db.record_message(event)

    (through_summariser,) = await db.relevant_messages_in_channel(
        event.provider, event.channel_id
    )

    assert CURL not in through_summariser.text
    (artifact,) = await db.artifacts_for_message(event.provider, event.provider_message_id)
    assert artifact.id in through_summariser.text
    assert artifact.description in through_summariser.text


async def test_a_message_with_no_code_reaches_the_summariser_unchanged(db):
    event = make_event(message_id="m1", text="cảm ơn anh nhiều")

    await db.record_message(event)

    (through_summariser,) = await db.relevant_messages_in_channel(
        event.provider, event.channel_id
    )
    assert through_summariser.text == event.text


async def test_the_current_tasks_own_build_still_sees_the_curl_whole(db):
    """`original_text_for` is node 0's own read — the extractor's, not the
    summariser's — and it is unaffected: the curl a reporter pasted reaches
    the person who will run it exactly as typed (the ticket's own words)."""
    conversation = ConversationId("fake", "watched")
    task = await db.create_task(
        conversation=conversation, type="api_issue", state="pending",
        confidence=0.9, params={},
    )
    event = _curl_event(message_id="m1")
    await db.record_message(event)
    await db.mark_triaged(event, task.id, decision={"type": "api_issue"})

    said = await db.original_text_for(task.id)

    assert CURL in said


async def test_every_other_reader_of_text_is_unaffected(db):
    """Triage, the responder's tone examples, the Rooms screen — everything
    that reads `messages()`/`relevant_messages()` keeps seeing the curl
    inline, exactly as before this ticket."""
    conversation = ConversationId("fake", "watched")
    event = _curl_event(message_id="m1")
    await db.record_message(event)

    (via_messages,) = await db.messages(conversation)
    (via_relevant,) = await db.relevant_messages(conversation)

    assert CURL in via_messages.text
    assert CURL in via_relevant.text


async def test_a_correlation_id_reaches_the_extractor_character_for_character(db):
    conversation = ConversationId("fake", "watched")
    task = await db.create_task(
        conversation=conversation, type="api_issue", state="pending",
        confidence=0.9, params={},
    )
    event = _curl_event(message_id="m1")
    await db.record_message(event)
    await db.mark_triaged(event, task.id, decision={"type": "api_issue"})

    said = await db.original_text_for(task.id)

    assert "c0rr3l4t10n" in said


async def test_a_correlation_id_reaches_the_params_object_itself(db):
    """The checklist's own words are "reaches the *parameters*", not "reaches
    the text a model is shown" — the two prior tests prove the second; this
    proves the first, through a real `Extractor.run()` and a real `Params`
    instance, the same seam `tests/test_extraction.py` drives its own
    end-to-end tests through."""
    from conftest import ScriptedHarness
    from friday.domain.models import ApiIssueParams
    from friday.extraction import build_extractor
    from tests.test_extraction import _context

    conversation = ConversationId("fake", "watched")
    task = await db.create_task(
        conversation=conversation, type="api_issue", state="pending",
        confidence=0.9, params={},
    )
    event = _curl_event(message_id="m1")
    await db.record_message(event)
    await db.mark_triaged(event, task.id, decision={"type": "api_issue"})
    text = await db.original_text_for(task.id)

    seen_prompts: list[str] = []

    class StubResult:
        # A model that copies the id out of whatever it was shown, the way a
        # correct extraction would — this is what proves the boundary this
        # test is for: not that a model *can* copy correctly (that is a
        # question for the live provider, per CLAUDE.md's eval rule), but
        # that what reaches it, and what a correct copy becomes, survive
        # character for character.
        final_output = '{"correlation_id": "c0rr3l4t10n"}'

    class StubHarness(ScriptedHarness):
        tool_turns = 0
        last_error = None

        async def run(self, prompt, **kwargs):
            seen_prompts.append(prompt)
            return StubResult()

    ext = build_extractor(
        params_cls=ApiIssueParams,
        harness=StubHarness(answers=ApiIssueParams),  # type: ignore[arg-type]
        name="stub",
    )
    filled, _ = await ext.run(_context(text, ApiIssueParams), task_id=task.id)

    assert "c0rr3l4t10n" in seen_prompts[0], (
        "the id never reached the model's own input"
    )
    assert filled.correlation_id == "c0rr3l4t10n"


# --- the reader of an artifact cannot itself produce one --------------------


async def test_redaction_runs_once_per_message_never_on_an_artifacts_own_content(
    db, monkeypatch
):
    """D8: 'the reader of an artifact may not itself produce one' — otherwise
    a large artifact read back into a build is spilled to a second artifact,
    and so on. `redact` is only ever called from `_record_artifacts`, once,
    on the message that produced the code — never re-run on an artifact's
    `content`. Counted directly rather than inferred from the absence of a
    second artifact, which mutation testing would not distinguish from
    "never called at all"."""
    import friday.store.db as db_module

    calls = []
    real_redact = db_module.redact

    def counting(raw, refs):
        calls.append(raw)
        return real_redact(raw, refs)

    monkeypatch.setattr(db_module, "redact", counting)

    event = _curl_event(message_id="m1")
    await db.record_message(event)

    assert len(calls) == 1
    assert calls[0] == event.text


async def test_a_split_that_disagrees_on_re_read_degrades_rather_than_crashes(
    db, monkeypatch
):
    """`redact` re-splits already-restored text, and a code span containing
    its own literal triple backtick could — code review found no input that
    actually does this, after both a hand-built and a 20,000-trial random
    search, but the split's own regex gives no proof it never can — make
    that re-split disagree in count with the original. Rather than let the
    resulting `ValueError` surface with the message row already committed,
    `_record_artifacts` catches it: the message is still recorded, with no
    artifacts and `redacted_text` left `NULL`, falling back to `text`
    everywhere including the summariser — the same shape of gap a message
    with no code at all already has, not a new failure mode."""
    import friday.store.db as db_module

    def always_disagrees(raw, refs):
        raise ValueError("simulated: the second split found a different count")

    monkeypatch.setattr(db_module, "redact", always_disagrees)

    event = _curl_event(message_id="m1")
    recorded = await db.record_message(event)

    assert recorded is True
    assert await db.artifacts_for_message(event.provider, event.provider_message_id) == []
    (through_summariser,) = await db.relevant_messages_in_channel(
        event.provider, event.channel_id
    )
    assert through_summariser.text == event.text  # the known, narrow fallback
