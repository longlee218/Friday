Status: done
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

- [x] One test per refusal class; an off-list command writes an
      `audit_log` row.
- [x] A path escaping the workspace root is refused (test).
- [x] Boot refusal 9, carried from ticket 05: a `core.shell` host not
      declared in `config.yaml` refuses the boot (in
      `friday/kernel/boot_refusals.py`, test watched red).
- [x] Guard: only one module imports `pydantic_ai_harness`.
- [x] `docs/DESIGN.md` D6 corrected in this commit (world read-only by an
      allowlist in code; Friday writes only its workspace).
- [x] `CONTEXT.md`: *workspace*, *read-command allowlist*, *toolset*.
- [x] Whole suite green; `code-review` done.

## Built (2026-09-29)

Operator's calls this session: (Q1) refusal 9 = an action granting
`core.shell` while `config.yaml` `shell_hosts` is empty; a host the model
names that is not declared is refused at call time and audited. (Q2) the core
toolsets are built by `core_toolsets(db=, skills=, hosts=)`, registered under
a `core` owner in `load_plugins`; `RunContext`/sdk unchanged. `db`/`skills`
are `None` at boot until 14 wires the runner (a factory built without its
dependency raises `NotWired`).

Review fixes: a quoted `"|"`/`";"` stays an argument (non-POSIX lexer for
operators); `kubectl` credential/server flags refused (`--kubeconfig` can name
a workspace file whose `exec` entry runs any binary), `journalctl
--cursor-file` refused; a timeout kills the whole process group; `ssh --`
before the host.

Left open, on purpose:
- **No workspace delete.** pydantic-ai-harness 0.36.0's `FileSystem` has no
  delete tool; the amendment's "read, write, delete" is read/write/edit/list.
- Refusals are in `audit_log` but **not listed on the board** (amendment §3):
  not in this ticket's Goal/Acceptance.
- `core.memory` needs a `FridayState` on `ctx.deps`, which `run_agent` does
  not pass; the Harness still wires skill tools itself when given `skills=`,
  so granting `core.skills` to such an agent would duplicate them. Both are
  14's to reconcile when it wires spine agents.
- Shell output enters `Evidence` by duck typing (`run.evidence.show`); the
  class lives in `plugins/backend`.
- Secret reads (fixed after close, operator's call — option A, patch the
  known holes; two review rounds): `kubectl` refuses the `secret` resource,
  `--raw`/`-f`/`-k`/`--template` and `*-file` outputs, its short flags
  matched inside a cluster; `ps` refuses printing environments (`e` as its
  first argument, `-E`); `cat`/`grep`/`head`/`tail` refuse an argument naming
  a credential file/dir (`SECRET_FILES`, `SECRET_DIRS`, case-insensitive —
  grep's pattern included, since telling it from a path means parsing grep's
  options); every `grep` runs with `--exclude`/`--exclude-dir` for them and
  may not `--include` or `-R`, long options matched by unambiguous prefix.
  **Still a guardrail by name, not a boundary** (the operator accepted this
  over dropping the file readers): `grep -r` on a symlink named otherwise
  still follows it on the command line; a secret in a file named otherwise
  (`config.yaml`, a pod's logs) is read. False refusals, accepted:
  `kubectl get pods -n secrets`, `kubectl logs -f` (use `--follow`), a value
  written into a kubectl cluster (`-ojsonpath=…`; write `-o jsonpath=…`), a
  grep pattern that looks like a credential name (`grep id_rsa auth.log`).
- `/tmp/friday/<id>` is on a shared `/tmp` with predictable ids; a
  pre-planted symlink there would redirect writes. Low risk on the operator's
  one-user machine.
