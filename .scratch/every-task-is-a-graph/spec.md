# Spec: every task is a graph

Status: ready-for-agent. Design rationale was reached in one grilling session on
2026-09-02 and is recorded here under Implementation Decisions; nothing below
is still open.

## Problem Statement

I built a task-processing system and then found two things in it that do the
same job under different names. `friday/workflows/` and `friday/dag/` both
"decide what to do with a task", they import each other, and I could not say
what the first one was *for*. When I look for how a task actually moves — is it
created, is it run, who is allowed to speak — the answer is spread across a
loop, a gate, a fallback and a graph engine, and the fallback turns out to be a
graph with one node that nobody called a graph.

Meanwhile the agents inside the graph report their conclusions by returning
strings and JSON that code then parses — `CANNOT FIX`, `{"cause": ...}` — while
triage already reports its conclusion by calling a tool, which is the shape that
does not need parsing. And the one dangerous action a node can take, applying a
code patch, is guarded by a word list and by being left unconfigured.

## Solution

One engine. Every task type runs a graph; the trivial types run a one-node
graph. The first node of every graph is the same — read everything the reporter
said, fill in the fields, check them — so the graph always starts from stable
input. A loop pulls pending tasks and hosts their graphs; it owns the task's
lifecycle and nothing else. Agents inside a node report conclusions by calling
tools — *ask for this*, *answer with this*, *hand this to the operator* — and
code turns the call into the next step. The graph's shape stays code; a model
never chooses the next node.

Dangerous actions get a real gate: a patch stops the run until the operator
approves, and the run resumes where it stopped, in this process or the next.
Everything a person reads under the operator's name still comes from the one
family of agents built to write in their voice.

## User Stories

1. As the operator, I want every task type to run through the same engine, so that resume, checkpointing and pausing behave the same whether the task is an API issue or an access request.
2. As the operator, I want a type without an investigation to still be a graph — one node, ask or hand over — so that adding a real investigation later is adding nodes, not switching mechanisms.
3. As a reporter, I want the system to read everything I said, including the message I sent three seconds after the first one, before it decides what it knows.
4. As a reporter, I want to be asked for a missing detail once, in a sentence a person would write, not in a template.
5. As a reporter, I want the follow-up I send after being asked to reach the same task that asked, and to change what the system does next.
6. As a reporter, I want a "cảm ơn anh" after the investigation not to trigger the investigation again.
7. As a reporter, I want a new correlationId to make the system re-investigate, because the old conclusion was about a request without one.
8. As the operator, I want the graph to survive a restart mid-investigation without redoing the expensive steps.
9. As the operator, I want a code patch never to be applied without my approval, and I want approving it to resume the run rather than restart it.
10. As the operator, I want to approve a patch in a later session, possibly after a redeploy, and have it still apply.
11. As the operator, I want a reply to a reporter to keep waiting for my approval at the outbox exactly as it does today; nothing about that gate moves.
12. As the operator, I want the agent to hand a task to me with the reason it stopped, in the agent's own finding, when it cannot proceed.
13. As the operator, I want the extractor to decide it needs to ask — it just read the whole thread — but I want a malformed correlationId refused by code even if the model did not ask about it.
14. As the operator, I want the question the extractor decided to ask to be written by the agent that knows my voice, my room's register and how to address a stranger, so that tickets 36, 40 and 41 keep applying to it.
15. As the operator, I want only the responder-family agents to be able to produce text that reaches a reporter, and I want a test that says so.
16. As the operator, I want triage to create a task by naming its type through one tool, and to describe each type from the same place the type's fields are defined, so adding a type is one class.
17. As the operator, I want `skip` to stay a separate tool, because a tool called `create_task` that creates nothing is lying in its name.
18. As the operator, I want no model ever to decide the next node of a graph, because this system has already watched one write "ok có correlationId rồi" when there was none.
19. As the operator, I want no model to be able to spawn a persistent task from inside a graph.
20. As the operator, I want task execution to stay automatic — reading logs needs no approval — with approval only where the risk is: acting and answering.
21. As a maintainer, I want the action vocabulary (`Ask`, `Reply`, `Park`) in the domain package, so that the graph engine and the loop no longer import each other.
22. As a maintainer, I want the module that registers graphs and builds their agents to be one file, next to the lookup that routes to them.
23. As a maintainer, I want the loop named for what it is — a pool engine pulling tasks — and living in a package that does not collide with the domain module of the same name.
24. As a maintainer, I want the checkpoint's meaning to be stated in one sentence and enforced by tests: the first node is never checkpointed; the rest are checkpointed against its output.
25. As a maintainer, I want every assembled prompt to be byte-identical before and after each refactoring ticket, captured rather than eyeballed, because a refactor that changes a prompt is a behaviour change wearing a refactor's name.
26. As a maintainer, I want `PauseForHuman` gone once nothing needs it, rather than left beside the mechanism that replaced it.
27. As a maintainer, I want CLAUDE.md and CONTEXT.md to describe the result in the same commit that produces it, because those two files go stale silently.

## Implementation Decisions

Numbered so tickets can reference them as D1…D18.

### Structure

- **D1. Every task type is a graph.** `access_request` and `doc_question` get a one-node graph. The router never returns "no graph"; the fallback path and its planner function dissolve. The existing principle "a type without a graph is not a mistake" survives as "do not build multi-node graphs before there are steps worth skipping" — a one-node graph is the same thing under one name. What it buys: checkpoint, fingerprint and hand-over apply uniformly; today `access_request` has no resume for no reason but the branch it took.
- **D2. Prepare — extract and validate — is node 0 of every graph.** One shared node, the entrypoint. Chosen by the operator over keeping it as an outside gate; D7 is what makes it work.
- **D3. `Ask`, `Reply` and `Park` move to the domain package.** They are vocabulary — what a decision about a task results in — and both the engine and the loop import the domain. This ends the import cycle between them.
- **D4. Graph registration merges into the router module.** Register, look up, build agents: one file answers "which graph for which type, built how".
- **D5. The loop lives in a `tasks` package as the pool.** A loop engine pulling pending tasks: stand down when the operator answered, announce needs-human, host the graph, act on the outcome. Not inside the engine package — it owns task lifecycle, not graph hosting alone.
- **D6. The domain module holding task states is renamed to `states`.** It holds `TaskState` and `OutboundState`; the old name was already wrong, and a `tasks` package would collide with it.

### Checkpoint

- **D7. Two tiers.** New reporter text → prepare always re-runs (there is a new message; it must be read). Nodes 1+ re-run only if the params prepare returned differ from the stored ones. The fingerprint is therefore on node 0's *output*, and node 0 is never checkpointed. Hashing the text alone would re-investigate over a "cảm ơn anh"; hashing params from before prepare could never see the new correlationId.

  Worked example, three messages:

  | Reporter sends | Prepare | Nodes 1+ |
  |---|---|---|
  | "API lỗi" | runs; no id → ask | never reached |
  | "cảm ơn anh" | runs (new text); params unchanged | state kept, nothing re-runs |
  | "cid là abc…" | runs; params changed | state discarded, investigation re-runs |

- **D8. Stored in SQLite, in the existing `dag_state` table, one row per task.** `params_fingerprint` becomes the hash of prepare's output; `state` holds nodes 1+ only; a new nullable `interruption` column holds the SDK `RunState` (as JSON) and the waiting node while a tool approval is pending. Not the filesystem: SQLite is the only state store, the container persists only `data/`, and only one store lets "task moved to waiting + checkpoint saved + ask queued" commit in one transaction. Actions are never stored — they end a run; they are not input to a later node.
- **D9. `PauseForHuman` dissolves.** A node returning an ask ends the run and the task waits; new text re-runs from node 1 under D7. "Resume from the paused node" becomes "re-run from node 1" — same outcome, one mechanism.

### Tools and decisions

- **D10. Tools are a typed return channel; the graph's shape is still code.** A node's agent calls `ask_clarification` / `answer` / `hand_over` to report its conclusion; the node function turns the call into an Action. This is how triage already works. It is *not* the lead-agent model where the model chooses the next step: deer-flow's own prompt mandates "clarify FIRST, before working", which is what D2 does deterministically, and this system has already watched a model invent a step.
- **D11. `ask_clarification(fields, because)` carries intent, not words.** The extractor — and any node agent that finds it needs something mid-run — calls it. The Responder writes the sentence. Two capabilities kept apart: the extractor knows what is missing (it just read the whole thread); the Responder knows how to speak to a person (voice, room register, stranger pronouns, skills). The Responder does not hold the tool; it fulfils it. `fields` is a closed enum, so the model cannot ask for a field that does not exist.
- **D12. The extractor decides whether to ask; code is the floor.** The validation rules run after regardless. If the model does not ask and a value is malformed, code refuses to proceed and asks with the template. Model-first for wording and judgement; code-fallback so a persuasive message cannot argue a rule away. Both paths produce the same Action, and the existing per-task dedup applies.
- **D13. `answer(text)` on the composing node; approval stays at the outbox.** The outbox predicate — a reply row needs an approval — is one place every sender passes, provider-agnostic, restart-safe, already wired to Discord buttons. Moving message approval into the SDK's approval flow would rebuild that. Nothing about it changes.
- **D14. `hand_over(reason)` on any node → the operator.** Replaces `Park`. The reason is a node agent's finding, quoted to the operator; it is not a message under the operator's name to anyone else. A graph that reaches its end without answering hands over by code, no model involved.
- **D15. Non-message actions use the SDK's tool approval.** The patch-applying tool is marked as needing approval; the run stops; the SDK's run state goes into the checkpoint (D8); the operator approves; the run resumes, possibly in another process. Two kinds of side effect, two gates: messages through the outbox, actions through tool approval.
- **D16. Task creation: model proposes, code decides.** Triage's per-type tools collapse into `create_task(task_type, confidence)` with a closed enum of types, plus `skip(confidence)` kept as its own tool because it creates nothing. Per-type descriptions are generated from the params registry, so adding a type is adding one params class. The confidence threshold and the follow-up rules still decide whether a task exists.
- **D17. Task execution stays automatic.** The risk is in acting — answering under the operator's name, patching code — not in investigating. Both actions have gates (D13, D15); reading logs does not need one.
- **D18. No `create_task` tool inside graphs.** The referenced tool in deer-flow is synchronous subagent delegation within one run, not persistent task creation. Delegation here is a node, and the graph's shape is code (D10).

### Invariant

Only Responder-family agents produce text that reaches a reporter. Pinned by test.

## Testing Decisions

A good test here verifies behaviour through an interface a caller uses, not the shape of the code inside. The test should still pass if a node function is split in two, a helper is renamed, or a query is rewritten — and it should fail the moment a message goes out that should not have, a checkpoint is reused that should have been discarded, or a model is reached that should not have been.

Three seams, all of which already exist. No new ones.

1. **The pool's `run_once()`** — the highest seam. Insert messages into the store, run the loop, read tasks and outbox rows back. Covers D1 (every type runs a graph), D7 (the three-message table produces three behaviours), D11/D12 (an ask from intent, the code floor), D13/D14 (an answer waits at the outbox, a hand-over becomes a help-wanted row), D16 (one tool creates a task). This is today's `WorkflowRunner.run_once`, renamed.
2. **The graph runner with a synthetic graph** — for D7, D8, D9 and D15 at engine level: two-tier checkpoint, run state stored and resumed, no model involved. Exists today.
3. **The scripted model** — to force a specific tool call: the extractor calling `ask_clarification`, the patch tool being intercepted for approval. The seam triage has used all along.

Prior art: `tests/test_workflow_runner.py` (seam 1), `tests/test_dag.py` (seam 2), `tests/test_triage.py` (seam 3). Every refactoring ticket additionally captures all assembled prompts before and after and asserts byte-identity — the pattern from tickets 42–45. Every guard added is checked by removing it and watching the suite go red — the pattern from tickets 34–41.

No test reaches into `_fill`, the fingerprint function, or a node's body directly. Wanting to is the signal that a module has the wrong shape.

## Out of Scope

- A lead agent that chooses the next node (the deer-flow model) — ruled out by D10.
- Moving reply approval from the outbox into the SDK's approval flow — ruled out by D13.
- SDK sessions for conversation memory — a third store with its own reasons to change; not touched here.
- Board UI work (tickets 18–20 in `discord-mention-triage`).
- New task types or new graphs beyond the one-node graphs D1 requires.
- Any change to what the persona files or the skills say.

## Further Notes

The refactoring tickets (moves, renames, dissolutions) must each leave every assembled prompt byte-identical — captured with the golden script used for tickets 42–45, not eyeballed. Tickets that change behaviour (D2, D7, D11–D16) are exempt from that rule and say so.

`friday/workflows/` is deleted in the final ticket, not earlier: it is the contract step of an expand–contract, and folding the deletion into whichever ticket lands last would make that ticket's green depend on landing order.

The invariant "only Responder-family agents produce text that reaches a reporter" is new to this codebase and is the one line most likely to be eroded quietly by a future ticket. Its test should be the loudest one in the suite.
