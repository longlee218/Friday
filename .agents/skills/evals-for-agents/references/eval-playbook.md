# Eval-building playbook

## Step by step: building a first eval set for a capability that has none

1. **Mine 20–50 tasks from real failures**, not imagined edge cases. Sources: user/operator reports, existing manual QA checks, incidents. Prioritize by impact — a rare failure mode that never mattered to anyone isn't where the first eval set should go.
2. **Write a human-authored reference solution for each task** before writing any grading logic. If a competent human (or domain expert) can't produce a correct answer for the task, the task is broken, not the system under test.
3. **Get a second domain expert to independently mark each task pass/fail** on the reference solution. If they disagree with each other, the task's success criteria are ambiguous — fix the task before using it to judge anything.
4. **Balance the set**: include cases where the behavior should occur and cases where it deliberately shouldn't, so the suite isn't silently only testing one direction (e.g., only "should classify as X," never "should correctly refuse to classify as X").
5. **Pick the grader for each task independently** — don't default to one grader type for the whole suite. A task with a checkable structured output (a specific field value, a specific tool called) gets a code-based grader; a task judging open-ended prose quality gets an LLM-as-judge; anything load-bearing enough to matter gets occasional human spot-checks even after the other two are in place.
6. **Run multiple trials per task**, not one — outputs vary, and a single run can't tell you whether a pass or fail was representative.
7. **Read transcripts, not just scores**, especially for failures. Ask "would this seem like a fair failure to someone reading this cold?" before trusting the number.

## LLM-as-judge calibration checklist

- [ ] Does each judge call grade exactly one dimension, not several at once?
- [ ] Has the judge's verdict been checked against human experts on a sample, and do they agree closely enough to trust it unsupervised going forward?
- [ ] Does the judge have an explicit instruction to say "Unknown" or equivalent when it lacks enough information, rather than being forced into a binary verdict it has to guess at?
- [ ] Is there a periodic (not per-run) human spot-check to catch calibration drift over time?

## Grader-type decision table

| Situation | Grader |
|---|---|
| A specific field/value/tool call is either present or it isn't | Code-based |
| Output quality is open-ended or freeform (prose tone, reasoning soundness) | LLM-as-judge, calibrated against humans |
| The eval is load-bearing enough that periodic ground-truth validation matters, regardless of the primary grader | Add human spot-checks on a sample, not every run |

## Suite lifecycle

```
new capability, no eval yet
        │
        ▼
capability eval: hard tasks, deliberately low pass rate
        │  (agent improves against it)
        ▼
tasks "graduate" into a regression suite: expected to stay near 100%
        │
        ▼
watch for saturation (100% pass, no signal left)
        │
        ▼
author harder tasks before saturation hides a real regression
```

Treat the suite itself as an owned, living artifact — assign it clear ownership (ideally people who aren't the ones building the feature being evaluated), and prefer writing the eval for a capability *before* the agent can do it ("eval-driven development") over writing evals reactively after something breaks in production.

## Ground truth authority — the throughline

The source doesn't use the phrase "model-graded labels" directly, but its examples consistently route pass/fail judgment and reference solutions through people "closest to product requirements and users" — domain experts, PMs, customer-success staff — never through the system under test grading itself. Any eval whose ground truth was produced by the same model (or a sibling of it) being evaluated has a structural credibility problem regardless of how good the accuracy number looks, because a model graded against its own priors measures agreement with itself, not correctness.
