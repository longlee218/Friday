Status: ready-for-agent
Blocked by: 24

# A sensitive bash command waits for the operator

Operator, 2026-09-30: "giả sử model thực hiện 1 lệnh nào đó nằm trong nhóm
sensitive, tôi muốn nó hỏi lại tôi, nếu okie thì được phép chạy tiếp".
Claude Code's "ask", for an agent that runs unattended: the command is not
refused and not run — it waits for the operator's yes.
Amends: [24](24-bash-with-full-rights-and-todo-write.md) (fills its
`sensitive` hook); build-the-spine ticket 14 (`deliver`) and 10
(`run_agent` outcomes); `docs/DESIGN.md` D6.

## What is decided (operator, 2026-09-30)

1. **Approved on the board** (web), as a card beside the reply approval
   cards — never in the reporter's room.
2. **24 hours without an answer → `hand_over`** (`approval_expired`): the
   logs and the running tag may have moved while it waited.
3. **No remembered approvals.** Every sensitive command is its own card;
   no "always allow", not even for the same command in the same task.
4. **Its own ticket**, after 24.

## The flow

```
diagnose calls bash("kubectl delete pod x", host="dev")
  → sensitive → the tool raises Pydantic AI's `ApprovalRequired`; nothing ran
  → the run ends with `DeferredToolRequests`
  → run_agent returns a new outcome: NeedsApproval(call_id, command, host,
      why_sensitive, history, evidence)
  → deliver: task → needs_approval; a board card: command, host, why,
      [Approve] [Reject] + a note; nothing is sent to the reporter
Approve → pass n+1 → the step continues from its history with
          DeferredToolResults(approvals={call_id: True}) → the command runs;
          its audit row names the approver
Reject  → approvals={call_id: False} (the note as the message)
          → the model reads "the operator denied: …" and changes course
no answer in 24 h → HandOver approval_expired
```

It rides the path an `Ask` already takes: the pass ends, no workflow waits
on a person (`workflow.py:14`), and the next pass continues the step from
its stored history (`Resume`). `NeedsApproval` is not an `Ask`: it goes to
the operator, and it does not count against `MAX_ASKS_PER_TASK`.

## What is sensitive

- **A default set in code**, from Claude Code's
  `destructiveCommandWarning.ts`: `rm` with `-r`/`-f`, `git push`,
  `git reset --hard`, `git clean -f`, `kubectl delete|apply|edit|scale|
  patch|rollout|drain|cordon`, `terraform destroy|apply`, `DROP|TRUNCATE`,
  `DELETE FROM` without `WHERE`, `shutdown`/`reboot`.
- **Plus `bash.ask` in `config.yaml`**, rules in Claude Code's syntax
  (`git push:*`, exact commands).
- **Matched as Claude Code matches:** the whole command and each
  subcommand (`&&`, `;`, `||`, `|`); leading env vars and wrappers
  (`timeout`, `nice`, `nohup`, `env`) stripped; for an SSH `host`, the
  command that runs there.
- **What cannot be read is sensitive:** `$(…)`, backticks, `eval`,
  `bash -c`/`sh -c`, `| sh`/`| bash`, `base64 -d` into a shell — Claude
  Code's "too complex" → ask. Otherwise a log line that injects a command
  only has to wrap it to pass.
- The reason ("matches `kubectl delete`", "cannot be parsed: `$(`") is on
  the card and in the audit row.

## Acceptance

- [ ] A sensitive command does not run; the run ends `NeedsApproval`; the
      task goes to `needs_approval` with a board card; the reporter gets
      nothing (tests).
- [ ] Approve runs exactly that call once and continues the step from its
      history; the audit row names the approver (test).
- [ ] Reject hands the model the note and the step continues (test).
- [ ] 24 h without an answer → `HandOver approval_expired` (test).
- [ ] Two sensitive commands in one task make two cards (no remembered
      approval) (test).
- [ ] The default set, `bash.ask` rules, subcommand splitting, env/wrapper
      stripping and "cannot be read → ask" each have a case (tests).
- [ ] The board shows the card and its two buttons (`web/`), built.
- [ ] `docs/DESIGN.md` D6 and the spine outcomes, `CONTEXT.md`
      (*NeedsApproval*, *sensitive command*) amended.
- [ ] Whole suite green; `code-review` done.
