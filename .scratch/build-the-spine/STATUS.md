# build-the-spine — STATUS

**Check this one file for progress.** Detail lives in each ticket.

Legend: ⬜ not started · 🔵 in progress · ✅ done · 🧑 waiting on operator

| # | Ticket | State | Blocked by |
| --- | --- | --- | --- |
| 01 | Knobs to constants, budget in three groups | 🧑 built, awaiting operator review/commit (`feat/knobs-to-constants`) | — |
| 02 | Rename actions and plugins, wipe the db | ⬜ | 01 |
| 03 | Split the grab-bag modules | ⬜ | 02 |
| 04 | Board card and pool line from the opening message | ⬜ | 02 |
| 05 | SDK declarations and boot refusals | ⬜ | 03 |
| 06 | Plan, `step_key` and GatePlan | ⬜ | 05 |
| 07 | Core Intake and the backend enricher | ⬜ | 05 |
| 08 | Core toolsets: memory, skills, shell, workspace | ⬜ | 05 |
| 09 | Backend toolsets from `sources/` | ⬜ | 05, 07 |
| 10 | The Harness runs an `AgentSpec` | ⬜ | 05 |
| 11 | The Planner and its plan-shape eval | ⬜ | 06, 10 |
| 12 | The WorkflowRunner and `step_results` | ⬜ | 06, 10 |
| 13 | The assembled triage prompt | ⬜ | 05 |
| 14 | The spine pass; `trace_problem` moves onto it | ⬜ | 07, 08, 09, 11, 12, 13 |
| 15 | `backend.answer_question` on the spine | ⬜ | 14 |
| 16 | `ops.request_permission` on the spine; delete the DAG path | ⬜ | 14, 15 |
| 17 | Re-triage | ⬜ | 16 |
| 18 | The board shows plans and re-triage | ⬜ | 17 |
| 19 | Doc sweep | ⬜ | 18 |

Operator steps: the db wipe in 02 (asked first), the eval relabel
confirmations in 13, one real end-to-end run in 14.
