"""The verdicts repository — a `Database` mixin (ticket 16)."""

from __future__ import annotations

from friday.store._common import *


class VerdictsRepo:
    # ---- what the operator said about a classification -------------------

    async def record_verdict(
        self,
        *,
        provider: str,
        provider_message_id: str,
        mark: str,
        by: str,
    ) -> None:
        """Record that the operator marked this classification right or wrong.

        Upserted, so changing their mind replaces rather than accumulates.
        Marking the same thing twice leaves one row saying what they currently
        think, which is the only thing anything downstream wants to know.
        """
        statement = insert(schema.Verdict).values(
            provider=provider,
            provider_message_id=provider_message_id,
            mark=mark,
            marked_by=by,
            marked_at=_now(),
        )
        async with self._sessions.begin() as session:
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[
                        schema.Verdict.provider,
                        schema.Verdict.provider_message_id,
                    ],
                    set_={
                        "mark": statement.excluded.mark,
                        "marked_by": statement.excluded.marked_by,
                        "marked_at": statement.excluded.marked_at,
                    },
                )
            )

    async def clear_verdict(self, *, provider: str, provider_message_id: str) -> None:
        """The operator took the mark back. The row goes with it.

        Deleted rather than recorded as a third state, because "unmarked" and
        "never marked" mean the same thing to everything downstream: nobody
        is vouching for this one.
        """
        async with self._sessions.begin() as session:
            await session.execute(
                delete(schema.Verdict).where(
                    schema.Verdict.provider == provider,
                    schema.Verdict.provider_message_id == provider_message_id,
                )
            )

    async def verdict_for(
        self, *, provider: str, provider_message_id: str
    ) -> tuple[str, str] | None:
        """The mark and who left it, or None if nobody has."""
        async with self._sessions() as session:
            row = await session.get(schema.Verdict, (provider, provider_message_id))
            return (row.mark, row.marked_by) if row else None

    async def confirmed_classifications(
        self, *, limit: int = 20, decisions: tuple[str, ...] | None = None
    ) -> list[tuple[str, str]]:
        """Message text and the type it was marked *right* as.

        The join is the guarantee: only a classification the operator marked
        right appears here. One they never looked at is absent, and so cannot
        become an example the classifier learns its own habits from.

        Balanced across types rather than purely newest-first. Marks arrive in
        bursts — an afternoon spent confirming that a noisy channel is mostly
        `skip` is a realistic afternoon — and eight of eight examples reading
        "this one is skip" teaches the classifier to skip. Recency still
        orders *within* a type; what is shared out is the eight slots.

        `decisions` is the closed set a right-marked row must name to count
        (the task types plus `skip`). It can arrive as an argument so the store
        stays below the registry that holds it — `triage/runner.py` passes the
        registered actions plus `skip`; `None` reads the task-type registry here,
        through a lazy import, so the store names no registry at module
        load and the layering holds.
        """
        if decisions is None:
            from friday.kernel.dag import registry

            decisions = registry.decisions()
        async with self._sessions() as session:
            rows = await session.execute(
                select(schema.Message.text, schema.Message.decision_type)
                .join(
                    schema.Verdict,
                    (schema.Verdict.provider == schema.Message.provider)
                    & (
                        schema.Verdict.provider_message_id
                        == schema.Message.provider_message_id
                    ),
                )
                .where(
                    schema.Verdict.mark == "right",
                    # A closed set, not merely "not null". `mark_triaged`
                    # also writes the *state* a message ended in — a
                    # low-confidence one is recorded as `needs_human` — and
                    # marking that right is a perfectly sensible thing for
                    # the operator to do. Showing it back as an example
                    # would teach the classifier a label it has no tool for.
                    schema.Message.decision_type.in_(decisions),
                )
                .order_by(schema.Verdict.marked_at.desc())
                # Deeper than `limit`, because the balancing below picks from
                # this rather than taking it whole.
                .limit(max(limit * 4, limit))
            )
            return _balanced([(text, kind) for text, kind in rows], limit)
