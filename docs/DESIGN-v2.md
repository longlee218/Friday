# friday-agents — Design v2 (target architecture)

**Status: accepted target (operator, 2026-09-22), revised 2026-09-21 after
a five-lens review (Appendix A), an external review (Appendix B) and an
independent review (Appendix C), then re-sequenced 2026-09-22 (Appendix D,
`docs/adr/0001-runtime-libraries-before-plugin-migration.md`). Nothing here
is built yet.** `docs/DESIGN.md` describes the system as it
exists and stays authoritative until a piece of this document is
implemented; each migration step (§15) moves its section across and
deletes it here. Where the two disagree today, `DESIGN.md` and the code
are right. **Where the body below and Appendix D disagree, Appendix D and
ADR 0001 win** — the body is being brought into line as the plan (§15) is
executed.

The one-line version: **a small kernel that owns the safety invariants,
and everything else — every task type, tool, source, memory kind, skill,
and later every channel, model provider and store — contributed through
one registration API.** Friday's own `api_issue` graph and Discord
integration become plugins like anybody else's; the kernel does not know
their names.

How to read this document: every contribution type has a **now** form
(small, built in the migration) and a **later** form with the **trigger**
that justifies building it (§16). The later forms are designed here so
that building them is an addition, not a rewrite — but they are not built
before their trigger fires.

---

## 1. Goals and non-goals

**Goals**

- **G1. Everything is a plugin.** Adding a task type, a tool, a source or
  a memory kind is adding a plugin. No literal registry in the kernel
  (today: `PARAMS`, `EXTRACTS`, `_graphs`, `_READERS`/`_WRITERS`, the
  sender map).
- **G2. Personas beyond developers.** A designer, a PM or a doctor gets
  Friday by installing a different set of plugins, not a fork.
- **G3. Organisations later, without a rewrite.** The data model names
  scope and principals (§9) so that adding them is filling columns, not
  redesigning.
- **G4. The safety properties survive plugin code.** No plugin sends
  without approval, reaches what it was not handed, or skips redaction,
  recording or the budget — and where Python cannot enforce that, this
  document says so (§3.2).
- **G5. Moving a backend is an adapter plus a config change.** Discord →
  Slack, SQLite → Postgres, local clone → GitHub API.

**Non-goals**

- **Third-party code in-process.** Out-of-process only (§3). This single
  decision is what lets most of §4–§5 stay small.
- **Many organisations in one process.** Tenancy is one deployment
  (process + database) per organisation (§9.1).
- **Hot reload.** Plugins, handles and DAG versions are fixed at boot;
  changing plugins is a restart.
- **A public marketplace.** A registry needs the supply-chain controls of
  §12 first; ClawHub found 341 of 2,857 skills malicious.
- **Model-driven orchestration.** Workflows stay deterministic Python (§7).

---

## 2. Prior art: what the top tier converged on

Researched 2026-09-21 from primary docs.

| Pattern | Who does it | Friday v2 |
| --- | --- | --- |
| Tool = typed function, schema from signature + docstring, decorator | OpenAI Agents SDK `@function_tool`, Google ADK `FunctionTool`, MS Agent Framework `@tool`, Pydantic AI | Yes (§6.2) |
| **Toolset** as the unit of composition | Pydantic AI `AbstractToolset`, ADK `BaseToolset`, OpenAI `ToolSearchTool` | Yes — plugins contribute toolsets, agents are given toolsets |
| Run context injected into tools | Pydantic AI `RunContext[Deps]`, ADK `ToolContext` | Yes — typed `Deps`, built per run (§5) |
| Interceptors at run / model call / tool call | LangChain v1 middleware, MAF middleware, ADK plugins, Claude Code hooks | Kernel chain fixed now; plugin middleware later (§8) |
| HITL = pause, persist, resume | OpenAI `RunState`, LangGraph `interrupt()`, MAF `RequestInfoExecutor`, A2A `INPUT_REQUIRED` | Already true (`Ask`/`HandOver`, outbox approval) |
| Two-tier memory behind a protocol | OpenAI `Session`, ADK `SessionService` + `MemoryService`, LangGraph checkpointer + Store | Typed kinds + scopes (§9) |
| Hierarchical memory namespaces | mem0 `user_id/agent_id/run_id`, LangMem, ADK `user:`/`app:` prefixes, ElizaOS world/room/entity | Scope + audience (§9.1) |
| Skills as filesystem bundles, progressive disclosure | Agent Skills (`SKILL.md`): Claude Code, OpenClaw, Hermes, Pydantic AI | Already true; becomes a contribution (§6.3) |
| MCP as the out-of-process boundary | every framework above; spec 2026-07-28 | The only way third-party code runs (§3) |
| Static manifest read before code runs | Claude Code `plugin.json`, OpenClaw `openclaw.plugin.json`, Home Assistant `manifest.json` | **Not needed**: no untrusted code runs in-process; `register()` is the manifest (§4) |
| Channel adapter thin; core owns sessions and sending | OpenClaw, ElizaOS 1.x, Hermes gateway | Yes, when the second channel lands (§6.6) |
| Per-tool human approval | OpenAI `needs_approval`, n8n, Hermes `tools/approval.py` | Stronger: no plugin has a send verb (§5.3) |

Where they diverge, and which side v2 takes:

- **Orchestration:** explicit graphs (LangGraph, MAF) vs model handoffs
  (OpenAI) vs subagents (Claude). v2: explicit graphs, an agent is a node.
- **Durability:** own checkpointer vs external engine (Pydantic AI →
  Temporal). v2: own (`dag_state`), one process, SQLite.
- **Plugin trust:** OpenClaw runs native plugins in-process and documents
  it as "equivalent to arbitrary code execution". v2: in-process is
  first-party and reviewed; everything else crosses a process boundary.

**Terminology.** "Microkernel" here is Mark Richards' *plug-in
architecture* (core system + plug-in modules through a registry;
*Software Architecture Patterns*, 2015) — not the OS sense (Mach, L4),
whose kernel is far smaller. Closest working examples: pytest (much of
its core is plugins over `pluggy`), Home Assistant integrations, VS Code
contribution points, Kubernetes CRI/CNI/CSI. v2's kernel test is not
Liedtke's minimality but §3.1's: *could a replacement break a safety
property?*

Sources: `code.claude.com/docs/en/plugins-reference`,
`openai.github.io/openai-agents-python/{tools,human_in_the_loop,sessions}`,
`adk.dev/{plugins,tools-custom,sessions}`,
`learn.microsoft.com/en-us/agent-framework/concepts/{agents/middleware,workflows}`,
`docs.langchain.com/oss/python/langchain/middleware`,
`pydantic.dev/docs/ai/tools-toolsets/toolsets`,
`blog.modelcontextprotocol.io/posts/2026-07-28`,
`docs.openclaw.ai/{plugins/architecture,gateway/security,concepts/memory}`,
`docs.elizaos.ai/plugin-registry/overview`,
`hermes-agent.nousresearch.com/docs/developer-guide/architecture`,
`docs.mem0.ai/core-concepts/memory-types`,
`langchain-ai.github.io/langmem/concepts/conceptual_guide`,
`unit42.paloaltonetworks.com/openclaw-ai-supply-chain-risk`,
`thehackernews.com/2025/09/first-malicious-mcp-server-found.html`,
`invariantlabs.ai/blog/mcp-github-vulnerability`.

---

## 3. Shape and trust

```
      ┌────────────────────────────── kernel ──────────────────────────────┐
      │ Registry   Inbox + triage   Pool   DAG runner   Outbox state machine│
      │ Memory write path   Recording   Budget   Redaction   Audit log      │
      └───────▲──────────────────────────▲──────────────────────────▲───────┘
              │ friday.sdk ports          │ register(api)            │ MCP
   ┌──────────┴─────────┐   ┌─────────────┴────────────┐   ┌─────────┴──────────┐
   │ trusted adapters   │   │ contribution plugins     │   │ out-of-process     │
   │ Channel, Approval, │   │ task types, toolsets,    │   │ MCP servers:       │
   │ Store, ModelProv.  │   │ sources, kinds, skills   │   │ third-party, too-  │
   │                    │   │                          │   │ specific tools     │
   └────────────────────┘   └──────────────────────────┘   └────────────────────┘
```

### 3.1 Four tiers

| Tier | Runs | Who | Trust |
| --- | --- | --- | --- |
| **Kernel** | in-process | this repo | holds the invariants |
| **Trusted adapter** | in-process | this repo, reviewed like kernel | holds a capability the kernel must hand it (`Channel.send`, every prompt, every row) — kernel invariants sit *above* its port (§3.3) |
| **Contribution plugin** | in-process, via `register(api)` | this repo, and the operator's own plugin (`~/friday-personal`) | handed narrowed handles; restricted by construction and review |
| **Out-of-process** | MCP server | anything third-party, and any tool too specific to belong in a plugin | the only real boundary: explicit env, allow-list, pinned |

The kernel test: **would a replacement be able to break a safety
property?** If yes, it is kernel — or, if it must be swappable, a trusted
adapter whose port the kernel does not trust for the invariant.

### 3.2 What is enforced and what is convention

Said plainly, because Python allows any in-process code to `importlib`
its way to the kernel, read `os.environ` or monkeypatch a function:

| Enforced (a boundary) | Convention (keeps honest code honest) |
| --- | --- |
| Outbox state machine + approval check in kernel code above the Store | Narrowed handles and `Deps` for in-process plugins |
| Allow-lists in code for every MCP server; explicit child env | `ast` import rules (static imports only) |
| Toolset narrowing per run (`allowed_tools`, agent toolsets) | Self-declared `side_effect` on tools |
| Value-based redaction of declared secrets | The instruction-shaped word list |
| Process boundary for third-party code (MCP) | Skill content heuristics; the operator's own scripts |

The right-hand column is still worth having — it makes mistakes loud and
review cheap — but it is never cited as the reason something is safe.

### 3.3 Kernel invariants above trusted-adapter ports

| Invariant (carried from `DESIGN.md`) | Held by kernel code, not by |
| --- | --- |
| Nothing is sent by the caller that decided to send it; approval belongs to the row | the Store adapter (it persists; the kernel decides state transitions) |
| A decision counts only from an approval surface the kernel recognises, by a principal the policy allows (§9.3). Today: the kernel checks the decider's id equals `operator_id` — v1 records whatever user pressed the button (`providers/discord/bot.py`, `run_agent.py` `decided`) and is safe only because the card is a DM | the Approval adapter |
| `Channel.send` is called by the outbox loop only | the Channel adapter (it is never handed to a plugin) |
| Budget is checked before every call and counted from usage after it. A call's worst case is bounded by `max_tokens` × `max_turns`, so the budget is overshot by at most one call — no reservation ledger. Two failure cases, deliberately different: the **store** failing to count fails **open** (v1: the budget is a cost guard, and a database hiccup must not silence Friday); a **provider** reporting no usage fails **closed** (the count would silently stop) | the ModelProvider |
| What is sent is exactly what was approved — or, for kinds that need no approval, exactly what was enqueued: payload, destination and attachments are frozen and hashed; any change voids the approval (§3.4) | the Channel adapter, the responder, any renderer |
| A send whose outcome is unknown is never retried automatically (§3.4) | the outbox's retry loop |
| Inbound dedup on `(channel, message_id)`; never drop an addressed event | the Channel adapter |
| Every node timed, retried, enveloped, recorded | the graph |
| Memory writers, admin-row protection, instruction guard | the Store adapter |
| One agent process per database (§12.1) | the operator remembering |

### 3.4 Outbox delivery

v1 marks a row sent *after* the channel call, and says why: a crash in
between may post twice, and the other order loses an approved reply
silently (`friday/outbox/__init__.py`, `_deliver`). v2 removes the choice:

```
enqueued ─┬─ needs approval ──→ awaiting_approval ─→ approved ─┐
          │                                                     ├─→ dispatching ─┬─→ sent
          └─ policy_approved (kind needs none, or a policy rule)┘                ├─→ delivery_unknown  (found at startup)
                                                                                 └─→ failed            (raised; retried with backoff)
```

v1 has three outbound kinds and only `reply` waits for a person
(`_NEEDS_APPROVAL = ("reply",)` in `db.py`); `ask_for_details` and
`approval_card` go out unapproved by design. In v2 those rows take the
`policy_approved` edge, recorded as such, and are **hashed at enqueue**
instead of at approval. §9.3's "auto-approved by policy" is the same
edge.

- **`dispatching` is written before the channel call.** A row found in
  `dispatching` at startup was interrupted mid-send; it becomes
  `delivery_unknown` and goes to the operator — *check the channel, then
  mark sent or resend* — never retried by the loop. Duplicates and silent
  losses both become impossible without a person deciding.
- Each row carries `idempotency_key` (passed down when the channel
  supports one), `approved_payload_hash`, `approved_destination` and
  `adapter_message_id`.
- **Frozen at approval:** the outbox sends the stored bytes to the stored
  destination. Nothing re-renders a template, re-reads voice, re-expands
  mentions or refreshes an attachment after approval. v1 already sends the
  stored `text`; v2 adds the hash check at dispatch and voids the approval
  if anything differs.
- The staleness check (`_overtaken`: the conversation moved on) stays, and
  still runs at dispatch.

---

## 4. The plugin contract

### 4.1 A plugin is a Python object, named by import path

```python
# friday/sdk/plugin.py
@dataclass(frozen=True)
class Plugin:
    id: str                                   # "devops"; namespace of everything it registers
    register: Callable[[PluginAPI], None]     # the one entrypoint
    requires: tuple[str, ...] = ()            # plugin ids: load order + presence, never imports
    config: type | None = None                # dataclass for its block under `config.<id>`; None = takes none
```

A plugin is a package whose `__init__.py` exposes one `PLUGIN`. Everything
else in the package is the plugin's own business:

```
plugins/devops/
├── __init__.py      PLUGIN + register(api)   ← the only file the kernel knows about
├── params.py        ApiIssueParams
├── extractor.py     node 0 prompt + schema
├── graph/           nodes and edges
├── sources/         LokiSource, SshKubectlSource, LocalClone — implement sdk ports
├── toolsets.py      Toolset("devops:logs"), Toolset("devops:code")
├── memory.py        MemoryKindSpec for devops.*
├── config.py        DevopsConfig
├── skills/          SKILL.md files
├── evals/           triage eval_cases, case replays
└── tests/
```

A small plugin (`docs`) may be `__init__.py` and `params.py` alone.

```python
# plugins/devops/__init__.py
def register(api: PluginAPI) -> None:
    api.task_type(TaskTypeSpec(
        name="devops:api_issue",
        params=ApiIssueParams,
        extractor=build_extractor,
        graph=build_graph,
        deps=ApiIssueDeps,
        needs=frozenset({"source:loki", "devops.service"}),
    ))
    api.source(LogSource, "loki", lambda deps: LokiSource(api.config.loki))
    api.toolset(logs)
    api.toolset(code)
    for kind in DEVOPS_KINDS:
        api.memory_kind(kind)
    api.skills(Path(__file__).parent / "skills")


PLUGIN = Plugin(
    id="devops",
    requires=("core-memory",),
    config=DevopsConfig,
    register=register,
)
```

`api.config` is the plugin's block, already validated against
`PLUGIN.config` and handed over as an instance of it; a block that does
not fit refuses the boot, naming the plugin and the field (v1's typed
`ApiIssueConfig` is the first such dataclass).

```yaml
# config.yaml
plugins:
  - plugins.core_memory:PLUGIN
  - plugins.devops:PLUGIN
  - path: ~/friday-personal       # the operator's plugin: this directory joins sys.path
    plugin: friday_personal:PLUGIN
```

**Now: plugins live in this repo and are named by import path in
config.** No per-plugin `pyproject.toml`, no entry points, no version
ranges on `requires`, no lockfile. Those are packaging for plugins that
live elsewhere; §16 builds them when the first plugin outside this repo
exists. Until then they would be ceremony for three task types and one
operator.

**No base class for plugins.** A plugin is a `Plugin` value and a
function, not a subclass of `BasePlugin`:

- **A plugin contributes an uneven mix.** One task type and two toolsets
  here, a single memory kind there. A base class makes every plugin
  override or stub a method per contribution type, and every new
  contribution type in `sdk` becomes a new method on every plugin.
  `register(api)` calls only what the plugin has.
- **There is no behaviour to inherit.** A plugin *declares*; declarations
  are data. Inheritance earns its keep when subclasses reuse the parent's
  code, and there is none to reuse.
- **It keeps the lifecycle rule enforceable.** `register()` only
  registers (§4.3); anything with a lifetime is a factory the kernel
  builds and closes. A plain function has nowhere to hide an `on_start`
  or instance state; a class invites both.
- **It is what the reference systems do**: pytest's module-level hooks,
  Home Assistant's `async_setup_entry`, VS Code's `activate(context)`,
  OpenClaw's `register(api)` — a function handed an API, not a subclass.

**The abstractions are the ports**, not the plugin: `LogSource`,
`CodeSource`, and later `Channel`, `Approval`, `Store`, `ModelProvider` in
`friday.sdk`. They are `typing.Protocol`s — structural, so an
implementation is any class of the right shape and inherits nothing, as
v1's `LogSource` already is (`friday/sources/__init__.py`). The type
checker checks the shape; contract tests (§13, on trigger) check the
behaviour. Behaviour genuinely shared between implementations — a retry
helper several channels use — is a helper the implementation calls, never
part of the contract.

**No static manifest file.** A manifest read before import is how
OpenClaw and Claude Code protect a host from untrusted in-process code.
v2 has none (§1 non-goals), so a second file restating what `register()`
already says would be three places to edit for every contribution. The
board shows what each plugin actually registered (§6.10) — that is the
inspectable record.

**Only what config names is imported.** Importing a module runs its
top-level code before `register()` is called, so there is no discovery
step that imports everything and filters afterwards. When entry points
arrive (§16) the same rule holds: read metadata, keep the enabled ids,
then import.

This makes `register()` a manifest **only for trusted code**: it cannot be
used to inspect a plugin before it runs. The operator's own plugin is
arbitrary local code in the same tier as first-party — accepted (§18) —
and the board labels every plugin with its tier.

### 4.2 `PluginAPI` — now

```python
class PluginAPI(Protocol):
    config: Any                                     # an instance of PLUGIN.config, already validated (None if it takes none)
    def task_type(self, spec: TaskTypeSpec) -> None: ...          # §6.1
    def toolset(self, toolset: Toolset) -> None: ...              # §6.2
    def skills(self, directory: Path) -> None: ...                # §6.3
    def source(self, port: type[P], name: str, factory: Callable[[Deps], P]) -> None: ...  # §6.4
    def memory_kind(self, spec: MemoryKindSpec) -> None: ...      # §9.2
    def check(self, probe: Callable[[], Awaitable[str | None]]) -> None: ...  # boot health: None = ok
```

**Later**, each added when its trigger fires (§16): `channel`, `approval`,
`store`, `model_provider`, `trigger`, `middleware`, `memory_search`,
`board_hints`.

### 4.3 Registry rules

- **Namespaced ids** `plugin:name` (`devops:logs`). Duplicates refuse the
  boot. **Core kinds are the one exception**: `fact`, `voice` etc. stay
  unprefixed, because every install has them and existing rows use them.
- **`register()` only registers.** No I/O, no connections. Anything with a
  lifetime is a factory the kernel calls and closes.
- **A plugin registers only against its own ids.** Attaching a graph to
  another plugin's task type is refused.
- **Singleton adapters are chosen by config** (`store: sqlite`), not by
  who registered first.
- **Order:** config → plugins in `requires` order → `register()` →
  `check()` probes → run. A failing probe or a missing requirement refuses
  the boot with a sentence naming the plugin.
- **Boot checks are deterministic** — duplicate ids, a `contrasts` or
  `rank` entry naming a type that is not enabled, a `requires` cycle, an
  invalid config block, a scope with no resolvable fallback. Nothing that
  needs a model or an embedding runs at boot; a provider being offline
  never stops Friday starting.
- **Built without `pluggy`.** Its value is ordered hook calling; the
  registry needs namespacing, typed singletons and refusal rules, and a
  validated dict does that in about 150 lines with no dependency. Plugin
  middleware (§8), when it arrives, borrows pluggy's ordering vocabulary
  (`tryfirst`/`trylast`), not the library.

---

## 5. Handles, `Deps` and side effects

### 5.1 Handles, not grants — for in-process plugins

A plugin states what it needs **when it registers** (`TaskTypeSpec.needs`,
`@tool(needs=...)`); the kernel checks the need exists at boot and hands
narrowed handles at run time: a memory reader that sees only the kinds
asked for, a source handle for `loki` only, a draft handle bound to the
task's room. There is **no capability grammar and no `grants:` block for
in-process plugins** — the operator would be granting first-party code to
itself, and §3.2 already says such grants are not a boundary.

Grants in config exist for the **out-of-process tier only** (§11): which
MCP server, which tools, which egress hosts, which secrets. No wildcards.

### 5.2 `Deps` is typed and built per run

A task type's `deps` factory declares a dataclass; the kernel fills it
**per run, from the run's scope** (§9.1), after checking at boot that
every field can be satisfied. v1's `DAG_DEPS_EXTRA` (one producer,
`router.py`; one consumer, `pool.py`) is the first thing it replaces.

### 5.3 Side effects are classed by destination

| Class | May | How |
| --- | --- | --- |
| **read** | read a surface inside the deployment's boundary | a `Source`, through a handle |
| **draft** | propose something visible | an outbox row via the draft handle; the kernel's approval gate decides |
| **memory** | propose or write memory | the kernel's write path |

- **No plugin has a send verb.** `Channel.send` is called by the outbox
  loop only.
- **A draft targets the originating task's room** unless the task type
  declares another destination; the approval card shows it (§12).
- **A "read" that carries arguments to a host outside the deployment is
  not a read.** A URL fetch, a public search, a third-party MCP tool can
  exfiltrate through its arguments. Such tools are declared `egress` and
  a run may hold them together with private sources only if the operator
  opted that agent in (the "lethal trifecta": private data + untrusted
  input + outbound channel in one run — the GitHub-MCP incident).

---

## 6. Contribution types

### 6.1 Task types — now

```python
@dataclass(frozen=True)
class TaskTypeSpec:
    name: str                               # "devops:api_issue"
    params: type                            # docstring = the type's description to triage
    extractor: Callable[[Deps], Extractor]
    graph: Callable[[Deps], DAG] | None     # None → the one-node simple graph
    deps: type | None                       # §5.2
    needs: frozenset[str]                   # sources, kinds, agents it uses
    accepts: frozenset[str] = {"message"}   # message | email | file | calendar | trigger
    examples: tuple[Example, ...] = ()      # few-shot seeds for triage
    contrasts: tuple[str, ...] = ()         # "not devops:api_issue when …": similar types
    eval_cases: Path | None = None          # labelled rows for the triage eval (§10)
```

A task type whose plugin is removed: its open tasks go to `needs_human`
with a reason; its memory rows stay dormant, not deleted.

### 6.2 Tools and toolsets — now

```python
logs = Toolset("devops:logs", instructions="Read service logs around a request.")

@logs.tool(side_effect="read", needs={"source:loki"})
async def query_logs(ctx: ToolContext[LogsDeps], service: str, since: datetime, until: datetime) -> Lines:
    """Log lines for one service inside a window."""
```

- **Every tool belongs to a toolset, every toolset to a plugin**,
  including the kernel's own memory and skill tools (`core-memory`,
  `core-skills`). v1's rule "every tool lives in `friday/tools/`" becomes
  "every tool is registered through `PluginAPI.toolset`"; the answer tool
  stays the one exemption.
- Schema from signature + docstring; `ToolContext` injected, invisible to
  the model.
- **An agent is given toolsets**, not a tool list. A skill's
  `allowed_tools` narrows the toolset for that run and can never widen it.
- **MCP servers are toolsets** built by the `mcp` plugin from config.
  For servers our code calls (Loki through `LokiSource`), the allow-list
  stays **in code**, as v1 argues (`friday/sources/__init__.py`: a guard a
  file can widen is one the file's next editor widens by accident). A
  third-party server has no code of ours to hold it, so its allow-list is
  the operator's `tools:` list in config — the one place config names
  tools — with no wildcards, shown in the boot log and written to the
  audit log when it changes (§12).

### 6.3 Skills — now

A plugin contributes a directory of Agent Skills; names are namespaced;
progressive disclosure is unchanged.

**Skills replace v1's `runbook` memory kind** (operator, 2026-09-21): both
were "a procedure", and two mechanisms for one idea is one too many. What
a runbook had that a skill did not, the skill gains:

- **A `when:` frontmatter block** (`services`, `error_codes`,
  `path_patterns`, `keywords` — v1's `RunbookWhen`). Code matches it
  against the case and hands the matching skills to the run, as v1 does
  for runbooks (`db.py` `domain_memories`); the model does not have to
  find them. Skills without `when:` stay model-chosen.
- **Operator-authored skills are a memory kind, `skill`**, registered by
  `core-skills`: a row holding a `SKILL.md` body plus `when:`, scoped like
  any memory (§9.1), written from the board through the kernel's one
  write path. `core-skills` reads them through a memory handle, like any
  plugin — it never sees the store. File skills ship with plugins; `skill`
  rows are the operator's; the skill tools serve both. Said plainly: v1's
  `runbook` kind is **generalised and renamed**, not removed.

Existing `runbook` rows become `skill` rows in §15 step 6.

Third-party skills, when there are any, are pinned by hash (§16) and a
change is a review. v2 does **not** promise
to detect a malicious skill by reading it — the control is toolset
narrowing, not content filtering.

### 6.4 Sources — now

v1's layering holds: a **source** reads and decides nothing; a **check**
is a formula over sources; a **node** is the frame. Changes:

- Registered against a port type (`LogSource`, `CodeSource`), returning
  structured values with provenance (`Lines`, `Excerpt` with `ref`,
  window, `truncated`).
- **Mechanism in the source, policy in data.** Container roots, vendored
  paths, excerpt size, Loki labels come from plugin config or memory rows
  (a `stack_profile` on `devops.project`), not module constants —
  `sources/code.py` (`CONTAINER_ROOTS`, `NOT_OURS`) is the first to fix.
- **Security invariants in the adapter constructor** (`LocalClone(root)`
  refuses an empty root; traversal checked inside).
- `test_sources_are_the_only_door` generalises: only source adapters and
  trusted adapters spawn processes or open sockets.

### 6.5 Memory kinds — §9.2.

### 6.6 Channel and Approval — later (trigger: a second channel)

Designed now so that the second channel is an adapter, not a refactor:

- `Channel` = `stream`, `history`, `recent`, `send` (kernel-only),
  `features` (`REPLY`, `REACT`, `THREAD`, `DM`, …), and **what "addressed
  to Friday" means** on that channel (a mention, a DM, an email to an
  alias). "Never drop a mention" becomes "never drop an addressed event".
- `InboundEvent` gains `attachments` (references into the artifact store,
  each with a sensitivity class).
- World / room / entity replaces guild / channel / user in `friday.sdk`.
- Cursors become opaque strings with a channel-supplied order.
- `Approval` is separate from `Channel`: where the operator approves is
  not where the request came from. The board becomes an Approval adapter
  **only after** it has Host/Origin checks and a CSRF token (§12).

- **Identities are part of the channel.** v1 speaks as two identities on
  one channel: replies go out as **the operator's own Discord account**
  (`discord_user`, the accepted risk of a self-bot) and asks and cards as
  **Friday's bot** (`discord_bot`). That is the most Discord-specific thing
  in the system. In v2 a channel declares the identities it can send as —
  `operator` (needs a user-level credential: a Slack user token, the
  operator's mailbox) and/or `friday` (a bot or service account) — and
  `Outbound.as_` names one. When the channel cannot send as the operator,
  the row is sent as Friday with the operator named, or handed to the
  operator to send by hand (v1's `sent_manually` state already exists);
  the task type declares which it prefers. Voice is looked up by `as_`.

**Now**, without waiting for the trigger: remove the Discord leaks that
cost nothing to fix — `PersonData.discord_id` becomes a list of
`EntityRef`, the pool's `"discord_user"`/`"discord_bot"` defaults move to
config.

### 6.7 Model provider — now (trigger fired: the Pydantic AI adoption, ADR 0001)

v1's `harness.py` is built on the SDK's `Agent`/`Runner`/`RunState`, so
"swap the provider" means either the kernel re-implements the loop or the
provider owns the whole loop. **That trigger has fired**: the Pydantic AI
migration (roadmap item 5) is that rewrite, and it is decided (Appendix D,
ADR 0001), so the `ModelProvider`/harness seam is drawn **as part of the
Pydantic AI adoption — §15 step 2 — not deferred**. Pydantic AI owns the
agent loop; the kernel wraps its §3.3 invariants (budget, redaction,
recording) around every model call. `harness.py` stays the only module
importing a vendor/agent SDK, and moving between compatible vendors is
`base_url`/`api_key`/`model`.

### 6.8 Store — later (trigger: a second store)

`friday/store/db.py` (3,900 lines, ~120 methods) splits **now** into
repository modules behind the same `Database` facade, for readability
and so kernel invariants (outbox transitions, memory writers) move out of
it into kernel code (§3.3). The `Store` Protocol and a second adapter
come with Postgres. Plugins never see the store. Migrations stay with the
kernel; an adapter only runs them.

### 6.9 Triggers — later (trigger: the first task that is not a message)

A `Trigger` (cron, webhook, alert) produces a `TaskRequest` of a declared
type directly, skipping triage, through the same pool. The summariser and
liveness are already schedules and would be the first users.

### 6.10 Board — now: generic and protected; later: hints

The board renders envelopes and lists each plugin's registrations, tiers
and `check()` results. **It already writes** — the operator's `admin`
memory rows (`POST/PUT/DELETE /api/channels/{id}/memories`), which reach
the extractor's prompt — so it is protected **now**, not when a second
person arrives (§12): loopback bind, Host and Origin checked against an
exact list, a random session secret minted at startup, `SameSite=Strict`
cookie, CSRF token on every write. v1's `BOARD_TOKEN` is deleted: it is a
switch that permits a non-loopback bind, not a credential any request is
checked against. No accounts are needed for one operator; an
unauthenticated write surface is not acceptable for any.

 A plugin may later contribute **declarative render
hints** (JSON Schema + `ui:` hints on its envelopes), never JS: the board
shows every prompt, and a plugin bundle there is XSS with everything
visible.

---

## 7. Workflows (DAG)

**Durability is DBOS's, behind a Friday-owned port** (Appendix D, ADR 0001).
Unchanged in substance: deterministic Python graphs; an agent is a node;
pause is an action (`Ask`, `HandOver`). What changes is where the loop and
its checkpoints live.

- **`sdk/workflow.py` is a thin Friday port** (`Node`/`Step`/`Edge`, the
  envelope, `Ask`/`Reply`/`HandOver`); **DBOS is the adapter beneath the
  kernel**, and a plugin imports `friday.sdk`, **never `dbos`** (§6.2 rule,
  Rule 11). This is what keeps the plugin contract stable while the engine
  underneath is a library.
- **DBOS owns run persistence, step memoization and resume.** The kernel
  no longer hand-rolls checkpoints or a derived `DAG.version`; recovery is
  DBOS's (step name + application version), which **resumes from the last
  incomplete step**, not a re-run-from-entry on a source-digest mismatch.
  The v1 `version = digest of node source` scheme is **dropped** — it was
  built for the hand-written engine DBOS replaces.
- **Every step is still wrapped by the kernel chain** (§8): budget,
  recording, redaction, `needs`/`side_effect` — the kernel does not
  delegate its §3.3 invariants to DBOS (§3.1).

Changes carried from the plugin design:

1. **Graphs come from `TaskTypeSpec.graph`**, expressed in the `sdk`
   workflow types. The router imports no graph.
2. **`Deps` are reconstructed inside the run from a serializable scope
   key** (§5.2). DBOS persists a workflow's inputs, and `Deps` hold live
   handles that cannot be serialized, so the workflow input is the scope
   key and the kernel rebuilds `Deps` at run start.
3. **No parallel fan-out** until a graph needs it; `asyncio.gather`
   inside a step remains the answer.

---

## 8. Middleware

**Now:** the kernel chain is fixed code around every model and tool call
— budget, recording, redaction, the tool's `needs`/`side_effect` check —
as v1 already does in `_settle`, the recording sink and `redact.py`.

**Later** (trigger: a second caller for plugin middleware, e.g. PII
masking or tracing export): plugins may add middleware at three layers
(run, model, tool). **The kernel sits outermost and innermost**: checks
run again at final dispatch, so a plugin wrapper cannot rewrite arguments
after they were checked or short-circuit the kernel's post-processing.
Order among plugins is explicit (`after=[plugin_id]`).

---

## 9. Memory, scope and principals

### 9.1 Scope and audience

- **Tenancy is the deployment.** One process + database per organisation;
  no `org` column. (Many orgs per process is a non-goal.)
- **Scope within a deployment** is `(team?, room?)`; reads walk `room →
  team → '*'`, and **the narrower row always wins** — constraints included
  (operator, 2026-09-21). A room may override a global rule on purpose;
  there is no "binding" floor. Rules that must hold everywhere are kernel
  code (§3.3), not memory rows.
- **User is an audience axis, not a level.** A user-owned row (personal
  voice, personal notes) is readable only in a run whose audience is that
  user (a DM, a personal task), never injected into a run that answers a
  shared room.
- **The kernel binds scope from the inbound event.** A plugin never passes
  one in.
- **Model writes land at the run's own scope.** Moving a row broader is a
  person's action.
- v1's `channel_id` + `'*'` is the `room` + `'*'` case. The `team` level
  is added when a deployment has a second team; the audience axis when
  personal rows exist. Both are defined in `friday.sdk` now so the column
  added later has a known meaning.

### 9.2 Kinds: core and packs

```python
@dataclass(frozen=True)
class MemoryKindSpec:
    name: str                          # core: "fact"; pack: "devops.service"
    data: type | None                  # structured shape, validated with `fits`; None = prose
    schema_version: int = 1            # stored on each row
    upgrade: Callable[[int, dict], dict] | None = None   # old rows readable after a plugin upgrade
    audience: Literal["model", "code"] = "model"
    writers: frozenset[Origin] = frozenset({"admin"})
    cardinality: Literal["one-per-key", "append"] = "one-per-key"
    injected: bool = False             # rendered into a prompt section (behind trust_boundary) vs tool/code only
    max_chars: int = 500
    sensitivity: Literal["public", "internal", "personal", "restricted"] = "internal"
    allowed_scopes: frozenset[str] = frozenset({"room", "team", "*"})
```

- **Readers come from the reader's side**: an agent that declares it needs
  `devops.service` gets a handle that reads it. `core-memory` does not
  have to name `devops:diagnose` (which the dependency rule forbids).
- **Core kinds** (`core-memory`): `fact`, `constraint`, `decision`,
  `voice`, `summary`, `finding` (a lesson from finished work; subject key
  generic, `data` typed by the pack), `person` (a participant or
  colleague, with several `EntityRef`s across channels).
- **Pack kinds** come with their plugin: `devops.service`, `.route`,
  `.environment`, `.project`, `.dependency` (procedures are skills, §6.3,
  not a kind). A clinic plugin
  brings its own; **patient data stays in its system of record** and
  memory holds references, marked `restricted`.
- **Every sensitivity level has a provider policy**, not only
  `restricted`:

  | Sensitivity | May reach |
  | --- | --- |
  | `public` | any configured provider |
  | `internal` | providers the deployment allows |
  | `personal` | providers the owner approved for personal data |
  | `restricted` | a local model, or a provider explicitly allow-listed for it |

  **Decided once, at run start**, not per call: the run's sensitivity is
  the maximum over what it *may* touch — the inbound event, the memory
  kinds and sources its task type `needs`. The provider is chosen for the
  whole run. Per-call routing is not buildable on the current harness:
  tool results re-enter the model inside the SDK's own loop
  (`harness.py`, `Runner.run`), where the kernel cannot switch providers.
  A tool result more sensitive than the run was admitted for is withheld
  from the model, with a reason. **Built on trigger** (§16: personal or
  restricted data); until then every sensitivity is `internal` and the
  one configured provider is allowed.
- Retention and erasure-by-subject are fields added with the first
  persona that needs them (trigger: personal or restricted data).

### 9.3 Principals and approval policy

- `Principal(EntityRef)` with a `Role` (owner, admin, approver, member) at
  a scope is defined in `friday.sdk` now; today there is one principal,
  the operator.
- Rows record `(origin, principal)`, not `origin` alone. A model-origin
  call still cannot alter an admin row.
- `Outbound.as_` names the identity a draft is sent under; voice is
  looked up by that identity.
- **Approval policy is about risk, not about who asked.** In a personal
  deployment the owner asks Friday for something and approves the draft
  — requester and approver are the same person, and that is the common
  case. The policy per outbound kind per scope says:
  - `self_approval`: `allowed` (default for the owner),
    `allowed_with_confirmation`, or `forbidden` (a shared team room with
    separation of duties);
  - high-risk kinds (irreversible, external audience) may require a
    second principal or re-confirmation;
  - low-risk reversible kinds may be auto-approved by policy — v1's
    `auto_ask_for_details` is the first such rule.

  "First decision wins" among allowed principals, with a unique
  constraint on the approval write. Trigger for more than the owner-only
  policy: a second human approver.

### 9.4 Carried over, and tightened

- `origin`, the instruction guard at the one write path (its word list
  becomes per-locale data), candidates waiting for a verdict, verbatim
  material as `Artifact` — unchanged.
- **Every tool result carries its provenance** and is escaped at the
  prompt seam (v1's `trust_boundary`). **A model write from a run that
  touched untrusted content** (a reporter's message, a log line, a
  third-party tool) **goes to candidates**, not straight to memory — one
  injected log line must not become a lasting `finding`.
- `source_ref` is mandatory on model writes; `valid_from`/`valid_to` are
  added when something expires facts.
- **Injection is recorded as a decision.** `DESIGN.md` § Memory allows a
  tool result *or* an injected section; `CONTEXT.md` still says tool
  result only. v2 keeps both, injected kinds behind `trust_boundary`, and
  `CONTEXT.md` is corrected in the step that implements §9.2.

---

## 10. Triage across plugins

The busiest call in the system; installing a plugin changes it.

- **The label set is the task types enabled for the event's scope that
  `accept` the event's shape** — not every type every plugin registered.
- **Overlap is found by the eval, not at boot.** Declared `contrasts`
  and per-scope `rank` are validated at boot (§4.3); whether two types'
  descriptions are too close is a semantic question, answered by the
  triage eval's confusion matrix on the enabled set. A close call between
  two ranked types goes to a person.
- **What nothing claims goes to a generalist, which asks the operator**
  (operator, 2026-09-21). v1's triage prompt says "everything else is
  `api_issue`"; v2 replaces that with a core task type,
  `core:intake`, from the first-party `core-intake` plugin, always
  enabled:
  - Its graph is one broad agent node: read the turn and the room's
    summary, say in two lines what this looks like and which registered
    task types came close, and **ask the operator how to handle it** —
    through the approval surface, never the reporter.
  - The operator's answer is one of: *treat it as `<type>`* (the task is
    re-typed and runs that graph), *reply this way* (a draft, approved as
    usual), or *ignore* (closed with a reason).
  - A re-typing is recorded like a verdict, so it becomes a triage example
    for the scope — the next similar message is classified, not asked. It
    is an explicit human mark, so it is active at once (v1's rule: only a
    marked classification becomes an example), but every example carries
    `created_by`, `source_event_id` and `task_type_version`; it retires by
    itself when the type's version changes, the board lists and disables
    examples, and a per-type cap keeps one odd case from dominating.
  - "Never drop a mention" holds: intake is a task with a row, not a skip.
  - **Capped**, because every unclaimed message is a model call and an
    interruption: past `intake.max_asks_per_room_per_hour`, further intake
    tasks are held and asked about together in one digest.
  - Per scope, config may point the fallback elsewhere (`default_type`),
    but `core:intake` is the default and the kernel refuses a boot where
    no fallback resolves.
- **Two-stage triage** (plugin, then type) when the enabled set passes a
  threshold measured by the eval — not before.
- **Evals follow the install.** Each plugin ships `eval_cases`; the triage
  eval runs on the set an install actually enables. Under `CLAUDE.md`
  rule 4, **installing or upgrading a plugin that contributes a task type
  is a change upstream of the triage prompt** and needs the eval run and
  reported.

---

## 11. Configuration

```yaml
plugins:                               # import paths (§4.1); ids are what the rest of config uses
  - plugins.core_memory:PLUGIN
  - plugins.core_skills:PLUGIN
  - plugins.core_intake:PLUGIN
  - plugins.devops:PLUGIN
  - plugins.docs:PLUGIN
  - plugins.mcp:PLUGIN
# store: sqlite                        # once trusted adapters are plugins (§6.8, §16)

scopes:
  "*":         { enabled: [devops, docs] }          # fallback: core:intake (§10)
  "room:1234": { enabled: [devops], rank: ["devops:api_issue", "docs:doc_question"] }

config:
  devops:
    loki: { server: devops-generic, tool: loki_query_range }
    kubectl: { ssh_host: dev }
    code: { stack_profile: node-docker }

mcp:                                   # the out-of-process tier: the only grants
  devops-generic:
    transport: streamable-http
    url: ${DEVOPS_MCP_URL}
    auth: keycloak
    tools: [loki_query_range, loki_series]   # no wildcards
    secrets: [DEVOPS_CLIENT_SECRET]          # the child gets these and nothing else
```

- Each plugin's block is validated against its own schema at boot (v1's
  `ApiIssueConfig` becomes `devops`'s schema).
- Secrets are referenced by name, and every declared secret **value**
  joins the redaction list. A stdio MCP child already gets only a short
  safe list from the SDK (`mcp.client.stdio.get_default_environment`);
  the gap is v1 passing a server's `env` from config through verbatim
  (`friday/agent/mcp.py`). v2 passes that list plus the server's declared
  secrets, nothing else.
- Kernel knobs (budget, outbox retries, thresholds) stay top-level.

---

## 12. Security controls

| Control | Now / trigger |
| --- | --- |
| Approval card shows the exact bytes, destination, audience (public/private), expanded links/mentions/attachments, and flags secret-pattern and secret-value matches; redaction also runs on drafts | now |
| **Audit log**, application-level append-only (the kernel never updates or deletes a row; the database file itself is not tamper-proof): who approved which bytes, plugin loads with tiers, lockfile and MCP-grant changes, refused handle requests. A hash chain for tamper evidence is added on trigger (a second principal or an external auditor) | now |
| Value-based redaction of declared secrets; explicit env for MCP children | now |
| No wildcards on MCP tools; boot logs every server's tool list, and a change is an audit entry | now |
| The decider of an approval is checked against `operator_id` in kernel code (§3.3) | **now** |
| Model writes from untrusted runs go to candidates (§9.4) | now |
| Board protection (§6.10): loopback, exact Host/Origin, startup session secret, `SameSite=Strict`, CSRF on every write; **`BOARD_TOKEN` removed** — today it only lifts the loopback refusal in `check_exposure` (`friday/ops/api.py`) and no route checks it, so setting it and binding `0.0.0.0` opens a board that writes admin memory to the LAN | **now** — the board already writes admin memory |
| Board accounts / login | second human principal |
| `egress` tools + opt-in for private-source runs (§5.3) | first tool that reaches outside the deployment |
| **A process boundary is not a sandbox.** Before a third-party MCP server may see private data, it runs with: empty environment plus declared secrets, a dedicated working directory, read-only mounts and a filesystem allow-list, an egress host allow-list, timeout, output byte limit, CPU/memory limits, and a tool allow-list the operator wrote (never the server's own annotations) | precondition for the first third-party MCP server touching private data |
| Per-tool rate limits; row/byte caps on SQL and log reads; bytes read per run | first read surface that can return unbounded data |

### 12.1 Running on one machine

Friday runs on the operator's laptop (`DESIGN.md` § Running it), so the
operational model is part of the design:

- **One agent process per database.** `run_agent.py` takes an exclusive
  lock (a lock file next to the database) at startup and refuses to start
  if another holds it. Two agents would run two outbox loops. The board
  (`serve_board.py`) may run beside it — it writes only through the same
  store under WAL, and never dispatches.
- **SQLite:** WAL (already on, `db.py`), an explicit `busy_timeout`, every
  write in a transaction.
- **Startup recovery:** `dispatching` → `delivery_unknown` (§3.4); graphs
  resume from checkpoints (§7); the inbox sweep backfills from cursors
  (as v1).
- **Graceful shutdown:** stop claiming work, let running nodes reach a
  checkpoint or time out, close adapters.
- **Sleep/wake and reconnect:** v1's gateway reconnect and sweep already
  cover a laptop lid; liveness does not alarm on a gap that started with
  a suspend.
- **Backup:** a daily online backup of the database (SQLite backup API)
  with a retention count; restore is a documented command.
- **Later, with their triggers:** disk-full behaviour and storage caps
  (first artifact store that grows unbounded), OS keychain for secrets
  (a second user on the machine), database encryption (restricted data),
  provider-offline mode (a local model).

---

## 13. Package layout

**★ = created in migration steps 1–10 (§15).** The rest is created when
its trigger fires (§16).

```
friday-agents/
├── run_agent.py                  ★ composition root: load config → discover plugins → build kernel → run
├── serve_board.py                  the board alone, against the live db
├── config.yaml                   ★ plugins, scopes, per-plugin config, mcp grants, kernel knobs (§11)
├── pyproject.toml                ★ one package: kernel + in-repo plugins (entry points and a lockfile on trigger, §16)
│
├── friday/
│   ├── sdk/                      ★ contracts only: Protocols + dataclasses, no I/O, no third-party imports
│   │   ├── plugin.py             ★ Plugin, PluginAPI, TaskTypeSpec (§4, §6.1)
│   │   ├── workflow.py           ★ DAG, Node, Edge, envelope, Ask/Reply/HandOver, Deps (§7)
│   │   ├── tools.py              ★ Toolset, @tool, ToolContext, SideEffect (§6.2)
│   │   ├── memory.py             ★ MemoryKindSpec, Origin, reader/writer handles (§9.2)
│   │   ├── sources/              ★ one port per read surface (§6.4)
│   │   │   ├── logs.py             LogSource, Placement, Lines
│   │   │   └── code.py             CodeSource, Excerpt, RepoRef, StackProfile
│   │   ├── scope.py              ★ Scope, audience, Principal, Role — defined now, filled later (§9)
│   │   ├── outbound.py             Outbound, Kind, draft request (§5.3)
│   │   ├── channel.py              Channel, Feature, InboundEvent, RoomRef, EntityRef (§6.6, later)
│   │   ├── approval.py             Approval, ApprovalCard, policy (§6.6, §9.3, later)
│   │   ├── model.py                ModelProvider (§6.7, later)
│   │   ├── store.py                Store + repository Protocols (§6.8, later)
│   │   ├── triggers.py             Trigger, TaskRequest (§6.9, later)
│   │   ├── middleware.py           Middleware, Layer (§8, later)
│   │   └── testing/              ★ what every plugin tests against
│   │       ├── channel.py          FakeChannel (today's fake Provider)
│   │       ├── model.py            ScriptedModel (today's scripted transport)
│   │       └── contracts/          one suite per port — when a port gets its second adapter (§16)
│   │
│   ├── kernel/                   ★ owns the invariants (§3.3); names no plugin
│   │   ├── registry.py           ★ import configured plugins, requires order, refusal rules, check() probes (§4)
│   │   ├── config/               ★ load, ${VAR}, per-plugin schemas, mcp grants
│   │   ├── deps.py               ★ per-run Deps from scope + narrowed handles (§5)
│   │   ├── harness/              ★ agents from specs + toolsets; the only vendor SDK import (§6.7)
│   │   ├── chain/                ★ budget, recording, redaction, needs/side_effect check (§8)
│   │   ├── prompts/                assemble(), escaping, trust_boundary — mechanism only
│   │   ├── inbox/                ★ stream, sweep, dedup, cursors, turns, never drop an addressed event
│   │   ├── triage/               ★ label set per scope, ranking, default_type, verdict examples (§10)
│   │   ├── extraction/             node 0 runner: transcript budget, known fields
│   │   ├── dag/                  ★ runner, state, checkpoint, version = shape + node versions
│   │   ├── pool/                 ★ tasks, concurrency, help-wanted asks
│   │   ├── outbox/               ★ state machine, approval check, the only caller of Channel.send
│   │   ├── responder/              drafts; voice by identity, prompt text from the task type
│   │   ├── memory/               ★ the one write path: writers, guard, candidates, scope walk
│   │   ├── audit.py              ★ append-only audit log (§12)
│   │   ├── summariser/             room summary rebuild
│   │   ├── ops/                    liveness, events bus, board API (plugins + checks screen)
│   │   ├── domain/               ★ TaskState / OutboundState transitions, validation engine
│   │   └── text/                   transform, param hygiene
│   │
│   └── store/                    ★ db.py split into repositories behind the Database facade (§6.8)
│
├── plugins/                      ★ first-party: same API as anyone's
│   ├── core_memory/              ★ id `core-memory`: core kinds + memory toolset
│   ├── core_intake/              ★ id `core-intake`: core:intake, the fallback that asks the operator (§10)
│   ├── core_skills/              ★ id `core-skills`: skills toolset; file skills + `skill` kind rows, `when:` matching (§6.3)
│   ├── devops/                   ★ the shape every contribution plugin follows:
│   │   ├── __init__.py             PLUGIN + register(api)
│   │   ├── params.py               ApiIssueParams
│   │   ├── extractor.py            node 0 prompt + schema
│   │   ├── graph/                  resolve, find_request_log, read_failing_code, diagnose, report
│   │   ├── sources/                LokiSource, SshKubectlSource, LocalClone
│   │   ├── toolsets.py             logs, code
│   │   ├── memory.py               devops.service, .route, .environment, .project, .dependency
│   │   ├── config.py               config schema
│   │   ├── skills/                 trace-a-request, where-to-find-a-correlation-id
│   │   ├── evals/                  triage eval_cases + case replays
│   │   └── tests/
│   ├── docs/                     ★ doc_question — the second-persona proof (§15 step 7)
│   ├── access/                     access_request
│   ├── mcp/                      ★ MCP toolsets from config; allow-lists; Keycloak auth
│   ├── discord/                    trusted adapter: Channel + Approval (§6.6, moved on trigger)
│   ├── sqlite/                     trusted adapter: Store (§6.8, moved on trigger)
│   ├── openai_compat/              trusted adapter: ModelProvider (§6.7, moved on trigger)
│   └── slack/ email/ postgres/ github_code/ anthropic/   when their triggers fire
│
├── web/                          ★ board, plugin-agnostic; Plugins screen: registrations + checks
├── migrations/                   ★ kernel tables, owned by the kernel
├── tests/
│   ├── architecture/             ★ §14 as ast tests
│   ├── contracts/                  one suite per port, when a port has two adapters
│   ├── kernel/                   ★
│   └── e2e/                        FakeChannel → task → graph → draft → approval → send
├── evals/                          triage eval runner over the enabled set (§10)
├── docs/                           DESIGN.md, DESIGN-v2.md, plugins/ (writing a plugin), agents/
├── .scratch/
└── data/

~/friday-personal/                  the operator's own plugin, outside the repo
├── friday_personal/__init__.py     PLUGIN + register(api); named by path in config (§4.1)
├── skills/                         answer-in-vietnamese, personal procedures
├── scripts/                        in-process toolset, side_effect = read; too specific → an MCP server
└── memory.yaml                     seed rows (voice, people), imported as admin
```

Why cut this way:

- **`sdk/` apart from `kernel/`** is the load-bearing split: `sdk` is the
  only thing a plugin imports, so it changes slowly (new abilities arrive
  as optional methods or `features` flags, never as a new required method
  on an existing Protocol); `kernel` changes freely.
- **`sdk/testing/` ships with the contract** so a plugin tests itself
  without copying this repo's tests.
- **Inbox, triage, outbox, memory write path, audit are kernel** (§3.3).
- **No plugin tables.** Plugins store through `memories` (pack kinds),
  `artifacts` and `dag_state`. A plugin that genuinely needs a table gets
  an Alembic branch then, on purpose.
- **Trusted adapters stay where they are until their trigger.** Discord,
  SQLite and the harness under `plugins/` is the end state, not step one.

**Dependency rule** (`tests/architecture/test_dependency_rule.py`): `sdk`
imports nothing of ours; `kernel` imports `sdk`; a plugin imports `sdk`
only. Plugin-to-plugin is **declared** (`requires`) for order and
presence, never imported; shared types cross as `sdk` ports or as a
kind's `data` schema.

---

## 14. Enforcement

| Rule | Test |
| --- | --- |
| Dependency rule (§13) | `ast` import graph over `friday/sdk`, `friday/kernel`, `plugins/*` |
| Kernel names no plugin (G1) | `ast`: no plugin import, no task-type or pack-kind literal in `friday/kernel` |
| Only source and trusted adapters spawn or connect (§6.4) | generalised `test_sources_are_the_only_door` |
| Tool `side_effect` declared, `needs` satisfiable (§6.2) | registry check at boot + test |
| No plugin receives a `Channel` (§5.3) | `Deps` construction test |
| Outbox transitions and approval check live in kernel, not Store (§3.3) | a Store fake that approves on its own is ignored by the outbox |
| Kernel chain runs even if a later plugin wrapper raises (§8) | when plugin middleware exists |
| Scope is bound by the kernel (§9.1) | every repository query takes a `Scope`; no plugin-facing API accepts one |
| Untrusted runs write candidates, not memories (§9.4) | test through the write path |
| One contract suite per port | run against every adapter and fake |
| A send interrupted mid-call is never resent automatically (§3.4) | kill between channel call and write → `delivery_unknown` after restart |
| What is sent equals what was approved (§3.4) | mutate a row after approval → dispatch refuses |
| Board writes need the session's CSRF token and an exact Host (§6.10) | foreign Host / missing token refused |
| One agent per database (§12.1) | second `run_agent` refuses to start |

---

## 15. Migration from v1

Reordered three times (Appendix A, B, then **D / ADR 0001**). The current
order: **library-independent defects first**, then the **runtime libraries
as the foundation** (Pydantic AI, then DBOS) so the plugin-facing seams are
drawn once on the libraries that will actually live there, then the
registry / `sdk`–`kernel`–`plugins` split, then the expensive adapter moves
when their triggers fire. Step 1 fixes v1 as it is today and depends on
nothing else. Each step leaves the suite green and moves its section into
`DESIGN.md`.

| # | Step | Size | Done when |
| --- | --- | --- | --- |
| 0 | Land or stash the uncommitted `api_issue` work; commit the `params` migration fix | S | clean tree |
| 1 | **Library-independent defects.** Board protection (§6.10: delete `BOARD_TOKEN`; exact Host/Origin, startup session secret, CSRF on every write); **the decider checked against `operator_id`** (§3.3); single-instance lock and `busy_timeout` (§12.1). *(The outbox delivery state machine — §3.4 `dispatching`/`delivery_unknown`, idempotency, frozen-payload hash — moves to step 3, folded into DBOS.)* | S–M | a write without a token or with a foreign Host is refused; a second `run_agent` refuses to start (guards deleted once and watched go red) |
| 2 | **Pydantic AI — the agent loop and the `ModelProvider` seam** (§6.7, ADR 0001). Replace `openai-agents` in `harness.py`; draw the `ModelProvider`/harness seam; the kernel wraps its §3.3 invariants (budget, redaction, recording) around every model call. Replace the vendored `ScriptedModel` test double with a Pydantic-AI equivalent under `sdk/testing/` | L | the harness suite is green on Pydantic AI; **triage eval re-run and reported** (the harness is upstream of the classifier) |
| 3 | **DBOS — durable workflow port + adapter** (§7, ADR 0001). Spike DBOS on MiniMax-M3 + SQLite → confirm in an ADR; `sdk/workflow.py` a thin Friday port, DBOS the adapter beneath the kernel (plugins never import `dbos`); migrate the DAG engine to DBOS; drop the derived-version scheme; **fold the outbox delivery state machine into a DBOS workflow**; `Deps` reconstructed inside the run from a serializable scope key | L | kill between the channel call and the write → the row is `delivery_unknown` after restart and nothing is sent twice, now via DBOS recovery |
| 4 | **Task types register themselves.** `PARAMS`, `EXTRACTS`, `_graphs` → one `TaskTypeSpec` registry filled by `register()`; graphs expressed in the `sdk` workflow types; `run_agent.py` calls them | S–M | `router.py` has no `api_issue` import or literal; triage prompt untouched |
| 5 | **Memory kinds register themselves.** `_READERS`/`_WRITERS` and `*Data` from `MemoryKindSpec` (**trimmed to the fields this step uses**, §9.2, Appendix D); `MemoryKind` a validated string; `ModelMemoryKind` stays closed | S–M | `test_memory_kinds.py` asserts against the registry |
| 6 | **Typed per-run `Deps`.** Replace `DAG_DEPS_EXTRA` with the task type's `deps` factory, built from the run's scope key | S | guard deleted once and watched go red |
| 7 | **`friday/sdk/` + `plugins/devops/`.** Extract only the ports devops needs (`LogSource`, `CodeSource`, `TaskTypeSpec`, `MemoryKindSpec`, `Deps`, workflow port); move `api_issue`, `sources/`, devops kinds (rows renamed to `devops.*` by a data migration, which also covers every column holding a type or kind name — `tasks.type`, triage examples and verdicts, `node_runs`, `model_calls.agent` — each checked by grep before the migration is written; `runbook` rows become `skill` rows), skills, `ApiIssueConfig` → plugin schema; fix `sources/code.py` policy-as-data | L | kernel `ast` test passes (no plugin import, no task-type literal); triage eval re-run and reported |
| 8 | **Second persona: `plugins/docs/`** (`doc_question`, already a one-node graph) as the proof G2 holds without kernel edits | M | added with zero kernel diff |
| 9 | **Store split** behind the `Database` facade; outbox transitions and memory writers move into kernel code; **workflow state is DBOS's and is excluded from the split** | L | no invariant enforced only inside `db.py` |
| 10 | **Remaining "now" rows of §12** (approval card, audit log, value redaction, MCP env, backup) | M | each has a test |
| 11+ | Triage across plugins (§10, deferred — Appendix D), Channel/Approval, Store port, Triggers, Scope columns, principals — each when its trigger fires (§16) | L each | — |

**Done criterion for "the kernel knows no domain"** is *no import of a
plugin and no task-type or pack-kind literal* — not a ban on words.
`curl` in `text/transform.py` (verbatim spans) and `correlationId` in the
operator's voice are generic behaviour or data; they move to data (voice
rows, span patterns in config) only if a second persona needs different
ones.

---

## 16. Deferred, with triggers

| Designed in | Built when |
| --- | --- |
| `Channel`/`Approval` split, opaque cursors, attachments, world/room/entity (§6.6) | a second channel is being written |
| `ModelProvider` port + ADR on who owns the loop (§6.7) | **trigger fired — moved to §15 step 2** (the Pydantic AI adoption, ADR 0001) |
| `Store` Protocol + second adapter (§6.8) | Postgres, or a second process |
| `Trigger` contribution (§6.9) | the first task that is not a message |
| Plugin middleware (§8) | a second caller (PII masking, tracing export) |
| `MemorySearch` port, hybrid retrieval (§9.4) | substring search measurably misses |
| `team` scope level; user audience axis (§9.1) | a second team; personal rows |
| Principals, roles, policies beyond owner self-approval, board accounts (§9.3) | a second human approver |
| Retention, erasure (§9.2) | personal or restricted data |
| Audit hash chain (§12) | a second principal or an external auditor |
| Disk caps, keychain, encryption, offline mode (§12.1) | per row in §12.1 |
| Triage across plugins (§10): per-scope label set, `core:intake`, `eval_cases` per plugin, example provenance/retirement; and two-stage triage | **deferred (Appendix D)** — the enabled set is actually large (only `api_issue` is real today); until then the hard-coded `api_issue` fallback stays |
| Board render hints (§6.10) | a plugin whose envelopes the generic view renders badly |
| Egress class, MCP sandbox (a precondition, not an upgrade), rate limits (§12) | per row in §12 |
| Entry-point discovery, per-plugin packaging, version ranges on `requires`, a separate `friday-sdk` distribution (§4.1) | the first plugin outside this repo |
| `friday.lock`: third-party MCP servers and skills pinned by version + hash (§6.3, §12) | the first third-party MCP server or skill |
| Contract suites per port (§13) | a port's second adapter |
| Provider policy by sensitivity, decided at run start (§9.2) | personal or restricted data |
| Runtime libraries — Pydantic AI for the agent loop, DBOS for durable workflows — replacing `openai-agents` and the hand-written DAG engine behind the same seams | **now — the foundation, before the plugin migration** (§15 steps 2–3, ADR 0001); research in `docs/research/pydantic-ai-migration.md` |

---

## 17. Vocabulary (new terms — move to `CONTEXT.md` when adopted)

- **Kernel** — holds the invariants of §3.3; names no plugin.
- **Trusted adapter** — a swappable implementation of a port the kernel
  must hand a dangerous capability (Channel, Approval, Store,
  ModelProvider); kernel invariants sit above it.
- **Contribution plugin** — first-party code that registers task types,
  toolsets, sources, kinds, skills through `register(api)`.
- **Out-of-process tier** — MCP servers; the only real boundary.
- **Handle** — a narrowed object a plugin is given at run time (memory
  reader for some kinds, one source, a draft bound to one room).
- **Toolset** — a named group of tools; what an agent is given.
- **Core kind / pack kind** — a memory kind every install has / one a
  plugin brings, namespaced by the plugin.
- **Scope / audience** — where a row applies (`team?`, `room?`) / whose
  eyes a run's output reaches.
- **Principal** — a person with a role at a scope; the operator is the
  first.
- **Trigger** (later) — a non-message source of tasks.
- **Intake** — `core:intake`, the task type for what no other type claims;
  a generalist agent that asks the operator how to handle it (§10).
- **`skill` kind** — an operator-authored skill stored as a scoped memory
  row, beside file skills shipped by plugins; v1's `runbook`, generalised
  (§6.3).

## 18. Open questions for the operator

Resolved 2026-09-21:

- `runbook` becomes skills (§6.3).
- The fallback is a generalist that asks the operator (`core:intake`, §10).
- The operator's own scripts run in-process as the operator's plugin; a
  tool too specific for a plugin is an MCP server (§3.1).
- The narrower scope always wins, constraints included; no binding rows
  (§9.1).

None open.

---

## Appendix A. Review log — 2026-09-21

Five reviewers read the first draft independently (pragmatist, plugin
architect, security, migration against the real code, domain model).
What changed, what was kept, what was declined.

### Accepted

| Finding | From | Change |
| --- | --- | --- |
| Static manifest + grants duplicate `register()` three times; their justification is untrusted in-process code, which the draft had already ruled out | pragmatist | §4.1: `register()` is the manifest; §5.1: handles, no grant grammar in-process |
| Store, Approval, Channel, ModelProvider fail the kernel test yet were "plugins" | security | §3.1 trusted-adapter tier; §3.3 invariants above their ports |
| In-process grants are convention in Python | security | §3.2 enforced vs convention table |
| A "read" tool can exfiltrate through its arguments; drafts can leak once approved | security | §5.3 destination classes, draft bound to room, `egress`; §12 approval card |
| Kernel middleware must be outermost *and* innermost | security | §8 |
| Memory poisoning through model-written `finding` | security | §9.4 untrusted runs → candidates, provenance mandatory |
| Secrets: MCP children inherit env; redaction matches shapes only | security | §11 explicit env, value redaction |
| Unauthenticated board as approval surface | security | §6.6, §12: CSRF/Origin before it approves |
| Audit log missing | security | §12, `kernel/audit.py` |
| `user` is an audience, not a level | domain | §9.1 |
| Single unnamed operator | domain | §9.3 principals, roles, approval policy (defined now, built on trigger) |
| `readers` on the kind breaks the dependency rule | domain | §9.2 readers from the reader's needs |
| `injected` quietly reverses a v1 rule | domain | §9.4 recorded as a decision; `CONTEXT.md` fix scheduled |
| Kind spec lacks sensitivity, schema versioning, dormant-on-uninstall | domain | §9.2, §6.1 |
| `finding` is generic; `person` needs several `EntityRef`s | domain | §9.2 core kinds |
| One closed-set classifier does not scale; plugin installs change it | domain, architect, pragmatist | §10; `TaskTypeSpec.accepts/examples/contrasts/eval_cases` |
| Extractor/graph keyed by string; foreign registration | architect | §6.1 extractor and graph inside `TaskTypeSpec`; §4.3 own ids only |
| Plugin-to-plugin dependencies used but forbidden | architect | §4.1 `requires` for order/presence, never imports |
| Plugin version in `DAG.version` discards checkpoints on a README patch | architect | §7 node versions only |
| Boot health, hot reload, `pluggy`, middleware order | architect | §4.2 `check()`, §1 non-goal, §4.3 no pluggy, §8 `after=` |
| Board plugin JS = XSS | architect | §6.10 declarative hints only |
| Migrations belong to the kernel, not the sqlite plugin | architect | §6.8, §13 |
| "No `curl` in kernel" unreachable by moving files; generic code uses it | migration | §15 done criterion reworded |
| Harness split is a rewrite, undecided | migration | §6.7 deferred behind an ADR |
| Per-plugin config missing from the plan; kind-name data migration missing | migration, pragmatist | §15 step 6 |
| Step order: registries first, adapters last, second persona early | migration, pragmatist | §15 reordered |
| Tenancy by deployment instead of org columns | pragmatist | §1, §9.1 |
| Store port, ModelProvider port, Channel split built before a second adapter | pragmatist | §6.6–6.8 now/later with triggers |

### Declined or narrowed

| Proposal | From | Why |
| --- | --- | --- |
| Ship a TOML manifest as package data, read without import | architect | Solves untrusted in-process code, which v2 does not run; kept as the answer if that non-goal ever changes |
| Re-grant on manifest digest change (Android-style) | architect | Applies only to the out-of-process tier, where `friday.lock` + the boot diff (§12) does it |
| Drop scope from the design entirely | pragmatist | Kept as *types defined now, columns on trigger* — cheap, and it fixes the meaning of the column added later |
| Build principals and board auth now | domain | Defined in `sdk` now; built with the second approver |
| Network sandbox for MCP servers now | security | No third-party server yet; row in §12 with its trigger |
| Two-stage triage now | domain | Measured by the eval first |
| Separate `sdk` distribution now | architect | No plugin outside this repo yet |
| Binding constraint rows that narrower scopes cannot override | domain | Operator: the narrower scope always wins; rules that must hold everywhere belong in kernel code |
| Operator's scripts as a subprocess toolset | security | Operator: not needed yet; specific tools go to MCP. Recorded in §3.2 as convention, not boundary |

---

## Appendix B. External review — 2026-09-21

A reviewer outside the first five read the revised draft. Its findings
were checked against the code before being accepted; three turned out to
be defects in the running system, not only in this document.

### Accepted

| Finding | Evidence in code | Change |
| --- | --- | --- |
| Outbox crash window: a send that succeeds before the process dies is resent | `friday/outbox/__init__.py` `_deliver` marks sent after the call and documents the double-post as a chosen trade-off | §3.4 `dispatching` / `delivery_unknown`; §15 step 1 |
| Approved payload must be immutable | v1 sends stored `text`, but nothing checks it against what was approved | §3.3, §3.4 frozen payload + hash |
| Board protection cannot wait for a second person | The board already writes `admin` memory rows (`friday/ops/api.py` memory routes) with loopback as its only defence; no Host, Origin or CSRF check | §6.10, §12, §15 step 2 |
| No single-instance guarantee; `serve_board.py` is a second writer | No lock in `run_agent.py`; `serve_board.py` documents that it writes | §12.1 |
| "Requester may not approve" breaks the personal case | The owner asking Friday and approving the draft is the common flow | §9.3 risk-based `self_approval` |
| Provider policy must cover `personal`, not only `restricted` | — | §9.2 policy per sensitivity, max over the prompt |
| A process boundary is not a sandbox | — | §12: sandbox is a precondition for third-party servers touching private data |
| `entry_points().load()` executes module code | — | §4.1 filter before import; tier shown on the board |
| "Close descriptions detected at boot" is not deterministic | — | §4.3 deterministic boot checks; §10 overlap via the eval |
| "Append-only" in SQLite overstates it | — | §12 "application-level append-only"; hash chain on trigger |
| Local-first operations missing | v1 has WAL and reconnect/sweep, no lock, no backup | §12.1 |

### Accepted with changes

| Finding | Change and why |
| --- | --- |
| Triage corrections can self-poison; add a candidate stage with repeated confirmation | A re-typing is an explicit human mark, which v1 already treats as sufficient. Kept active at once, but with provenance, retirement on type-version change, a per-type cap and a board switch (§10) |
| Resume needs a full `RunVersion`; incompatible → `needs_human` | Added params and state schema digests to `DAG.version` and snapshot it on the run. A mismatch still discards and re-runs, as v1 does — nodes only read, so re-running is safe and never runs new code on old state (§7) |

### Declined

| Finding | Why |
| --- | --- |
| Budget reservation ledger (reserve max cost, settle, release) | Every call is already bounded by `max_tokens` and `max_turns`, so the daily budget is overshot by at most one call. A money ledger adds state for a single operator with no gain; the bound is now stated in §3.3 |

---

## Appendix C. Independent review — 2026-09-21

A reviewer given only §1–§18, the code and `DESIGN.md` — not the earlier
reviews or their appendices — scored the draft: soundness 7, internal
consistency 5, complexity balance 4, safety/reliability 6, migration
realism 6, fit for current stage 4. Three of its claims were re-checked in
the code before acting (`BOARD_TOKEN` in `check_exposure`, the MCP SDK's
default environment, the `DAG.version` docstring); all three held.

### Accepted

| Finding | Change |
| --- | --- |
| The approver's identity is never checked; v1 is safe only because the card is a DM | §3.3, §12, §15 step 1: kernel checks the decider against `operator_id` |
| `BOARD_TOKEN` lifts the loopback refusal but no request is checked against it | §6.10, §12, §15 step 2: deleted |
| §3.4 drew one path; `ask_for_details` and `approval_card` go out unapproved | §3.4 `policy_approved` edge; hashed at enqueue |
| Entry points, per-plugin packaging, lockfile, contract suites are over-built for one plugin (the third reviewer to say so) | §4.1 import paths in config; the rest moved to §16 with triggers |
| Declared node `version` silently reversed v1's "derived, never declared" | §7: derived from node source, params and state digests |
| Per-call sensitivity routing is not buildable inside the SDK's tool loop | §9.2: decided at run start from `needs`; built on trigger; removed from step 10 |
| "Row skills" contradicted "plugins never see the store" | §6.3: a `skill` memory kind read through a handle; `runbook` generalised, not removed |
| "stdio servers inherit the parent's env" is false for the SDK in use | §11 corrected; the gap is config `env` passed verbatim |
| Third-party allow-list moved to a lockfile without argument | §6.2: code for our callers, operator's config list for third-party servers, argued |
| The two-identity model (send as operator vs as Friday) is missing | §6.6 identities per channel, `Outbound.as_`, fallback to Friday or `sent_manually` |
| Budget "fails closed" contradicts v1 failing open on a store error | §3.3 both cases, deliberately different |
| `core:intake` has no cap | §10 per-room hourly cap, digest beyond it |
| Step 6's data migration may miss columns holding type names | §15 step 6 lists them and requires a grep first |

### Declined

None. The reviewer's "keep exactly as is" list — §3.2, §3.4's two new
states, §1 non-goals, no `pluggy`, tenancy by deployment, narrower-wins,
`ast` enforcement, "no import, no literal", the trigger table, steps 1–2
first — is unchanged.

### Added after review — 2026-09-22

The operator asked how a plugin is structured and whether it needs a base
class. §4.1 now gives `Plugin` its `config` field (§4.2 already validated
a plugin's block against "its schema" without saying where the schema
lives), the package shape, a full `register()` example, and the case
against a `BasePlugin` class: abstractions live in the `sdk` ports as
`Protocol`s, and a plugin is a declaration.

---

## Appendix D. Grilling session — 2026-09-22 (accepted, re-sequenced)

A grilling session with the operator turned this document from a proposal
into the accepted target and **re-sequenced the migration**. An adversarial
re-read (a sub-agent, findings beyond Appendices A–C) surfaced that the
plugin-facing contract was being defined against the DAG engine and harness
that the runtime libraries replace. Recorded in
`docs/adr/0001-runtime-libraries-before-plugin-migration.md`. **These
decisions win over any body text above that still reads the old way** — the
body is corrected as §15 is executed.

| Q | Decision |
| --- | --- |
| Adopt the plugin restructure? | **Yes, now, deliberately** — not trigger-gated; the goal is a stable SDK surface plugins can be built against in parallel |
| Runtime libraries vs the restructure | **Libraries are the foundation, first**: Pydantic AI (§15 step 2) → DBOS (§15 step 3) → the plugin split. Reverses the old "defects first, libraries in §16" order |
| How plugins see the workflow engine | `sdk/workflow.py` is a **thin Friday port**; DBOS is an adapter **beneath the kernel**; plugins import `friday.sdk`, **never `dbos`** (§6.2, §7) |
| DBOS/Pydantic AI in the trust model (§3.1) | **Foundations the kernel builds on** (not swappable-behind-a-port); the kernel still **wraps its §3.3 invariants** around every step and model call |
| §7 version/checkpoint | The `version = digest of node source` scheme is **dropped**; durability, memoization and resume are DBOS's |
| §5.2 `Deps` under DBOS | A workflow takes a **serializable scope key**; `Deps` (live handles) are rebuilt inside the run |
| Defects (old steps 1–2) | **Split by "is it a durable loop?"**: board protection + approver-id + single-instance lock + `busy_timeout` ship in step 1; the **outbox delivery state machine folds into DBOS** (step 3) |
| Triage across plugins (old step 8, §10) | **Deferred to §16** until the enabled set is actually large; the hard-coded `api_issue` fallback stays |
| `MemoryKindSpec` (§9.2) | **Trimmed** to the fields step 5 uses; `schema_version`/`upgrade`/`sensitivity`/`allowed_scopes`/`audience`/provider-policy added on trigger with a test (Rule 13) |
| Plugin location (§4.1, §16) | Plugins land **in the repo**; "develop elsewhere, then copy/register in" is a workflow, not an architecture requirement — no entry-point/packaging/`friday.lock` machinery now |
| `DESIGN.md` vs this document | `DESIGN.md` stays the **as-built** record; sections move here → there as each step lands (no wholesale overwrite) |

### Scope of §3.1 with DBOS

DBOS holds run persistence, step memoization and resume — three of the
§3.3 invariants' *mechanism* ("every node timed, retried, recorded";
checkpoint) now live in a library. It is neither kernel nor a plugin nor
out-of-process, so §3.1 gains a line: **the kernel builds directly on DBOS
(and Pydantic AI) as foundations, and re-asserts every §3.3 invariant in
its own chain (§8) around each step** — a library reporting success does
not by itself satisfy an invariant.
