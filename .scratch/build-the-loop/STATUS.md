# build-the-loop — STATUS

**Check this one file for progress.** Updated as tickets move. Detail lives in
each ticket; this is the board at a glance.

Legend: ⬜ not started · 🔵 in progress · ✅ done · 🧑 waiting on operator

| # | Ticket | State | Blocked by |
| --- | --- | --- | --- |
| 1 | Superset capture + canned source | ✅ code (2 boxes pend) | — |
| 2 | Diagnose output: HandOver | ✅ done | — |
| 3 | The Intake node | ✅ done (wired in ticket 6) | — |
| 4 | Checkpoint/resume on `placement_identity` (resume half) | ⬜ **takeable** | 6 (done) |
| 5 | Remove FindRequestLog + ReadFailingCode | ✅ done | 3 |
| 6 | Rewire graph; drop extraction + unify Placement | ✅ done | 3, 5, 10 |
| 7 | Operator: capture ≥10 cases | 🧑 parked (needs launch) | 1 |
| 8 | Delete fixed-feed baseline | ⬜ code done, measure parked | 7 |
| 9 | Doc debts (CONTEXT/DESIGN/ADR) | ⬜ **takeable** | 6 (done) |
| 10 | `ask_reporter` terminal tool (Ask outcome, split from 4) | ✅ done | — |

## Now
- **Ticket 6 — done.** Graph rewired to `intake → acknowledge → diagnose →
  report`; `prepare`(extractor) + `resolve` removed for api_issue (shared
  `prepare_node`/`access_request` untouched). **`Placement` unified** into one
  `friday.sdk.sources.Placement` (superset; `intake.Placement` deleted, `project`
  dict folded in) — resolves the `Placement`×2 smell early. Fog item decided:
  `acknowledge` always runs (no external gate — the loop reads code/docs, not
  only logs), generic text when service is vague. `stack` hint restored to the
  diagnose prompt. Suite 1575 passed / 7 known env fails; code-review clean
  (2 Medium items were operator decisions, both resolved). Developer stalled
  twice on infra (cert, watchdog); finished on resume, final tweaks by hand.
- **Next takeable: ticket 9** (doc debts — CONTEXT/DESIGN/ADR for the new shape)
  and **ticket 4** (placement_identity resume — now that Intake is node 0).
- **Ticket 5 — done (option B).** Deleted `graph/code.py`, removed
  `find_request_log`/`read_failing_code` nodes + the dossier diagnose path + the
  `diagnose_reads` flag; **reads-mode `_reading` is now the only diagnose path**.
  Graph: `prepare → resolve → acknowledge → diagnose → report`. ~54 dossier tests
  removed; the seven `_judged` gates rewritten through reads-mode (code-review:
  no gate lost coverage). This also landed ticket 8's **code** half.
- **Ticket 8 — code done, measurement parked.** Flag/baseline path gone (ticket
  5); the eval showing agentic ≥ baseline needs real cases → parked with ticket 7.
  `Blocked by: 06, 07` → `07`.
- **Next takeable: ticket 6** (rewire: Intake as node 0, drop extraction) — its
  blockers 3/5/10 are all done. Intake (ticket 3) is built-not-wired; ticket 6
  wires it and removes `prepare`/the extractor.
- **Parked (need launch/data): ticket 7** (capture ≥10 real cases) and ticket 8's
  measurement. See [[build-the-loop-scoring-deferred]].
- **Ticket 10 — done (split from ticket 4).** `ask_reporter(question) -> Ask`
  terminal tool on the diagnose loop, mirroring ticket 2's `hand_over`; wired
  into `ends_with=[hand_over, ask_reporter]`. The loop can now end as
  `Diagnosis | Ask | HandOver`. code-review: clean mirror, no critical/high/medium.
- **Ticket 4 — split & deferred.** The `Ask` outcome shipped as ticket 10. The
  **resume half** (placement_identity checkpoint) now depends on ticket 6 (it
  resumes a loop/anchor not yet on the live graph), so `Blocked by: 06`.
- **Ticket 3 — done (built, NOT wired).** `Intake` — deterministic, no model —
  in `plugins/devops/graph/intake.py`: folds `Resolve`'s env/service table
  lookup + regex hints (uuid correlation_id, artifact ids) + `diagnose_memories`
  retrieval (memory/skills/findings) + `reported_at`, into one `IntakeContext`
  with a `placement_identity` staleness key. Service uses the decided (c)→(a)
  whole-token match (never a model guess; vague → room candidate set). NOT on
  the live edges — ticket 6 wires it and drops the extractor. `code-review`:
  no critical/high; M1 (substring→token service match) + L2 (storage round-trip
  test) fixed, both guards watched red. Suite: 1624 passed, 7 known env fails.
- **Ticket 2 — done.** `HandOver` via a `hand_over(reason)` terminal output tool
  on the diagnose reads loop; reaches the operator end-to-end with the model's
  reason (gated `diagnose→report` edge). New general Harness `ends_with` seam.
  Ask deferred to ticket 4. code-review found a SEVERE "passes tests, does
  nothing" bug (edge fall-through) + 3 more — all fixed & guarded.
- **Ticket 1 — code complete.** Superset canned source; 2 boxes pend (ticket 7
  case; repo-snapshot deferred).
- **Next takeable:** ticket 3 (Intake node) — unblocked. Ticket 4 now also
  carries the Ask outcome (build it with resume). Ticket 7 (operator cases)
  unblocked by ticket 1.

- 2026-09-27 — ticket 6 done. Rewire intake → acknowledge → diagnose → report
  (prepare+resolve dropped for api_issue); Placement unified into one
  sdk.sources.Placement; acknowledge always-on with generic text for vague/
  external; stack hint restored. Pool tests moved to access_request. Suite 1575
  passed, 1 skipped, 7 known env fails; code-review clean. Not committed.
- 2026-09-26 — ticket 5 done (option B). Deleted graph/code.py; removed the two
  fixed nodes + dossier path + diagnose_reads flag; reads-mode is the only
  diagnose path; report.py + config.py pruned. ~54 dossier tests removed, 7
  `_judged` gates rewritten through reads-mode + 1 guard watched-red. Suite: 1580
  passed, 1 skipped, 7 known env fails. code-review: clean, no gate lost.
  Landed ticket 8's code half (measurement parked). Not committed.
- 2026-09-26 — ticket 5 re-gated `03` → `03, 07` then un-gated back to `03`
  under option B (measurement deferred to post-launch; no baseline to protect
  pre-launch).
- 2026-09-26 — ticket 4 split. `ask_reporter` Ask terminal tool shipped as
  ticket 10 (mirror of ticket 2's `hand_over`; +3 tests; guard watched red;
  suite 1627 passed, 7 known env fails; code-review clean). Ticket 4 reduced to
  the resume half, re-blocked on ticket 6. Ticket 6 blockers 03,04,05 → 03,05,10.
  Not committed.
- 2026-09-26 — ticket 3 (Intake node) implemented (built, not wired). New
  `plugins/devops/graph/intake.py` + 11 tests in `tests/test_api_issue.py`.
  `code-review` found 1 medium (M1 substring service match) + 3 low; M1 + L2
  fixed, L1/L3 noted as ticket-6 follow-ups; two guards deleted-and-watched-red.
  Suite: 1624 passed, 1 skipped, 7 failed (all pre-existing `OPENROUTER_API_KEY`
  env, unrelated). Not committed.
- 2026-09-26 — boards committed on branch `plan/the-graph-becomes-a-loop`
  (`f6adb1d`).
- 2026-09-26 — ticket 1 implemented; `code-review` found 2 HIGH bugs (kubectl
  needle double-shell-quoting: crash on apostrophes, silent-empty on
  spaced needles); both fixed + guarded by a test verified red-without-fix.
  Suite green (1605 passed; the 7 failures are the pre-existing uncommitted
  `config.yaml`/`OPENROUTER_API_KEY` env issue, not this change). Not committed.
