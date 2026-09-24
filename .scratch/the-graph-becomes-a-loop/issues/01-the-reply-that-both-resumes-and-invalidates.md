Type: grilling
Status: resolved
Blocked by:

# The reply that both resumes and invalidates

## Question

When the reporter replies to an `Ask`, that reply is **two things at once**:

1. the **input that resumes** the paused `Diagnose` loop from its persisted
   `message_history` + `Evidence` (charting decision Q6b); and
2. a **new message** that re-runs `Intake` — and `Intake` is the **staleness
   anchor** whose changed output discards downstream checkpoints (Q9).

These conflict. Naively, the resume-reply makes `Intake` re-run, its output
changes (a new message arrived), the discard fires, and it throws away the very
`message_history` / `Evidence` the loop needs to continue from — re-burning
reads and breaking the `Lnn` grounding.

Decide:

- How does the graph tell a **resume-reply** (answering an open `Ask`) apart
  from a **context-changing follow-up** (new facts that should invalidate)?
- In the resume case, how does the `Diagnose` loop's state (`message_history` +
  `Evidence`) **survive** an `Intake` re-run?
- The mixed case: a reply that **both** answers the `Ask` **and** changes the
  context (e.g. "here's the curl, and it's now on prod too"). Continue, discard,
  or replan?
- Does "answering an `Ask`" count as a context change at all, or is an open
  `Ask` a distinct task state the pool already tracks?

Blocks the build: resume cannot be implemented until this is decided. Consult
`CONTEXT.md` § Graph (checkpoint/discard keyed on node-0 output), § Pool
(pause/resume), § Action.

## Answer

Decided 2026-09-24 (grilling). **The discriminator is the Intake-snapshot diff;
intent is never guessed.**

1. **Discriminator (Q1 = a).** Every incoming reporter message re-runs Intake
   (cheap, deterministic — no LLM). The graph diffs Intake's output against the
   stored snapshot:
   - **Unchanged** → the downstream checkpoint (Diagnose `message_history` +
     `Evidence`) **survives**; if the loop is paused on an `Ask`, **resume**.
     `Lnn` ids stay stable → grounding intact.
   - **Changed** → **discard** downstream and re-investigate from the new
     snapshot (old reads were against the old placement; the tools were closed
     over it).

2. **Staleness key = "placement identity" (Q2)** = `env + service + clone +
   repo_path + release_tag` — the fields the Diagnose tools are built around
   (`investigate_tools`). Only a change here invalidates. Memory / skills
   changing does **not** nuke an in-flight run: it is context, not what the
   tools close over. `placement identity` **replaces node-0 output as the
   checkpoint discard anchor** (per charting Q9).

3. **Resume mechanics (Q3).** On resume the reply enters as a **new reporter
   turn appended to `message_history`** — not a Pydantic `DeferredToolResults`
   entry, because `Ask` is a loop-boundary Action, not a deferred tool call.
   `message_history` + `Evidence` live in the **Diagnose node's checkpoint**,
   surviving iff placement identity is unchanged.

4. **Mixed case — no special handling.** "curl only" → identity unchanged →
   resume with the curl; "…and it's on prod too" → `env` changed → discard +
   re-investigate. The diff decides, not the loop.

5. **An open `Ask` is a task state the Pool already tracks** — it decides
   *whether to resume*; invalidation is purely the snapshot diff.

**Build consequences:** (a) define `placement_identity` over Intake's output as
the checkpoint discard key; (b) store the Diagnose agent's `message_history` +
`Evidence` in its checkpoint; (c) no reply-intent classifier, no separate
change-detector. New term `placement identity` goes to `CONTEXT.md` § Vocabulary
at build time (not now — the shape is decided, not built).

