Status: ready-for-agent
Blocked by: 05

# Core toolsets: memory, skills, shell, workspace

Decision: [The plugin API surface](../../domains-plug-in/issues/03-the-plugin-api-surface.md) — amendment §3–4, §5.9.

## Goal

`friday/kernel/toolsets/` (was `kernel/tools/`), each a `ToolsetSpec`:

- `core.memory`, `core.skills` — today's tools regrouped.
- `core.shell` — local or SSH on a host declared in `config.yaml`;
  read-command allowlist as a core constant; `shlex` parse; `|` only when
  every segment is allowed; `; && || $( ) \` > <` refused; write flags
  (`sed -i`, `find -delete/-exec`, …) refused. Off-list → refused, written to
  `audit_log` (task, toolset, host, command). Output enters `Evidence`,
  passes secret redaction; `save_to` writes into the workspace.
- `core.workspace` — `/tmp/friday/<task_id>/` via pydantic-ai-harness
  `FileSystem(root_dir=…)`; `uv add pydantic-ai-harness`, imported in one
  module.

## Acceptance

- [ ] One test per refusal class; an off-list command writes an
      `audit_log` row.
- [ ] A path escaping the workspace root is refused (test).
- [ ] Guard: only one module imports `pydantic_ai_harness`.
- [ ] `docs/DESIGN.md` D6 corrected in this commit (world read-only by an
      allowlist in code; Friday writes only its workspace).
- [ ] `CONTEXT.md`: *workspace*, *read-command allowlist*, *toolset*.
- [ ] Whole suite green; `code-review` done.
