# Build the spine — execution board

Executes the design locked by the decision map
[Domains plug in without touching the core](../domains-plug-in/map.md).
This board does not re-decide anything: every ticket cites the map ticket(s)
it builds. Where a map ticket was amended later (by 16 or 17), the amendment
wins — read the ticket's `## Amended` section, and `map.md` § Decisions so
far for the latest one-line state.

## Target shape

```
Inbox → Triage (assembled) → Task → Pool
  → pass task-<id>/pass-<n> (DBOS):
      Intake → acknowledge (once) → plan (Planner + GatePlan) → run → [retriage] → deliver
  → Approval → Outbox
```

A plugin is a domain (`plugins/backend`, `plugins/ops`); it registers
actions (intent + contract), agents (`AgentSpec`) and toolsets
(`ToolsetSpec`). The core runs every task on one spine. Initial catalog:
`backend.trace_problem`, `backend.answer_question`, `ops.request_permission`.

## Ground rules

- **Suite green at every ticket** (`uv run pytest -q`, whole suite) and a
  `code-review` subagent per change — `CLAUDE.md` § Verifying a change.
- **Every guard is watched red once**: delete it, see the test fail, restore.
- **The grounding gate is inviolable**: `Lnn` refs resolve against what was
  read, or the answer is void.
- **A change to `friday/kernel/triage/prompt.py` or upstream** runs
  `uv run python -m evals.run_triage_eval` and reports accuracy, confusion
  matrix and threshold table (tickets 02, 13, 17).
- **Docs move with the code**: the commit that changes a load-bearing rule
  corrects `docs/DESIGN.md`; each new term goes to `CONTEXT.md` § Vocabulary
  in the ticket that names it in code.
- **Old and new may coexist only until ticket 16.** Tickets 05–12 build the
  new pieces beside the DAG path; 14 moves `trace_problem` onto the spine;
  16 deletes the DAG path. Nothing temporary survives 16.
- **Code in a separate git worktree**, and ask the operator before starting a
  ticket (a `ready-for-agent` ticket is not a go).

## Sequence

| # | Ticket | Builds map ticket(s) | Blocked by |
| --- | --- | --- | --- |
| 01 | Knobs to constants, budget in three groups | 07, 17 | — |
| 02 | Rename actions and plugins, wipe the db | 08 | 01 |
| 03 | Split the grab-bag modules | 09 §4–7, §10 | 02 |
| 04 | Board card and pool line from the opening message | 05 (board, pool) | 02 |
| 05 | SDK declarations and boot refusals | 03, 02 §5, 12 §2 | 03 |
| 06 | Plan, `step_key` and GatePlan | 10, 11 | 05 |
| 07 | Core Intake and the backend enricher | 04 | 05 |
| 08 | Core toolsets: memory, skills, shell, workspace | 03 amend §3–4 | 05 |
| 09 | Backend toolsets from `sources/` | 09 §3, 03 §2, 04 §6 | 05, 07 |
| 10 | The Harness runs an `AgentSpec` | 03 §3–4, 13 §1, 16 §1, 17 | 05 |
| 11 | The Planner and its plan-shape eval | 12 | 06, 10 |
| 12 | The WorkflowRunner and `step_results` | 13, 09 §8 | 06, 10 |
| 13 | The assembled triage prompt | 02 | 05 |
| 14 | The spine pass; `trace_problem` moves onto it | 14, 15, 05 (responder) | 07, 08, 09, 11, 12, 13 |
| 15 | `backend.answer_question` on the spine | 06 | 14 |
| 16 | `ops.request_permission` on the spine; delete the DAG path | 05, 09 §2, 01 (deletions) | 14, 15 |
| 17 | Re-triage | 16 | 16 |
| 18 | The board shows plans and re-triage | 11 §6, 16 §9 | 17 |
| 19 | Doc sweep | all | 18 |

Parallel lanes after 05: {06, 07, 08, 10, 13} are independent of each other.

## Sequencing notes (not re-decisions)

- **The rename goes first (02), on the old machinery.** Ticket 08 decided
  "all names at once, wipe, no alias"; doing it before the spine means every
  later ticket writes straight into `plugins/backend` / `plugins/ops` and no
  file moves twice.
- **`tasks.params` is dropped in 16, not with the rename.** Ticket 08 carried
  the drop "with the rename migration"; the params readers only disappear
  with the extractor, so the schema-only migration lands there. The wipe
  still happens once, in 02.
- **Guards** (map § Not yet specified: "structural tests once the API shape
  lands") are carried per ticket: each ticket's acceptance names its guard.
- **Over-200-line files** (`kernel/ops/api.py` 1108, `instruction_prompt.py`
  868, `config.py`, `store/schema.py`, `store/_common.py`) split in the
  ticket that touches them (map ticket 09 §9), not in a ticket of their own.

## Carried in

- `build-the-loop` ticket 04's acceptance (resume on unchanged placement,
  `Lnn` stable, reads not repeated; changed placement re-investigates) → 14.
- `harness-auto-compaction` stays parked until 19 is done; its seam
  (`harness.py`) is split by 03.
