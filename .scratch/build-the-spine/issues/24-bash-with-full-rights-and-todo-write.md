Status: ready-for-agent
Blocked by: 23

# `bash` with full rights, and `todo_write`

Second of three (see [23](23-core-repos-read-grep-glob-at-the-running-tag.md)).
Blocked by 23: `bash`'s description steers to `read`/`grep`/`glob`, and
they are removed from `MAX_READS` there.
Amends: build-the-spine ticket 08 (`core.shell`), **`docs/DESIGN.md` D6**.
Source: operator decisions, 2026-09-30; research on Claude Code's
`src/tools/{Bash,TodoWrite}Tool`.

## The problem

`core.shell` (`run_command`, a read-only allowlist) exists but no agent
holds it, and it does not count against the read budget. A diagnosis is
5–15 reads with nowhere to write down what to check next.

## What is decided

- **`bash` has full rights, for now** (operator: "đây là máy của tôi và
  tôi có thể control nên tạm thời tôi cho phép Bash được full quyền").
  The allowlist (`READ_COMMANDS`, `KUBECTL_VERBS`, `REFUSED_FLAGS`, secret
  paths) and its refusal tests are deleted. D6 is amended: the other tools
  stay reads; `bash` runs as the operator.
- **What a full shell still keeps:** a timeout with a process-group kill
  (120 s default, 600 s max), stdout then stderr, a 30k-char cap with the
  rest spilled to `save_to` in the workspace, `scrub` before the model sees
  output, output through `Evidence.show`, and **an audit row for every
  command run** — host, command, exit code, duration — not only refusals.
- **Known risk, accepted:** `backend.diagnose` reads data nobody vouches
  for (ticket 22's reason); an instruction-shaped log line can now reach a
  shell holding the operator's credentials.
- **SSH** (operator: "SSH ok"): `host` is a name from `config.yaml`
  `shell_hosts`; code runs `ssh -o BatchMode=yes -o ConnectTimeout=10 --
  <host> <command>`; an undeclared host is refused. An SSH `ControlMaster`
  (`ControlPath` in the task's workspace, `ControlPersist` 60 s) reuses one
  connection per run. The description says to use `host` rather than
  typing `ssh` in the command.
- **Declared explicitly, never from a docstring**, as in 23 (rule: 26).

## Goal

### `bash(command, host="local", timeout_ms=120000, save_to="")`

From Claude Code: exit-code meaning per command (grep/rg 1 = "no matches",
not an error); `sleep N` polling refused; description steers to `read`/
`grep`/`glob` for the room's repositories ("NEVER invoke grep as a Bash
command"). Granted to `backend.diagnose` (contract + ceiling).

### `todo_write(todos: [{content, status}])` — `core.todo`

`status` ∈ `pending | in_progress | completed`; the whole list is replaced
per call; **at most one `in_progress`, enforced in code** (Claude Code
leaves it to the prompt). The list belongs to the agent run and travels
with a stored `Ask` (beside `Evidence.dump/load`). Not grounded evidence,
never shown to the reporter. It is the agent's own plan of how to
investigate; the Planner's plan is unchanged (tickets 20, 22). Granted to
`backend.diagnose`. Description from Claude Code's: use it for 3+ steps,
mark `in_progress` before starting, `completed` only when fully done.

## Sensitive commands ask the operator (ticket 27)

Decided 2026-09-30: no deny list. A command in the sensitive set does not
run until the operator approves it — that flow is
[27](27-a-sensitive-bash-command-waits-for-the-operator.md). This ticket
ships `bash` with the hook 27 fills: a `sensitive(command, host)` check
that returns `False` for everything until 27 lands, and is called before a
command runs.

## Acceptance

- [ ] `bash` runs a command locally and on a declared SSH host, refuses an
      undeclared host, kills on timeout, spills over-cap output, and writes
      an audit row for every command (tests).
- [ ] `bash` calls the `sensitive` check before it runs a command (test).
- [ ] `todo_write` refuses two `in_progress`; the list survives an `Ask`
      continuation (tests).
- [ ] `backend.diagnose`, run through `run_agent`, is offered `bash` and
      `todo_write` (test).
- [ ] `bash` and `todo_write` definitions are identical with every
      `__doc__` set to `None` (test).
- [ ] Guard tests updated: `tests/test_tools.py`,
      `tests/test_core_toolsets.py` (the refusal tests go),
      `tests/test_sources_are_the_only_door.py`.
- [ ] `backend.trace_problem` eval and `core.planner` run and reported.
- [ ] `docs/DESIGN.md` D6 and the toolsets row, `CONTEXT.md`, ticket 08
      amended.
- [ ] Whole suite green; `code-review` done.
