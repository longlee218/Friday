"""`FridayState`: what one message's journey knows about itself."""

from __future__ import annotations

from dataclasses import dataclass, replace

from friday.kernel.domain.conversation import ConversationId
from friday.kernel.domain.messages import InboundEvent


@dataclass(frozen=True, slots=True)
class FridayState:
    """What one message's journey knows about itself, the whole way down.

    Board `every-answer-has-a-shape`, D8–D10. This is what the SDK's per-run
    `context` carries, and it is the **only** thing it carries: the slot used
    to mean "who is this run about" for one agent and "where the answer will
    appear" for another, which is two mechanisms sharing one parameter.

    **It replaces `MemoryScope`**, which named the same room under a second
    name for a narrower purpose — a memory's channel, task, agent and source
    message. One notion of "which room is this", not two, because a second
    name for one thing is how the two drift (D10). Everything that made
    `MemoryScope` correct is unchanged and is the reason this is a value
    rather than a closure variable: an agent here is built once, at startup,
    and reused for every task in every channel, so a scope captured when the
    tools were made would pin every room's memory to whichever room happened
    to be first. It arrives per call. The model still cannot name it, which
    was the point.

    `channel_id` is the read *and* write boundary for memory — a memory
    written in one room is invisible in another. Not a nicety: this system's
    rooms are different teams, and a fact learned in one is a leak in the
    next.

    **Read-only, and every change is a named method** (D9). Not a style
    choice: this value is handed to a tool, to the store, and to the recording
    sink within one run, and a field anything could assign would make "what
    can change this, and where" unanswerable — which is the question the
    threading this replaces could not answer either. Each method below returns
    a *new* state, so a value handed to one step cannot be changed underneath
    another, and each changes exactly one thing. There is deliberately no
    general `with_(**fields)`: a generic setter would make every change legal
    again and put the list back out of reach.

    **Only `channel_id` and `agent` are required**, and that is not laziness
    about the rest. Those two are the boundary and the provenance: a memory
    written without a room has nowhere safe to live, and one written without
    an author loses "who wrote this, and while doing what" — the question the
    operator's board exists to answer about a line an agent is now acting on.
    Everything else is a fact the journey supplies when it has it, which is
    what `None` already meant on `task_id`: a run that belongs to no task says
    so. `for_conversation` fills the four a task's conversation already knows,
    and the named methods below add the rest as the journey learns them.
    """

    channel_id: str
    #: Which agent is running right now. Changed by `as_agent` at each
    #: hand-off, so a memory written during an extraction is not attributed to
    #: whoever ran first.
    agent: str
    #: Which provider the message came from. The other half of a message's
    #: identity key — the store dedupes on `(provider, provider_message_id)` —
    #: and `None` in a state built by hand for something that does not need
    #: it, the same way `task_id` is `None` before triage has decided.
    provider: str | None = None
    thread_id: str | None = None
    #: The message this step is about. For triage that is the mention; for the
    #: responder, the message it is drafting a reply to. It is also what the
    #: recording sink correlates a model call by, and what a memory records as
    #: the message that produced it — a tool with no message in scope leaves
    #: it `None`, and the store keeps it `None`.
    message_id: str | None = None
    author_id: str | None = None
    author_name: str | None = None
    #: The `provider_message_id` this replies to, if it is a reply.
    reply_to: str | None = None
    #: The task this message became, once it has become one. `None` for a run
    #: that belongs to no task, which is every run before triage has decided.
    task_id: int | None = None

    @classmethod
    def for_event(cls, event: InboundEvent, *, agent: str) -> FridayState:
        """The state a message's journey starts with.

        Seven of the nine fields are facts the inbound message already carries,
        and copying them here once is the whole point: they used to be named
        again, as a different subset, by `Triage.decide`, `Responder.draft`,
        `prepare`, `Extractor.run` and the recording sink — so adding one more
        fact meant threading one more parameter through five signatures.

        Deliberately absent until board `every-answer-has-a-shape`'s ticket 07
        gave it a caller: triage is where a journey actually starts, and a
        constructor with no caller is the speculative generality this board is
        otherwise removing.
        """
        return cls(
            channel_id=event.channel_id,
            agent=agent,
            provider=event.provider,
            thread_id=event.thread_id,
            message_id=event.provider_message_id,
            author_id=event.author_id,
            author_name=event.author_name,
            reply_to=event.reply_to,
        )

    @classmethod
    def for_conversation(
        cls, conversation: ConversationId, *, agent: str
    ) -> FridayState:
        """The state for work about a conversation rather than about one
        message — a task being worked on, which is what the pool has.

        `ConversationId` already is the provider-qualified place, so taking it
        whole is the same argument this class makes one level up: the pool
        used to hand a responder `channel_id`, `task_id` and `message_id` as
        three parameters and take the channel off a conversation it had in its
        hand.
        """
        return cls(
            channel_id=conversation.channel_id,
            agent=agent,
            provider=conversation.provider,
            thread_id=conversation.thread_id,
        )

    def as_agent(self, agent: str) -> FridayState:
        """Hand the run on to a different agent."""
        return replace(self, agent=agent)

    def for_task(self, task_id: int | None) -> FridayState:
        """The message became this task."""
        return replace(self, task_id=task_id)

    def about_message(self, message_id: str | None) -> FridayState:
        """This step is about a different message than the last one was."""
        return replace(self, message_id=message_id)
