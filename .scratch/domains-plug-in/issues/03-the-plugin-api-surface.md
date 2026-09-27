Type: prototype
Status: open
Blocked by: 01, 10

# The plugin API surface

## Question

Stub `PluginAPI` as a plugin needs it under the spine, contributing everything
without touching the core: register an **action contract**, a **step type**,
`toolset(name, factory)`, the MCP tools its sources need, memory kinds and
readers, an intake enricher, and reference a model tier by name. No `graph`.
Show `plugins/backend/__init__.py` registering both backend actions, and what
`caps` still carries. Decide what the boot refuses (duplicate names, unknown
tier / toolset / step type).
