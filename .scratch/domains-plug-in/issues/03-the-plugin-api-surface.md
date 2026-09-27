Type: prototype
Status: open
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
