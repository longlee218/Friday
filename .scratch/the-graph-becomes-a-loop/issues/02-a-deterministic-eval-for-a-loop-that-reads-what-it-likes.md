Type: grilling
Status: resolved
Blocked by:

# A deterministic eval for a loop that reads what it likes

## Question

The current eval (ticket 14) **freezes evidence** and re-runs the `Diagnose`
model-call alone — that is what makes it measurable. Once `Diagnose` **chooses
its own reads** (the agentic loop, non-deterministic, hitting live sources),
that frozen-evidence eval no longer maps, and `replay_case.py` can no longer
replay a run whose tool calls touch live back ends.

Design the replacement:

1. **Record** every tool-call + response of a run into a **cassette** (the
   `read_log` / `read_code` / `what_code_means` calls and what they returned).
2. **Replay** the loop deterministically against the cassette — no live source,
   same reads, same order.
3. **Score** cause + `refs` on the resulting transcript (the grounding gate
   still applies: a ref must resolve).
4. **Plug in**: how this sits alongside `evals/run_triage_eval.py` and
   `replay_case.py`, and how it satisfies the `CLAUDE.md` rule-4 gate (a
   behaviour change is measured before it counts).

Deliver the **decided eval design** (a short spec + a stub to react to is
enough; this is plan-not-build).

**This gates** the "delete fixed-feed baseline" decision: the baseline
(`diagnose_reads=False`) may be deleted only after the cassette-eval proves the
agentic loop is **≥ baseline on the 5 frozen cases**. See `evals/README.md`.

## Answer

Decided 2026-09-24 (grilling). Most of the infra already exists —
`evals/api_issue.py` scores the `diagnosis` **output** and is agnostic to how
the reads happened; `data/cases/*.json` + `replay_case.py --case` run offline.
Domain: the repo's term is **captured case / canned source**, not "cassette".

The one thing that breaks under agentic reads is the canned source: today
`CannedReads` holds only the `{window, narrowed}` pair for a **single captured
needle** (`replay_case.py`, "a graph that widens sees the same lines again"), so
an agentic loop trying a different needle gets the old lines filtered by the new
needle → wrong/empty.

1. **Capture the superset (Q1).** Redefine a captured case's `reads` to hold the
   **raw log lines for the whole incident window** (the superset any `read_log`
   can filter), not the single pair. The canned source filters the superset
   in-process by the model's needle/window; the real `LokiSource` narrowing +
   `distil` run live. Any needle/window within the captured window is served
   faithfully.
2. **Out-of-superset policy (Q2).** A read beyond the captured window, or a
   needle not present, returns the **truthful** answer ("the log does not reach
   back that far" / "no line carries X"), reusing the existing out-of-reach /
   not-found branches — never an error. The window is capped to the captured
   span.
3. **`read_code` / `what_code_means` offline (Q3).** Capture a **repo snapshot
   at the release tag** (paths under `container_roots`) + the **error-code
   table**, so those tools serve offline too. Heavier capture, acceptable
   (`data/cases/` is gitignored).
4. **Scoring unchanged (Q4).** Deterministic substring on `cause_mentions`, **no
   judge model** (spec rule); `refs` kept as count + `grounded`, not matched to
   ground-truth refs. `evals/api_issue.py` does not change.
5. **The real gate to delete the fixed-feed baseline is case count, not the
   mechanism (Q5).** With 1 case it is a regression check, not a score. Deleting
   the baseline is gated on (a) superset-capture working AND (b) ≥ ~10 captured
   cases with agentic tying/beating baseline on all. Capturing cases is
   **operator work** (Friday proposes the four labels, operator confirms).

**Build consequences:** change the capture format + `CannedReads` /
`canned_source` to superset-filtering; keep `evals/api_issue.py` and
`scoring.py` as-is; baseline deletion waits on the operator's case set.

**On the task ticket I offered:** on reflection, capturing cases is *execution*
that unblocks a build action, not a decision on this map, so it belongs to the
**build/migration board** (map's Not yet specified), not a decision ticket here.

