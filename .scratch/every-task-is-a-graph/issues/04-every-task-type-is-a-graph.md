# 04: Every task type is a graph

**What to build:** `access_request` and `doc_question` run a graph too — one
node — so resume, checkpointing and hand-over behave the same whatever the
task is. The second way of deciding what to do with a task disappears, and
with it the exception a node had to raise to stop.

**Blocked by:** 03

**Decisions:** D1, D9

**Status:** ready-for-agent

## Why

There is no reason `access_request` has no resume other than the branch it
took. A one-node graph is what the deterministic path already is; calling it a
graph costs one registration and deletes a whole route through the loop —
the "is there a graph for this?" question, the `None` answer, the fallback
planner, and every test that exercised the fallback separately.

`PauseForHuman` goes at the same time. It exists so a node can stop and be
resumed at the node that stopped; ticket 03 replaced that with re-running from
node 1 when the params change, which reaches the same place through the
mechanism every task already uses. Leaving the exception beside its
replacement is how a codebase ends up with two of everything — which is what
this whole spec is repairing.

The principle it does not repeal: **do not build multi-node graphs before
there are steps worth skipping.** A one-node graph is that principle under one
name, not an argument against it.

## Acceptance criteria

- [ ] `access_request` and `doc_question` each run a one-node graph: prepare,
      then ask for what is missing or hand over
- [ ] The router never answers "no graph", and the loop has no branch for the
      absence of one
- [ ] A one-node type gets checkpointing, fingerprinting and hand-over on the
      same terms as `api_issue`, pinned by a test that runs one through
      `run_once` twice
- [ ] `PauseForHuman` is gone: a node that cannot decide returns an action,
      the run ends, and the task waits
- [ ] The specific question a node stopped on still reaches the operator's
      help-wanted message, worded as it is today
- [ ] New reporter text re-runs from node 1 under ticket 03's rule, reaching
      the same outcome the paused-node resume gave
- [ ] Every assembled prompt is byte-identical before and after, captured
