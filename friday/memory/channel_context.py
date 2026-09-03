"""What the agent knows about one channel, and what the operator has told it.

Two kinds of knowledge share one file, and the split has to be visible in it.
**derived** is machine-written from what has been learned — safe to delete,
because it rebuilds. **overrides** is the operator's, and a rebuild never
touches it: that is what makes a correction stick rather than surviving until
the next rebuild.

A file per channel inherits a base file that holds what is true everywhere —
who the agent is, how it behaves. YAML rather than JSON, for the comments: a
fact without its reason next to it is a fact nobody dares change.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from friday.config import AgentConfig
from friday.store.db import Database
from friday.agent.harness import Harness
from friday.memory.notes import Promotion

__all__ = ["ChannelContext", "ContextRebuilder", "ContextStore"]

log = logging.getLogger(__name__)

BASE_NAME = "base.yaml"

SUMMARY_INSTRUCTIONS = """Summarise this conversation in a few sentences: who
is asking for what, and where things stand. This is written once and read
many times, so favour what is still true over exactly what was said."""


@dataclass(frozen=True, slots=True)
class ChannelContext:
    """One channel's knowledge, already layered: base < derived < overrides."""

    channel_id: str
    base: dict[str, Any]
    derived: dict[str, Any]
    overrides: dict[str, Any]

    def merged(self) -> dict[str, Any]:
        return {**self.base, **self.derived, **self.overrides}


class ContextStore:
    @classmethod
    def build(cls, config) -> "ContextStore":
        store = cls(config.context.directory).hold_all()
        for problem in store.validate_all():
            log.warning("channel context file could not be read — %s", problem)
        if store._held:
            log.info("channel context for %d channel(s)", len(store._held))
        return store

    """One YAML file per channel, inheriting `base.yaml`.

    Reads tolerate a missing or malformed file — a channel the agent has never
    heard from, or one an operator is mid-edit on, still lets every other
    channel work. Writes go through `rebuild_derived` (the machine's section)
    or `init_channel` (the operator's); nothing else in this module writes a
    file.
    """

    def __init__(self, directory: Path | str) -> None:
        self._dir = Path(directory)
        #: Read once at startup, like everything else the operator writes.
        #: Reading per message would be file I/O on the event loop; and two
        #: rules for "when does my edit take effect" is one too many.
        self._held: dict[str, ChannelContext] = {}

    def hold_all(self) -> "ContextStore":
        self._held = {c: self.load(c) for c in self.known_channels()}
        return self

    def context(self, channel_id: str) -> ChannelContext | None:
        """The held context for a channel, or None if it has no file."""
        return self._held.get(channel_id)

    def base(self) -> dict[str, Any]:
        return self._read(self._dir / BASE_NAME) or {}

    def load(self, channel_id: str) -> ChannelContext:
        raw = self._read(self.path_for(channel_id)) or {}
        return ChannelContext(
            channel_id=channel_id,
            base=self.base(),
            derived=raw.get("derived") or {},
            overrides=raw.get("overrides") or {},
        )

    def init_channel(
        self, channel_id: str, overrides: dict[str, Any] | None = None
    ) -> None:
        """Create a channel's file before the agent has learned anything.

        Refuses to overwrite one that already exists — the whole point of
        `overrides` is that nothing clobbers it, an operator's own `init` call
        included.
        """
        path = self.path_for(channel_id)
        if path.exists():
            raise FileExistsError(f"{path} already exists — edit it directly")
        self._write(path, {"derived": {}, "overrides": overrides or {}})

    def rebuild_derived(self, channel_id: str, derived: dict[str, Any]) -> None:
        """Replace the machine-written section. Never touches `overrides`."""
        existing = self._read(self.path_for(channel_id)) or {}
        existing["derived"] = derived
        existing.setdefault("overrides", {})
        self._write(self.path_for(channel_id), existing)
        # The writer refreshes what is held. The learned layer is the one part
        # the operator does not write, so it must not wait for a restart.
        self._held[channel_id] = self.load(channel_id)

    def known_channels(self) -> list[str]:
        """Channels with a file already — the set a rebuild considers."""
        if not self._dir.exists():
            return []
        return sorted(
            p.stem for p in self._dir.glob("*.yaml") if p.name != BASE_NAME
        )

    def validate_all(self) -> list[str]:
        """Every file that fails to parse, by name.

        Called at startup so a bad file is visible immediately, rather than
        discovered when a classification quietly ran without context nobody
        meant to drop.
        """
        problems = []
        if not self._dir.exists():
            return problems
        paths = [*self._dir.glob("*.yaml")]
        for path in paths:
            try:
                yaml.safe_load(path.read_text())
            except yaml.YAMLError as exc:
                problems.append(f"{path}: {exc}")
        return problems

    def path_for(self, channel_id: str) -> Path:
        return self._dir / f"{channel_id}.yaml"

    def _read(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            return yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as exc:
            log.warning("could not parse %s: %s", path, exc)
            return None

    def _write(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# derived is machine-written and safe to delete — it rebuilds.\n"
            "# overrides is yours. Nothing here ever overwrites it.\n"
            + yaml.safe_dump(data, sort_keys=False)
        )


class ContextRebuilder:
    """Rebuilds every known channel's derived section when something has been
    learned.

    Rides `Promotion`'s existing cadence rather than owning a timer: a
    rebuild that fires whether or not anything changed would spend a summary
    call on channels with nothing new to say.
    """

    @classmethod
    def build(cls, config, *, store, db, promotion) -> "ContextRebuilder":
        """Which agent summarises a channel, and when, are this module's
        business. The composition root asks for a rebuilder."""
        return cls(
            store=store,
            db=db,
            promotion=promotion,
            summary_config=config.agents.get("summary"),
            summary_share=config.context.summary_share,
        )

    def __init__(
        self,
        *,
        store: ContextStore,
        db: Database,
        promotion: Promotion,
        summary_config: AgentConfig | None = None,
        summary_share: float = 0.5,
        model=None,
    ) -> None:
        self._store = store
        self._db = db
        self._promotion = promotion
        self._summary_config = summary_config
        self._summary_share = summary_share
        #: Test seam, same convention as `Triage`/`Responder`: a real run
        #: never passes this, and a scripted one never touches the network.
        self._model = model

    async def rebuild_all(self) -> None:
        learned = await self._promotion.render()
        for channel_id in self._store.known_channels():
            derived: dict[str, Any] = {}
            if learned:
                derived["learned"] = learned
            summary = await self._maybe_summarize(channel_id)
            if summary:
                derived["summary"] = summary
            self._store.rebuild_derived(channel_id, derived)

    async def _maybe_summarize(self, channel_id: str) -> str | None:
        if self._summary_config is None:
            return None
        messages = await self._db.relevant_messages_in_channel("discord", channel_id)
        if not messages:
            return None
        # A rough count, not an exact one: the threshold it is compared
        # against is itself a configured share, so precision here buys
        # nothing a cheaper estimate would not.
        tokens = sum(len(m.text) for m in messages) // 4
        if tokens < self._summary_config.context_window * self._summary_share:
            return None
        harness = Harness(
            config=self._summary_config,
            instructions=SUMMARY_INSTRUCTIONS,
            model=self._model,
        )
        result = await harness.run(_transcript(messages))
        return result.final_output if result else None


def _transcript(messages) -> str:
    """The conversation, through the one boundary that escapes it.

    `instruction_prompt.conversation` renders exactly this and escapes both
    halves, with a comment saying why: on Discord a nickname is as
    attacker-controlled as a message. This path built its own string and
    escaped neither — and its output is stored as the channel's derived
    summary, which every later prompt for that room reads.
    """
    # Deferred: `instruction_prompt` imports `ChannelContext` from this
    # module, so importing it at load time is a cycle. Same reason
    # `harness.py` defers `llm_log`.
    from friday.agent.instruction_prompt import conversation, user_input

    return user_input(conversation(list(messages)).render())
