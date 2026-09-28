"""The memory repository — a `Database` mixin (ticket 16): one table's reads and
writes. Over 200 lines because the per-kind write checks are one decision; it
its retrieval read is `case_memories`, core Intake's (build-the-spine ticket 07). Candidates are `memory_candidates.py`."""

from __future__ import annotations

from friday.store._common import *  # noqa: F401,F403 (shared store internals)


class MemoryRepo:

    async def memory_search(
        self, state: FridayState, query: str, *, kind: str, limit: int
    ) -> list[Memory]:
        """Every active match of this kind in this channel, newest first —
        not ranked by how well it matches, only by when it was written.

        `kind` is required, the same reasoning `limit` already got: the only
        caller (`friday/kernel/tools/memory.py`, scoped to the responder) always
        knows which kind it means — `voice` — and a default here
        would let a second caller agree with that by coincidence rather than
        by saying so. Only `ACTIVE` rows match (D16): a superseded or deleted
        row is for the operator's own view, never a model's.

        `query` is matched the way `SkillLibrary.search` matches one of its
        ranks — every word has to appear somewhere in the text — but this
        does not rank: `SkillLibrary` scores a fixed catalogue read at
        startup, and a channel's memory changes underneath every call, so
        recency is the cheap, honest order rather than a score this method
        does not compute. Over a corpus that is a handful of rows per channel
        today; revisit if a room's memory ever grows past what a linear scan
        over its own rows can do cheaply.

        An empty `query` matches every word-count check vacuously and returns
        the channel's most recent memories up to `limit` — not validated
        against, because a model sending "" is a model that wants to see what
        is there, and that is a reasonable thing to want.
        """
        words = [w for w in query.lower().split() if w]
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.Memory)
                .where(
                    schema.Memory.channel_id == state.channel_id,
                    schema.Memory.deleted_at.is_(None),
                    schema.Memory.status == MemoryStatus.ACTIVE,
                    schema.Memory.kind == kind,
                )
                .order_by(schema.Memory.created_at.desc())
            )
            matched = [
                row
                for row in rows
                if all(word in row.text.lower() for word in words)
            ]
            return [_memory(row) for row in matched[:limit]]

    async def domain_memories(self, channel_id: str) -> list[Memory]:
        """This channel's active domain-kind memories — fact, constraint,
        finding, decision — and the ones written for `channel_id = '*'`,
        newest first, for the extractor's per-call input (D14, D21). `voice`
        never reaches here; that kind is the responder's alone.

        `'*'` is "true everywhere", which is what `base.yaml` was, and the
        operator's rows are what a channel file's `overrides` were (board
        `read-it-the-way-the-operator-does`, ticket 10) — so this is the
        whole of what the extractor is told a room is.

        Not scoped by agent the way `memory_search` is, and takes no query:
        the extractor has no memory tools of its own (D21) and reads by
        injection, so there is no per-call search to filter by — only "what
        does this room's memory currently claim". Bounded the same way every
        other memory reader here is, by `MEMORY_PER_CHANNEL` on the write
        side rather than a limit here.
        """
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.Memory)
                .where(
                    schema.Memory.channel_id.in_([channel_id, "*"]),
                    schema.Memory.deleted_at.is_(None),
                    schema.Memory.status == MemoryStatus.ACTIVE,
                    schema.Memory.kind.in_(list(memory_kinds.domain_kinds())),
                )
                .order_by(schema.Memory.created_at.desc())
            )
            return [_memory(row) for row in rows]

    async def room_summary(self, channel_id: str) -> Memory | None:
        """This room's active `summary` row, or `None` for a room nobody has
        summarised — what a channel file's `derived` section was (ticket 10).
        The partial unique index holds a room to one active summary, so there
        is never a second one to choose between."""
        async with self._sessions() as session:
            row = await session.scalar(
                select(schema.Memory).where(
                    schema.Memory.channel_id == channel_id,
                    schema.Memory.deleted_at.is_(None),
                    schema.Memory.status == MemoryStatus.ACTIVE,
                    schema.Memory.kind == memory_kinds.SUMMARY,
                )
            )
            return _memory(row) if row is not None else None

    async def structured_memory(
        self, channel_id: str, *, kind: str, key: str
    ) -> Any | None:
        """One structured row as its own type, by its natural key — this
        room's, or the one written for every room.

        `route`, `service` and `project` are read by code and never by a model
        (`readers_for`), and what code wants is the typed object, not a
        `Memory` whose `data` dict every call site would rebuild. So this
        returns the kind's `data` instance, or `None`.

        Checked again on the way out although it was checked on the way in: a
        row written before a schema changed is an ordinary thing, and a graph
        node that reads one should hand over naming the row rather than raise
        `TypeError` two nodes later. An unfit row reads as absent here, which
        is the outcome that already has a hand-over behind it.

        This room's row wins over the `'*'` one. Two rows can hold the same
        key — that is what `'*'` is for — and the more specific is the one
        somebody wrote about this room on purpose.
        """
        schema_type = memory_kinds.data_of(kind)
        if schema_type is None:
            raise ValueError(f"a {kind} is prose — it has no structured data")
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.Memory)
                .where(
                    schema.Memory.channel_id.in_([channel_id, "*"]),
                    schema.Memory.deleted_at.is_(None),
                    schema.Memory.status == MemoryStatus.ACTIVE,
                    schema.Memory.kind == memory_kinds.validate_kind(kind),
                    schema.Memory.key == key,
                )
                # The room's own row first; `'*'` sorts before any real
                # channel id, so this is descending rather than ascending.
                .order_by(schema.Memory.channel_id.desc())
            )
            for row in rows:
                fitted, unfit = fits(row.data or {}, schema_type)
                if unfit is None:
                    return fitted
                log.warning(
                    "memory %s: a %s row no longer fits its schema — %s",
                    row.id, kind, unfit.why,
                )
            return None

    async def _refuse_dangling(self, session, channel_id: str, kind: str, data) -> None:
        """Refuse a row that names a row nobody has written (ticket 19).

        The scope is the one `structured_memory` reads by — this room or
        `'*'` — so what this accepts and what a lookup will later find cannot
        be two different sets. At the store rather than in the form, because
        the form is one door, the API is another and `memory_supersede` is a
        third, and it is `memory_add`'s own argument that the operator's hand
        meets the check a model's does.

        **Checked and written in one session, not one lock.** A row this
        names could in principle be removed between the check and the
        commit. One process holds one `Database` and the operator is one
        person, so the window is theoretical; it is written down rather than
        defended against, because defending it would mean a lock around
        every structured write for a race nobody has met.
        """
        for field_name, names_kind in memory_kinds.names_in(kind).items():
            value = (data or {}).get(field_name)
            if not value:
                continue
            found = await session.scalar(
                select(schema.Memory.id).where(
                    schema.Memory.channel_id.in_([channel_id, "*"]),
                    schema.Memory.deleted_at.is_(None),
                    schema.Memory.status == MemoryStatus.ACTIVE,
                    schema.Memory.kind == names_kind,
                    schema.Memory.key == value,
                ).limit(1)
            )
            if found is None:
                raise MemoryRefused(
                    f"{kind}.{field_name} names the {names_kind} "
                    f"{value!r}, and no {names_kind} row here is called that. "
                    f"Write that {names_kind} first, or correct the name."
                )

    async def _dependants(self, session, channel_id: str, kind: str, key: str):
        """Every active row that names `key` — the rows a rename or a delete
        would orphan.

        A structured row's key *is* its data, so moving it silently breaks
        rows nobody touched. That happened twenty minutes after the first
        dangling reference was fixed, which is why this exists as well as
        the check above.
        """
        # **A `'*'` row is named from everywhere, so its dependants are
        # searched everywhere.** Scoping this to `[channel_id, "*"]` with
        # `channel_id == "*"` collapses to `'*'` alone, and a shared project
        # could then be removed while one room's service still named it —
        # the orphan this method exists to prevent, one scope over. Found by
        # review.
        everywhere = channel_id == "*"
        found = []
        for other_kind, field_name in memory_kinds.named_by(kind):
            rows = await session.scalars(
                select(schema.Memory).where(
                    *(
                        ()
                        if everywhere
                        else (schema.Memory.channel_id.in_([channel_id, "*"]),)
                    ),
                    schema.Memory.deleted_at.is_(None),
                    schema.Memory.status == MemoryStatus.ACTIVE,
                    schema.Memory.kind == other_kind,
                    func.json_extract(schema.Memory.data, f"$.{field_name}") == key,
                )
            )
            found += [(other_kind, row.key or row.id) for row in rows]
        return found

    async def _refuse_orphaning(
        self, session, channel_id: str, kind: str, key: str | None, what: str
    ) -> None:
        if not key:
            return
        held = await self._dependants(session, channel_id, kind, key)
        if held:
            named = ", ".join(f"{k} {n!r}" for k, n in held)
            raise MemoryRefused(
                f"{what} would leave {named} naming a {kind} called {key!r} "
                f"that no longer exists. Change or remove {'them' if len(held) > 1 else 'it'} first."
            )

    async def structured_memories(self, channel_id: str, *, kind: str) -> list[Any]:
        """Every active row of one structured kind, as its own type — this
        room's and the ones written for every room.

        The sibling of `structured_memory` for a kind that is read as a
        *table* rather than looked up by key: `environment` is matched by
        longest suffix, so the caller needs all of them. Room rows first, for
        the same reason and by the same ordering.

        A row that no longer fits its schema is skipped with a warning rather
        than raising, exactly as the by-key read does: one bad row must not
        stop the rest of the table from being read.
        """
        schema_type = memory_kinds.data_of(kind)
        if schema_type is None:
            raise ValueError(f"a {kind} is prose — it has no structured data")
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.Memory)
                .where(
                    schema.Memory.channel_id.in_([channel_id, "*"]),
                    schema.Memory.deleted_at.is_(None),
                    schema.Memory.status == MemoryStatus.ACTIVE,
                    schema.Memory.kind == memory_kinds.validate_kind(kind),
                )
                .order_by(schema.Memory.channel_id.desc())
            )
            found = []
            for row in rows:
                fitted, unfit = fits(row.data or {}, schema_type)
                if unfit is None:
                    found.append(fitted)
                else:
                    log.warning(
                        "memory %s: a %s row no longer fits its schema — %s",
                        row.id, kind, unfit.why,
                    )
            return found

    async def knows_person(self, channel_id: str, discord_id: str) -> bool:
        """Whether the operator wrote this person down, for this room or for
        every room — an active `person` row keyed on their Discord id. What a
        channel file's `people:` map was, read by code and never shown to a
        model raw (ticket 10)."""
        async with self._sessions() as session:
            found = await session.scalar(
                select(schema.Memory.id).where(
                    schema.Memory.channel_id.in_([channel_id, "*"]),
                    schema.Memory.deleted_at.is_(None),
                    schema.Memory.status == MemoryStatus.ACTIVE,
                    schema.Memory.kind == memory_kinds.PERSON,
                    schema.Memory.key == discord_id,
                ).limit(1)
            )
            return found is not None

    async def case_memories(
        self, channel_id: str, keys: Mapping[str, str], text: str = ""
    ) -> list[Memory]:
        """What core Intake retrieves for one case (spec, "Memory: one store,
        twelve kinds"): every active `fact`, `constraint` and `decision`; the
        runbooks whose `when` matches the case; and the few newest findings
        on the same keys.

        `keys` are the domain type's `retrieval_keys()` (backend:
        `{"service": …}`) — named, so core matches them without knowing the
        domain: a runbook matches when its `when` list of the same name holds
        the value, or one of its `keywords` is in `text`; a finding matches
        when its data carries every key with the same value. No keys → no
        findings. An empty `when` matches nothing — a runbook code cannot
        pick is one nobody asked for.

        This room's rows and the ones written for `channel_id = '*'`, which
        is "true everywhere". Ordered the way the spec orders a prompt —
        operator rows before model rows, runbooks after the domain kinds,
        findings last — so two cases on one service share a prefix.
        """
        domain = [memory_kinds.FACT, memory_kinds.CONSTRAINT, memory_kinds.DECISION]
        async with self._sessions() as session:
            rows = list(
                await session.scalars(
                    select(schema.Memory)
                    .where(
                        schema.Memory.channel_id.in_([channel_id, "*"]),
                        schema.Memory.deleted_at.is_(None),
                        schema.Memory.status == MemoryStatus.ACTIVE,
                        schema.Memory.kind.in_(
                            [*domain, memory_kinds.SKILL, memory_kinds.FINDING]
                        ),
                    )
                    .order_by(schema.Memory.created_at)
                )
            )
        admin_first = lambda row: row.origin != MemoryOrigin.ADMIN  # noqa: E731
        known = sorted((r for r in rows if r.kind in domain), key=admin_first)
        runbooks = [
            r for r in rows
            if r.kind == memory_kinds.SKILL and _runbook_matches(r.data, keys, text)
        ]
        findings = [
            r for r in reversed(rows)
            if r.kind == memory_kinds.FINDING and keys
            and all((r.data or {}).get(name) == value for name, value in keys.items())
        ][: self.CASE_FINDINGS]
        return [_memory(r) for r in (*known, *runbooks, *findings)]

    async def memory_add(
        self,
        state: FridayState,
        text: str,
        *,
        kind: str = memory_kinds.VOICE,
        origin: str = MemoryOrigin.MODEL,
        key: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> Memory | None:
        """Write a new memory, or refuse if the channel is already full.

        `kind` defaults to `voice` because the only wired producer
        today is the responder (`friday/kernel/tools/memory.py`), which writes
        nothing else — unlike `memory_search`'s `kind`, a default here names
        the one thing every caller before this ticket already meant, rather
        than standing in for a caller that forgot to say.

        `None` means the channel is at `MEMORY_PER_CHANNEL` — the caller
        (`friday/kernel/tools/memory.py`) turns that into a message the model can
        act on, the same way it turns a wrong-scope id into one. The count
        only considers active memories: a superseded or deleted row already
        freed its slot, the same rule `test_deleting_a_memory_frees_its_slot`
        pins for deletion.

        **The count and the insert happen under one lock**, because they are
        two awaits apart and two calls for one channel could otherwise both
        read 199 and both write. This said the race could not happen, since
        the pool drained its tasks one at a time; ticket 13 made the pool work
        tasks side by side, and a reaction marking a candidate right writes
        through here from the gateway whatever the pool is doing. One process
        and one `Database` (the constraint this whole store rests on), so an
        `asyncio.Lock` is the whole of it — not a database lock, which SQLite
        would only turn into a `database is locked` for one of the two.

        **The trust-boundary invariants are the kernel's, not this store's**
        (ticket 16): the instruction-shape guard (D25) and "an origin may write
        only a kind its `writers` allow" are enforced in `friday.kernel.memory.write`
        before a runtime caller reaches this method, so a dumb store — this one,
        or a fake in a test — cannot smuggle either past them. What stays here is
        this store's own data integrity: `data` is validated against the kind's
        `data` schema with the `fits` the harness uses on a model's answer (a
        wrong-typed field is refused, `MemoryRefused`, with the field named), the
        natural key is read off the validated data, referential and cap checks
        run, and the unique key is enforced.
        """
        stored, key = _checked_data(kind, data, key)
        async with self._memory_slots, self._sessions.begin() as session:
            await self._refuse_dangling(session, state.channel_id, kind, stored)
            count = await session.scalar(
                select(func.count()).select_from(schema.Memory).where(
                    schema.Memory.channel_id == state.channel_id,
                    schema.Memory.deleted_at.is_(None),
                    schema.Memory.status == MemoryStatus.ACTIVE,
                )
            )
            if (count or 0) >= self.MEMORY_PER_CHANNEL:
                return None
            now = _now()
            row = schema.Memory(
                id=_memory_id(),
                channel_id=state.channel_id,
                agent=state.agent,
                text=text[: self.TEXT_CHARS],
                kind=kind,
                task_id=state.task_id,
                # The message that produced this memory, when the caller
                # supplies one. The Rooms screen joins on it.
                source_message_id=state.message_id,
                created_at=now,
                updated_at=now,
                origin=origin,
                key=key,
                data=stored,
            )
            session.add(row)
            await _flush_keyed(session, kind, key)
            return _memory(row)

    async def memory_update(
        self,
        state: FridayState,
        memory_id: str,
        text: str,
        *,
        data: dict[str, Any] | None = None,
        origin: str = MemoryOrigin.MODEL,
    ) -> Memory | None:
        """Correct a memory's wording in place — the same claim, said
        better — or `None` if this scope has no such (live, active) memory by
        this id: wrong channel, never existed, already deleted, and already
        superseded all read the same, on purpose.

        Distinct from `memory_supersede` (D16): this never changes what the
        memory claims, only how it is worded, so it never touches `kind`,
        `status` or `superseded_by`.

        The instruction-shape guard runs in `friday.kernel.memory.write` before
        a caller reaches here (ticket 16), not in this store. `data`, when given,
        replaces a structured row's payload and is checked here as `memory_add`
        checks it; its natural key moves with it. Omitted, the payload is left as
        it is. A model-origin call never resolves an `origin=admin` row
        (`_live_memory`) — that access check needs the row, so it stays here.
        """
        async with self._sessions.begin() as session:
            row = await self._live_memory(session, state, memory_id, origin)
            if row is None:
                return None
            if data is not None:
                was = row.key
                row.data, row.key = _checked_data(row.kind, data, row.key)
                await self._refuse_dangling(
                    session, state.channel_id, row.kind, row.data
                )
                if row.key != was:
                    await self._refuse_orphaning(
                        session, state.channel_id, row.kind, was,
                        f"renaming this {row.kind} to {row.key!r}",
                    )
            row.text = text[: self.TEXT_CHARS]
            row.updated_at = _now()
            await _flush_keyed(session, row.kind, row.key)
            return _memory(row)

    async def memory_supersede(
        self,
        state: FridayState,
        memory_id: str,
        text: str,
        *,
        origin: str = MemoryOrigin.MODEL,
        data: dict[str, Any] | None = None,
    ) -> Memory | None:
        """Replace what a memory claims, rather than correcting how it is
        worded (D16) — the operation `memory_update` deliberately is not.

        `data`, when given, is the new claim's payload for a structured
        kind, checked as `memory_add` checks it — the summariser's rebuild is
        this call, since a new summary is a new claim about the room (ticket
        10). Omitted, the replacement keeps the old row's payload.

        The old row is marked `SUPERSEDED` and points `superseded_by` at a
        freshly written row carrying the new claim, under the same `kind` so
        a reader that already trusts that kind's shape keeps trusting it. The
        old row's text is untouched: the board can still show what it used to
        say, and `updated_at` says when it changed.

        `None` for the same three reasons `memory_update` returns it — wrong
        channel, never existed, already deleted — plus a fourth: a row that
        is already superseded cannot be superseded again through this id.
        "The current one" is the row it points at, so supersede that one
        instead.

        Never refused for the channel's cap: an active row becomes inactive
        and a new active row is written in the same call, so the channel's
        active count does not move.

        The instruction-shape guard runs in `friday.kernel.memory.write` before
        a caller reaches here (ticket 16), the same as for `memory_add` and
        `memory_update` — not in this store.
        """
        async with self._sessions.begin() as session:
            old = await self._live_memory(session, state, memory_id, origin)
            if old is None:
                return None
            stored, key = (
                _checked_data(old.kind, data, old.key)
                if data is not None
                else (old.data, old.key)
            )
            if data is not None:
                # This path takes `data` too, so it can move a key and
                # orphan every row that names it — the same hole
                # `memory_update` has, reached by a different door. Found by
                # review rather than by a test, which is why it is checked
                # here rather than argued about in a comment on the other.
                await self._refuse_dangling(
                    session, state.channel_id, old.kind, stored
                )
                if key != old.key:
                    await self._refuse_orphaning(
                        session, state.channel_id, old.kind, old.key,
                        f"superseding this {old.kind} with one called {key!r}",
                    )
            now = _now()
            new_row = schema.Memory(
                id=_memory_id(),
                channel_id=state.channel_id,
                agent=state.agent,
                text=text[: self.TEXT_CHARS],
                kind=old.kind,
                task_id=state.task_id,
                source_message_id=state.message_id,
                created_at=now,
                updated_at=now,
                status=MemoryStatus.ACTIVE,
                # The replacement keeps what the row is *about*; only the
                # claim in `text` changes. The old row goes inactive first
                # so the two never hold the key at once.
                origin=origin,
                key=key,
                data=stored,
            )
            old.status = MemoryStatus.SUPERSEDED
            old.superseded_by = new_row.id
            old.updated_at = now
            await session.flush()
            session.add(new_row)
            await session.flush()
            return _memory(new_row)

    async def memory_delete(
        self, state: FridayState, memory_id: str, *, origin: str = MemoryOrigin.MODEL
    ) -> bool:
        """Soft-delete: the row survives with who removed it and when, so an
        operator can see what a line said after it is gone. `False` for the
        same cases `memory_update` treats alike."""
        async with self._sessions.begin() as session:
            row = await self._live_memory(session, state, memory_id, origin)
            if row is None:
                return False
            await self._refuse_orphaning(
                session, state.channel_id, row.kind, row.key,
                f"removing this {row.kind}",
            )
            row.deleted_at = _now()
            row.deleted_by = state.agent
            return True

    async def full_memory_channels(self) -> list[str]:
        """Every channel currently at `MEMORY_PER_CHANNEL` (board
        `what-the-room-already-knows`, ticket 12, D18). The cap itself
        already refuses a write there and evicts nothing — this is the other
        half: the condition is visible to the one party who can act on it,
        not only to the model that gets the refusal message. Sorted for a
        stable read, not by how full each one is — this is a short,
        occasional list, not a leaderboard.
        """
        async with self._sessions() as session:
            rows = await session.execute(
                select(schema.Memory.channel_id, func.count())
                .where(
                    schema.Memory.deleted_at.is_(None),
                    schema.Memory.status == MemoryStatus.ACTIVE,
                )
                .group_by(schema.Memory.channel_id)
                .having(func.count() >= self.MEMORY_PER_CHANNEL)
            )
            return sorted(channel_id for channel_id, _ in rows)

    async def memories_for_channel(
        self, channel_id: str, *, limit: int = 200
    ) -> list[Memory]:
        """This channel's memories, live or deleted, newest first — the
        operator's view. Not scope-filtered by agent: this is a human looking
        at one room, not a tool call from inside it.

        Bounded, unlike the room a caller might expect this to have: live
        memories are bounded by `MEMORY_PER_CHANNEL`, but a deleted row is
        never purged, so a channel that has churned through many corrections
        holds an unbounded number of rows this method would otherwise return
        every one of.
        """
        async with self._sessions() as session:
            rows = await session.scalars(
                select(schema.Memory)
                .where(schema.Memory.channel_id == channel_id)
                .order_by(schema.Memory.created_at.desc())
                .limit(limit)
            )
            return [_memory(row) for row in rows]

    async def _live_memory(
        self, session, state: FridayState, memory_id: str, origin: str
    ):
        """The row, if it exists, belongs to this scope, is not deleted, and
        is still active — the one query `memory_update`, `memory_supersede`
        and `memory_delete` share, so the reasons an id can fail to resolve
        cannot drift apart between them. A superseded row is frozen history
        (D16): correct or retract the row that replaced it, not this one.

        **A model-origin caller never resolves an operator's row** (ticket
        09): it gets the same `None` a wrong-scope id gets, so a model cannot
        tell an `origin=admin` row from one that does not exist. The
        operator may correct any row."""
        query = select(schema.Memory).where(
            schema.Memory.id == memory_id,
            schema.Memory.channel_id == state.channel_id,
            schema.Memory.deleted_at.is_(None),
            schema.Memory.status == MemoryStatus.ACTIVE,
        )
        if MemoryOrigin(origin) is not MemoryOrigin.ADMIN:
            query = query.where(schema.Memory.origin != MemoryOrigin.ADMIN)
        return await session.scalar(query)
