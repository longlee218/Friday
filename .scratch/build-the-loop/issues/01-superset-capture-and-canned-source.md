Status: code-complete — end-to-end (box 1) pends ticket 7; repo-snapshot (box 3) descoped
Blocked by:

# Superset capture + canned source (the eval foundation)

Decision: [A deterministic eval for a loop that reads what it likes](../../the-graph-becomes-a-loop/issues/02-a-deterministic-eval-for-a-loop-that-reads-what-it-likes.md).
Lands first — the safety net that lets later loop changes be measured.

## Goal

Make an offline captured case serve **arbitrary** reads, so an agentic loop
replays faithfully.

- Redefine a case's `reads` to hold the **raw incident-window superset**, not the
  single `{window, narrowed}` pair. `CannedReads` / `canned_source`
  (`replay_case.py`) filter the superset in-process; real `LokiSource` narrowing
  + `distil` run live.
- Out-of-superset reads return the truthful out-of-reach / not-found answer
  (reuse existing branches); window capped to the captured span.
- Capture a repo snapshot at the release tag (paths under `container_roots`) +
  the error-code table, so `read_code` / `what_code_means` serve offline.
- Keep `evals/api_issue.py` + `scoring.py` unchanged (substring on
  `cause_mentions`, no judge).

## Acceptance

- [ ] A captured case replays with the model choosing needles/windows not in
      the original capture, and scores. **Mechanism landed + unit-tested**
      (`CannedReads`/`CannedKubectl` filter a superset by needle∩window∩limit).
      End-to-end on a real case **pends a superset-format case** (operator work,
      ticket 7) — the one existing case is legacy `{window, narrowed}` and keeps
      the legacy path.
- [x] Out-of-superset read returns the honest answer, not an error — a needle
      not present / a line outside the window comes back empty (the loop's
      existing not-found / out-of-reach branches handle the message).
- [ ] `read_code` / `what_code_means` resolve offline from the case —
      **DEFERRED by scope decision**: they already replay offline against the
      clone at the release tag (deterministic); embedding a repo snapshot in the
      case is a follow-on only if that proves insufficient.
- [x] Whole suite green; `code-review` done — suite green (1605 passed; 191 of
      the config-dependent tests pass with `OPENROUTER_API_KEY` set), the only
      failures being the pre-existing uncommitted `config.yaml` env issue.
      `code-review` subagent ran; both HIGH findings (double-shell-quoting in
      the kubectl needle) fixed and guarded by a test verified red-without-fix.

## Result

Additive `reads["superset"]` in `replay_case.py`; legacy cases byte-for-byte
unchanged. Code complete and reviewed. Two boxes intentionally open: end-to-end
replay pends a superset case (ticket 7); repo-snapshot deferred by decision.
Not committed (operator controls commits).
