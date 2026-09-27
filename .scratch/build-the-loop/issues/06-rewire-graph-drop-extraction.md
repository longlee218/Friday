Status: done
Blocked by: 03, 05, 10

<!-- Was `03, 04, 05`. Ticket 04 split (2026-09-26): its `ask_reporter` half
became ticket 10 (a rewire prerequisite — the loop's Ask terminal tool); its
resume half now depends on THIS ticket, so 04 no longer blocks 06. -->


# Rewire the graph; drop extraction/prepare for api_issue

Decisions: charting Q1 (drop extraction), the target shape.

## Goal

Assemble the final shape and remove node 0 for `api_issue`:

```
Intake → [Acknowledge] → Diagnose (loop) → Report
```

- Replace `prepare`/extraction with `Intake` as node 0 (the fresh-each-pass
  anchor). **Only for `api_issue`** — the shared `prepare_node` and other task
  types are untouched.
- Keep `Acknowledge` as a standalone unapproved send; confirm its trigger/text
  under the new shape (fog item — decide here).
- Confirm `Report` is unchanged (still a `Reply` that waits approval) or record
  the minimal change.

## Acceptance

- [x] `api_issue` runs the four-stage shape end to end. Live-verified shape
      `intake → acknowledge → diagnose → report`; the e2e reads-mode test
      `test_the_whole_line_runs_from_a_curl_to_a_report` drives it through a real
      `read_log` call. (`run_api_issue_eval` replay-with-model not run — option B:
      no model key/data; parked with ticket 7.)
- [x] No extractor/`prepare` on the `api_issue` path; other task types
      unchanged. (`prepare`/`resolve` removed for api_issue; the shared
      `prepare_node` + `access_request` untouched — pool tests moved onto
      `access_request` stay green.)
- [x] `Acknowledge` still precedes the slow part, no approval. (edge
      `intake →(ok) acknowledge`; `Kind.ACKNOWLEDGED`, unapproved, once per task.)
- [x] Whole suite green; `code-review` done. (`uv run pytest -q`: 1575 passed,
      1 skipped, 7 pre-existing `OPENROUTER_API_KEY` env failures unrelated.
      code-review: no critical/high; the two Medium items it raised were operator
      decisions, both resolved below.)

## Decisions made here

- **Fog item — Acknowledge trigger/text:** keep `acknowledge` **always** after
  `intake` (no gate on `env=="external"`). The operator's reasoning: input is
  not always a curl, and a case with no matching Apero log environment is still
  investigable — the loop reads **code and docs**, not only logs. `ack_text`
  names the service when Intake resolved one; otherwise a generic "đang xem lại
  vụ này" (never "log của external"). `Report` unchanged (still a `Reply` that
  waits approval).
- **Placement unified (option i):** one `friday.sdk.sources.Placement` (superset
  of fields), `intake.Placement` deleted, `project` dict folded in,
  `investigate_tools` dropped `project`/`release_tag`/`container_roots` (reads
  them off placement). This also resolves the `Placement`×2 smell early — one
  fewer item for the refactor board.
- **`stack` restored:** the unification initially dropped `ProjectData.stack`
  from the diagnose prompt; re-added to `Placement` + `build_reads_input` (a
  framework hint the model used to read a trace in-idiom).

## Follow-ups (out of scope, noted)

- `replay_case.py` dev CLI still seeds `{"prepare": ...}` (harmless, unused key);
  `friday/kernel/dag/adapter.py` docstring has a stale `resolve`/`prepare`
  example (kernel, out of a devops-scoped ticket). Tidy in a later pass.
