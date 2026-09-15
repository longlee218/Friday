from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from friday.config import IngestConfig
from friday.store.db import Database
from friday.agent.harness import Harness
from friday.inbox import Inbox
from friday.domain.models import InboundEvent, MentionType


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
        created_at=created_at or datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
        mention_type=mention_type,
        is_own=is_own,
        reply_to=reply_to,
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
        mention_types=frozenset(
            {MentionType.DIRECT, MentionType.ROLE, MentionType.DM}
        ),
    )


@pytest.fixture
def inbox(provider, db, config) -> Inbox:
    return Inbox(provider=provider, db=db, config=config)


async def captured(inbox: Inbox) -> list[InboundEvent]:
    return [event async for event in inbox.stream()]


@pytest.fixture(autouse=True)
def workflow_graphs():
    """Register the workflow graphs the way the composition root does.

    Without this the tests exercise a route production never takes: a task
    type with no registered graph. `api_issue` moved into a graph in ticket
    33; every other classifiable type followed in ticket 04, so a runner test
    that does not register them is testing the absence of a graph rather than
    a graph.

    The config stand-in declares no agents, which is the state of a fresh
    install — every node skips, and the graph's last node hands over rather
    than investigating.
    """
    from types import SimpleNamespace

    from friday.dag.router import DAG_DEPS_EXTRA, DAG_SERVERS, EDGE_ROUTER, register_dags

    register_dags(
        SimpleNamespace(
            agents={},
            context=SimpleNamespace(extraction_budget_tokens=None),
        ),
        servers={},
    )
    try:
        yield
    finally:
        EDGE_ROUTER.pop("api_issue", None)
        DAG_DEPS_EXTRA.clear()
        DAG_SERVERS.clear()


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
        # `refusal = "over budget"` as a class attribute keeps it: an
        # instance attribute set here would shadow it, and the test asserting
        # that a refusal is told apart from a failure would silently stop
        # testing anything.
        from friday.config import AgentConfig

        for name, default in (
            ("tool_turns", 0), ("last_error", None), ("refusal", None),
            ("answers", None),
            # A `Harness` has one, and the inherited code logs through it. A
            # stand-in rather than a mock: the only thing read off it here is
            # the agent's name in a log line.
            ("_config", AgentConfig(
                name="scripted", api_key="k", base_url="http://x/v1", model="m",
            )),
        ):
            if not hasattr(self, name):
                setattr(self, name, default)
        for name, value in attrs.items():
            setattr(self, name, value)
