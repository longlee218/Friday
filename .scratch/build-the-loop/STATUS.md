# build-the-loop — STATUS

**Check this one file for progress.** Updated as tickets move. Detail lives in
each ticket; this is the board at a glance.

Legend: ⬜ not started · 🔵 in progress · ✅ done · 🧑 waiting on operator

| # | Ticket | State | Blocked by |
| --- | --- | --- | --- |
| 1 | Superset capture + canned source | ✅ code (2 boxes pend) | — |
| 2 | Diagnose output `Diagnosis\|Ask\|HandOver` | ⬜ | — |
| 3 | The Intake node | ⬜ | — |
| 4 | Checkpoint/resume on `placement_identity` | ⬜ | 2, 3 |
| 5 | Remove FindRequestLog + ReadFailingCode | ⬜ | 3 |
| 6 | Rewire graph; drop extraction | ⬜ | 3, 4, 5 |
| 7 | Operator: capture ≥10 cases | 🧑 | 1 |
| 8 | Delete fixed-feed baseline | ⬜ | 6, 7 |
| 9 | Doc debts (CONTEXT/DESIGN/ADR) | ⬜ | 6 |

## Now
- **Ticket 1 — code complete & reviewed.** Additive `reads["superset"]` in
  `replay_case.py`'s canned sources filters the whole captured window by
  needle/window/limit the way Loki/kubectl do; legacy `{window, narrowed}`
  untouched. +4 tests. Two acceptance boxes intentionally open: end-to-end
  replay pends a superset-format case (**ticket 7**); repo-snapshot **deferred**
  (read_code already replays offline against the clone at the release tag).
- **Next takeable:** ticket 2 (loop output types) or ticket 3 (Intake node) —
  both unblocked, parallel. Ticket 7 (operator cases) unblocked by ticket 1.

## Log
- 2026-09-26 — boards committed on branch `plan/the-graph-becomes-a-loop`
  (`f6adb1d`).
- 2026-09-26 — ticket 1 implemented; `code-review` found 2 HIGH bugs (kubectl
  needle double-shell-quoting: crash on apostrophes, silent-empty on
  spaced needles); both fixed + guarded by a test verified red-without-fix.
  Suite green (1605 passed; the 7 failures are the pre-existing uncommitted
  `config.yaml`/`OPENROUTER_API_KEY` env issue, not this change). Not committed.
