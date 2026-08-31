from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from friday.models import MentionType

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
    #: Step-specific knobs the model layer does not care about, e.g. the
    #: confidence threshold for triage or the tone-example count for the
    #: responder.
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkflowConfig:
    #: How many times to ask for the same missing detail before handing the
    #: task to a person. Asking forever is how a helpful question becomes noise.
    max_asks: int = 3
    #: Send the "which environment / correlationId?" question without waiting
    #: for approval. The only reply allowed out unreviewed: it is the same
    #: question every time, and a wrong classification costs the reporter one
    #: unnecessary question. Everything else parks for a human.
    auto_ask_for_details: bool = False


@dataclass(frozen=True, slots=True)
class OutboxConfig:
    #: How many times to try one message before handing it to a person.
    max_attempts: int = 3
    #: Doubling from here. Retrying a rate-limited send at once is how a rate
    #: limit becomes a ban.
    backoff_seconds: float = 30.0


@dataclass(frozen=True, slots=True)
class Config:
    database_path: str
    ingest: IngestConfig
    agents: dict[str, AgentConfig] = field(default_factory=dict)
    workflows: WorkflowConfig = field(default_factory=WorkflowConfig)
    outbox: OutboxConfig = field(default_factory=OutboxConfig)
    #: How often to say the process is alive and what it is holding. A
    #: working agent on a quiet day is otherwise indistinguishable from a
    #: dead one.
    heartbeat_seconds: float = 60.0


def load_config(path: Path | str = DEFAULT_PATH) -> Config:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except FileNotFoundError as exc:
        raise ConfigError(f"No configuration file at {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Could not parse {path}: {exc}") from exc

    ingest = raw.get("ingest") or {}
    return Config(
        agents=_agents(_expand(raw.get("agents") or {})),
        workflows=WorkflowConfig(
            max_asks=int((raw.get("workflows") or {}).get("max_asks", 3)),
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
        heartbeat_seconds=float(raw.get("heartbeat_seconds", 60.0)),
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


def _agents(raw: dict[str, Any]) -> dict[str, AgentConfig]:
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
            options=spec,  # whatever is left is step-specific
        )
    return agents


def _mention_type(value: str) -> MentionType:
    try:
        return MentionType(value)
    except ValueError as exc:
        known = ", ".join(m.value for m in MentionType)
        raise ConfigError(
            f"Unknown mention type {value!r}. Known types: {known}"
        ) from exc
