"""The only store.

A deep module: every caller sees domain dataclasses, and nothing above this
seam knows SQLAlchemy exists. Mapped classes live in `friday.store.schema` and
are converted at the edge, so the domain models stay free of persistence
concerns.

`Database` is a **facade over repositories** (ticket 16): the ~120 methods live
in cohesive mixins under `friday/store/repositories/`, each using `self._sessions`,
and this class composes them so callers still reach one flat surface
(`db.queue_outbound(...)`, `db.memory_add(...)`). The shared internals — imports,
row converters, id generators, selectors, constants — are in
`friday.store._common`. Kernel *invariants* (outbox approval, the memory-write
guard) do **not** live here: they are enforced in kernel code against the `Store`
contract, so a store that persisted a self-approved row could not smuggle it out.

Never call this from a sync path — a blocking database call on the event loop
stalls ingestion.
"""

from __future__ import annotations

from friday.store._common import *  # noqa: F401,F403 (shared store internals)
from friday.store.repositories.calls import CallsRepo
from friday.store.repositories.memory import MemoryRepo
from friday.store.repositories.messages import MessagesRepo
from friday.store.repositories.monitor import MonitorRepo
from friday.store.repositories.outbox import OutboxRepo
from friday.store.repositories.rooms import RoomsRepo
from friday.store.repositories.tasks import TasksRepo
from friday.store.repositories.verdicts import VerdictsRepo

__all__ = ["Database", "estimated_tokens"]


def _engine(path: str):
    """One engine per process. In-memory needs `StaticPool`: without it every
    checkout opens a *different* empty database."""
    if path == ":memory:":
        return create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(connection, _record):
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")

    return engine


class Database(
    MessagesRepo,
    RoomsRepo,
    MonitorRepo,
    CallsRepo,
    MemoryRepo,
    OutboxRepo,
    TasksRepo,
    VerdictsRepo,
):
    #: Caps and cooldowns the repositories read as `self.X` — on the facade so
    #: they resolve through the MRO for every mixin, and so the docstrings that
    #: name them `Database.MEMORY_PER_CHANNEL` stay accurate.
    MEMORY_PER_CHANNEL = 200
    TEXT_CHARS = 500
    DIAGNOSE_FINDINGS = 5
    COMPACTION_COOLDOWN_AFTER = 2

    def __init__(self, engine, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._engine = engine
        self._sessions = sessions
        #: Held across `memory_add`'s count and insert, which are two awaits
        #: apart — see there.
        self._memory_slots = asyncio.Lock()

    @classmethod
    async def connect(cls, path: str, *, create: bool = False) -> "Database":
        """Open the store. `create` builds the schema straight from the models.

        Off by default, because a real database gets its shape from Alembic and
        `run_agent.migrate()` has already run by the time this is called.
        Building tables here as well would hide a forgotten revision: every
        fresh database would work, and only the one that already exists would
        break. Tests opt in; nothing else should.
        """
        engine = _engine(path)
        if create:
            async with engine.begin() as connection:
                await connection.run_sync(schema.Base.metadata.create_all)
        return cls(engine, async_sessionmaker(engine, expire_on_commit=False))

    async def close(self) -> None:
        await self._engine.dispose()
