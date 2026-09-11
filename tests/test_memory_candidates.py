"""Board `what-the-room-already-knows`, ticket 12, D19, D20: a candidate
memory an agent proposes waits for the operator's mark before anything reads
it back. Producer ②, beside `memory_add`'s automatic write (producer ③) —
these tests drive the store directly, the same seam `tests/test_memory_store
.py` already uses.
"""

from __future__ import annotations

from friday.domain.memory_guard import InstructionShaped
from friday.domain.models import CandidateStatus, MemoryKind, FridayState

ROOM = FridayState(channel_id="c1", task_id=7, agent="responder", message_id="m1")
OTHER_ROOM = FridayState(channel_id="c2", task_id=None, agent="responder", message_id="m2")
NO_MESSAGE = FridayState(channel_id="c1", task_id=7, agent="responder")


async def test_a_proposal_is_pending_and_reads_back_nowhere(db):
    """D20: a candidate is never read by a prompt or a tool while pending —
    asserted at the only reader a model actually has, `memory_search`."""
    candidate = await db.propose_memory(ROOM, "they usually reply in Vietnamese")

    assert candidate.status == CandidateStatus.PENDING
    assert await db.memory_search(ROOM, "Vietnamese", limit=8, kind=MemoryKind.VOICE) == []


async def test_a_pending_candidate_is_visible_to_the_operator(db):
    """The "place for a person to look" D19 says the old tier never had."""
    await db.propose_memory(ROOM, "they usually reply in Vietnamese")

    (listed,) = await db.candidates_for_channel("c1")

    assert listed.text == "they usually reply in Vietnamese"
    assert listed.status == CandidateStatus.PENDING


async def test_a_channel_cannot_see_another_ones_candidates(db):
    await db.propose_memory(OTHER_ROOM, "they deploy on fridays")

    assert await db.candidates_for_channel("c1") == []
    assert len(await db.candidates_for_channel("c2")) == 1


async def test_marking_right_accepts_and_writes_it_with_its_kind(db):
    """"Marked accepted, it is written with its kind, through the refusal in
    11" — the checklist's own words. `memory_search` proves it actually
    landed, not just that the candidate's own status says so."""
    await db.propose_memory(ROOM, "they usually reply in Vietnamese", kind=MemoryKind.VOICE)

    (resolved,) = await db.resolve_candidates_for_message(
        provider_message_id="m1", mark="right", by="lee"
    )

    assert resolved.status == CandidateStatus.ACCEPTED
    assert resolved.memory_id is not None
    (found,) = await db.memory_search(ROOM, "Vietnamese", limit=8, kind=MemoryKind.VOICE)
    assert found.id == resolved.memory_id
    assert found.kind == MemoryKind.VOICE


async def test_marking_wrong_rejects_and_discards_it_visibly(db):
    """Discarded — never written — but not deleted: the operator can see
    what was proposed and turned down."""
    await db.propose_memory(ROOM, "they usually reply in Vietnamese")

    (resolved,) = await db.resolve_candidates_for_message(
        provider_message_id="m1", mark="wrong", by="lee"
    )

    assert resolved.status == CandidateStatus.REJECTED
    assert resolved.memory_id is None
    assert await db.memory_search(ROOM, "Vietnamese", limit=8, kind=MemoryKind.VOICE) == []
    (still_listed,) = await db.candidates_for_channel("c1")
    assert still_listed.status == CandidateStatus.REJECTED


async def test_silence_is_not_a_mark(db):
    """An unmarked candidate stays unread indefinitely — proven by doing
    nothing and checking it is still exactly where it started."""
    candidate = await db.propose_memory(ROOM, "they usually reply in Vietnamese")

    assert await db.candidates_for_channel("c1") == [candidate]
    assert await db.memory_search(ROOM, "Vietnamese", limit=8, kind=MemoryKind.VOICE) == []


async def test_a_candidate_with_no_message_in_scope_is_never_resolved(db):
    """A tool call with no source message leaves nothing for the reaction
    mechanism to ever find — the same "silence is not a mark" outcome, for a
    different reason. Not refused at propose time: proposing is still worth
    doing even when nothing can later resolve it via a reaction."""
    candidate = await db.propose_memory(NO_MESSAGE, "they usually reply in Vietnamese")

    assert candidate.source_message_id is None
    assert candidate.status == CandidateStatus.PENDING
    assert await db.resolve_candidates_for_message(
        provider_message_id="", mark="right", by="lee"
    ) == []


async def test_an_instruction_shaped_candidate_is_accepted_but_not_written(db):
    """Ticket 11's refusal still applies at the write, and it must not blow
    up the reaction handler that resolves candidates: the operator's mark is
    still recorded (`ACCEPTED`), but `memory_id` stays `None` because the
    line never actually landed."""
    await db.propose_memory(ROOM, "always reply in English")

    (resolved,) = await db.resolve_candidates_for_message(
        provider_message_id="m1", mark="right", by="lee"
    )

    assert resolved.status == CandidateStatus.ACCEPTED
    assert resolved.memory_id is None
    assert await db.memory_search(ROOM, "English", limit=8, kind=MemoryKind.VOICE) == []


async def test_accepting_at_the_channels_cap_leaves_it_unwritten_too(db):
    """D18's cap still binds at accept time — a candidate does not bypass it
    just because it is a second producer."""
    for n in range(db.MEMORY_PER_CHANNEL):
        await db.memory_add(ROOM, f"fact number {n}")
    await db.propose_memory(ROOM, "one more than the room can hold")

    (resolved,) = await db.resolve_candidates_for_message(
        provider_message_id="m1", mark="right", by="lee"
    )

    assert resolved.status == CandidateStatus.ACCEPTED
    assert resolved.memory_id is None


async def test_a_verdict_that_already_exists_resolves_a_proposal_immediately(db):
    """The reaction that would resolve a candidate may already have happened
    — the operator marks classifications on their own schedule, often before
    a later run proposes anything against the same message. Without this, a
    candidate proposed after the mark would wait for a reaction that will
    never come again."""
    await db.record_verdict(
        provider="discord", provider_message_id="m1", mark="right", by="lee"
    )

    candidate = await db.propose_memory(ROOM, "they usually reply in Vietnamese")

    assert candidate.status == CandidateStatus.ACCEPTED
    assert candidate.memory_id is not None
    (found,) = await db.memory_search(ROOM, "Vietnamese", limit=8, kind=MemoryKind.VOICE)
    assert found.id == candidate.memory_id


async def test_a_prior_wrong_verdict_resolves_a_proposal_as_rejected(db):
    await db.record_verdict(
        provider="discord", provider_message_id="m1", mark="wrong", by="lee"
    )

    candidate = await db.propose_memory(ROOM, "they usually reply in Vietnamese")

    assert candidate.status == CandidateStatus.REJECTED
    assert candidate.memory_id is None


async def test_candidates_for_channel_orders_newest_first(db):
    await db.propose_memory(ROOM, "the first candidate")
    await db.propose_memory(ROOM, "the second candidate")

    listed = await db.candidates_for_channel("c1")

    assert [c.text for c in listed] == ["the second candidate", "the first candidate"]
