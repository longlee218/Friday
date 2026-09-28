Type: prototype
Status: claimed
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
