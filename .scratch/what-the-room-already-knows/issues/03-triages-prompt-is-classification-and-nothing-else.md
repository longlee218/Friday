# 03: Triage's prompt is classification and nothing else

**What to build:** Triage stops carrying a catalogue of skills it has never
fetched. 1,008 of its 2,272 system-prompt characters describe four tools it does
not use, and wiring those tools silently tripled its turn budget — on the
highest-volume path in the system.

Sequenced before 09 on purpose, though nothing technically gates it: both
tickets change what reaches the classifier and both must run the evaluation, so
running them separately keeps one variable per measurement. If accuracy moves,
this ordering says which change moved it.

**Blocked by:** None (can start immediately)

**Decisions:** D2, D23

**Status:** done

- [x] Triage is built without the skill library, so it receives neither the
      catalogue section nor the four skill tools
- [x] Triage's turn budget is what its configuration states, without the
      addition the tool families bought
- [x] A test asserts the catalogue is absent from triage's instructions and
      fails if it comes back — no test pins this today, in either direction
- [x] No other agent loses its catalogue
- [x] Triage's tool schema is unchanged: a type and a confidence
- [x] The evaluation is run against the frozen set, and accuracy, the confusion
      matrix and the threshold table are reported alongside the change
- [x] The new guard is deleted once and watched go red

## Comments

**Measured, both sides, one variable.**

| | instructions | tools | `tool_turns` | run budget |
| --- | --- | --- | --- | --- |
| before | 2,272 chars | 6 | 2 | 4 |
| after | **1,262 chars** | **2** (`classify`, `skip`) | **0** | **2** |

Eval against MiniMax-M3 on the frozen 16-row set, **93.8% accuracy both
before and after**, byte-identical confusion matrix and threshold table:

```
                access_request  api_issue  doc_question  needs_human  skip
access_request  4               0          0             0            0
api_issue       0               4          0             0            0
doc_question    0               0          3             1            0
needs_human     0               0          0             0            0
skip            0               0          0             0            4

0.5: 1/16   0.6: 1/16   0.7: 1/16   0.8: 1/16   0.9: 2/16
```

Removing 44% of the prompt and half the turn budget costs nothing this set can
see. **Its resolution is 1/16 — 6.25% — so a smaller change would be
invisible**, and both runs failed on the same single `doc_question` row.

**The eval had never measured production, and finding that is how the "after"
number arrived before the change did.** `evals/run_triage_eval.py:85` calls
`build_triage(config, db=db)` with no `skills=`; production
(`friday/triage/runner.py`) always passed it. So from 2026-09-07 the regression
net scored a classifier 1,008 characters shorter than the real one — the
post-ticket-03 shape, two days early, by accident.

Ticket 06's own review split `build_triage` out of `TriageRunner.build`
precisely to stop the eval drifting from production, and named the risk: *"if
`TriageRunner.build` later changes what feeds the prompt, the eval's copy would
not follow, and the numbers this tool prints would silently stop representing
the live classifier."* Then `skills` was added to the signature and the eval's
caller never learned it. The guard was a refactor, and a refactor does not hold
a parameter.

So the fix here is not "stop passing skills to triage" but **remove the
parameter**: `Triage.__init__` and `build_triage` no longer accept one, and a
test asserts they cannot. A parameter that exists is a parameter something can
pass, and something did.

**The "before" number was taken by patching the eval to pass `skills=`,** which
is the only way to score the prompt production actually ran. That patch was
reverted; it exists in this comment and nowhere else.

**A guard that checked a proxy.** The first version of this test asserted on
`friday.triage.INSTRUCTIONS`, the module constant — which is
`build_instructions()` with no examples, and *not* the string
`Triage.__init__` hands the `Harness`. A mutation that appended a catalogue to
the real call passed it, and only *errored* on the mutation run rather than
failing, which is what gave it away. It asserts on
`Triage(config=...)._run.agent.instructions` now, as well as the constant.
Third time this session that an assertion turned out to be on a proxy rather
than on the thing.

**Three guards, each deleted once and watched go red:** the `skills` parameter
coming back, a catalogue smuggled into the real instructions, and the `skip`
tool dropped.

**Suite: 962 passed, 1 skipped.**
