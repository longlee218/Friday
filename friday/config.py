from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from friday.domain.models import MentionType

log = logging.getLogger(__name__)

DEFAULT_PATH = Path("config.yaml")


class ConfigError(Exception):
    """Configuration is missing or cannot be understood."""


@dataclass(frozen=True, slots=True)
class IngestConfig:
    """Which conversations and which kinds of mention are watched."""

    watched_channels: frozenset[str]
    mention_types: frozenset[MentionType]
    # Testing escape hatch only. Leave off in normal operation: once the agent
    # can reply as the account, capturing its own messages makes it answer
    # itself in a loop.
    capture_own_messages: bool = False
    # How often the recovery sweep runs. The live connection is the fast path;
    # this only exists to close gaps it missed.
    sweep_interval_seconds: float = 300.0
    # How many prior messages to pull in when a conversation first mentions us.
    context_messages: int = 20


_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

_REQUIRED_AGENT_FIELDS = ("api_key", "base_url", "model")


@dataclass(frozen=True, slots=True)
class AgentConfig:
    """One step that calls a model, and everything it needs to do so.

    Self-contained on purpose: reading this tells you where the model lives,
    which key reaches it, and how it should behave, without following a
    reference somewhere else.
    """

    name: str
    api_key: str
    base_url: str
    model: str
    settings: dict[str, Any] = field(default_factory=dict)
    max_turns: int = 1
    #: How much room this model has, in tokens. Providers vary, so this
    #: cannot be hardcoded — it decides when a conversation has grown large
    #: enough that summarising it costs less than passing it raw.
    context_window: int = 128_000
    #: Step-specific knobs the model layer does not care about, e.g. the
    #: confidence threshold for triage or the tone-example count for the
    #: responder.
    options: dict[str, Any] = field(default_factory=dict)
    #: Who this agent is, before it is told what its job is. The same text for
    #: every agent that asked for the same mode, stamped here rather than
    #: threaded through every construction site: `Harness` already receives
    #: this object, and the alternative is a new argument on the responder, on
    #: every extractor, and on every graph node.
    persona: str = ""


@dataclass(frozen=True, slots=True)
class WorkflowConfig:
    #: Let the responder write the ask in the operator's voice. Off by default:
    #: a drafted message needs approval, and the template does not.
    use_responder: bool = False
    #: How many times to ask for the same missing detail before handing the
    #: task to a person. Asking forever is how a helpful question becomes noise.
    max_asks: int = 3
    #: How long to let a burst of follow-ups settle before answering it.
    debounce_seconds: float = 45.0
    #: Send the "which environment / correlationId?" question without waiting
    #: for approval. The only reply allowed out unreviewed: it is the same
    #: question every time, and a wrong classification costs the reporter one
    #: unnecessary question. Everything else parks for a human.
    auto_ask_for_details: bool = False


@dataclass(frozen=True, slots=True)
class MCPServerConfig:
    """A tool server outside this process.

    Either `command` (started here, spoken to over stdio) or `url` (already
    running, spoken to over SSE). `allow` names the tools an agent may see —
    empty means all of them, which is a choice rather than an oversight.
    """

    name: str
    command: str = ""
    args: tuple[str, ...] = ()
    env: dict = field(default_factory=dict)
    url: str = ""
    allow: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OutboxConfig:
    #: How many times to try one message before handing it to a person.
    max_attempts: int = 3
    #: Doubling from here. Retrying a rate-limited send at once is how a rate
    #: limit becomes a ban.
    backoff_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class ContextConfig:
    """Where a channel's knowledge lives, and when it is worth summarising."""

    directory: str = "context"
    #: Where the operator's skills live. One Markdown file per skill; adding
    #: one is adding a file, with no list to edit.
    skills_directory: str = "skills"
    #: What share of the model's context window a conversation has to reach
    #: before a summary is worth a model call. Below it, the raw messages are
    #: cheaper than summarising them.
    summary_share: float = 0.5


@dataclass(frozen=True, slots=True)
class Config:
    database_path: str
    ingest: IngestConfig
    agents: dict[str, AgentConfig] = field(default_factory=dict)
    workflows: WorkflowConfig = field(default_factory=WorkflowConfig)
    outbox: OutboxConfig = field(default_factory=OutboxConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    mcp_servers: tuple[MCPServerConfig, ...] = ()
    #: Classifications the operator wrote by hand, as `(message, task type)`.
    #: Used whether or not anything has been marked in Discord — a fresh
    #: install has nothing marked, and waiting for the first reaction before
    #: the classifier sees any example at all is a worse start than none.
    triage_examples: tuple[tuple[str, str], ...] = ()
    #: How often to say the process is alive and what it is holding. A
    #: working agent on a quiet day is otherwise indistinguishable from a
    #: dead one.
    #: Who is asked to approve a reply. The bot direct-messages them; it
    #: needs no shared server, verified against the live account.
    operator_id: int = 0
    #: The read-only debug view. Loopback by default: it shows every
    #: captured message and model prompt, and has no authentication.
    board_host: str = "127.0.0.1"
    board_port: int = 8086
    #: Browser origins allowed to read the API — the frontend in development.
    board_origins: tuple[str, ...] = ()
    heartbeat_seconds: float = 60.0
    #: How long the gateway may be down before the operator is told. Discord
    #: drops and resumes constantly; alerting on a blip trains you to ignore
    #: the alert that matters.
    down_after_seconds: float = 300.0
    #: Hour of the day for the 'still alive' summary. None to not send one.
    summary_at_hour: int | None = 9
    #: How long to keep model calls. Prompts are large and nobody reads old
    #: ones; a container that never restarts would fill its volume.
    keep_model_calls_days: float = 14.0


def load_config(path: Path | str = DEFAULT_PATH) -> Config:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except FileNotFoundError as exc:
        raise ConfigError(f"No configuration file at {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Could not parse {path}: {exc}") from exc

    from friday.agent.persona import load as load_persona

    # Read before the agents, because every one of them is stamped with it.
    # Relative to the configuration file, not to the working directory: the
    # two are the same when run from the repository and are not the same in a
    # container, and the file that names it is the one it sits beside.
    persona_path = (raw.get("persona") or {}).get("file", "PERSONA.md")
    # Debug, not info: `load_config` runs twice at startup — once from the
    # Alembic environment for the database path, once by the composition root
    # — so anything said here is said twice. What the operator needs to know
    # about the persona is which agents took which mode, and that belongs in
    # the startup summary, not here.
    persona = load_persona(path.parent / persona_path)
    log.debug("persona: %d section(s) from %s", len(persona), persona_path)

    ingest = raw.get("ingest") or {}
    return Config(
        agents=_agents(_expand(raw.get("agents") or {}), persona),
        workflows=WorkflowConfig(
            max_asks=int((raw.get("workflows") or {}).get("max_asks", 3)),
            debounce_seconds=float(
                (raw.get("workflows") or {}).get("debounce_seconds", 45.0)
            ),
            use_responder=bool(
                (raw.get("workflows") or {}).get("use_responder", False)
            ),
            auto_ask_for_details=bool(
                (raw.get("workflows") or {}).get("auto_ask_for_details", False)
            ),
        ),
        outbox=OutboxConfig(
            max_attempts=int((raw.get("outbox") or {}).get("max_attempts", 3)),
            backoff_seconds=float(
                (raw.get("outbox") or {}).get("backoff_seconds", 30.0)
            ),
        ),
        context=ContextConfig(
            directory=str((raw.get("context") or {}).get("directory", "context")),
            skills_directory=str(
                (raw.get("context") or {}).get("skills_directory", "skills")
            ),
            summary_share=float(
                (raw.get("context") or {}).get("summary_share", 0.5)
            ),
        ),
        operator_id=int(raw.get("operator_id", 0)),
        # Overridable from the environment, because the container needs a
        # different answer from the laptop and they share config.yaml.
        board_host=os.environ.get(
            "FRIDAY_BOARD_HOST", str(raw.get("board_host", "127.0.0.1"))
        ),
        board_port=int(raw.get("board_port", 8086)),
        board_origins=tuple(raw.get("board_origins") or ()),
        heartbeat_seconds=float(raw.get("heartbeat_seconds", 60.0)),
        down_after_seconds=float(raw.get("down_after_seconds", 300.0)),
        summary_at_hour=(
            None if raw.get("summary_at_hour", 9) is None
            else int(raw.get("summary_at_hour", 9))
        ),
        keep_model_calls_days=float(raw.get("keep_model_calls_days", 14.0)),
        mcp_servers=_mcp_servers(_expand(raw.get("mcp_servers") or {})),
        triage_examples=_triage_examples(raw.get("triage_examples") or []),
        database_path=raw.get("database_path", "./data/friday.db"),
        ingest=IngestConfig(
            # Coerced to str: an unquoted id in YAML parses as an int and
            # would then never match, silently watching nothing.
            watched_channels=frozenset(
                str(c) for c in ingest.get("watched_channels") or ()
            ),
            mention_types=frozenset(
                _mention_type(value) for value in ingest.get("mention_types") or ()
            ),
            capture_own_messages=bool(ingest.get("capture_own_messages", False)),
            sweep_interval_seconds=float(
                ingest.get("sweep_interval_seconds", 300.0)
            ),
            context_messages=int(ingest.get("context_messages", 20)),
        ),
    )


def _expand(value: Any) -> Any:
    """Replace ${VAR} with the environment, failing loudly when unset.

    An unset variable substituted as an empty string surfaces later as an
    unexplained 401 from the provider. Naming it here is the whole point.
    """
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            try:
                return os.environ[name]
            except KeyError:
                raise ConfigError(
                    f"{name} is referenced in the configuration but is not set. "
                    f"Add it to .env (see .env.example)."
                ) from None

        return _ENV_REF.sub(replace, value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def _agents(raw: dict[str, Any], persona: "Persona | None" = None) -> dict:
    agents = {}
    for name, spec in raw.items():
        spec = dict(spec or {})
        missing = [f for f in _REQUIRED_AGENT_FIELDS if not spec.get(f)]
        if missing:
            raise ConfigError(
                f"Agent {name!r} is missing: {', '.join(missing)}"
            )
        agents[name] = AgentConfig(
            name=name,
            api_key=spec.pop("api_key"),
            base_url=spec.pop("base_url"),
            model=spec.pop("model"),
            settings=spec.pop("settings", None) or {},
            max_turns=int(spec.pop("max_turns", 1)),
            context_window=int(spec.pop("context_window", 128_000)),
            persona=_persona_for(name, spec.pop("persona", "full"), persona),
            options=spec,  # whatever is left is step-specific
        )
    return agents


def _persona_for(agent: str, mode: Any, persona: "Persona | None") -> str:
    """Resolve one agent's `persona:` setting into the text it will carry.

    The mode is validated here, when the file is read, rather than where it is
    used. A typo silently giving an agent no persona is the one failure that
    leaves no trace anywhere — nothing errors, the replies just stop sounding
    like anyone.
    """
    from friday.agent.persona import Mode

    try:
        wanted = Mode(str(mode))
    except ValueError as exc:
        known = ", ".join(m.value for m in Mode)
        raise ConfigError(
            f"Agent {agent!r} has persona: {mode!r}. Known modes: {known}"
        ) from exc
    return persona.render(wanted) if persona is not None else ""


def _mention_type(value: str) -> MentionType:
    try:
        return MentionType(value)
    except ValueError as exc:
        known = ", ".join(m.value for m in MentionType)
        raise ConfigError(
            f"Unknown mention type {value!r}. Known types: {known}"
        ) from exc


def _mcp_servers(raw: dict) -> tuple[MCPServerConfig, ...]:
    """A server with neither a command nor a url is half a connection, which is
    worse than none: it fails at the first tool call, inside an agent run, hours
    after anyone edited the file."""
    servers = []
    for name, spec in raw.items():
        spec = spec or {}
        if not spec.get("command") and not spec.get("url"):
            raise ConfigError(
                f"mcp server {name!r} needs either a command (stdio) or a url (sse)."
            )
        servers.append(
            MCPServerConfig(
                name=name,
                command=spec.get("command", ""),
                args=tuple(spec.get("args") or ()),
                env=dict(spec.get("env") or {}),
                url=spec.get("url", ""),
                allow=tuple(spec.get("allow") or ()),
            )
        )
    return tuple(servers)


def _triage_examples(raw) -> tuple[tuple[str, str], ...]:
    """Hand-written classifications, as `(message, task type)` pairs.

    Written as a list of one-key mappings so the file reads as examples
    rather than as configuration:

        triage_examples:
          - "the checkout api is 500ing": api_issue
          - "can I get access to the payments repo": access_request

    A malformed entry is refused rather than skipped. An example the operator
    believes they wrote, and which silently is not there, is worse than a
    startup that says which line is wrong.
    """
    if not isinstance(raw, list):
        # Without this, a string iterates character by character and the error
        # names `triage_examples[0] ... got 'o'` — the first letter of the
        # value, which sends the reader looking for a list entry that does not
        # exist.
        raise ConfigError(
            f"triage_examples should be a list of 'message: task_type' pairs, "
            f"got {type(raw).__name__}"
        )
    examples: list[tuple[str, str]] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict) or len(entry) != 1:
            raise ConfigError(
                f"triage_examples[{index}] should be one 'message: task_type' "
                f"pair, got {entry!r}"
            )
        (message, kind), = entry.items()
        if not isinstance(message, str) or not isinstance(kind, str):
            raise ConfigError(
                f"triage_examples[{index}]: both the message and the task "
                f"type must be text, got {entry!r}"
            )
        examples.append((message, kind))
    return tuple(examples)
