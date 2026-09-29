"""Candidate memories — a `Database` mixin: an agent proposes, the operator marks."""

from __future__ import annotations

from friday.store._common import *


class MemoryCandidatesRepo:
    # ---- candidate memories (board `what-the-room-already-knows`,
    # ticket 12, D19, D20) --------------------------------------------------
    #
    # A second producer of memory, beside `memory_add`'s automatic write:
    # an agent proposes, and nothing reads the proposal back until the
    # operator marks it. `MemoryCandidate` is its own table rather than a
    # status on `memories` so that no reader of `memories` has to remember to
    # exclude a pending row — the guarantee D20 asks to hold structurally.

    async def propose_memory(
        self, state: FridayState, text: str, *, kind: str = memory_kinds.VOICE
    ) -> MemoryCandidate:
        """Stage a memory for the operator's mark rather than writing it.

        Not capped the way `memory_add` is: `MEMORY_PER_CHANNEL` bounds what
        a room's *memory* holds, and a candidate is not memory yet — the cap
        is enforced once, when `resolve_candidates_for_message` accepts one
        and calls `memory_add` for real.

        If `state.message_id` already carries a verdict — the operator
        marked this task's classification before this call ran — resolved
        immediately rather than left `PENDING` with no future reaction to
        ever trigger it: the reaction that would have resolved it already
        happened.
        """
        candidate_id = _memory_id()
        now = _now()
        async with self._sessions.begin() as session:
            row = schema.MemoryCandidate(
                id=candidate_id,
                channel_id=state.channel_id,
                agent=state.agent,
                text=text[: self.TEXT_CHARS],
                kind=kind,
                task_id=state.task_id,
                source_message_id=state.message_id,
                status=CandidateStatus.PENDING,
                proposed_at=now,
            )
            session.add(row)
            await session.flush()
            candidate = _candidate(row)

        if state.message_id is None:
            return candidate
        verdict = await self.verdict_for(
            provider="discord", provider_message_id=state.message_id
        )
        if verdict is None:
            return candidate
        mark, by = verdict
        return await self._resolve_candidate(candidate, mark=mark, by=by)

    async def resolve_candidates_for_message(
        self, *, provider_message_id: str, mark: str, by: str
    ) -> list[MemoryCandidate]:
        """Every `PENDING` candidate this message resolves, marked accepted
        or rejected by the same gesture that already confirms this message's
        classification (D19: "there is one thing to learn", not two) — call
        this alongside `record_verdict`, from the same reaction handler,
        never on its own.

        Accepting writes the candidate through `memory_add`, so ticket 11's
        refusal and `MEMORY_PER_CHANNEL`'s cap both still apply; either one
        refusing leaves the candidate `ACCEPTED` with `memory_id` still
        `None` rather than raising into a live reaction handler — the
        operator's judgement is recorded either way, the same way a
        `Verdict` records what they said independent of what a later pass
        does with it.
        """
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.MemoryCandidate).where(
                    schema.MemoryCandidate.source_message_id == provider_message_id,
                    schema.MemoryCandidate.status == CandidateStatus.PENDING,
                )
            )
            pending = [_candidate(row) for row in rows]
        return [
            await self._resolve_candidate(candidate, mark=mark, by=by)
            for candidate in pending
        ]

    async def _resolve_candidate(
        self, candidate: MemoryCandidate, *, mark: str, by: str
    ) -> MemoryCandidate:
        accepted = mark == "right"
        memory_id = None
        if accepted:
            state = FridayState(
                channel_id=candidate.channel_id,
                task_id=candidate.task_id,
                agent=candidate.agent,
                message_id=candidate.source_message_id,
            )
            # The candidate's text was guarded at propose time
            # (`friday.kernel.memory.write.propose`), so this resolve reuses an
            # already-checked line; the store's `memory_add` only refuses it for
            # the channel's cap (`None`).
            written = await self.memory_add(state, candidate.text, kind=candidate.kind)
            if written is None:
                log.warning(
                    "candidate %s accepted but not written — channel %s is at its cap",
                    candidate.id,
                    candidate.channel_id,
                )
            else:
                memory_id = written.id
        async with self._sessions.begin() as session:
            row = await session.get(schema.MemoryCandidate, candidate.id)
            assert row is not None, f"candidate {candidate.id} vanished mid-resolve"
            row.status = (
                CandidateStatus.ACCEPTED if accepted else CandidateStatus.REJECTED
            )
            row.resolved_at = _now()
            row.resolved_by = by
            row.memory_id = memory_id
            await session.flush()
            return _candidate(row)

    async def candidates_for_channel(
        self, channel_id: str, *, limit: int = 200
    ) -> list[MemoryCandidate]:
        """Every candidate this channel has proposed, newest first, pending
        or resolved — the operator's own view, and the "place for a person
        to look" D19 says the old staging tier never had. A rejected
        candidate stays here rather than being deleted, so the operator can
        see what was proposed and turned down."""
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.MemoryCandidate)
                .where(schema.MemoryCandidate.channel_id == channel_id)
                .order_by(schema.MemoryCandidate.proposed_at.desc())
                .limit(limit)
            )
            return [_candidate(row) for row in rows]
