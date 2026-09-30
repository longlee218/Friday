# build-the-spine — STATUS

**Check this one file for progress.** Detail lives in each ticket.

Legend: ⬜ not started · 🔵 in progress · ✅ done · 🧑 waiting on operator

| # | Ticket | State | Blocked by |
| --- | --- | --- | --- |
| 01 | Knobs to constants, budget in three groups | ✅ done (main `aa0dd9d`, `2a98a2d`) | — |
| 02 | Rename actions and plugins, wipe the db | ✅ done (branch `feat/build-the-spine-02`, `cbbcde1`); its triage eval superseded by the 2026-09-29 runs (see 13) | 01 |
| 03 | Split the grab-bag modules | ✅ done (main, 2026-09-28) | 02 |
| 04 | Board card and pool line from the opening message | ⬜ **takeable** | 02 |
| 05 | SDK declarations and boot refusals | ✅ done (main, 2026-09-28); refusal 9 moved to 08 | 03 |
| 06 | Plan, `step_key` and GatePlan | ✅ done (main, 2026-09-28); `sdk/actions.py` file move deferred to 16 | 05 |
| 07 | Core Intake and the backend enricher | ✅ done (main, 2026-09-29) | 05 |
| 08 | Core toolsets: memory, skills, shell, workspace | ✅ done (main, 2026-09-29); workspace has no delete (library lacks one) | 05 |
| 09 | Backend toolsets from `sources/` | ✅ done (main, 2026-09-29); the `backend.trace_problem` eval (was `run_api_issue_eval`) not run yet — the key has credits now, 1 captured case in `data/cases/` | 05, 07 |
| 10 | The Harness runs an `AgentSpec` | ✅ done (main, 2026-09-29) | 05 |
| 11 | The Planner and its plan-shape eval | ✅ done (main, 2026-09-29); `core.planner` 6/8 on `strong` (glm-5.3-flash) | 06, 10 |
| 12 | The WorkflowRunner and `step_results` | ✅ done (main, 2026-09-29) | 06, 10 |
| 13 | The assembled triage prompt | ✅ done (main, 2026-09-29); `core.triage` measured 2026-09-29: deepseek 34/35, `trace_problem` subset qwen 21/23 | 05 |
| 14 | The spine pass; `trace_problem` moves onto it | 🧑 code in (main, 2026-09-30); waiting on the operator: the paid `backend.trace_problem` eval, one real end-to-end run, a read of the rendered diagnose prompt | 07, 08, 09, 11, 12, 13 |
| 15 | `backend.answer_question` on the spine | ⬜ | 14, 23, 24, 25 |
| 16 | `ops.request_permission` on the spine; delete the DAG path | ⬜ | 14, 15 |
| 17 | Re-triage | ⬜ | 16 |
| 18 | The board shows plans and re-triage | ⬜ | 17 |
| 19 | Doc sweep | ⬜ | 18 |
| 20 | The Planner hands over goals, not methods | 🧑 code in (main, 2026-09-30); `core.planner` 6/8 after 21 but one case worse (`question-about-the-docs` planner_failed) — a second run to settle it | 14 |
| 21 | The Planner reads the reporter through the same boundary | ✅ done (main, 2026-09-30); extended to every agent's prompt; `core.planner` 6/8 | 20 |
| 22 | The grant is code, not the Planner; memory writes are their own toolset | ✅ done (main `7334d70`, 2026-09-30); `core.planner` 7/8 and 8/8 | — |
| 23 | `core.repos`: read, grep, glob at the running tag | ⬜ **takeable** (opened 2026-09-30, inserted before 15) | — |
| 24 | `bash` with full rights, and `todo_write` | ⬜ after 23 (SSH decided; sensitive commands → 27, no deny list) | 23 |
| 25 | Log tools named for their source (`loki_query_range`, `ssh_read_log`), MCP args 1:1, one output | ⬜ **takeable** | — |
| 26 | Nothing a model reads comes from a docstring (backlog) | ⬜ after 23 — tool, terminal-tool and answer-schema descriptions declared explicitly; `__doc__ = None` guard | 23 |
| 27 | A sensitive bash command waits for the operator | ⬜ after 24 — board card, 24 h → hand_over, no remembered approvals | 24 |
| 28 | Diagnose reads Kubernetes and Loki through `devops-generic`, under the MCP's own names | ⬜ after 25 — `loki_pod_logs`, `loki_query`, `k8s_*`, MCP args 1:1 (no scope check); `ProdPlacement.k8s_cluster` for hints | 25 |

Operator steps: the db wipe in 02 (asked first), the eval relabel
confirmations in 13, one real end-to-end run in 14.
