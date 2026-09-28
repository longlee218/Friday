Type: prototype
Status: resolved
Blocked by: 10

# The plugin API surface

## Question

Stub `PluginAPI` as a plugin needs it under the spine, contributing everything
without touching the core: register an **`Action`** (name, recognition,
`ActionContract` — shape decided in the action-contract ticket), a named
**agent** (`api.agent(...)`: instructions, result shape, terminal tools, model
tier, maximum toolsets, per-run budget), `toolset(name, factory)`, the MCP tools
its sources need, memory kinds and readers, and the **domain's** intake enricher
and `placement_identity`. No `graph`, no plugin-defined step types.

Also settle where the per-run `deps` factory (live handles built per run) lives
now that `TaskTypeSpec` is gone. Show `plugins/backend/__init__.py` registering
both backend actions and their agents, and what `caps` still carries. Decide
what the boot refuses (duplicate names, unknown tier / toolset / agent).

## Carried in from ticket 04 (2026-09-28)

- **Domain tools wrap MCP; the agent never sees MCP.** A toolset tool (e.g.
  `running_version` in `backend.code`) calls the MCP tool underneath and
  parses the answer down to what the agent needs — the `sources/` pattern
  (`ReleaseSource.running_tag`: `release_status` returns ~104,761 chars,
  measured 2026-09-21, of which the tag is one field). Decide how
  `api.toolset` declares the MCP tools a wrapper may call.
- **Read-only allowlist.** devops-generic also offers `release_apply`,
  `release_rollback`, `release_rollout`, `release_set_env`; only declared
  reads may be reachable (today: `ReleaseSource.TOOLS`, `friday.sources.Reads`).
- **Keycloak auth already exists** — `SsoTokens` in
  `friday/kernel/harness/auth.py` (refresh-token rotation, early refresh,
  one 401 retry) for both devops-generic and db-generic. Nothing to decide
  beyond wiring it through whatever `api.toolset` becomes.

## Carried in from ticket 15 (2026-09-28)

- **`Action` gains an optional `acknowledge(IntakeContext) -> str | None`**,
  outside the contract like `recognition` / `planning`. Absent or `None` →
  the spine sends no acknowledgement for that action.

## Answer

Decided 2026-09-28 (prototype + grilling). Prototype: branch
`prototype/plugin-api-surface`, file
`.scratch/domains-plug-in/plugin_api_surface_STUB.py` — run it to see
`plugins/backend` register both actions, a toolset factory get its narrowed
`Reads`, and every boot refusal fire.

```
Plugin(id, register, enricher=None, config=None)     one PLUGIN per package
register(api)          runs ONCE at boot; declares data + functions, no I/O
  api.action(Action(name, recognition, contract, acknowledge=None, planning=None))
  api.agent(AgentSpec(name, description, instructions, result, tier,
                      toolsets, budget, temperature))
  api.toolset(ToolsetSpec(name, description, factory, mcp={server: TOOLS}))
  api.memory_kind(spec)                      unchanged
  api.reader(name, kinds)                    unchanged; agent name or "code"
factory(run: RunContext) -> tools            per run
  RunContext = task_id · domain (the enricher's type) · evidence ·
               mcp {server: Reads narrowed to this toolset} · config
```

1. **`deps` and `caps` are deleted.** Under the spine the plugin builds no
   graph, so nothing is left for `caps` (`prepare_node`, `simple_dag`,
   `make_harness` go; `sender`/`approver` belong to the core outbox). The one
   per-run need — live sources — is built by the toolset factory that uses
   them, from a core-built `RunContext`. Shared state across toolsets is
   already in `ctx.domain` (`Placement`).
2. **MCP reads are declared per toolset**: `mcp={"devops-generic":
   ReleaseSource.TOOLS}`. The source class's `TOOLS` stays the single place the
   list is written; the core alone holds raw servers (auth via `SsoTokens`
   wired there) and hands the factory `Reads(server, allowed)`, so a factory
   never reaches `release_rollback`. Per plugin would let `backend.logs` see
   `backend.db`'s reads.
3. **`AgentSpec` is a declaration, not a Pydantic AI agent** — pure data in
   `friday/sdk`, the `CONTEXT.md` "agent declaration". The core joins it with
   the tier from `config.yaml` and the run's toolsets (contract ∩
   `spec.toolsets`) and runs it through the **Harness**; only `harness.py`
   imports `pydantic_ai`. Named `...Spec` like `MemoryKindSpec`/`ToolSpec`.
   `budget` (turns, tokens, seconds) and `temperature` live here (ticket 07).
4. **Terminal tools are the core's.** Every agent gets `ask_reporter`,
   `hand_over`, `replan`; the plugin declares only `result`. `ask_reporter` is
   dropped only when the action's contract has no `ask` — the plugin author's
   fixed ceiling, never the Planner's. The Planner writes a phase's `brief`
   and never steers the agent's turns.
5. **The enricher sits on the `Plugin` value** (`Plugin(enricher=enrich)`),
   not `api.*` — one per domain by type, no boot check. Its return type is
   the domain type; `IDENTITY` and `retrieval_keys()` stay on it (ticket 04).
   No enricher (`ops`) → domain `None`, identity `()`.
6. **`register` runs once.** The two lifecycles existed only because `caps`
   appeared late; with `caps` gone, the host records everything then
   cross-checks.
7. **Boot refuses** — all offline checks on registered data:
   1. a duplicate name within actions, agents, toolsets or memory kinds;
   2. a name not under `<plugin.id>.` (core uses `core.`);
   3. an agent tier not in `config.yaml`;
   4. a dangling reference: agent → toolset, contract → agent / toolset,
      reader → agent (except `"code"`);
   5. a contract granting an agent/toolset whose domain type differs from its
      domain's enricher type (ticket 04);
   6. a toolset `mcp` server not in `config.yaml`;
   7. the recognition checks of ticket 02.
   Not at boot: whether an allowlisted tool exists on the server (needs the
   network) — the server filter and `Reads` refuse at call time, as today.
8. **Memory kinds and readers unchanged** (`api.memory_kind`, `api.reader`),
   since readers include `"code"` and core agents that have no `AgentSpec`.

Build consequences: `TaskTypeSpec`, `PluginAPI.task_type`, `caps`, the
two-lifecycle `plugin_host.py` and `plugins/devops/__init__.py`'s
`_enrich_deps` go; `sources/*` keep their `TOOLS`; `ApiIssueDeps` dissolves
into toolset factories. New sdk terms for `CONTEXT.md` § Vocabulary at build
time: *agent spec* (the agent declaration), *toolset spec*, *run context*.

## Amended 2026-09-28 (second grilling session)

A second session grilled the same stub while the answer above was being
committed. Where the two differ, **this section wins**.

1. **No cross-plugin grants.** An action's contract may grant only its own
   plugin's agents/toolsets plus `core.*`. A tool two domains need moves to
   core. `Plugin.requires` is deleted.
2. **`Plugin.config` is deleted; `RunContext` loses `config`.** After ticket
   07 and 15 the backend block held nothing a plugin needs:
   `timeout_seconds` / `reports_dir` were already gone; `loki_server` /
   `loki_tool` become constants in the toolset's `mcp` declaration
   (`mcp_servers` stays global in core config); `ssh_host` goes (alias `dev`
   in `~/.ssh/config`, a constant); `container_roots` / `not_ours` are knobs,
   not install facts, and become constants in `plugins/backend`.
   `Plugin(id, register, enricher=None)`.
3. **New core toolset `core.shell`** — run a command locally or over SSH on
   a host the operator declares in core `config.yaml` (install fact, global
   like `mcp_servers`; a host not listed is refused). **Read-only**:
   - one **read-command allowlist, a core constant**, the same for every
     plugin (`kubectl get/logs/describe/top`, `cat`, `grep`, `tail`, `head`,
     `ls`, `ps`, `df`, `journalctl`, …); a plugin only chooses whether its
     contract grants `core.shell`;
   - parsed with `shlex`, never regex: `|` allowed when **every** segment is
     on the list; `;`, `&&`, `||`, `$( )`, backticks, `>`, `<` refused;
     known write flags refused (`sed -i`, `find -delete/-exec`, …);
   - a command off the list is **refused, not queued for approval** — no
     approval flow, no pause. Every refusal is written to `audit_log`
     (task, toolset, host, command) and listed on the board, so the operator
     widens the list by commit when it was harmless;
   - output enters `Evidence` like `read_log` (refs resolve against it) and
     passes the existing secret redaction; `save_to` writes it into the
     workspace instead of the context.
   `backend.logs`' `read_log` stays (it knows `Placement`, numbers lines);
   `core.shell` is for everything else.
4. **New core toolset `core.workspace`** — `/tmp/friday/<task_id>/`, a core
   constant. Friday's own scratch space: read, write, delete freely inside it,
   through pydantic-ai-harness `FileSystem(root_dir=…)`, which confines file
   tools to the root. Losing it on reboot is fine; the DB, backups, skills
   and clones stay where they are. Not a shell with full power: the harness
   docs say a command allowlist and `cwd` are "a guardrail … rather than a
   robust security boundary", so a write-capable shell waits for a container
   (fog).
5. **Boot also refuses**: 8. a contract granting another plugin's
   agent/toolset; 9. a `core.shell` host not declared in `config.yaml`.

**Amends D6** (`docs/DESIGN.md`): "every tool a graph is given is a read"
still holds for the world outside Friday — `core.shell` is read-only by an
allowlist enforced in code, not by prompt. The one place Friday writes is its
own workspace. Correct D6 in the build commit that adds `core.shell`.

Build consequences (in addition to the above): `DevopsConfig` /
`load_devops_config` and the `devops:` block in `config.yaml` go;
`SshKubectlSource.host` stays `"dev"`; core config gains a shell-host list;
`pydantic-ai-harness` becomes a dependency (imported in one module, per the
reuse-before-rewrite rule). New terms for `CONTEXT.md` § Vocabulary at build
time: *workspace*, *read-command allowlist*.

## Amended 2026-09-28 by ticket 16

`AgentSpec.budget` is `(max_turns, tokens)`, not (turns, tokens, seconds): `max_turns` includes tool turns, `tokens` is input + output summed over the run. See [The budget in three groups](16-the-budget-in-three-groups.md).
