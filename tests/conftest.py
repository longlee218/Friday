from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from friday.kernel.config import IngestConfig
from friday.kernel.domain.messages import InboundEvent, MentionType
from friday.kernel.harness.harness import Harness
from friday.kernel.inbox import Inbox
from friday.store.db import Database


class FakeProvider:
    """Stands in for a chat platform at the Provider seam.

    Tests queue already-normalised events; the platform-specific work of turning
    a raw payload into an InboundEvent belongs to a real provider and is tested
    against that provider, not here.
    """

    name = "fake"

    def __init__(self) -> None:
        self._queued: list[InboundEvent] = []
        self._history: dict[str, list[InboundEvent]] = {}
        self.history_calls: list[tuple[str, str | None, datetime | None]] = []
        self._recent: dict[str, list[InboundEvent]] = {}
        self.recent_calls: list[tuple[str, str, int]] = []
        self.reconnected = asyncio.Event()
        # By default the live stream ends once queued events are drained, so
        # tests terminate. Set True when a test needs it to stay open.
        self.keep_open = False
        self._closed = asyncio.Event()

    def close(self) -> None:
        self._closed.set()

    def emit(self, event: InboundEvent) -> None:
        """Deliver on the live path."""
        self._queued.append(event)

    def emit_recent(self, conversation_id: str, *events: InboundEvent) -> None:
        """Make events retrievable as prior context for a conversation."""
        self._recent.setdefault(conversation_id, []).extend(events)

    def emit_history(self, channel_id: str, *events: InboundEvent) -> None:
        """Make events retrievable by a sweep of this channel."""
        self._history.setdefault(channel_id, []).extend(events)

    async def stream(self):
        for event in self._queued:
            yield event
        if self.keep_open:
            await self._closed.wait()

    async def recent(self, conversation, *, before: str, limit: int):
        self.recent_calls.append((conversation.target_id, before, limit))
        for event in self._recent.get(conversation.target_id, [])[-limit:]:
            yield event

    async def history(
        self, channel_id: str, *, after: str | None, since: datetime | None
    ):
        """Replay a channel, oldest first.

        `since` is the cold-cursor lookback (ticket 02): with no cursor to
        start from, the sweep says how far back it is willing to read instead
        of reading from the day the channel was created.
        """
        self.history_calls.append((channel_id, after, since))
        for event in self._history.get(channel_id, []):
            if after is not None and int(event.provider_message_id) <= int(after):
                continue
            if after is None and since is not None and event.created_at < since:
                continue
            yield event


def make_event(
    *,
    provider: str = "fake",
    message_id: str = "m1",
    channel_id: str = "watched",
    thread_id: str | None = None,
    mention_type: MentionType | None = MentionType.DIRECT,
    text: str = "hey can you look at this",
    author_id: str = "u-reporter",
    author_name: str = "reporter",
    is_own: bool = False,
    reply_to: str | None = None,
    created_at: datetime | None = None,
) -> InboundEvent:
    return InboundEvent(
        provider=provider,
        provider_message_id=message_id,
        channel_id=channel_id,
        thread_id=thread_id,
        author_id=author_id,
        author_name=author_name,
        text=text,
        created_at=created_at or datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
        mention_type=mention_type,
        is_own=is_own,
        reply_to=reply_to,
    )


def summary_row(channel_id: str = "watched", **fields):
    """A room's `summary` row as the store hands it back — what a channel
    file's `derived: {summary: ...}` was until board
    `read-it-the-way-the-operator-does`, ticket 10. For a renderer test that
    has no reason to run the summariser to get one."""
    from friday.kernel.domain.memory import Memory, RoomSummary

    now = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    data = {
        f: fields.get(f, [] if f != "topic" else "")
        for f in RoomSummary.__dataclass_fields__
    }
    data.update(summary_of=fields.get("summary_of", "m1"))
    return Memory(
        id="s1",
        channel_id=channel_id,
        agent="summary",
        text=data["topic"],
        kind="summary",
        created_at=now,
        updated_at=now,
        key="room",
        data=data,
    )


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
async def db():
    database = await Database.connect(":memory:", create=True)
    yield database
    await database.close()


@pytest.fixture
def config() -> IngestConfig:
    return IngestConfig(
        watched_channels=frozenset({"watched"}),
        mention_types=frozenset({MentionType.DIRECT, MentionType.ROLE, MentionType.DM}),
    )


@pytest.fixture
def inbox(provider, db, config) -> Inbox:
    return Inbox(provider=provider, db=db, config=config)


async def captured(inbox: Inbox) -> list[InboundEvent]:
    return [event async for event in inbox.stream()]


class BoardClient(TestClient):
    """A board client that behaves like the operator's own loopback page.

    The board (ticket 02) refuses a write that does not come from a loopback
    `Host` carrying the session's CSRF token. A browser reads that token from
    the `friday_csrf` cookie a GET sets and echoes it in `X-CSRF-Token`; this
    does the same on every write, so a write test asserts behaviour rather than
    the guard's plumbing. It speaks from `127.0.0.1` because the guard trusts
    only loopback. The cookie/header names come from the guard's own constants,
    so a rename cannot leave this helper echoing the wrong one."""

    def __init__(self, app, **kw):
        kw.setdefault("base_url", "http://127.0.0.1")
        super().__init__(app, **kw)

    def request(self, method, url, *args, **kw):
        from friday.kernel.ops.api import _WRITE_METHODS, CSRF_COOKIE, CSRF_HEADER

        if method.upper() in _WRITE_METHODS:
            if CSRF_COOKIE not in self.cookies:
                super().request("GET", "/api/board")
            headers = dict(kw.pop("headers", None) or {})
            headers.setdefault(CSRF_HEADER, self.cookies.get(CSRF_COOKIE, ""))
            kw["headers"] = headers
        return super().request(method, url, *args, **kw)


@pytest.fixture(autouse=True)
def workflow_graphs():
    """Register the workflow graphs the way the composition root does.

    Without this the tests exercise a route production never takes: a task
    type with no registered graph. `trace_problem` moved into a graph in ticket
    33; every other classifiable type followed in ticket 04, so a runner test
    that does not register them is testing the absence of a graph rather than
    a graph.

    The config stand-in resolves no agent (`agent` answers `None`), so every
    model node skips, and the graph's last node hands over rather than
    investigating.
    """
    from types import SimpleNamespace

    from friday.kernel.dag import registry
    from friday.kernel.dag.router import EDGE_ROUTER, register_dags
    from friday.kernel.memory import registry as memory_kinds

    # Memory kinds register themselves too (ticket 12): the store reads the
    # registry for a kind's writers/schema/natural key, so every test needs it
    # filled the way the composition root fills it at boot.
    memory_kinds.register_all_memory_kinds()
    register_dags(
        SimpleNamespace(
            agent=lambda declaration: None,
            context=SimpleNamespace(extraction_budget_tokens=None),
        ),
        servers={},
    )
    try:
        yield
    finally:
        EDGE_ROUTER.clear()
        registry.clear()
        memory_kinds.clear()


@pytest.fixture
async def workflows(db):
    """A real DBOS behind the pool, for the tests that run a task past node 0.

    Most pool tests stop at node 0 (a missing-details `Ask`, a one-node
    hand-over) and never touch a workflow, so DBOS is opt-in: this launches it
    on a throwaway SQLite system database and registers the graphs on the
    adapter with this test's store, the way the composition root does. A fresh
    system database each test keeps one task's `task-<id>` workflow from
    colliding with the next's.
    """
    import tempfile
    from types import SimpleNamespace

    from friday.kernel.dag import adapter
    from friday.kernel.dag.router import register_dags

    tmp = tempfile.mkdtemp()
    adapter.launch("friday-test", f"{tmp}/system.db")
    register_dags(
        SimpleNamespace(
            agent=lambda declaration: None,
            context=SimpleNamespace(extraction_budget_tokens=None),
        ),
        servers={},
        db=db,
    )
    try:
        yield db
    finally:
        adapter.shutdown()


class ScriptedHarness(Harness):
    """A `Harness` whose model call is scripted and whose every other seam is
    the real one.

    Subclass it and write `run()`; everything else — chiefly
    `run_structured`'s validation and its one correction turn — is inherited
    rather than imitated. That distinction is the point of this class
    existing: a double that supplied its own `run_structured` would let a
    test pass while skipping the mechanism the test is nominally about, which
    is the failure `tests/test_dag_prepare.py`'s own extractor doubles
    already record once.

    `Harness.__init__` is deliberately not called: it builds a client and a
    model, which is exactly what a test is here to avoid. Only the attributes
    the inherited code actually reads are set.
    """

    def __init__(self, **attrs) -> None:
        # `hasattr` rather than plain assignment, so a subclass that declares
        # `last_error = "boom"` as a class attribute keeps it: an instance
        # attribute set here would shadow it.
        from friday.kernel.config import AgentConfig

        for name, default in (
            ("last_error", None),
            ("answers", None),
            # A `Harness` has one, and the inherited code logs through it. A
            # stand-in rather than a mock: the only thing read off it here is
            # the agent's name in a log line.
            (
                "_config",
                AgentConfig(
                    name="scripted",
                    api_key="k",
                    base_url="http://x/v1",
                    model="m",
                ),
            ),
        ):
            if not hasattr(self, name):
                setattr(self, name, default)
        for name, value in attrs.items():
            setattr(self, name, value)
