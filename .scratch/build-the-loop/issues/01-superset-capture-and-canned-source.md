Status: ready-for-agent
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

- [ ] An existing captured case replays with the model choosing needles/windows
      not in the original capture, and scores.
- [ ] Out-of-superset read returns the honest answer, not an error.
- [ ] `read_code` / `what_code_means` resolve offline from the case.
- [ ] Whole suite green; `code-review` done.
