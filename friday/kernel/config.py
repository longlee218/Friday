from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

from friday.kernel.domain.messages import MentionType
from friday.sdk.agent import AgentDeclaration

log = logging.getLogger(__name__)

DEFAULT_PATH = Path("config.yaml")


class ConfigError(Exception):
    """Configuration is missing or cannot be understood."""


@dataclass(frozen=True, slots=True)
class IngestConfig:
    """Which conversations and which kinds of mention are watched."""

    watched_channels: frozenset[str]
    mention_types: frozenset[MentionType]


_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

_REQUIRED_TIER_FIELDS = ("api_key", "base_url", "model")

#: Shorthand for the `base_url` of the OpenAI-compatible providers Friday's
#: first version runs on. A `provider:` in a tier block fills the `base_url`
#: from this, so config names the provider instead of pasting a URL — nothing
#: in `harness.py` changes, because all three speak Chat Completions and the
#: only thing that varies is where the request goes. An explicit `base_url`
#: still wins, for a provider not listed here or a custom endpoint.
PROVIDER_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "minimax": "https://api.minimax.io/v1",
    "deepseek": "https://api.deepseek.com",
    # One key, every model. OpenRouter is OpenAI-compatible, so `provider:
    # openrouter` + a `deepseek/…` or `google/…` model id is all a tier needs.
    "openrouter": "https://openrouter.ai/api/v1",
}


@dataclass(frozen=True, slots=True)
class TierConfig:
    """A named model tier: where a model lives and which key reaches it.

    Named freely in `config.yaml` (`flash`, `strong`); code picks one by name
    (board `domains-plug-in`, ticket 07). `settings` are provider settings
    (`max_tokens`), not behaviour — temperature is per job and sits on the
    agent declaration.
    """

    name: str
    api_key: str
    base_url: str
    model: str
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentConfig:
    """One step that calls a model, and everything it needs to do so: its
    tier's endpoint and key, and its declaration's budget and temperature,
    resolved by `Config.agent`."""

    name: str
    api_key: str
    base_url: str
    model: str
    settings: dict[str, Any] = field(default_factory=dict)
    #: Every request to the model in one run, tool turns included.
    max_turns: int = 1
    #: Input + output tokens summed over one run; `None` is no ceiling.
    tokens: int | None = None


@dataclass(frozen=True, slots=True)
class WorkflowConfig:
    #: Let the responder write the ask in the operator's voice. Off by default:
    #: a drafted message needs approval, and the template does not.
    use_responder: bool = False
    #: How many times to ask for the same missing detail before handing the
    #: task to a person. Asking forever is how a helpful question becomes noise.
    max_asks: int = 3
    #: Send the "which environment / correlationId?" question without waiting
    #: for approval. The only reply allowed out unreviewed: it is the same
    #: question every time, and a wrong classification costs the reporter one
    #: unnecessary question. Everything else hands over to a human.
    auto_ask_for_details: bool = False
    #: How many tasks the pool works at once. Small on purpose: every graph
    #: shares one SQLite file and one provider's rate limit, and the point is
    #: only that a slow graph does not hold every other task behind it.
    concurrency: int = 2


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
    #: `http` (streamable HTTP, the default for a `url`) or `sse`. The
    #: server decides which it speaks; an SSE client against an HTTP server
    #: fails at connect saying nothing about transports.
    transport: str = "http"
    #: Sent with every request. For a header a server wants that is not a
    #: credential; a credential comes from `auth` instead, because a token
    #: written here is a token somebody has to rewrite when it lapses.
    headers: dict = field(default_factory=dict)
    #: How to obtain a credential, or `None` for a server that wants none.
    #:
    #: **`{}` and `None` are different answers**, which is why this is not a
    #: plain dict with a default: `auth: {}` says "this server needs signing
    #: in to, and everything about how is discovered", and leaving the key
    #: out says "it needs nothing". Both servers here are the first case, and
    #: writing their endpoints down would be writing down what one HTTP GET
    #: already says.
    #:
    #: Keys, all optional and all pinning something otherwise discovered:
    #: `authorize_url`, `token_url`, `registration_url`, `client_id`,
    #: `scope`, `client_secret`, `resource`.
    #:
    #: The operator signs in once with `authorize.py`, which discovers the
    #: endpoints, registers this machine, and keeps a refresh token; this
    #: process exchanges that for an access token and never asks anyone
    #: anything.
    auth: dict | None = None


@dataclass(frozen=True, slots=True)
class ContextConfig:
    """What a task's transcript may cost, and where the skills live. A channel's knowledge lived here too, as a directory of YAML
    files; it is rows in the database now (board
    `read-it-the-way-the-operator-does`, ticket 10)."""

    #: Where the operator's skills live. One Markdown file per skill; adding
    #: one is adding a file, with no list to edit.
    skills_directory: str = "skills"
    #: Node 0's own budget, in *estimated* tokens — characters divided by
    #: four (D5): the configured provider is MiniMax, for which there is no
    #: tokenizer, and a tokenizer for a different vendor would be
    #: confidently wrong rather than roughly right. `None` means no
    #: compaction at all (D7): the measurement runs from the first day
    #: and the ceiling is something the operator sets once a normal task's
    #: cost is known. Board `what-the-room-already-knows`, ticket 08.
    extraction_budget_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class Config:
    database_path: str
    ingest: IngestConfig
    #: The named model tiers, by name. Code picks one by name through
    #: `agent`; there is no per-agent block.
    tiers: dict[str, TierConfig] = field(default_factory=dict)
    workflows: WorkflowConfig = field(default_factory=WorkflowConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    mcp_servers: tuple[MCPServerConfig, ...] = ()
    #: The plugins this build loads, by import path (`friday.kernel.plugin_host` reads
    #: each package's `PLUGIN`). A plugin owns its own config block, validated
    #: against its own schema — the core no longer names any of them.
    plugins: tuple[str, ...] = ("plugins.backend", "plugins.ops")
    #: The raw config mapping, so `friday.kernel.plugin_host` can read a plugin's own
    #: block by its id (`plugin_blocks["backend"]`) and hand it to the plugin's
    #: validator. Kept raw because the core does not know a plugin's schema.
    plugin_blocks: Mapping[str, Any] = field(default_factory=dict)
    #: Classifications the operator wrote by hand, as `(message, task type)`.
    #: Used whether or not anything has been marked in Discord — a fresh
    #: install has nothing marked, and waiting for the first reaction before
    #: the classifier sees any example at all is a worse start than none.
    triage_examples: tuple[tuple[str, str], ...] = ()
    #: Words that keep a message away from the model. The operator adds to
    #: this as they notice things, so it is a line in `config.yaml` and a
    #: restart rather than a commit.
    sensitive_words: tuple[str, ...] = ()
    #: Who is asked to approve a reply. The bot direct-messages them; it
    #: needs no shared server, verified against the live account.
    operator_id: int = 0
    #: The read-only debug view. Loopback by default: it shows every
    #: captured message and model prompt, and has no authentication.
    board_host: str = "127.0.0.1"
    board_port: int = 8086
    #: Browser origins allowed to read the API — the frontend in development.
    board_origins: tuple[str, ...] = ()
    #: Where the `repo_path` picker may look (ticket 19). Empty turns it off:
    #: a board that browses `/` by default is one nobody meant to switch on.
    #: The route resolves under this and refuses anything landing outside,
    #: the same guard a stack frame meets.
    repo_root: str = ""
    #: Where the daily backup writes both SQLite files (§12.1). Under `data/`
    #: by default (already git-ignored), in its own subdirectory so a backup is
    #: never mistaken for the live db a restore would overwrite.
    backup_dir: str = "data/backups"

    def agent(self, declaration: AgentDeclaration) -> AgentConfig:
        """The declared agent on its tier. An undeclared tier refuses the
        boot: every agent is built at boot, and a name code picks that
        `config.yaml` does not carry is a typo or a missing block, never a
        reason to run without the model."""
        tier = self.tiers.get(declaration.tier)
        if tier is None:
            declared = ", ".join(sorted(self.tiers)) or "none"
            raise ConfigError(
                f"Agent {declaration.name!r} runs on tier {declaration.tier!r}, "
                f"which config.yaml does not declare (tiers: {declared})"
            )
        return AgentConfig(
            name=declaration.name,
            api_key=tier.api_key,
            base_url=tier.base_url,
            model=tier.model,
            settings={
                **tier.settings,
                "temperature": declaration.temperature,
                "timeout": declaration.request_timeout_seconds,
            },
            max_turns=declaration.max_turns,
            tokens=declaration.tokens,
        )


def declared_secrets(config: Config, *tokens: str | None) -> set[str]:
    """Every secret this deployment was configured with, for value-based
    redaction (DESIGN-v2 §12).

    The tiers' API keys, each MCP server's declared env values and any auth
    client secret, plus whatever tokens the caller holds (the Discord user and
    bot tokens, read from the environment by the composition root). Here rather
    than in the composition root because reading `config.tiers` is this module's
    job, not the root's (`test_composition_root_reads_no_agent_config`). Blank
    and one-character values are dropped by `register_secret_values`.
    """
    secrets: set[str] = {t for t in tokens if t}
    for tier in config.tiers.values():
        if tier.api_key:
            secrets.add(tier.api_key)
    for server in config.mcp_servers:
        secrets.update(str(v) for v in server.env.values())
        if server.auth:
            client_secret = server.auth.get("client_secret")
            if client_secret:
                secrets.add(str(client_secret))
    return secrets


def load_config(path: Path | str = DEFAULT_PATH) -> Config:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except FileNotFoundError as exc:
        raise ConfigError(f"No configuration file at {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Could not parse {path}: {exc}") from exc

    _refuse_moved_knobs(raw)
    ingest = raw.get("ingest") or {}
    return Config(
        tiers=_tiers(_expand(raw.get("tiers") or {})),
        workflows=WorkflowConfig(
            max_asks=int((raw.get("workflows") or {}).get("max_asks", 3)),
            use_responder=bool(
                (raw.get("workflows") or {}).get("use_responder", False)
            ),
            auto_ask_for_details=bool(
                (raw.get("workflows") or {}).get("auto_ask_for_details", False)
            ),
            concurrency=_slots((raw.get("workflows") or {}).get("concurrency", 2)),
        ),
        context=ContextConfig(
            skills_directory=str(
                (raw.get("context") or {}).get("skills_directory", "skills")
            ),
            extraction_budget_tokens=_positive_or_none(
                (raw.get("context") or {}).get("extraction_budget_tokens"),
                "context.extraction_budget_tokens",
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
        repo_root=str(raw.get("repo_root") or ""),
        backup_dir=str(raw.get("backup_dir") or "data/backups"),
        plugins=tuple(raw.get("plugins") or ("plugins.backend", "plugins.ops")),
        # Raw, so `friday.kernel.plugin_host` can read each plugin's own block by id
        # and validate it against the plugin's own schema — the core does not
        # know a plugin's config shape.
        plugin_blocks=raw,
        mcp_servers=_mcp_servers(_expand(raw.get("mcp_servers") or {})),
        triage_examples=_triage_examples(raw.get("triage_examples") or []),
        sensitive_words=_sensitive_words(raw.get("sensitive_words") or []),
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


#: Keys `config.yaml` used to carry and no longer reads, and where each went.
#: Refused rather than ignored: a knob the operator edits and nothing reads is
#: a change that silently does nothing (board `domains-plug-in`, ticket 07).
_MOVED_KNOBS = {
    ("agents",): "named model tiers under `tiers:`; each agent's budget and "
    "temperature are a declaration in code",
    ("outbox",): "`OUTBOX_ATTEMPTS` / `OUTBOX_BACKOFF_SECONDS` in friday/kernel/outbox",
    ("heartbeat_seconds",): "`HEARTBEAT_SECONDS` in friday/kernel/ops/liveness.py",
    ("down_after_seconds",): "`DOWN_AFTER_SECONDS` in friday/kernel/ops/liveness.py",
    ("summary_at_hour",): "`SUMMARY_AT_HOUR` in friday/kernel/ops/liveness.py",
    ("keep_model_calls_days",): "`KEEP_MODEL_CALLS_DAYS` in friday/kernel/ops/liveness.py",
    ("keep_backups",): "`KEEP_BACKUPS` in friday/kernel/ops/backup.py",
    ("ingest", "turn_seconds"): "`TURN_SECONDS` in friday/kernel/inbox",
    ("ingest", "sweep_interval_seconds"): "`SWEEP_INTERVAL_SECONDS` in friday/kernel/inbox",
    ("ingest", "context_messages"): "`CONTEXT_MESSAGES` in friday/kernel/inbox",
    ("context", "summary_max_chars"): "`SUMMARY_MAX_CHARS` in "
    "friday/kernel/memory/channel_context.py",
}


def _refuse_moved_knobs(raw: Mapping[str, Any]) -> None:
    for keys, where in _MOVED_KNOBS.items():
        block: Any = raw
        for key in keys[:-1]:
            block = block.get(key) if isinstance(block, Mapping) else None
        if isinstance(block, Mapping) and keys[-1] in block:
            raise ConfigError(
                f"{'.'.join(keys)} is no longer read from config.yaml — it is "
                f"{where}. Remove it from the file."
            )


def _tiers(raw: dict[str, Any]) -> dict[str, TierConfig]:
    tiers = {}
    for name, spec in raw.items():
        spec = dict(spec or {})
        # `provider: minimax` is shorthand for its `base_url`. An explicit
        # `base_url` wins — for a custom endpoint, or a provider not listed.
        provider = spec.pop("provider", None)
        if provider is not None and not spec.get("base_url"):
            if provider not in PROVIDER_BASE_URLS:
                raise ConfigError(
                    f"Tier {name!r}: provider {provider!r} is not one of "
                    f"{', '.join(sorted(PROVIDER_BASE_URLS))} — set `base_url` "
                    f"directly for anything else"
                )
            spec["base_url"] = PROVIDER_BASE_URLS[provider]
        missing = [f for f in _REQUIRED_TIER_FIELDS if not spec.get(f)]
        if missing:
            hint = (
                " (or a `provider:` shorthand)" if "base_url" in missing else ""
            )
            raise ConfigError(
                f"Tier {name!r} is missing: {', '.join(missing)}{hint}"
            )
        tiers[name] = TierConfig(
            name=name,
            api_key=spec.pop("api_key"),
            base_url=spec.pop("base_url"),
            model=spec.pop("model"),
            settings=spec.pop("settings", None) or {},
        )
        if spec:
            raise ConfigError(
                f"Tier {name!r}: {', '.join(sorted(spec))} is not a tier key — "
                f"a tier is api_key, provider/base_url, model and settings; "
                f"behaviour is declared in code beside the agent"
            )
    return tiers


def _slots(value: Any) -> int:
    """Zero slots is a pool that takes every task and never acts on one —
    refused where the operator is looking, not discovered as a silent stall."""
    parsed = int(value)
    if parsed < 1:
        raise ConfigError(
            f"workflows.concurrency must be at least 1 — got {parsed}"
        )
    return parsed


def _positive_or_none(value: Any, name: str) -> int | None:
    """`None` unset, a positive int given — never anything in between.

    Board `what-the-room-already-knows`, ticket 08, D6: "a budget clause
    that cannot be evaluated fails loudly; it is never dropped" — the
    failure shape that emptied five context mechanisms in this repo was a
    bad value tolerated at run time rather than refused where the operator
    is looking. Zero and negative are exactly as unevaluable as a budget can
    be, so they raise here rather than reaching `original_text_for` as a
    ceiling that refuses every message on every task, silently.
    """
    if value is None:
        return None
    parsed = int(value)
    if parsed <= 0:
        raise ConfigError(
            f"{name} must be a positive number of estimated tokens, or unset "
            f"for no compaction — got {parsed}"
        )
    return parsed


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
                f"mcp server {name!r} needs either a command (stdio) or a url."
            )
        if "allow" in spec:
            raise ConfigError(
                f"mcp server {name!r}: `allow` is no longer read here. Which "
                "tools may be called is declared in code, on the class that "
                "calls them — see `friday.sources.Reads` — so a file cannot "
                "widen it."
            )
        transport = str(spec.get("transport", "http"))
        if transport not in {"http", "sse"}:
            raise ConfigError(
                f"mcp server {name!r}: transport {transport!r} is not one of "
                "http, sse"
            )
        servers.append(
            MCPServerConfig(
                name=name,
                command=spec.get("command", ""),
                args=tuple(spec.get("args") or ()),
                env=dict(spec.get("env") or {}),
                url=spec.get("url", ""),
                transport=transport,
                headers=dict(spec.get("headers") or {}),
                auth=None if spec.get("auth") is None else dict(spec["auth"]),
            )
        )
    return tuple(servers)


def _sensitive_words(raw) -> tuple[str, ...]:
    """A flat list of words and phrases. Refused if it is anything else.

    A string here iterates character by character, and the resulting rule holds
    every message containing the letter "l" — a failure that looks like the
    whole system going quiet.
    """
    if not isinstance(raw, list):
        raise ConfigError(
            f"sensitive_words should be a list of words, got {type(raw).__name__}"
        )
    bad = [w for w in raw if not isinstance(w, str)]
    if bad:
        raise ConfigError(f"sensitive_words should all be text, got {bad!r}")
    return tuple(w.strip() for w in raw if w.strip())


def _triage_examples(raw) -> tuple[tuple[str, str], ...]:
    """Hand-written classifications, as `(message, task type)` pairs.

    Written as a list of one-key mappings so the file reads as examples
    rather than as configuration:

        triage_examples:
          - "the checkout api is 500ing": backend.trace_problem
          - "can I get access to the payments repo": ops.request_permission

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
