Type: prototype
Status: resolved
Blocked by:

# The recognition reasoning and the assembled triage prompt

## Question

Stub, to react to: the exact shape of an action's **recognition reasoning**
(when to pick it, when not and which action instead, a few examples), and the
triage prompt the core **assembles** from the domain-agnostic reasoning + every
registered action's reasoning + operator-confirmed DB examples — with **no
order and no catch-all**.

Show the assembled prompt for `backend.trace_problem`, `backend.answer_question`
and `ops.request_permission`, and how the label meanings that today come from
`Params` docstrings (deleted with the extractor) move into it. Decide the
boot-time checks (an example whose label is not registered refuses the boot).

## Answer

Resolved 2026-09-28 (prototype + grilling). Prototype: branch
`prototype/recognition-and-triage-prompt`, file
`.scratch/domains-plug-in/recognition_and_triage_prompt_STUB.py` — run it to
print the assembled prompt and the boot checks.

1. **`Recognition`** sits on `Action` beside its `ActionContract`:
   `means` (one sentence: what this kind of message asks for), `pick_when`
   (signals pointing here), `not_when` (pairs *(signal, other action's name)*),
   `examples` (message texts; the label is the declaring action). `not_when`
   is what replaces today's ordered ladder — the pair decides between two
   actions, not which rung comes first.
2. **Label meaning lives in the prompt only**, in a `## The labels` section.
   The answer schema keeps just `type` as a `Literal` closed to the registered
   actions + `skip` (so `out_of_set` still works), described as "one of the
   labels above". `_type_doc` / `_means` go with `Params`.
3. **`not_when` is declared and rendered on one side** — a plugin writes only
   about its own actions and names others by name; no mirroring, no required
   reverse pair.
4. **Examples always add up**: each action's declared examples → core `skip`
   examples → operator-confirmed DB rows. Declared ones never drop out as DB
   rows accumulate (stable cache prefix; a new action is not outweighed).
   The DB count knob is ticket 07's.
5. **Boot refuses** on: `not_when` naming an unregistered action or itself;
   one example text claimed by two labels; an action with no examples; empty
   `means` or `pick_when`. **Dropped, not refused**: DB rows naming a label no
   longer registered (renames: ticket 08). **A suite test**, not a boot
   check: a declared example also in `evals/triage.jsonl`.
6. **Render order**: actions sorted by name (stable bytes, no priority), `skip`
   always last as the core's own entry; the core thinking says outright "no
   label comes first".

`skip` stays core-owned (no plugin, no contract). The core thinking text in the
stub (the old ladder minus its order) is a draft: changing `prompt.py` needs
`evals.run_triage_eval` at build time, as CLAUDE.md requires.
