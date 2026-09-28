Type: grilling
Status: resolved
Blocked by: 02, 04

# Designing `backend.answer_question`

## Question

A new action: "how does this business rule / endpoint work, where is it
written", answered from **code and docs**. Decide its contract — allowed step
types and toolsets (read code, read docs, what-code-means), model tier, budget,
acceptance criteria (a grounded answer citing the lines it read, like
`Diagnosis`?) — what it returns (a `Reply` that waits for approval?), and its
recognition reasoning against `trace_problem`: "it does not work" vs "how does
it work".

> Note from "`trace_problem`'s graph becomes the first plan" (15): each action
> writes its own acknowledgement through an optional `Action.acknowledge`
> hook (none → silent); `answer_question` decides its text, or none.

## Answer

Resolved 2026-09-28 (grilling).

What it answers — from three real questions: "does batch generation use the
free-gen path?", "is the webhook secured by a token yet?", "does the Printful
position calculation do the image crop?". All the same shape: **does the code
running now do X, and how** — a yes/no with the lines that show it.

1. **Scope**: questions about what the code/docs currently do. Replaces all
   of `docs.doc_question`. Nothing ran, no behaviour reported.
2. **Sources**: code **and** docs, both in the project's repo. Docs are read
   only under the project's declared `docs_paths` (already on
   `devops.project`).
3. **Which repo**: the candidates are the channel's projects. `devops.project`
   gains a `purpose` field (what this repo does, in words a reporter would
   use: "batch generation, Printful calls, incoming webhooks").
4. **Who picks the repo**: the agent, from `purpose`, and it switches when
   what it reads says it is in the wrong place. The Planner's `brief` may
   name several repos as a starting point — a hint, not a limit.
5. **One repo per tool call**: `search_code(repo, query)` (new — finds
   `file:line` by route, symbol or term; there is no stack frame here),
   `read_code(repo, file, line)`, `read_docs(repo, …)`. `repo` is required
   and must be one of the channel's projects. The plan stays **one** `agent`
   step; the agent works the repos one after another.
6. **Version**: the running tag of the env the question names, prod when
   none (ticket 04's mechanism: learn the running version, `git show
   <tag>:<file>`). The answer names the ref it read.
7. **Agent `backend.explain`**, new, beside `diagnose` — its result is
   `Explanation`:
   `verdict` (`yes | no | partly | unknown`), `answer` (1–3 sentences,
   Vietnamese), `refs` (line ids over code and docs; an id naming no line
   voids the answer), `conclusive`, `next_checks`. No
   `alternatives_rejected`. **`no` + `conclusive` needs a ref to the place
   that would have done it** (the webhook handler, read, with no token
   check) — "not found" is not "not there". Code fills which repos were
   searched and which ref was read; the model is not asked to remember.
8. **Approval**: the `Reply` waits for the operator, as for `trace_problem`.
9. **Recognition**:
   `means` "asks whether the current code/docs do something, or how; reports
   no behaviour that happened" · `pick_when` "đã làm … chưa", "có dùng …
   không", "hoạt động thế nào", "ở đâu"; no error, curl or response ·
   `not_when` (a real run gave a wrong result, or an error code / curl /
   response → `backend.trace_problem`) · `examples` the three questions
   above. **Observed behaviour always wins**: "the webhook was just called
   with no token and still ran — is token security done?" →
   `trace_problem`.
10. **Contract**:

```
ActionContract (backend.answer_question)
├── allowed_step_types   agent · ask · hand_over · draft
├── allowed_agents       backend.explain
├── allowed_toolsets     backend.code (search_code, read_code) · backend.docs (read_docs)
├── constraints          every ref points at a line that was read
├── approval_policy      Reply waits for approval
├── acceptance_template  a verdict; conclusive ⇒ ≥1 ref; no + conclusive ⇒ a ref
│                        to where it would have been done
└── limits               total_time 10 min · max_replans 1 · max_steps 3

Agent backend.explain    tier strong (as diagnose) · max_turns 20
```

   No `backend.logs`. The numbers are **starting values, not measured** —
   correct them once real runs exist.

**Amends** ticket 04 and uses ticket 03's `RunContext`: the channel's projects
(each with `repo_path`, `docs_paths`, `purpose`) reach the toolset factory
through `RunContext`, not only one `Placement`; `answer_question`'s
`placement_identity` is usually empty (no service), so a reply always
continues. **Amends** the map's Q19 tool split: `backend.code`'s tools take
`repo`, and gain `search_code`. (`devops.project` is `backend.project` after
ticket 08's rename.)

**To ticket 08's eval relabel**: the three `trace_problem` rows that "read like
'how does it work'" are checked against the rule in point 9 at build time — no
behaviour reported → `backend.answer_question`; the operator confirms each.

To the fog: compare HEAD of `default_branch` with the running tag ("on main,
not deployed yet"); an external wiki (Confluence/Notion) as a docs source;
sending a conclusive answer without approval once the judge is calibrated.

## Amended 2026-09-28 by ticket 16

The contract's `total_time 10 min` goes: there is no time limit; `max_replans 1` and `max_steps 3` stay. See [The budget in three groups](16-the-budget-in-three-groups.md).
